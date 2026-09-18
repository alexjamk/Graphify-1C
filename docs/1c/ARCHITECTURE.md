# Graphify-1C: architecture and implementation plan

The current requirements for the unified configuration, extensions and viewer
are tracked in [REQUIREMENTS_UNIFIED_GRAPH.md](REQUIREMENTS_UNIFIED_GRAPH.md).
The 1C-specific viewer design is recorded in [ADR 004](ADR-004-visualization.md).
The planned computed effective configuration is described in
[ADR 005](ADR-005-effective-configuration.md).

## Baseline

Upstream: `Graphify-Labs/graphify`, branch `v8`, commit
`26b02b5e3430e4ab85dd7e72c7b98836d8e65c48` (18 September 2026).

The upstream pipeline is `detect → extract → build → cluster → report → export`.
`graphify/extract.py` dispatches by suffix to language extractors, then applies
cross-file symbol resolution. Extraction dictionaries contain nodes and directed
edges; `graphify/build.py` loads them into NetworkX, and `graphify/export.py`
writes JSON/HTML and other formats. Edge confidence is restricted to
`EXTRACTED`, `INFERRED`, and `AMBIGUOUS`. `graphify/__main__.py` and
`graphify/serve.py` provide CLI and MCP entry points; `path`, `explain`, and
`query` consume saved graph JSON. Tests are under `tests/`.

1C requires project-wide metadata before call resolution. A `.bsl` extractor
alone cannot identify the owner of `ObjectModule.bsl` or resolve a query table.
Therefore `graphify.onec.project.extract_project()` reads the exported project,
builds a 1C symbol table, parses BSL/SDBL, resolves edges, and returns the
existing Graphify extraction schema. `graphify onec` feeds that extraction to
the existing graph builder and JSON exporter. Core changes are confined to a
CLI dispatch and packaging entry. This preserves upstream mergeability.

Tested inputs include Configurator **XML exports** with `Configuration.xml`,
type directories such as `Documents/`, object XML files, and `Ext/*.bsl`, and
EDT source projects with `src/Configuration/Configuration.mdo`, metadata
`.mdo`, and managed-form `.form` files. The [1C developer guide](https://1c-dn.com/library/tutorials/1c_enterprise_developer_guide_8_3_27/)
documents the platform concepts. Both layouts feed one graph schema.

`tree-sitter-bsl` 0.1.7 is MIT licensed, supports Python 3.10+, and exposes
separate BSL and SDBL grammars. Probe ASTs contained `procedure_definition`,
`function_definition`, `preprocessor/annotation`, `call_expression`, and SDBL
`table_source`; embedded query text stays a BSL string and needs a second parse.
The package has Windows, Linux, and macOS wheels. Upstream Graphify supports
Python 3.10+ and tree-sitter 0.23–0.25. The 1C grammar is an optional dependency
so other Graphify installations are unaffected.

## Stable IDs and resolution

Metadata IDs use `1c://<Type>/<Name>`, modules append their kind, and methods
append their name. The BSL spelling is preserved for display; lookup indexes
use `casefold()`. Edges retain Graphify confidence labels. Explicit XML
containment is `EXTRACTED`; statically resolved call destinations are
`INFERRED`; multiple global candidates are `AMBIGUOUS`. Extensions use
`1c://Extension/<Name>/...` as described in ADR 002. Multiple independent base
configurations are not merged by one invocation.

For the standard layout, `graphify analyze ./src` discovers `src/cf` and each
valid direct child of `src/cfe`. The extractor reads each export separately,
then resolves calls against indexed base and extension symbols. Nodes
retain `source_type` and `extension_name`; borrowed objects and extension
configurations use `EXTENDS` edges. Multiple hooks to one base method remain
separate edges because their extension method IDs are distinct.

## Implementation sequence

1. Complete Configurator metadata traversal: attributes, table sections,
   compound types, and module ownership. Add fixtures from real exports.
2. Harden BSL extraction: local/common/global calls, execution contexts,
   metadata manager references, dynamic-call evidence, and parser diagnostics.
3. Expand SDBL analysis to joins, virtual tables, query fragments, and writes.
4. Add forms, event subscriptions, scheduled jobs, and subsystem edges.
5. Expand extension resolution beyond direct and global calls.
6. Expand EDT coverage, add incremental extraction, and profile large projects.

Each step requires targeted tests, an integration fixture, CLI verification,
full upstream tests, status updates, and a logical commit.
