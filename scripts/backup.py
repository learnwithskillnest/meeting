#!/usr/bin/env python3
"""Consistent backup of the database and private media.

    python scripts/backup.py /path/to/backups [--keep 14]

Uses SQLite's online backup API, so it is safe while the server is running.
Writes <dir>/heartsync-<UTC timestamp>/{heartsync.db, media.tar.gz} and
deletes the oldest backups beyond --keep. Restore: stop the server, copy
heartsync.db to DATABASE_PATH, extract media.tar.gz into MEDIA_ROOT, start.
Backups contain personal data: store them encrypted with restricted access.
"""
from __future__ import annotations

import argparse
import shutil
import sqlite3
import sys
import tarfile
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from heartsync.config import get_settings  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("dest")
    ap.add_argument("--keep", type=int, default=14, help="number of backups to keep (default 14)")
    args = ap.parse_args()
    s = get_settings()
    dest_root = Path(args.dest)
    dest_root.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    target = dest_root / f"heartsync-{stamp}"
    target.mkdir(mode=0o700)

    src = sqlite3.connect(s.database_path)
    dst = sqlite3.connect(target / "heartsync.db")
    with dst:
        src.backup(dst)
    ok = dst.execute("PRAGMA integrity_check").fetchone()[0]
    src.close()
    dst.close()
    if ok != "ok":
        print(f"Integrity check failed: {ok}", file=sys.stderr)
        return 1

    media = Path(s.media_root)
    with tarfile.open(target / "media.tar.gz", "w:gz") as tar:
        if media.exists():
            tar.add(media, arcname=".")

    backups = sorted(p for p in dest_root.glob("heartsync-*") if p.is_dir())
    for old in backups[:-args.keep] if args.keep > 0 else []:
        shutil.rmtree(old)
    print(f"Backup written to {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
