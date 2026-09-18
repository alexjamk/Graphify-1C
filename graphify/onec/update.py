"""Route project updates to the 1C extractor when the input is a 1C export."""

from __future__ import annotations

import json
from pathlib import Path

from graphify.onec.project import _is_project_export, _project_layout

_MARKER = ".graphify_onec.json"


def remember_analysis(
    root: Path, extensions: list[Path], out: Path, html: Path | None
) -> None:
    """Save the exact source selection and outputs for a later `update`."""
    source_root = root.resolve()
    project_root = source_root.parent if source_root.name.casefold() == "src" else source_root
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
        args = [manifest["root"]]
        for extension in manifest.get("extensions", []):
            args.extend(("--extension", extension))
        args.extend(("--out", manifest["out"]))
        if manifest.get("html"):
            args.extend(("--html", manifest["html"]))
    else:
        args = [str(path), "--out", "graphify-out/graph.json", "--html", "graphify-out/graph.html"]
    print("Updating 1C metadata and BSL graph (full rebuild)...")
    main(args)
