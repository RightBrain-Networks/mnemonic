import { Link } from "@tanstack/react-router";
import type { ComponentPropsWithRef } from "react";
import { parseDashboardSearch } from "@/lib/dashboard-routes";

// Existing task helpers return complete relative URLs. Split them at this
// boundary so links and programmatic navigation share the router's URL state.
export default function DashboardLink({ href, ...props }: ComponentPropsWithRef<"a"> & { href: string }) {
  const target = new URL(href, "http://mnemonic.invalid");
  return <Link {...props} to={target.pathname} search={parseDashboardSearch(target.search)}
    hash={target.hash.slice(1)} activeProps={{}} />;
}
