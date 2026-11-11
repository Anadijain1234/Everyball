"""
Keep a safe copy of every downloaded file on GitHub, so nothing is lost even if the Codespace stops or is deleted.

The copy lives in this private repo under  Releases -> raw-data  (private, like the repo).

  python scripts/backup.py watch     # every 20 min: zip the NEW files and upload them (start.sh runs this for you)
  python scripts/backup.py now       # one backup round right now
  python scripts/backup.py restore   # new or reset Codespace: bring every saved file back (skips files already here)
  python scripts/backup.py laptop    # when everything is done: one big zip on GitHub for one-click download to your laptop
  python scripts/backup.py status    # how much is saved

Only finished data files are saved (folders davis/, espn/, cricsheet/, kaggle/) - never logs or half-downloaded .part files.
Each round uploads only what is new since the last round: part-0001.zip, part-0002.zip, ...
Uses the GitHub CLI (gh), which is already logged in inside a Codespace. No keys or tokens needed.
"""
import json
import subprocess
import sys
import time
import zipfile
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "data" / "raw"
TAG = "raw-data"
FOLDERS = ["cricsheet", "kaggle", "davis", "espn"]
EVERY_MIN = 20
MAX_PART = 1_500_000_000  # bytes per zip (GitHub allows up to 2 GB per file)
NOTES = ("Automatic backup of data/raw (Everyball downloads). Restore with: python scripts/backup.py restore. "
         "Data from Charles Davis's Test Match Database, Cricsheet, ESPNcricinfo and the Kaggle ODI dataset, "
         "used with permission. Private: do not make public.")


def manifest_path():
    return RAW / "backup_manifest.json"


def work_dir():
    return RAW / "_backup"


def say(msg):
    line = f"{datetime.now():%H:%M:%S} {msg}"
    print(line, flush=True)


def gh(*args):
    """Run the GitHub CLI. Returns (ok, stdout, stderr). Tests replace this function."""
    try:
        r = subprocess.run(["gh", *args], cwd=ROOT, capture_output=True, text=True, timeout=3600)
    except FileNotFoundError:
        return False, "", "the GitHub CLI (gh) is not installed here"
    except subprocess.TimeoutExpired:
        return False, "", "gh took more than an hour and was stopped"
    return r.returncode == 0, r.stdout, r.stderr.strip()


# ---------- what is saved ----------

def load():
    try:
        m = json.loads(manifest_path().read_text())
        m.setdefault("parts", {})
        return m
    except Exception:
        return {"parts": {}}


def save_manifest(m):
    p = manifest_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(json.dumps(m))
    tmp.replace(p)


def saved_files(m):
    return {f for files in m["parts"].values() for f in files}


def data_files():
    """Every finished data file, as a path relative to data/raw (e.g. davis/zArchive/contents.pdf)."""
    out = []
    for folder in FOLDERS:
        d = RAW / folder
        if d.exists():
            for p in sorted(d.rglob("*")):
                if p.is_file() and not p.name.endswith((".part", ".tmp")):
                    out.append(p.relative_to(RAW).as_posix())
    return out


def write_status(**info):
    info["updated"] = time.time()
    try:
        p = RAW / "progress_backup.json"
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_name(p.name + ".tmp")
        tmp.write_text(json.dumps(info))
        tmp.replace(p)
    except Exception:
        pass


# ---------- GitHub release ----------

def remote_assets():
    """Names of the files in the raw-data release, or None if the release does not exist yet."""
    ok, out, err = gh("api", f"repos/{{owner}}/{{repo}}/releases/tags/{TAG}")  # gh fills in owner/repo itself
    if not ok:
        if "404" in err or "not found" in err.lower():
            return None
        raise RuntimeError(f"can't reach GitHub: {err or 'gh failed'}")
    return sorted(a["name"] for a in json.loads(out or "{}").get("assets", []))


def remote_parts():
    names = remote_assets()
    return None if names is None else [n for n in names if n.startswith("part-") and n.endswith(".zip")]


def ensure_release():
    if remote_assets() is None:
        ok, _, err = gh("release", "create", TAG, "--title", "Raw data backup (automatic)", "--notes", NOTES)
        if not ok:
            raise RuntimeError(f"couldn't create the backup on GitHub: {err}")
        say("backup: created Releases -> raw-data on GitHub")


def upload(path):
    ok, _, err = gh("release", "upload", TAG, str(path), "--clobber")
    if not ok:
        raise RuntimeError(f"upload of {path.name} failed: {err}")


def download(name, folder):
    ok, _, err = gh("release", "download", TAG, "--pattern", name, "--dir", str(folder), "--clobber")
    if not ok:
        raise RuntimeError(f"download of {name} failed: {err}")


# ---------- zip helpers ----------

def batches(files, limit=MAX_PART):
    out, cur, size = [], [], 0
    for f in files:
        s = (RAW / f).stat().st_size
        if cur and size + s > limit:
            out.append(cur)
            cur, size = [], 0
        cur.append(f)
        size += s
    if cur:
        out.append(cur)
    return out


def make_zip(path, files):
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w") as zf:
        for f in files:
            kind = zipfile.ZIP_STORED if f.endswith(".zip") else zipfile.ZIP_DEFLATED
            zf.write(RAW / f, f, compress_type=kind)
    return path.stat().st_size


def part_number(name):
    try:
        return int(name.split("-")[1].split(".")[0])
    except Exception:
        return 0


# ---------- the commands ----------

def backup_round():
    """Upload every file that is not on GitHub yet. Returns the number of files uploaded."""
    m = load()
    remote = remote_parts()
    if remote is None:
        ensure_release()
        remote = []
    unknown = [p for p in remote if p not in m["parts"]]
    if unknown:  # GitHub has parts this Codespace doesn't know about (new Codespace): bring them back first
        say(f"backup: GitHub has {len(unknown)} saved part(s) this Codespace doesn't know about; restoring them first")
        restore()
        m = load()
    have = saved_files(m)
    new = [f for f in data_files() if f not in have]
    total = len(have) + len(new)
    write_status(saved=len(have), total=total, parts=len(m["parts"]), last=m.get("last"), working=bool(new), error=None)
    if not new:
        m["last"] = time.time()
        save_manifest(m)
        write_status(saved=len(have), total=total, parts=len(m["parts"]), last=m["last"], working=False, error=None)
        say(f"backup: nothing new ({len(have):,} files already saved on GitHub)")
        return 0
    n = max([part_number(p) for p in list(m["parts"]) + remote] + [0])
    sent = 0
    for files in batches(new):
        n += 1
        name = f"part-{n:04d}.zip"
        z = work_dir() / name
        size = make_zip(z, files)
        try:
            upload(z)
        finally:
            z.unlink(missing_ok=True)
        m["parts"][name] = files
        save_manifest(m)
        sent += len(files)
        write_status(saved=len(have) + sent, total=total, parts=len(m["parts"]), last=m.get("last"), working=True,
                     error=None)
        say(f"backup: {name} saved on GitHub ({len(files):,} files, {size / 1e6:,.1f} MB)")
    m["last"] = time.time()
    save_manifest(m)
    write_status(saved=len(have) + sent, total=total, parts=len(m["parts"]), last=m["last"], working=False, error=None)
    return sent


def restore():
    """Download every saved part this Codespace doesn't have yet and unpack it (never overwrites a file)."""
    remote = remote_parts()
    if not remote:
        say("restore: nothing saved on GitHub yet, nothing to bring back")
        return 0
    m = load()
    todo = [p for p in remote if p not in m["parts"]]
    if not todo:
        say(f"restore: this Codespace already has all {len(remote)} saved part(s)")
        return 0
    root = RAW.resolve()
    brought = 0
    for name in todo:
        download(name, work_dir())
        z = work_dir() / name
        names = []
        with zipfile.ZipFile(z) as zf:
            for item in zf.namelist():
                if item.endswith("/"):
                    continue
                dest = (RAW / item).resolve()
                if root not in dest.parents:
                    continue  # never write outside data/raw
                names.append(item)
                if not dest.exists():
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    tmp = dest.with_name(dest.name + ".part")
                    tmp.write_bytes(zf.read(item))
                    tmp.replace(dest)
                    brought += 1
        z.unlink(missing_ok=True)
        m["parts"][name] = names
        save_manifest(m)
        say(f"restore: {name} unpacked")
    say(f"restore: done, {brought:,} files brought back from GitHub")
    return brought


def laptop():
    """One (or a few, if over 1.9 GB) zip of everything, uploaded to the release for one-click download."""
    ensure_release()
    files = data_files()
    groups = batches(files, limit=1_900_000_000)
    for i, group in enumerate(groups, 1):
        name = "EVERYTHING.zip" if len(groups) == 1 else f"EVERYTHING-{i}-of-{len(groups)}.zip"
        z = work_dir() / name
        size = make_zip(z, group)
        try:
            upload(z)
        finally:
            z.unlink(missing_ok=True)
        say(f"laptop: {name} is on GitHub ({len(group):,} files, {size / 1e6:,.0f} MB)")
    say("Open your Everyball repo on GitHub -> Releases -> raw-data, and click the EVERYTHING file(s) to download.")


def watch_round():
    """One round that never crashes: a problem is shown on the tracker and tried again next round."""
    try:
        backup_round()
        return True
    except Exception as e:
        m = load()
        write_status(saved=len(saved_files(m)), total=len(data_files()), parts=len(m["parts"]),
                     last=m.get("last"), working=False, error=str(e)[:200])
        say(f"backup problem (will try again in {EVERY_MIN} min): {e}")
        return False


def watch(every=EVERY_MIN * 60):
    say(f"Backing up new files to GitHub (Releases -> raw-data) every {every // 60} min. "
        "Ctrl+C stops the backup only, not the downloads.")
    while True:
        watch_round()
        time.sleep(every)


def status():
    m = load()
    have, files = saved_files(m), data_files()
    missing = len([f for f in files if f not in have])
    last = datetime.fromtimestamp(m["last"]).strftime("%d %b %H:%M") if m.get("last") else "never"
    print(f"Saved on GitHub: {len(have):,} files in {len(m['parts'])} part(s). Last backup: {last}.")
    print(f"Downloaded here but not saved yet: {missing:,} (the next round saves them).")


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    cmd = argv[0] if argv else "status"
    if cmd == "watch":
        watch()
    elif cmd == "now":
        backup_round()
    elif cmd == "restore":
        restore()
    elif cmd == "laptop":
        backup_round()
        laptop()
    elif cmd == "status":
        status()
    else:
        print(__doc__)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
    except RuntimeError as e:
        print(f"Problem: {e}")
        sys.exit(1)
