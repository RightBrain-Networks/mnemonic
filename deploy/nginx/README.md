# Mnemonic behind nginx TLS

[`mnemonic.conf`](mnemonic.conf) and its checked
[`mnemonic-dashboard-api-policy.conf`](snippets/mnemonic-dashboard-api-policy.conf)
snippet follow the supplied host configuration:
host-managed nginx, the existing wildcard certificate, a LAN allowlist, and
separate logs. They do not include any host-owned snippet or modify other
virtual hosts. There is one production instance and no default-server claim,
development instance, or legacy hostname redirects.

The assumed address is **https://mnemonic.example.com**. nginx must run
on the same host as Mnemonic's Docker stack, because the upstreams bind to
loopback. This file is not installed by a container build or Compose startup.

| Public path | Destination |
| --- | --- |
| `/`, client routes, artwork and `/assets/*` | Static web image at `127.0.0.1:3000` |
| `/api/mnemonic`, `/api/artifacts`, `/api/transcripts`, `/api/backups` and descendants | Static web ingress forwards unchanged to FastAPI |
| `/api/mnemonic/sync` upgrade | Static web WebSocket relay to FastAPI |
| `/api/v1`, `/worker`, `/projects`, docs and unknown API paths | Rejected with 404 |
| `/mcp` (no trailing slash) | MCP at `127.0.0.1:8001/mcp` |
| `/mcp/` and descendants | Rejected with 404 |
| HTTP port 80 | Redirects to the fixed HTTPS hostname, preserving path and query |

The API port and PostgreSQL remain private. The web image forwards only the four
browser namespaces, preserving the external Host and Origin. It contains no API
key, backup token, Node server, or worker connection. Runtime display settings
come from `/api/mnemonic/runtime-config`; changing them requires no asset rebuild.

All static files, including SVG artwork and `/icon.svg`, come from the same web
image as `index.html`. The host proxy no longer reads a checkout directory.
Vite's fingerprinted `/assets/*` files are immutable for one year; HTML and public
unfingerprinted files revalidate. Asset and API misses never receive SPA HTML.
The explicit `/healthz` endpoint reports static-server health.

The WebSocket carries data-free invalidation notices; browsers refetch changed
records through the same-origin browser API boundary. nginx forwards the
`Upgrade` and `Connection` headers, and the API checks the browser's exact
origin before accepting the subscription.

Phase 11 evidence history is deliberately identity-coded. The dashboard API
location uses the checked snippet to request `identity` from FastAPI, disable
nginx gzip for that location, emit
`Cache-Control: no-store, max-age=0, no-transform`, and set
`X-DNS-Prefetch-Control: off`. The FastAPI boundary and browser still reject an
unexpected or malformed `Content-Encoding` before reading its body. The
API-owned nonempty `Content-Encoding: identity` marker also prevents the
supported google/ngx_brotli filter from recoding the response. Do not enable
an untested response filter that can recode an already-coded response for
`/api/mnemonic/`; merely viewing evidence must not create a decompression or
speculative external-contact path.

## Trust and prerequisites

The default allowlist trusts loopback and `192.168.0.0/16`. **Everyone on an
allowed network can read, edit, and delete all prompts in the dashboard.** There
is no application login. Tighten that subnet to your actual trusted clients, or
enable the dashboard's optional Basic authentication below. MCP always requires
the existing bearer API key for RPCs, in addition to the network allowlist.
Local artifact transfers can use an exact-operation upload grant issued through
that authenticated connection; it grants no general RPC or REST access.

The example's custom Docker pool, `198.51.100.0/24`, is a documentation
placeholder and is commented out deliberately: it sits outside the RFC 1918
private ranges, and such addresses may be routable public space. Uncomment it
only if that pool really belongs to local, trusted containers on your host. Prefer a
private subnet for new Docker networks; do not broaden the ACL to all addresses
just to make a remote client connect. IPv6 LAN access is not enabled by default.

This assumes nginx is the first/only edge proxy. Do not trust arbitrary
`X-Forwarded-For` values or put another proxy in front without reviewing client
IP handling: a proxy that makes all clients appear to be loopback would defeat
the network restriction. Keep all Compose port bindings on `127.0.0.1`.

Before installation, arrange DNS for the hostname and confirm these files
exist on the nginx host, with the private key readable only by the appropriate
nginx process/operator:

```text
/etc/nginx/certs/star.example.com.chained.crt
/etc/nginx/certs/star.example.com.key
```

The certificate must cover `mnemonic.example.com` and be trusted by your
browsers and MCP clients. No certificate or private key is stored in this repo.
The file expects the normal `/etc/nginx/conf.d/*.conf` include **inside `http`**.
Do not separately copy its `geo` or `log_format` into nginx.conf, which would
define those names twice. HTTP/2 is optional and commented for compatibility.

## Install on the nginx/Docker host

Production service replacement and host nginx reload require explicit operator
authorization. Validate the isolated stack before scheduling that interruption.
Run from the Mnemonic checkout on that host. Enable the companion Compose file
first; it adds the HTTPS hostname/origin to the API and MCP while retaining
loopback access and container healthchecks:

```sh
docker compose -f compose.yaml -f compose.tls.yaml config --quiet
docker compose -f compose.yaml -f compose.tls.yaml up -d --build --wait
```

Use both files for future Compose updates/restarts so the TLS allowlists are
retained. For example:

```sh
docker compose -f compose.yaml -f compose.tls.yaml ps
docker compose -f compose.yaml -f compose.tls.yaml logs --tail=50 web mcp
```

Keep the existing secrets in `.env`; do not replace them. Set
`MNEMONIC_TLS_HOST` in `.env` to the bare hostname you are serving. Unless that
hostname is literally `mnemonic.example.com`, leaving it unset is not a working
default: `compose.tls.yaml` falls back to the placeholder, so the allowlists
never learn your hostname and the dashboard answers 403 to its own
`/api/mnemonic/*` requests while the page itself still loads. Update the literal
hostname and certificate paths in `mnemonic.conf` to match. nginx does not
substitute Compose environment variables. If you changed published web/MCP
ports, update both `proxy_pass` addresses. When changing the local web port,
keep `MNEMONIC_DASHBOARD_ORIGINS` consistent with it as described in
[`docs/operations.md`](../../docs/operations.md).

Install the config, its required Phase 11 snippet, and prepare the log
directory. These commands match the Debian/Ubuntu account names in the example;
adjust ownership for your host. Preserve any existing files before replacing
them.

```sh
sudo install -d -o www-data -g adm -m 0750 /var/log/nginx/mnemonic
sudo install -d -o root -g root -m 0755 /etc/nginx/snippets
sudo install -m 0644 deploy/nginx/snippets/mnemonic-dashboard-api-policy.conf /etc/nginx/snippets/mnemonic-dashboard-api-policy.conf
sudo install -m 0644 deploy/nginx/mnemonic.conf /etc/nginx/conf.d/mnemonic.conf
sudo nginx -t && sudo systemctl reload nginx
```

nginx syntax validation must succeed before reload. Keep the prior config
available to restore if validation fails. Nothing here modifies the running
nginx config or its shared snippets.

## Optional dashboard password

If your allowed LAN includes other people or untrusted devices, create a
password file using `htpasswd` (provided by Apache's utility package on many
Linux systems). It prompts for a password without putting it in shell history:

```sh
sudo htpasswd -c /etc/nginx/mnemonic.htpasswd YOUR_USERNAME
sudo chown root:www-data /etc/nginx/mnemonic.htpasswd
sudo chmod 0640 /etc/nginx/mnemonic.htpasswd
```

Use `-c` only when creating a new file; it overwrites an existing password file.
Uncomment both `auth_basic` lines in every dashboard location: `location /`,
the combined `/api/mnemonic` and `/api/transcripts` location,
and the `/api/artifacts` and `/api/backups` locations in the repository config, install it again,
validate, and reload. Do not enable Basic auth at server level: MCP clients need
their Authorization header for bearer auth. Missing a dashboard location leaves
that surface outside the optional password boundary.
It does not turn Mnemonic into a multi-user application or enable public cloud
MCP/OAuth integrations. Keep the network ACL even with a dashboard password.

## Check the deployment

Run `./scripts/test-nginx-e2e.sh` for the deployed transport matrix. Every run
now sends requests through the actual static web image and the FastAPI browser
boundary, including raw transfers, backup restore and WebSocket upgrade.
`--browser-transports` remains an alias for earlier automation. The shipped
[`frontend/nginx.conf`](../../frontend/nginx.conf) owns the internal routing;
there is no separate test-only ingress substitute. See
[the backup gateway handoff](../../docs/browser-backup-api.md) for worker
credentials, 30-minute transfer deadlines and raw-byte rules. The host backup
location's body limit must allow the configured `MNEMONIC_BACKUP_MAX_BYTES`, and
its send/read timeouts must exceed 1800 seconds.

A production switch or rollback requires its own operator authorization. Keep
the prior Next/API image pair and host nginx configuration available until the
static deployment passes checks; restore that pair together for rollback.
No schema change is required by the static cutover.

The repository configuration passed `nginx -t` on nginx 1.30.4 and HTTPS checks
against temporary copies of the real dashboard and MCP containers. Checks
covered dashboard/API routing, MCP initialize/list-tools/list-projects, bearer
and origin rejection, source-IP restrictions, body limits, and log privacy.
Those checks used a temporary certificate and did not change stored prompts
or the running stack. They do not verify your host's DNS, certificate files,
existing nginx configuration, or firewall; perform the checks below after
installation.

Phase 11 also ships `./scripts/test-nginx-e2e.sh`. It combines
`compose.e2e.yaml` with `compose.nginx-e2e.yaml`, includes the exact
production dashboard-API snippet in two executable checks. Stock nginx first
syntax-checks the snippet without an optional Brotli module. The disposable
edge then loads the ABI-matched Alpine google/ngx_brotli package, enables it at
`http` scope, proves a JSON control response is actually `br`, and proves the
same inherited filter leaves evidence responses at exactly one application-
owned `Content-Encoding: identity` marker. Encoded success and error bodies
still fail content-free at the FastAPI boundary, while a controlled response with
no upstream coding emerges as explicit `identity`, proving that the API owns the
outer marker. A literal `brotli off` is intentionally absent because stock
nginx rejects that unknown directive when the optional module is not loaded.
This is a specific ngx_brotli proof, not a claim about arbitrary response
filters. The runner also sends valid completion JSON at exactly 1 MiB and at
byte 1 MiB + 1, both
with an ordinary Content-Length and with chunked transfer coding. A misleading
over-limit Content-Length on an exact-limit body is rejected before the
controlled upstream can parse it. Run the harness before installing a changed
policy.

From an allowed machine, open **https://mnemonic.example.com** and verify
project selection, editing, and copying a prompt. HTTPS enables the browser's
clipboard support on the LAN hostname. Dashboard requests must retain their
real `Origin` and `Sec-Fetch-Site` headers; do not rewrite them to silence a 403.

```sh
curl -I http://mnemonic.example.com/
curl -I https://mnemonic.example.com/
curl -i https://mnemonic.example.com/mcp
```

Expect an HTTP 308 redirect, then a successful dashboard response (or 401 when
Basic auth is enabled). The final request has no bearer token and must return
401. Do not disable certificate verification to make these checks pass.

For the existing Claude Code/OpenCode configuration, replace the local MCP URL
with **https://mnemonic.example.com/mcp**, keep bearer authentication, and
verify `list_projects`. Configure clients with HTTPS directly; an HTTP redirect
cannot protect a key already sent in an unencrypted request. See
[`docs/agents.md`](../../docs/agents.md) for client configuration.

Check that a client outside the allowlist cannot connect, even with a valid API
key. A 444 closes the connection without an HTTP response. A 502 usually means
the loopback upstream is down or its port differs; a dashboard 403 or MCP 421
usually means the TLS Compose file was omitted or the hostname does not match.

## Logs and reference

Logs live under `/var/log/nginx/mnemonic/`, separated into `web`, `mcp`, and
`redirect` access/error pairs. Add that nested directory to the host's logrotate
configuration: a default `/var/log/nginx/*.log` glob does not include it. Use
your existing nginx rotation/reopen procedure, with private permissions and a
bounded retention period (for example, the example host's 30 days).

Access logs exclude query strings and Referer to avoid retaining search text.
Error logs can still include request URLs, so they also need private storage.
The proxy stores no responses of its own. It preserves the static web image's
browser cache headers and does not buffer MCP, browser API streaming responses,
or upgraded WebSocket traffic.

Directive behavior was checked against nginx's official
[proxy documentation](https://nginx.org/en/docs/http/ngx_http_proxy_module.html),
[geo documentation](https://nginx.org/en/docs/http/ngx_http_geo_module.html), and
[HTTPS configuration guide](https://nginx.org/en/docs/http/configuring_https_servers.html).

## Local artifact upload grants

The TLS Compose overlay sets `MNEMONIC_MCP_PUBLIC_URL` from the configured
`MNEMONIC_TLS_HOST` and this proxy's exact `/mcp` route. An explicit
`MNEMONIC_MCP_PUBLIC_URL` overrides that default for a custom endpoint.
Refresh the installed nginx configuration when upgrading to 0.52.0: this route
allows up to 1 GiB of granted raw bytes with request buffering disabled and a
310-second response wait. Ordinary MCP JSON requests still meet the adapter's
90-MiB protocol bound. Existing header buffers accommodate the signed intent.
The gateway validates each grant before receiving bytes and forwards only its
verified immutable intent. See the [upload workflow](../../docs/artifact-upload-client.md).
