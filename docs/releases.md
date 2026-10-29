# Releases and image distribution

Mnemonic publishes stable release bundles and container images so an installation
needs Docker Compose and a POSIX shell, without host Python, a source checkout, or
a local image build.
The server and Claude plugin retain separate version numbers. Every release ships
an exact compatibility table; use the plugin catalog from the same release as the
server.

## Version and channel policy

Follow the repository's existing Semantic Versioning policy: increment the minor
version for user-facing changes and the patch version for other changes. Major
version changes require explicit owner approval. A patch number does not imply
that an upgrade needs no backup or has no configuration changes. Read that
release's upgrade notes before every upgrade.

Stable releases are reviewed weekly when changes are ready, with urgent patches
as needed. Maintainers create the release tags; the weekly workflow prepares a
candidate bundle without editing `main` or creating tags. All source changes,
including version bumps, reach `main` through a pull request and its required
checks.

Images are published under `ghcr.io/rightbrain-networks/mnemonic-api`,
`mnemonic-mcp`, `mnemonic-web`, and `mnemonic-tika` in the same namespace. API,
worker, storage initialization, and migration services share the API image.

- `X.Y.Z` identifies an exact stable application release. Publication refuses to
  replace an existing version tag with a different image digest.
- `X.Y` and `latest` move when a newer stable release passes its release gates.
  Use an exact version for installations and upgrades.
- `edge` and `sha-<12-character-commit>` follow successfully tested main commits.
  Edge is a development channel and has no stable release guarantee.
- `sha-<full-commit>-<workflow-run>-<attempt>` identifies a tested publication
  candidate. Release promotion uses its recorded manifest digests.

The owner's production channel and deployment remain separate operator decisions.
Publishing a release does not deploy or restart production.

## Prepare a release

From a clean topic worktree based on current remote `main`:

```sh
python3 scripts/release.py bump X.Y.Z --plugin A.B.C
python3 scripts/release.py check
```

Omit `--plugin` when its version is unchanged. The tool updates coordinated
version locations and regenerates lockfiles and OpenAPI using the repository's
normal generators. Document compatibility and configuration changes in the pull
request. Run the applicable checks, merge through GitHub, and wait for the exact
merged commit's `Required checks` to pass.

A maintainer then creates and pushes `vX.Y.Z` at that merged commit. The release
workflow verifies the tag/version match, main ancestry, successful required CI,
and that the version is newer than every published stable release. Maintenance
backports with an older version require a separate release-policy change; this
workflow never moves `latest` backwards.

The first publication also requires the owner to make all four container packages
public and linked to this repository. The OCI source labels identify the source
repository. Package write permissions and private vulnerability reporting are
repository/organization settings; a workflow cannot assume they are configured.
The tag release's anonymous-install test fails if an image cannot be pulled
without registry authentication. Verify that private vulnerability reporting remains enabled before announcing
the release, as described in [SECURITY.md](../SECURITY.md).

## Automated release gates

Every pull request builds all four images on native amd64 and arm64 runners,
loads the results locally, scans for fixable critical vulnerabilities, and starts
an isolated installation from the bundle. These jobs are dependencies of
`Required checks`. Pull request jobs have read-only repository permissions and
never log into the registry or publish images.

A successful main build can publish the edge channel. Publishing jobs alone
receive package, identity-token, and attestation write permissions. They push
native image digests with BuildKit provenance and software bills of materials,
then merge the two architectures after their scans and local installation tests.
The merged manifests receive GitHub build-provenance attestations, which the
workflow verifies. Scan reports are retained as SARIF artifacts and publishing
runs also upload them to code scanning. Fixable critical findings stop the job;
there is no release bypass in these workflows.

A version tag builds publication candidates, creates a draft GitHub Release, and
runs an anonymous installation test. When a previous stable release exists, it
also installs that release, seeds disposable work and artifact data, upgrades to
the candidate, verifies that data, and runs the project-activity and code-review
integrity audits. Only after these tests pass does it promote the recorded image
digests to stable tags and publish the draft. A failed run leaves the draft
unpublished. The first public release explicitly reports that no previous public
image release exists to test; a fresh installation test is not presented as
upgrade evidence.

If a release run fails after creating its candidate receipt or promoting some exact
version tags, rerun only the failed job or jobs. That retry reuses the original receipt
and tested image digests, including the original build attempt. Rerunning every job
can rebuild different bytes; exact version tags deliberately refuse replacement by
different digests, even when the source commit is unchanged.

Nightly rehearsals run fresh-install and previous-release upgrade checks on both
architectures plus browser acceptance tests. Every stack uses an isolated Compose
project and disposable storage; no rehearsal targets production.

## Release contents

`scripts/release_bundle.py --output dist --previous-tag vPREVIOUS` generates:

- `mnemonic-X.Y.Z.tar.gz`: Compose configuration and installation overlays,
  `.env.example`, a root `setup.py`, required deployment files, diagnostic scripts,
  the license, and the complete Markdown operations guides, including historical
  upgrade prerequisites. It also includes `up_mnemonic.sh`, client configuration
  examples, and the complete plugin resources plus `scripts/export_agent_skills.py`
  for portable Codex/OpenCode skills. Executable helpers retain their executable
  permissions. Contributor build overlays and application source trees are excluded.
- `UPGRADE.md`: before/after database schema heads, migrations added, changed
  configuration keys, plugin version, reconnect guidance, and rollback limits.
- `COMPATIBILITY.md`: the server/plugin version pair and source commit.
- `marketplace.json`: a Claude plugin catalog pinned to the release tag and full
  source commit. The repository's `.claude-plugin/marketplace.json` is the
  contributor catalog for a local checkout.
- `SHA256SUMS`: checksums for the release assets. The archive also contains a
  checksum manifest for its own payload.

The workflow adds `images.json` containing the exact tested multi-architecture
manifest digests and combines the generated upgrade notes with GitHub's
label-based pull request notes. Release tooling generates these artifacts without
changing repository files. Release history is available through
[CHANGELOG.md](../CHANGELOG.md) and [GitHub Releases](https://github.com/RightBrain-Networks/mnemonic/releases).

## Install and check a chosen release

Choose an exact stable version from Releases and download its archive and
`SHA256SUMS`. Verify the downloaded files with `sha256sum --check --ignore-missing
--strict SHA256SUMS`, extract the archive, and enter its `mnemonic-X.Y.Z` directory.
With Docker, Compose v2, and a POSIX shell, replace `X.Y.Z` below with that same release:

```sh
docker run --rm --pull always --user "$(id -u):$(id -g)" \
  --mount "type=bind,source=$(pwd),target=/release" --entrypoint python \
  ghcr.io/rightbrain-networks/mnemonic-api:X.Y.Z /release/setup.py
docker compose pull
docker compose up -d --wait
```

Setup runs in the selected API image before Compose reads the required secrets.
The host user owns the resulting regular `.env` file with mode `0600`; no host
Python or sudo is needed. Do not run the installer through Compose before `.env`
exists, because Compose must resolve those secrets first. Host Python 3.10 or newer
can optionally run `python3 setup.py` instead of the initial Docker command.
Existing `.env` files are left unchanged.

To configure transcript sources during containerized setup, append the setup options
after `/release/setup.py` and add a read-only bind for every approved source at its
original absolute path, such as
`--mount type=bind,source=/absolute/source,target=/absolute/source,readonly` before the
image name. This preserves the same source-path, ownership, and permission checks as
host Python setup. See [transcript setup](transcripts.md#filesystem-deployment).

For an existing installation, follow [operations.md](operations.md) before
starting the new version. Preserve its database, `.env`, and data stores; existing
bind-mounted installs need `compose.bind-mounts.yaml`. Named volumes do not import
existing host directories automatically.

Run the read-only stack check through the installed MCP container, which contains
its Python dependencies:

```sh
docker compose exec -T mcp python - \
  --api-url http://api:8000 \
  --mcp-url http://mcp:8001/mcp \
  --web-url http://web:3000 --web-origin http://localhost:3000 < scripts/check-stack.py
```

If transcript capture is configured, run its read-only diagnostics in both services:

```sh
docker compose exec -T api python -m mnemonic_api.transcript_doctor
docker compose exec -T worker python -m mnemonic_api.transcript_doctor --worker
```

Compare the reported UID, GID and approved roots; API and worker must agree. With
host Python available, `python3 scripts/check_transcript_access.py` runs both checks
and compares those fields automatically.

Set `--web-origin` to the actual configured dashboard origin when its port changes or
TLS is enabled; it must match `MNEMONIC_DASHBOARD_ORIGINS`. The checker connects through
internal Docker DNS while retaining that public Host/Origin identity, and still rejects
untrusted hosts and origins. The MCP container receives its API key from Compose.
Do not add `--project-id` to
an ordinary installation check; that option explicitly enables destructive test
work intended only for a disposable project.

Install the Claude plugin using the catalog belonging to the server release. From the
extracted, checksum-verified release directory, copy its catalog into a persistent
marketplace directory:

```sh
mnemonic_catalog_dir="$HOME/.local/share/mnemonic/marketplace"
mkdir -p "$mnemonic_catalog_dir/.claude-plugin"
cp marketplace.json "$mnemonic_catalog_dir/.claude-plugin/marketplace.json"
claude plugin marketplace add "$mnemonic_catalog_dir"
claude plugin install mnemonic@mnemonic
```

Keep that directory for future refreshes. Claude Code 2.1.303 treats a GitHub release
download URL passed directly to `marketplace add` as a Git repository and fails to
clone it. The local directory works and preserves the catalog's release tag and full
commit pin. The `.claude-plugin/marketplace.json` layout is required for subsequent
plugin discovery and refreshes.

On a server upgrade, replace the catalog in that same directory with `marketplace.json`
from the chosen verified release, run `claude plugin marketplace update mnemonic` and
`claude plugin update mnemonic@mnemonic`, then restart Claude Code. The `releases/latest`
page can help discover releases; following a moving latest catalog can select a plugin
that does not match a pinned server.

For Codex or OpenCode, export the complete portable skills directly from the extracted
bundle with `python3 scripts/export_agent_skills.py /absolute/path/to/new-skill-export`.
Follow [the client setup guide](mcp-clients.md#install-the-complete-workflow) to install
the exported folders and the matching configuration examples.

Published assets and images also carry GitHub provenance. For example, with the
GitHub CLI, verify the downloaded archive or an image digest:

```sh
gh attestation verify mnemonic-X.Y.Z.tar.gz --repo RightBrain-Networks/mnemonic
gh attestation verify oci://ghcr.io/rightbrain-networks/mnemonic-api@sha256:DIGEST \
  --repo RightBrain-Networks/mnemonic
```

Use the real digest from that release's `images.json`. Database downgrades are not
a supported rollback mechanism: restore the pre-migration database backup and
matching filesystem backups with the previous release images. Filesystem backups
remain necessary for artifacts, editable prompts, and retained native transcripts.
