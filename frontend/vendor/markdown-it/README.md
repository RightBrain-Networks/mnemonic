# Vendored Markdown parser

`markdown-it.esm.min.mjs` is the published, self-contained browser ESM build
of [markdown-it 15.0.1](https://github.com/markdown-it/markdown-it/tree/15.0.1),
released August 27, 2026. Its code is unchanged; a final newline was added.
The source map and unused CLI/server builds are omitted. It works in the
Next.js server and client bundles with no CDN or runtime download.

## Selection

Research on September 6, 2026 compared markdown-it, react-markdown, and Marked.
markdown-it provides a synchronous Markdown-to-HTML API, a self-contained build,
CommonMark conformance tests, regression/pathological-input tests, and active
maintenance, including security fixes in this release:

- [Upstream tests](https://github.com/markdown-it/markdown-it/tree/15.0.1/test)
- [Release history](https://github.com/markdown-it/markdown-it/blob/15.0.1/CHANGELOG.md)
- [HTML and escaping options](https://markdown-it.github.io/markdown-it/interfaces/MarkdownItOptions.html)
- [react-markdown](https://github.com/remarkjs/react-markdown): a suitable React
  alternative with a larger dependency graph for a vendored distribution.
- [Marked](https://marked.js.org/): maintained, but requires a separate HTML
  sanitization layer for untrusted messages.

The shared configuration is in `frontend/lib/markdown.ts`; callers use
`MarkdownContent`. HTML stays disabled, upstream URL validation remains enabled,
and images are disabled to avoid automatic remote requests from message prose.
Never pass arbitrary HTML directly to the component's HTML sink or enable raw
HTML, custom highlighting, or plugins without reviewing their output safety.

## Provenance and upgrades

`upstream.json` records the npm archive URL, SHA-512 integrity, upstream commit,
bundled dependency versions, and SHA-256 hashes of the retained upstream files.
The dependency versions come from that release's `package-lock.json` and were
matched to its browser source map. `LICENSE` covers markdown-it; `licenses/`
retains the notices for every dependency embedded in the browser bundle.
`markdown-it.esm.min.d.mts` is a local declaration of the small API we use.

For an upgrade, download the selected release with `npm pack markdown-it@VERSION`
in a temporary directory, verify its registry integrity, and replace the browser
build from `package/dist/browser/markdown-it.esm.min.mjs` (add a final newline if
absent). Review the release notes and browser source map for dependency changes,
refresh notices and `upstream.json`, then run the frontend unit tests, typecheck,
build, and `markdown-messages.spec.ts` browser tests. Vendored code is outside
`npm audit`; review upstream security fixes when upgrading dependencies.
