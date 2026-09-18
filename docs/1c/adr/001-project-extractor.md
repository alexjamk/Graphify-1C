# ADR 001: project-wide 1C extractor

Status: accepted.

Use a separate `graphify.onec` package that emits Graphify's extraction schema.
1C module identity and call resolution depend on the metadata tree and cannot
be solved reliably by the existing independent per-file extractor dispatch.
The project extractor keeps this context locally and passes its result through
the existing builder/exporter. Reconsider a general extractor hook when there
is another project-aware language needing the same interface.
