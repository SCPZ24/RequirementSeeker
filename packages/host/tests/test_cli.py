"""仅独立进程、固定合成输入；安装环境由调用方指定。"""

import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HOST, BUNDLE = map(Path, sys.argv[1:3])
del sys.argv[1:3]


class CliTests(unittest.TestCase):
    def invoke(self, *args):
        return subprocess.run(
            [
                sys.executable,
                "-I",
                "-B",
                "-X",
                "utf8",
                str(HOST),
                "--bundle",
                str(BUNDLE),
                *map(str, args),
            ],
            capture_output=True,
            cwd=HOST.parent,
        )

    def test_cases_and_independent_read(self):
        expected = {
            "new": ("committed", "new_candidate"),
            "append": ("committed", "merge_evidence"),
            "zero": ("no_opportunity", "rejected"),
            "incomplete": ("incomplete", None),
            "unknown": ("incomplete", None),
            "fatal": ("incomplete", "fatal_error"),
            "retryable": ("incomplete", "retryable_error"),
            "cancelled": ("incomplete", "cancelled"),
            "budget": ("incomplete", "budget_exhausted"),
        }
        for case, (status, outcome) in expected.items():
            with self.subTest(case=case), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp) / "runs"
                p = self.invoke("run", "--case", case, "--output", root)
                self.assertIn(p.returncode, (0, 1), p.stderr)
                directories = list(root.iterdir())
                self.assertEqual(len(directories), 1)
                read = self.invoke("read", directories[0])
                self.assertEqual(read.returncode, 0, read.stderr)
                row = json.loads(read.stdout)
                self.assertEqual((row["status"], row["outcome"]), (status, outcome))

    def test_bounded_retry_and_links(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "runs"
            p = self.invoke("run", "--case", "retryable", "--max-retries", "2", "--output", root)
            self.assertEqual(p.returncode, 1, p.stderr)
            rows = [json.loads((p / "attempt.json").read_bytes()) for p in sorted(root.iterdir())]
            self.assertEqual(len(rows), 3)
            self.assertIsNone(rows[0]["retry_of_run_id"])
            self.assertEqual(
                [r["retry_of_run_id"] for r in rows[1:]], [r["run_id"] for r in rows[:-1]]
            )

    def test_nonretryable_stops(self):
        for case in ("fatal", "unknown", "incomplete", "budget", "zero"):
            with self.subTest(case=case), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp) / "runs"
                self.invoke("run", "--case", case, "--max-retries", "2", "--output", root)
                self.assertEqual(len(list(root.iterdir())), 1)

    def test_replay_and_preserve(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "runs"
            self.assertEqual(self.invoke("run", "--case", "replay", "--output", root).returncode, 0)
            rows = [json.loads(self.invoke("read", p).stdout) for p in sorted(root.iterdir())]
            self.assertEqual([r["status"] for r in rows], ["committed", "already_committed"])
            self.assertEqual(rows[0]["payload"], rows[1]["payload"])
            before = {
                str(p): hashlib.sha256(p.read_bytes()).hexdigest()
                for p in root.rglob("*")
                if p.is_file()
            }
            self.assertNotEqual(self.invoke("run", "--case", "new", "--output", root).returncode, 0)
            self.assertEqual(
                before,
                {
                    str(p): hashlib.sha256(p.read_bytes()).hexdigest()
                    for p in root.rglob("*")
                    if p.is_file()
                },
            )

    def test_retry_limit_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "runs"
            self.assertEqual(
                self.invoke(
                    "run", "--case", "new", "--max-retries", "3", "--output", root
                ).returncode,
                2,
            )
            self.assertFalse(root.exists())

    def test_partial_and_tampered_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "runs"
            self.invoke("run", "--case", "new", "--output", root)
            run = next(root.iterdir())
            original = (run / "receipt.json").read_bytes()
            (run / "receipt.json").unlink()
            partial = self.invoke("read", run)
            self.assertNotEqual(partial.returncode, 0)
            self.assertIn(b"incomplete", partial.stdout)
            (run / "receipt.json").write_bytes(original)
            (run / "results.json").write_text("[]")
            self.assertNotEqual(self.invoke("read", run).returncode, 0)


if __name__ == "__main__":
    unittest.main()
