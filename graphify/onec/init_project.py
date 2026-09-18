"""Add small, project-local agent instructions for one installed Graphify-1C CLI."""

from __future__ import annotations

import argparse
from pathlib import Path

from graphify.onec.project import _is_project_export, _project_layout

_START = "<!-- graphify-1c:start -->"
_END = "<!-- graphify-1c:end -->"
_AGENT_FILES = {
    "codex": Path("AGENTS.md"),
    "claude": Path("CLAUDE.md"),
    "vscode": Path(".github/copilot-instructions.md"),
}
_RULES = """## Graphify-1C: граф этого проекта

Работай из корня этого проекта. Graphify-1C установлен отдельно и доступен как
`graphify-1c`. Основная конфигурация — `src/cf`. В каталоге `src/cfe/` могут
быть и другие расширения, не входящие в этот граф: не включай их без запроса.

Сначала прочитай только `graphify-out/.graphify_onec.json`, если он есть.
Это маленький манифест текущего проекта: `root` указывает выгрузку конфигурации,
`extensions` — точно выбранные расширения, `out` — JSON, `html` — HTML.
Путь к SQLite-индексу получается заменой расширения файла `html` на `.sqlite`.
Назови этот путь `<индекс>` в командах ниже. Если манифеста нет, используй
`graphify-out/graph.sqlite` как `<индекс>` и стандартную структуру `src/`.
Не путай пути индекса разных проектов и не открывай большой JSON целиком.

Для вопросов о структуре, вызовах, метаданных и местах использования:

1. Если `<индекс>` отсутствует, при наличии манифеста выполни
   `graphify-1c update .` — команда сохранит выбранные расширения и пути.
   Без манифеста выполни
   `graphify-1c analyze ./src --out graphify-out/graph.json --html graphify-out/graph.html`.
   Для небольшого графа создай индекс командой
   `graphify-1c index build graphify-out/graph.json graphify-out/graph.sqlite`.
2. Найди узел: `graphify-1c index search '<индекс>' '<имя>'`.
   Выбери точный ID по `kind`, `source_type`, `extension_name` и пути.
3. Сначала получи счётчики через `graphify-1c index groups '<индекс>' '<id>' --direction in`.
   Затем запрашивай конкретный тип связей страницами по 10:
   `graphify-1c index neighbors '<индекс>' '<id>' --direction in --relation calls --limit 10 --offset 0`.
   Для всех мест использования продолжай `--offset 10`, `20` и далее до пустой
   страницы; проверь нужные отношения (`calls`, `references`, `type_reference`,
   `query_reads`, `writes`, `EXTENDS`, `BEFORE`, `AFTER`, `INSTEAD`, `CHANGE_CONTROL`).
4. Если связей тысячи, запиши полный перечень через
   `graphify-1c index export '<индекс>' '<id>' graphify-out/usages.jsonl --direction in --relation calls`.
   Читай из JSONL только нужные строки. Не выводи файл или `graph.json` целиком
   в диалог. Ответы команд ограничены 12 КиБ.
5. Проверь важные выводы по `source_file` и `start_line` в XML/BSL. Укажи тип
   связи и уверенность; `INFERRED` и `AMBIGUOUS` не выдавай за доказанный вызов.
   После изменения исходников выполни `graphify-1c update .`.

Не используй универсальные `graphify query/path/explain` для большого графа 1С:
они могут пытаться читать JSON. Для этого проекта используй `graphify-1c index`.
"""


def _write_section(path: Path) -> str:
    if path.is_symlink():
        raise ValueError(f"refusing to edit symlink: {path}")
    section = f"{_START}\n{_RULES}{_END}\n"
    existed = path.exists()
    if existed:
        existing = path.read_text(encoding="utf-8")
        if _START in existing or _END in existing:
            if existing.count(_START) != 1 or existing.count(_END) != 1:
                raise ValueError(f"broken Graphify-1C markers in {path}")
            start = existing.index(_START)
            end = existing.index(_END) + len(_END)
            updated = existing[:start] + section.rstrip("\n") + existing[end:]
        else:
            updated = existing.rstrip("\n") + "\n\n" + section
        if updated == existing:
            return "unchanged"
    else:
        updated = section
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(updated, encoding="utf-8")
    return "updated" if existed else "created"


def init_project(root: Path, agents: list[str]) -> list[tuple[Path, str]]:
    root = root.resolve()
    base, _ = _project_layout(root)
    if base != root / "src" / "cf" or not _is_project_export(base):
        raise ValueError(f"1C project not found at {root}: expected src/cf export")
    targets = [root / _AGENT_FILES[agent] for agent in dict.fromkeys(agents)]
    ignore = root / ".gitignore"
    for target in [*targets, ignore]:
        if target.is_symlink():
            raise ValueError(f"refusing to edit symlink: {target}")
    changed = []
    for target in targets:
        changed.append((target, _write_section(target)))
    current = ignore.read_text(encoding="utf-8") if ignore.exists() else ""
    if "graphify-out/" not in {line.strip() for line in current.splitlines()}:
        prefix = current.rstrip("\n")
        ignore.write_text((prefix + "\n" if prefix else "") + "graphify-out/\n", encoding="utf-8")
        changed.append((ignore, "updated"))
    return changed


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="graphify-1c init-project")
    parser.add_argument("root", nargs="?", type=Path, default=Path("."))
    parser.add_argument(
        "--agents", default="codex,claude,vscode",
        help="comma-separated: codex,claude,vscode (default: all three)",
    )
    args = parser.parse_args(argv)
    agents = [part.strip().lower() for part in args.agents.split(",")]
    if not agents or any(agent not in _AGENT_FILES for agent in agents):
        parser.error("--agents must contain codex, claude and/or vscode")
    try:
        changed = init_project(args.root, agents)
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    for path, status in changed:
        print(f"{status}: {path}")
    print("Next: graphify-1c analyze ./src --out graphify-out/graph.json --html graphify-out/graph.html")


if __name__ == "__main__":
    main()
