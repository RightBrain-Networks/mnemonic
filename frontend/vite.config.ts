import { fileURLToPath, URL } from "node:url";
import { defineConfig, type Connect } from "vite";
import type { IncomingMessage } from "node:http";
import type { Duplex } from "node:stream";
import react from "@vitejs/plugin-react";
import { themeInitializationScript } from "./lib/theme-preference.ts";
import { applicationSettingsInitializationScript } from "./lib/application-settings.ts";
import { libraryToolsInitializationScript } from "./lib/dashboard-preferences.ts";
import { canonicalDashboardPath, dashboardPaths } from "./lib/dashboard-routes.ts";

const browserBoundary = /^\/api\/(mnemonic|artifacts|transcripts|backups)(\/|$)/;
const privateBoundary = /^\/(api|worker|projects|docs|redoc|openapi\.json|readyz)(\/|$)/;
const documentBoundary: Connect.NextHandleFunction = (request, response, next) => {
  const url = new URL(request.url ?? "/", "http://mnemonic.invalid");
  if (privateBoundary.test(url.pathname) && !browserBoundary.test(url.pathname)) {
    response.statusCode = 404;
    response.end("Not found");
    return;
  }
  if (["GET", "HEAD"].includes(request.method ?? "")
    && dashboardPaths.some((path) => path === canonicalDashboardPath(url.pathname))) {
    request.url = `/index.html${url.search}`;
  }
  next();
};

function rejectPrivateUpgrade(request: IncomingMessage, socket: Duplex) {
  const pathname = new URL(request.url ?? "/", "http://mnemonic.invalid").pathname;
  if (privateBoundary.test(pathname) && pathname !== "/api/mnemonic/sync") {
    socket.end("HTTP/1.1 404 Not Found\r\nConnection: close\r\nContent-Length: 0\r\n\r\n");
  }
}

export default defineConfig({
  // Only named document routes receive index.html. Disabling Vite's blanket
  // SPA fallback keeps missing API and asset requests as real 404 responses.
  appType: "mpa",
  plugins: [react(), {
    name: "mnemonic-document-boundary",
    configureServer(server) {
      server.middlewares.use(documentBoundary);
      server.httpServer?.on("upgrade", rejectPrivateUpgrade);
    },
    configurePreviewServer(server) {
      server.middlewares.use(documentBoundary);
      server.httpServer.on("upgrade", rejectPrivateUpgrade);
    }
  }, {
    name: "mnemonic-prepaint-preferences",
    transformIndexHtml: {
      order: "pre",
      handler: (html) => html.replace(
        "<!-- mnemonic-prepaint-preferences -->",
        `<script>${[themeInitializationScript, applicationSettingsInitializationScript, libraryToolsInitializationScript].join("\n")}</script>`
      )
    }
  }],
  resolve: { alias: { "@": fileURLToPath(new URL(".", import.meta.url)) } },
  server: {
    host: "127.0.0.1",
    port: 3000,
    strictPort: true,
    proxy: {
      "^/api/mnemonic/sync(?:\\?|$)": {
        target: process.env.MNEMONIC_DEV_API_URL ?? "http://127.0.0.1:8000",
        ws: true
      },
      "^/api/(mnemonic|artifacts|transcripts|backups)(/|$)": {
        target: process.env.MNEMONIC_DEV_API_URL ?? "http://127.0.0.1:8000"
      }
    }
  },
  build: { outDir: "dist", sourcemap: false }
});
