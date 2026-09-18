"""Ordered, replaceable BSL call-resolution rules."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from .metadata_types import MANAGERS


_MANAGER_PREFIXES = {name.casefold(): kind for name, kind in MANAGERS.items()}


@dataclass(frozen=True)
class Symbols:
    methods: dict[tuple[str, str], str]
    objects: dict[tuple[str, str], str]
    exported_methods: set[str]
    global_methods: dict[str, list[str]]


@dataclass(frozen=True)
class CombinedSymbols:
    """Indexes for unresolved calls after base and extensions are assembled."""

    base_methods: dict[str, str]
    common_methods: dict[tuple[str, str], list[str]]
    global_methods: dict[str, list[str]]
    manager_methods: dict[tuple[str, str, str], list[str]]

    @classmethod
    def from_nodes(cls, nodes: list[dict]) -> CombinedSymbols:
        owners = {node["id"].casefold(): node for node in nodes if node["kind"] == "CommonModule"}
        all_owners = {
            node["id"].casefold(): node
            for node in nodes
            if node["kind"] in set(_MANAGER_PREFIXES.values())
        }
        base_methods: dict[str, str] = {}
        common_methods: dict[tuple[str, str], list[str]] = {}
        global_methods: dict[str, list[str]] = {}
        manager_methods: dict[tuple[str, str, str], list[str]] = {}
        for node in nodes:
            if node["kind"] not in {"Procedure", "Function"}:
                continue
            if node.get("source_type") == "configuration":
                base_methods[node["id"].casefold()] = node["id"]
            if not node.get("export"):
                continue
            owner = owners.get(node.get("metadata_object", "").casefold())
            if node.get("module", "").endswith("/ManagerModule"):
                manager_owner = all_owners.get(node.get("metadata_object", "").casefold())
                if manager_owner is not None:
                    manager_methods.setdefault(
                        (
                            manager_owner["kind"].casefold(),
                            manager_owner["label"].casefold(),
                            node["label"].casefold(),
                        ),
                        [],
                    ).append(node["id"])
            if owner is None:
                continue
            common_methods.setdefault(
                (owner["label"].casefold(), node["label"].casefold()), []
            ).append(node["id"])
            if str(owner.get("global", "")).casefold() == "true":
                global_methods.setdefault(node["label"].casefold(), []).append(node["id"])
        return cls(base_methods, common_methods, global_methods, manager_methods)

    def resolve(self, call: str, module_id: str) -> list[str]:
        parts = call.split(".")
        if len(parts) == 3:
            kind = _MANAGER_PREFIXES.get(parts[0].casefold())
            if kind:
                return self.manager_methods.get(
                    (kind.casefold(), parts[1].casefold(), parts[2].casefold()), []
                )
            return []
        if len(parts) == 2:
            return self.common_methods.get((parts[0].casefold(), parts[1].casefold()), [])
        if len(parts) != 1:
            return []
        local = self.base_methods.get(f"{module_id}/{call}".casefold())
        if local:
            return [local]
        return self.global_methods.get(call.casefold(), [])


class CallRule(Protocol):
    def resolve(self, call: str, module_id: str, symbols: Symbols) -> list[str] | None: ...


class LocalCallResolver:
    def resolve(self, call: str, module_id: str, symbols: Symbols) -> list[str] | None:
        if "." in call:
            return None
        target = symbols.methods.get((module_id.casefold(), call.casefold()))
        return [target] if target else None


class CommonModuleResolver:
    def resolve(self, call: str, module_id: str, symbols: Symbols) -> list[str] | None:
        parts = call.split(".")
        if len(parts) != 2:
            return None
        owner = symbols.objects.get(("commonmodule", parts[0].casefold()))
        if not owner:
            return None
        target = symbols.methods.get((f"{owner}/CommonModule".casefold(), parts[1].casefold()))
        return [target] if target in symbols.exported_methods else []


class ManagerModuleResolver:
    def resolve(self, call: str, module_id: str, symbols: Symbols) -> list[str] | None:
        parts = call.split(".")
        if len(parts) != 3:
            return None
        kind = _MANAGER_PREFIXES.get(parts[0].casefold())
        if kind is None:
            return None
        owner = symbols.objects.get((kind.casefold(), parts[1].casefold()))
        if not owner:
            return []
        target = symbols.methods.get((f"{owner}/ManagerModule".casefold(), parts[2].casefold()))
        return [target] if target in symbols.exported_methods else []


class GlobalModuleResolver:
    def resolve(self, call: str, module_id: str, symbols: Symbols) -> list[str] | None:
        if "." in call:
            return None
        return symbols.global_methods.get(call.casefold(), [])


RULES: tuple[CallRule, ...] = (
    LocalCallResolver(),
    CommonModuleResolver(),
    ManagerModuleResolver(),
    GlobalModuleResolver(),
)


def resolve_call(
    call: str, module_id: str, symbols: Symbols, rules: tuple[CallRule, ...] = RULES
) -> list[str]:
    """Return all candidates from the first rule that applies to a call."""
    for rule in rules:
        candidates = rule.resolve(call, module_id, symbols)
        if candidates is not None:
            return candidates
    return []
