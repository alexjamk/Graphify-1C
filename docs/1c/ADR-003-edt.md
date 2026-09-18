# ADR 003: EDT projects use the same extraction schema

Status: Accepted (18 September 2026)

## Context

Configurator XML and EDT project sources organize metadata differently. EDT stores objects in directories containing `.mdo` files and managed forms in `.form` files. Both formats describe the same 1C configuration concepts.

## Decision

Detect EDT through `Configuration/Configuration.mdo` (at the root or under `src`). Normalize object and child names through a central type registry. Feed both formats into the same stable `1c://` IDs, resolver and Graphify extraction dictionaries. Keep `source_format` on the configuration node and source paths on all evidence.

## Consequences

The existing Graphify graph builder, query, path, explain and exporters work on either source format. EDT form parsing currently covers core items, commands, attributes and events; specialized controls need incremental extensions. A protected binary `.bsl` module is marked `binary_skipped` instead of being parsed as source.
