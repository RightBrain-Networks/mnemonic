// Local declarations for the small public API used by lib/markdown.ts.
// The browser build bundles its dependencies and runs on both the server
// and browser without their separate type packages.
declare class MarkdownIt {
  constructor(options: {
    html: boolean;
    breaks: boolean;
    linkify: boolean;
    typographer: boolean;
  });
  disable(rule: string): this;
  render(source: string): string;
}

export default MarkdownIt;
