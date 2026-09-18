"""CLI bridge to Graphify's graph builder and JSON exporter."""

from __future__ import annotations

import argparse
from collections import Counter
from pathlib import Path
import sys

from graphify.export import to_json
from graphify.onec.graph import build_onec_graph
from graphify.onec.export import write_onec_json
from graphify.onec.project import extract_project
from graphify.onec.viewer import write_large_onec_html, write_onec_html
from graphify.onec.update import remember_analysis
from graphify.validate import assert_valid


def main(argv: list[str] | None = None, *, record_manifest: bool = True) -> None:
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
    parser.add_argument("--no-cache", action="store_true", help="parse every BSL module from source")
    args = parser.parse_args(argv)
    timings: dict[str, float] = {}
    cache_path = None if args.no_cache else args.out.parent / ".graphify-onec-modules.sqlite"
    extraction = extract_project(
        args.root, extensions=args.extension, timings=timings, module_cache=cache_path
    )
    assert_valid(extraction)
    node_count = len(extraction["nodes"])
    edge_count = len(extraction["edges"])
    args.out.parent.mkdir(parents=True, exist_ok=True)
    if node_count > 5000:
        node_count, edge_count = write_onec_json(extraction, args.out)
    else:
        graph = build_onec_graph(extraction)
        to_json(graph, {}, str(args.out), force=True)
    if args.html:
        if node_count > 5000:
            write_large_onec_html(args.out, args.html, node_count, edge_count)
            print(
                f"Large viewer: {args.html} + {args.html.with_suffix('.sqlite')}; "
                f"run graphify-1c serve {args.html}"
            )
        else:
            write_onec_html(graph, args.html, graph_path=args.out)
    print(
        f"1C graph: {node_count} nodes, {edge_count} edges → {args.out}"
    )
    if cache_path:
        print(
            f"BSL cache: {int(timings.get('bsl_cache_hits', 0))} reused, "
            f"{int(timings.get('bsl_cache_misses', 0))} parsed"
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
    if record_manifest:
        remember_analysis(args.root, args.extension, args.out, args.html)
