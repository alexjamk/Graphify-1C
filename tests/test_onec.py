from pathlib import Path
import json
import os
import shutil
import subprocess
import sys

import networkx as nx
import pytest

from graphify.export import to_json
from graphify.onec import extract_project
from graphify.onec.graph import build_onec_graph
from graphify.onec.export import write_onec_json
from graphify.onec.index import (
    _print_agent_result, build_index, explain, main as index_main,
    neighbors, search, shortest_path, export_neighbors,
)
from graphify.onec.viewer import write_onec_html
from graphify.onec.update import _manifest_for, remember_analysis
from graphify.validate import validate_extraction


FIXTURE = Path(__file__).parent / "fixtures" / "onec"
EXTENSION = Path(__file__).parent / "fixtures" / "onec_extension"
EDT = Path(__file__).parent / "fixtures" / "onec_edt"


def test_record_manager_variable_write_and_reassignment(tmp_path):
    root = tmp_path / "cf"
    shutil.copytree(FIXTURE, root)
    manager = root / "InformationRegisters" / "Цены" / "Ext" / "ManagerModule.bsl"
    manager.parent.mkdir(parents=True)
    manager.write_text(
        "Процедура ЗаписатьСтатус() Экспорт\n"
        " Запись = РегистрыСведений.Цены.СоздатьМенеджерЗаписи();\n"
        " Запись.Записать(Истина);\n"
        "КонецПроцедуры\n"
        "Процедура Переназначить()\n"
        " Запись = РегистрыСведений.Цены.СоздатьМенеджерЗаписи();\n"
        " Запись = Неопределено;\n"
        " Запись.Записать();\n"
        "КонецПроцедуры\n",
        encoding="utf-8",
    )
    edges = extract_project(root)["edges"]
    target = "1c://InformationRegister/Цены"
    assert any(
        edge["source"] == f"{target}/ManagerModule/ЗаписатьСтатус"
        and edge["target"] == target
        and edge["relation"] == "writes"
        and edge["confidence"] == "INFERRED"
        for edge in edges
    )
    assert not any(
        edge["source"] == f"{target}/ManagerModule/Переназначить" and edge["relation"] == "writes"
        for edge in edges
    )


def test_onec_graph():
    graph = extract_project(FIXTURE)
    assert validate_extraction(graph) == []
    assert graph["diagnostics"]["procedures"] > 0
    assert graph["diagnostics"]["calls_resolved"]["AMBIGUOUS"] == 2
    assert graph["diagnostics"]["unresolved_calls"] >= 1
    nodes = {node["id"]: node for node in graph["nodes"]}
    procedure = "1c://Document/ЗаказКлиента/ObjectModule/ОбработкаПроведения"
    function = "1c://CommonModule/ЦенообразованиеСервер/CommonModule/ПолучитьЦену"
    assert nodes[procedure]["execution_context"] == "НаСервере"
    assert nodes[procedure]["parameters"] == ["Отказ"]
    assert nodes[procedure]["metadata_object"] == "1c://Document/ЗаказКлиента"
    assert nodes[function]["export"] is True
    assert "1c://Configuration/Test/SessionModule/ПриНачалеСеанса" in nodes
    edges = {(e["source"], e["target"], e["relation"]) for e in graph["edges"]}
    assert (procedure, function, "calls") in edges
    assert (
        procedure,
        "1c://Document/ЗаказКлиента/ObjectModule/РассчитатьСумму",
        "calls",
    ) in edges
    assert (procedure, "1c://InformationRegister/Цены", "query_reads") in edges
    assert (procedure, "1c://AccumulationRegister/ОстаткиТоваров", "writes") in edges
    assert (
        "1c://Document/ЗаказКлиента/ObjectModule/НастроитьДвижения",
        "1c://AccumulationRegister/ОстаткиТоваров",
        "writes",
    ) in edges
    assert (
        "1c://Document/ЗаказКлиента",
        "1c://AccumulationRegister/ОстаткиТоваров",
        "writes",
    ) in edges
    assert not any(
        edge["source"].endswith("/ОтключитьДвижения") and edge["relation"] == "writes"
        for edge in graph["edges"]
    )
    dynamic = "1c://Document/ЗаказКлиента/ObjectModule/ДинамическийЗапрос"
    dynamic_reads = [
        e for e in graph["edges"] if e["source"] == dynamic and e["relation"] == "query_reads"
    ]
    assert len(dynamic_reads) == 1
    assert dynamic_reads[0]["confidence"] == "INFERRED"
    assert dynamic_reads[0]["partial"] is True
    assert not any(
        e["source"].endswith("/ПростоСообщение") and e["relation"] == "query_reads"
        for e in graph["edges"]
    )
    joined = "1c://Document/ЗаказКлиента/ObjectModule/ОстаткиИЦены"
    assert (joined, "1c://InformationRegister/Цены", "query_reads") in edges
    assert (joined, "1c://AccumulationRegister/ОстаткиТоваров", "query_reads") in edges
    assert (
        "1c://Document/ЗаказКлиента/ObjectModule/ЗапросВКонструкторе",
        "1c://InformationRegister/Цены",
        "query_reads",
    ) in edges
    assembled = "1c://Document/ЗаказКлиента/ObjectModule/СобранныйЗапрос"
    partial_assembled = "1c://Document/ЗаказКлиента/ObjectModule/ЧастичноСобранныйЗапрос"
    query_edges = [
        edge
        for edge in graph["edges"]
        if edge["relation"] == "query_reads" and edge["target"] == "1c://InformationRegister/Цены"
    ]
    assert any(
        edge["source"] == assembled
        and edge["confidence"] == "EXTRACTED"
        and edge["partial"] is False
        for edge in query_edges
    )
    assert any(
        edge["source"] == partial_assembled
        and edge["confidence"] == "INFERRED"
        and edge["partial"] is True
        for edge in query_edges
    )
    assert not any(edge["source"].endswith("/ПерезаписанныйЗапрос") for edge in query_edges)
    assert not any(edge["source"].endswith("/ЗапросПослеВетвления") for edge in query_edges)
    assert any(
        e["source"].endswith("/ВызватьДинамически")
        and e["relation"] == "dynamic_call"
        and e["confidence"] == "AMBIGUOUS"
        for e in graph["edges"]
    )
    global_calls = [
        e
        for e in graph["edges"]
        if e["source"].endswith("/ПроверитьГлобальныйВызов") and e["relation"] == "calls"
    ]
    assert len(global_calls) == 2
    assert all(e["confidence"] == "AMBIGUOUS" for e in global_calls)
    assert (procedure, "1c://Catalog/Номенклатура", "references") in edges
    assert (
        "1c://Document/ЗаказКлиента/Attribute/Товар",
        "1c://Catalog/Номенклатура",
        "type_reference",
    ) in edges
    defined = "1c://DefinedType/ТоварыИЗаказы"
    assert (defined, "1c://Catalog/Номенклатура", "type_reference") in edges
    assert (defined, "1c://Document/ЗаказКлиента", "type_reference") in edges
    assert (
        "1c://Document/ЗаказКлиента/ObjectModule/СтрокиДокумента",
        "1c://Document/ЗаказКлиента/TabularSection/Товары",
        "query_reads",
    ) in edges
    form = "1c://Document/ЗаказКлиента/Form/ФормаДокумента"
    assert (form + "/Attribute/Товар", "1c://Catalog/Номенклатура", "type_reference") in edges
    assert (form + "/Command/Провести", form + "/Module/ПровестиКоманда", "handler") in edges
    assert (form + "/Element/КнопкаПровести", form + "/Command/Провести", "uses_command") in edges
    assert (form + "/Element/КнопкаПровести", form + "/Module/ПровестиКоманда", "handler") in edges
    assert (form + "/Module/ПровестиКоманда", function, "calls_server") in edges
    assert (form, form + "/Module/ПриОткрытии", "handler") in edges
    assert ("1c://ScheduledJob/ОбновитьЦены", function, "executes") in edges
    subscription = "1c://EventSubscription/ПередЗаписьюЗаказа"
    assert (subscription, function, "handler") in edges
    assert (subscription, "1c://Document/ЗаказКлиента", "source") in edges
    assert (subscription, "1c://PlatformEvent/BeforeWrite", "event") in edges
    assert (
        "1c://Subsystem/Продажи",
        "1c://Document/ЗаказКлиента",
        "includes",
    ) in edges
    assert (
        "1c://HTTPService/API/URLTemplate/Цены/Method/Получить",
        "1c://HTTPService/API/Module/ОбработатьGET",
        "handler",
    ) in edges
    assert ("1c://Role/Менеджер", "1c://Document/ЗаказКлиента", "grants") in edges
    assert (
        "1c://Role/Менеджер",
        "1c://Document/ЗаказКлиента/Attribute/Товар",
        "grants",
    ) in edges


def test_extension_hook():
    graph = extract_project(FIXTURE, extensions=[EXTENSION])
    assert validate_extraction(graph) == []
    edges = {(e["source"], e["target"], e["relation"]) for e in graph["edges"]}
    assert (
        "1c://Extension/КонтрольЗаказов/Document/ЗаказКлиента/ObjectModule/ПроверитьЗаказ",
        "1c://Document/ЗаказКлиента/ObjectModule/ОбработкаПроведения",
        "BEFORE",
    ) in edges
    target = "1c://Document/ЗаказКлиента/ObjectModule/ОбработкаПроведения"
    prefix = "1c://Extension/КонтрольЗаказов/Document/ЗаказКлиента/ObjectModule/"
    assert (prefix + "ПослеПроведения", target, "AFTER") in edges
    assert (prefix + "ВместоПроведения", target, "INSTEAD") in edges
    assert (prefix + "КонтрольПроведения", target, "CHANGE_CONTROL") in edges
    assert (
        prefix + "ПроверитьЗаказ",
        "1c://CommonModule/ЦенообразованиеСервер/CommonModule/ПолучитьЦену",
        "calls",
    ) in edges
    assert (
        "1c://Extension/КонтрольЗаказов/Document/ЗаказКлиента",
        "1c://Document/ЗаказКлиента",
        "EXTENDS",
    ) in edges


def test_preprocessor_annotation_does_not_include_module_body(tmp_path):
    (tmp_path / "Configuration.xml").write_text(
        "<MetaDataObject><Configuration><Properties><Name>Test</Name>"
        "</Properties></Configuration></MetaDataObject>",
        encoding="utf-8",
    )
    module = tmp_path / "Ext" / "ManagedApplicationModule.bsl"
    module.parent.mkdir()
    module.write_text(
        "#Область Обработчики\n&НаКлиенте\n"
        'Процедура Проверка()\nСообщить("тело");\nКонецПроцедуры\n'
        "#КонецОбласти\n",
        encoding="utf-8",
    )
    graph = extract_project(tmp_path)
    method = next(node for node in graph["nodes"] if node["kind"] == "Procedure")
    assert method["annotations"] == ["&НаКлиенте"]


def test_disk_index_queries_graph_without_loading_json(tmp_path):
    graph_path = tmp_path / "graph.json"
    database = tmp_path / "graph.sqlite"
    graph_path.write_text(
        json.dumps(
            {
                "nodes": [
                    {
                        "id": "1c://Document/Заказ",
                        "label": "Заказ",
                        "kind": "Document",
                        "source_file": "Documents/Заказ.xml",
                        "source_type": "configuration",
                    },
                    {
                        "id": "1c://Extension/Тест/Document/Заказ",
                        "label": "Заказ",
                        "kind": "Document",
                        "extension_name": "Тест",
                        "annotations": ["&Вместо"],
                    },
                ],
                "links": [
                    {
                        "source": "1c://Extension/Тест/Document/Заказ",
                        "target": "1c://Document/Заказ",
                        "relation": "EXTENDS",
                    }
                ],
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    assert build_index(graph_path, database)["edges"] == 1
    assert len(search(database, "Заказ")) == 2


    assert len(search(database, "заказ")) == 2
    assert len(search(database, "аказ")) == 2
    assert len(search(database, "Заказ", limit=1, offset=0)) == 1
    assert len(search(database, "Заказ", limit=1, offset=1)) == 1
    assert search(database, "Заказ", limit=1, offset=2) == []
    links = neighbors(database, "1c://Document/Заказ", direction="in", relation="EXTENDS")
    assert len(links) == 1
    assert links[0]["source"] == "1c://Extension/Тест/Document/Заказ"
    assert links[0]["source_extension"] == "Тест"
    assert links[0]["target_type"] == "configuration"
    assert neighbors(database, "1c://Document/Заказ", direction="in", offset=1) == []
    with pytest.raises(ValueError, match="1..30"):
        neighbors(database, "1c://Document/Заказ", limit=1000000)
    with pytest.raises(ValueError, match="1..30"):
        search(database, "Заказ", limit=1000000)
    with pytest.raises(ValueError, match="must not be empty"):
        search(database, " ")
    broken = tmp_path / "broken.json"
    broken.write_text('{"nodes": [], "links": []}\n', encoding="utf-8")
    with pytest.raises(ValueError):
        build_index(broken, database)
    assert len(search(database, "Заказ")) == 2


def test_agent_index_output_is_small_and_large_json_cannot_be_loaded(tmp_path, capsys, monkeypatch):
    from graphify import security

    graph = tmp_path / "graph.json"
    graph.write_text(
        json.dumps({"nodes": [{"id": "1c://Document/Тест", "label": "Тест"}],
                    "links": [{"source": "1c://Document/Тест", "target": "1c://Document/Тест",
                               "relation": "references"}]}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    database = tmp_path / "graph.sqlite"
    build_index(graph, database)
    index_main(["neighbors", str(database), "1c://Document/Тест"])
    payload = capsys.readouterr().out
    assert len(payload.encode("utf-8")) < 12 * 1024
    assert "neighbor_label" in payload and "source_label" not in payload
    export_path = tmp_path / "all-uses.jsonl"
    result = export_neighbors(database, "1c://Document/Тест", export_path,
                              relation="references")
    assert result["count"] == 1
    assert json.loads(export_path.read_text(encoding="utf-8"))["neighbor_kind"] is None
    index_main(["groups", str(database), "1c://Document/Тест", "--direction", "in"])
    assert '"count": 1' in capsys.readouterr().out
    with pytest.raises(SystemExit) as exc:
        index_main(["search", str(database), "Тест", "--limit", "1000000"])
    assert exc.value.code == 2
    assert capsys.readouterr().out == ""
    with pytest.raises(ValueError, match="12 KiB"):
        _print_agent_result({"oversized": "x" * 15000})
    assert capsys.readouterr().out == ""
    monkeypatch.setattr(security, "_MAX_ONEC_JSON_BYTES", graph.stat().st_size - 1)
    monkeypatch.setenv("GRAPHIFY_MAX_GRAPH_BYTES", "2GB")
    with pytest.raises(ValueError, match="loading it into memory is disabled"):
        security.check_graph_file_size_cap(graph)


def test_streamed_large_graph_builds_disk_index(tmp_path):
    extraction = {
        "nodes": [
            {"id": f"1c://Document/{number}", "label": f"Документ {number}",
             "kind": "Document", "source_type": "configuration", "extension_name": None}
            for number in range(5001)
        ],
        "edges": [
            {"source": "1c://Document/0", "target": "1c://Document/1",
             "relation": "references", "confidence": "EXTRACTED"},
            {"source": "1c://Document/0", "target": "1c://Document/1",
             "relation": "references", "confidence": "INFERRED"},
        ],
    }
    extraction["nodes"].append({"id": "1c://Document/0", "label": "Первый документ"})
    output = tmp_path / "graph.json"
    database = tmp_path / "graph.sqlite"
    write_onec_json(extraction, output)
    data = json.loads(output.read_text(encoding="utf-8"))
    assert data["directed"] and data["multigraph"]
    assert len(data["nodes"]) == 5001
    assert next(node for node in data["nodes"] if node["id"] == "1c://Document/0")["kind"] == "Document"
    assert len(data["links"]) == 1
    assert data["links"][0]["confidence"] == "INFERRED"
    assert data["links"][0]["key"] == "references"
    assert build_index(output, database)["nodes"] == 5001
    assert search(database, "Документ 5000")[0]["id"] == "1c://Document/5000"
    path = shortest_path(database, "1c://Document/0", "1c://Document/1")
    assert path["found"] and path["path"][0]["relation"] == "references"
    assert not shortest_path(database, "1c://Document/1", "1c://Document/0")["found"]
    summary = explain(database, "1c://Document/0", sample=1)
    assert summary["node"]["label"] == "Первый документ"
    assert summary["groups"][0]["relation"] == "references"
    generic = subprocess.run(
        [sys.executable, "-m", "graphify", "path", "1c://Document/0", "1c://Document/1",
         "--graph", str(output)], capture_output=True, text=True,
    )
    assert generic.returncode == 0, generic.stderr
    explanation = subprocess.run(
        [sys.executable, "-m", "graphify", "explain", "1c://Document/0",
         "--graph", str(output)], capture_output=True, text=True,
    )
    assert explanation.returncode == 0, explanation.stderr
    query = subprocess.run(
        [sys.executable, "-m", "graphify", "query", "Документ 0",
         "--graph", str(output)], capture_output=True, text=True,
    )
    assert query.returncode == 0, query.stderr


def test_init_project_reuses_one_install_for_separate_projects(tmp_path):
    from graphify.onec.init_project import init_project

    first = tmp_path / "first"
    second = tmp_path / "second"
    shutil.copytree(FIXTURE, first / "src" / "cf")
    shutil.copytree(FIXTURE, second / "src" / "cf")
    manifest = first / "graphify-out" / ".graphify_onec.json"
    manifest.parent.mkdir()
    manifest.write_text('{"extensions": ["selected"], "html": "external.html"}', encoding="utf-8")
    (first / "AGENTS.md").write_text("# Existing rules\n", encoding="utf-8")
    init_project(first, ["codex", "claude", "vscode"])
    init_project(second, ["codex"])
    first_rules = (first / "AGENTS.md").read_text(encoding="utf-8")
    assert first_rules.startswith("# Existing rules")
    assert first_rules.count("<!-- graphify-1c:start -->") == 1
    assert "graphify-1c index groups" in first_rules
    assert "graphify-out/.graphify_onec.json" in first_rules
    assert "не включай их без запроса" in first_rules
    assert '"selected"' in manifest.read_text(encoding="utf-8")
    assert (first / "CLAUDE.md").exists()
    assert (first / ".github" / "copilot-instructions.md").exists()
    assert (second / "AGENTS.md").exists()
    assert not (second / "CLAUDE.md").exists()
    assert "graphify-out/" in (first / ".gitignore").read_text(encoding="utf-8")
    init_project(first, ["codex", "claude", "vscode"])
    assert (first / "AGENTS.md").read_text(encoding="utf-8") == first_rules
    assert (first / ".gitignore").read_text(encoding="utf-8").count("graphify-out/") == 1


def test_update_rebuilds_onec_graph_and_preserves_extensions(tmp_path):
    project = tmp_path / "project"
    cf = project / "src" / "cf"
    extension = project / "selected" / "Extension1"
    shutil.copytree(FIXTURE, cf)
    shutil.copytree(EXTENSION, extension)
    output = project / "reports" / "custom.json"
    html = project / "reports" / "custom.html"
    env = {**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1])}
    analyze = subprocess.run(
        [sys.executable, "-m", "graphify", "analyze", "./src", "--extension",
         str(extension), "--out", str(output), "--html", str(html)],
        cwd=project, capture_output=True, text=True, env=env,
    )
    assert analyze.returncode == 0, analyze.stderr
    assert (project / "graphify-out" / ".graphify_onec.json").exists()
    before = json.loads(output.read_text(encoding="utf-8"))
    assert any(node.get("source_type") == "extension" for node in before["nodes"])
    initial_bytes = output.read_bytes()
    unchanged = subprocess.run(
        [sys.executable, "-m", "graphify", "update", "."],
        cwd=project, capture_output=True, text=True, env=env,
    )
    assert unchanged.returncode == 0, unchanged.stderr
    assert "sources unchanged" in unchanged.stdout
    assert output.read_bytes() == initial_bytes
    module = cf / "Documents" / "ЗаказКлиента" / "Ext" / "ObjectModule.bsl"
    module.write_text(module.read_text(encoding="utf-8") + "\nПроцедура НовыйМетод()\nКонецПроцедуры\n", encoding="utf-8")
    updated = subprocess.run(
        [sys.executable, "-m", "graphify", "update", "."],
        cwd=project, capture_output=True, text=True, env=env,
    )
    assert updated.returncode == 0, updated.stderr
    assert "Updating 1C" in updated.stdout
    after = json.loads(output.read_text(encoding="utf-8"))
    assert any(node["id"].endswith("/НовыйМетод") for node in after["nodes"])
    assert any(node.get("source_type") == "extension" for node in after["nodes"])
    assert html.exists()
    previous_json = output.read_bytes()
    previous_html = html.read_bytes()
    (extension / "Configuration.xml").unlink()
    failed = subprocess.run(
        [sys.executable, "-m", "graphify", "update", "."],
        cwd=project, capture_output=True, text=True, env=env,
    )
    assert failed.returncode != 0
    assert output.read_bytes() == previous_json
    assert html.read_bytes() == previous_html


def test_update_detects_onec_project_without_previous_analyze(tmp_path):
    project = tmp_path / "project"
    shutil.copytree(FIXTURE, project / "src" / "cf")
    shutil.copytree(EXTENSION, project / "src" / "cfe" / "Extension1")
    env = {**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1])}
    updated = subprocess.run(
        [sys.executable, "-m", "graphify", "update", "."],
        cwd=project, capture_output=True, text=True, env=env,
    )
    assert updated.returncode == 0, updated.stderr
    assert "Updating 1C" in updated.stdout
    assert (project / "graphify-out" / "graph.json").exists()
    assert (project / "graphify-out" / "graph.html").exists()
    nodes = json.loads((project / "graphify-out" / "graph.json").read_text(encoding="utf-8"))["nodes"]
    assert any(node.get("source_type") == "extension" for node in nodes)


def test_direct_cf_analysis_registers_project_root(tmp_path):
    root = tmp_path / "project"
    cf = root / "src" / "cf"
    cf.mkdir(parents=True)
    remember_analysis(cf, [], root / "reports" / "graph.json", None)
    assert (root / "graphify-out" / ".graphify_onec.json").exists()
    assert _manifest_for(root.resolve())["root"] == str(cf.resolve())


def test_parallel_onec_relations_survive_graph_build(tmp_path):
    source = "1c://Extension/Тест/Module/Перехват"
    target = "1c://Configuration/Основная/Module/Метод"
    graph = build_onec_graph(
        {
            "nodes": [{"id": source}, {"id": target}],
            "edges": [
                {"source": source, "target": target, "relation": "CHANGE_CONTROL"},
                {"source": source, "target": target, "relation": "calls"},
            ],
        }
    )
    assert graph.number_of_edges() == 2
    assert set(graph[source][target]) == {"CHANGE_CONTROL", "calls"}
    output = tmp_path / "graph.json"
    to_json(graph, {}, str(output), force=True)
    saved = json.loads(output.read_text(encoding="utf-8"))
    assert saved["multigraph"] is True
    assert {edge["relation"] for edge in saved["links"]} == {"CHANGE_CONTROL", "calls"}


def test_configuration_module_hook_resolves_across_configuration_names(tmp_path):
    base = tmp_path / "cf"
    extension = tmp_path / "cfe" / "Тест"
    for root, name, bsl in (
        (base, "Основная", "Процедура ПриНачалеРаботыСистемы()\nКонецПроцедуры\n"),
        (
            extension,
            "Дополнение",
            '&После("ПриНачалеРаботыСистемы")\n'
            "Процедура Доп_ПриНачалеРаботыСистемы()\nКонецПроцедуры\n",
        ),
    ):
        root.mkdir(parents=True)
        (root / "Configuration.xml").write_text(
            "<MetaDataObject><Configuration><Properties><Name>" + name + "</Name>"
            "</Properties></Configuration></MetaDataObject>",
            encoding="utf-8",
        )
        module = root / "Ext" / "ManagedApplicationModule.bsl"
        module.parent.mkdir()
        module.write_text(bsl, encoding="utf-8")
    graph = extract_project(base, extensions=[extension])
    assert any(
        edge["relation"] == "AFTER"
        and edge["target"]
        == "1c://Configuration/Основная/ManagedApplicationModule/ПриНачалеРаботыСистемы"
        for edge in graph["edges"]
    )


def test_borrowed_common_picture_is_linked(tmp_path):
    base = tmp_path / "cf"
    extension = tmp_path / "cfe" / "Тест"
    for root, name, belonging in (
        (base, "Основная", None),
        (extension, "Дополнение", "Adopted"),
    ):
        root.mkdir(parents=True)
        (root / "Configuration.xml").write_text(
            f"<MetaDataObject><Configuration><Properties><Name>{name}</Name>"
            "</Properties></Configuration></MetaDataObject>",
            encoding="utf-8",
        )
        picture = root / "CommonPictures" / "Иконка.xml"
        picture.parent.mkdir()
        property_xml = f"<ObjectBelonging>{belonging}</ObjectBelonging>" if belonging else ""
        picture.write_text(
            "<MetaDataObject><CommonPicture><Properties><Name>Иконка</Name>"
            f"{property_xml}</Properties></CommonPicture></MetaDataObject>",
            encoding="utf-8",
        )
        document = root / "Documents" / "Заказ.xml"
        document.parent.mkdir()
        document.write_text(
            "<MetaDataObject><Document><Properties><Name>Заказ</Name>"
            f"{property_xml}</Properties></Document></MetaDataObject>",
            encoding="utf-8",
        )
        form = root / "Documents" / "Заказ" / "Forms" / "Основная.xml"
        form.parent.mkdir(parents=True)
        form.write_text(
            "<MetaDataObject><Form><Properties><Name>Основная</Name>"
            f"{property_xml}</Properties></Form></MetaDataObject>",
            encoding="utf-8",
        )
    graph = extract_project(base, extensions=[extension])
    assert any(
        edge["source"] == "1c://Extension/Дополнение/CommonPicture/Иконка"
        and edge["target"] == "1c://CommonPicture/Иконка"
        and edge["relation"] == "EXTENDS"
        for edge in graph["edges"]
    )
    assert any(
        edge["source"] == "1c://Extension/Дополнение/Document/Заказ/Form/Основная"
        and edge["target"] == "1c://Document/Заказ/Form/Основная"
        and edge["relation"] == "EXTENDS"
        for edge in graph["edges"]
    )


def test_register_manager_method_call_resolves(tmp_path):
    (tmp_path / "Configuration.xml").write_text(
        "<MetaDataObject><Configuration><Properties><Name>Test</Name>"
        "</Properties></Configuration></MetaDataObject>",
        encoding="utf-8",
    )
    for directory, kind, name in (
        ("Documents", "Document", "Заказ"),
        ("InformationRegisters", "InformationRegister", "Статусы"),
    ):
        metadata = tmp_path / directory / f"{name}.xml"
        metadata.parent.mkdir()
        metadata.write_text(
            f"<MetaDataObject><{kind}><Properties><Name>{name}</Name>"
            f"</Properties></{kind}></MetaDataObject>",
            encoding="utf-8",
        )
    caller = tmp_path / "Documents" / "Заказ" / "Ext" / "ObjectModule.bsl"
    caller.parent.mkdir(parents=True)
    caller.write_text(
        "Процедура ОбработкаПроведения()\n"
        "РегистрыСведений.Статусы.ЗаписатьСтатус();\nКонецПроцедуры\n",
        encoding="utf-8",
    )
    manager = tmp_path / "InformationRegisters" / "Статусы" / "Ext" / "ManagerModule.bsl"
    manager.parent.mkdir(parents=True)
    manager.write_text("Процедура ЗаписатьСтатус() Экспорт\nКонецПроцедуры\n", encoding="utf-8")
    graph = extract_project(tmp_path)
    assert any(
        edge["source"] == "1c://Document/Заказ/ObjectModule/ОбработкаПроведения"
        and edge["target"] == "1c://InformationRegister/Статусы/ManagerModule/ЗаписатьСтатус"
        and edge["relation"] == "calls"
        for edge in graph["edges"]
    )


def test_edt_project():
    graph = extract_project(EDT)
    assert validate_extraction(graph) == []
    nodes = {node["id"]: node for node in graph["nodes"]}
    assert nodes["1c://Configuration/EDTTest"]["source_format"] == "EDT"
    assert nodes["1c://Catalog/Номенклатура"]["synonym"] == "Товары"
    edges = {(edge["source"], edge["target"], edge["relation"]) for edge in graph["edges"]}
    assert (
        "1c://Catalog/Номенклатура/Attribute/Родитель",
        "1c://Catalog/Номенклатура",
        "type_reference",
    ) in edges
    assert (
        "1c://Document/Заказ/ObjectModule/Провести",
        "1c://CommonModule/ЦеныСервер/CommonModule/ПолучитьЦену",
        "calls",
    ) in edges
    form = "1c://Document/Заказ/Form/ФормаДокумента"
    assert (form + "/Element/Кнопка", form + "/Command/Провести", "uses_command") in edges
    assert (form + "/Command/Провести", form + "/Module/ПровестиКоманда", "handler") in edges
    assert (form + "/Attribute/Товар", "1c://Catalog/Номенклатура", "type_reference") in edges


def test_edt_protected_binary_module_is_skipped(tmp_path):
    project = tmp_path / "edt"
    shutil.copytree(EDT, project)
    (project / "src/CommonModules/ЦеныСервер/Module.bsl").write_bytes(
        b"\xff\xff\xff\x7f" + b"\x00" * 32
    )
    graph = extract_project(project)
    assert validate_extraction(graph) == []
    nodes = {node["id"]: node for node in graph["nodes"]}
    assert nodes["1c://CommonModule/ЦеныСервер/CommonModule"]["parse_status"] == "binary_skipped"
    assert "1c://CommonModule/ЦеныСервер/CommonModule/ПолучитьЦену" not in nodes


def test_standard_layout_discovers_all_extensions_and_resolves_cross_calls(tmp_path):
    src = tmp_path / "src"
    shutil.copytree(FIXTURE, src / "cf")
    first = src / "cfe" / "ExtensionA"
    second = src / "cfe" / "ExtensionB"
    shutil.copytree(EXTENSION, first)
    shutil.copytree(EXTENSION, second)
    invalid = src / "cfe" / "Broken"
    invalid.mkdir()
    (invalid / "Configuration.xml").write_text("<broken>", encoding="utf-8")
    config = second / "Configuration.xml"
    config.write_text(
        config.read_text(encoding="utf-8").replace("КонтрольЗаказов", "КонтрольЗаказовБ"),
        encoding="utf-8",
    )
    common = second / "CommonModules"
    common.mkdir()
    (common / "МежРасширениями.xml").write_text(
        "<MetaDataObject><CommonModule><Properties><Name>МежРасширениями</Name></Properties></CommonModule></MetaDataObject>",
        encoding="utf-8",
    )
    module = common / "МежРасширениями" / "Ext" / "Module.bsl"
    module.parent.mkdir(parents=True)
    module.write_text("Функция Вычислить() Экспорт\n Возврат 1;\nКонецФункции", encoding="utf-8")
    third = src / "cfe" / "ExtensionC"
    shutil.copytree(second, third)
    third_config = third / "Configuration.xml"
    third_config.write_text(
        third_config.read_text(encoding="utf-8").replace("КонтрольЗаказовБ", "КонтрольЗаказовВ"),
        encoding="utf-8",
    )
    caller = first / "Documents" / "ЗаказКлиента" / "Ext" / "ObjectModule.bsl"
    with caller.open("a", encoding="utf-8") as stream:
        stream.write(
            "\nПроцедура ВызватьДругоеРасширение()\n МежРасширениями.Вычислить();\n"
            " Движения.ОстаткиТоваров.Записывать = Истина;\nКонецПроцедуры\n"
        )

    extraction = extract_project(src)
    assert validate_extraction(extraction) == []
    assert len(extract_project(tmp_path)["nodes"]) == len(extraction["nodes"])
    nodes = {node["id"]: node for node in extraction["nodes"]}
    assert sum(node["kind"] == "Extension" for node in nodes.values()) == 3
    assert all(
        node["source_type"] == "configuration" and node["extension_name"] is None
        for node in nodes.values()
        if node["id"].startswith("1c://Document/")
    )
    for name in ("КонтрольЗаказов", "КонтрольЗаказовБ", "КонтрольЗаказовВ"):
        prefix = f"1c://Extension/{name}/Document/ЗаказКлиента"
        assert nodes[prefix]["source_type"] == "extension"
        assert nodes[prefix]["extension_name"] == name
        assert any(
            e["source"] == prefix
            and e["target"] == "1c://Document/ЗаказКлиента"
            and e["relation"] == "EXTENDS"
            for e in extraction["edges"]
        )
        assert any(
            e["source"] == prefix + "/ObjectModule/ПроверитьЗаказ"
            and e["target"] == "1c://Document/ЗаказКлиента/ObjectModule/ОбработкаПроведения"
            and e["relation"] == "BEFORE"
            for e in extraction["edges"]
        )
    for relation in ("BEFORE", "AFTER", "INSTEAD", "CHANGE_CONTROL"):
        assert (
            sum(
                edge["relation"] == relation
                and edge["target"] == "1c://Document/ЗаказКлиента/ObjectModule/ОбработкаПроведения"
                for edge in extraction["edges"]
            )
            == 3
        )
    cross_calls = [
        edge
        for edge in extraction["edges"]
        if edge["source"]
        == "1c://Extension/КонтрольЗаказов/Document/ЗаказКлиента/ObjectModule/ВызватьДругоеРасширение"
        and edge["relation"] == "calls"
    ]
    assert {edge["target"] for edge in cross_calls} == {
        f"1c://Extension/{name}/CommonModule/МежРасширениями/CommonModule/Вычислить"
        for name in ("КонтрольЗаказовБ", "КонтрольЗаказовВ")
    }
    assert all(edge["confidence"] == "AMBIGUOUS" for edge in cross_calls)
    assert any(
        edge["source"]
        == "1c://Extension/КонтрольЗаказов/Document/ЗаказКлиента/ObjectModule/ВызватьДругоеРасширение"
        and edge["target"] == "1c://AccumulationRegister/ОстаткиТоваров"
        and edge["relation"] == "writes"
        for edge in extraction["edges"]
    )
    assert any(
        edge["source"] == "1c://Extension/КонтрольЗаказов/Document/ЗаказКлиента"
        and edge["target"] == "1c://AccumulationRegister/ОстаткиТоваров"
        and edge["relation"] == "writes"
        for edge in extraction["edges"]
    )
    output = tmp_path / "graph.json"
    html_output = tmp_path / "graph.html"
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "graphify",
            "analyze",
            str(src),
            "--out",
            str(output),
            "--html",
            str(html_output),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    assert output.is_file()
    assert html_output.is_file()
    assert "1C graph:" in result.stdout
    assert "Top unresolved calls:" in result.stdout
    assert "3 extensions" in result.stdout
    saved = json.loads(output.read_text(encoding="utf-8"))
    saved_nodes = {node["id"]: node for node in saved["nodes"]}
    assert saved_nodes["1c://Document/ЗаказКлиента"]["source_type"] == "configuration"
    assert saved_nodes["1c://Document/ЗаказКлиента"]["metadata_type"] == "Document"
    assert saved_nodes["1c://Document/ЗаказКлиента"]["source_path"]
    assert any(edge["relation_type"] == "BEFORE" for edge in saved["links"])
    assert (
        saved_nodes["1c://Extension/КонтрольЗаказовБ/CommonModule/МежРасширениями"][
            "extension_name"
        ]
        == "КонтрольЗаказовБ"
    )
    page = html_output.read_text(encoding="utf-8")
    viewer_data = json.loads(page.split("const ONEC_DATA = ", 1)[1].split(";\n", 1)[0])
    assert {node["extension_name"] for node in viewer_data["nodes"] if node["extension_name"]} == {
        "КонтрольЗаказов",
        "КонтрольЗаказовБ",
        "КонтрольЗаказовВ",
    }
    assert {"BEFORE", "AFTER", "INSTEAD", "CHANGE_CONTROL", "EXTENDS"} <= {
        edge["relation"] for edge in viewer_data["edges"]
    }
    assert "Тип узла" in page and "Тип связи" in page and "Оба направления" in page


def test_large_onec_viewer_starts_without_rendering_all_nodes(tmp_path):
    graph = nx.DiGraph()
    for number in range(3):
        graph.add_node(
            f"1c://Document/{number}",
            label=f"Документ {number}",
            kind="Document",
            source_type="configuration",
            extension_name=None,
            source_file="Configuration.xml",
        )
    graph.add_edge("1c://Document/0", "1c://Document/1", relation="calls", confidence="INFERRED")
    target = tmp_path / "large.html"
    graph_path = tmp_path / "large.json"
    to_json(graph, {}, str(graph_path), force=True)
    write_onec_html(graph, target, node_limit=2, graph_path=graph_path)
    page = target.read_text(encoding="utf-8")
    assert "const RAW_NODES = [];" in page
    assert "const ONEC_DATA" not in page
    assert "'/api/neighborhood?'" in page
    assert "showOverview();" in page
    assert target.stat().st_size < 500_000
    from graphify.onec.serve import filter_options, neighborhood, search_nodes

    database = target.with_suffix(".sqlite")
    overview = json.loads(target.with_suffix(".overview.json").read_text(encoding="utf-8"))
    assert any(node["kind"] == "Document" and node["count"] == 3 for node in overview["nodes"])
    assert search_nodes(database, "Документ")
    assert search_nodes(database, "Документ", kind="Document")
    assert not search_nodes(database, "Документ", kind="Procedure")
    assert "calls" in filter_options(database)["relations"]
    result = neighborhood(database, "1c://Document/0")
    assert len(result["nodes"]) == 2
    assert len(result["edges"]) == 1


def test_large_viewer_http_returns_only_bounded_data(tmp_path):
    from http.server import ThreadingHTTPServer
    from threading import Thread
    from urllib.request import urlopen

    from graphify.onec.serve import (
        MAX_NODES,
        NEIGHBOR_PAGE_SIZE,
        make_handler,
        neighbor_groups,
        neighbor_page,
    )

    graph = nx.DiGraph()
    graph.add_node("root", label="Корень", kind="Document")
    for index in range(MAX_NODES + 20):
        graph.add_node(f"n{index}", label=f"Узел {index}", kind="Procedure")
        graph.add_edge("root", f"n{index}", relation="calls")
    path = tmp_path / "large.json"
    page = tmp_path / "large.html"
    to_json(graph, {}, str(path), force=True)
    write_onec_html(graph, page, node_limit=2, graph_path=path)
    database = page.with_suffix(".sqlite")
    groups = neighbor_groups(database, "root")
    assert groups == [{"direction": "out", "relation": "calls", "count": MAX_NODES + 20}]
    first = neighbor_page(database, "root", direction="out", relation="calls")
    second = neighbor_page(database, "root", direction="out", relation="calls", page=1)
    assert len(first["edges"]) == NEIGHBOR_PAGE_SIZE
    assert first["total"] == MAX_NODES + 20 and first["has_next"]
    assert {edge["target"] for edge in first["edges"]}.isdisjoint(
        {edge["target"] for edge in second["edges"]}
    )
    assert neighbor_groups(database, "root", kind="Document") == []
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(page.with_suffix(".sqlite"), page))
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        root = f"http://127.0.0.1:{server.server_port}"
        with urlopen(root + "/") as response:
            assert b"const RAW_NODES = [];" in response.read()
        with urlopen(root + "/api/overview") as response:
            assert json.loads(response.read())["nodes"]
        with urlopen(root + "/api/search?q=%D0%9A%D0%BE%D1%80%D0%B5%D0%BD%D1%8C") as response:
            assert json.loads(response.read())[0]["id"] == "root"
        with urlopen(root + "/api/neighborhood?id=root&depth=3") as response:
            result = json.loads(response.read())
        assert len(result["nodes"]) <= MAX_NODES
        assert result["truncated"] is True
        with urlopen(root + "/api/neighbor-groups?id=root") as response:
            assert json.loads(response.read())[0]["count"] == MAX_NODES + 20
        with urlopen(
            root + "/api/neighbor-page?id=root&direction=out&relation=calls&page=1"
        ) as response:
            assert len(json.loads(response.read())["edges"]) == NEIGHBOR_PAGE_SIZE
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
