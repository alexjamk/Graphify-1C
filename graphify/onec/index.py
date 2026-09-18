"""Disk-backed index for exploring large 1C Graphify JSON exports."""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
from collections import deque
from contextlib import closing
from pathlib import Path
from time import perf_counter
from uuid import uuid4


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


def _build_index_file(graph_path: Path, db_path: Path) -> dict[str, int | float]:
    """Populate a new SQLite file with bounded Python memory use."""
    graph_path = Path(graph_path)
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    started = perf_counter()
    with closing(sqlite3.connect(db_path)) as db:
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
        try:
            db.execute(
                "CREATE VIRTUAL TABLE nodes_fts USING fts5("
                "norm_label, norm_id, content='nodes', content_rowid='rowid', tokenize='trigram')"
            )
            db.execute(
                "INSERT INTO nodes_fts(rowid,norm_label,norm_id) "
                "SELECT rowid,norm_label,norm_id FROM nodes"
            )
        except sqlite3.OperationalError as exc:
            if "no such tokenizer" not in str(exc).lower() and "no such module" not in str(exc).lower():
                raise
        db.commit()
    return {"nodes": nodes, "edges": edges, "seconds": round(perf_counter() - started, 2)}


def build_index(graph_path: Path, db_path: Path) -> dict[str, int | float]:
    """Replace the published index only after a complete successful build."""
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = db_path.with_name(f".{db_path.name}.{uuid4().hex}.tmp")
    try:
        result = _build_index_file(graph_path, temporary)
        os.replace(temporary, db_path)
        return result
    finally:
        temporary.unlink(missing_ok=True)
        temporary.with_name(temporary.name + "-wal").unlink(missing_ok=True)
        temporary.with_name(temporary.name + "-shm").unlink(missing_ok=True)


def search(db_path: Path, term: str, limit: int = 20, offset: int = 0) -> list[dict]:
    if limit < 1 or offset < 0:
        raise ValueError("limit must be positive and offset non-negative")
    with closing(sqlite3.connect(db_path)) as db:
        db.row_factory = sqlite3.Row
        normalized = term.casefold()
        has_fts = db.execute(
            "SELECT 1 FROM sqlite_master WHERE name='nodes_fts'"
        ).fetchone() is not None
        if has_fts and len(normalized) >= 3:
            rows = db.execute(
                "SELECT n.id,n.label,n.kind,n.source_file,n.start_line,n.source_type,n.extension_name "
                "FROM nodes_fts JOIN nodes n ON n.rowid=nodes_fts.rowid "
                "WHERE nodes_fts MATCH ? ORDER BY n.id LIMIT ? OFFSET ?",
                ('"' + normalized.replace('"', '""') + '"', limit, offset),
            )
        else:
            rows = db.execute(
                "SELECT id,label,kind,source_file,start_line,source_type,extension_name "
                "FROM nodes WHERE norm_label LIKE ? OR norm_id LIKE ? ORDER BY id LIMIT ? OFFSET ?",
                (f"%{normalized}%", f"%{normalized}%", limit, offset),
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
    with closing(sqlite3.connect(db_path)) as db:
        db.row_factory = sqlite3.Row
        return [dict(row) for row in db.execute(sql, parameters)]


def shortest_path(
    db_path: Path,
    source: str,
    target: str,
    *,
    max_hops: int = 6,
    max_visited: int = 50000,
) -> dict:
    """Find a directed path using SQLite edge indexes and bounded Python state."""
    if max_hops < 0 or max_visited < 1:
        raise ValueError("max_hops must be non-negative and max_visited positive")
    with closing(sqlite3.connect(db_path)) as db:
        db.row_factory = sqlite3.Row
        for node_id in (source, target):
            if db.execute("SELECT 1 FROM nodes WHERE id=?", (node_id,)).fetchone() is None:
                raise KeyError(node_id)
        queue = deque([(source, 0)])
        previous: dict[str, tuple[str, str] | None] = {source: None}
        while queue and target not in previous:
            current, depth = queue.popleft()
            if depth >= max_hops:
                continue
            for row in db.execute(
                "SELECT target,relation FROM edges WHERE source=? ORDER BY rowid", (current,)
            ):
                neighbor = row["target"]
                if neighbor in previous:
                    continue
                if len(previous) >= max_visited:
                    return {"found": False, "truncated": True, "visited": len(previous), "path": []}
                previous[neighbor] = (current, row["relation"])
                queue.append((neighbor, depth + 1))
                if neighbor == target:
                    break
        if target not in previous:
            return {"found": False, "truncated": False, "visited": len(previous), "path": []}
        route = []
        current = target
        while current != source:
            predecessor, relation = previous[current]
            route.append({"source": predecessor, "target": current, "relation": relation})
            current = predecessor
        route.reverse()
        return {"found": True, "truncated": False, "visited": len(previous), "path": route}


def explain(db_path: Path, node_id: str, *, sample: int = 20) -> dict:
    """Return bounded node context from SQLite for an agent."""
    from graphify.onec.serve import neighbor_groups

    if sample < 0:
        raise ValueError("sample must be non-negative")
    with closing(sqlite3.connect(db_path)) as db:
        db.row_factory = sqlite3.Row
        row = db.execute(
            "SELECT id,label,kind,source_file,start_line,source_type,extension_name "
            "FROM nodes WHERE id=?", (node_id,)
        ).fetchone()
        if row is None:
            raise KeyError(node_id)
        node = dict(row)
    return {
        "node": node,
        "groups": neighbor_groups(db_path, node_id),
        "sample": neighbors(db_path, node_id, limit=sample) if sample else [],
    }


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
    route = sub.add_parser("path")
    route.add_argument("database", type=Path)
    route.add_argument("source")
    route.add_argument("target")
    route.add_argument("--max-hops", type=int, default=6)
    route.add_argument("--max-visited", type=int, default=50000)
    summary = sub.add_parser("explain")
    summary.add_argument("database", type=Path)
    summary.add_argument("node_id")
    summary.add_argument("--sample", type=int, default=20)
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
    elif args.command == "path":
        print(
            json.dumps(
                shortest_path(
                    args.database, args.source, args.target,
                    max_hops=args.max_hops, max_visited=args.max_visited,
                ),
                ensure_ascii=False, indent=2,
            )
        )
    elif args.command == "explain":
        print(json.dumps(explain(args.database, args.node_id, sample=args.sample),
                         ensure_ascii=False, indent=2))
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
