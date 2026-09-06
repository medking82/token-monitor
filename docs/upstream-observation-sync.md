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

Create the linked worktree from the published observation branch before maintenance, keeping the
primary checkout as the protected keeper:

```text
git fetch origin codex/token-monitor-quota-snapshot
git worktree add -b codex/token-monitor-upstream-sync <isolated-path> origin/codex/token-monitor-quota-snapshot
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

For a prepared change, run the ordinary upstream checks (`npm run verify`, plus the focused quota
snapshot tests), inspect the exact diff, and complete one Native Review round when risk admission
requires it. Then use a normal non-force push to the maintenance branch and promote through the
repository's manual policy. A no-change check exits without inference, package install, build, or
release activity. A conflict or failed check stops, preserves the isolated evidence, leaves the
currently installed app untouched, and reports the condition for operator action.

The local per-user Windows promotion path is an NSIS/local build only. An unsigned local artifact
must never enter the signed release feed. Preserve autostart, native provider state, userData,
and the existing `verifyUpdateCodeSignature` setting; an explicitly approved official update may
replace the export package through the normal signed channel.

## Windowless Windows checks

The workstation's periodic check belongs to Windows Task Scheduler, not a Codex heartbeat.
`scripts/install-upstream-check-task.ps1` installs **Token Monitor Upstream Check** for the current
user at logon and every six hours. Preview first, then pass `-Execute`; use `-Uninstall -Execute`
to remove only that owned task. The action uses the actual `pythonw.exe` interpreter, and its
optional notification child uses `CREATE_NO_WINDOW`. No console or Electron process is started.
The task runs with Limited privileges while the user is logged on, ignores overlapping runs,
has no automatic failure retry, and has a five-minute execution limit.

The installer copies the two runtime files into
`%USERPROFILE%\.token-monitor-maintenance\runtime`; candidate worktree edits cannot silently
change a registered task. Re-run the installer to deploy a script update. The registration XML,
the previous registration/runtime when replaced, and local status remain outside Git.
Keep this directory outside LocalAppData: packaged Codex can redirect new LocalAppData writes
into its private LocalCache, which a normal Task Scheduler action cannot see at the original path.

`check-upstream-task.py` reads the public upstream and published fork `main` refs, then reads the
fork's observation manifest pinned to that exact fork SHA. It needs no Git checkout update,
GitHub login or provider credentials. It reads the running app's existing quota export directly
and checks its timestamps and the three configured provider statuses. This is advisory health
observation, not reviewer quota/account admission. No raw account identity or quota is copied
into the task status. A 30-minute freshness limit detects a stopped exporter without restarting it.

Every run writes a fresh `status.json` and rechecks current conditions; a historical permission
failure never becomes a permanent blocker. State transitions append a bounded `events.jsonl`.
Unchanged healthy checks are silent. A new upstream SHA or changed failure condition submits
one silent Windows notification and records it; repeated ages/timestamps do not generate alerts.
Windows notification settings may suppress display, so the JSON status remains authoritative.
New upstream is recorded as `pending_update`, for the existing prepare/check/review/promotion
workflow above. The scheduled action does not perform that update or launch a Codex task.

The checker never closes, hides, restarts, or installs Token Monitor. The currently open widget,
native login, userData, autostart, primary checkout, and signed update settings are keepers.
For a later authorized installation, report the required brief interruption, stop the app only
after a verified artifact and backup are ready, and restore the running window afterward.
Polling, quota reads, no-change checks, and old failures must never trigger installation.

Validate this boundary with `python -m unittest discover -s tests -p test_upstream_check_task.py`,
PowerShell parser checks for the two scripts, and one real scheduled run. Verify the task result,
fresh status, and unchanged Token Monitor process identity before removing the replaced Codex
heartbeat. No app rebuild is needed for a checker-only change.
