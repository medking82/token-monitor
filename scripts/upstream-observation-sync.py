#!/usr/bin/env python3
"""Prepare a reviewable Token Monitor update from the pinned upstream repository.

This helper only fetches and merges Git objects in the current disposable worktree. It never
reads credentials, runs package scripts, edits provider code, commits, pushes, or changes the
user's primary checkout. SOP remains responsible for deciding review, promotion, and release.
"""

from __future__ import annotations

import argparse
import atexit
import json
from pathlib import Path
import re
import subprocess
import sys
import tempfile
from typing import Any


UPSTREAM_REPOSITORY = "https://github.com/Javis603/token-monitor.git"
UPSTREAM_REF = "main"
MANIFEST_RELATIVE = Path("scripts/upstream-observation.json")
SHA = re.compile(r"^[0-9a-f]{40}$")
_INERT_HOOKS = Path(tempfile.mkdtemp(prefix="token-monitor-sync-hooks-"))
atexit.register(lambda: _INERT_HOOKS.rmdir())


class SyncError(RuntimeError):
    def __init__(self, reason: str, details: dict[str, Any] | None = None):
        super().__init__(reason)
        self.reason = reason
        self.details = details or {}


def run(repo: Path, argv: list[str], *, timeout: int = 60) -> tuple[int, str, str]:
    command = ["git", "-c", f"core.hooksPath={_INERT_HOOKS}", "-c", "core.fsmonitor=false",
               "-c", "submodule.recurse=false", *argv]
    try:
        result = subprocess.run(
            command, cwd=repo, text=True, encoding="utf-8", errors="replace",
            capture_output=True, check=False, timeout=timeout,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except subprocess.TimeoutExpired as exc:
        return 124, "", "timeout"
    return result.returncode, result.stdout.strip(), result.stderr.strip()


def git(repo: Path, argv: list[str]) -> str:
    code, stdout, stderr = run(repo, argv)
    if code:
        raise SyncError("git_command_failed")
    return stdout


def repo_root() -> Path:
    path = Path(git(Path.cwd(), ["rev-parse", "--show-toplevel"])).resolve()
    if path != Path.cwd().resolve():
        raise SyncError("run from the exact Token Monitor worktree root")
    return path


def linked_worktree_context(repo: Path) -> tuple[Path, str, str]:
    code, stdout, _ = run(repo, ["worktree", "list", "--porcelain"])
    if code:
        raise SyncError("worktree_list_failed")
    entries = [line.split(maxsplit=1)[1] for line in stdout.splitlines()
               if line.startswith("worktree ")]
    current = str(repo.resolve()).casefold()
    matched = [Path(path).resolve() for path in entries if str(Path(path).resolve()).casefold() == current]
    primary = Path(entries[0]).resolve() if entries else repo.resolve()
    if primary == repo.resolve():
        raise SyncError("primary_worktree_refused")
    if len(matched) != 1 or len(entries) < 2 or (repo / ".git").is_file() is False:
        raise SyncError("isolated_linked_worktree_required")
    return primary, git(primary, ["rev-parse", "HEAD"]), git(primary, ["status", "--porcelain=v1", "--untracked-files=all"])


def regular_owned_file(path: Path) -> None:
    if path.is_symlink() or not path.is_file():
        raise SyncError("owned_file_drifted")


def load_manifest(repo: Path) -> dict[str, Any]:
    path = repo / MANIFEST_RELATIVE
    regular_owned_file(path)
    try:
        def pairs(items):
            result = {}
            for key, value in items:
                if key in result:
                    raise SyncError("manifest_duplicate_key")
                result[key] = value
            return result
        value = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=pairs)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        if isinstance(exc, SyncError):
            raise
        raise SyncError("manifest_unreadable") from exc
    required = {
        "schema_version", "upstream_repository", "upstream_ref", "upstream_base_commit",
        "observation_patch_commit", "observation_contract_schema", "observation_files",
    }
    if (not isinstance(value, dict) or set(value) != required
            or type(value["schema_version"]) is not int or value["schema_version"] != 1):
        raise SyncError("manifest schema is unsupported")
    if value["upstream_repository"] != UPSTREAM_REPOSITORY or value["upstream_ref"] != UPSTREAM_REF:
        raise SyncError("manifest upstream identity is not the fixed Token Monitor upstream")
    for key in ("upstream_base_commit", "observation_patch_commit"):
        if not isinstance(value[key], str) or SHA.fullmatch(value[key]) is None:
            raise SyncError(f"manifest {key} is not an exact commit SHA")
    if (type(value["observation_contract_schema"]) is not int
            or value["observation_contract_schema"] != 1
            or not isinstance(value["observation_files"], list)):
        raise SyncError("manifest observation contract is invalid")
    if value["observation_files"] != ["src/shared/quotaSnapshot.js", "src/shared/quotaSnapshotWriter.js"]:
        raise SyncError("manifest observation file allowlist changed")
    for relative in value["observation_files"]:
        if not isinstance(relative, str) or relative.startswith("/") or ".." in Path(relative).parts:
            raise SyncError("observation_file_path_invalid")
        regular_owned_file(repo / relative)
    return value


def validate_commit_contract(repo: Path, manifest: dict[str, Any], head: str) -> None:
    for key in ("upstream_base_commit", "observation_patch_commit"):
        try:
            git(repo, ["cat-file", "-e", f"{manifest[key]}^{{commit}}"])
        except SyncError as exc:
            raise SyncError(f"{key}_missing") from exc
    if run(repo, ["merge-base", "--is-ancestor", manifest["upstream_base_commit"], head])[0] != 0:
        raise SyncError("upstream_base_not_ancestor")
    if run(repo, ["merge-base", "--is-ancestor", manifest["observation_patch_commit"], head])[0] != 0:
        raise SyncError("observation_patch_not_ancestor")


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


def remote_head(repo: Path, url: str) -> str:
    code, output, _ = run(repo, ["ls-remote", url, f"refs/heads/{UPSTREAM_REF}"], timeout=30)
    if code:
        raise SyncError("upstream_fetch_failed")
    fields = output.split()
    if len(fields) != 2 or fields[1] != f"refs/heads/{UPSTREAM_REF}" or SHA.fullmatch(fields[0]) is None:
        raise SyncError("upstream_ref_invalid")
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
        value["changed_paths_total"] = len(changed)
        value["changed_paths_truncated"] = len(changed) > 128
    if reason:
        value["reason"] = reason
    return value


def prepare(repo: Path, manifest: dict[str, Any], url: str, fetched: str,
            primary: tuple[Path, str, str]) -> dict[str, Any]:
    assert_clean(repo)
    head = git(repo, ["rev-parse", "HEAD"])
    base = manifest["upstream_base_commit"]
    if fetched == base:
        return compact("current", head=head, upstream=fetched, base=base, changed=[])

    # Fetch into FETCH_HEAD and merge only the exact SHA observed above. No upstream executable is
    # invoked. --no-commit leaves the candidate reviewable and keeps this helper out of release.
    code, _, _ = run(repo, ["fetch", "--no-tags", url, f"refs/heads/{UPSTREAM_REF}"], timeout=120)
    if code:
        raise SyncError("upstream_fetch_failed")
    fetched_again = git(repo, ["rev-parse", "FETCH_HEAD"])
    if fetched_again != fetched:
        raise SyncError("upstream_ref_changed_during_fetch")
    if run(repo, ["merge-base", "--is-ancestor", base, fetched])[0] != 0:
        raise SyncError("upstream_rewind_or_divergence")
    if run(repo, ["cat-file", "-e", f"{fetched}:{MANIFEST_RELATIVE.as_posix()}"])[0] == 0:
        raise SyncError("upstream_owned_manifest_collision")
    code, _, _ = run(repo, ["merge", "--no-commit", "--no-ff", fetched])
    if code:
        changed = [line for line in git(repo, ["diff", "HEAD", "--name-only"]).splitlines() if line]
        raise SyncError("upstream_merge_conflict", {
            "worktree_head": head, "fetched_sha": fetched, "upstream_base_commit": base,
            "changed_paths": changed[:128], "changed_paths_total": len(changed),
            "changed_paths_truncated": len(changed) > 128,
        })

    # Record the new upstream base only after the exact merge succeeds. The candidate remains
    # uncommitted for review; the observation patch and provider login code are untouched.
    manifest["upstream_base_commit"] = fetched
    path = repo / MANIFEST_RELATIVE
    regular_owned_file(path)
    path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    changed = [line for line in git(repo, ["diff", "HEAD", "--name-only"]).splitlines() if line]
    if not (repo / MANIFEST_RELATIVE).is_file() or (repo / MANIFEST_RELATIVE).is_symlink():
        raise SyncError("manifest_write_refused")
    primary_path, primary_head, primary_status = primary
    if git(primary_path, ["rev-parse", "HEAD"]) != primary_head or git(primary_path, ["status", "--porcelain=v1", "--untracked-files=all"]) != primary_status:
        raise SyncError("primary_worktree_changed")
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
        primary = linked_worktree_context(repo)
        validate_commit_contract(repo, manifest, head)
        fetched = remote_head(repo, url)
        if args.action == "check":
            status = "current" if fetched == manifest["upstream_base_commit"] else "update_available"
            print(json.dumps(compact(status, head=head, upstream=fetched,
                                     base=manifest["upstream_base_commit"], changed=[]), sort_keys=True))
            return 0
        print(json.dumps(prepare(repo, manifest, url, fetched, primary), sort_keys=True))
        return 0
    except (OSError, SyncError, ValueError) as exc:
        details = exc.details if isinstance(exc, SyncError) else {}
        value = {"schema_version": 1, "kind": "token-monitor-upstream-sync",
                 "status": "blocked", "reason": exc.reason if isinstance(exc, SyncError) else "input_invalid"}
        value.update(details)
        print(json.dumps(value, sort_keys=True))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
