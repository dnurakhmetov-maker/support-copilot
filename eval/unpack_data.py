"""Unpack the case archive into data/. Owner: Infra / QA.

  python -m eval.unpack_data --archive ~/Downloads/handout.gz [--dest data]

Same as `tar -xzf <archive> --strip-components=1 -C data --exclude='._*'`, but works on every OS.
Skips macOS resource forks (._*, __MACOSX) and refuses paths that escape the destination.
"""
from __future__ import annotations

import argparse
import os
import shutil
import tarfile
from pathlib import Path, PurePosixPath


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--archive", required=True, help="path to handout .gz / .tar.gz")
    ap.add_argument("--dest", default=os.getenv("DATA_DIR", "data"))
    a = ap.parse_args()

    archive = Path(os.path.expanduser(a.archive))
    dest = Path(a.dest).resolve()
    dest.mkdir(parents=True, exist_ok=True)
    n = 0
    with tarfile.open(archive, "r:*") as tar:
        for m in tar:
            parts = PurePosixPath(m.name).parts[1:]  # --strip-components=1
            if not parts or not m.isfile():
                continue
            if any(p.startswith("._") or p == "__MACOSX" for p in parts):
                continue
            target = dest.joinpath(*parts).resolve()
            if not target.is_relative_to(dest):
                raise SystemExit(f"unsafe path in archive: {m.name}")
            target.parent.mkdir(parents=True, exist_ok=True)
            with tar.extractfile(m) as src, open(target, "wb") as dst:
                shutil.copyfileobj(src, dst)
            n += 1
            print(f"  {target.relative_to(dest)}  ({m.size:,} bytes)")
    print(f"unpacked {n} files -> {dest}")


if __name__ == "__main__":
    main()
