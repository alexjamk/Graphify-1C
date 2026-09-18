"""Time ten documented questions using a graph or source search."""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import statistics
import subprocess
from pathlib import Path
from time import perf_counter


CASH = "ПоступлениеБезналичныхДенежныхСредств"
ORDER = "ЗаказКлиента"
HOOKS = {
    "Перед": "BEFORE",
    "После": "AFTER",
    "Вместо": "INSTEAD",
    "ИзменениеИКонтроль": "CHANGE_CONTROL",
}
CALL = re.compile(r"(?<![\w])([\w]+(?:\.[\w]+){1,2})\s*\(")
HOOK = re.compile(r'&(?P<kind>Перед|После|Вместо|ИзменениеИКонтроль)\("(?P<target>[^"]+)"\)')


def query(database: Path, sql: str, params: tuple = ()) -> set:
    with sqlite3.connect(database) as db:
        return set(db.execute(sql, params).fetchall())


def rg(pattern: str, paths: list[Path], *options: str) -> list[str]:
    paths = [path for path in paths if path.exists()]
    if not paths:
        return []
    result = subprocess.run(
        ["rg", *options, pattern, *(str(path) for path in paths)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if result.returncode not in (0, 1):
        raise RuntimeError(result.stderr)
    return result.stdout.splitlines()


def graph_extensions(database: Path, document: str) -> set[str]:
    rows = query(
        database,
        "SELECT n.extension_name FROM edges e JOIN nodes n ON n.id=e.source "
        "WHERE e.target=? AND e.relation='EXTENDS'",
        (f"1c://Document/{document}",),
    )
    return {row[0] for row in rows}


def source_extensions(src: Path, document: str, extensions: list[str]) -> set[str]:
    paths = [src / "cfe" / extension / "Documents" / f"{document}.xml" for extension in extensions]
    lines = rg("<ObjectBelonging>Adopted</ObjectBelonging>", paths, "-l", "-F")
    return {Path(line).parent.parent.name for line in lines}


def graph_hooks(database: Path, document: str) -> set[tuple[str, str]]:
    rows = query(
        database,
        "SELECT e.relation,e.target FROM edges e WHERE e.target LIKE ? "
        "AND e.relation IN ('BEFORE','AFTER','INSTEAD','CHANGE_CONTROL')",
        (f"1c://Document/{document}/%",),
    )
    return {(relation, target.rsplit("/", 1)[-1]) for relation, target in rows}


def source_hooks(src: Path, document: str, extensions: list[str]) -> set[tuple[str, str]]:
    paths = [src / "cfe" / extension / "Documents" / document for extension in extensions]
    lines = rg(r"^\s*&(Перед|После|Вместо|ИзменениеИКонтроль)\(", paths, "-n", "--glob", "*.bsl")
    return {
        (HOOKS[match["kind"]], match["target"]) for line in lines if (match := HOOK.search(line))
    }


def _call_label(target: str) -> str:
    parts = target.removeprefix("1c://").split("/")
    if parts[0] == "CommonModule":
        return f"{parts[1]}.{parts[-1]}"
    if parts[0] == "InformationRegister":
        return f"РегистрыСведений.{parts[1]}.{parts[-1]}"
    return target


def graph_calls(database: Path, document: str) -> set[str]:
    rows = query(
        database,
        "SELECT target FROM edges WHERE source=? AND relation='calls'",
        (f"1c://Document/{document}/ObjectModule/ОбработкаПроведения",),
    )
    return {_call_label(row[0]) for row in rows}


def source_calls(src: Path, document: str) -> set[str]:
    module = src / "cf" / "Documents" / document / "Ext" / "ObjectModule.bsl"
    lines = rg(r"^Процедура ОбработкаПроведения\(", [module], "-n", "-A", "30")
    calls = set()
    for line in lines[1:]:
        body = re.sub(r"^\d+[-:]", "", line)
        if "КонецПроцедуры" in body:
            break
        calls.update(match.group(1) for match in CALL.finditer(body))
    return calls


def graph_manager_target(database: Path) -> set[str]:
    source = f"1c://Document/{ORDER}/ObjectModule/ОбработкаПроведения"
    target = (
        "1c://InformationRegister/СтатусыСборкиИДоставки/ManagerModule/ЗаписатьСтатусИзРаспоряжения"
    )
    rows = query(
        database,
        "SELECT target FROM edges WHERE source=? AND target=? AND relation='calls'",
        (source, target),
    )
    return {row[0] for row in rows}


def source_manager_target(src: Path) -> set[str]:
    paths = [src / "cf" / "InformationRegisters"]
    lines = rg(
        "Процедура ЗаписатьСтатусИзРаспоряжения", paths, "-l", "-F", "--glob", "ManagerModule.bsl"
    )
    return {
        f"1c://InformationRegister/{Path(line).parents[1].name}/ManagerModule/ЗаписатьСтатусИзРаспоряжения"
        for line in lines
    }


def graph_register_write(database: Path) -> set[str]:
    source = "1c://InformationRegister/СтатусыСборкиИДоставки/ManagerModule/ЗаписатьСтатус"
    rows = query(
        database, "SELECT target FROM edges WHERE source=? AND relation='writes'", (source,)
    )
    return {row[0] for row in rows}


def source_register_write(src: Path) -> set[str]:
    module = (
        src / "cf" / "InformationRegisters" / "СтатусыСборкиИДоставки" / "Ext" / "ManagerModule.bsl"
    )
    lines = rg("ЗаписьРегистра.Записать(Истина)", [module], "-n", "-F")
    return {"1c://InformationRegister/СтатусыСборкиИДоставки"} if lines else set()


def graph_type(database: Path, document: str, attribute: str) -> set[str]:
    rows = query(
        database,
        "SELECT target FROM edges WHERE source=? AND relation='type_reference'",
        (f"1c://Document/{document}/Attribute/{attribute}",),
    )
    return {f"CatalogRef.{target.rsplit('/', 1)[-1]}" for (target,) in rows}


def source_type(src: Path, document: str, attribute: str) -> set[str]:
    metadata = src / "cf" / "Documents" / f"{document}.xml"
    lines = rg(f"<Name>{attribute}</Name>", [metadata], "-n", "-A", "20", "-F")
    return set(re.findall(r"cfg:(CatalogRef\.[\w]+)", "\n".join(lines)))


def measure(fn, repeats: int) -> tuple[set, float]:
    samples = []
    result = set()
    for _ in range(repeats):
        start = perf_counter()
        result = fn()
        samples.append(perf_counter() - start)
    return result, statistics.median(samples) * 1000


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--src", type=Path, required=True)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--extension", action="append", required=True, dest="extensions")
    parser.add_argument("--repeats", type=int, default=7)
    args = parser.parse_args()
    if args.repeats < 1:
        parser.error("--repeats must be positive")
    cases = [
        (
            1,
            lambda: graph_extensions(args.database, CASH),
            lambda: source_extensions(args.src, CASH, args.extensions),
        ),
        (
            2,
            lambda: graph_extensions(args.database, ORDER),
            lambda: source_extensions(args.src, ORDER, args.extensions),
        ),
        (
            3,
            lambda: graph_hooks(args.database, CASH),
            lambda: source_hooks(args.src, CASH, args.extensions),
        ),
        (
            4,
            lambda: graph_hooks(args.database, ORDER),
            lambda: source_hooks(args.src, ORDER, args.extensions),
        ),
        (5, lambda: graph_calls(args.database, CASH), lambda: source_calls(args.src, CASH)),
        (6, lambda: graph_calls(args.database, ORDER), lambda: source_calls(args.src, ORDER)),
        (7, lambda: graph_manager_target(args.database), lambda: source_manager_target(args.src)),
        (8, lambda: graph_register_write(args.database), lambda: source_register_write(args.src)),
        (
            9,
            lambda: graph_type(args.database, CASH, "БанковскийСчет"),
            lambda: source_type(args.src, CASH, "БанковскийСчет"),
        ),
        (
            10,
            lambda: graph_type(args.database, ORDER, "ГрафикОплаты"),
            lambda: source_type(args.src, ORDER, "ГрафикОплаты"),
        ),
    ]
    output = []
    for number, graph_fn, source_fn in cases:
        graph_answer, graph_ms = measure(graph_fn, args.repeats)
        source_answer, source_ms = measure(source_fn, args.repeats)
        output.append(
            {
                "question": number,
                "graph_ms": round(graph_ms, 3),
                "source_ms": round(source_ms, 3),
                "graph_count": len(graph_answer),
                "source_count": len(source_answer),
                "same_answer": graph_answer == source_answer,
                "graph_only": sorted(graph_answer - source_answer),
                "source_only": sorted(source_answer - graph_answer),
            }
        )
    print(json.dumps(output, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
