import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { readFile } from "node:fs/promises";
import test from "node:test";
import { markdownToHtml } from "../lib/markdown.ts";

test("plain messages retain text, Unicode, paragraph boundaries, and line breaks", () => {
  assert.equal(markdownToHtml(""), "");
  assert.equal(markdownToHtml("Hello مرحبًا 🙂 & < 3"), "<p>Hello مرحبًا 🙂 &amp; &lt; 3</p>\n");
  assert.equal(markdownToHtml("First line\nSecond line\n\nNext paragraph."),
    "<p>First line<br>\nSecond line</p>\n<p>Next paragraph.</p>\n");
  assert.equal(markdownToHtml("First\r\nSecond"), "<p>First<br>\nSecond</p>\n");
});

test("human summaries and FYIs support emphasis, inline code, strikethrough, and links", () => {
  const html = markdownToHtml("**Ready** for *review*: use `npm test`, ~~old command~~, [details](https://example.com/review?a=1&b=2).");
  assert.match(html, /<strong>Ready<\/strong> for <em>review<\/em>/);
  assert.match(html, /<code>npm test<\/code>/);
  assert.match(html, /<s>old command<\/s>/);
  assert.match(html, /<a href="https:\/\/example.com\/review\?a=1&amp;b=2">details<\/a>/);
  assert.equal(markdownToHtml("**later**"), "<p><strong>later</strong></p>\n");
});

test("questions support headings, nested lists, quotations, fenced code, and tables", () => {
  const html = markdownToHtml([
    "## Choose a rollout", "", "1. **Stage first**", "   - Check readiness", "2. Deploy", "",
    "> Keep the old version ready.", "", "```sh", "npm test && npm run build", "```", "",
    "| Option | Cost |", "| --- | ---: |", "| Stage | 5 minutes |"
  ].join("\n"));
  assert.match(html, /<h2>Choose a rollout<\/h2>/);
  assert.match(html, /<ol>\n<li><strong>Stage first<\/strong>\n<ul>\n<li>Check readiness<\/li>/);
  assert.match(html, /<blockquote>\n<p>Keep the old version ready\.<\/p>/);
  assert.match(html, /<pre><code class="language-sh">npm test &amp;&amp; npm run build\n<\/code><\/pre>/);
  assert.match(html, /<table>[\s\S]*<th>Option<\/th>[\s\S]*<td>Stage<\/td>/);
  assert.match(html, /<td style="text-align:right">5 minutes<\/td>/);
});

test("safe explicit, reference, automatic, and relative links render", () => {
  const html = markdownToHtml([
    "[Dashboard](/summaries) and [mail](mailto:team@example.com)", "",
    "[Reference][docs] and https://example.com/help", "", "[docs]: https://example.com/docs"
  ].join("\n"));
  for (const href of ["/summaries", "mailto:team@example.com", "https://example.com/docs", "https://example.com/help"]) {
    assert.ok(html.includes(`href="${href}"`));
  }
  // Reference definitions must not leak between independent messages.
  assert.equal(markdownToHtml("[Reference][docs]"), "<p>[Reference][docs]</p>\n");
});

test("raw HTML, event attributes, and code samples remain inert text", () => {
  const html = markdownToHtml([
    '<script>alert("x")</script>', '<img src=x onerror=alert(1)>',
    '<iframe srcdoc="<script>alert(1)</script>"></iframe>', '<svg onload=alert(1)>',
    "", '`<button onclick="alert(1)">`', "", "```html", '<script>alert("code")</script>', "```"
  ].join("\n"));
  assert.doesNotMatch(html, /<(script|img|iframe|svg|button)\b/i);
  assert.match(html, /&lt;script&gt;alert\(&quot;x&quot;\)&lt;\/script&gt;/);
  assert.match(html, /<code>&lt;button onclick=&quot;alert\(1\)&quot;&gt;<\/code>/);
  assert.match(html, /<pre><code class="language-html">&lt;script&gt;/);
});

test("dangerous and entity-obfuscated link schemes never become anchors", () => {
  for (const target of [
    "javascript:alert(1)", "JaVaScRiPt:alert(1)", "jav&#x61;script:alert(1)",
    "javascript&#58;alert(1)", "vbscript:msgbox(1)", "file:///etc/passwd", "data:text/html;base64,PHNjcmlwdD4="
  ]) {
    assert.doesNotMatch(markdownToHtml(`[Unsafe](${target})`), /<a\b/i, target);
    assert.doesNotMatch(markdownToHtml(`[Unsafe][x]\n\n[x]: ${target}`), /<a\b/i, target);
  }
  const html = markdownToHtml('[quote](https://example.com/ "x\\\" onmouseover=alert(1)")');
  assert.equal(html, '<p><a href="https://example.com/" title="x&quot; onmouseover=alert(1)">quote</a></p>\n');
  assert.equal(markdownToHtml('```" onmouseover="alert(1)\ntext\n```'),
    '<pre><code class="language-&quot;">text\n</code></pre>\n');
});

test("image Markdown never embeds remote resources", () => {
  for (const source of [
    "![Tracking pixel](https://example.com/track.png)",
    "![SVG](data:image/svg+xml;base64,PHN2Zz4=)",
    "![Image][img]\n\n[img]: /image.png"
  ]) assert.doesNotMatch(markdownToHtml(source), /<(img|image)\b/i);
});

test("vendored runtime and license notices match the recorded hashes", async () => {
  const base = new URL("../vendor/markdown-it/", import.meta.url);
  const provenance = JSON.parse(await readFile(new URL("upstream.json", base), "utf8"));
  for (const [path, expected] of Object.entries(provenance.files)) {
    const bytes = await readFile(new URL(path, base));
    assert.equal(createHash("sha256").update(bytes).digest("hex"), expected, path);
  }
});
