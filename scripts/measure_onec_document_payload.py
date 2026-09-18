"""Measure tokens in minimal graph facts and raw rg output for document questions."""

from __future__ import annotations

import argparse
import json
import sqlite3
import subprocess
from pathlib import Path

import tiktoken

from benchmark_onec_documents import CASH, ORDER, EXTENSIONS


def search(pattern: str, paths: list[Path], *options: str) -> str:
    paths = [path for path in paths if path.exists()]
    if not paths:
        return ""
    result = subprocess.run(
        ["rg", *options, pattern, *(str(path) for path in paths)],
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        text=True,
    )
    if result.returncode not in (0, 1):
        raise RuntimeError(result.stderr)
    return result.stdout.strip()


def rows(db: sqlite3.Connection, sql: str, *params: str) -> str:
    result = db.execute(sql, params).fetchall()
    return "\n".join(" | ".join(str(value) for value in row) for row in result)


def module(src: Path, doc: str) -> Path:
    return src / "cf" / "Documents" / doc / "Ext" / "ObjectModule.bsl"


def extension_paths(src: Path, doc: str, metadata: bool) -> list[Path]:
    suffix = f"{doc}.xml" if metadata else doc
    return [src / "cfe" / name / "Documents" / suffix for name in EXTENSIONS]


def cases(src: Path, db: sqlite3.Connection) -> list[tuple[int, str, str]]:
    result = []
    for number, doc in ((1, CASH), (2, ORDER)):
        graph = rows(
            db,
            "SELECT DISTINCT n.extension_name FROM edges e JOIN nodes n ON n.id=e.source "
            "WHERE e.target=? AND e.relation='EXTENDS' ORDER BY n.extension_name",
            f"1c://Document/{doc}",
        )
        source = search(
            "<ObjectBelonging>Adopted</ObjectBelonging>",
            extension_paths(src, doc, True),
            "-l",
            "-F",
        )
        result.append((number, graph, source))
    for number, doc in ((3, CASH), (4, ORDER)):
        graph = rows(
            db,
            "SELECT relation,target FROM edges WHERE target LIKE ? "
            "AND relation IN ('BEFORE','AFTER','INSTEAD','CHANGE_CONTROL') "
            "ORDER BY relation,target",
            f"1c://Document/{doc}/%",
        )
        source = search(
            r"^\s*&(Перед|После|Вместо|ИзменениеИКонтроль)\(",
            extension_paths(src, doc, False),
            "-n",
            "--glob",
            "*.bsl",
        )
        result.append((number, graph, source))
    for number, doc in ((5, CASH), (6, ORDER)):
        graph = rows(
            db,
            "SELECT target FROM edges WHERE source=? AND relation='calls' ORDER BY target",
            f"1c://Document/{doc}/ObjectModule/ОбработкаПроведения",
        )
        source = search(r"^Процедура ОбработкаПроведения\(", [module(src, doc)], "-n", "-A", "30")
        source = source.split("КонецПроцедуры", 1)[0] + "КонецПроцедуры"
        result.append((number, graph, source))
    target = (
        "1c://InformationRegister/СтатусыСборкиИДоставки/ManagerModule/ЗаписатьСтатусИзРаспоряжения"
    )
    graph = rows(
        db,
        "SELECT target FROM edges WHERE source=? AND target=? AND relation='calls'",
        f"1c://Document/{ORDER}/ObjectModule/ОбработкаПроведения",
        target,
    )
    source = search(
        "Процедура ЗаписатьСтатусИзРаспоряжения",
        [src / "cf" / "InformationRegisters"],
        "-n",
        "-F",
        "--glob",
        "ManagerModule.bsl",
    )
    result.append((7, graph, source))
    graph = rows(
        db,
        "SELECT target FROM edges WHERE source=? AND relation='writes'",
        "1c://InformationRegister/СтатусыСборкиИДоставки/ManagerModule/ЗаписатьСтатус",
    )
    source = search(
        "ЗаписьРегистра.Записать(Истина)",
        [
            src
            / "cf"
            / "InformationRegisters"
            / "СтатусыСборкиИДоставки"
            / "Ext"
            / "ManagerModule.bsl"
        ],
        "-n",
        "-F",
    )
    result.append((8, graph, source))
    for number, doc, attribute in ((9, CASH, "БанковскийСчет"), (10, ORDER, "ГрафикОплаты")):
        graph = rows(
            db,
            "SELECT target FROM edges WHERE source=? AND relation='type_reference'",
            f"1c://Document/{doc}/Attribute/{attribute}",
        )
        source = search(
            f"<Name>{attribute}</Name>",
            [src / "cf" / "Documents" / f"{doc}.xml"],
            "-n",
            "-A",
            "12",
            "-F",
        )
        result.append((number, graph, source))
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--src", required=True, type=Path)
    parser.add_argument("--database", required=True, type=Path)
    args = parser.parse_args()
    encoding = tiktoken.get_encoding("o200k_base")
    with sqlite3.connect(args.database) as db:
        values = cases(args.src, db)
    output = [
        {
            "question": number,
            "graph_tokens": len(encoding.encode(graph)),
            "source_tokens": len(encoding.encode(source)),
            "graph_chars": len(graph),
            "source_chars": len(source),
        }
        for number, graph, source in values
    ]
    print(json.dumps(output, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
