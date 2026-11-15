"""B34固定合成本地串行宿主；SQLite是持久提交的唯一事实来源。"""

import argparse
import contextlib
import hashlib
import io
import json
import runpy
import sqlite3
import sys
from pathlib import Path

cli = runpy.run_path(str(Path(__file__).with_name("cli.py")))


def encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def source_key(payload):
    body = payload["candidate"] if payload["status"] == "new_candidate" else payload["merge"]
    evidence = sorted(body["evidence"], key=lambda item: item["comment_id"])
    return hashlib.sha256(encode(evidence).encode()).hexdigest()


def open_database(path):
    db = sqlite3.connect(path)
    db.execute("PRAGMA synchronous=FULL")
    db.executescript("""
        CREATE TABLE IF NOT EXISTS history (id TEXT PRIMARY KEY, data TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS intents (
            source_key TEXT PRIMARY KEY, payload TEXT NOT NULL,
            source TEXT NOT NULL, original_run TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS runs (run_id TEXT PRIMARY KEY, record TEXT NOT NULL);
    """)
    with db:
        db.execute(
            "INSERT OR IGNORE INTO history VALUES (?, ?)",
            (
                "past-export",
                encode(
                    dict(
                        target_user="记录员",
                        scenario="导出个人记录",
                        problem="手工复制",
                        software_shape="本地导出工具",
                        evidence=[],
                        manual_stage="待人工审核",
                        manual_note="固定合成人工备注",
                    )
                ),
            ),
        )
    return db


def read_record(db, run_id):
    from m4_result_format import RunSource, project

    found = db.execute("SELECT record FROM runs WHERE run_id=?", (run_id,)).fetchone()
    if found is None:
        raise ValueError("run_not_found")
    record = json.loads(found[0])
    if record["run_id"] != run_id:
        raise ValueError("run_identity_mismatch")
    if record["host_status"] in ("committed", "already_committed"):
        original = record["original_receipt"]
        first_id = original["run_id"] if original else run_id
        stored = db.execute(
            "SELECT payload, source, source_key FROM intents WHERE original_run=?", (first_id,)
        ).fetchone()
        if stored is None:
            raise ValueError("missing_persisted_intent")
        source = RunSource.model_validate_json(stored[1])
        projected = project(source)
        payload = json.loads(stored[0])
        if (
            projected.run_id != first_id
            or source.receipt.intent is None
            or source.receipt.intent.model_dump(mode="json") != payload
            or record["payload"] != payload
            or source_key(payload) != stored[2]
        ):
            raise ValueError("persistent_source_mismatch")
        if original and read_record(db, first_id) != original:
            raise ValueError("original_receipt_mismatch")
    return record


def persist(db, directory, fault):
    from m4_result_format import project, sources_type

    proposal = cli["read_run"](directory)
    attempt = json.loads((directory / "attempt.json").read_bytes())
    row = dict(
        run_id=proposal["run_id"],
        retry_of_run_id=attempt["retry_of_run_id"],
        agent_proposal=proposal,
        host_status="not_committed",
        payload=None,
        original_receipt=None,
        attempt=attempt,
    )
    source = None
    key = None
    if proposal["status"] in ("committed", "already_committed"):
        sources = sources_type.validate_json((directory / "sources.json").read_bytes())
        if len(sources) != 1 or project(sources[0]).run_id != row["run_id"]:
            raise ValueError("untrusted_source")
        source = sources[0]
        # 完整证据包括platform/video/author/text，不以候选措辞或case判定重复。
        payload = proposal["payload"]
        body = payload["candidate"] if payload["status"] == "new_candidate" else payload["merge"]
        key = source_key(payload)
        if payload["status"] == "merge_evidence":
            target = db.execute(
                "SELECT id FROM history WHERE id=?", (body["target_opportunity_id"],)
            ).fetchone()
            if target is None:
                raise ValueError("untrusted_target")
    try:
        with db:
            if source is not None:
                old = db.execute(
                    "SELECT payload, original_run FROM intents WHERE source_key=?", (key,)
                ).fetchone()
                if old:
                    row.update(
                        host_status="already_committed",
                        payload=json.loads(old[0]),
                        original_receipt=read_record(db, old[1]),
                    )
                else:
                    row.update(host_status="committed", payload=proposal["payload"])
                    db.execute(
                        "INSERT INTO intents VALUES (?, ?, ?, ?)",
                        (key, encode(row["payload"]), source.model_dump_json(), row["run_id"]),
                    )
            db.execute("INSERT INTO runs VALUES (?, ?)", (row["run_id"], encode(row)))
            if fault == "transaction":
                raise OSError("synthetic_transaction_failure")
    except OSError:
        # 原事务全部回滚；失败审计另起事务，不保存半个业务提交。
        row.update(host_status="commit_failed", payload=None, original_receipt=None)
        with db:
            db.execute("INSERT INTO runs VALUES (?, ?)", (row["run_id"], encode(row)))
    return row


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--database", type=Path, required=True)
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run")
    run.add_argument("--case", choices=cli["CASES"], required=True)
    run.add_argument("--output", type=Path, required=True)
    run.add_argument("--max-retries", type=int, choices=(0, 1, 2), default=0)
    run.add_argument("--fault", choices=("transaction", "file", "stdout"))
    read = sub.add_parser("read")
    read.add_argument("run_id")
    args = parser.parse_args()

    # 与B33相同的离线约束；不检查provider环境变量。
    def offline(event, arguments):
        if event in ("socket.connect", "socket.getaddrinfo", "socket.sendto"):
            raise RuntimeError("offline_host")

    sys.addaudithook(offline)
    try:
        identity = cli["bootstrap"](args.bundle.resolve())
        if args.command == "read" and not args.database.is_file():
            raise ValueError("database_missing")
        if args.command == "run" and args.output.exists():
            raise FileExistsError("old_output_protected")
        connection = (
            sqlite3.connect(args.database.resolve().as_uri() + "?mode=ro", uri=True)
            if args.command == "read"
            else open_database(args.database)
        )
        with contextlib.closing(connection) as db:
            # 先验证全体已存来源绑定，不能因坏索引查无记录而重复INSERT。
            for (original_run,) in db.execute("SELECT original_run FROM intents").fetchall():
                read_record(db, original_run)
            if args.command == "read":
                print(encode(read_record(db, args.run_id)))
                return 0
            # B33所有内存结果只作提议，绝不直接输出成宿主持久提交。
            with contextlib.redirect_stdout(io.StringIO()):
                result = cli["execute"](args, identity)
            for directory in sorted(args.output.iterdir()):
                row = persist(db, directory, args.fault)
                if args.fault == "file":
                    raise OSError("synthetic_export_failure")
                cli["write"](directory / "host-receipt.json", row)
                if args.fault == "stdout":
                    raise OSError("synthetic_stdout_failure")
                print(encode(row))
                if row["host_status"] == "commit_failed":
                    return 1
            return result
    except Exception:
        # 输出失败不写业务失败状态；重启read以DB事实为准。
        print(encode(dict(host_status="unavailable", reason="inspect_persistent_run")))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
