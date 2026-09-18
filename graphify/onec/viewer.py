"""1C controls layered onto Graphify's existing interactive HTML viewer."""

from __future__ import annotations

import json
from pathlib import Path

import networkx as nx

from graphify.cluster import cluster
from graphify.export import to_html
from graphify.paths import write_text_atomic
from graphify.security import sanitize_label
from graphify.onec.index import build_index


_NODE_FIELDS = (
    "kind",
    "source_type",
    "extension_name",
    "source_file",
    "start_line",
    "end_line",
    "export",
    "execution_context",
    "uuid",
    "module",
    "metadata_object",
)


def _safe_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")


def write_large_onec_html(
    graph_path: Path, output_path: Path, node_count: int, edge_count: int
) -> None:
    """Build the large viewer without materializing a NetworkX graph."""
    from graphify.onec.serve import write_viewer_page

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    database = output_path.with_suffix(".sqlite")
    build_index(graph_path, database)
    write_viewer_page(output_path, node_count, edge_count, database)


def write_onec_html(
    graph: nx.Graph,
    output_path: Path,
    *,
    node_limit: int = 5000,
    graph_path: Path | None = None,
) -> None:
    """Write an embedded small viewer or a SQLite-backed large viewer."""
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    large = graph.number_of_nodes() > node_limit
    if large:
        if graph_path is None:
            raise ValueError("large 1C viewer requires the saved graph JSON path")
        write_large_onec_html(
            graph_path, output_path, graph.number_of_nodes(), graph.number_of_edges()
        )
        return
    overview_name = None
    to_html(graph, cluster(graph), str(output_path), node_limit=node_limit)

    nodes = [
        {
            "id": node_id,
            "label": sanitize_label(str(data.get("label", node_id))),
            **{field: data.get(field) for field in _NODE_FIELDS},
            "kind": data.get("metadata_type") or data.get("kind"),
        }
        for node_id, data in graph.nodes(data=True)
    ]
    edges = [
        {
            "from": source,
            "to": target,
            "relation": data.get("relation", ""),
            "confidence": data.get("confidence", "EXTRACTED"),
        }
        for source, target, data in graph.edges(data=True)
    ]
    payload = _safe_json(
        {
            "nodes": nodes,
            "edges": edges,
            "large": large,
            "overview": overview_name,
            "local_limit": node_limit,
        }
    )
    html = output_path.read_text(encoding="utf-8")
    if "</body>" not in html or '<div id="legend-wrap">' not in html:
        raise ValueError("Graphify HTML template changed; 1C controls could not be attached")
    html = html.replace(
        '<div id="legend-wrap">', '<div id="onec-controls"></div>\n<div id="legend-wrap">', 1
    )
    html = html.replace("</head>", _STYLE + "\n</head>", 1)
    html = html.replace(
        "</body>",
        "<script>\nconst ONEC_DATA = " + payload + ";\n" + _SCRIPT + "\n</script>\n</body>",
        1,
    )
    write_text_atomic(output_path, html)


_STYLE = """<style>
  #graph { min-width: 0; overflow: hidden; }
  #sidebar { flex: 0 0 300px; position: relative; z-index: 1; }
  #info-panel { max-height: 36vh; overflow-y: auto; flex-shrink: 0; }
  #info-content { overflow-wrap: anywhere; }
  #onec-controls { max-height: 46vh; overflow-y: auto; padding: 10px 12px; border-bottom: 1px solid #2a2a4e; font-size: 12px; }
  #onec-controls h3 { font-size: 12px; color: #aaa; margin: 8px 0 5px; }
  #onec-controls label { display: block; padding: 2px 0; cursor: pointer; }
  #onec-controls button, #onec-controls select { background: #26263d; color: #eee; border: 1px solid #4a4a67; border-radius: 4px; padding: 4px; margin: 3px 2px 3px 0; cursor: pointer; }
  #onec-controls .onec-list { max-height: 95px; overflow-y: auto; }
  #onec-controls .onec-hint { color: #aaa; line-height: 1.4; }
</style>"""


_SCRIPT = r"""
const onecNodes = ONEC_DATA.nodes;
const onecEdges = ONEC_DATA.edges;
const onecById = new Map(onecNodes.map(n => [n.id, n]));
const onecCommunityById = new Map(RAW_NODES.map(n => [n.id, n.community]));
const onecOutgoing = new Map(), onecIncoming = new Map();
onecEdges.forEach((e, i) => {
  if (!onecOutgoing.has(e.from)) onecOutgoing.set(e.from, []);
  if (!onecIncoming.has(e.to)) onecIncoming.set(e.to, []);
  onecOutgoing.get(e.from).push(i);
  onecIncoming.get(e.to).push(i);
});
const onecKinds = [...new Set(onecNodes.map(n => n.kind || 'Unknown'))].sort();
const onecExtensions = [...new Set(onecNodes.map(n => n.extension_name).filter(Boolean))].sort();
const onecRelations = [...new Set(onecEdges.map(e => e.relation || 'unknown'))].sort();
const onecPalette = ['#4E79A7','#F28E2B','#E15759','#76B7B2','#59A14F','#EDC948','#B07AA1','#FF9DA7','#9C755F'];
const onecKindColors = new Map(onecKinds.map((kind, i) => [kind, onecPalette[i % onecPalette.length]]));
const onecHiddenSources = new Set(), onecHiddenKinds = new Set(), onecHiddenRelations = new Set();
let onecFocus = null;
let onecDepth = 1;
let onecDirection = 'both';
let onecTruncated = false;

function onecNodeView(n) {
  const color = onecKindColors.get(n.kind) || '#4E79A7';
  return {
    id: n.id, label: n.label, title: esc(n.id),
    color: {background: color, border: n.source_type === 'extension' ? '#d8b4fe' : color},
    size: n.kind === 'Configuration' || n.kind === 'Extension' ? 25 : 13,
    shape: n.kind === 'Extension' ? 'diamond' : 'dot',
    font: {size: 12, color: '#fff'},
    _source_file: n.source_file, _file_type: n.kind,
    _community_name: n.extension_name || 'Основная конфигурация',
    _degree: (onecOutgoing.get(n.id) || []).length + (onecIncoming.get(n.id) || []).length,
  };
}
function onecAllowedRelation(e) { return !onecHiddenRelations.has(e.relation || 'unknown'); }
function onecLocalIds() {
  if (!onecFocus) return ONEC_DATA.large ? new Set() : new Set(onecById.keys());
  const seen = new Set([onecFocus]);
  let frontier = [onecFocus];
  for (let level = 0; level < onecDepth; level++) {
    const next = [];
    for (const id of frontier) {
      const indexes = [];
      if (onecDirection !== 'incoming') indexes.push(...(onecOutgoing.get(id) || []));
      if (onecDirection !== 'outgoing') indexes.push(...(onecIncoming.get(id) || []));
      for (const i of indexes) {
        const e = onecEdges[i];
        if (!onecAllowedRelation(e)) continue;
        const other = e.from === id ? e.to : e.from;
        if (!seen.has(other)) {
          if (seen.size >= ONEC_DATA.local_limit) { onecTruncated = true; return seen; }
          seen.add(other); next.push(other);
        }
      }
    }
    frontier = next;
  }
  return seen;
}
function onecRefresh() {
  onecTruncated = false;
  const local = onecLocalIds();
  const visible = new Set();
  for (const id of local) {
    const n = onecById.get(id);
    if (!n || onecHiddenKinds.has(n.kind || 'Unknown')) continue;
    if (n.source_type === 'extension' && onecHiddenSources.has(n.extension_name)) continue;
    if (n.source_type !== 'extension' && onecHiddenSources.has('configuration')) continue;
    if (hiddenCommunities.has(onecCommunityById.get(id))) continue;
    visible.add(id);
  }
  const shownNodes = [...visible].map(id => onecNodeView(onecById.get(id)));
  const shownEdges = [];
  for (const id of visible) {
    for (const i of onecOutgoing.get(id) || []) {
      const e = onecEdges[i];
      if (!visible.has(e.to) || !onecAllowedRelation(e)) continue;
      if (shownEdges.length >= 10000) { onecTruncated = true; break; }
      shownEdges.push({id: i, from: e.from, to: e.to, title: `${e.relation} [${e.confidence}]`,
        dashes: e.confidence !== 'EXTRACTED', arrows: {to: {enabled: true, scaleFactor: 0.5}}});
    }
    if (shownEdges.length >= 10000) break;
  }
  nodesDS.clear(); edgesDS.clear();
  nodesDS.add(shownNodes); edgesDS.add(shownEdges);
  document.getElementById('onec-count').textContent = `${shownNodes.length} узлов · ${shownEdges.length} связей${onecTruncated ? ' · показана часть окружения' : ''}`;
  if (onecFocus && visible.has(onecFocus)) {
    network.selectNodes([onecFocus]);
    onecShowInfo(onecFocus);
    // DataSet changes are applied by vis-network on the next frame.
    setTimeout(() => network.fit({animation: false}), 100);
  }
}
function onecShowInfo(id) {
  const n = onecById.get(id);
  if (!n) return;
  showInfo(id);
  const panel = document.getElementById('info-content');
  const fields = [
    ['Полное имя', n.id], ['Тип 1С', n.kind], ['Источник', n.source_type],
    ['Расширение', n.extension_name], ['Файл', n.source_file],
    ['Строки', n.start_line ? `${n.start_line}–${n.end_line || n.start_line}` : null],
    ['Экспорт', n.export === undefined || n.export === null ? null : String(n.export)],
    ['Контекст', n.execution_context], ['UUID', n.uuid],
  ];
  for (const [label, value] of fields) {
    if (value === undefined || value === null || value === '') continue;
    const line = document.createElement('div'); line.className = 'field';
    const bold = document.createElement('b'); bold.textContent = label + ': ';
    line.appendChild(bold); line.appendChild(document.createTextNode(value)); panel.appendChild(line);
  }
  const hooks = onecEdges.filter(e => e.from === id && ['BEFORE','AFTER','INSTEAD','CHANGE_CONTROL'].includes(e.relation));
  for (const e of hooks) {
    const line = document.createElement('div'); line.className = 'field';
    line.textContent = `${e.relation} → ${e.to}`; panel.appendChild(line);
  }
}
function onecCheckboxList(title, values, hidden, mapLabel) {
  const section = document.createElement('section');
  const heading = document.createElement('h3'); heading.textContent = title; section.appendChild(heading);
  const list = document.createElement('div'); list.className = 'onec-list';
  for (const value of values) {
    const label = document.createElement('label');
    const cb = document.createElement('input'); cb.type = 'checkbox'; cb.checked = true;
    cb.addEventListener('change', () => { cb.checked ? hidden.delete(value) : hidden.add(value); onecRefresh(); });
    label.appendChild(cb); label.appendChild(document.createTextNode(' ' + mapLabel(value)));
    list.appendChild(label);
  }
  section.appendChild(list); return section;
}
const onecControls = document.getElementById('onec-controls');
const onecHeading = document.createElement('h3'); onecHeading.textContent = 'Граф 1С'; onecControls.appendChild(onecHeading);
const onecCount = document.createElement('div'); onecCount.id = 'onec-count'; onecCount.className = 'onec-hint'; onecControls.appendChild(onecCount);
if (ONEC_DATA.large) {
  const hint = document.createElement('div'); hint.className = 'onec-hint';
  hint.textContent = 'Большой граф: найдите узел, чтобы показать локальное окружение.';
  onecControls.appendChild(hint);
  if (ONEC_DATA.overview) {
    const link = document.createElement('a'); link.href = ONEC_DATA.overview;
    link.textContent = 'Обзор сообществ'; link.style.color = '#93c5fd';
    link.style.display = 'block'; link.style.margin = '6px 0'; onecControls.appendChild(link);
  }
}
const depth = document.createElement('select');
for (const n of [1, 2, 3]) { const option = document.createElement('option'); option.value = n; option.textContent = `${n} уровень`; depth.appendChild(option); }
depth.addEventListener('change', () => { onecDepth = Number(depth.value); onecRefresh(); }); onecControls.appendChild(depth);
const direction = document.createElement('select');
for (const [value, label] of [['both','Оба направления'],['outgoing','Исходящие'],['incoming','Входящие']]) {
  const option = document.createElement('option'); option.value = value; option.textContent = label; direction.appendChild(option);
}
direction.addEventListener('change', () => { onecDirection = direction.value; onecRefresh(); }); onecControls.appendChild(direction);
const reset = document.createElement('button'); reset.textContent = 'Сбросить фокус';
reset.addEventListener('click', () => { onecFocus = null; onecRefresh(); }); onecControls.appendChild(reset);
onecControls.appendChild(onecCheckboxList('Источник', ['configuration', ...onecExtensions], onecHiddenSources,
  value => value === 'configuration' ? 'Основная конфигурация' : value));
onecControls.appendChild(onecCheckboxList('Тип узла', onecKinds, onecHiddenKinds, value => value));
onecControls.appendChild(onecCheckboxList('Тип связи', onecRelations, onecHiddenRelations, value => value.toUpperCase()));
network.on('click', params => { if (params.nodes.length) { onecFocus = params.nodes[0]; onecRefresh(); } });
searchInput.addEventListener('input', () => {
  const q = searchInput.value.toLocaleLowerCase().trim(); searchResults.replaceChildren();
  if (!q) { searchResults.style.display = 'none'; return; }
  const matches = onecNodes.filter(n => `${n.label} ${n.id} ${n.extension_name || ''}`.toLocaleLowerCase().includes(q)).slice(0, 30);
  searchResults.style.display = matches.length ? 'block' : 'none';
  for (const n of matches) {
    const item = document.createElement('div'); item.className = 'search-item';
    item.textContent = `${n.label} · ${n.extension_name || 'Base'} · ${n.kind}`;
    item.addEventListener('click', () => {
      onecFocus = n.id; onecRefresh(); searchResults.style.display = 'none'; searchInput.value = '';
    }); searchResults.appendChild(item);
  }
});
onecRefresh();
"""
