"""
Collect what Claude needs to look at into one small zip: the build report plus the Davis files (PDF + the text
read from it) of the matches named.

  python scripts/collect_samples.py T1673 T1767 T1988      ->  samples_for_claude.zip
"""
import shutil
import sqlite3
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw"


def main(ids):
    out = ROOT / "samples_for_claude"
    shutil.rmtree(out, ignore_errors=True)
    out.mkdir()
    con = sqlite3.connect(ROOT / "data" / "everyball.db")
    files = [ROOT / "data" / "build_report.txt"]
    lines = []
    for mid in ids:
        row = con.execute("SELECT * FROM matches WHERE match_id = ?", (mid,)).fetchone()
        lines.append(f"{mid}: {row}")
        for (f, st, d) in con.execute("SELECT file, status, detail FROM build_log WHERE source='davis' AND detail LIKE ?",
                                      (mid + ":%",)):
            lines.append(f"   {st} {f} {d}")
            p = Path(f)
            if p.suffix != ".pdf":                 # ball-by-ball without a readable Test page: take its folder's pages
                for d in (RAW / "davis" / "zArchive").glob(f"*/{p.name}"):
                    lines.append("   folder: " + " ".join(sorted(x.name for x in d.iterdir())))
                    files += sorted(x for x in d.glob("*.pdf") if "bbb" not in x.name)[:6]
                continue
            files += [p] + sorted(p.parent.glob(p.stem + "bbb*.pdf"))
            lines.append("   folder: " + " ".join(sorted(x.name for x in p.parent.iterdir())))
        n = con.execute("SELECT number FROM matches WHERE match_id = ?", (mid,)).fetchone()
        if n:   # Davis files that mention this Test number but weren't matched (e.g. pages that didn't read)
            for (f, st, d) in con.execute("SELECT file, status, detail FROM build_log WHERE source='davis' AND status!='ok'"
                                          " AND file LIKE ?", (f"%/{(row[3] or '????')[:4]}%",)):
                lines.append(f"   same year, not read: {st} {f} {d}")
    (out / "about.txt").write_text("\n".join(lines))
    for f in files:
        f = Path(f)
        if not f.exists():
            continue
        rel = Path(str(f).split("data/raw/")[-1]) if "data/raw/" in str(f) else Path(f.name)
        (out / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(f, out / rel)
        txt = RAW / "_text" / rel.with_suffix(".txt")
        if f.suffix == ".pdf" and txt.exists():
            shutil.copy(txt, out / rel.with_suffix(".txt"))
    z = ROOT / "samples_for_claude.zip"
    with zipfile.ZipFile(z, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in out.rglob("*"):
            if f.is_file():
                zf.write(f, f.relative_to(out))
    print(f"{z.name} ready: {z.stat().st_size / 1e6:.1f} MB")


if __name__ == "__main__":
    main(sys.argv[1:] or ["T1673", "T1767", "T1988"])
