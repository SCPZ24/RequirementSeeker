"""B35固定合成机会应用，独立进程与真实SQLite事务。"""

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


class OpportunityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.db = self.root / "state.sqlite"

    def invoke(self, *args, entry=None):
        return subprocess.run(
            [
                sys.executable,
                "-I",
                "-B",
                "-X",
                "utf8",
                str(entry or HOST),
                "--bundle",
                str(BUNDLE),
                "--database",
                str(self.db),
                *map(str, args),
            ],
            capture_output=True,
        )

    def seed(self, case="new"):
        self.assertEqual(self.invoke("init").returncode, 0)
        result = self.invoke(
            "run",
            "--case",
            case,
            "--output",
            self.root / case,
            entry=HOST.with_name("persistent.py"),
        )
        self.assertEqual(result.returncode, 1 if case == "unknown" else 0, result.stderr)
        return json.loads(result.stdout)["run_id"]

    def rows(self, table):
        with closing(sqlite3.connect(self.db)) as db:
            return db.execute("SELECT * FROM " + table).fetchall()

    def test_new_apply_replay_and_queries(self):
        run_id = self.seed()
        first = self.invoke("apply", run_id)
        self.assertEqual(first.returncode, 0, first.stdout)
        receipt = json.loads(first.stdout)
        self.assertEqual(receipt["status"], "applied")
        replay = self.invoke("apply", run_id)
        self.assertEqual(replay.returncode, 0)
        self.assertEqual(json.loads(replay.stdout)["original_receipt"], receipt)
        self.assertEqual(len(self.rows("opportunities")), 2)
        self.assertEqual(len(self.rows("opportunity_sources")), 1)
        self.assertEqual(len(self.rows("applications")), 1)
        restarted = self.invoke(
            "run",
            "--case",
            "new",
            "--output",
            self.root / "restart",
            entry=HOST.with_name("persistent.py"),
        )
        self.assertEqual(restarted.returncode, 0)
        new_run_id = json.loads(restarted.stdout)["run_id"]
        self.assertNotEqual(new_run_id, run_id)
        self.assertEqual(
            json.loads(self.invoke("apply", new_run_id).stdout)["original_receipt"], receipt
        )
        self.assertEqual(len(self.rows("applications")), 1)
        detail = self.invoke("detail", receipt["opportunity_id"])
        self.assertEqual(detail.returncode, 0)
        self.assertEqual(len(json.loads(detail.stdout)["sources"][0]["evidence"]), 3)
        self.assertEqual(len(json.loads(self.invoke("list").stdout)), 2)

    def test_append_preserves_manual_fields_and_original_intent(self):
        run_id = self.seed("append")
        with closing(sqlite3.connect(self.db)) as db, db:
            db.execute(
                "UPDATE opportunities SET manual_note='人工后续备注',manual_stage='人工状态'"
            )
        old = self.rows("opportunities"), self.rows("intents")
        self.assertEqual(self.invoke("apply", run_id).returncode, 0)
        self.assertEqual(self.rows("opportunities"), old[0])
        self.assertEqual(self.rows("intents"), old[1])
        self.assertEqual(
            json.loads(self.invoke("detail", "past-export").stdout)["manual_note"], "人工后续备注"
        )
        self.assertEqual(self.invoke("apply", run_id).returncode, 0)
        self.assertEqual(len(self.rows("opportunity_sources")), 1)

    def test_bad_target_and_unknown_stop(self):
        run_id = self.seed("append")
        with closing(sqlite3.connect(self.db)) as db, db:
            db.execute("DELETE FROM opportunities WHERE id='past-export'")
        self.assertEqual(self.invoke("init").returncode, 0)
        self.assertEqual(self.invoke("apply", run_id).returncode, 1)
        self.assertEqual(self.rows("applications"), [])
        self.assertEqual(self.rows("opportunity_sources"), [])

    def test_unknown_stops(self):
        run_id = self.seed("unknown")
        self.assertEqual(self.invoke("apply", run_id).returncode, 1)
        self.assertEqual(self.rows("applications"), [])
        self.assertEqual(self.rows("opportunity_sources"), [])

    def test_sql_failure_rolls_back_all_application_rows(self):
        run_id = self.seed()
        before = self.rows("opportunities")
        with closing(sqlite3.connect(self.db)) as db, db:
            db.execute(
                "CREATE TRIGGER fail_apply BEFORE INSERT ON applications "
                "BEGIN SELECT RAISE(ABORT,'synthetic failure'); END"
            )
        self.assertEqual(self.invoke("apply", run_id).returncode, 1)
        self.assertEqual(self.rows("opportunities"), before)
        self.assertEqual(self.rows("opportunity_sources"), [])
        self.assertEqual(self.rows("applications"), [])

    def test_output_failure_recovers_by_query_and_replay(self):
        run_id = self.seed()
        self.assertEqual(self.invoke("apply", run_id, "--fault", "stdout").returncode, 1)
        listing = json.loads(self.invoke("list").stdout)
        created = next(row for row in listing if row["id"] != "past-export")
        self.assertEqual(len(json.loads(self.invoke("detail", created["id"]).stdout)["sources"]), 1)
        self.assertEqual(
            json.loads(self.invoke("apply", run_id).stdout)["status"], "already_applied"
        )
        self.assertEqual(len(self.rows("opportunities")), 2)


if __name__ == "__main__":
    unittest.main()
