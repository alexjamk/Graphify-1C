"""Stream large 1C graphs in Graphify's node-link JSON format."""

from __future__ import annotations

import json
import os
import tempfile
import unicodedata
from pathlib import Path


_CONFIDENCE = {"EXTRACTED": 1.0, "INFERRED": 0.55, "AMBIGUOUS": 0.2}


def _normal(text: object) -> str:
    value = unicodedata.normalize("NFKD", str(text or ""))
    return "".join(char for char in value if not unicodedata.combining(char)).lower()


def _record(stream, item: dict, *, first: bool) -> None:
    if not first:
        stream.write(",\n")
    body = json.dumps(item, ensure_ascii=False, indent=2)
    stream.write("\n".join("    " + line for line in body.splitlines()))


def write_onec_json(extraction: dict, output: Path) -> tuple[int, int]:
    """Avoid NetworkX and a second in-memory copy of a large node-link graph."""
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    # MultiDiGraph merges repeated node IDs and repeated (source, target, key)
    # edges. Preserve that behavior without constructing the graph itself.
    nodes = {}
    for node in extraction["nodes"]:
        key = node["id"]
        if key in nodes:
            nodes[key].update(node)
        else:
            nodes[key] = node
    edges = {}
    for edge in extraction["edges"]:
        key = (edge["source"], edge["target"], edge["relation"])
        if key in edges:
            edges[key].update(edge)
        else:
            edges[key] = edge
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", newline="\n", dir=output.parent,
            prefix=f".{output.name}.", suffix=".tmp", delete=False,
        ) as stream:
            temporary = Path(stream.name)
            stream.write('{\n  "directed": true,\n  "multigraph": true,\n  "graph": {},\n  "nodes": [\n')
            for index, original in enumerate(nodes.values()):
                node = dict(original)
                node["community"] = None
                node["norm_label"] = _normal(node.get("label"))
                _record(stream, node, first=index == 0)
            stream.write('\n  ],\n  "links": [\n')
            for index, original in enumerate(edges.values()):
                edge = dict(original)
                edge["key"] = edge["relation"]
                edge.setdefault(
                    "confidence_score", _CONFIDENCE.get(edge.get("confidence", "EXTRACTED"), 1.0)
                )
                _record(stream, edge, first=index == 0)
            stream.write('\n  ],\n  "hyperedges": []\n}\n')
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, output)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return len(nodes), len(edges)
