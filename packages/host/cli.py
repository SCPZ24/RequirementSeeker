"""B33：只使用冻结合成输入与普通安装Agent的本地宿主。"""

import argparse
import hashlib
import importlib.metadata
import json
import sys
import uuid
import zipfile
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path

MANIFEST_SHA = "8ce466f988bb4a6f2be691590a9bd07415e84e53175bb5146cca5ab32c6a2e03"
WHEEL_SHA = "6c7091093679e14a4221ac9130b86d5a4efc4c92be9e875473e1937d21885cee"
CASES = (
    "new",
    "append",
    "replay",
    "zero",
    "incomplete",
    "unknown",
    "fatal",
    "retryable",
    "cancelled",
    "budget",
)


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write(path, value):
    with path.open("x", encoding="utf8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.flush()
        import os

        os.fsync(stream.fileno())


def bootstrap(bundle):
    """验证示例来源及安装包字节，绝不将Agent源码加入路径。"""
    if digest(bundle / "manifest.json") != MANIFEST_SHA:
        raise ValueError("bundle_manifest_mismatch")
    manifest = json.loads((bundle / "manifest.json").read_bytes())
    actual = {p.relative_to(bundle).as_posix() for p in bundle.rglob("*") if p.is_file()} - {
        "manifest.json"
    }
    if actual != set(manifest["files"]):
        raise ValueError("bundle_inventory_mismatch")
    for name, sha in manifest["files"].items():
        if digest(bundle / name) != sha:
            raise ValueError("bundle_file_mismatch")
    import requirementseeker_agent

    package = Path(requirementseeker_agent.__file__).resolve().parent
    site = (Path(sys.prefix) / "Lib/site-packages").resolve()
    if not package.is_relative_to(site):
        raise ValueError("agent_not_installed")
    dist = importlib.metadata.distribution("requirementseeker-agent")
    direct = dist.read_text("direct_url.json")
    if direct and json.loads(direct).get("dir_info", {}).get("editable"):
        raise ValueError("editable_agent_rejected")
    wheels = list((bundle / "wheels").glob("requirementseeker_agent-*.whl"))
    if len(wheels) != 1 or digest(wheels[0]) != WHEEL_SHA:
        raise ValueError("agent_wheel_mismatch")
    with zipfile.ZipFile(wheels[0]) as wheel:
        names = [
            n
            for n in wheel.namelist()
            if n.startswith("requirementseeker_agent/") and not n.endswith("/")
        ]
        for name in names:
            if (site / name).read_bytes() != wheel.read(name):
                raise ValueError("installed_agent_bytes_mismatch")
    sys.path.insert(0, str(bundle / "examples"))
    return {
        "source_commit": manifest["source_commit"],
        "manifest_sha256": MANIFEST_SHA,
        "wheel_sha256": WHEEL_SHA,
        "agent_origin": str(package),
        "package_files_checked": len(names),
        "python": sys.executable,
    }


def read_run(directory):
    from read_m4_results import read

    attempt = json.loads((directory / "attempt.json").read_bytes())
    if not (directory / "receipt.json").exists() and (directory / "host-error.json").is_file():
        failure = json.loads((directory / "host-error.json").read_bytes())
        if (
            failure.get("run_id") != attempt["run_id"]
            or directory.name != attempt["run_id"]
            or failure.get("status") != "incomplete"
            or failure.get("reason") != "host_execution_failed"
        ):
            raise ValueError("invalid_local_failure")
        return dict(
            run_id=attempt["run_id"],
            status="incomplete",
            reason=failure["reason"],
            agent_reason=failure.get("agent_reason"),
            outcome=None,
            payload=None,
        )
    if not (directory / "receipt.json").is_file():
        raise ValueError("incomplete_attempt_without_receipt")
    receipt = json.loads((directory / "receipt.json").read_bytes())
    if receipt["run_id"] != attempt["run_id"] or directory.name != attempt["run_id"]:
        raise ValueError("run_identity_mismatch")
    for name, sha in receipt["files"].items():
        if (
            name not in ("attempt.json", "sources.json", "results.json")
            or digest(directory / name) != sha
        ):
            raise ValueError("run_file_mismatch")
    if set(receipt["files"]) != {"attempt.json", "sources.json", "results.json"}:
        raise ValueError("incomplete_run")
    rows = list(read(directory))
    if len(rows) != 1 or rows[0] != receipt["result"] or rows[0]["run_id"] != attempt["run_id"]:
        raise ValueError("receipt_mismatch")
    return rows[0]


def execute(args, identity):
    from generate_m4_results import ObservedJudge
    from m2_fake_demo import demo_manifest, demo_request
    from m2_h0_demo import RecordingGateway
    from m4_history_demo import FixtureRouter
    from m4_result_format import RunSource, write_handoff
    from read_m4_results import read
    from requirementseeker_agent import analyze_m2
    from requirementseeker_agent.pipeline.h0 import HistoricalOpportunity, MemoryIntentStore, run_h0
    from requirementseeker_agent.runtime import InMemorySemanticCache
    from requirementseeker_agent.sampling import plan_sampling

    args.output.mkdir(parents=True, exist_ok=False)
    router = FixtureRouter()
    router.answer = (
        "same" if args.case == "append" else "failure" if args.case == "unknown" else "different"
    )
    history = HistoricalOpportunity(
        "past-export", "记录员", "导出个人记录", "手工复制", "本地导出工具"
    )
    store = MemoryIntentStore(
        history=(history,),
        history_revision="b33-synthetic",
        history_judge=router,
        history_complete=args.case != "incomplete",
    )
    previous_run = None
    for index in range(2 if args.case == "replay" else args.max_retries + 1):
        request = demo_request()  # r1固定合成JSON；不接受任意真实请求路径。
        request.run_id = f"b33-{index:02d}-{uuid.uuid4().hex}"
        request.retry_of_run_id = previous_run if args.case != "replay" else None
        request.history_revision = "b33-synthetic"
        request.retry_policy.max_transport_retries = 0
        request.retry_policy.max_output_repairs = 0
        request.cancellation_requested = args.case == "cancelled"
        if args.case == "budget":
            request.budget.max_model_calls = 1
        directory = args.output / request.run_id
        directory.mkdir()
        write(
            directory / "attempt.json",
            dict(
                run_id=request.run_id,
                retry_of_run_id=request.retry_of_run_id,
                case=args.case,
                attempt=index + 1,
                max_retries=args.max_retries,
                started_utc=datetime.now(UTC).isoformat(),
                identity=identity,
                request=request.model_dump(mode="json"),
            ),
        )
        manifest = demo_manifest(request)
        gateway = RecordingGateway(
            {"zero": "no_signal", "fatal": "authentication_failed", "retryable": "timeout"}.get(
                args.case, "valid_pipeline"
            )
        )
        receipt = None
        try:
            m2 = analyze_m2(
                request,
                manifest,
                plan_sampling(manifest, request.comments, request.budget),
                gateway,
                InMemorySemanticCache(),
            )
            judge = ObservedJudge(True)
            before = deepcopy(list(store.intents.values()))
            receipt = run_h0(request, m2, judge, store)
            source = RunSource(
                request=request,
                m2=m2,
                context=judge.context,
                candidate=judge.candidate,
                target_ids=[history.opportunity_id],
                history_complete=store.history_complete,
                receipt=receipt,
                previous=before,
                current=deepcopy(list(store.intents.values())),
            )
            write_handoff(directory, [source])
            row = list(read(directory))[0]
            write(
                directory / "receipt.json",
                dict(
                    run_id=request.run_id,
                    result=row,
                    files={
                        name: digest(directory / name)
                        for name in ("attempt.json", "sources.json", "results.json")
                    },
                    finished_utc=datetime.now(UTC).isoformat(),
                    gateway_stages=gateway.stages,
                ),
            )
        except Exception:
            # 失败/中断不造正式结果，不自动再试；原attempt与已写文件保留。
            write(
                directory / "host-error.json",
                dict(
                    status="incomplete",
                    reason="host_execution_failed",
                    run_id=request.run_id,
                    agent_reason=receipt.reason if receipt is not None else None,
                ),
            )
            print(
                json.dumps(
                    dict(status="incomplete", reason="host_execution_failed", run_id=request.run_id)
                )
            )
            return 1
        # 完成回执是业务事实；终端输出失败不能再写相冲突的执行失败回执。
        print(json.dumps(read_run(directory), ensure_ascii=False))
        if args.case == "replay" and index == 0 and row["status"] == "committed":
            continue
        if row["outcome"] != "retryable_error":
            return 0 if row["status"] in ("committed", "already_committed", "no_opportunity") else 1
        previous_run = request.run_id
    return 1


def main():
    def offline(event, args):
        if event in ("socket.connect", "socket.getaddrinfo", "socket.sendto"):
            raise RuntimeError("offline_host")

    sys.addaudithook(offline)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", type=Path, required=True)
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run")
    run.add_argument("--case", choices=CASES, required=True)
    run.add_argument("--output", type=Path, required=True)
    run.add_argument("--max-retries", type=int, choices=(0, 1, 2), default=0)
    read = sub.add_parser("read")
    read.add_argument("directory", type=Path)
    args = parser.parse_args()
    try:
        identity = bootstrap(args.bundle.resolve())
        if args.command == "read":
            print(json.dumps(read_run(args.directory), ensure_ascii=False))
            return 0
        return execute(args, identity)
    except Exception:
        print(json.dumps(dict(status="incomplete", reason="host_input_or_record_invalid")))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
