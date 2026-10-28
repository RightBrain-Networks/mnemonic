# Changelog

Versioned release notes and upgrade instructions are published with each
[GitHub Release](https://github.com/RightBrain-Networks/mnemonic/releases).

`scripts/release_bundle.py` generates each release's migration list, configuration
key changes, server/plugin compatibility table, and upgrade notes. GitHub adds
categorized pull request notes using `.github/release.yml`. Release jobs do not
edit the source tree or commit directly to `main`.

Before the first published release, installation was available from source only.
