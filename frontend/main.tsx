import { StrictMode, useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import { RouterProvider } from "@tanstack/react-router";
import { QueryClientProvider } from "@tanstack/react-query";
import { createDashboardQueryClient } from "./lib/query-client";
import { createApplicationRouter } from "./router";
import { loadRuntimeConfig } from "./lib/runtime-config";
import "./app/globals.css";

function Application() {
  const [queryClient] = useState(createDashboardQueryClient);
  const [router, setRouter] = useState<ReturnType<typeof createApplicationRouter> | null>(null);
  const [error, setError] = useState(false);
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    setError(false);
    void loadRuntimeConfig(controller.signal)
      .then((config) => { if (!controller.signal.aborted) setRouter(createApplicationRouter(config)); })
      .catch(() => { if (!controller.signal.aborted) setError(true); });
    return () => controller.abort();
  }, [attempt]);

  if (router) return <QueryClientProvider client={queryClient}><RouterProvider router={router} /></QueryClientProvider>;
  return <main className="bootstrap-state">
    <h1>Mnemonic<span className="brand-period">.</span></h1>
    {error ? <div role="alert"><p>Dashboard settings could not be loaded. Check your connection and try again.</p>
      <button className="button button-primary" onClick={() => setAttempt((value) => value + 1)}>Retry loading settings</button></div>
      : <p role="status">Loading dashboard settings…</p>}
  </main>;
}

createRoot(document.getElementById("root")!).render(<StrictMode><Application /></StrictMode>);
