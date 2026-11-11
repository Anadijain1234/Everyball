# Everyball: plan and progress

> **For any new chat with Claude:** read this file first. It says what is decided, what is done, and the exact next step.

## What we are building
A cricket history site: **every Test since 1877, every ODI since 1971**, ball by ball wherever it exists.
- Ask in plain words (English + Hindi) and get exact numbers, with a "how it was counted" line. The AI only turns the question into a database query; **the database does the counting** and never invents numbers.
- **Legendary Duels**: the great batter vs the great bowler of each era, ball by ball, with a coverage meter and a "who won" verdict.
- Player pages, top lists, "same age" comparisons (Gill at 27 vs Gavaskar at 27), eras.
- Men's cricket only for now. T20s later.

## Data sources (all with permission)
| Source | What we take | Years | Permission / credit |
|---|---|---|---|
| **Charles Davis**, Test Match Database (sportstats.com.au) | Test ball-by-ball (about 82% of all Tests) + Test scorecards | 1877 to 2010s | Permission by email (Anadi). Raw files may be shared too. Credit on the Credits page. |
| **Cricsheet** (cricsheet.org) | Test + ODI ball-by-ball | about 2002 to now | Free (ODC-BY): credit |
| **ESPNcricinfo old archive** (i.imgci.com) | ODI/Test ball-by-ball commentary + "player v player" files; old scorecards (who took each wicket) | Oct 1998 to 2002; scorecards 1970-71 on | Permission by email (Anadi), with credit |
| **Kaggle ODI dataset** (bhuvaneshprasad) | ODI scorecards, balls faced, partnerships, fall of wickets; players' bowling style + date of birth | 1971 to Mar 2024 | Credit the uploader (TODO: note its licence from the Kaggle page) |
| Our own lists | nicknames, ground to country (SENA, home/away) | | ours |

Not used: the Z-Library Wisden PDF (unofficial copy; only a bought copy may be used, and only as a cross-check). CricketArchive (paid). Cricmetric (no API).

**Priority when sources overlap:** Cricsheet, then Davis, then the ESPN files, then scorecards. Every match's balls must add up to its scorecard, or the match is flagged.

**Credits:** one Credits page only (in the menu). No footer credits.

## Coverage (approximate, to 11 Nov 2026)
- Tests: about 2,660; ball-by-ball about 82%; scorecards 100%.
- ODIs between full members: about 4,300; ball-by-ball about 60-65% (patchy Oct 1998 to 2001, complete from 2002); scorecards 100%.
- Not available anywhere digital: ODI ball-by-ball before Oct 1998 (only on paper: MCC Library, Cricket Australia's scorebooks, Pakistan TV scorers).

## Stages
1. **Data foundation**: downloader, readers, one database, cross-checks, coverage counts.
2. **The site**: Ask, Legendary Duels, players, top lists, Credits, coverage meter, weekly auto-update from Cricsheet (GitHub Actions).
3. **Context model**: pitch/spin/seam indices from match numbers, pitch tags from match reports, pressure, matchup ("expected vs actual") verdicts with ranges.
4. **Research**: estimate 1971-98 ODI head-to-heads from scorecards (IPF, Bayesian/MCMC, Markov + multinomial), tested on hidden modern matches; paper with a professor.

## Storage
- Raw downloads: `data/raw/` in the Codespace (gitignored, never in the code history).
- **Safe copy of the raw downloads: automatic, every 20 min, to this private repo's Releases -> `raw-data`**
  (`scripts/backup.py`). A stopped Codespace loses nothing; a deleted one is restored by `bash scripts/start.sh`.
  Laptop copy: `python scripts/backup.py laptop` when everything is done, then one click on the Releases page.
- Finished database: in this private repo (compressed, split into files under 100 MB each; a Release if bigger).
- Render (the website, later) stores nothing: its free disk is wiped on every restart. It reads the finished
  database that comes with the code from GitHub.
- Keys (later): `.env` only, never on GitHub.

## Progress
- [x] **Step 1**: private repo `Everyball`, Codespace, folders, .gitignore (Anadi, 11 Nov 2026)
- [x] **Step 2**: downloader `scripts/download.py` + tests (Claude, 11 Nov 2026)
- [x] Step 2b: live tracker `scripts/tracker.py` (+ phone page), progress files, retry of failed files (Claude, 11 Nov 2026)
- [x] Step 2c: automatic backup to GitHub Releases (`scripts/backup.py`) + one-command start/resume (`scripts/start.sh`) (Claude, 11 Nov 2026)
- [ ] **Step 3**: run the downloader in the Codespace (Anadi): `bash scripts/start.sh`. Started 11 Nov 2026: Cricsheet + Kaggle done; Davis found 24 archive links in contents.pdf, then about 11,400 PDFs in all (about 9-10 hours at 1.5 s each); ESPN ball-by-ball about 480+ files. Then `python scripts/download.py retry` and `python scripts/backup.py now`.
- [ ] **Step 4**: send Claude 6 sample files (listed below) + the output of `python scripts/download.py status`
- [ ] **Step 5**: readers + database builder (Claude)
- [ ] **Step 6**: build the database (Anadi, one command)
- [ ] Anadi's side task: 100 test questions with correct answers from ESPN (Tests + ODIs, old + new)

### Step 4 sample files
1. `data/raw/davis/zArchive/contents.pdf`
2. any one `...bbb...pdf` from `data/raw/davis/zArchive/1970s/`
3. any one Davis file from before 1950
4. one Test and one ODI JSON from inside `data/raw/cricsheet/tests_json.zip` and `odis_json.zip`
5. one ESPN `..._BBB-COMMS.html` and one `..._PLYR-V-PLYR.html` from `data/raw/espn/`
6. one ESPN scorecard from the 1980s from `data/raw/espn/`
