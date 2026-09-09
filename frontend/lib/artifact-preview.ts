import type { Artifact } from "./artifacts.ts";

export type ArtifactPreviewKind = "text" | "markdown" | "image";

// These image types stay inert even when their object URL opens in a new tab.
const imageTypes = new Set([
  "image/png", "image/jpeg", "image/gif", "image/webp", "image/avif",
  "image/bmp", "image/x-icon", "image/vnd.microsoft.icon", "image/tiff"
]);

export function artifactPreviewKind(artifact: Pick<Artifact, "filename" | "mime_type" | "content_available">): ArtifactPreviewKind | null {
  if (!artifact.content_available) return null;
  const mime = artifact.mime_type?.split(";")[0].trim().toLowerCase();
  if (mime && imageTypes.has(mime)) return "image";
  if (mime === "text/markdown" || mime === "text/x-markdown") return "markdown";
  // Detection identifies Markdown as plain text; use its extension to choose the renderer.
  if (!mime || mime === "text/plain" || mime === "application/octet-stream") {
    if (/\.(md|markdown|mdown|mkd)$/i.test(artifact.filename)) return "markdown";
    if (mime === "text/plain" || /\.txt$/i.test(artifact.filename)) return "text";
  }
  return null;
}
