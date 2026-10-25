// These are the public document routes. Retired /settings and unknown paths
// deliberately do not resolve to the dashboard.
export const dashboardPaths = [
  "/", "/work-items", "/code-reviews", "/attention", "/summaries",
  "/artifacts", "/transcripts", "/knowledge", "/knowledge/decisions", "/knowledge/domains", "/settings/workspace", "/settings/prompts",
  "/settings/code-reviews", "/settings/backups"
] as const;

export type DashboardSearch = Record<string, string | string[]>;

export function canonicalDashboardPath(pathname: string): string {
  const candidate = pathname.endsWith("/") ? pathname.slice(0, -1) : pathname;
  return dashboardPaths.some((path) => path === candidate) ? candidate : pathname;
}

// Preserve ordinary URLSearchParams semantics instead of coercing saved links
// through the router's default JSON search-parameter serialization.
export function parseDashboardSearch(search: string): DashboardSearch {
  const result: DashboardSearch = Object.create(null);
  for (const [key, value] of new URLSearchParams(search)) {
    const previous = result[key];
    result[key] = previous === undefined ? value
      : Array.isArray(previous) ? [...previous, value] : [previous, value];
  }
  return result;
}

export function stringifyDashboardSearch(search: Record<string, unknown>): string {
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(search)) {
    if (Array.isArray(value)) {
      for (const item of value) if (typeof item === "string") params.append(key, item);
    } else if (typeof value === "string") params.append(key, value);
  }
  const query = params.toString();
  return query ? `?${query}` : "";
}

export function savedWorkRedirect(pathname: string, search: string): string | null {
  const params = new URLSearchParams(search);
  // The former root route redirected only a single work parameter. Preserve
  // every parameter (including repeats) and the original encoding on that hop.
  return pathname === "/" && params.getAll("work").length === 1
    ? `/work-items${search}` : null;
}
