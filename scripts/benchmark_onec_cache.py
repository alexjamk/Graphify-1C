"""Measure cold, warm and one-file-changed extraction with a BSL module cache.

This measures extraction only. JSON/SQLite output costs are excluded.
"""

from __future__ import annotations

import argparse
import json
import tempfile
from pathlib import Path
from time import perf_counter

from graphify.onec import extract_project

from scripts.benchmark_onec import _synthetic


def _run(root: Path, cache: Path | None) -> tuple[dict, float, dict]:
    timings: dict[str, float] = {}
    start = perf_counter()
    graph = extract_project(root, timings=timings, module_cache=cache)
    return graph, perf_counter() - start, timings


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--synthetic", type=int, default=1000, metavar="DOCUMENTS")
    parser.add_argument("--samples", type=Path, help="optional source of real BSL bodies")
    args = parser.parse_args()
    if args.synthetic < 2:
        parser.error("--synthetic must be at least 2")
    with tempfile.TemporaryDirectory(prefix="graphify-onec-cache-") as directory:
        root = Path(directory)
        _synthetic(root, args.synthetic)
        if args.samples:
            sample_modules = (
                path for path in sorted(args.samples.rglob("*.bsl"))
                if 16 * 1024 <= path.stat().st_size <= 512 * 1024
            )
            for index, source in zip(range(args.synthetic), sample_modules):
                target = root / "Documents" / f"Документ{index:05d}" / "Ext" / "ObjectModule.bsl"
                target.write_bytes(source.read_bytes())
        cache = root / "modules.sqlite"
        cold, cold_seconds, cold_phases = _run(root, cache)
        warm, warm_seconds, warm_phases = _run(root, cache)
        if cold != warm:
            raise AssertionError("warm extraction differs from cold extraction")
        module = root / "Documents" / "Документ00000" / "Ext" / "ObjectModule.bsl"
        module.write_text(
            module.read_text(encoding="utf-8") + "\nПроцедура Новая()\nКонецПроцедуры\n",
            encoding="utf-8",
        )
        changed, changed_seconds, changed_phases = _run(root, cache)
        fresh, fresh_seconds, fresh_phases = _run(root, None)
        if changed != fresh:
            raise AssertionError("cached changed extraction differs from fresh extraction")
        result = {
            "documents": args.synthetic,
            "cold_seconds": round(cold_seconds, 3),
            "warm_seconds": round(warm_seconds, 3),
            "one_changed_seconds": round(changed_seconds, 3),
            "fresh_after_change_seconds": round(fresh_seconds, 3),
            "warm_cache_hits": int(warm_phases.get("bsl_cache_hits", 0)),
            "changed_cache_hits": int(changed_phases.get("bsl_cache_hits", 0)),
            "changed_cache_misses": int(changed_phases.get("bsl_cache_misses", 0)),
            "cold_bsl_seconds": round(cold_phases["bsl"], 3),
            "warm_bsl_seconds": round(warm_phases["bsl"], 3),
            "changed_bsl_seconds": round(changed_phases["bsl"], 3),
            "fresh_bsl_seconds": round(fresh_phases["bsl"], 3),
        }
        print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
