"""Benchmark the 1C extraction pipeline on an export or synthetic project."""

from __future__ import annotations

import argparse
import json
import tempfile
import tracemalloc
from pathlib import Path
from time import perf_counter

from graphify.onec import extract_project
from graphify.onec.graph import build_onec_graph


def _synthetic(root: Path, count: int) -> None:
    (root / "Configuration.xml").write_text(
        "<MetaDataObject><Configuration><Properties><Name>Benchmark</Name>"
        "</Properties></Configuration></MetaDataObject>",
        encoding="utf-8",
    )
    directory = root / "Documents"
    directory.mkdir()
    for index in range(count):
        name = f"Документ{index:05d}"
        (directory / f"{name}.xml").write_text(
            f"<MetaDataObject><Document><Properties><Name>{name}</Name>"
            "</Properties></Document></MetaDataObject>",
            encoding="utf-8",
        )
        module = directory / name / "Ext" / "ObjectModule.bsl"
        module.parent.mkdir(parents=True)
        module.write_text(
            "Процедура Обработать()\nВспомогательная();\nКонецПроцедуры\n"
            "Процедура Вспомогательная()\nКонецПроцедуры\n",
            encoding="utf-8",
        )


def _run(root: Path) -> dict:
    timings: dict[str, float] = {}
    tracemalloc.start()
    start = perf_counter()
    extraction = extract_project(root, timings=timings)
    build_start = perf_counter()
    graph = build_onec_graph(extraction)
    timings["build"] = perf_counter() - build_start
    timings["total"] = perf_counter() - start
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    return {
        **{key + "_seconds": round(value, 4) for key, value in timings.items()},
        "peak_memory_mb": round(peak / 1024**2, 2),
        "nodes": graph.number_of_nodes(),
        "edges": graph.number_of_edges(),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    choice = parser.add_mutually_exclusive_group(required=True)
    choice.add_argument("--root", type=Path)
    choice.add_argument("--synthetic", type=int, metavar="DOCUMENTS")
    args = parser.parse_args()
    if args.root:
        result = _run(args.root)
    else:
        if args.synthetic < 1:
            parser.error("--synthetic must be positive")
        with tempfile.TemporaryDirectory(prefix="graphify-onec-") as temp:
            root = Path(temp).resolve()
            assert root.is_relative_to(Path(tempfile.gettempdir()).resolve())
            _synthetic(root, args.synthetic)
            result = _run(root)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
