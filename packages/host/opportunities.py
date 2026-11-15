"""B35固定合成本地机会管理；显式应用已持久提交的意图。"""

import argparse
import json
import runpy
import sqlite3
import sys
from contextlib import closing
from pathlib import Path

persistent = runpy.run_path(str(Path(__file__).with_name("persistent.py")))
encode = persistent["encode"]


def history_data(data):
    return {k: v for k, v in data.items() if k not in ("manual_stage", "manual_note")}


def initialize(db):
    with db:
        # sqlite3 不会为 CREATE TABLE 自动开始事务，首次建表和历史目标必须一起提交。
        db.execute("BEGIN")
        existed = db.execute("SELECT 1 FROM sqlite_master WHERE name='opportunities'").fetchone()
        db.execute(
            "CREATE TABLE IF NOT EXISTS opportunities (id TEXT PRIMARY KEY, data TEXT NOT NULL, "
            "manual_stage TEXT NOT NULL, manual_note TEXT NOT NULL)"
        )
        db.execute(
            "CREATE TABLE IF NOT EXISTS opportunity_sources (source_key TEXT PRIMARY KEY, "
            "opportunity_id TEXT NOT NULL, evidence TEXT NOT NULL)"
        )
        db.execute(
            "CREATE TABLE IF NOT EXISTS applications "
            "(source_key TEXT PRIMARY KEY, receipt TEXT NOT NULL)"
        )
        if not existed:
            for target, raw in db.execute("SELECT id,data FROM history").fetchall():
                data = json.loads(raw)
                db.execute(
                    "INSERT INTO opportunities VALUES (?,?,?,?)",
                    (target, encode(history_data(data)), data["manual_stage"], data["manual_note"]),
                )


def intent(db, key):
    stored = db.execute("SELECT original_run FROM intents WHERE source_key=?", (key,)).fetchone()
    if stored is None:
        raise ValueError("missing_intent")
    record = persistent["read_record"](db, stored[0])
    payload = record["payload"]
    if persistent["source_key"](payload) != key:
        raise ValueError("source_key_mismatch")
    return record, payload


def destination(db, key, payload):
    if payload["status"] == "new_candidate":
        return "local-" + key, payload["candidate"], payload["candidate"]["evidence"]
    body = payload["merge"]
    target = body["target_opportunity_id"]
    trusted = db.execute("SELECT data FROM history WHERE id=?", (target,)).fetchone()
    existing = db.execute("SELECT data FROM opportunities WHERE id=?", (target,)).fetchone()
    if trusted is None or existing is None:
        raise ValueError("untrusted_target")
    data = history_data(json.loads(trusted[0]))
    if json.loads(existing[0]) != data:
        raise ValueError("target_data_mismatch")
    return target, data, body["evidence"]


def validate(db):
    for (run_id,) in db.execute("SELECT original_run FROM intents").fetchall():
        persistent["read_record"](db, run_id)
    keys = {row[0] for row in db.execute("SELECT source_key FROM applications")}
    if keys != {row[0] for row in db.execute("SELECT source_key FROM opportunity_sources")}:
        raise ValueError("partial_application")
    for key, raw in db.execute("SELECT source_key,receipt FROM applications").fetchall():
        original, payload = intent(db, key)
        target, data, evidence = destination(db, key, payload)
        expected = dict(
            status="applied",
            source_key=key,
            opportunity_id=target,
            original_run_id=original["run_id"],
        )
        existing = db.execute("SELECT data FROM opportunities WHERE id=?", (target,)).fetchone()
        source = db.execute(
            "SELECT opportunity_id,evidence FROM opportunity_sources WHERE source_key=?", (key,)
        ).fetchone()
        if (
            json.loads(raw) != expected
            or existing is None
            or json.loads(existing[0]) != data
            or source[0] != target
            or json.loads(source[1]) != evidence
        ):
            raise ValueError("application_source_mismatch")


def apply(db, run_id):
    record = persistent["read_record"](db, run_id)
    if record["host_status"] not in ("committed", "already_committed"):
        raise ValueError("intent_not_committed")
    key = persistent["source_key"](record["payload"])
    original, payload = intent(db, key)
    target, data, evidence = destination(db, key, payload)
    previous = db.execute("SELECT receipt FROM applications WHERE source_key=?", (key,)).fetchone()
    if previous:
        return dict(status="already_applied", original_receipt=json.loads(previous[0]))
    receipt = dict(
        status="applied", source_key=key, opportunity_id=target, original_run_id=original["run_id"]
    )
    with db:
        if payload["status"] == "new_candidate":
            db.execute("INSERT INTO opportunities VALUES (?,?,?,?)", (target, encode(data), "", ""))
        db.execute(
            "INSERT INTO opportunity_sources VALUES (?,?,?)", (key, target, encode(evidence))
        )
        db.execute("INSERT INTO applications VALUES (?,?)", (key, encode(receipt)))
    return receipt


def detail(db, target):
    row = db.execute(
        "SELECT data,manual_stage,manual_note FROM opportunities WHERE id=?", (target,)
    ).fetchone()
    if row is None:
        raise ValueError("opportunity_missing")
    sources = []
    for key, evidence in db.execute(
        "SELECT source_key,evidence FROM opportunity_sources "
        "WHERE opportunity_id=? ORDER BY source_key",
        (target,),
    ):
        original, payload = intent(db, key)
        receipt = json.loads(
            db.execute("SELECT receipt FROM applications WHERE source_key=?", (key,)).fetchone()[0]
        )
        sources.append(
            dict(
                source_key=key,
                evidence=json.loads(evidence),
                payload=payload,
                original_run_id=original["run_id"],
                application=receipt,
            )
        )
    return dict(
        id=target, data=json.loads(row[0]), manual_stage=row[1], manual_note=row[2], sources=sources
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--database", type=Path, required=True)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("init")
    sub.add_parser("list")
    sub.add_parser("detail").add_argument("opportunity_id")
    action = sub.add_parser("apply")
    action.add_argument("run_id")
    action.add_argument("--fault", choices=("stdout",))
    args = parser.parse_args()

    def offline(event, arguments):
        if event in ("socket.connect", "socket.getaddrinfo", "socket.sendto"):
            raise RuntimeError("offline_host")

    sys.addaudithook(offline)
    try:
        persistent["cli"]["bootstrap"](args.bundle.resolve())
        if args.command == "init":
            with closing(persistent["open_database"](args.database)) as db:
                initialize(db)
            print(encode(dict(status="initialized")))
            return 0
        mode = "ro" if args.command in ("list", "detail") else "rw"
        with closing(
            sqlite3.connect(args.database.resolve().as_uri() + "?mode=" + mode, uri=True)
        ) as db:
            db.execute("PRAGMA synchronous=FULL")
            validate(db)
            if args.command == "apply":
                result = apply(db, args.run_id)
                if args.fault == "stdout":
                    raise OSError("synthetic_output_failure")
            elif args.command == "detail":
                result = detail(db, args.opportunity_id)
            else:
                result = [
                    detail(db, target)
                    for (target,) in db.execute(
                        "SELECT id FROM opportunities ORDER BY id"
                    ).fetchall()
                ]
            print(encode(result))
        return 0
    except Exception:
        print(encode(dict(status="unavailable", reason="inspect_database")))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
