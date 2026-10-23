#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Drive the svFilm Lightroom bridge via the file queue.

Protocol: command file starts with "@<id>\\n"; bridge replies "<id>|OK ...".
This avoids reading a stale result from the previous command.

Usage:
  python bridge_client.py ping
  python bridge_client.py select <image path>
  python bridge_client.py dump <image path>
  python bridge_client.py keys <image path>
  python bridge_client.py one <image path> <outdir>
  python bridge_client.py apply-export <outdir>
"""
from __future__ import annotations

import argparse
import os
import sys
import time
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import paths  # noqa: E402

CMD_DIR = Path(paths.CMD_DIR)
CMD = CMD_DIR / "cmd.txt"
TMP = CMD_DIR / "cmd.tmp"
RES = CMD_DIR / "result.txt"


def run(command: str, timeout: float = 180.0) -> str:
    CMD_DIR.mkdir(parents=True, exist_ok=True)
    cid = uuid.uuid4().hex[:10]
    TMP.write_text("@%s\n%s" % (cid, command), encoding="utf-8")
    os.replace(TMP, CMD)
    t0 = time.time()
    while time.time() - t0 < timeout:
        if RES.exists():
            text = RES.read_text(encoding="utf-8", errors="replace").strip()
            if text.startswith(cid + "|"):
                return text[len(cid) + 1:]
        time.sleep(0.25)
    return "TIMEOUT"


def main() -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("ping")
    p = sub.add_parser("select"); p.add_argument("path")
    p = sub.add_parser("dump"); p.add_argument("path")
    p = sub.add_parser("keys"); p.add_argument("path")
    p = sub.add_parser("one"); p.add_argument("path"); p.add_argument("outdir")
    p = sub.add_parser("apply-export"); p.add_argument("outdir")
    args = ap.parse_args()
    if args.cmd == "ping":
        print(run("PING"))
    elif args.cmd == "select":
        print(run("SELECT " + args.path))
    elif args.cmd == "dump":
        print(run("DUMP " + args.path))
    elif args.cmd == "keys":
        print(run("KEYS " + args.path))
    elif args.cmd == "one":
        print(run("ONE %s %s" % (args.path, args.outdir)))
    elif args.cmd == "apply-export":
        print(run("APPLY_EXPORT " + args.outdir))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
