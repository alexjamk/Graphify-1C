"""Bounded localhost viewer for large 1C graphs stored in SQLite."""

from __future__ import annotations

import argparse
from html import escape
import json
import sqlite3
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import networkx as nx

from graphify.export import to_html
from graphify.paths import write_text_atomic


MAX_NODES = 300
MAX_EDGES = 800
NEIGHBOR_PAGE_SIZE = 20


def write_viewer_page(
    path: Path, node_count: int, edge_count: int, database: Path | None = None
) -> None:
    """Produce a small offline HTML shell; graph facts come from localhost APIs."""
    to_html(nx.DiGraph(), {}, str(path))
    html = path.read_text(encoding="utf-8")
    if "</body>" not in html:
        raise ValueError("Graphify HTML template changed")
    intro = (
        f'<div id="onec-intro" style="padding:10px;color:#ddd">'
        f"Граф 1С: {node_count:,} узлов, {edge_count:,} связей. "
        "Введите имя или ID в поиск. Для просмотра запустите локальный сервер: "
        f"<code>python -m graphify.onec.serve &quot;{escape(str(path))}&quot;</code>. "
        "Страница получает только найденные узлы и их окружение.</div>"
    )
    html = html.replace('<div id="legend-wrap">', intro + '<div id="legend-wrap">', 1)
    html = html.replace("</head>", _STYLE + "\n</head>", 1)
    html = html.replace("</body>", "<script>\n" + _CLIENT + "\n</script>\n</body>", 1)
    write_text_atomic(path, html)
    if database is not None:
        write_text_atomic(
            path.with_suffix(".overview.json"),
            json.dumps(build_overview(database), ensure_ascii=False, separators=(",", ":")),
        )


def build_overview(database: Path) -> dict:
    """Summarize node kinds and relation counts without exporting graph records."""
    with sqlite3.connect(f"file:{database.as_posix()}?mode=ro", uri=True) as db:
        counts = db.execute(
            "SELECT kind,COUNT(*) FROM nodes WHERE kind IS NOT NULL "
            "GROUP BY kind ORDER BY COUNT(*) DESC"
        ).fetchall()
        selected = dict(counts[:6])
        required = {
            "Configuration",
            "Extension",
            "Document",
            "Catalog",
            "CommonModule",
            "InformationRegister",
            "AccumulationRegister",
            "Form",
            "Enum",
            "Module",
            "Procedure",
            "Function",
            "Attribute",
            "FormElement",
        }
        selected.update((kind, count) for kind, count in counts if kind in required)
        grouped = db.execute(
            "SELECT s.kind,t.kind,e.relation,COUNT(*) FROM edges e "
            "JOIN nodes s ON s.id=e.source JOIN nodes t ON t.id=e.target "
            "WHERE e.relation IN ('contains','calls','writes','query_reads',"
            "'type_reference','EXTENDS','BEFORE','AFTER','INSTEAD','CHANGE_CONTROL') "
            "GROUP BY s.kind,t.kind,e.relation"
        ).fetchall()
        adopted = db.execute(
            "SELECT t.kind,COUNT(*) FROM edges e JOIN nodes t ON t.id=e.target "
            "WHERE e.relation='EXTENDS' GROUP BY t.kind"
        ).fetchall()
    candidates = [
        (source, target, relation, count)
        for source, target, relation, count in grouped
        if source in selected and target in selected and source != target
    ]
    edges = []
    for relation in (
        "contains",
        "calls",
        "writes",
        "query_reads",
        "type_reference",
        "EXTENDS",
        "BEFORE",
        "AFTER",
        "INSTEAD",
        "CHANGE_CONTROL",
    ):
        strongest = sorted(
            (edge for edge in candidates if edge[2] == relation),
            key=lambda edge: edge[3],
            reverse=True,
        )[:3]
        edges.extend(
            {
                "source": f"kind:{source}",
                "target": f"kind:{target}",
                "relation": relation,
                "count": count,
            }
            for source, target, relation, count in strongest
        )
    edges.extend(
        {
            "source": "kind:Extension",
            "target": f"kind:{kind}",
            "relation": "EXTENDS",
            "count": count,
        }
        for kind, count in adopted
        if kind in selected
    )
    return {
        "nodes": [
            {"id": f"kind:{kind}", "kind": kind, "count": count} for kind, count in selected.items()
        ],
        "edges": edges[:30],
    }


def group_examples(database: Path, kind: str, limit: int = 30) -> list[dict]:
    with sqlite3.connect(f"file:{database.as_posix()}?mode=ro", uri=True) as db:
        db.row_factory = sqlite3.Row
        return [
            dict(row)
            for row in db.execute(
                "SELECT id,label,kind,source_file,start_line,source_type,extension_name "
                "FROM nodes WHERE kind=? ORDER BY label LIMIT ?",
                (kind, min(limit, 30)),
            )
        ]


def search_nodes(
    database: Path,
    term: str,
    limit: int = 30,
    *,
    source: str = "",
    kind: str = "",
) -> list[dict]:
    term = term.strip()[:200]
    if not term:
        return []
    # Escape LIKE metacharacters so a user's name is searched literally.
    escaped = term.casefold().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    with sqlite3.connect(f"file:{database.as_posix()}?mode=ro", uri=True) as db:
        db.row_factory = sqlite3.Row
        sql = (
            "SELECT id,label,kind,source_file,start_line,source_type,extension_name "
            "FROM nodes WHERE (norm_label LIKE ? ESCAPE '\\' OR norm_id LIKE ? ESCAPE '\\')"
        )
        parameters: list[str | int] = [f"%{escaped}%", f"%{escaped}%"]
        if source == "configuration":
            sql += " AND source_type='configuration'"
        elif source:
            sql += " AND extension_name=?"
            parameters.append(source)
        if kind:
            sql += " AND kind=?"
            parameters.append(kind)
        sql += " LIMIT ?"
        parameters.append(min(limit, 30))
        return [dict(row) for row in db.execute(sql, parameters)]


def filter_options(database: Path) -> dict[str, list[str]]:
    with sqlite3.connect(f"file:{database.as_posix()}?mode=ro", uri=True) as db:
        return {
            "relations": [
                row[0]
                for row in db.execute("SELECT DISTINCT relation FROM edges ORDER BY 1")
                if row[0]
            ],
            "kinds": [
                row[0] for row in db.execute("SELECT DISTINCT kind FROM nodes ORDER BY 1") if row[0]
            ],
            "extensions": [
                row[0]
                for row in db.execute(
                    "SELECT DISTINCT extension_name FROM nodes WHERE extension_name IS NOT NULL ORDER BY 1"
                )
            ],
        }


def _neighbor_filter(source: str, kind: str) -> tuple[str, list[str]]:
    sql = ""
    params = []
    if source == "configuration":
        sql += " AND n.source_type='configuration'"
    elif source:
        sql += " AND n.extension_name=?"
        params.append(source)
    if kind:
        sql += " AND n.kind=?"
        params.append(kind)
    return sql, params


def neighbor_groups(
    database: Path,
    node_id: str,
    *,
    direction: str = "both",
    relation: str | None = None,
    source: str = "",
    kind: str = "",
) -> list[dict]:
    """Count adjacent relationships without loading their endpoints."""
    if direction not in ("both", "in", "out"):
        raise ValueError("invalid direction")
    groups = []
    with sqlite3.connect(f"file:{database.as_posix()}?mode=ro", uri=True) as db:
        if db.execute("SELECT 1 FROM nodes WHERE id=?", (node_id,)).fetchone() is None:
            raise KeyError(node_id)
        for side, column in (("out", "source"), ("in", "target")):
            if direction not in ("both", side):
                continue
            other = "target" if side == "out" else "source"
            sql = (
                f"SELECT e.relation,COUNT(*) FROM edges e "
                f"JOIN nodes n ON n.id=e.{other} WHERE e.{column}=?"
            )
            params: list[str] = [node_id]
            if relation:
                sql += " AND e.relation=?"
                params.append(relation)
            extra, extra_params = _neighbor_filter(source, kind)
            sql += extra + " GROUP BY e.relation"
            params.extend(extra_params)
            groups.extend(
                {"direction": side, "relation": name or "", "count": count}
                for name, count in db.execute(sql, params)
            )
    return sorted(groups, key=lambda group: (-group["count"], group["relation"]))


def neighbor_page(
    database: Path,
    node_id: str,
    *,
    direction: str,
    relation: str,
    page: int = 0,
    source: str = "",
    kind: str = "",
) -> dict:
    """Return one stable, bounded page of a single relationship group."""
    if direction not in ("in", "out") or not relation or page < 0 or page > 10000:
        raise ValueError("invalid page or relationship group")
    column = "source" if direction == "out" else "target"
    other = "target" if direction == "out" else "source"
    extra, extra_params = _neighbor_filter(source, kind)
    where = (
        f"FROM edges e JOIN nodes n ON n.id=e.{other} WHERE e.{column}=? AND e.relation=?" + extra
    )
    params = [node_id, relation, *extra_params]
    with sqlite3.connect(f"file:{database.as_posix()}?mode=ro", uri=True) as db:
        db.row_factory = sqlite3.Row
        focus = db.execute(
            "SELECT id,label,kind,source_file,start_line,source_type,extension_name "
            "FROM nodes WHERE id=?",
            (node_id,),
        ).fetchone()
        if focus is None:
            raise KeyError(node_id)
        total = db.execute(
            "SELECT COUNT(*) " + where,
            params,
        ).fetchone()[0]
        rows = db.execute(
            "SELECT e.source,e.target,e.relation,e.confidence,n.id,n.label,n.kind,"
            "n.source_file,n.start_line,n.source_type,n.extension_name "
            + where
            + " ORDER BY e.rowid LIMIT ? OFFSET ?",
            (*params, NEIGHBOR_PAGE_SIZE, page * NEIGHBOR_PAGE_SIZE),
        ).fetchall()
        edges = [
            {key: row[key] for key in ("source", "target", "relation", "confidence")}
            for row in rows
        ]
        nodes = [dict(focus)]
        seen = {node_id}
        for row in rows:
            if row["id"] in seen:
                continue
            seen.add(row["id"])
            nodes.append(
                {
                    key: row[key]
                    for key in (
                        "id",
                        "label",
                        "kind",
                        "source_file",
                        "start_line",
                        "source_type",
                        "extension_name",
                    )
                }
            )
    return {
        "nodes": nodes,
        "edges": edges,
        "total": total,
        "page": page,
        "page_size": NEIGHBOR_PAGE_SIZE,
        "has_next": (page + 1) * NEIGHBOR_PAGE_SIZE < total,
        "truncated": False,
    }


def neighborhood(
    database: Path,
    node_id: str,
    *,
    depth: int = 1,
    direction: str = "both",
    relation: str | None = None,
) -> dict:
    if depth not in (1, 2, 3) or direction not in ("both", "in", "out"):
        raise ValueError("invalid depth or direction")
    seen = {node_id}
    frontier = [node_id]
    edges: list[dict] = []
    truncated = False
    with sqlite3.connect(f"file:{database.as_posix()}?mode=ro", uri=True) as db:
        db.row_factory = sqlite3.Row
        if db.execute("SELECT 1 FROM nodes WHERE id=?", (node_id,)).fetchone() is None:
            raise KeyError(node_id)
        for _ in range(depth):
            next_frontier = []
            for current in frontier:
                if len(edges) >= MAX_EDGES or len(seen) >= MAX_NODES:
                    truncated = True
                    break
                clauses = []
                params: list[str | int] = []
                if direction in ("out", "both"):
                    clauses.append("source=?")
                    params.append(current)
                if direction in ("in", "both"):
                    clauses.append("target=?")
                    params.append(current)
                sql = (
                    "SELECT source,target,relation,confidence FROM edges WHERE ("
                    + " OR ".join(clauses)
                    + ")"
                )
                if relation:
                    sql += " AND relation=?"
                    params.append(relation)
                sql += " LIMIT ?"
                params.append(MAX_EDGES - len(edges) + 1)
                records = db.execute(sql, params).fetchall()
                for row in records:
                    if len(edges) >= MAX_EDGES:
                        truncated = True
                        break
                    other = row["target"] if row["source"] == current else row["source"]
                    if other not in seen:
                        if len(seen) >= MAX_NODES:
                            truncated = True
                            continue
                        seen.add(other)
                        next_frontier.append(other)
                    edges.append(dict(row))
            frontier = next_frontier
            if truncated or not frontier:
                break
        nodes = []
        for node in seen:
            row = db.execute(
                "SELECT id,label,kind,source_file,start_line,source_type,extension_name "
                "FROM nodes WHERE id=?",
                (node,),
            ).fetchone()
            if row:
                nodes.append(dict(row))
    return {"nodes": nodes, "edges": edges, "truncated": truncated}


def make_handler(database: Path, page: Path):
    overview = page.with_suffix(".overview.json")

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            parsed = urlsplit(self.path)
            params = parse_qs(parsed.query)
            try:
                if parsed.path == "/":
                    content = page.read_bytes()
                    mime = "text/html; charset=utf-8"
                elif parsed.path == "/api/overview":
                    content = overview.read_bytes()
                    mime = "application/json; charset=utf-8"
                elif parsed.path == "/api/options":
                    content = json.dumps(filter_options(database), ensure_ascii=False).encode(
                        "utf-8"
                    )
                    mime = "application/json; charset=utf-8"
                elif parsed.path == "/api/search":
                    payload = search_nodes(
                        database,
                        params.get("q", [""])[0],
                        source=params.get("source", [""])[0],
                        kind=params.get("kind", [""])[0],
                    )
                    content = json.dumps(payload, ensure_ascii=False).encode("utf-8")
                    mime = "application/json; charset=utf-8"
                elif parsed.path == "/api/group":
                    payload = group_examples(database, params.get("kind", [""])[0])
                    content = json.dumps(payload, ensure_ascii=False).encode("utf-8")
                    mime = "application/json; charset=utf-8"
                elif parsed.path == "/api/neighbor-groups":
                    payload = neighbor_groups(
                        database,
                        params.get("id", [""])[0],
                        direction=params.get("direction", ["both"])[0],
                        relation=params.get("relation", [""])[0] or None,
                        source=params.get("source", [""])[0],
                        kind=params.get("kind", [""])[0],
                    )
                    content = json.dumps(payload, ensure_ascii=False).encode("utf-8")
                    mime = "application/json; charset=utf-8"
                elif parsed.path == "/api/neighbor-page":
                    payload = neighbor_page(
                        database,
                        params.get("id", [""])[0],
                        direction=params.get("direction", [""])[0],
                        relation=params.get("relation", [""])[0],
                        page=int(params.get("page", ["0"])[0]),
                        source=params.get("source", [""])[0],
                        kind=params.get("kind", [""])[0],
                    )
                    content = json.dumps(payload, ensure_ascii=False).encode("utf-8")
                    mime = "application/json; charset=utf-8"
                elif parsed.path == "/api/neighborhood":
                    payload = neighborhood(
                        database,
                        params.get("id", [""])[0],
                        depth=int(params.get("depth", ["1"])[0]),
                        direction=params.get("direction", ["both"])[0],
                        relation=params.get("relation", [""])[0] or None,
                    )
                    content = json.dumps(payload, ensure_ascii=False).encode("utf-8")
                    mime = "application/json; charset=utf-8"
                else:
                    self.send_error(404)
                    return
            except (ValueError, KeyError):
                self.send_error(400)
                return
            except FileNotFoundError:
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Type", mime)
            self.send_header("Content-Length", str(len(content)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(content)

    return Handler


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="python -m graphify.onec.serve")
    parser.add_argument("html", type=Path)
    parser.add_argument("--database", type=Path)
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args(argv)
    page = args.html.resolve()
    database = (args.database or args.html.with_suffix(".sqlite")).resolve()
    if not page.is_file() or not database.is_file():
        parser.error("HTML and SQLite index must exist")
    with ThreadingHTTPServer(("127.0.0.1", args.port), make_handler(database, page)) as server:
        print(f"Open http://127.0.0.1:{server.server_port}/", flush=True)
        server.serve_forever()


_STYLE = """<style>
  #sidebar { flex: 0 0 340px; }
  #legend-wrap, #stats { display: none; }
  #info-panel { max-height: 25vh; overflow-y: auto; flex-shrink: 0; }
  #info-content { overflow-wrap: anywhere; }
  #onec-groups { max-height: 28vh; overflow-y: auto; padding: 8px 10px;
    border-bottom: 1px solid #34344d; color: #eee; font-size: 12px; flex-shrink: 0; }
  #onec-groups[hidden] { display: none; }
  #onec-groups button { margin: 3px 3px 3px 0; padding: 4px 6px; color: #ddd;
    background: #26263d; border: 1px solid #4a4a67; cursor: pointer; }
  #onec-groups button.active { border-color: #93c5fd; color: #fff; background: #344260; }
  #onec-groups .pager { margin-top: 5px; color: #aaa; }
  #onec-intro { font-size: 12px; line-height: 1.45; border-top: 1px solid #34344d; }
  #onec-intro select { margin: 8px 5px 4px 0; max-width: 100%; padding: 4px;
    color: #f3f4f6; background: #26263d; border: 1px solid #4a4a67; }
  #onec-intro span { display: block; margin-top: 5px; color: #93c5fd; }
</style>"""


_CLIENT = r"""
// The built-in Graphify shell contains no graph records in this mode.
// Only a bounded neighborhood is passed to vis-network.
const intro = document.getElementById('onec-intro');
if (location.protocol !== 'file:') {
  intro.firstChild.textContent = intro.firstChild.textContent.split('Для просмотра запустите')[0] +
    ' Сначала показан обзор; выбор объекта откроет локальное окружение. ';
  const command = intro.querySelector('code');
  if (command) { if (command.nextSibling) command.nextSibling.remove(); command.remove(); }
}
const depthSelect = document.createElement('select');
for (const n of [1,2,3]) {
  const option = document.createElement('option'); option.value = n;
  option.textContent = n === 1 ? 'Связи по группам' : `${n} уровня`; depthSelect.appendChild(option);
}
const directionSelect = document.createElement('select');
for (const [value,label] of [['both','Оба направления'],['out','Исходящие'],['in','Входящие']]) {
  const option = document.createElement('option'); option.value = value;
  option.textContent = label; directionSelect.appendChild(option);
}
const status = document.createElement('span'); status.style.marginLeft = '10px';
const overviewButton = document.createElement('button');
overviewButton.textContent = 'Обзор графа';
overviewButton.style.cssText = 'margin:8px 5px 4px 0;padding:5px 8px;color:#f3f4f6;background:#333654;border:1px solid #66708f;cursor:pointer';
const relationSelect = document.createElement('select');
const sourceSelect = document.createElement('select');
const kindSelect = document.createElement('select');
const groupPanel = document.createElement('section');
groupPanel.id = 'onec-groups'; groupPanel.hidden = true;
document.getElementById('sidebar').insertBefore(groupPanel, document.getElementById('info-panel'));
function addOptions(select, values, allLabel) {
  select.replaceChildren();
  for (const [value,label] of [['',allLabel], ...values.map(v => [v,v])]) {
    const option = document.createElement('option'); option.value = value;
    option.textContent = label; select.appendChild(option);
  }
}
addOptions(relationSelect, [], 'Все связи');
addOptions(sourceSelect, [], 'Все источники');
addOptions(kindSelect, [], 'Все типы узлов');
intro.append(overviewButton, depthSelect, directionSelect, relationSelect, sourceSelect, kindSelect, status);
fetch('/api/options').then(r => r.json()).then(options => {
  addOptions(relationSelect, options.relations, 'Все связи');
  addOptions(sourceSelect, ['configuration', ...options.extensions], 'Все источники');
  sourceSelect.options[1].textContent = 'Основная конфигурация';
  addOptions(kindSelect, options.kinds, 'Все типы узлов');
}).catch(() => { status.textContent = 'Не удалось загрузить фильтры'; });
let focusId = null;
let currentData = null;
let activeGroup = null;
let activePage = 0;
let requestSerial = 0;
const relationPriority = {writes:0, EXTENDS:1, BEFORE:2, AFTER:3, INSTEAD:4,
  CHANGE_CONTROL:5, calls:6, calls_server:7, query_reads:8, type_reference:9,
  contains:12, references:13};
const kindNames = {Procedure:'Процедуры', Function:'Функции', Document:'Документы',
  Catalog:'Справочники', Module:'Модули', CommonModule:'Общие модули',
  InformationRegister:'Регистры сведений', AccumulationRegister:'Регистры накопления',
  Extension:'Расширения', Configuration:'Конфигурация', Form:'Формы',
  Attribute:'Реквизиты', FormElement:'Элементы форм', FormAttribute:'Реквизиты форм',
  FormCommand:'Команды форм', EnumValue:'Значения перечислений'};
function showResults(items) {
  searchResults.replaceChildren();
  searchResults.style.display = items.length ? 'block' : 'none';
  for (const n of items) {
    const item = document.createElement('div'); item.className = 'search-item';
    item.textContent = `${n.label} · ${n.extension_name || 'Основная конфигурация'} · ${n.kind}`;
    item.addEventListener('click', () => {
      searchResults.style.display = 'none'; searchInput.value = ''; openNode(n.id);
    });
    searchResults.appendChild(item);
  }
}
async function openGroup(kind) {
  status.textContent = `Группа «${kindNames[kind] || kind}»: выберите объект из списка`;
  const response = await fetch('/api/group?' + new URLSearchParams({kind}));
  if (response.ok) showResults(await response.json());
}
async function showOverview() {
  ++requestSerial;
  focusId = null; currentData = null; activeGroup = null;
  groupPanel.hidden = true;
  document.getElementById('info-content').textContent = 'Выберите группу на схеме или найдите объект по имени.';
  searchResults.style.display = 'none';
  status.textContent = 'Загрузка обзора…';
  try {
    const response = await fetch('/api/overview');
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const data = await response.json();
    network.setOptions({physics:{enabled:false}});
    nodesDS.clear(); edgesDS.clear();
    nodesDS.add(data.nodes.map((n,i) => ({
      id: n.id, label: `${kindNames[n.kind] || n.kind}\n${n.count.toLocaleString('ru-RU')}`,
      title: `${kindNames[n.kind] || n.kind}: ${n.count.toLocaleString('ru-RU')} узлов`,
      color: n.kind === 'Extension' ? '#b07aa1' :
        ['Document','Catalog','InformationRegister'].includes(n.kind) ? '#59a14f' : '#4e79a7',
      size: Math.min(38, 13 + 4 * Math.log10(n.count + 1)),
      x: (i < 7 ? 145 : 335) * Math.cos(2 * Math.PI * (i < 7 ? i/7 : (i-7)/(data.nodes.length-7))),
      y: (i < 7 ? 145 : 335) * Math.sin(2 * Math.PI * (i < 7 ? i/7 : (i-7)/(data.nodes.length-7))),
      fixed: {x:true,y:true},
      font: {color:'#f3f4f6', size:14, strokeWidth:3, strokeColor:'#14141d'},
      _file_type: n.kind, _community_name:'Обзор типов', _degree:n.count
    })));
    edgesDS.add(data.edges.map((e,i) => ({
      id:i, from:e.source, to:e.target,
      title:`${e.relation}: ${e.count.toLocaleString('ru-RU')} связей`,
      width:Math.min(5, 1 + Math.log10(e.count + 1)),
      arrows:{to:{enabled:true, scaleFactor:0.4}}
    })));
    status.textContent = `Обзор по типам: ${data.nodes.length} групп · ${data.edges.length} агрегированных связей. Нажмите группу для списка объектов.`;
    setTimeout(() => network.fit({animation:false}), 50);
  } catch (error) { status.textContent = `Ошибка обзора: ${error.message}`; }
}
overviewButton.addEventListener('click', showOverview);
function renderGroupList(groups) {
  groupPanel.replaceChildren(); groupPanel.hidden = false;
  const heading = document.createElement('b');
  heading.textContent = 'Связи выбранного узла по типу и направлению';
  groupPanel.appendChild(heading);
  const list = document.createElement('div');
  const sorted = [...groups].sort((a,b) =>
    (relationPriority[a.relation] ?? 11) - (relationPriority[b.relation] ?? 11) ||
    b.count - a.count);
  for (const group of sorted) {
    const button = document.createElement('button');
    const arrow = group.direction === 'out' ? '→' : '←';
    button.textContent = `${arrow} ${group.relation} (${group.count})`;
    button.title = group.direction === 'out' ? 'Исходящие связи' : 'Входящие связи';
    if (activeGroup && group.direction === activeGroup.direction &&
        group.relation === activeGroup.relation) button.className = 'active';
    button.addEventListener('click', () => selectGroup(group));
    list.appendChild(button);
  }
  if (!groups.length) list.textContent = 'Связей с выбранными фильтрами нет.';
  groupPanel.appendChild(list);
  const pager = document.createElement('div'); pager.className = 'pager';
  pager.id = 'onec-pager'; groupPanel.appendChild(pager);
  return sorted;
}
async function selectGroup(group, page = 0) {
  activeGroup = group; activePage = page;
  const serial = ++requestSerial;
  status.textContent = `Загрузка: ${group.relation}…`;
  const response = await fetch('/api/neighbor-page?' + new URLSearchParams({
    id:focusId, direction:group.direction, relation:group.relation, page:String(page),
    source:sourceSelect.value, kind:kindSelect.value
  }));
  if (!response.ok) { status.textContent = `Ошибка HTTP ${response.status}`; return; }
  const data = await response.json();
  if (serial !== requestSerial) return;
  currentData = data;
  renderData(data);
  groupPanel.querySelectorAll('button').forEach(button => button.classList.remove('active'));
  const selected = [...groupPanel.querySelectorAll('button')].find(button =>
    button.textContent === `${group.direction === 'out' ? '→' : '←'} ${group.relation} (${group.count})`);
  if (selected) selected.classList.add('active');
  const pager = document.getElementById('onec-pager'); pager.replaceChildren();
  const previous = document.createElement('button'); previous.textContent = '← Назад';
  previous.disabled = page === 0;
  previous.addEventListener('click', () => selectGroup(group, page - 1));
  const next = document.createElement('button'); next.textContent = 'Далее →';
  next.disabled = !data.has_next;
  next.addEventListener('click', () => selectGroup(group, page + 1));
  const label = document.createElement('span');
  label.textContent = ` ${page * data.page_size + 1}–${page * data.page_size + data.edges.length} из ${data.total} `;
  pager.append(previous, label, next);
  status.textContent = `${group.direction === 'out' ? 'Исходящие' : 'Входящие'} ${group.relation}: ` +
    `страница ${page + 1}, ${data.edges.length} из ${data.total} связей`;
}
function renderData(data) {
  network.setOptions({physics:{enabled:true}});
  const visible = data.nodes.filter(n => n.id === focusId || (
    (!sourceSelect.value || (sourceSelect.value === 'configuration' ?
      n.source_type === 'configuration' : n.extension_name === sourceSelect.value)) &&
    (!kindSelect.value || n.kind === kindSelect.value)));
  const ids = new Set(visible.map(n => n.id));
  const links = data.edges.filter(e => ids.has(e.source) && ids.has(e.target));
  const degrees = new Map();
  for (const e of links) {
    degrees.set(e.source, (degrees.get(e.source) || 0) + 1);
    degrees.set(e.target, (degrees.get(e.target) || 0) + 1);
  }
  nodesDS.clear(); edgesDS.clear();
  nodesDS.add(visible.map(n => ({
    id: n.id, label: n.label, title: esc(n.id),
    color: n.source_type === 'extension' ? '#b07aa1' : '#4e79a7',
    font: {color: '#f3f4f6', size: visible.length <= 60 ? 15 : 0,
      strokeWidth: 3, strokeColor: '#14141d'},
    size: n.id === focusId ? 22 : 14,
    _source_file: n.source_file, _file_type: n.kind,
    _community_name: n.extension_name || 'Основная конфигурация',
    _degree: degrees.get(n.id) || 0
  })));
  edgesDS.add(links.map((e,i) => ({
    id: i, from: e.source, to: e.target, title: esc(e.relation),
    label: links.length <= 30 ? e.relation : '',
    font: {color: '#cbd5e1', size: 10, strokeWidth: 2, strokeColor: '#14141d'},
    arrows: {to: {enabled: true, scaleFactor: 0.5}}
  })));
  status.textContent = `${visible.length} узлов · ${links.length} связей` +
    (data.truncated ? ' · показана часть окружения' : '');
  if (ids.has(focusId)) { network.selectNodes([focusId]); showInfo(focusId); }
  network.fit({animation: false});
}
async function openNode(id) {
  const serial = ++requestSerial;
  focusId = id;
  status.textContent = 'Загрузка окружения…';
  try {
    if (Number(depthSelect.value) === 1) {
      const groupsResponse = await fetch('/api/neighbor-groups?' + new URLSearchParams({
        id, direction:directionSelect.value, relation:relationSelect.value,
        source:sourceSelect.value, kind:kindSelect.value
      }));
      if (!groupsResponse.ok) throw new Error(`HTTP ${groupsResponse.status}`);
      const groups = await groupsResponse.json();
      if (serial !== requestSerial) return;
      activeGroup = null;
      const sorted = renderGroupList(groups);
      if (sorted.length) {
        await selectGroup(sorted[0]);
      } else {
        const response = await fetch('/api/neighborhood?' + new URLSearchParams({
          id, depth:'1', direction:directionSelect.value, relation:relationSelect.value || '__none__'
        }));
        currentData = await response.json(); renderData(currentData);
      }
      return;
    }
    groupPanel.hidden = true;
    activeGroup = null;
    const url = '/api/neighborhood?' + new URLSearchParams({
      id, depth: depthSelect.value, direction: directionSelect.value,
      relation: relationSelect.value
    });
    const response = await fetch(url);
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    if (serial !== requestSerial) return;
    currentData = await response.json();
    renderData(currentData);
  } catch (error) { status.textContent = `Ошибка: ${error.message}`; }
}
depthSelect.addEventListener('change', () => { if (focusId) openNode(focusId); });
directionSelect.addEventListener('change', () => { if (focusId) openNode(focusId); });
relationSelect.addEventListener('change', () => { if (focusId) openNode(focusId); });
sourceSelect.addEventListener('change', () => { if (focusId) openNode(focusId); });
kindSelect.addEventListener('change', () => { if (focusId) openNode(focusId); });
let searchTimer;
searchInput.addEventListener('input', () => {
  clearTimeout(searchTimer);
  const q = searchInput.value.trim();
  if (!q) { searchResults.replaceChildren(); searchResults.style.display = 'none'; return; }
  searchTimer = setTimeout(async () => {
    const response = await fetch('/api/search?' + new URLSearchParams({
      q, source: sourceSelect.value, kind: kindSelect.value
    }));
    if (!response.ok) return;
    const items = await response.json();
    showResults(items);
  }, 180);
});
network.on('click', params => {
  if (!params.nodes.length) return;
  const id = params.nodes[0];
  if (id.startsWith('kind:')) openGroup(id.slice(5)); else openNode(id);
});
showOverview();
"""


if __name__ == "__main__":
    main()
