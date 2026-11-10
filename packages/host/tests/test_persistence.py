"""B34独立进程事务与恢复验证；只使用固定合成输入。"""

import json
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

HOST, BUNDLE = map(Path, sys.argv[1:3])
del sys.argv[1:3]


class PersistenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.db = self.root / "state.sqlite"

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
                "--database",
                str(self.db),
                *map(str, args),
            ],
            capture_output=True,
        )

    def run_case(self, name, case="new", *extra):
        return self.invoke("run", "--case", case, "--output", self.root / name, *extra)

    def records(self, table):
        with closing(sqlite3.connect(self.db)) as db:
            return db.execute("SELECT * FROM " + table).fetchall()

    def test_restart_replay_preserves_payload_and_manual_fields(self):
        for case in ("new", "append"):
            with self.subTest(case=case):
                self.db = self.root / (case + ".sqlite")
                first = self.run_case(case + "1", case)
                self.assertEqual(first.returncode, 0, first.stderr)
                original = json.loads(first.stdout.splitlines()[-1])
                with closing(sqlite3.connect(self.db)) as db, db:
                    saved = json.loads(db.execute("SELECT data FROM history").fetchone()[0])
                    saved["manual_note"] = "人工后续修改，重放不得覆盖"
                    db.execute("UPDATE history SET data=?", (json.dumps(saved),))
                history = self.records("history")
                second = self.run_case(case + "2", case)
                self.assertEqual(second.returncode, 0, second.stderr)
                replay = json.loads(second.stdout.splitlines()[-1])
                self.assertEqual(original["host_status"], "committed")
                self.assertEqual(replay["host_status"], "already_committed")
                self.assertEqual(replay["original_receipt"], original)
                self.assertEqual(replay["payload"], original["payload"])
                self.assertNotEqual(replay["run_id"], original["run_id"])
                self.assertEqual(len(self.records("intents")), 1)
                self.assertEqual(self.records("history"), history)

    def test_unknown_and_incomplete_do_not_write_business_data(self):
        for case in ("unknown", "incomplete"):
            p = self.run_case(case, case, "--max-retries", "2")
            self.assertEqual(p.returncode, 1, p.stderr)
            row = json.loads(p.stdout.splitlines()[-1])
            self.assertEqual(row["host_status"], "not_committed")
            self.assertIsNone(row["payload"])
        self.assertEqual(len(self.records("intents")), 0)
        self.assertEqual(len(self.records("runs")), 2)

    def test_transaction_rollback_preserves_existing_data(self):
        failed = self.run_case("empty-failure", "new", "--fault", "transaction")
        self.assertEqual(failed.returncode, 1)
        self.assertEqual(self.records("intents"), [])
        self.assertEqual(self.run_case("first").returncode, 0)
        before = self.records("intents")
        p = self.run_case("broken", "append", "--fault", "transaction")
        self.assertEqual(p.returncode, 1)
        self.assertEqual(self.records("intents"), before)
        row = json.loads(p.stdout.splitlines()[-1])
        self.assertEqual(row["host_status"], "commit_failed")
        self.assertEqual(len(self.records("runs")), 3)

    def test_corrupt_persisted_payload_rejected(self):
        self.assertEqual(self.run_case("first").returncode, 0)
        with closing(sqlite3.connect(self.db)) as db, db:
            db.execute("UPDATE intents SET payload='{}'")
        result = self.run_case("corrupt-replay")
        self.assertEqual(result.returncode, 1)
        self.assertEqual(len(self.records("runs")), 1)

    def test_postcommit_failures_recover_and_replay(self):
        for fault in ("file", "stdout"):
            with self.subTest(fault=fault):
                self.db = self.root / (fault + ".sqlite")
                p = self.run_case(fault, "new", "--fault", fault)
                self.assertEqual(p.returncode, 1)
                run_id = next((self.root / fault).iterdir()).name
                read = self.invoke("read", run_id)
                self.assertEqual(read.returncode, 0, read.stderr)
                row = json.loads(read.stdout)
                self.assertEqual(row["host_status"], "committed")
                replay = self.run_case(fault + "replay")
                self.assertEqual(replay.returncode, 0)
                self.assertEqual(
                    json.loads(replay.stdout.splitlines()[-1])["original_receipt"], row
                )
                self.assertEqual(len(self.records("intents")), 1)

    def test_retry_chain_and_old_output_protection(self):
        p = self.run_case("retry", "retryable", "--max-retries", "2")
        self.assertEqual(p.returncode, 1)
        rows = [json.loads(line) for line in p.stdout.splitlines()]
        self.assertEqual(len(rows), 3)
        self.assertEqual([r["retry_of_run_id"] for r in rows[1:]], [r["run_id"] for r in rows[:-1]])
        before = self.records("runs")
        self.assertEqual(self.run_case("retry").returncode, 1)
        self.assertEqual(self.records("runs"), before)


if __name__ == "__main__":
    unittest.main()
