# Local repository check helper

Read `help(topic="recall_work usage")` for interpreting saved context. This
optional helper compares an inspected Git baseline and declared paths locally;
it does not establish correctness or grant permission to act.

Resolve the [helper](${CLAUDE_PLUGIN_ROOT}/bin/mnemonic-repository-freshness)
against this installed bundle, then invoke its quoted absolute path while the
working directory is the selected repository:

```sh
/resolved/mnemonic-repository-freshness --baseline FULL_COMMIT --path 'backend/**'
```

Repeat `--path` for additional declared dependencies. Use the actual inspected
baseline and scope rather than deriving them from a summary or search hit.
The helper reports `unchanged`, `changed`, or `indeterminate` with bounded
metadata. It does not fetch, modify Git state, or inspect undeclared files.
Retain unknown coverage as unknown. Project owners choose when repository
freshness checks belong in their workflow.
