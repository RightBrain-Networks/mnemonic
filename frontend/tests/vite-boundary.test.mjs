import assert from "node:assert/strict";
import { randomBytes } from "node:crypto";
import { once } from "node:events";
import { createServer as createHttpServer } from "node:http";
import { connect } from "node:net";
import { fileURLToPath } from "node:url";
import test from "node:test";
import { createServer } from "vite";

test("Vite proxies public browser traffic while private routes and missing assets stay closed", async (t) => {
  const received = [];
  const upgrades = [];
  const upstream = createHttpServer((request, response) => {
    received.push({ url: request.url, headers: request.headers });
    response.setHeader("Content-Type", "application/json");
    response.end(JSON.stringify({ boundary: "public" }));
  });
  upstream.on("upgrade", (request, socket) => {
    upgrades.push(request.url);
    socket.end("HTTP/1.1 101 Switching Protocols\r\nConnection: Upgrade\r\nUpgrade: websocket\r\n\r\n");
  });
  upstream.listen(0, "127.0.0.1");
  await once(upstream, "listening");
  t.after(() => new Promise((resolve) => { upstream.closeAllConnections(); upstream.close(resolve); }));

  const previous = process.env.MNEMONIC_DEV_API_URL;
  process.env.MNEMONIC_DEV_API_URL = `http://127.0.0.1:${upstream.address().port}`;
  t.after(() => {
    if (previous === undefined) delete process.env.MNEMONIC_DEV_API_URL;
    else process.env.MNEMONIC_DEV_API_URL = previous;
  });
  const root = fileURLToPath(new URL("..", import.meta.url));
  const vite = await createServer({
    root, configFile: `${root}/vite.config.ts`, logLevel: "silent",
    server: { port: 0, watch: null }, optimizeDeps: { noDiscovery: true, include: [] }
  });
  t.after(() => vite.close());
  await vite.listen();
  const port = vite.httpServer.address().port;
  const base = `http://127.0.0.1:${port}`;

  for (const family of ["mnemonic", "artifacts", "transcripts", "backups"]) {
    const response = await fetch(`${base}/api/${family}/probe`, {
      headers: { Origin: base, "X-Example": "preserved" }
    });
    assert.equal(response.status, 200);
    assert.deepEqual(await response.json(), { boundary: "public" });
    const request = received.at(-1);
    assert.equal(request.headers.host, `127.0.0.1:${port}`);
    assert.equal(request.headers.origin, base);
    assert.equal(request.headers.authorization, undefined);
    assert.equal(request.headers["x-example"], "preserved");
  }
  for (const path of ["/api/v1/projects", "/api/unknown", "/api", "/worker/healthz", "/docs",
    "/openapi.json", "/assets/missing.js", "/fonts/missing.woff2", "/missing.svg", "/settings"]) {
    const response = await fetch(base + path, { headers: { Accept: "text/html" } });
    assert.equal(response.status, 404, path);
    assert.equal((await response.text()).includes('<div id="root">'), false, path);
  }
  assert.equal(received.length, 4);
  for (const path of ["/", "/work-items?work=test", "/settings/prompts/", "/@vite/client", "/main.tsx", "/icon.svg"]) {
    const response = await fetch(base + path);
    assert.equal(response.status, 200, path);
    await response.arrayBuffer();
  }

  async function upgrade(path) {
    const socket = connect(port, "127.0.0.1");
    socket.setTimeout(5000, () => socket.destroy(new Error("Upgrade did not complete")));
    await once(socket, "connect");
    const webSocketKey = randomBytes(16).toString("base64");
    socket.write(`GET ${path} HTTP/1.1\r\nHost: 127.0.0.1:${port}\r\nOrigin: ${base}\r\nConnection: Upgrade\r\nUpgrade: websocket\r\nSec-WebSocket-Key: ${webSocketKey}\r\nSec-WebSocket-Version: 13\r\n\r\n`);
    const [data] = await once(socket, "data");
    socket.destroy();
    return data.toString();
  }
  assert.match(await upgrade("/api/mnemonic/sync"), /^HTTP\/1\.1 101/);
  assert.match(await upgrade("/api/mnemonic/sync?probe=1"), /^HTTP\/1\.1 101/);
  for (const path of ["/api/artifacts/sync", "/api/mnemonic/other", "/api/mnemonic/sync/", "/api/v1/sync"]) {
    assert.match(await upgrade(path), /^HTTP\/1\.1 404/);
  }
  assert.deepEqual(upgrades, ["/api/mnemonic/sync", "/api/mnemonic/sync?probe=1"]);
});
