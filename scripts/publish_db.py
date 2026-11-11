"""
Put the finished database where the website can get it, without ever going over GitHub's file-size limit.

  python scripts/publish_db.py pack      # data/everyball.db -> data/web/everyball.db.xz.001, .002, ... (45 MB each)
  python scripts/publish_db.py unpack    # the parts -> data/everyball.db  (Render runs this when it builds the site)
  python scripts/publish_db.py status

The parts go to GitHub with the code (git add data/web). The website copy leaves out what the site doesn't show
(the commentary text and the build log), so it stays small.
"""
import hashlib
import json
import lzma
import sqlite3
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common as C  # noqa: E402

WEB = C.ROOT / "data" / "web"
PART = 45 * 1024 * 1024
MANIFEST = WEB / "manifest.json"


def sha(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def pack():
    if not C.DB_PATH.exists():
        sys.exit("data/everyball.db not found: run python scripts/build_db.py all first")
    WEB.mkdir(parents=True, exist_ok=True)
    slim = WEB / "everyball-web.db"
    slim.unlink(missing_ok=True)
    t0 = time.time()
    src = sqlite3.connect(C.DB_PATH)
    src.execute("VACUUM INTO ?", (str(slim),))
    src.close()
    con = sqlite3.connect(slim)
    con.execute("PRAGMA journal_mode=DELETE")
    con.execute("DELETE FROM commentary")
    con.execute("DELETE FROM build_log")
    con.commit()
    con.execute("VACUUM")
    counts = {t: con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
              for t in ("matches", "players", "batting", "bowling", "balls", "pvp")}
    con.close()
    for old in WEB.glob("everyball.db.xz.*"):
        old.unlink()
    parts, n, out = [], 0, None
    comp = lzma.LZMACompressor(preset=6)

    def write(data):
        nonlocal n, out
        while data:
            if out is None or out.tell() >= PART:
                if out:
                    out.close()
                n += 1
                p = WEB / f"everyball.db.xz.{n:03d}"
                parts.append(p.name)
                out = open(p, "wb")
            room = PART - out.tell()
            out.write(data[:room])
            data = data[room:]

    with open(slim, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 22), b""):
            write(comp.compress(chunk))
    write(comp.flush())
    out.close()
    info = {"sha256": sha(slim), "bytes": slim.stat().st_size, "parts": parts, "built": time.strftime("%Y-%m-%d %H:%M"),
            "counts": counts}
    MANIFEST.write_text(json.dumps(info, indent=1))
    slim.unlink()
    size = sum((WEB / p).stat().st_size for p in parts)
    print(f"Packed {info['bytes'] / 1e6:,.0f} MB into {len(parts)} parts ({size / 1e6:,.0f} MB) in {time.time() - t0:,.0f} s")
    print("Next: git add data/web && git commit -m 'Website database' && git push")


def unpack(target=None, quiet=False):
    """Rebuild the database from the parts, unless the one already there is the same."""
    target = Path(target or C.DB_PATH)
    if not MANIFEST.exists():
        if not quiet:
            print("No data/web/manifest.json: nothing to unpack (run pack where the database was built)")
        return False
    info = json.loads(MANIFEST.read_text())
    stamp = target.with_suffix(".sha256")
    if target.exists() and stamp.exists() and stamp.read_text().strip() == info["sha256"]:
        if not quiet:
            print("Database already up to date")
        return True
    tmp = target.with_suffix(".tmp")
    target.parent.mkdir(parents=True, exist_ok=True)
    dec = lzma.LZMADecompressor()
    with open(tmp, "wb") as out:
        for p in info["parts"]:
            with open(WEB / p, "rb") as f:
                for chunk in iter(lambda: f.read(1 << 22), b""):
                    out.write(dec.decompress(chunk))
    if sha(tmp) != info["sha256"]:
        tmp.unlink()
        raise SystemExit("The unpacked database doesn't match its checksum: a part is missing or damaged")
    tmp.replace(target)
    stamp.write_text(info["sha256"])
    if not quiet:
        print(f"Unpacked {info['bytes'] / 1e6:,.0f} MB to {target} (built {info['built']})")
    return True


def status():
    if MANIFEST.exists():
        info = json.loads(MANIFEST.read_text())
        print(f"Website database: built {info['built']}, {info['bytes'] / 1e6:,.0f} MB, {len(info['parts'])} parts")
        for k, v in info["counts"].items():
            print(f"   {k:10} {v:,}")
    else:
        print("No website database packed yet")


if __name__ == "__main__":
    what = sys.argv[1] if len(sys.argv) > 1 else "status"
    {"pack": pack, "unpack": unpack, "status": status}.get(what, status)()
