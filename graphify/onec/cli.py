"""CLI bridge to Graphify's graph builder and JSON exporter."""

from __future__ import annotations

import argparse
from collections import Counter
from pathlib import Path
import sys

from graphify.export import to_json
from graphify.onec.graph import build_onec_graph
from graphify.onec.project import extract_project
from graphify.onec.viewer import write_onec_html
from graphify.validate import assert_valid


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="graphify analyze")
    parser.add_argument(
        "root", type=Path, help="1C project, Configurator XML export or EDT directory"
    )
    parser.add_argument(
        "--extension",
        action="append",
        type=Path,
        default=[],
        help="Configurator XML export of an extension; repeatable",
    )
    parser.add_argument("--out", type=Path, default=Path("graphify-out/graph.json"))
    parser.add_argument("--html", type=Path, help="write interactive Graphify HTML")
    args = parser.parse_args(argv)
    extraction = extract_project(args.root, extensions=args.extension)
    assert_valid(extraction)
    graph = build_onec_graph(extraction)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    to_json(graph, {}, str(args.out), force=True)
    if args.html:
        write_onec_html(graph, args.html, graph_path=args.out)
        if graph.number_of_nodes() > 5000:
            print(
                f"Large viewer: {args.html} + {args.html.with_suffix('.sqlite')}; "
                f"run graphify-1c serve {args.html}"
            )
    print(
        f"1C graph: {graph.number_of_nodes()} nodes, {graph.number_of_edges()} edges → {args.out}"
    )
    stats = extraction["diagnostics"]
    calls = stats["calls_resolved"]
    print(
        f"Metadata: {stats['metadata_objects']} objects; {stats['modules']} modules; "
        f"{stats['procedures']} procedures; {stats['functions']} functions; "
        f"{stats['extensions']} extensions"
    )
    print(
        f"Calls: {sum(calls.values())} resolved "
        f"(extracted={calls.get('EXTRACTED', 0)}, inferred={calls.get('INFERRED', 0)}, "
        f"ambiguous={calls.get('AMBIGUOUS', 0)}); "
        f"{stats['unresolved_calls']} unresolved"
    )
    print(
        f"Methods with query reads: {stats['methods_with_query_reads']}; "
        f"metadata references: {stats['metadata_references']}"
    )
    if stats["top_unresolved_calls"]:
        print(
            "Top unresolved calls: "
            + ", ".join(f"{name}={count}" for name, count in stats["top_unresolved_calls"])
        )
    issues = Counter(
        node["parse_status"] for node in extraction["nodes"] if node.get("parse_status")
    )
    if issues:
        print(
            "BSL modules requiring review: "
            + ", ".join(f"{status}={count}" for status, count in sorted(issues.items())),
            file=sys.stderr,
        )
