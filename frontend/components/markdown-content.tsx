import { markdownToHtml } from "@/lib/markdown";

export default function MarkdownContent({ children, className = "" }: {
  children: string;
  className?: string;
}) {
  return <div
    className={`markdown-content ${className}`.trim()}
    dir="auto"
    dangerouslySetInnerHTML={{ __html: markdownToHtml(children) }}
  />;
}
