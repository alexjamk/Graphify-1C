# ADR 002: namespace extension nodes

Status: accepted.

Each extension export gets `1c://Extension/<Name>/...` IDs while base objects
retain `1c://<Type>/<Name>/...`. This keeps identically named borrowed objects
and methods distinct. An `extends` edge links extension to base configuration;
`borrows` links adopted metadata objects; interception methods link to the
base method through `before`, `after`, `instead`, or `change_control`.

Extension roots are explicit CLI inputs. This avoids guessing which neighboring
directories are extensions. Duplicate extension names fail rather than silently
merging IDs. Cross-layer calls and UUID-based matching remain future work.
