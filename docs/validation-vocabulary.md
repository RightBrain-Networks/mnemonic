# Public validation vocabulary

[validation-vocabulary.json](validation-vocabulary.json) is the reviewed catalog
of field names that may appear in validation-error locations. Each surface uses
the union of `common_fields` and its entry in `surface_fields`. The separate
`browser_location_roots` catalog names the leading HTTP locations the browser
removes when presenting a field path.

The local constants remain in each deployable package. Backend, MCP, and browser
unit tests compare those constants exactly with this catalog; all three consume
[the same location corpus](../tests/fixtures/validation-locations-v1.json).
No package needs this documentation file at runtime.

When adding a public input field, review whether its name is safe to expose on
each surface, update the catalog and the affected local constants, and run all
three vocabulary test files. Keep each catalog list sorted and free of duplicates.
Move a field to `common_fields` only when every surface approves it. Do not
populate this catalog automatically from OpenAPI or from arbitrary metadata keys.
An input value is never approved by approving the name of its field.

The catalog deliberately does not unify sanitizer behavior:

- The backend replaces unapproved location segments with `field`, retains integer
  indices, and emits fixed messages/error types without raw input or context.
- The MCP adapter selects approved string segments, omits indices, and formats
  only approved error types. It sanitizes both local and upstream failures.
- The browser removes a leading HTTP root, preserves nonnegative safe array
  indices after a known field, and stops at the first unapproved segment.
  Its message text remains trusted backend/proxy output; it is not a replacement
  for the backend's raw Pydantic-error sanitizer.

Surface-only fields preserve existing boundaries, such as the MCP `changes`
argument. Unknown names, including free-form metadata keys, retain each
sanitizer's existing fallback behavior.
