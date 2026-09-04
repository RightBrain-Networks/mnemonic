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
| `*.svg` | Read from disk, falling back to Next.js |
| `/` and `/api/mnemonic/*` | Next.js at `127.0.0.1:3000` |
| `/api/mnemonic/sync` upgrade | Next.js WebSocket relay to the API |
| `/mcp` (no trailing slash) | MCP at `127.0.0.1:8001/mcp` |
| `/mcp/` and descendants | Rejected with 404 |
| HTTP port 80 | Redirects to the fixed HTTPS hostname, preserving path and query |

FastAPI and PostgreSQL are not routed publicly. In particular, do not send
`/api/mnemonic/*` to port 8000: that path belongs to the dashboard's server proxy.

SVG requests are answered from `frontend/public` in the checkout, cached for a
week, and never reach Next.js, which labels that directory `max-age=0` and so
makes every page view revalidate artwork that changes only on redeploy. The
`root` in `mnemonic.conf` must name the checkout that built the web image --
`frontend/Dockerfile` copies the same directory into the container -- because
nginx now answers from the tree on disk rather than from the image. An SVG that
is not there falls through to the dashboard, which is how `/icon.svg` keeps
working: Next.js generates it from `app/icon.svg`, outside `public/`. The
location repeats the security headers `next.config.ts` sets, plus HSTS, since
none of those apply to a response nginx serves itself.

The WebSocket carries data-free invalidation notices; browsers refetch changed
records through the authenticated dashboard API proxy. nginx forwards the
`Upgrade` and `Connection` headers, and the API checks the browser's exact
origin before accepting the subscription.

Phase 11 evidence history is deliberately identity-coded. The dashboard API
location uses the checked snippet to request `identity` from Next.js, disable
nginx gzip for that location, emit
`Cache-Control: no-store, max-age=0, no-transform`, and set
`X-DNS-Prefetch-Control: off`. The Next.js proxy and browser still reject an
unexpected or malformed `Content-Encoding` before reading its body. The
Next-owned nonempty `Content-Encoding: identity` marker also prevents the
supported google/ngx_brotli filter from recoding the response. Do not enable
an untested response filter that can recode an already-coded response for
`/api/mnemonic/`; merely viewing evidence must not create a decompression or
speculative external-contact path.

## Trust and prerequisites

The default allowlist trusts loopback and `192.168.0.0/16`. **Everyone on an
allowed network can read, edit, and delete all prompts in the dashboard.** There
is no application login. Tighten that subnet to your actual trusted clients, or
enable the dashboard's optional Basic authentication below. MCP always requires
the existing bearer API key, in addition to the network allowlist.

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

Run from the Mnemonic checkout on that host. Enable the companion Compose file
first; it adds the HTTPS hostname/origin to both applications while retaining
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
Uncomment both `auth_basic` lines in **both** `location /` and
`location ^~ /api/mnemonic/` in the repository config, install it again,
validate, and reload. Do not enable Basic auth at server level: MCP clients need
their Authorization header for bearer auth. Enabling only one location leaves
the other dashboard surface outside that optional password boundary.
It does not turn Mnemonic into a multi-user application or enable public cloud
MCP/OAuth integrations. Keep the network ACL even with a dashboard password.

## Check the deployment

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
still fail content-free at the Next.js proxy, while a controlled response with
no upstream coding emerges as explicit `identity`, proving that Next owns the
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
The proxy stores no responses of its own. It preserves Next.js's browser cache
headers, apart from the SVG location above, which sets its own, and does not
buffer MCP, Next.js streaming responses, or upgraded WebSocket traffic.

Directive behavior was checked against nginx's official
[proxy documentation](https://nginx.org/en/docs/http/ngx_http_proxy_module.html),
[geo documentation](https://nginx.org/en/docs/http/ngx_http_geo_module.html), and
[HTTPS configuration guide](https://nginx.org/en/docs/http/configuring_https_servers.html).
