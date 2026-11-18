# What to do, in order (18 Nov) - everything from tonight in ONE zip

This zip = careers work + solver work + model work. Nothing from 17-18 Nov is on GitHub yet (GitHub's latest
commit is 96abc51 "Add gold build tools").
NOT in it: repair mode (not built; it needs your "build").

Before you start:
- Codespace: GitHub -> Settings -> Codespaces -> Default idle timeout -> 240 minutes (long runs need it).
- Drag everyball-all-18nov.zip into the Codespace file list, into /workspaces/Everyball.

---------------------------------------------------------------------------------------------------------------
## STEP 1 - Careers + fall of wickets + site pack + model export (one paste, ~30-45 min)
Each script prints its own live ⏱ line.

```bash
cd /workspaces/Everyball && unzip -oq everyball-all-18nov.zip -d /tmp/eb && cp -r /tmp/eb/everyball-all-18nov/. . && rm -rf /tmp/eb everyball-all-18nov.zip && test -f data/everyball.db && (test -f data/everyball.db.before_18nov || cp data/everyball.db data/everyball.db.before_18nov) && (ls data/raw/espn_full/espn_full_commentary_part067.json.gz >/dev/null 2>&1 || gh release download raw-data -p 'espn_full_commentary_part*' -D data/raw/espn_full --clobber) && (test -f data/raw/kaggle/odi/odi_Matches_Data.csv || (gh release download raw-data -p 'part-0001.zip' -D /tmp --clobber && unzip -oq /tmp/part-0001.zip 'kaggle/odi/*' -d data/raw && rm /tmp/part-0001.zip)) && python3 scripts/add_espn_cards.py && python3 scripts/tools/build_gold.py && python3 scripts/tools/publish_gold.py && python3 scripts/tools/career_check.py && python3 scripts/add_espn_fow.py && python3 scripts/publish_db.py pack && python3 scripts/model/export_balls.py && echo && head -30 data/reports/career_check.txt && echo "STEP 1 DONE"
```

What it does:
- Backs up your database once.
- Gets the ESPN files and the Kaggle XIs from the release only if missing.
- ESPN scorecards + players joined, then the gold vote, then the site reads gold, then the career check.
- Fall of wickets + partnerships.
- Packs the site database (data/web).
- Makes the model upload files (data/model_export, 13 MB).

If it stops with an error, send me a screenshot of the last lines. The backup is data/everyball.db.before_18nov.

---------------------------------------------------------------------------------------------------------------
## STEP 2 - Push (you push; Claude never does). The site updates itself (~5-10 min on Render)

```bash
cd /workspaces/Everyball && git add .gitignore PLAN.md docs scripts data/web data/reports && git status --short | head -40 && git commit -m "18 Nov: ESPN scorecards and gold careers, fall of wickets, solver fixes, model scripts" && git push
```

Check the status list before it commits. It must NOT show data/raw, data/model_export or everyball.db (all ignored).

Then open the site and check:
- Sachin: 200 Tests, 463 ODIs.
- Kallis: 166 Tests.

---------------------------------------------------------------------------------------------------------------
## STEP 3 - Group B: fix "who faced" in the flagged innings (long run; start it and leave it)
- 4-core Codespace: ~4.5 h.
- 8-core Azure: ~2.3 h.

If it stops, paste the same block again: it carries on where it stopped.

```bash
cd /workspaces/Everyball && N=$(( $(nproc) / 2 )); N=$(( N < 1 ? 1 : N )); T0=$(date +%s); for k in $(seq 1 $N); do nohup python3 scripts/solver/fix_innings.py who_faced --workers 2 --shard $k/$N --resume > /tmp/who_$k.log 2>&1 & done; sleep 10; while pgrep -f "fix_innings.py who_faced" >/dev/null; do d=$(tail -qn +2 data/reports/fix_who_faced_s*.csv 2>/dev/null | wc -l); f=$(tail -qn +2 data/reports/fix_who_faced_s*.csv 2>/dev/null | grep -c ",fixed except\|,OPTIMAL,\|,FEASIBLE,"); e=$(( ($(date +%s) - T0) / 60 )); echo -ne "\r  ⏱ ${e} min | Group B | ${d} of 797 innings done | fixed ${f} | ${N} copies side by side   "; sleep 30; done; echo; (head -1 data/reports/fix_who_faced_s1.csv; tail -qn +2 data/reports/fix_who_faced_s*.csv) > data/reports/fix_who_faced.csv && echo "GROUP B DONE. Send Claude data/reports/fix_who_faced.csv"
```

After it: do NOT run `publish_db.py pack` again until we decide how "rebuilt" balls show on the site.

---------------------------------------------------------------------------------------------------------------
## STEP 4 - The model on Kaggle (can run while Step 3 runs; it uses Kaggle's machine)

1. Codespace file list: right-click -> Download:
   - data/model_export/everyball_balls.npz, innings.csv, players.csv
   - scripts/model/train_everyball.ipynb
2. kaggle.com -> Create -> **New Dataset**: drop the 3 data files, name it `everyball-balls`, set it to **Private**,
   press Create.
3. kaggle.com -> Create -> **New Notebook** -> File -> **Import Notebook** -> train_everyball.ipynb.
4. Right panel:
   - **Add Input** -> Your Datasets -> everyball-balls.
   - **Session options -> Accelerator -> GPU T4 x2** (or P100). This needs a phone-verified Kaggle account.
5. Top right: **Save Version -> "Save & Run All (Commit)" -> Save**. It runs on Kaggle's machine (~1-3 h), even
   with your browser closed.
   - Progress: Your Work -> notebook -> the version -> **Logs**.
   - The line shows `⏱ | epoch | step | loss | about N min left`.
6. When done: that version -> **Output** -> download **everyball_model.pt** and **metrics.json**.
   - everyball_model.pt goes to GitHub -> Releases -> raw-data -> Edit -> attach the file -> Update. NOT into the repo.
   - Send Claude metrics.json.

Why GPU and not Kaggle CPU: this network learns ~15 million numbers from 6.5 million balls. On a GPU that is
~1-3 h; on Kaggle's 4-core CPU it would be roughly 50-100x slower (days), past the 12 h limit. The 12 h is only
Kaggle's maximum per run, not how long it takes.

---------------------------------------------------------------------------------------------------------------
## What Claude needs back
- Step 1: "STEP 1 DONE" plus the career_check lines it prints (or a screenshot of any error).
- Step 2: the site shows Sachin 463 ODIs.
- Step 3: data/reports/fix_who_faced.csv.
- Step 4: metrics.json, and everyball_model.pt in the release.

Then Claude:
- runs the honest model test (3,000 overs from matches it never saw) and real dream duels (~30-45 min);
- switches the model on in the solver only if it beats the old rule;
- builds repair mode when you say "build".
