import { createRootRouteWithContext, createRoute, createRouter, Outlet, redirect } from "@tanstack/react-router";
import Dashboard from "@/components/dashboard";
import WorkSummaryLimitProvider from "@/components/work-summary-limit-provider";
import { canonicalDashboardPath, dashboardPaths, parseDashboardSearch, savedWorkRedirect, stringifyDashboardSearch } from "@/lib/dashboard-routes";
import type { RuntimeConfig } from "@/lib/runtime-config";

const rootRoute = createRootRouteWithContext<{ runtimeConfig: RuntimeConfig }>()({
  component: Outlet,
  notFoundComponent: NotFound
});

function NotFound() {
  return <main className="bootstrap-state"><h1>Page not found.</h1>
    <p>This address does not belong to the dashboard.</p><a href="/">Return to Mnemonic</a></main>;
}

function DashboardRoot() {
  const { runtimeConfig } = rootRoute.useRouteContext();
  return <WorkSummaryLimitProvider maximum={runtimeConfig.work_summary_max_chars}>
    <Dashboard timeZone={runtimeConfig.timezone} artifactMaxBytes={runtimeConfig.artifact_max_bytes}
      backupMaxBytes={runtimeConfig.backup_max_bytes} />
    <Outlet />
  </WorkSummaryLimitProvider>;
}

// One shared layout owns the dashboard, mutation registry and pending actions.
// Leaf routes carry identity only; changing a view never remounts that owner.
const dashboardRoute = createRoute({
  getParentRoute: () => rootRoute,
  id: "dashboard",
  component: DashboardRoot
});
const routeTree = rootRoute.addChildren([
  dashboardRoute.addChildren(dashboardPaths.map((path) => createRoute({
    getParentRoute: () => dashboardRoute,
    path,
    beforeLoad: ({ location }) => {
      const pathname = canonicalDashboardPath(location.pathname);
      const destination = savedWorkRedirect(pathname, location.searchStr)
        ?? (pathname !== location.pathname ? pathname + location.searchStr : null);
      if (destination) {
        const hashAt = location.href.indexOf("#");
        const hash = hashAt < 0 ? "" : location.href.slice(hashAt);
        throw redirect({ href: destination + hash, replace: true });
      }
    },
    component: () => null
  })))
]);

export function createApplicationRouter(runtimeConfig: RuntimeConfig) {
  return createRouter({
    routeTree,
    context: { runtimeConfig },
    parseSearch: parseDashboardSearch,
    stringifySearch: stringifyDashboardSearch,
    defaultNotFoundComponent: NotFound,
    notFoundMode: "root"
  });
}
