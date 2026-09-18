"""Disk-backed index for exploring large 1C Graphify JSON exports."""

from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path
from time import perf_counter


NODE_FIELDS = {"id", "label", "kind", "source_file", "start_line", "source_type", "extension_name"}
EDGE_FIELDS = {"source", "target", "relation", "confidence"}


def _records(path: Path):
    """Read the indented node-link JSON written by Graphify without loading it."""
    section = None
    record = None
    with path.open(encoding="utf-8") as stream:
        for line in stream:
            if line == '  "nodes": [\n':
                section = "nodes"
                continue
            if line == '  "links": [\n':
                section = "links"
                continue
            if line.startswith("  ],") or line == "  ]\n":
                section = None
                continue
            if section is None:
                continue
            if line == "    {\n":
                record = {}
                continue
            if line in ("    },\n", "    }\n"):
                if record is not None:
                    yield section, record
                record = None
                continue
            if record is None or not line.startswith('      "'):
                continue
            key, separator, raw = line[7:].partition('": ')
            if not separator or key not in (NODE_FIELDS if section == "nodes" else EDGE_FIELDS):
                continue
            raw = raw.rstrip("\r\n").removesuffix(",")
            if raw.startswith(("[", "{")):
                continue
            record[key] = json.loads(raw)


def build_index(graph_path: Path, db_path: Path) -> dict[str, int | float]:
    """Build a queryable index with bounded Python memory use."""
    graph_path = Path(graph_path)
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    started = perf_counter()
    with sqlite3.connect(db_path) as db:
        db.executescript(
            """
            PRAGMA journal_mode=WAL;
            DROP TABLE IF EXISTS nodes;
            DROP TABLE IF EXISTS edges;
            CREATE TABLE nodes (
              id TEXT PRIMARY KEY, label TEXT, kind TEXT, source_file TEXT,
              start_line INTEGER, source_type TEXT, extension_name TEXT,
              norm_label TEXT, norm_id TEXT
            );
            CREATE TABLE edges (
              source TEXT, target TEXT, relation TEXT, confidence TEXT
            );
            """
        )
        nodes = edges = 0
        node_batch = []
        edge_batch = []
        for section, item in _records(graph_path):
            if section == "nodes":
                node_batch.append(
                    (
                        *(
                            item.get(key)
                            for key in (
                                "id",
                                "label",
                                "kind",
                                "source_file",
                                "start_line",
                                "source_type",
                                "extension_name",
                            )
                        ),
                        (item.get("label") or "").casefold(),
                        (item.get("id") or "").casefold(),
                    )
                )
                if len(node_batch) >= 5000:
                    db.executemany("INSERT INTO nodes VALUES (?,?,?,?,?,?,?,?,?)", node_batch)
                    nodes += len(node_batch)
                    node_batch.clear()
            else:
                edge_batch.append(
                    tuple(item.get(key) for key in ("source", "target", "relation", "confidence"))
                )
                if len(edge_batch) >= 5000:
                    db.executemany("INSERT INTO edges VALUES (?,?,?,?)", edge_batch)
                    edges += len(edge_batch)
                    edge_batch.clear()
        if node_batch:
            db.executemany("INSERT INTO nodes VALUES (?,?,?,?,?,?,?,?,?)", node_batch)
            nodes += len(node_batch)
        if edge_batch:
            db.executemany("INSERT INTO edges VALUES (?,?,?,?)", edge_batch)
            edges += len(edge_batch)
        if not nodes or not edges:
            raise ValueError("Graphify node-link JSON has no nodes or links")
        db.executescript(
            """
            CREATE INDEX edges_source ON edges(source, relation);
            CREATE INDEX edges_target ON edges(target, relation);
            CREATE INDEX edges_relation ON edges(relation);
            CREATE INDEX nodes_label ON nodes(norm_label);
            CREATE INDEX nodes_extension ON nodes(extension_name);
            """
        )
        db.commit()
    return {"nodes": nodes, "edges": edges, "seconds": round(perf_counter() - started, 2)}


def search(db_path: Path, term: str, limit: int = 20, offset: int = 0) -> list[dict]:
    if limit < 1 or offset < 0:
        raise ValueError("limit must be positive and offset non-negative")
    with sqlite3.connect(db_path) as db:
        db.row_factory = sqlite3.Row
        rows = db.execute(
            "SELECT id,label,kind,source_file,start_line,source_type,extension_name "
            "FROM nodes WHERE norm_label LIKE ? OR norm_id LIKE ? ORDER BY id LIMIT ? OFFSET ?",
            (f"%{term.casefold()}%", f"%{term.casefold()}%", limit, offset),
        )
        return [dict(row) for row in rows]


def neighbors(
    db_path: Path,
    node_id: str,
    *,
    direction: str = "both",
    relation: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> list[dict]:
    if direction not in {"in", "out", "both"}:
        raise ValueError("direction must be in, out, or both")
    if limit < 1 or offset < 0:
        raise ValueError("limit must be positive and offset non-negative")
    clauses = []
    parameters: list[str | int] = []
    if direction in {"out", "both"}:
        clauses.append("e.source = ?")
        parameters.append(node_id)
    if direction in {"in", "both"}:
        clauses.append("e.target = ?")
        parameters.append(node_id)
    sql = (
        "SELECT e.source, e.target, e.relation, e.confidence, "
        "s.label AS source_label, s.kind AS source_kind, "
        "s.source_type AS source_type, s.extension_name AS source_extension, "
        "s.source_file AS source_file, s.start_line AS source_line, "
        "t.label AS target_label, t.kind AS target_kind, "
        "t.source_type AS target_type, t.extension_name AS target_extension, "
        "t.source_file AS target_file, t.start_line AS target_line "
        "FROM edges e LEFT JOIN nodes s ON s.id=e.source LEFT JOIN nodes t ON t.id=e.target "
        "WHERE (" + " OR ".join(clauses) + ")"
    )
    if relation:
        sql += " AND e.relation = ?"
        parameters.append(relation)
    sql += " ORDER BY e.rowid LIMIT ? OFFSET ?"
    parameters.extend((limit, offset))
    with sqlite3.connect(db_path) as db:
        db.row_factory = sqlite3.Row
        return [dict(row) for row in db.execute(sql, parameters)]


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="graphify-1c index")
    sub = parser.add_subparsers(dest="command", required=True)
    build = sub.add_parser("build")
    build.add_argument("graph", type=Path)
    build.add_argument("database", type=Path)
    find = sub.add_parser("search")
    find.add_argument("database", type=Path)
    find.add_argument("term")
    find.add_argument("--limit", type=int, default=20)
    find.add_argument("--offset", type=int, default=0)
    adjacent = sub.add_parser("neighbors")
    adjacent.add_argument("database", type=Path)
    adjacent.add_argument("node_id")
    adjacent.add_argument("--direction", choices=("in", "out", "both"), default="both")
    adjacent.add_argument("--relation")
    adjacent.add_argument("--limit", type=int, default=100)
    adjacent.add_argument("--offset", type=int, default=0)
    args = parser.parse_args(argv)
    if args.command == "build":
        print(json.dumps(build_index(args.graph, args.database), ensure_ascii=False))
    elif args.command == "search":
        print(
            json.dumps(
                search(args.database, args.term, limit=args.limit, offset=args.offset),
                ensure_ascii=False,
                indent=2,
            )
        )
    else:
        print(
            json.dumps(
                neighbors(
                    args.database,
                    args.node_id,
                    direction=args.direction,
                    relation=args.relation,
                    limit=args.limit,
                    offset=args.offset,
                ),
                ensure_ascii=False,
                indent=2,
            )
        )


if __name__ == "__main__":
    main()
