#!/usr/bin/env python3
"""Prepare a reviewable Token Monitor update from the pinned upstream repository.

This helper only fetches and merges Git objects in the current disposable worktree. It never
reads credentials, runs package scripts, edits provider code, commits, pushes, or changes the
user's primary checkout. SOP remains responsible for deciding review, promotion, and release.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from typing import Any


UPSTREAM_REPOSITORY = "https://github.com/Javis603/token-monitor.git"
UPSTREAM_REF = "main"
MANIFEST_RELATIVE = Path("scripts/upstream-observation.json")
SHA = re.compile(r"^[0-9a-f]{40}$")


class SyncError(RuntimeError):
    pass


def run(repo: Path, argv: list[str]) -> tuple[int, str, str]:
    result = subprocess.run(
        ["git", *argv], cwd=repo, text=True, encoding="utf-8", errors="replace",
        capture_output=True, check=False, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    return result.returncode, result.stdout.strip(), result.stderr.strip()


def git(repo: Path, argv: list[str]) -> str:
    code, stdout, stderr = run(repo, argv)
    if code:
        raise SyncError(stderr or stdout or f"git command failed: {' '.join(argv)}")
    return stdout


def repo_root() -> Path:
    path = Path(git(Path.cwd(), ["rev-parse", "--show-toplevel"])).resolve()
    if path != Path.cwd().resolve():
        raise SyncError("run from the exact Token Monitor worktree root")
    return path


def load_manifest(repo: Path) -> dict[str, Any]:
    path = repo / MANIFEST_RELATIVE
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise SyncError(f"manifest is unreadable: {exc}") from exc
    required = {
        "schema_version", "upstream_repository", "upstream_ref", "upstream_base_commit",
        "observation_patch_commit", "observation_contract_schema", "observation_files",
    }
    if set(value) != required or value["schema_version"] != 1:
        raise SyncError("manifest schema is unsupported")
    if value["upstream_repository"] != UPSTREAM_REPOSITORY or value["upstream_ref"] != UPSTREAM_REF:
        raise SyncError("manifest upstream identity is not the fixed Token Monitor upstream")
    for key in ("upstream_base_commit", "observation_patch_commit"):
        if not isinstance(value[key], str) or SHA.fullmatch(value[key]) is None:
            raise SyncError(f"manifest {key} is not an exact commit SHA")
    if value["observation_contract_schema"] != 1 or not isinstance(value["observation_files"], list):
        raise SyncError("manifest observation contract is invalid")
    if value["observation_files"] != ["src/shared/quotaSnapshot.js", "src/shared/quotaSnapshotWriter.js"]:
        raise SyncError("manifest observation file allowlist changed")
    return value


def assert_clean(repo: Path) -> None:
    if git(repo, ["status", "--porcelain=v1", "--untracked-files=all"]):
        raise SyncError("worktree is dirty; prepare requires a clean isolated worktree")


def remote_for(args: argparse.Namespace) -> str:
    if args.test_mode:
        if not args.remote_url or not args.remote_url.startswith("file://"):
            raise SyncError("test mode requires a local file:// remote")
        return args.remote_url
    if args.remote_url:
        raise SyncError("remote override is test-only")
    return UPSTREAM_REPOSITORY


def resolve_remote(repo: Path, url: str) -> str:
    # Use a disposable remote name so the repository's configured remotes and credentials stay
    # untouched. Fetching is the only network operation this helper performs.
    return url


def remote_head(repo: Path, url: str) -> str:
    output = git(repo, ["ls-remote", url, f"refs/heads/{UPSTREAM_REF}"])
    fields = output.split()
    if len(fields) != 2 or fields[1] != f"refs/heads/{UPSTREAM_REF}" or SHA.fullmatch(fields[0]) is None:
        raise SyncError("upstream main ref is missing or malformed")
    return fields[0]


def compact(status: str, *, head: str, upstream: str, base: str, changed: list[str] | None = None,
            reason: str | None = None) -> dict[str, Any]:
    value: dict[str, Any] = {
        "schema_version": 1, "kind": "token-monitor-upstream-sync", "status": status,
        "repository": UPSTREAM_REPOSITORY, "ref": UPSTREAM_REF,
        "worktree_head": head, "fetched_sha": upstream, "upstream_base_commit": base,
    }
    if changed is not None:
        value["changed_paths"] = changed[:128]
    if reason:
        value["reason"] = reason
    return value


def prepare(repo: Path, manifest: dict[str, Any], url: str) -> dict[str, Any]:
    assert_clean(repo)
    head = git(repo, ["rev-parse", "HEAD"])
    fetched = remote_head(repo, url)
    base = manifest["upstream_base_commit"]
    if fetched == base:
        return compact("current", head=head, upstream=fetched, base=base, changed=[])

    # Fetch into FETCH_HEAD and merge only the exact SHA observed above. No upstream executable is
    # invoked. --no-commit leaves the candidate reviewable and keeps this helper out of release.
    git(repo, ["fetch", "--no-tags", url, f"refs/heads/{UPSTREAM_REF}"])
    fetched_again = git(repo, ["rev-parse", "FETCH_HEAD"])
    if fetched_again != fetched:
        raise SyncError("upstream ref changed during fetch; rerun check and prepare")
    code, stdout, stderr = run(repo, ["merge", "--no-commit", "--no-ff", fetched])
    if code:
        changed = [line for line in git(repo, ["diff", "--name-only"]).splitlines() if line]
        evidence = compact("conflict", head=head, upstream=fetched, base=base, changed=changed,
                           reason=stderr or stdout or "merge conflict")
        print(json.dumps(evidence, sort_keys=True), file=sys.stderr)
        raise SyncError("upstream merge conflict left in isolated worktree")

    # Record the new upstream base only after the exact merge succeeds. The candidate remains
    # uncommitted for review; the observation patch and provider login code are untouched.
    manifest["upstream_base_commit"] = fetched
    path = repo / MANIFEST_RELATIVE
    path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    changed = [line for line in git(repo, ["diff", "--name-only"]).splitlines() if line]
    return compact("prepared", head=head, upstream=fetched, base=base, changed=changed)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("check", "prepare"))
    parser.add_argument("--test-mode", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--remote-url", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    try:
        repo = repo_root()
        manifest = load_manifest(repo)
        url = remote_for(args)
        head = git(repo, ["rev-parse", "HEAD"])
        fetched = remote_head(repo, url)
        if args.action == "check":
            status = "current" if fetched == manifest["upstream_base_commit"] else "update_available"
            print(json.dumps(compact(status, head=head, upstream=fetched,
                                     base=manifest["upstream_base_commit"], changed=[]), sort_keys=True))
            return 0
        print(json.dumps(prepare(repo, manifest, url), sort_keys=True))
        return 0
    except (OSError, SyncError, ValueError) as exc:
        print(json.dumps({"schema_version": 1, "kind": "token-monitor-upstream-sync",
                          "status": "blocked", "reason": str(exc)}, sort_keys=True))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
