import MarkdownIt from "../vendor/markdown-it/markdown-it.esm.min.mjs";

// Agent-authored prose is untrusted. Keep raw HTML disabled and use the
// upstream parser's escaping and link validation for every consumer.
const markdown = new MarkdownIt({
  html: false,
  breaks: true,
  linkify: true,
  typographer: false
}).disable("image");

/** Render human-facing Markdown without embedded HTML or remote image loads. */
export function markdownToHtml(source: string): string {
  return markdown.render(source);
}
