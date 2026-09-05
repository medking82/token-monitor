import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "upstream-observation-sync.py"


def git(cwd, *args):
    return subprocess.check_output(["git", *args], cwd=cwd, text=True).strip()


def run_sync(cwd, action, remote):
    return subprocess.run(
        ["python", str(SCRIPT), action, "--test-mode", "--remote-url", remote],
        cwd=cwd, text=True, encoding="utf-8", capture_output=True,
    )


class UpstreamObservationSyncTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="token-monitor-upstream-sync-")
        root = Path(self.temp.name)
        self.remote_path = root / "remote.git"
        subprocess.run(["git", "init", "--bare", str(self.remote_path)], check=True,
                       capture_output=True, text=True)
        seed = root / "seed"
        subprocess.run(["git", "init", "-b", "main", str(seed)], check=True,
                       capture_output=True, text=True)
        self.seed = seed
        self.git_config(seed)
        (seed / "README.md").write_text("upstream\n", encoding="utf-8")
        self.commit(seed, "upstream base")
        subprocess.run(["git", "remote", "add", "origin", str(self.remote_path)], cwd=seed,
                       check=True, capture_output=True, text=True)
        subprocess.run(["git", "push", "origin", "main"], cwd=seed, check=True,
                       capture_output=True, text=True)
        self.base = git(seed, "rev-parse", "HEAD")
        self.remote = self.remote_path.as_uri()

    @staticmethod
    def git_config(path):
        for key, value in (("user.email", "test@example.invalid"), ("user.name", "Test")):
            subprocess.run(["git", "config", key, value], cwd=path, check=True,
                           capture_output=True, text=True)

    @staticmethod
    def commit(path, message):
        subprocess.run(["git", "add", "-A"], cwd=path, check=True, capture_output=True, text=True)
        subprocess.run(["git", "commit", "-m", message], cwd=path, check=True,
                       capture_output=True, text=True)

    def candidate(self):
        candidate = Path(self.temp.name) / ("candidate-" + str(len(list(Path(self.temp.name).iterdir()))))
        # Avoid relying on the candidate's configured remote: the helper uses the test URL only.
        subprocess.run(["git", "clone", "--branch", "main", str(self.remote_path), str(candidate)],
                       check=True, capture_output=True, text=True)
        self.git_config(candidate)
        (candidate / "src" / "shared").mkdir(parents=True)
        (candidate / "src" / "shared" / "quotaSnapshot.js").write_text("observation patch\n", encoding="utf-8")
        (candidate / "src" / "shared" / "quotaSnapshotWriter.js").write_text("writer\n", encoding="utf-8")
        (candidate / "scripts").mkdir()
        manifest = {
            "schema_version": 1,
            "upstream_repository": "https://github.com/Javis603/token-monitor.git",
            "upstream_ref": "main",
            "upstream_base_commit": self.base,
            "observation_patch_commit": "0" * 40,
            "observation_contract_schema": 1,
            "observation_files": ["src/shared/quotaSnapshot.js", "src/shared/quotaSnapshotWriter.js"],
        }
        (candidate / "scripts" / "upstream-observation.json").write_text(
            json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
        )
        self.commit(candidate, "observation patch")
        return candidate

    def test_no_change_is_current(self):
        candidate = self.candidate()
        result = run_sync(candidate, "check", self.remote)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["status"], "current")
        prepared = run_sync(candidate, "prepare", self.remote)
        self.assertEqual(prepared.returncode, 0, prepared.stderr)
        self.assertEqual(json.loads(prepared.stdout)["status"], "current")

    def test_prepare_updates_base_and_preserves_observation(self):
        candidate = self.candidate()
        (self.seed / "README.md").write_text("upstream update\n", encoding="utf-8")
        self.commit(self.seed, "upstream update")
        subprocess.run(["git", "push", "origin", "main"], cwd=self.seed, check=True,
                       capture_output=True, text=True)
        result = run_sync(candidate, "prepare", self.remote)
        self.assertEqual(result.returncode, 0, result.stderr)
        evidence = json.loads(result.stdout)
        self.assertEqual(evidence["status"], "prepared")
        self.assertIn("scripts/upstream-observation.json", evidence["changed_paths"])
        self.assertEqual((candidate / "src/shared/quotaSnapshot.js").read_text(), "observation patch\n")
        manifest = json.loads((candidate / "scripts/upstream-observation.json").read_text())
        self.assertEqual(manifest["upstream_base_commit"], git(self.seed, "rev-parse", "HEAD"))

    def test_dirty_prepare_is_blocked(self):
        candidate = self.candidate()
        (candidate / "local-note.txt").write_text("user work\n", encoding="utf-8")
        result = run_sync(candidate, "prepare", self.remote)
        self.assertEqual(result.returncode, 2)
        self.assertEqual(json.loads(result.stdout)["status"], "blocked")
        self.assertIn("dirty", json.loads(result.stdout)["reason"])

    def test_unknown_remote_is_blocked(self):
        candidate = self.candidate()
        result = run_sync(candidate, "check", (Path(self.temp.name) / "missing.git").as_uri())
        self.assertEqual(result.returncode, 2)
        self.assertEqual(json.loads(result.stdout)["status"], "blocked")

    def tearDown(self):
        self.temp.cleanup()


if __name__ == "__main__":
    unittest.main()
