"""从 Git 固定对象和已验收的 A32-r1 ZIP 离线生成 B36 交付物。"""

import argparse
import hashlib
import json
import subprocess
import zipfile
from pathlib import Path

BASE = "4cd51967751411aa98fe1c39da46914a00fe3ced"
A32 = "42b60c718073b2687cdc8da05582ba6f8e9e24e82a27dc0e253bfaf9b94b6f6a"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    if hashlib.sha256(args.archive.read_bytes()).hexdigest() != A32:
        raise SystemExit("A32-r1 ZIP SHA256 mismatch")
    repo = Path(__file__).resolve().parents[3]

    def git(*args):
        return subprocess.check_output(["git", "-C", str(repo), *args])

    head = git("rev-parse", "HEAD").decode().strip()
    with zipfile.ZipFile(args.archive) as archive:
        files = {
            name: archive.read(name)
            for name in archive.namelist()
            if not name.endswith("/")
        }
    for name in ("cli.py", "persistent.py", "opportunities.py"):
        files["host/" + name] = git("show", f"{BASE}:packages/host/{name}")
    for name in ("install.py", "README.md"):
        files[name] = git("show", f"{head}:packages/host/delivery/{name}")
    manifest = dict(
        host_commit=BASE,
        packaging_commit=head,
        a32_zip_sha256=A32,
        files={
            name: hashlib.sha256(data).hexdigest()
            for name, data in sorted(files.items())
        },
    )
    files["MANIFEST.json"] = json.dumps(manifest, indent=2).encode()
    with zipfile.ZipFile(args.output, "x") as release:
        for name, data in sorted(files.items()):
            release.writestr(
                zipfile.ZipInfo(name), data, compress_type=zipfile.ZIP_DEFLATED
            )
    print(hashlib.sha256(args.output.read_bytes()).hexdigest(), args.output)


if __name__ == "__main__":
    main()
