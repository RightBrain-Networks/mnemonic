# Mnemonic behind nginx TLS

[`mnemonic.conf`](mnemonic.conf) follows the supplied host configuration:
host-managed nginx, the existing wildcard certificate, a LAN allowlist, and
separate logs. It is self-contained and does not include the host's snippets or
modify its virtual hosts. There is one production instance and no default-server
claim, development instance, or legacy hostname redirects.

The assumed address is **https://mnemonic.example.com**. nginx must run
on the same host as Mnemonic's Docker stack, because the upstreams bind to
loopback. This file is not installed by a container build or Compose startup.

| Public path | Destination |
| --- | --- |
| `/` and `/api/mnemonic/*` | Next.js at `127.0.0.1:3000` |
| `/api/mnemonic/sync` upgrade | Next.js WebSocket relay to the API |
| `/mcp` (no trailing slash) | MCP at `127.0.0.1:8001/mcp` |
| `/mcp/` and descendants | Rejected with 404 |
| HTTP port 80 | Redirects to the fixed HTTPS hostname, preserving path and query |

FastAPI and PostgreSQL are not routed publicly. In particular, do not send
`/api/mnemonic/*` to port 8000: that path belongs to the dashboard's server proxy.

The WebSocket carries data-free invalidation notices; browsers refetch changed
records through the authenticated dashboard API proxy. nginx forwards the
`Upgrade` and `Connection` headers, and the API checks the browser's exact
origin before accepting the subscription.

## Trust and prerequisites

The default allowlist trusts loopback and `192.168.0.0/16`. **Everyone on an
allowed network can read, edit, and delete all prompts in the dashboard.** There
is no application login. Tighten that subnet to your actual trusted clients, or
enable the dashboard's optional Basic authentication below. MCP always requires
the existing bearer API key, in addition to the network allowlist.

The example's custom Docker pool, `198.51.100.0/24`, is commented out deliberately:
it is public address space, not an RFC 1918 private subnet. Uncomment it only if
that pool really belongs to local, trusted containers on your host. Prefer a
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

Keep the existing secrets in `.env`; do not replace them. To use another
hostname, set `MNEMONIC_TLS_HOST` to its bare hostname in `.env` and update the
literal hostname and certificate paths in `mnemonic.conf` as well. nginx does
not substitute Compose environment variables. If you changed published web/MCP
ports, update both `proxy_pass` addresses. When changing the local web port,
keep `MNEMONIC_DASHBOARD_ORIGINS` consistent with it as described in
[`docs/operations.md`](../../docs/operations.md).

Install the config and prepare its log directory. These commands match the
Debian/Ubuntu account names in the example; adjust ownership for your host.
Preserve any existing `mnemonic.conf` before replacing it.

```sh
sudo install -d -o www-data -g adm -m 0750 /var/log/nginx/mnemonic
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
Uncomment both `auth_basic` lines **inside `location /`** in the repository
config, install it again, validate, and reload. Do not enable Basic auth at
server level: MCP clients need their Authorization header for bearer auth.
This password protects the dashboard and its `/api/mnemonic` routes together.
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
The proxy does not cache responses; it preserves Next.js's own browser cache
headers and does not buffer MCP, Next.js streaming responses, or upgraded
WebSocket traffic.

Directive behavior was checked against nginx's official
[proxy documentation](https://nginx.org/en/docs/http/ngx_http_proxy_module.html),
[geo documentation](https://nginx.org/en/docs/http/ngx_http_geo_module.html), and
[HTTPS configuration guide](https://nginx.org/en/docs/http/configuring_https_servers.html).
