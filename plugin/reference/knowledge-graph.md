# Decisions and local provenance

Read `help(topic="decision_get usage")`, `help(topic="decision_put usage")`, or
`help(topic="decision_impact usage")` for the relevant decision workflow.
For closeout declarations, read `help(topic="complete_work job_completion_report")`.

The optional [provenance helper](${CLAUDE_PLUGIN_ROOT}/scripts/decision_provenance.py)
checks local commits and prepares declarations without network access or writes:

```sh
python3 /resolved/decision_provenance.py --directory /selected/repository \
  --repository github.com/team/repository --decision ADR-00194-0 --commit HEAD
```

Resolve the helper from this installed bundle. Repeat `--decision` and `--commit`
only when every listed decision informed every listed commit; otherwise prepare
separate declarations. Use `--help` for reconciliation inputs. The helper's output
is evidence to inspect, not a submission or proof that a decision governed work.
