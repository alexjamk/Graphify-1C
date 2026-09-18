"""Compare indexed 1C relationships with plain ripgrep on the same sources."""

from __future__ import annotations

import argparse
import json
import sqlite3
import statistics
import subprocess
from pathlib import Path
from time import perf_counter


def _query(database: Path, sql: str, params: tuple = ()) -> list[tuple]:
    with sqlite3.connect(database) as db:
        return db.execute(sql, params).fetchall()


def _rg(
    pattern: str, paths: list[Path], *, fixed: bool, glob: str, files: bool = False
) -> list[str]:
    command = ["rg", "-l" if files else "-n", "--glob", glob]
    if fixed:
        command.append("-F")
    command.extend([pattern, *(str(path) for path in paths)])
    result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8")
    if result.returncode not in (0, 1):
        raise RuntimeError(result.stderr)
    return result.stdout.splitlines()


def _measure(fn, repeats: int) -> dict:
    timings = []
    count = None
    for _ in range(repeats):
        started = perf_counter()
        result = fn()
        timings.append(perf_counter() - started)
        count = len(result)
    return {"results": count, "median_seconds": round(statistics.median(timings), 4)}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--base", type=Path, required=True)
    parser.add_argument("--extension", type=Path, action="append", required=True)
    parser.add_argument("--method", required=True)
    parser.add_argument("--target-id", required=True)
    parser.add_argument("--repeats", type=int, default=5)
    args = parser.parse_args()
    if args.repeats < 1:
        parser.error("--repeats must be positive")
    paths = [args.base, *args.extension]
    result = {
        "method_relationships": {
            "graph": _measure(
                lambda: _query(
                    args.database,
                    "SELECT source, relation FROM edges WHERE target=? AND relation IN "
                    "('calls','BEFORE','AFTER','INSTEAD','CHANGE_CONTROL')",
                    (args.target_id,),
                ),
                args.repeats,
            ),
            "rg": _measure(lambda: _rg(args.method, paths, fixed=True, glob="*.bsl"), args.repeats),
        },
        "extension_hooks": {
            "graph": _measure(
                lambda: _query(
                    args.database,
                    "SELECT source,target,relation FROM edges WHERE relation IN "
                    "('BEFORE','AFTER','INSTEAD','CHANGE_CONTROL')",
                ),
                args.repeats,
            ),
            "rg": _measure(
                lambda: _rg(
                    r"^\s*&(Перед|После|Вместо|ИзменениеИКонтроль)\(",
                    args.extension,
                    fixed=False,
                    glob="*.bsl",
                ),
                args.repeats,
            ),
        },
        "borrowed_objects": {
            "graph": _measure(
                lambda: _query(
                    args.database, "SELECT source,target FROM edges WHERE relation='EXTENDS'"
                ),
                args.repeats,
            ),
            "rg": _measure(
                lambda: _rg(
                    "<ObjectBelonging>Adopted</ObjectBelonging>",
                    args.extension,
                    fixed=True,
                    glob="*.xml",
                    files=True,
                ),
                args.repeats,
            ),
        },
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
