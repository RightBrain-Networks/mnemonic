import { QueryClient } from "@tanstack/react-query";

export function createDashboardQueryClient() {
  return new QueryClient({ defaultOptions: { queries: {
    retry: false,
    staleTime: 30_000,
    gcTime: 5 * 60_000,
    // Activity catch-up and WebSocket reconnect own freshness. In particular,
    // focus must not silently repeat semantic inference.
    refetchOnWindowFocus: false,
    refetchOnReconnect: false
  }, mutations: { retry: false } } });
}
