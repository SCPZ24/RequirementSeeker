"""验证完整交付目录，在包外创建新的 Windows Python 3.12 x64 离线环境。"""

import argparse
import hashlib
import json
import os
import platform
import subprocess
import sys
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("environment", type=Path)
    args = parser.parse_args()
    root = Path(__file__).resolve().parent
    target = args.environment.resolve()
    if (
        sys.platform != "win32"
        or sys.implementation.name != "cpython"
        or sys.version_info[:2] != (3, 12)
        or platform.machine().lower() != "amd64"
        or sys.maxsize <= 2**32
    ):
        raise SystemExit("Windows x64 / CPython 3.12 x64 required")
    if target.exists() or target.is_relative_to(root):
        raise SystemExit("choose a new environment outside the extracted package")
    expected = json.loads((root / "MANIFEST.json").read_bytes())["files"]
    actual = {
        p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in root.rglob("*")
        if p.is_file() and p != root / "MANIFEST.json"
    }
    if actual != expected:
        raise SystemExit("package inventory or SHA256 mismatch")
    env = {
        k: os.environ[k]
        for k in ("SystemRoot", "SystemDrive", "TEMP", "TMP", "COMSPEC")
        if k in os.environ
    }

    def run(*cmd):
        subprocess.run(cmd, check=True, env=env)

    target.mkdir(parents=True, exist_ok=False)
    run(sys.executable, "-I", "-m", "venv", str(target))
    python = str(target / "Scripts/python.exe")
    pip = [python, "-I", "-m", "pip", "--isolated", "--disable-pip-version-check"]
    wheels = root / "bundle/wheels"
    run(
        *pip,
        "install",
        "--no-index",
        "--no-cache-dir",
        "--only-binary=:all:",
        "--require-hashes",
        "--find-links",
        str(wheels),
        "-r",
        str(root / "bundle/runtime-requirements.txt"),
    )
    run(*pip, "check")
    print(python)


if __name__ == "__main__":
    main()
