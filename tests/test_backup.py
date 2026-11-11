"""Offline tests for the GitHub backup (a fake GitHub stands in for the real one; no internet needed).
Run:  python tests/test_backup.py"""
import json
import shutil
import sys
import tempfile
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import backup as B  # noqa: E402
import tracker as T  # noqa: E402


class FakeGitHub:
    """Behaves like `gh release ...` for one release, keeping its files in a folder."""

    def __init__(self):
        self.store = Path(tempfile.mkdtemp())
        self.exists = False
        self.fail_upload = False
        self.uploads = []

    def __call__(self, *args):
        if args == ("api", "repos/{owner}/{repo}/releases/tags/raw-data"):
            if not self.exists:
                return False, '{"message":"Not Found"}', "gh: Not Found (HTTP 404)"
            return True, json.dumps({"assets": [{"name": p.name} for p in self.store.iterdir()]}), ""
        if args[:2] == ("release", "create"):
            self.exists = True
            return True, "", ""
        if args[:2] == ("release", "upload"):
            if self.fail_upload:
                return False, "", "HTTP 502: Server Error"
            src = Path(args[3])
            shutil.copy(src, self.store / src.name)
            self.uploads.append(src.name)
            return True, "", ""
        if args[:2] == ("release", "download"):
            name = args[args.index("--pattern") + 1]
            d = Path(args[args.index("--dir") + 1])
            d.mkdir(parents=True, exist_ok=True)
            shutil.copy(self.store / name, d / name)
            return True, "", ""
        return False, "", "unexpected gh call: " + " ".join(args)


def new_codespace():
    tmp = Path(tempfile.mkdtemp())
    B.RAW = tmp
    T.RAW = tmp
    return tmp


def put(raw, rel, data=b"x"):
    p = raw / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)


def fill(raw):
    put(raw, "davis/zArchive/contents.pdf", b"%PDF contents")
    put(raw, "davis/zArchive/1970s/1976PN/1976PN3bbb1.pdf", b"%PDF bbb1")
    put(raw, "espn/ARCHIVE/1998-99/X_ODI1_01APR1998_BBB-COMMS.html", b"<p>0.1 to Tendulkar</p>")
    put(raw, "cricsheet/tests_json.zip", b"PK fake zip")
    put(raw, "kaggle/odi/odi_Matches_Data.csv", b"a,b\n1,2\n")
    put(raw, "davis/zArchive/1970s/1976PN/1976PN3bbb2.pdf.part", b"half")  # still downloading: never saved
    put(raw, "download_log.txt", b"log")  # logs and progress files: never saved
    put(raw, "progress_davis.json", b"{}")


def zip_names(path):
    with zipfile.ZipFile(path) as zf:
        return sorted(zf.namelist())


def test_backup_saves_only_finished_data_and_only_once():
    gh = FakeGitHub()
    B.gh = gh
    raw = new_codespace()
    fill(raw)
    assert B.backup_round() == 5
    assert gh.exists and gh.uploads == ["part-0001.zip"]
    assert zip_names(gh.store / "part-0001.zip") == [
        "cricsheet/tests_json.zip", "davis/zArchive/1970s/1976PN/1976PN3bbb1.pdf", "davis/zArchive/contents.pdf",
        "espn/ARCHIVE/1998-99/X_ODI1_01APR1998_BBB-COMMS.html", "kaggle/odi/odi_Matches_Data.csv"]
    assert not (raw / "_backup" / "part-0001.zip").exists(), "the local zip is deleted after upload"
    st = json.loads((raw / "progress_backup.json").read_text())
    assert st["saved"] == 5 and st["total"] == 5 and st["error"] is None
    assert B.backup_round() == 0 and gh.uploads == ["part-0001.zip"], "nothing new: nothing uploaded"
    put(raw, "davis/zArchive/1970s/1976PN/1976PN3bbb2.pdf", b"%PDF bbb2")  # the .part finished
    assert B.backup_round() == 1
    assert zip_names(gh.store / "part-0002.zip") == ["davis/zArchive/1970s/1976PN/1976PN3bbb2.pdf"]


def test_restore_on_a_new_codespace():
    gh = FakeGitHub()
    B.gh = gh
    old = new_codespace()
    fill(old)
    B.backup_round()
    put(old, "espn/ARCHIVE/1998-99/X_ODI1_01APR1998_PLYR-V-PLYR.html", b"<p>pvp</p>")
    B.backup_round()
    fresh = new_codespace()  # the old Codespace is gone
    assert B.restore() == 6
    for rel in ["davis/zArchive/contents.pdf", "cricsheet/tests_json.zip",
                "espn/ARCHIVE/1998-99/X_ODI1_01APR1998_PLYR-V-PLYR.html"]:
        assert (fresh / rel).read_bytes() == (old / rel).read_bytes(), rel
    assert B.restore() == 0, "second restore: nothing to do"
    assert B.backup_round() == 0 and len(gh.uploads) == 2, "restored files are not uploaded again"


def test_downloads_started_before_restore_are_not_duplicated_or_overwritten():
    gh = FakeGitHub()
    B.gh = gh
    old = new_codespace()
    fill(old)
    B.backup_round()
    fresh = new_codespace()
    put(fresh, "davis/zArchive/contents.pdf", b"%PDF newer copy")  # downloader already fetched this one again
    put(fresh, "davis/zArchive/1980s/1988EL/1988ELcov.pdf", b"%PDF new file")
    assert B.backup_round() == 1, "restores the saved part first, then uploads only the truly new file"
    assert (fresh / "davis/zArchive/contents.pdf").read_bytes() == b"%PDF newer copy", "never overwrites"
    assert (fresh / "kaggle/odi/odi_Matches_Data.csv").exists()
    assert zip_names(gh.store / "part-0002.zip") == ["davis/zArchive/1980s/1988EL/1988ELcov.pdf"]


def test_a_failed_upload_is_shown_and_tried_again():
    gh = FakeGitHub()
    B.gh = gh
    raw = new_codespace()
    fill(raw)
    gh.fail_upload = True
    assert B.watch_round() is False
    st = json.loads((raw / "progress_backup.json").read_text())
    assert "502" in st["error"] and st["saved"] == 0
    assert "PROBLEM" in T.text(T.rows())
    gh.fail_upload = False
    assert B.watch_round() is True
    st = json.loads((raw / "progress_backup.json").read_text())
    assert st["saved"] == 5 and st["error"] is None
    board = T.text(T.rows())
    assert "Backup to GitHub" in board and "100.0%" in board and "PROBLEM" not in board


def test_restore_never_writes_outside_data_raw():
    gh = FakeGitHub()
    B.gh = gh
    gh.exists = True
    with zipfile.ZipFile(gh.store / "part-0001.zip", "w") as zf:
        zf.writestr("../evil.txt", "no")
        zf.writestr("davis/ok.pdf", "yes")
    raw = new_codespace()
    B.restore()
    assert (raw / "davis/ok.pdf").exists() and not (raw.parent / "evil.txt").exists()


def test_laptop_zip_and_tracker_when_backup_not_started():
    gh = FakeGitHub()
    B.gh = gh
    raw = new_codespace()
    fill(raw)
    assert "NOT RUNNING" in T.text(T.rows())
    B.backup_round()
    B.laptop()
    assert len(zip_names(gh.store / "EVERYTHING.zip")) == 5
    assert [len(b) for b in B.batches(B.data_files(), limit=5)] == [1, 1, 1, 1, 1], "big files go one per zip"
    assert [len(b) for b in B.batches(B.data_files(), limit=10_000)] == [5]


if __name__ == "__main__":
    for t in (test_backup_saves_only_finished_data_and_only_once, test_restore_on_a_new_codespace,
              test_downloads_started_before_restore_are_not_duplicated_or_overwritten,
              test_a_failed_upload_is_shown_and_tried_again, test_restore_never_writes_outside_data_raw,
              test_laptop_zip_and_tracker_when_backup_not_started):
        t()
        print("ok ", t.__name__)
    print("All backup tests passed.")
