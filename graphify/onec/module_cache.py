"""Disk cache of BSL parse facts; final symbol resolution still runs for every build."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import zlib
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path


def metadata_fingerprint(root: Path) -> str:
    """Invalidate BSL facts when metadata names or properties can have changed."""
    digest = hashlib.sha256()
    for directory, folders, files in os.walk(root):
        folders.sort()
        for name in sorted(files):
            if Path(name).suffix.casefold() not in {".xml", ".mdo", ".form"}:
                continue
            path = Path(directory) / name
            stat = path.stat()
            digest.update(path.relative_to(root).as_posix().encode("utf-8"))
            digest.update(f"\0{stat.st_size}\0{stat.st_mtime_ns}\0".encode())
    return digest.hexdigest()


class ModuleCache:
    def __init__(self, path: Path, root: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=NORMAL")
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS modules (file TEXT PRIMARY KEY, "
            "metadata_sig TEXT NOT NULL, content_sig TEXT NOT NULL, payload BLOB NOT NULL)"
        )
        parser_revision = hashlib.sha256()
        parser_revision.update(metadata_fingerprint(root).encode())
        for source in (Path(__file__), Path(__file__).with_name("project.py"),
                       Path(__file__).with_name("metadata_types.py")):
            parser_revision.update(source.read_bytes())
        try:
            parser_revision.update(version("tree-sitter-bsl").encode())
        except PackageNotFoundError:
            pass
        self.metadata_sig = parser_revision.hexdigest()
        self.pending_writes = 0
        self.hits = 0
        self.misses = 0

    def get(self, file: Path, data: bytes) -> dict | None:
        content_sig = hashlib.sha256(data).hexdigest()
        row = self.db.execute(
            "SELECT payload FROM modules WHERE file=? AND metadata_sig=? AND content_sig=?",
            (str(file.resolve()), self.metadata_sig, content_sig),
        ).fetchone()
        if row is None:
            self.misses += 1
            return None
        try:
            payload = json.loads(zlib.decompress(row[0]))
        except (ValueError, zlib.error):
            self.misses += 1
            return None
        self.hits += 1
        return payload

    def put(self, file: Path, data: bytes, payload: dict) -> None:
        self.db.execute(
            "INSERT OR REPLACE INTO modules(file,metadata_sig,content_sig,payload) "
            "VALUES(?,?,?,?)",
            (
                str(file.resolve()),
                self.metadata_sig,
                hashlib.sha256(data).hexdigest(),
                zlib.compress(json.dumps(payload, ensure_ascii=False).encode("utf-8"), level=3),
            ),
        )
        self.pending_writes += 1
        if self.pending_writes >= 100:
            self.db.commit()
            self.pending_writes = 0

    def close(self) -> None:
        self.db.commit()
        self.db.close()
