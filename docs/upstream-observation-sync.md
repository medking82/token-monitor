# Upstream observation sync

`scripts/upstream-observation-sync.py` is a preparation boundary for the local quota observation
patch. It follows `https://github.com/Javis603/token-monitor.git` at `main` and records the exact
upstream base in `scripts/upstream-observation.json`. The observation contract remains schema 1;
the patch commit and its two owned files are recorded there so a provider login change is never
silently bundled into the observer.

Run from a clean linked worktree created from the latest observation patch:

```text
python scripts/upstream-observation-sync.py check
python scripts/upstream-observation-sync.py prepare
```

`check` performs one bounded ref lookup and reports `current` or `update_available`. `prepare`
fetches the frozen SHA, verifies that the recorded base is an ancestor, and merges it with
`--no-commit --no-ff` so the candidate remains reviewable. Git hooks, fsmonitor, and recursive
submodule behavior are disabled per command; no user configuration is changed. The primary
worktree keeper is fingerprinted before mutation and must remain unchanged. Dirty worktrees,
missing or rewound refs, manifest drift, failed fetches, and conflicts fail closed; a conflict
leaves the isolated worktree for inspection and reports bounded changed paths.

The helper does not read credentials, invoke upstream or package scripts, commit, push, install,
select a model, or release. After preparation, run the Token Monitor checks required by its
`AGENTS.md`, classify the frozen diff, and use the existing Native Review owner when the
classifier returns `risk=high` or an explicit review request admits it. Native evidence never
authorizes promotion. Publication, app promotion, installation, and live quota verification are
separate SOP actions; failures remain durable and are not retried automatically.
