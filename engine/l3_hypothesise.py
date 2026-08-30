"""
L3 -- HYPOTHESISE: candidate causes from unstructured data (support tickets).

The naive-RAG trap this module is built to avoid: retrieving tickets from
the anomaly's time window is fatally biased, because ticket volume moves in
*every* bad week regardless of cause -- naive retrieval will always "find
something" and present it as if it explained the movement.

Correct approach, per the spec:
  1. Embed every ticket continuously (not just at query time).
  2. Cluster into topics.
  3. Build a per-topic weekly time series.
  4. Run the SAME BOCPD algorithm from L1 independently on every topic's
     own series.
  5. A topic only becomes a candidate hypothesis if it has ITS OWN
     independent changepoint, AND that changepoint's tau precedes the
     KPI's tau (checked structurally here, at generation time -- not left
     to an LLM to "be careful about" downstream).

Embedding note: the spec's suggested stack is sentence-transformers + UMAP
+ HDBSCAN. This module uses sentence-transformers (all-MiniLM-L6-v2) for the
embeddings -- genuine semantic similarity matters here, since these tickets
are short and phrased in varied natural language (a TF-IDF baseline was
tried first and rejected: same-topic tickets barely shared vocabulary,
e.g. "carrier delays cited again" vs "WEST_DC dispatch backed up" have
almost no token overlap despite being about the same thing, so TF-IDF
cosine distance couldn't separate topics from noise at all). UMAP and
HDBSCAN are NOT used, though: this prototype's ticket corpus is 52
documents, and the spec's own honesty section states topic clustering
"degrades below ~200 documents per window" -- UMAP's manifold assumptions
and HDBSCAN's density estimates are both unreliable at this scale
regardless of embedding quality, so running them here would produce a
false impression of sophistication rather than a more correct result.
Agglomerative clustering directly on the embeddings (scikit-learn, no
extra dependency) is the honest choice at this corpus size; the fuller
stack is what a production build would swap in once ticket volume clears
that threshold, and that's a config change, not a rewrite, since
everything downstream only depends on "cluster label -> per-topic weekly
series."
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd
import yaml
from sentence_transformers import SentenceTransformer
from sklearn.cluster import AgglomerativeClustering
from sklearn.feature_extraction.text import TfidfVectorizer

from engine.l1_signal import BOCPD, fit_uninformative_prior

_EMBEDDING_MODEL_NAME = "all-MiniLM-L6-v2"
_model: SentenceTransformer | None = None


def _get_model() -> SentenceTransformer:
    global _model
    if _model is None:
        _model = SentenceTransformer(_EMBEDDING_MODEL_NAME)
    return _model

DATA_DIR = Path(__file__).parent.parent / "data" / "synthetic"
CONTRACT_PATH = Path(__file__).parent.parent / "semantic" / "kpi_contract.yaml"

DISTANCE_THRESHOLD = 0.65  # cosine distance on normalized sentence embeddings -- unsupervised cluster count, not fixed in advance
MIN_CLUSTER_SIZE = 3  # clusters smaller than this are treated as noise, not a candidate topic
TOPIC_CHANGEPOINT_THRESHOLD = 0.6  # topic series are far noisier/sparser than KPI series -- see module docstring on corpus size; this is deliberately looser than L1's 0.75 KPI threshold


@dataclass
class TopicCandidate:
    cluster_id: int
    region: str
    n_tickets: int
    top_terms: list[str]
    representative_text: str
    weekly_counts: dict
    changepoint_week: int
    changepoint_confidence: float
    precedes_kpi: bool
    kpi_onset_week: int
    became_candidate: bool
    reason: str


def cluster_tickets(tickets: pd.DataFrame) -> tuple[np.ndarray, np.ndarray]:
    """Embeds every ticket continuously (see module docstring on why
    semantic embeddings, not TF-IDF, are needed for this corpus) and
    clusters directly on the embeddings -- no UMAP step, for the same
    small-corpus-honesty reason UMAP/HDBSCAN aren't used for the
    clustering algorithm itself."""
    embeddings = _get_model().encode(tickets["text"].tolist(), normalize_embeddings=True)
    model = AgglomerativeClustering(n_clusters=None, distance_threshold=DISTANCE_THRESHOLD, metric="cosine", linkage="average")
    labels = model.fit_predict(embeddings)
    return labels, embeddings


def top_terms_for_cluster(tickets_in_cluster: pd.DataFrame, all_texts: pd.Series, n: int = 6) -> list[str]:
    """A lightweight TF-IDF pass used only to extract human-readable
    keywords for a cluster the embeddings already formed -- not used for
    the clustering decision itself, which is embeddings-only (see
    cluster_tickets)."""
    if len(tickets_in_cluster) == 0:
        return []
    vectorizer = TfidfVectorizer(stop_words="english", ngram_range=(1, 2), min_df=1)
    X_all = vectorizer.fit_transform(all_texts)
    mask = all_texts.index.isin(tickets_in_cluster.index)
    mean_tfidf = np.asarray(X_all[mask].mean(axis=0)).ravel()
    terms = np.array(vectorizer.get_feature_names_out())
    top_idx = mean_tfidf.argsort()[::-1][:n]
    return [terms[i] for i in top_idx if mean_tfidf[i] > 0]


def scan_for_burst_onset(R: np.ndarray, min_week: int = 2) -> tuple[int, float]:
    """L1's changepoint_estimate reads the FINAL time step's posterior mode
    -- correct for a sustained KPI-level shift, since the new regime is
    still active at the end of the series. A ticket topic often does the
    opposite: it bursts for a week or two and then reverts to its baseline
    rate (zero, usually). Reading the final time step in that case would
    find the REVERSION back to baseline, not the burst's actual onset --
    the model correctly notices "a new, stable run started when it went
    quiet again," which is real but not what we want here. This scans
    every time step for where a new run most likely began (R[t, 0], the
    marginal probability of a changepoint exactly at t) and returns the
    single strongest one across the whole series, not just the most recent."""
    T = R.shape[0]
    best_t, best_p = min_week, 0.0
    for i in range(min_week - 1, T):
        p = R[i, 0]
        if p > best_p:
            best_p = p
            best_t = i + 1
    return best_t, float(best_p)


def weekly_series(tickets_in_cluster: pd.DataFrame, week1_start: pd.Timestamp, total_weeks: int) -> pd.Series:
    weeks = ((tickets_in_cluster["created_at"] - week1_start).dt.days // 7) + 1
    counts = weeks.value_counts().reindex(range(1, total_weeks + 1), fill_value=0).sort_index()
    return counts


def generate_candidates(kpi_onset_week: int, region_filter: str = "West") -> list[TopicCandidate]:
    contract = yaml.safe_load(CONTRACT_PATH.read_text())
    total_weeks = contract["analysis_calendar"]["total_weeks"]
    week1_start = pd.Timestamp(contract["analysis_calendar"]["week1_start"])

    tickets = pd.read_csv(DATA_DIR / "support_tickets.csv", parse_dates=["created_at"])
    # region_filter was accepted here but never actually applied -- every
    # call clustered the FULL cross-region ticket pool regardless of the
    # argument. Latent and invisible while only one region ever had a real
    # signal-bearing cluster; surfaces the moment a second region does; two
    # thematically similar clusters (e.g. two different DCs' shipping
    # delays) would otherwise get merged into one, corrupting both regions'
    # candidate lists.
    tickets = tickets[tickets["region"] == region_filter]
    labels, embeddings = cluster_tickets(tickets)
    tickets = tickets.assign(cluster=labels)

    candidates: list[TopicCandidate] = []
    for cluster_id in sorted(tickets["cluster"].unique()):
        cluster_tickets_df = tickets[tickets["cluster"] == cluster_id]
        n = len(cluster_tickets_df)
        if n < MIN_CLUSTER_SIZE:
            continue

        counts = weekly_series(cluster_tickets_df, week1_start, total_weeks)
        prior = fit_uninformative_prior(counts.to_numpy(dtype=float))
        R = BOCPD(prior, hazard_lambda=10.0).run(counts.to_numpy(dtype=float))
        tau, confidence = scan_for_burst_onset(R)

        has_own_changepoint = confidence > TOPIC_CHANGEPOINT_THRESHOLD and tau > 1
        precedes_kpi = tau <= kpi_onset_week if has_own_changepoint else False
        became_candidate = has_own_changepoint and precedes_kpi

        if not has_own_changepoint:
            reason = f"No independent changepoint of its own (confidence={confidence:.2f}) -- ticket volume here doesn't move on its own; not proposed as a hypothesis."
        elif not precedes_kpi:
            reason = (
                f"Has its own changepoint at week {tau}, but that's AFTER the KPI's own onset (week {kpi_onset_week}) -- "
                "this looks like a downstream symptom of the KPI movement, not a cause of it. Structurally excluded at "
                "generation time, per the precedence requirement (this is exactly the naive-RAG trap: a topic that spikes "
                "*because* the KPI moved would otherwise get treated as if it explained the movement)."
            )
        else:
            reason = f"Independent changepoint at week {tau} (confidence={confidence:.2f}), which precedes the KPI's onset (week {kpi_onset_week}) -- proposed as a candidate hypothesis."

        rep_text = cluster_tickets_df.iloc[(cluster_tickets_df["created_at"] - cluster_tickets_df["created_at"].median()).abs().argsort()[:1]]["text"].iloc[0]

        candidates.append(
            TopicCandidate(
                cluster_id=int(cluster_id),
                region=region_filter,
                n_tickets=n,
                top_terms=top_terms_for_cluster(cluster_tickets_df, tickets["text"]),
                representative_text=rep_text,
                weekly_counts={int(k): int(v) for k, v in counts.items() if v > 0},
                changepoint_week=tau,
                changepoint_confidence=round(confidence, 4),
                precedes_kpi=precedes_kpi,
                kpi_onset_week=kpi_onset_week,
                became_candidate=became_candidate,
                reason=reason,
            )
        )

    return candidates


def main() -> None:
    # local import: engine/investigations.py pulls in engine/l4_compiler.py,
    # which this module has no other reason to depend on -- keep it lazy so
    # generate_candidates() stays usable standalone.
    from engine.investigations import INVESTIGATIONS

    l1_results = json.loads((DATA_DIR / "l1_signal_results.json").read_text())
    all_candidates: list[TopicCandidate] = []
    for region, inv in INVESTIGATIONS.items():
        l1_entry = next(r for r in l1_results if r["kpi"] == inv["kpi"] and r["region"] == region)
        candidates = generate_candidates(l1_entry["changepoint_period_estimate"], region_filter=region)
        all_candidates.extend(candidates)

        print(f"=== {region} · {inv['kpi']} (onset week {l1_entry['changepoint_period_estimate']}) ===")
        print(f"Discovered {len(candidates)} clusters with >= {MIN_CLUSTER_SIZE} tickets (unsupervised, TF-IDF + agglomerative):\n")
        for c in candidates:
            flag = "CANDIDATE" if c.became_candidate else "excluded"
            print(f"[{flag}] cluster {c.cluster_id} (n={c.n_tickets})  top terms: {', '.join(c.top_terms)}")
            print(f"  changepoint week={c.changepoint_week}  confidence={c.changepoint_confidence}")
            print(f"  representative ticket: \"{c.representative_text}\"")
            print(f"  {c.reason}\n")

    (DATA_DIR / "l3_topic_candidates.json").write_text(json.dumps([c.__dict__ for c in all_candidates], indent=2))


if __name__ == "__main__":
    main()
