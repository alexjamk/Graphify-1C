"""Project-wide 1C metadata and BSL extraction.

The resulting dictionaries use Graphify's ordinary extraction schema.  All
resolution happens here, where the complete configuration symbol table exists.
"""

from __future__ import annotations

import re
from collections import Counter
from time import perf_counter
import xml.etree.ElementTree as ET
from pathlib import Path

from tree_sitter import Parser
import tree_sitter_bsl

from .resolver import CombinedSymbols, Symbols, resolve_call
from .module_cache import ModuleCache
from .metadata_types import (
    TYPE_DIRS,
    MANAGERS,
    QUERY_TYPES,
    CHILD_TYPES,
    EDT_CHILD_TYPES,
    REFERENCE_TYPES,
)

_BSL_LANGUAGE = tree_sitter_bsl.Language()
_SDBL_LANGUAGE = tree_sitter_bsl.SDBLLanguage()
_METADATA_PREFIXES = {key.casefold(): value for key, value in {**QUERY_TYPES, **MANAGERS}.items()}


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _property(element: ET.Element, name: str) -> str | None:
    for child in element.iter():
        if _local(child.tag).casefold() != name.casefold():
            continue
        if child.text and child.text.strip():
            return child.text.strip()
        if name.casefold() == "synonym":
            for part in child.iter():
                if (
                    _local(part.tag).casefold() in {"content", "value"}
                    and part.text
                    and part.text.strip()
                ):
                    return part.text.strip()
    return None


def _node(identifier: str, label: str, source_file: str, kind: str, **extra: object) -> dict:
    return {
        "id": identifier,
        "label": label,
        "file_type": "code",
        "source_file": source_file,
        "source_path": source_file,
        "kind": kind,
        "metadata_type": label if kind == "Module" else kind,
        **extra,
    }


def _edge(
    source: str,
    target: str,
    relation: str,
    file: str,
    confidence: str = "EXTRACTED",
    **extra: object,
) -> dict:
    return {
        "source": source,
        "target": target,
        "relation": relation,
        "relation_type": relation,
        "confidence": confidence,
        "source_file": file,
        **extra,
    }


def _walk(node):
    yield node
    for child in node.named_children:
        yield from _walk(child)


def _module_items(node):
    """Flatten preprocessor regions while preserving method order and directives."""
    for child in node.named_children:
        if child.type == "preprocessor" and any(
            nested.type in {"preprocessor", "procedure_definition", "function_definition"}
            for nested in child.named_children
        ):
            yield from _module_items(child)
        else:
            yield child


def _text(data: bytes, node) -> str:
    return data[node.start_byte : node.end_byte].decode("utf-8")


def _metadata_target(
    parts: list[str], objects: dict[tuple[str, str], str]
) -> tuple[str, str | None] | None:
    if len(parts) < 2:
        return None
    kind = _METADATA_PREFIXES.get(parts[0].casefold())
    if not kind:
        return None
    target = objects.get((kind.casefold(), parts[1].casefold()))
    if not target:
        return None
    return target, parts[2] if len(parts) > 2 else None


def _query_sources(
    query: str, objects: dict[tuple[str, str], str], known_ids: dict[str, str]
) -> tuple[list[tuple[str, str | None]], bool]:
    parser = Parser(_SDBL_LANGUAGE)
    root = parser.parse(query.encode("utf-8")).root_node
    result: list[tuple[str, str | None]] = []
    data = query.encode("utf-8")
    for node in _walk(root):
        if node.type not in {"table_source", "join_clause"}:
            continue
        name = node.child_by_field_name("name" if node.type == "table_source" else "source")
        if name is None:
            continue
        raw = _text(data, name).split("(", 1)[0]
        parts = raw.split(".")
        resolved = _metadata_target(parts, objects)
        if resolved and len(parts) >= 3:
            section = known_ids.get(f"{resolved[0]}/TabularSection/{parts[2]}".casefold())
            if section:
                resolved = (section, None)
        if resolved and resolved not in result:
            result.append(resolved)
    return result, root.has_error


def _decode_bsl_string(raw: str) -> str:
    if not raw.startswith('"') or not raw.endswith('"'):
        return ""
    return re.sub(r"(?m)^\s*\|", "", raw[1:-1]).replace('""', '"')


def _queue_type_references(
    element: ET.Element,
    source: str,
    rel: str,
    pending: list[tuple[str, str, str, str]],
) -> None:
    properties = next(
        (part for part in element if _local(part.tag).casefold() == "properties"), None
    )
    if properties is None:
        properties = element
    for type_container in properties:
        if _local(type_container.tag).casefold() != "type":
            continue
        for text in type_container.itertext():
            for ref_type, ref_name in re.findall(r"(?:cfg:)?(\w+)\.([\wА-Яа-яЁё]+)", text):
                kind = REFERENCE_TYPES.get(ref_type)
                if kind:
                    pending.append((source, kind, ref_name, rel))


def _static_text(
    data: bytes, expression, values: dict[str, tuple[str, bool]]
) -> tuple[str, bool] | None:
    """Evaluate simple BSL string concatenation, retaining partial evidence."""
    if expression.type == "string":
        return _decode_bsl_string(_text(data, expression)), True
    if expression.type == "identifier":
        return values.get(_text(data, expression).casefold())
    if expression.type == "binary_expression":
        operator = next(
            (part for part in expression.named_children if part.type == "operator"), None
        )
        if operator is not None and _text(data, operator) == "+":
            left = _static_text(data, expression.child_by_field_name("left"), values)
            right = _static_text(data, expression.child_by_field_name("right"), values)
            if left or right:
                return (left[0] if left else "") + (right[0] if right else ""), bool(
                    left and right and left[1] and right[1]
                )
    if len(expression.named_children) == 1:
        return _static_text(data, expression.named_children[0], values)
    strings = [part for part in _walk(expression) if part.type == "string"]
    if strings:
        return "".join(_decode_bsl_string(_text(data, part)) for part in strings), False
    return None


def _movement_targets(name: str, objects: dict[tuple[str, str], str]) -> list[str]:
    return [
        objects[(kind.casefold(), name.casefold())]
        for kind in ("AccumulationRegister", "AccountingRegister", "CalculationRegister")
        if (kind.casefold(), name.casefold()) in objects
    ]


def _query_assignment(
    data: bytes, assignment, values: dict[str, tuple[str, bool]]
) -> tuple[str, bool] | None:
    """Recover Query.Text from literals or known local string variables."""
    left = assignment.child_by_field_name("left")
    right = assignment.child_by_field_name("right")
    if (
        left is None
        or right is None
        or not _text(data, left).casefold().endswith((".текст", ".text"))
    ):
        return None
    resolved = _static_text(data, right, values)
    return (resolved[0], not resolved[1]) if resolved else None


def _query_constructor(
    data: bytes, assignment, values: dict[str, tuple[str, bool]]
) -> tuple[str, bool] | None:
    """Read a literal passed directly to Новый Запрос(...)."""
    right = assignment.child_by_field_name("right")
    if right is None:
        return None
    constructor = next((node for node in _walk(right) if node.type == "new_expression"), None)
    if constructor is None:
        return None
    name = next((node for node in constructor.named_children if node.type == "identifier"), None)
    if name is None or _text(data, name).casefold() not in {"запрос", "query"}:
        return None
    arguments = next(
        (node for node in constructor.named_children if node.type == "arguments"), None
    )
    if arguments is None or len(arguments.named_children) != 1:
        return None
    argument = arguments.named_children[0]
    resolved = _static_text(data, argument, values)
    return (resolved[0], not resolved[1]) if resolved else None


def _metadata_children(
    element: ET.Element,
    parent_id: str,
    rel: str,
    nodes: list[dict],
    edges: list[dict],
    pending_types: list[tuple[str, str, str, str]],
    pending_handlers: list[tuple[str, str, str, str]],
    module_owner_id: str,
) -> None:
    """Read XML child objects while retaining tabular-section ownership."""
    for child in element:
        raw_kind = _local(child.tag)
        kind = EDT_CHILD_TYPES.get(raw_kind.casefold(), raw_kind)
        if kind in CHILD_TYPES:
            name = _property(child, "Name")
            if not name:
                continue
            identifier = f"{parent_id}/{kind}/{name}"
            extra = {}
            for field in ("HTTPMethod", "Template", "ProcedureName"):
                value = _property(child, field)
                if value:
                    extra[field.lower()] = value
            nodes.append(_node(identifier, name, rel, kind, **extra))
            edges.append(_edge(parent_id, identifier, "contains", rel))
            handler = _property(child, "Handler" if kind == "Method" else "ProcedureName")
            if handler and kind in {"Method", "Operation"}:
                pending_handlers.append((identifier, f"{module_owner_id}/Module", handler, rel))
            _queue_type_references(child, identifier, rel, pending_types)
            _metadata_children(
                child,
                identifier,
                rel,
                nodes,
                edges,
                pending_types,
                pending_handlers,
                module_owner_id,
            )
        else:
            _metadata_children(
                child,
                parent_id,
                rel,
                nodes,
                edges,
                pending_types,
                pending_handlers,
                module_owner_id,
            )


def _form_contents(
    root: Path,
    xml: Path,
    form_id: str,
    nodes: list[dict],
    edges: list[dict],
    modules: list[tuple[Path, str, str]],
    pending_handlers: list[tuple[str, str, str, str]],
    pending_types: list[tuple[str, str, str, str]],
) -> None:
    """Add a managed form's module, commands, attributes and event bindings."""
    form_dir = xml.with_suffix("")
    module = form_dir / "Ext" / "Form" / "Module.bsl"
    module_id = f"{form_id}/Module"
    if module.is_file():
        rel = module.relative_to(root).as_posix()
        nodes.append(_node(module_id, "FormModule", rel, "Module"))
        edges.append(_edge(form_id, module_id, "contains", rel))
        modules.append((module, module_id, form_id))
    contents = form_dir / "Ext" / "Form.xml"
    if not contents.is_file():
        return
    rel = contents.relative_to(root).as_posix()
    tree = ET.parse(contents).getroot()
    parents = {child: parent for parent in tree.iter() for child in parent}
    element_ids: dict[ET.Element, str] = {}
    command_ids: dict[str, str] = {}
    command_uses: list[tuple[str, str]] = []
    for section in tree.iter():
        if _local(section.tag) != "ChildItems":
            continue
        for element in section:
            name = element.get("name")
            if not name:
                continue
            element_id = f"{form_id}/Element/{name}"
            element_ids[element] = element_id
            nodes.append(
                _node(element_id, name, rel, "FormElement", element_type=_local(element.tag))
            )
            edges.append(_edge(form_id, element_id, "contains", rel))
            command_name = next(
                (
                    child.text.strip()
                    for child in element
                    if _local(child.tag) == "CommandName" and child.text
                ),
                None,
            )
            if command_name:
                command_uses.append((element_id, command_name.rsplit(".", 1)[-1]))
    for section in tree.iter():
        if _local(section.tag) == "Commands":
            for command in section:
                if _local(command.tag) != "Command" or not command.get("name"):
                    continue
                name = command.get("name")
                command_id = f"{form_id}/Command/{name}"
                command_ids[name.casefold()] = command_id
                nodes.append(_node(command_id, name, rel, "FormCommand"))
                edges.append(_edge(form_id, command_id, "contains", rel))
                action = _property(command, "Action")
                if action:
                    pending_handlers.append((command_id, module_id, action, rel))
        elif _local(section.tag) == "Attributes":
            for attribute in section:
                if _local(attribute.tag) == "Attribute" and attribute.get("name"):
                    name = attribute.get("name")
                    attribute_id = f"{form_id}/Attribute/{name}"
                    nodes.append(_node(attribute_id, name, rel, "FormAttribute"))
                    edges.append(_edge(form_id, attribute_id, "contains", rel))
                    _queue_type_references(attribute, attribute_id, rel, pending_types)
        elif _local(section.tag) == "Parameters":
            for parameter in section:
                if _local(parameter.tag) == "Parameter" and parameter.get("name"):
                    name = parameter.get("name")
                    parameter_id = f"{form_id}/Parameter/{name}"
                    nodes.append(_node(parameter_id, name, rel, "FormParameter"))
                    edges.append(_edge(form_id, parameter_id, "contains", rel))
                    _queue_type_references(parameter, parameter_id, rel, pending_types)
        elif _local(section.tag) == "Events":
            owner = element_ids.get(parents.get(section), form_id)
            for event in section:
                if _local(event.tag) == "Event" and event.text and event.text.strip():
                    pending_handlers.append((owner, module_id, event.text.strip(), rel))
    for element_id, command_name in command_uses:
        command_id = command_ids.get(command_name.casefold())
        if command_id:
            edges.append(_edge(element_id, command_id, "uses_command", rel))


def _edt_form_contents(
    root: Path,
    form_file: Path,
    form_id: str,
    nodes: list[dict],
    edges: list[dict],
    pending_handlers: list[tuple[str, str, str, str]],
    pending_types: list[tuple[str, str, str, str]],
) -> None:
    """Read EDT managed-form items, attributes, commands and event handlers."""
    if not form_file.is_file():
        return
    rel = form_file.relative_to(root).as_posix()
    form = ET.parse(form_file).getroot()
    module_id = f"{form_id}/Module"
    commands: dict[str, str] = {}
    uses: list[tuple[str, str]] = []

    def add_item(item: ET.Element) -> None:
        name = _property(item, "name")
        if name:
            item_id = f"{form_id}/Element/{name}"
            nodes.append(_node(item_id, name, rel, "FormElement"))
            edges.append(_edge(form_id, item_id, "contains", rel))
            command = _property(item, "commandName")
            if command:
                uses.append((item_id, command.rsplit(".", 1)[-1]))
            for event in item:
                if _local(event.tag) == "events":
                    handler = _property(event, "handler")
                    if handler:
                        pending_handlers.append((item_id, module_id, handler, rel))
        for child in item:
            if _local(child.tag) == "items":
                add_item(child)

    for section in form:
        kind = _local(section.tag)
        if kind == "items":
            add_item(section)
        elif kind in {"attributes", "parameters", "commands"}:
            name = _property(section, "name")
            if not name:
                continue
            node_kind = {
                "attributes": "FormAttribute",
                "parameters": "FormParameter",
                "commands": "FormCommand",
            }[kind]
            id_kind = {"attributes": "Attribute", "parameters": "Parameter", "commands": "Command"}[
                kind
            ]
            identifier = f"{form_id}/{id_kind}/{name}"
            nodes.append(_node(identifier, name, rel, node_kind))
            edges.append(_edge(form_id, identifier, "contains", rel))
            if kind == "commands":
                commands[name.casefold()] = identifier
                action = _property(section, "action")
                if action:
                    pending_handlers.append((identifier, module_id, action, rel))
            else:
                _queue_type_references(section, identifier, rel, pending_types)
        elif kind == "events":
            handler = _property(section, "handler")
            if handler:
                pending_handlers.append((form_id, module_id, handler, rel))
    for item_id, command in uses:
        target = commands.get(command.casefold())
        if target:
            edges.append(_edge(item_id, target, "uses_command", rel))


def _extract_single(
    root: Path, timings: dict[str, float] | None = None, module_cache: Path | None = None
) -> dict:
    """Extract a Configurator XML export or EDT source tree."""
    root = Path(root).resolve()
    phase_started = perf_counter()
    source_root = (
        root / "src" if (root / "src" / "Configuration" / "Configuration.mdo").is_file() else root
    )
    edt = (source_root / "Configuration" / "Configuration.mdo").is_file()
    configuration_file = (
        source_root / "Configuration" / "Configuration.mdo" if edt else root / "Configuration.xml"
    )
    if not configuration_file.is_file():
        raise ValueError(f"Not a Configurator XML export or EDT project: {root}")
    nodes: list[dict] = []
    edges: list[dict] = []
    config = ET.parse(configuration_file).getroot()
    config_name = _property(config, "Name") or root.name
    config_id = f"1c://Configuration/{config_name}"
    nodes.append(
        _node(
            config_id,
            config_name,
            configuration_file.relative_to(root).as_posix(),
            "Configuration",
            source_format="EDT" if edt else "Configurator",
        )
    )
    objects: dict[tuple[str, str], str] = {}
    global_modules: set[str] = set()
    server_modules: set[str] = set()
    modules: list[tuple[Path, str, str]] = []
    pending_types: list[tuple[str, str, str, str]] = []
    pending_handlers: list[tuple[str, str, str, str]] = []
    pending_entrypoints: list[tuple[str, str, str, str]] = []
    pending_sources: list[tuple[str, str, str, str]] = []
    pending_members: list[tuple[str, str, str, str]] = []
    unresolved_movements: list[tuple[str, str, str, str]] = []
    role_rights: list[tuple[str, Path]] = []
    event_nodes: set[str] = set()
    config_module_dir = source_root / "Configuration" if edt else root / "Ext"
    for bsl in sorted(config_module_dir.glob("*.bsl")):
        module_id = f"{config_id}/{bsl.stem}"
        rel = bsl.relative_to(root).as_posix()
        nodes.append(_node(module_id, bsl.stem, rel, "Module"))
        edges.append(_edge(config_id, module_id, "contains", rel))
        modules.append((bsl, module_id, config_id))

    for directory, kind in TYPE_DIRS.items():
        candidates = (
            (source_root / directory).glob("*/*.mdo") if edt else (root / directory).glob("*.xml")
        )
        for xml in sorted(candidates):
            rel = xml.relative_to(root).as_posix()
            element = ET.parse(xml).getroot()
            name = _property(element, "Name") or xml.stem
            identifier = f"1c://{kind}/{name}"
            objects[(kind.casefold(), name.casefold())] = identifier
            body = next((child for child in element if _local(child.tag) == kind), element)
            properties = {
                key: _property(element, key)
                for key in (
                    "Synonym",
                    "Comment",
                    "ClientManagedApplication",
                    "ClientOrdinaryApplication",
                    "Server",
                    "ServerCall",
                    "ExternalConnection",
                    "Global",
                    "Privileged",
                    "ReturnValuesReuse",
                    "Use",
                    "Predefined",
                    "MethodName",
                    "RootURL",
                    "ObjectBelonging",
                )
            }
            if body.get("uuid"):
                properties["UUID"] = body.get("uuid")
            object_node = _node(
                identifier,
                name,
                rel,
                kind,
                **{k.lower(): v for k, v in properties.items() if v},
            )
            nodes.append(object_node)
            if kind == "CommonModule" and (properties.get("Global") or "").casefold() == "true":
                global_modules.add(identifier)
            if kind == "CommonModule" and (properties.get("Server") or "").casefold() == "true":
                server_modules.add(identifier)
            edges.append(_edge(config_id, identifier, "contains", rel))
            _queue_type_references(body, identifier, rel, pending_types)
            _metadata_children(
                element,
                identifier,
                rel,
                nodes,
                edges,
                pending_types,
                pending_handlers,
                identifier,
            )
            object_dir = xml.parent if edt else xml.with_suffix("")
            if kind == "Role":
                rights = object_dir / "Ext" / "Rights.xml"
                if rights.is_file():
                    role_rights.append((identifier, rights))
            if edt:
                for form_meta in body:
                    if _local(form_meta.tag).casefold() != "forms":
                        continue
                    form_name = _property(form_meta, "Name")
                    if not form_name:
                        continue
                    form_id = f"{identifier}/Form/{form_name}"
                    nodes.append(_node(form_id, form_name, rel, "Form"))
                    edges.append(_edge(identifier, form_id, "contains", rel))
                    form_module = object_dir / "Forms" / form_name / "Module.bsl"
                    if form_module.is_file():
                        module_rel = form_module.relative_to(root).as_posix()
                        module_id = f"{form_id}/Module"
                        nodes.append(_node(module_id, "FormModule", module_rel, "Module"))
                        edges.append(_edge(form_id, module_id, "contains", module_rel))
                        modules.append((form_module, module_id, form_id))
                    _edt_form_contents(
                        root,
                        object_dir / "Forms" / form_name / "Form.form",
                        form_id,
                        nodes,
                        edges,
                        pending_handlers,
                        pending_types,
                    )
            elif kind == "CommonForm":
                _form_contents(
                    root, xml, identifier, nodes, edges, modules, pending_handlers, pending_types
                )
            else:
                for form_xml in sorted((object_dir / "Forms").glob("*.xml")):
                    form_metadata = ET.parse(form_xml).getroot()
                    form_name = _property(form_metadata, "Name") or form_xml.stem
                    form_id = f"{identifier}/Form/{form_name}"
                    form_rel = form_xml.relative_to(root).as_posix()
                    nodes.append(
                        _node(
                            form_id,
                            form_name,
                            form_rel,
                            "Form",
                            objectbelonging=_property(form_metadata, "ObjectBelonging"),
                        )
                    )
                    edges.append(_edge(identifier, form_id, "contains", form_rel))
                    _form_contents(
                        root,
                        form_xml,
                        form_id,
                        nodes,
                        edges,
                        modules,
                        pending_handlers,
                        pending_types,
                    )
            if kind == "ScheduledJob":
                method_name = _property(element, "MethodName")
                if method_name:
                    pending_entrypoints.append((identifier, method_name, "executes", rel))
                schedule = object_dir / "Ext" / "Schedule.xml"
                if schedule.is_file():
                    object_node["schedule_file"] = schedule.relative_to(root).as_posix()
            elif kind == "EventSubscription":
                event_name = _property(element, "Event")
                if event_name:
                    object_node["event"] = event_name
                    event_id = f"1c://PlatformEvent/{event_name}"
                    if event_id not in event_nodes:
                        nodes.append(_node(event_id, event_name, rel, "PlatformEvent"))
                        event_nodes.add(event_id)
                    edges.append(_edge(identifier, event_id, "event", rel))
                handler = _property(element, "Handler")
                if handler:
                    pending_entrypoints.append((identifier, handler, "handler", rel))
                for source in element.iter():
                    if _local(source.tag).casefold() != "source":
                        continue
                    for value in source.itertext():
                        match = re.search(r"(?:cfg:)?([A-Za-z]+)\.([\wА-Яа-яЁё]+)", value)
                        if match and match.group(1) in REFERENCE_TYPES:
                            pending_sources.append(
                                (identifier, REFERENCE_TYPES[match.group(1)], match.group(2), rel)
                            )
            elif kind == "Subsystem":
                for content in element.iter():
                    if _local(content.tag).casefold() != "content":
                        continue
                    for item in content.iter():
                        if _local(item.tag).casefold() not in {"item", "content"} or not item.text:
                            continue
                        parts = item.text.strip().split(".")
                        if len(parts) >= 2:
                            pending_members.append((identifier, parts[0], parts[1], rel))
            ext = object_dir if edt else object_dir / "Ext"
            for bsl in sorted(ext.glob("*.bsl")):
                module_kind = bsl.stem
                if kind == "CommonModule":
                    module_kind = "CommonModule"
                module_id = f"{identifier}/{module_kind}"
                bsl_rel = bsl.relative_to(root).as_posix()
                nodes.append(_node(module_id, module_kind, bsl_rel, "Module"))
                edges.append(_edge(identifier, module_id, "contains", bsl_rel))
                modules.append((bsl, module_id, identifier))

    for source, kind, name, rel in pending_types:
        target = objects.get((kind.casefold(), name.casefold()))
        if target:
            edges.append(_edge(source, target, "type_reference", rel))
    for source, kind, name, rel in pending_sources:
        target = objects.get((kind.casefold(), name.casefold()))
        if target:
            edges.append(_edge(source, target, "source", rel))
    for source, kind, name, rel in pending_members:
        target = objects.get((kind.casefold(), name.casefold()))
        if target:
            edges.append(_edge(source, target, "includes", rel))
    known_ids = {node["id"].casefold(): node["id"] for node in nodes}
    for role_id, rights_path in role_rights:
        rel = rights_path.relative_to(root).as_posix()
        rights_xml = ET.parse(rights_path).getroot()
        for granted_object in rights_xml.iter():
            if _local(granted_object.tag) != "object":
                continue
            object_name = next(
                (
                    part.text.strip()
                    for part in granted_object
                    if _local(part.tag) == "name" and part.text
                ),
                None,
            )
            if not object_name:
                continue
            parts = object_name.split(".")
            owner = (
                objects.get((parts[0].casefold(), parts[1].casefold())) if len(parts) > 1 else None
            )
            full_target = owner + "/" + "/".join(parts[2:]) if owner and len(parts) > 2 else owner
            target = known_ids.get(full_target.casefold()) if full_target else None
            if not target:
                continue
            grants: list[str] = []
            for right in granted_object:
                if _local(right.tag) != "right":
                    continue
                right_name = _property(right, "name")
                value = _property(right, "value")
                if right_name and value and value.casefold() == "true":
                    grants.append(right_name)
            if grants:
                edges.append(_edge(role_id, target, "grants", rel, rights=grants))
    if timings is not None:
        timings["metadata"] = timings.get("metadata", 0.0) + perf_counter() - phase_started
    phase_started = perf_counter()

    methods: dict[tuple[str, str], str] = {}
    exported_methods: set[str] = set()
    method_contexts: dict[str, str] = {}
    method_owners: dict[str, str] = {}
    globals_by_name: dict[str, list[str]] = {}
    pending: list[tuple[str, str, str, str]] = []
    module_nodes = {node["id"]: node for node in nodes if node["kind"] == "Module"}
    cache = ModuleCache(module_cache, root) if module_cache else None
    for bsl, module_id, owner_id in modules:
        rel = bsl.relative_to(root).as_posix()
        data = bsl.read_bytes()
        data = data.removeprefix(b"\xef\xbb\xbf")
        if data.startswith(b"\xff\xff\xff\x7f") or b"\x00" in data[:4096]:
            module_nodes[module_id]["parse_status"] = "binary_skipped"
            continue
        if data.lstrip().startswith((b"<?xml", b"<mdclass:")):
            module_nodes[module_id]["parse_status"] = "non_bsl_skipped"
            continue
        try:
            data.decode("utf-8")
        except UnicodeDecodeError:
            module_nodes[module_id]["parse_status"] = "invalid_utf8_skipped"
            continue
        can_cache = cache is not None and len(data) >= 4096
        if cache is not None and not can_cache:
            cache.misses += 1
        cached = cache.get(bsl, data) if can_cache else None
        if cached is not None:
            module_nodes[module_id].update(cached.get("module_attributes", {}))
            nodes.extend(cached["nodes"])
            edges.extend(cached["edges"])
            pending.extend(tuple(row) for row in cached["pending"])
            unresolved_movements.extend(tuple(row) for row in cached["unresolved_movements"])
            for method in cached["nodes"]:
                if method["kind"] not in {"Procedure", "Function"}:
                    continue
                method_id = method["id"]
                name = method["name"]
                methods[(module_id.casefold(), name.casefold())] = method_id
                method_contexts[method_id] = method["execution_context"]
                method_owners[method_id] = owner_id
                if method["export"]:
                    exported_methods.add(method_id)
                    if owner_id in global_modules:
                        globals_by_name.setdefault(name.casefold(), []).append(method_id)
            continue
        start_nodes = len(nodes)
        start_edges = len(edges)
        start_pending = len(pending)
        start_movements = len(unresolved_movements)
        parsed = Parser(_BSL_LANGUAGE).parse(data).root_node
        if parsed.has_error:
            module_nodes[module_id]["parse_status"] = "partial"
        preceding_annotations: list[str] = []
        for item in _module_items(parsed):
            if item.type == "preprocessor":
                if any(child.type == "annotation" for child in item.named_children):
                    preceding_annotations.append(_text(data, item).splitlines()[0])
                continue
            if item.type not in {"procedure_definition", "function_definition"}:
                preceding_annotations = []
                continue
            name_node = item.child_by_field_name("name")
            if name_node is None:
                continue
            name = _text(data, name_node)
            method_id = f"{module_id}/{name}"
            parameters_node = item.child_by_field_name("parameters")
            parameters = (
                [
                    _text(data, parameter.child_by_field_name("name"))
                    for parameter in _walk(parameters_node)
                    if parameter.type == "parameter"
                    and parameter.child_by_field_name("name") is not None
                ]
                if parameters_node is not None
                else []
            )
            exported = item.child_by_field_name("export") is not None
            context = next(
                (
                    a.lstrip("&")
                    for a in preceding_annotations
                    if a.casefold()
                    in {
                        "&наклиенте",
                        "&насервере",
                        "&насерверебезконтекста",
                        "&наклиентенасерверебезконтекста",
                    }
                ),
                "UNKNOWN",
            )
            nodes.append(
                _node(
                    method_id,
                    name,
                    rel,
                    "Function" if item.type == "function_definition" else "Procedure",
                    name=name,
                    full_name=method_id,
                    module=module_id,
                    metadata_object=owner_id,
                    parameters=parameters,
                    start_line=item.start_point.row + 1,
                    source_location=f"L{item.start_point.row + 1}",
                    end_line=item.end_point.row + 1,
                    file=rel,
                    export=exported,
                    execution_context=context,
                    annotations=preceding_annotations,
                )
            )
            edges.append(_edge(module_id, method_id, "contains", rel))
            methods[(module_id.casefold(), name.casefold())] = method_id
            method_contexts[method_id] = context
            method_owners[method_id] = owner_id
            if exported:
                exported_methods.add(method_id)
            if exported and owner_id in global_modules:
                globals_by_name.setdefault(name.casefold(), []).append(method_id)
            preceding_annotations = []
            string_values: dict[str, tuple[str, bool]] = {}
            record_variables: dict[str, str] = {}
            for child in _walk(item):
                if child.type == "execute_statement":
                    dynamic_id = f"{method_id}/DynamicCall/{child.start_point.row + 1}"
                    nodes.append(
                        _node(
                            dynamic_id,
                            "Выполнить",
                            rel,
                            "DynamicCall",
                            source_location=f"L{child.start_point.row + 1}",
                            expression=_text(data, child),
                        )
                    )
                    edges.append(_edge(method_id, dynamic_id, "dynamic_call", rel, "AMBIGUOUS"))
                elif child.type == "method_call" and child.parent.type != "call_expression":
                    called = child.child_by_field_name("name")
                    if called is not None:
                        called_name = _text(data, called)
                        pending.append((method_id, module_id, called_name, rel))
                        if called_name.casefold() in {"выполнить", "вычислить", "execute", "eval"}:
                            dynamic_id = f"{method_id}/DynamicCall/{child.start_point.row + 1}"
                            nodes.append(
                                _node(
                                    dynamic_id,
                                    called_name,
                                    rel,
                                    "DynamicCall",
                                    source_location=f"L{child.start_point.row + 1}",
                                    expression=_text(data, child),
                                )
                            )
                            edges.append(
                                _edge(method_id, dynamic_id, "dynamic_call", rel, "AMBIGUOUS")
                            )
                elif child.type == "call_expression":
                    access = next((x for x in child.named_children if x.type == "access"), None)
                    method = next(
                        (x for x in child.named_children if x.type == "method_call"), None
                    )
                    if method is None:
                        continue
                    called = method.child_by_field_name("name")
                    if called is None:
                        continue
                    receiver = _text(data, access) if access else ""
                    receiver_parts = receiver.split(".")
                    if (
                        len(receiver_parts) == 1
                        and _text(data, called).casefold() in {"записать", "write"}
                        and (target := record_variables.get(receiver.casefold()))
                    ):
                        edges.append(_edge(method_id, target, "writes", rel, "INFERRED"))
                    if (
                        len(receiver_parts) == 2
                        and receiver_parts[0].casefold() in {"движения", "movements"}
                        and _text(data, called).casefold() in {"добавить", "add"}
                    ):
                        write_targets = _movement_targets(receiver_parts[1], objects)
                        if not write_targets:
                            unresolved_movements.append(
                                (method_id, owner_id, receiver_parts[1], rel)
                            )
                        for target in write_targets:
                            confidence = "AMBIGUOUS" if len(write_targets) > 1 else "INFERRED"
                            edges.append(_edge(method_id, target, "writes", rel, confidence))
                            if owner_id.startswith("1c://Document/"):
                                edges.append(_edge(owner_id, target, "writes", rel, confidence))
                    pending.append(
                        (
                            method_id,
                            module_id,
                            receiver + "." + _text(data, called)
                            if receiver
                            else _text(data, called),
                            rel,
                        )
                    )
                elif child.type == "assignment_statement":
                    extracted = _query_assignment(data, child, string_values) or _query_constructor(
                        data, child, string_values
                    )
                    if extracted:
                        query, dynamic = extracted
                        dynamic = dynamic or child.parent != item
                        sources, parse_error = _query_sources(query, objects, known_ids)
                        for target, virtual in sources:
                            edges.append(
                                _edge(
                                    method_id,
                                    target,
                                    "query_reads",
                                    rel,
                                    "INFERRED" if dynamic or parse_error else "EXTRACTED",
                                    virtual_table=virtual,
                                    partial=dynamic or parse_error,
                                )
                            )
                    left = child.child_by_field_name("left")
                    right = child.child_by_field_name("right")
                    if left is not None and right is not None:
                        parts = _text(data, left).split(".")
                        if (
                            len(parts) == 3
                            and parts[0].casefold() in {"движения", "movements"}
                            and parts[2].casefold() in {"записывать", "write"}
                            and _text(data, right).strip().casefold() in {"истина", "true"}
                        ):
                            write_targets = _movement_targets(parts[1], objects)
                            if not write_targets:
                                unresolved_movements.append((method_id, owner_id, parts[1], rel))
                            for target in write_targets:
                                confidence = "AMBIGUOUS" if len(write_targets) > 1 else "INFERRED"
                                edges.append(_edge(method_id, target, "writes", rel, confidence))
                                if owner_id.startswith("1c://Document/"):
                                    edges.append(_edge(owner_id, target, "writes", rel, confidence))
                    if left is not None and right is not None and left.type == "identifier":
                        key = _text(data, left).casefold()
                        constructor = re.fullmatch(
                            r"(?:РегистрыСведений|InformationRegisters)\.([\w]+)\."
                            r"(?:СоздатьМенеджерЗаписи|CreateRecordManager|"
                            r"СоздатьНаборЗаписей|CreateRecordSet)\(\)",
                            _text(data, right).strip(),
                            re.IGNORECASE,
                        )
                        target = (
                            objects.get(("informationregister", constructor.group(1).casefold()))
                            if constructor
                            else None
                        )
                        if target:
                            record_variables[key] = target
                        else:
                            record_variables.pop(key, None)
                        value = _static_text(data, right, string_values)
                        if value and child.parent == item:
                            string_values[key] = value
                        else:
                            string_values.pop(key, None)
                elif child.type in {"access", "property_access"}:
                    raw = _text(data, child)
                    resolved = _metadata_target(raw.split("."), objects)
                    if resolved:
                        edges.append(_edge(method_id, resolved[0], "references", rel))

        if can_cache:
            cache.put(bsl, data, {
                "module_attributes": {
                    key: value for key, value in module_nodes[module_id].items()
                    if key == "parse_status"
                },
                "nodes": nodes[start_nodes:],
                "edges": edges[start_edges:],
                "pending": pending[start_pending:],
                "unresolved_movements": unresolved_movements[start_movements:],
            })

    if cache:
        cache.close()
        if timings is not None:
            timings["bsl_cache_hits"] = timings.get("bsl_cache_hits", 0) + cache.hits
            timings["bsl_cache_misses"] = timings.get("bsl_cache_misses", 0) + cache.misses
    if timings is not None:
        timings["bsl"] = timings.get("bsl", 0.0) + perf_counter() - phase_started
    phase_started = perf_counter()
    symbols = Symbols(methods, objects, exported_methods, globals_by_name)
    unresolved_calls: list[tuple[str, str, str, str]] = []
    for source, module_id, call, rel in pending:
        candidates = resolve_call(call, module_id, symbols)
        if not candidates:
            unresolved_calls.append((source, module_id, call, rel))
        for target in candidates:
            edges.append(
                _edge(
                    source, target, "calls", rel, "AMBIGUOUS" if len(candidates) > 1 else "INFERRED"
                )
            )
            if method_contexts.get(source, "").casefold() == "наклиенте" and (
                method_contexts.get(target, "").casefold().startswith("насервере")
                or method_owners.get(target) in server_modules
            ):
                edges.append(_edge(source, target, "calls_server", rel, "INFERRED"))

    for source, module_id, handler, rel in pending_handlers:
        target = methods.get((module_id.casefold(), handler.casefold()))
        if target:
            edges.append(_edge(source, target, "handler", rel))
    for source, handler, relation, rel in pending_entrypoints:
        parts = handler.split(".")
        if len(parts) != 3 or parts[0].casefold() != "commonmodule":
            continue
        owner = objects.get(("commonmodule", parts[1].casefold()))
        if owner:
            target = methods.get((f"{owner}/CommonModule".casefold(), parts[2].casefold()))
            if target and target in exported_methods:
                edges.append(_edge(source, target, relation, rel))

    # Repeated references at different AST depths carry the same semantics.
    unique_edges = list({(e["source"], e["target"], e["relation"]): e for e in edges}.values())
    if timings is not None:
        timings["resolution"] = timings.get("resolution", 0.0) + perf_counter() - phase_started
    return {
        "nodes": nodes,
        "edges": unique_edges,
        "_unresolved_calls": unresolved_calls,
        "_unresolved_movements": unresolved_movements,
    }


_HOOK_RELATIONS = {
    "перед": "BEFORE",
    "после": "AFTER",
    "вместо": "INSTEAD",
    "изменениеиконтроль": "CHANGE_CONTROL",
}


def _is_project_export(root: Path) -> bool:
    for configuration in (
        root / "Configuration.xml",
        root / "Configuration" / "Configuration.mdo",
        root / "src" / "Configuration" / "Configuration.mdo",
    ):
        if not configuration.is_file():
            continue
        try:
            document = ET.parse(configuration).getroot()
        except ET.ParseError:
            continue
        if _property(document, "Name") and any(
            _local(item.tag).casefold() == "configuration" for item in document.iter()
        ):
            return True
    return False


def _project_layout(root: Path) -> tuple[Path, list[Path]]:
    """Locate cf and all immediate cfe children in a standard 1C source tree."""
    root = Path(root).resolve()
    layout = root if (root / "cf").is_dir() else root / "src"
    base = layout / "cf"
    if not _is_project_export(base):
        return root, []
    extension_dir = layout / "cfe"
    extensions = (
        [
            child
            for child in sorted(extension_dir.iterdir())
            if child.is_dir() and _is_project_export(child)
        ]
        if extension_dir.is_dir()
        else []
    )
    return base, extensions


def _with_diagnostics(graph: dict, unresolved_calls: list[str]) -> dict:
    metadata_kinds = set(TYPE_DIRS.values())
    calls = [edge for edge in graph["edges"] if edge["relation"] == "calls"]
    graph["diagnostics"] = {
        "metadata_objects": sum(node["kind"] in metadata_kinds for node in graph["nodes"]),
        "modules": sum(node["kind"] == "Module" for node in graph["nodes"]),
        "procedures": sum(node["kind"] == "Procedure" for node in graph["nodes"]),
        "functions": sum(node["kind"] == "Function" for node in graph["nodes"]),
        "extensions": sum(node["kind"] == "Extension" for node in graph["nodes"]),
        "calls_resolved": dict(Counter(edge["confidence"] for edge in calls)),
        "methods_with_query_reads": len(
            {edge["source"] for edge in graph["edges"] if edge["relation"] == "query_reads"}
        ),
        "metadata_references": sum(
            edge["relation"] in {"references", "type_reference"} for edge in graph["edges"]
        ),
        "unresolved_calls": len(unresolved_calls),
        "top_unresolved_calls": Counter(unresolved_calls).most_common(5),
    }
    return graph


def extract_project(
    root: Path,
    *,
    extensions: list[Path] | None = None,
    timings: dict[str, float] | None = None,
    module_cache: Path | None = None,
) -> dict:
    """Build one graph from a base export and every discovered extension."""
    base_root, discovered = _project_layout(root)
    extension_roots = list(
        dict.fromkeys([*discovered, *(Path(p).resolve() for p in extensions or [])])
    )
    base = _extract_single(base_root, timings, module_cache)
    unresolved_names = [call for _, _, call, _ in base.pop("_unresolved_calls", [])]
    base.pop("_unresolved_movements", None)
    for node in base["nodes"]:
        node["source_type"] = "configuration"
        node["extension_name"] = None
        if base_root != Path(root).resolve():
            node["source_file"] = str((base_root / node["source_file"]).resolve())
            node["source_path"] = node["source_file"]
    if base_root != Path(root).resolve():
        for edge in base["edges"]:
            edge["source_file"] = str((base_root / edge["source_file"]).resolve())
    if not extension_roots:
        return _with_diagnostics(base, unresolved_names)
    base_index = {node["id"].casefold(): node["id"] for node in base["nodes"]}
    base_config = next(node["id"] for node in base["nodes"] if node["kind"] == "Configuration")
    pending_calls: list[tuple[str, str, str, str]] = []
    pending_movements: list[tuple[str, str, str, str]] = []
    namespaces: set[str] = set()
    for extension_root in extension_roots:
        extracted = _extract_single(extension_root, timings, module_cache)
        unresolved_calls = extracted.pop("_unresolved_calls", [])
        unresolved_movements = extracted.pop("_unresolved_movements", [])
        config = next(node for node in extracted["nodes"] if node["kind"] == "Configuration")
        old_config_id = config["id"]
        extension_name = config["label"]
        namespace = f"1c://Extension/{extension_name}"
        if namespace.casefold() in namespaces:
            raise ValueError(f"Duplicate extension name: {extension_name}")
        namespaces.add(namespace.casefold())
        mapping = {
            node["id"]: f"{namespace}/{node['id'][len('1c://') :]}" for node in extracted["nodes"]
        }
        # The base ID tail remains human-readable and reversible for hook lookup.
        for node in extracted["nodes"]:
            old_id = node["id"]
            node["id"] = mapping[old_id]
            node["source_file"] = str((Path(extension_root) / node["source_file"]).resolve())
            node["source_path"] = node["source_file"]
            node["source_type"] = "extension"
            node["extension_name"] = extension_name
            for field in ("module", "metadata_object", "full_name"):
                if node.get(field) in mapping:
                    node[field] = mapping[node[field]]
            if old_id == old_config_id:
                node["kind"] = "Extension"
            elif (
                old_id.casefold() in base_index
                and node.get("objectbelonging", "").casefold() == "adopted"
            ):
                base["edges"].append(
                    _edge(node["id"], base_index[old_id.casefold()], "EXTENDS", node["source_file"])
                )
            if node["kind"] not in {"Procedure", "Function"}:
                continue
            for annotation in node.get("annotations", []):
                match = re.fullmatch(r'&([\wА-Яа-яЁё]+)\s*\(\s*"([^"]+)"\s*\)', annotation)
                if not match:
                    continue
                relation = _HOOK_RELATIONS.get(match.group(1).casefold())
                if not relation:
                    continue
                base_method = old_id.rsplit("/", 1)[0] + "/" + match.group(2)
                if base_method.startswith(old_config_id + "/"):
                    base_method = base_config + base_method[len(old_config_id) :]
                target = base_index.get(base_method.casefold())
                if target:
                    base["edges"].append(_edge(node["id"], target, relation, node["source_file"]))
        for edge in extracted["edges"]:
            edge["source"] = mapping[edge["source"]]
            edge["target"] = mapping[edge["target"]]
            edge["source_file"] = str((Path(extension_root) / edge["source_file"]).resolve())
        for source, module_id, call, rel in unresolved_calls:
            pending_calls.append(
                (mapping[source], module_id, call, str((extension_root / rel).resolve()))
            )
        for source, owner, name, rel in unresolved_movements:
            pending_movements.append(
                (mapping[source], mapping[owner], name, str((extension_root / rel).resolve()))
            )
        base["nodes"].extend(extracted["nodes"])
        base["edges"].extend(extracted["edges"])
        base["edges"].append(
            _edge(mapping[old_config_id], base_config, "EXTENDS", config["source_file"])
        )
    symbols = CombinedSymbols.from_nodes(base["nodes"])
    for source, module_id, call, rel in pending_calls:
        candidates = symbols.resolve(call, module_id)
        if not candidates:
            unresolved_names.append(call)
        for target in dict.fromkeys(candidates):
            base["edges"].append(
                _edge(
                    source, target, "calls", rel, "AMBIGUOUS" if len(candidates) > 1 else "INFERRED"
                )
            )
    registers: dict[str, list[str]] = {}
    for node in base["nodes"]:
        if node["kind"] in {"AccumulationRegister", "AccountingRegister", "CalculationRegister"}:
            registers.setdefault(node["label"].casefold(), []).append(node["id"])
    for source, owner, name, rel in pending_movements:
        candidates = registers.get(name.casefold(), [])
        for target in candidates:
            confidence = "AMBIGUOUS" if len(candidates) > 1 else "INFERRED"
            base["edges"].append(_edge(source, target, "writes", rel, confidence))
            if "/Document/" in owner:
                base["edges"].append(_edge(owner, target, "writes", rel, confidence))
    return _with_diagnostics(base, unresolved_names)
