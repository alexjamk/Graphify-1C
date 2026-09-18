"""Preserve distinct 1C relationships between the same pair of nodes."""

from __future__ import annotations

import networkx as nx


def build_onec_graph(extraction: dict) -> nx.MultiDiGraph:
    """Build a directed graph with one edge per source, target and relation."""
    graph = nx.MultiDiGraph()
    for node in extraction["nodes"]:
        graph.add_node(node["id"], **{key: value for key, value in node.items() if key != "id"})
    for edge in extraction["edges"]:
        graph.add_edge(
            edge["source"],
            edge["target"],
            key=edge["relation"],
            **{key: value for key, value in edge.items() if key not in {"source", "target"}},
        )
    return graph
