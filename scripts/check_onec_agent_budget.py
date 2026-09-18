"""Check that common agent queries stay small on an existing 1C SQLite graph.

This deliberately never opens graph.json. Optional tiktoken measures the actual
tool-output token count; the enforced byte limit works without that dependency.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path


def _command(*args: str) -> bytes:
    result = subprocess.run(
        [sys.executable, "-m", "graphify", "index", *args],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
    )
    if result.returncode:
        raise RuntimeError(result.stderr.decode("utf-8", errors="replace"))
    if len(result.stdout) > 12 * 1024:
        raise AssertionError("agent response exceeded 12 KiB")
    return result.stdout


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("index", type=Path)
    parser.add_argument("--node", help="exact node ID; defaults to the most connected source")
    args = parser.parse_args()
    database = args.index.resolve()
    with sqlite3.connect(f"file:{database.as_posix()}?mode=ro", uri=True) as db:
        node = args.node or db.execute(
            "SELECT source FROM edges GROUP BY source ORDER BY COUNT(*) DESC LIMIT 1"
        ).fetchone()[0]
    groups = _command("groups", str(database), node, "--direction", "out")
    page = _command("neighbors", str(database), node, "--direction", "out")
    explanation = _command("explain", str(database), node)
    with tempfile.TemporaryDirectory(prefix="graphify-agent-budget-") as directory:
        output = Path(directory) / "all-links.jsonl"
        exported = _command("export", str(database), node, str(output),
                            "--direction", "out")
        details = json.loads(exported)
        total = sum(group["count"] for group in json.loads(groups))
        if details["count"] != total or details["bytes"] != output.stat().st_size:
            raise AssertionError("SQLite group count and disk export disagree")
        result = {
            "links": total,
            "groups_output_bytes": len(groups),
            "first_page_rows": len(json.loads(page)),
            "first_page_output_bytes": len(page),
            "explain_output_bytes": len(explanation),
            "export_response_bytes": len(exported),
            "export_file_bytes": details["bytes"],
        }
        try:
            import tiktoken
        except ImportError:
            pass
        else:
            encoding = tiktoken.get_encoding("o200k_base")
            result["groups_tokens"] = len(encoding.encode(groups.decode("utf-8")))
            result["first_page_tokens"] = len(encoding.encode(page.decode("utf-8")))
            result["explain_tokens"] = len(encoding.encode(explanation.decode("utf-8")))
            result["export_response_tokens"] = len(encoding.encode(exported.decode("utf-8")))
        print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
