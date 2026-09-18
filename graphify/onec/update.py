"""Route project updates to the 1C extractor when the input is a 1C export."""

from __future__ import annotations

import json
import os
from pathlib import Path
from uuid import uuid4

from graphify.onec.project import _is_project_export, _project_layout

_MARKER = ".graphify_onec.json"


def remember_analysis(
    root: Path, extensions: list[Path], out: Path, html: Path | None
) -> None:
    """Save the exact source selection and outputs for a later `update`."""
    source_root = root.resolve()
    if source_root.name.casefold() == "src":
        project_root = source_root.parent
    elif source_root.name.casefold() == "cf" and source_root.parent.name.casefold() == "src":
        project_root = source_root.parent.parent
    else:
        project_root = source_root
    manifest = {
        "version": 1,
        "project_root": str(project_root),
        "root": str(source_root),
        "extensions": [str(path.resolve()) for path in extensions],
        "out": str(out.resolve()),
        "html": str(html.resolve()) if html else None,
    }
    destinations = {project_root / "graphify-out", out.resolve().parent}
    for directory in destinations:
        directory.mkdir(parents=True, exist_ok=True)
        (directory / _MARKER).write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )


def _manifest_for(path: Path) -> dict | None:
    candidates = (Path.cwd() / "graphify-out" / _MARKER, path / "graphify-out" / _MARKER)
    for marker in candidates:
        if not marker.is_file():
            continue
        try:
            data = json.loads(marker.read_text(encoding="utf-8"))
            root = Path(data["root"]).resolve()
            project_root = Path(data["project_root"]).resolve()
            if path in {root, project_root}:
                return data
        except (OSError, ValueError, KeyError, TypeError):
            continue
    return None


def is_onec_project(path: Path) -> bool:
    path = path.resolve()
    if _manifest_for(path):
        return True
    base, _ = _project_layout(path)
    return _is_project_export(base)


def update_project(path: Path) -> None:
    """Rebuild the 1C graph with the same selection and outputs as `analyze`."""
    from graphify.onec.cli import main

    path = path.resolve()
    manifest = _manifest_for(path)
    if manifest:
        root = Path(manifest["root"])
        extensions = [Path(item) for item in manifest.get("extensions", [])]
        out = Path(manifest["out"])
        html = Path(manifest["html"]) if manifest.get("html") else None
    else:
        root = path
        extensions = []
        out = Path("graphify-out/graph.json").resolve()
        html = Path("graphify-out/graph.html").resolve()
    token = uuid4().hex
    temporary_json = out.with_name(f".{out.stem}.{token}.tmp{out.suffix}")
    temporary_html = (
        html.with_name(f".{html.stem}.{token}.tmp{html.suffix}") if html else None
    )
    args = [str(root)]
    for extension in extensions:
        args.extend(("--extension", str(extension)))
    args.extend(("--out", str(temporary_json)))
    if temporary_html:
        args.extend(("--html", str(temporary_html)))
    print("Updating 1C metadata and BSL graph (full rebuild)...")
    try:
        main(args, record_manifest=False)
        os.replace(temporary_json, out)
        if temporary_html and html:
            for suffix in (".sqlite", ".overview.json", ".html"):
                source = temporary_html.with_suffix(suffix)
                if source.exists():
                    os.replace(source, html.with_suffix(suffix))
        remember_analysis(root, extensions, out, html)
    finally:
        temporary_json.unlink(missing_ok=True)
        if temporary_html:
            for suffix in (".sqlite", ".overview.json", ".html"):
                temporary_html.with_suffix(suffix).unlink(missing_ok=True)
