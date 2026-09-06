#!/usr/bin/env python3
"""Windowless, read-only Token Monitor maintenance observation. No model or app control."""

from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import urllib.error
import urllib.request

UPSTREAM = "Javis603/token-monitor"
FORK = "medking82/token-monitor"
SHA = re.compile(r"[0-9a-f]{40}")
MAX_BYTES = 1024 * 1024
PROVIDERS = ("claude", "codex", "antigravity")


class CheckError(Exception):
    pass


def utc_now():
    return datetime.now(timezone.utc)


def stamp(value):
    return value.isoformat().replace("+00:00", "Z")


def decode(raw):
    def unique_pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise CheckError("duplicate_json_key")
            result[key] = value
        return result

    try:
        return json.loads(raw, object_pairs_hook=unique_pairs)
    except (ValueError, UnicodeError) as exc:
        raise CheckError("invalid_json") from exc


def fetch_json(url):
    # Public endpoints only: no CLI auth, account files, credential helpers or model calls.
    request = urllib.request.Request(url, headers={
        "User-Agent": "Token-Monitor-Windows-Check/1", "Accept": "application/json",
        "Cache-Control": "no-cache",
    })
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            raw = response.read(MAX_BYTES + 1)
        if len(raw) > MAX_BYTES:
            raise CheckError("remote_response_too_large")
        return decode(raw)
    except urllib.error.HTTPError as exc:
        raise CheckError(f"remote_http_{exc.code}") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise CheckError("remote_unreachable") from exc


def exact_sha(value):
    if not isinstance(value, str) or SHA.fullmatch(value) is None:
        raise CheckError("invalid_commit_sha")
    return value


def check_upstream(fetch=fetch_json):
    heads = {}
    for key, repository in (("upstream", UPSTREAM), ("fork", FORK)):
        value = fetch(f"https://api.github.com/repos/{repository}/git/ref/heads/main")
        if not isinstance(value, dict) or not isinstance(value.get("object"), dict):
            raise CheckError("remote_ref_invalid")
        if value.get("ref") != "refs/heads/main" or value["object"].get("type") != "commit":
            raise CheckError("remote_ref_invalid")
        heads[key] = exact_sha(value["object"].get("sha"))
    manifest = fetch(f"https://raw.githubusercontent.com/{FORK}/{heads['fork']}/scripts/upstream-observation.json")
    if (not isinstance(manifest, dict)
            or type(manifest.get("schema_version")) is not int or manifest["schema_version"] != 1
            or manifest.get("upstream_repository") != f"https://github.com/{UPSTREAM}.git"
            or manifest.get("upstream_ref") != "main"
            or type(manifest.get("observation_contract_schema")) is not int
            or manifest["observation_contract_schema"] != 1
            or manifest.get("observation_files") != ["src/shared/quotaSnapshot.js", "src/shared/quotaSnapshotWriter.js"]):
        raise CheckError("published_manifest_invalid")
    base = exact_sha(manifest.get("upstream_base_commit"))
    exact_sha(manifest.get("observation_patch_commit"))
    return {"status": "current" if heads["upstream"] == base else "update_available",
            "upstream_sha": heads["upstream"], "fork_sha": heads["fork"], "upstream_base_commit": base}


def timestamp_age(value, now):
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            raise ValueError("timezone required")
        age = (now - parsed).total_seconds()
        if age < -60:
            raise ValueError("future timestamp")
        return max(0, round(age))
    except (ValueError, AttributeError, TypeError) as exc:
        raise CheckError("observation_timestamp_invalid") from exc


def check_observation(path, now):
    try:
        with path.open("rb") as handle:
            raw = handle.read(MAX_BYTES + 1)
    except FileNotFoundError:
        raise CheckError("observation_missing") from None
    if len(raw) > MAX_BYTES:
        raise CheckError("observation_too_large")
    value = decode(raw)
    if (not isinstance(value, dict) or value.get("kind") != "token-monitor-quota-snapshot"
            or type(value.get("schema_version")) is not int or value["schema_version"] != 1
            or not isinstance(value.get("providers"), list)):
        raise CheckError("observation_schema_invalid")
    age = timestamp_age(value.get("generated_at"), now)
    providers = {}
    for row in value["providers"]:
        if not isinstance(row, dict):
            raise CheckError("observation_provider_invalid")
        provider = row.get("provider")
        if provider not in PROVIDERS:
            continue
        if provider in providers:
            raise CheckError("observation_provider_duplicate")
        providers[provider] = row
    issues = ["export_stale"] if age > 1800 else []
    summary = {}
    for provider in PROVIDERS:
        row = providers.get(provider)
        if row is None:
            summary[provider] = "missing"
        elif row.get("status") != "ok":
            summary[provider] = "unavailable"
        else:
            provider_age = timestamp_age(row.get("observed_at"), now)
            summary[provider] = "ok" if provider_age <= 1800 else "stale"
        if summary[provider] != "ok":
            issues.append(f"{provider}_{summary[provider]}")
    # No account identifiers, credentials or raw quota data are copied to task logs.
    return {"status": "ok" if not issues else "attention", "generated_at": value["generated_at"],
            "age_seconds": age, "providers": summary, "issues": issues}


def collect(observation, now, fetch=fetch_json):
    result = {"schema_version": 1, "kind": "token-monitor-windows-check", "checked_at": stamp(now)}
    for name, operation in (("upstream", lambda: check_upstream(fetch)),
                            ("quota", lambda: check_observation(observation, now))):
        try:
            result[name] = operation()
        except (CheckError, OSError) as exc:
            result[name] = {"status": "blocked", "reason": str(exc) if isinstance(exc, CheckError) else "read_failed"}
    upstream = result["upstream"]
    result["pending_update"] = ({"upstream_sha": upstream["upstream_sha"],
                                  "published_fork_sha": upstream["fork_sha"],
                                  "next_action": "prepare_checks_review_then_install"}
                                 if upstream["status"] == "update_available" else None)
    result["status"] = ("blocked" if any(result[k]["status"] == "blocked" for k in ("upstream", "quota"))
                        else "attention" if result["pending_update"] or result["quota"]["status"] != "ok" else "current")
    return result


def issue_key(result):
    upstream, quota = result["upstream"], result["quota"]
    # Do not alert again just because age, check time or the fork's own commit changed.
    issues = {"upstream": {key: upstream[key] for key in ("status", "reason") if key in upstream},
              "quota": {key: quota[key] for key in ("status", "reason", "issues") if key in quota}}
    if upstream["status"] == "update_available":
        issues["upstream"]["sha"] = upstream["upstream_sha"]
    return hashlib.sha256(json.dumps(issues, sort_keys=True).encode()).hexdigest()


def atomic_json(path, value):
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)


@contextmanager
def exclusive_run(path):
    # Kernel-owned lock releases even if Task Scheduler terminates a run.
    with path.open("a+b") as handle:
        if handle.tell() == 0:
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield


def notify(status_path):
    powershell = Path(os.environ["SystemRoot"]) / "System32/WindowsPowerShell/v1.0/powershell.exe"
    try:
        result = subprocess.run([str(powershell), "-NoLogo", "-NoProfile", "-NonInteractive",
                                 "-WindowStyle", "Hidden", "-File",
                                 str(Path(__file__).with_name("notify-upstream-task.ps1")),
                                 "-StatusPath", str(status_path)],
                                capture_output=True, timeout=20,
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        return {0: "submitted", 3: "disabled_for_user", 4: "disabled_by_system"}.get(result.returncode, "failed")
    except (OSError, subprocess.TimeoutExpired):
        return "failed"


def run_once(state_dir, observation, *, now=None, fetch=fetch_json, notifier=notify):
    state_dir.mkdir(parents=True, exist_ok=True)
    with exclusive_run(state_dir / "check.lock"):
        status_path = state_dir / "status.json"
        try:
            previous = decode(status_path.read_bytes())
            if not isinstance(previous, dict):
                previous = {}
        except (OSError, CheckError):
            previous = {}
        result = collect(observation, now or utc_now(), fetch)
        result["issue_key"] = issue_key(result)
        changed = previous.get("issue_key") != result["issue_key"]
        result["notification"] = "quiet"
        atomic_json(status_path, result)
        if changed and result["status"] != "current":
            result["notification"] = notifier(status_path) if notifier else "disabled"
            atomic_json(status_path, result)
        # One bounded event per state transition, not one growing transcript per poll.
        if changed:
            events = state_dir / "events.jsonl"
            if events.exists() and events.stat().st_size > MAX_BYTES:
                events.replace(state_dir / "events.previous.jsonl")
            with events.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(result, sort_keys=True) + "\n")
        return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state-dir", type=Path, required=True)
    parser.add_argument("--observation", type=Path, required=True)
    parser.add_argument("--no-notify", action="store_true")
    args = parser.parse_args()
    try:
        result = run_once(args.state_dir, args.observation, notifier=None if args.no_notify else notify)
    except OSError:
        return 2
    if sys.stdout is not None:
        print(json.dumps(result, sort_keys=True))
    return 2 if result["status"] == "blocked" else 0


if __name__ == "__main__":
    raise SystemExit(main())
