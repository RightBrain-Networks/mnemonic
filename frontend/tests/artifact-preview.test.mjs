import assert from "node:assert/strict";
import test from "node:test";
import { artifactPreviewKind } from "../lib/artifact-preview.ts";

const artifact = (filename, mime_type, content_available = true) => ({ filename, mime_type, content_available });

test("preview chooses Markdown from detected text and its filename", () => {
  assert.equal(artifactPreviewKind(artifact("NOTES.MD", "text/plain")), "markdown");
  assert.equal(artifactPreviewKind(artifact("notes.markdown", null)), "markdown");
  assert.equal(artifactPreviewKind(artifact("notes", "text/markdown; charset=utf-8")), "markdown");
  assert.equal(artifactPreviewKind(artifact("notes.txt", "text/plain")), "text");
  assert.equal(artifactPreviewKind(artifact("LICENSE", "text/plain")), "text");
  assert.equal(artifactPreviewKind(artifact("empty.txt", null)), "text");
  assert.equal(artifactPreviewKind(artifact("empty.md", null)), "markdown");
});

test("preview allows inert image types and excludes unavailable or unsupported bytes", () => {
  for (const mime of ["image/png", "image/jpeg", "image/gif", "image/webp", "image/avif"]) {
    assert.equal(artifactPreviewKind(artifact("image", mime)), "image");
  }
  for (const [filename, mime] of [["report.pdf", "application/pdf"], ["fake.md", "application/pdf"], ["archive.zip", "application/zip"], ["page.html", "text/html"], ["image.svg", "image/svg+xml"], ["unknown.bin", null]]) {
    assert.equal(artifactPreviewKind(artifact(filename, mime)), null);
  }
  assert.equal(artifactPreviewKind(artifact("deleted.txt", "text/plain", false)), null);
});
