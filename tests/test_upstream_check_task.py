"""Deterministic tests for the windowless upstream observation task."""

import importlib.util
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
import tempfile
import unittest


SCRIPT = Path(__file__).parents[1] / "scripts" / "check-upstream-task.py"
SPEC = importlib.util.spec_from_file_location("check_upstream_task", SCRIPT)
TASK = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(TASK)


def sha(ch):
    return ch * 40


class FakeFetch:
    def __init__(self, upstream=None, fork=None, manifest=None, error=None):
        self.upstream = upstream or sha("a")
        self.fork = fork or sha("f")
        self.manifest = manifest or {
            "schema_version": 1,
            "upstream_repository": "https://github.com/Javis603/token-monitor.git",
            "upstream_ref": "main",
            "upstream_base_commit": self.upstream,
            "observation_patch_commit": sha("d"),
            "observation_contract_schema": 1,
            "observation_files": [
                "src/shared/quotaSnapshot.js",
                "src/shared/quotaSnapshotWriter.js",
            ],
        }
        self.error = error
        self.calls = []

    def __call__(self, url):
        self.calls.append(url)
        if self.error:
            raise self.error
        if url.endswith("Javis603/token-monitor/git/ref/heads/main"):
            return {"ref": "refs/heads/main", "object": {"type": "commit", "sha": self.upstream}}
        if url.endswith("medking82/token-monitor/git/ref/heads/main"):
            return {"ref": "refs/heads/main", "object": {"type": "commit", "sha": self.fork}}
        return self.manifest


def snapshot(now, *, age=0, missing=(), stale=()):
    generated = now - timedelta(seconds=age)
    providers = []
    for provider in TASK.PROVIDERS:
        if provider in missing:
            continue
        observed = generated - timedelta(seconds=age if provider in stale else 0)
        providers.append({"provider": provider, "status": "ok", "observed_at": observed.isoformat().replace("+00:00", "Z")})
    return {
        "kind": "token-monitor-quota-snapshot",
        "schema_version": 1,
        "generated_at": generated.isoformat().replace("+00:00", "Z"),
        "providers": providers,
    }


class UpstreamCheckTaskTests(unittest.TestCase):
    def test_public_ref_and_pinned_manifest_comparison(self):
        fetch = FakeFetch(upstream=sha("b"), fork=sha("c"))
        fetch.manifest["upstream_base_commit"] = sha("a")
        result = TASK.check_upstream(fetch)
        self.assertEqual(result["status"], "update_available")
        self.assertEqual(result["upstream_sha"], sha("b"))
        self.assertEqual(result["fork_sha"], sha("c"))
        self.assertEqual(result["upstream_base_commit"], sha("a"))
        self.assertEqual(fetch.calls, [
            "https://api.github.com/repos/Javis603/token-monitor/git/ref/heads/main",
            "https://api.github.com/repos/medking82/token-monitor/git/ref/heads/main",
            f"https://raw.githubusercontent.com/medking82/token-monitor/{sha('c')}/scripts/upstream-observation.json",
        ])

    def test_invalid_published_manifest_is_rejected(self):
        for field, value in (
            ("upstream_repository", "https://github.com/other/token-monitor.git"),
            ("schema_version", True),
            ("observation_contract_schema", 2),
            ("observation_patch_commit", "not-a-sha"),
        ):
            with self.subTest(field=field):
                fetch = FakeFetch()
                fetch.manifest[field] = value
                with self.assertRaises(TASK.CheckError):
                    TASK.check_upstream(fetch)

    def test_unchanged_polls_are_quiet_and_emit_no_duplicate_event(self):
        now = datetime(2026, 9, 6, tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory) / "state"
            observation = Path(directory) / "quota.json"
            observation.write_text(json.dumps(snapshot(now)), encoding="utf-8")
            notifications = []
            fetch = FakeFetch()
            first = TASK.run_once(state, observation, now=now, fetch=fetch, notifier=notifications.append)
            second = TASK.run_once(state, observation, now=now + timedelta(minutes=1), fetch=fetch, notifier=notifications.append)
            self.assertEqual(first["notification"], "quiet")
            self.assertEqual(second["notification"], "quiet")
            self.assertEqual(len(notifications), 0)
            self.assertEqual(len((state / "events.jsonl").read_text(encoding="utf-8").splitlines()), 1)

    def test_update_alert_is_once_even_when_fork_and_age_change(self):
        now = datetime(2026, 9, 6, tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory) / "state"
            observation = Path(directory) / "quota.json"
            observation.write_text(json.dumps(snapshot(now)), encoding="utf-8")
            notifications = []
            fetch = FakeFetch(upstream=sha("b"), fork=sha("c"))
            fetch.manifest["upstream_base_commit"] = sha("a")
            TASK.run_once(state, observation, now=now, fetch=fetch, notifier=notifications.append)
            fetch.fork = sha("d")
            observation.write_text(json.dumps(snapshot(now, age=60)), encoding="utf-8")
            result = TASK.run_once(state, observation, now=now + timedelta(seconds=60), fetch=fetch, notifier=notifications.append)
            self.assertEqual(result["notification"], "quiet")
            self.assertEqual(len(notifications), 1)

    def test_network_error_recovers_with_fresh_data(self):
        now = datetime(2026, 9, 6, tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory) / "state"
            observation = Path(directory) / "quota.json"
            observation.write_text(json.dumps(snapshot(now)), encoding="utf-8")
            failed = FakeFetch(error=TASK.CheckError("remote_unreachable"))
            first = TASK.run_once(state, observation, now=now, fetch=failed, notifier=None)
            self.assertEqual(first["status"], "blocked")
            notices = []
            repeated = TASK.run_once(state, observation, now=now + timedelta(seconds=1), fetch=failed, notifier=notices.append)
            self.assertEqual(repeated["status"], "blocked")
            self.assertEqual(repeated["notification"], "quiet")
            self.assertEqual(notices, [])
            recovered = TASK.run_once(state, observation, now=now + timedelta(seconds=2), fetch=FakeFetch(), notifier=notices.append)
            self.assertEqual(recovered["upstream"]["status"], "current")
            self.assertEqual(recovered["status"], "current")

    def test_missing_export_file_and_invalid_observation_timestamps(self):
        now = datetime(2026, 9, 6, tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "quota.json"
            with self.assertRaisesRegex(TASK.CheckError, "observation_missing"):
                TASK.check_observation(path, now)
            for value in ("bad", now.replace(tzinfo=None).isoformat(),
                          (now + timedelta(seconds=61)).isoformat().replace("+00:00", "Z")):
                with self.subTest(timestamp=value):
                    data = snapshot(now)
                    data["generated_at"] = value
                    path.write_text(json.dumps(data), encoding="utf-8")
                    with self.assertRaisesRegex(TASK.CheckError, "observation_timestamp_invalid"):
                        TASK.check_observation(path, now)

    def test_quota_missing_stale_malformed_and_timestamp_errors_are_redacted(self):
        now = datetime(2026, 9, 6, tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "quota.json"
            path.write_text(json.dumps(snapshot(now, missing=("claude",))), encoding="utf-8")
            result = TASK.check_observation(path, now)
            self.assertIn("claude_missing", result["issues"])
            path.write_text(json.dumps(snapshot(now, age=1801, stale=("codex",))), encoding="utf-8")
            result = TASK.check_observation(path, now)
            self.assertEqual(result["status"], "attention")
            self.assertIn("export_stale", result["issues"])
            self.assertIn("codex_stale", result["issues"])
            path.write_text("{\"generated_at\":\"bad\"}", encoding="utf-8")
            with self.assertRaises(TASK.CheckError):
                TASK.check_observation(path, now)
            data = snapshot(now)
            data["providers"][0]["account_key"] = "secret"
            data["providers"][0]["credentials"] = "secret"
            path.write_text(json.dumps({**data, "account_key": "secret", "credentials": "secret"}), encoding="utf-8")
            result = TASK.collect(path, now, fetch=FakeFetch())
            self.assertNotIn("secret", json.dumps(result))
            self.assertNotIn("account_key", json.dumps(result))

    def test_overlapping_lock_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            lock = state / "check.lock"
            with TASK.exclusive_run(lock):
                with self.assertRaises(OSError):
                    with TASK.exclusive_run(lock):
                        pass


if __name__ == "__main__":
    unittest.main()
