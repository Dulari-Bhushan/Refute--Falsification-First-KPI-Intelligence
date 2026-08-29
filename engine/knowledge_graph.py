"""
Knowledge graph over REFUTE's own semantic contract + live verdicts.

SOLUTIONING.md menu item 2 ("governed KPI semantics, metadata, lineage,
business rules, ontology or knowledge graphs") flagged this as a real,
low-risk addition: the relationship data already existed as flat YAML in
semantic/kpi_contract.yaml (a KPI lists its own drivers and sources; a role
lists its own domain_scope and delivery_channels) -- what was missing was a
queryable STRUCTURE over it, not new facts. This builds exactly that: a
small, dependency-free directed graph (a custom adjacency-list
implementation, not networkx -- at ~35 nodes this doesn't need a graph
library, and the project already prefers a custom, understandable
implementation over a dependency for anything this size, same reasoning as
the custom BOCPD/Shapley elsewhere), assembled from the contract plus the
latest real ledger verdicts, not a separately maintained copy of the truth.

Three genuinely new questions this answers that the flat YAML couldn't:
  - "what else touches this dimension?"                  -> related()
  - "what depends on this source, transitively?"          -> blast_radius()
  - "which hypotheses share a mechanism-shape with this?" -> shared_mechanism()
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

import yaml

DATA_DIR = Path(__file__).parent.parent / "data" / "synthetic"
CONTRACT_PATH = Path(__file__).parent.parent / "semantic" / "kpi_contract.yaml"

# Which table backs each falsification dimension -- mirrors
# engine/l4_compiler.py's DIM_REGISTRY. Kept as a small manual mapping here
# rather than importing l4_compiler (which pulls in pandas/sqlite plumbing
# this module has no other reason to depend on) -- if a new dimension is
# ever added to DIM_REGISTRY, add it here too.
DIMENSION_TABLE = {
    "fulfillment_center": "pos_transactions",
    "product_category": "pos_transactions",
    "rep_id": "crm_headcount",
    "channel": "marketing_spend",
}


@dataclass
class Node:
    id: str
    type: str
    label: str
    attrs: dict = field(default_factory=dict)


@dataclass
class Edge:
    source: str
    target: str
    relation: str


class KnowledgeGraph:
    def __init__(self) -> None:
        self.nodes: dict[str, Node] = {}
        self.edges: list[Edge] = []
        # node_id -> [(neighbor_id, relation, "out"|"in")] -- both directions
        # stored so traversal never has to know which side an edge was
        # declared from.
        self._adj: dict[str, list[tuple[str, str, str]]] = {}

    def add_node(self, node_id: str, type_: str, label: str, **attrs) -> None:
        if node_id not in self.nodes:
            self.nodes[node_id] = Node(node_id, type_, label, attrs)
            self._adj[node_id] = []

    def add_edge(self, source: str, target: str, relation: str) -> None:
        if source not in self.nodes or target not in self.nodes:
            return  # a dangling reference (e.g. a driver name that isn't itself a declared KPI) is silently skipped, not a broken edge
        self.edges.append(Edge(source, target, relation))
        self._adj[source].append((target, relation, "out"))
        self._adj[target].append((source, relation, "in"))

    def related(self, node_id: str) -> list[dict]:
        """Every direct neighbor of a node, either direction, with the
        relation that connects them -- 'what else touches this.'"""
        if node_id not in self._adj:
            return []
        return [
            {
                "node_id": neighbor,
                "type": self.nodes[neighbor].type,
                "label": self.nodes[neighbor].label,
                "relation": relation,
                "direction": direction,
            }
            for neighbor, relation, direction in self._adj[node_id]
        ]

    def blast_radius(self, node_id: str, max_depth: int = 3) -> list[dict]:
        """BFS outward in either direction from a node, up to max_depth
        hops -- 'if this changed or stopped working, what else is
        connected to it, and how many hops away.' Most useful from a
        source node (what depends on pos_transactions?) but works from
        any node."""
        if node_id not in self._adj:
            return []
        visited = {node_id}
        frontier = [node_id]
        result = []
        depth = 0
        while frontier and depth < max_depth:
            depth += 1
            next_frontier = []
            for nid in frontier:
                for neighbor, relation, _direction in self._adj.get(nid, []):
                    if neighbor not in visited:
                        visited.add(neighbor)
                        next_frontier.append(neighbor)
                        result.append(
                            {
                                "node_id": neighbor,
                                "type": self.nodes[neighbor].type,
                                "label": self.nodes[neighbor].label,
                                "hops": depth,
                                "via_relation": relation,
                            }
                        )
            frontier = next_frontier
        return sorted(result, key=lambda r: r["hops"])

    def shared_mechanism(self, hypothesis_node_id: str) -> list[dict]:
        """Other hypotheses sharing a TESTS_DIMENSION edge or the same
        test_archetype -- 'what else looks like this hypothesis,
        structurally,' a genuinely graph-native question the flat verdict
        list can't answer without a manual scan."""
        if hypothesis_node_id not in self.nodes or self.nodes[hypothesis_node_id].type != "hypothesis":
            return []
        my_dims = {n for n, rel, _d in self._adj[hypothesis_node_id] if rel == "TESTS_DIMENSION"}
        my_archetype = self.nodes[hypothesis_node_id].attrs.get("test_archetype")
        results = []
        for nid, node in self.nodes.items():
            if node.type != "hypothesis" or nid == hypothesis_node_id:
                continue
            their_dims = {n for n, rel, _d in self._adj[nid] if rel == "TESTS_DIMENSION"}
            shared_dims = sorted(d.split(":", 1)[1] for d in (my_dims & their_dims))
            same_archetype = my_archetype is not None and node.attrs.get("test_archetype") == my_archetype
            if shared_dims or same_archetype:
                results.append(
                    {
                        "node_id": nid,
                        "label": node.label,
                        "verdict": node.attrs.get("verdict"),
                        "test_archetype": node.attrs.get("test_archetype"),
                        "shared_dimensions": shared_dims,
                        "same_archetype": same_archetype,
                    }
                )
        return results

    def export(self) -> dict:
        return {
            "nodes": [{"id": n.id, "type": n.type, "label": n.label, **n.attrs} for n in self.nodes.values()],
            "edges": [{"source": e.source, "target": e.target, "relation": e.relation} for e in self.edges],
        }


def build_graph(contract: dict, verdicts: list[dict] | None = None) -> KnowledgeGraph:
    g = KnowledgeGraph()

    for name, meta in contract["sources"].items():
        g.add_node(f"source:{name}", "source", name, cadence=meta["refresh_cadence"], system_of_record=meta["system_of_record"])

    for domain in ("sales", "marketing", "hr"):
        g.add_node(f"domain:{domain}", "domain", domain)

    for kpi_name, meta in contract["kpis"].items():
        g.add_node(f"kpi:{kpi_name}", "kpi", kpi_name, domain=meta.get("domain"), owner=meta.get("owner"))

    for kpi_name, meta in contract["kpis"].items():
        for source_name in meta.get("sources", []):
            g.add_edge(f"kpi:{kpi_name}", f"source:{source_name}", "SOURCED_FROM")
        for source_name in meta.get("reconciled_against", []):
            g.add_edge(f"kpi:{kpi_name}", f"source:{source_name}", "RECONCILED_AGAINST")
        if meta.get("domain"):
            g.add_edge(f"kpi:{kpi_name}", f"domain:{meta['domain']}", "BELONGS_TO_DOMAIN")
        for driver in meta.get("drivers", []):
            g.add_edge(f"kpi:{kpi_name}", f"kpi:{driver}", "HAS_DRIVER")

    for dim, table in DIMENSION_TABLE.items():
        g.add_node(f"dim:{dim}", "dimension", dim)
        g.add_edge(f"dim:{dim}", f"source:{table}", "BACKED_BY_TABLE")

    for verdict_name in ("KILLED", "SURVIVED", "INCONCLUSIVE"):
        g.add_node(f"verdict:{verdict_name}", "verdict", verdict_name)

    for role_name, meta in contract["entitlements"].items():
        g.add_node(f"role:{role_name}", "role", role_name, persona=meta.get("persona"))
        for domain in meta.get("domain_scope", []):
            g.add_edge(f"role:{role_name}", f"domain:{domain}", "SCOPED_TO_DOMAIN")
        for channel in meta.get("delivery_channels", []):
            g.add_node(f"channel:{channel}", "channel", channel)
            g.add_edge(f"role:{role_name}", f"channel:{channel}", "CAN_PUSH_VIA")

    if verdicts:
        for v in verdicts:
            hid = v.get("hypothesis_id")
            if not hid:
                continue
            g.add_node(f"hyp:{hid}", "hypothesis", hid, test_archetype=v.get("test_archetype"), verdict=v.get("verdict"))
            dim = v.get("dim")
            if dim:
                g.add_node(f"dim:{dim}", "dimension", dim)  # no-op if already declared above
                g.add_edge(f"hyp:{hid}", f"dim:{dim}", "TESTS_DIMENSION")
            if v.get("verdict") in ("KILLED", "SURVIVED", "INCONCLUSIVE"):
                g.add_edge(f"hyp:{hid}", f"verdict:{v['verdict']}", "HAS_VERDICT")
            g.add_edge(f"hyp:{hid}", "kpi:revenue", "TARGETS_KPI")

    return g


def load_graph() -> KnowledgeGraph:
    """Rebuilds the graph fresh from the current contract + the current
    run's verdicts -- same 'read the live source, don't cache a stale
    export' discipline every other /api/* endpoint in this project
    follows."""
    contract = yaml.safe_load(CONTRACT_PATH.read_text())
    verdicts_path = DATA_DIR / "l5_verdicts.json"
    verdicts = json.loads(verdicts_path.read_text()) if verdicts_path.exists() else None
    return build_graph(contract, verdicts)


def main() -> None:
    graph = load_graph()
    export = graph.export()
    (DATA_DIR / "knowledge_graph.json").write_text(json.dumps(export, indent=2))

    print(f"Knowledge graph: {len(export['nodes'])} nodes, {len(export['edges'])} edges")

    print("\nExample query -- what touches the 'rep_id' dimension?")
    for r in graph.related("dim:rep_id"):
        print(f"  {r['relation']:<20} {r['direction']:<4} {r['label']} ({r['type']})")

    print("\nExample query -- blast radius of pos_transactions, up to 2 hops (what depends on it)?")
    for r in graph.blast_radius("source:pos_transactions", max_depth=2):
        print(f"  {r['hops']} hop(s) via {r['via_relation']:<20} -> {r['label']} ({r['type']})")

    verdicts_path = DATA_DIR / "l5_verdicts.json"
    verdicts = json.loads(verdicts_path.read_text()) if verdicts_path.exists() else []
    survived = next((v["hypothesis_id"] for v in verdicts if v.get("verdict") == "SURVIVED"), None)
    if survived:
        print(f"\nExample query -- what shares a mechanism-shape with '{survived}'?")
        for r in graph.shared_mechanism(f"hyp:{survived}"):
            print(f"  {r['label']:<30} verdict={r['verdict']:<12} archetype={r['test_archetype']:<14} shared_dims={r['shared_dimensions']} same_archetype={r['same_archetype']}")


if __name__ == "__main__":
    main()
