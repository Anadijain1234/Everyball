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

## Layers (medallion)
- **Bronze**: raw downloads in `data/raw/`, never changed.
- **Silver**: every source read into the same tables, each row tagged with its `source` (davis, cricsheet, espn,
  kaggle, book), plus `played` (every player in each XI) and `ref_matches` (ESPN's own year lists).
- **Gold**: one chosen version per match (`matches.card_source` / `ball_source`: a source that adds up, by priority),
  the views `bat`, `bowl`, `ball`, `inns`, one record per person (`people` step), and `player_stats`.
  The website and the Ask box read only gold.

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
- [x] Step 2d: Davis dead links handled fast (asked once, listed, never retried), pause 0.75 s, automatic slow-down on refusals, folder-typo and capital-letter fixes; ESPN one-year folders (`espn-years`), ESPN pause 1 s; tracker in India time with dead links shown (Claude, 11 Nov 2026)
- [x] **Step 3**: run the downloader in the Codespace (Anadi): `bash scripts/start.sh`. Started 11 Nov 2026: Cricsheet + Kaggle done; Davis found 24 archive links in contents.pdf, then about 11,400 PDFs in all (about 9-10 hours at 1.5 s each); ESPN ball-by-ball about 480+ files. Then `python scripts/download.py retry` and `python scripts/backup.py now`.
- [x] **Step 3**: everything downloaded and backed up (68,229 files; Davis 10,719 files, ESPN about 7,000, Cricsheet, Kaggle) (11 Nov 2026)
- [x] **Step 4**: samples studied (11 Nov 2026)
- [x] **Step 5, part 1 (Tests)**: `scripts/build_db.py` + readers for Davis (Test pages, ball-by-ball with batter-by-batter checks, covers, player lists), Cricsheet, the Wisden book data; `scripts/checks.py` (ESPN year lists, Test-number gaps, cricket maths, Davis vs Cricsheet/book, famous facts); `scripts/ask.py` (Claude, 11 Nov 2026)
- [x] **Step 5, part 2 (ODIs)**: `read_kaggle.py` (every ODI 1971-2024, numbered; players joined to Davis/Cricsheet names by cricinfo id, full name, then surname + first name/initial) and `read_espn.py` (scorecards used only when every innings adds up; ball-by-ball commentary + the commentary text; player-v-player tables); report checks ESPN commentary against ESPN's player-v-player tables (Claude, 11 Nov 2026)
- [x] **Stage 2a: the website** (Claude, 12 Nov 2026): Flask (`app/`), Duel finder with autocomplete and the
  "every ball" strip, player pages (totals, year by year, how he got out, rivals), coverage by decade, Credits page,
  light/dark, phone layout. Ask box: Groq `openai/gpt-oss-120b` writes one read-only SQL query; the database counts;
  the answer line is written from the returned rows only; "How it was counted" shows the query. Hindi via Sarvam
  (`mayura:v1`). 6 questions/min and 60/day per visitor; same question = cached answer. `scripts/publish_db.py`
  packs the database into 45 MB parts for GitHub; `render.yaml` for Render.
- [x] **Step 5, round 3: the full data, fixed in one go** (Claude, 12 Nov 2026, on Anadi's full data): Davis's newer
  pages (mixed-case teams), batter lines read by dismissal words (one-space gaps, wrapped lines, '#N/A', daggers,
  5 penalty runs), wrong team names in Davis's templates, "Scores of 50+" pages skipped, Davis's short names in the
  ball-by-ball ('M Jayaward.', 'Javed M.', 'G Pollock'), partial old records marked; every source's match number
  checked against ESPN's year lists; the book's OCR numbers checked by date and teams; one person = one record
  (ESPNcricinfo ids + name matching); every player in the XI counted (did not bat); a scorecard is only chosen if
  its batters and bowlers add up. Result: all 9 famous facts PASS; Davis scorecards add up 99.4%; Davis vs Cricsheet
  22,052/22,054 batters agree; Davis ball-by-ball: 98.6% of balls proven, 94.1% of batters match the scorecard.
- [ ] Anadi: run the one command (unzip, tests, commit, build), then try the site in the Codespace; pack + Render.
- [ ] Later: recent matches Cricsheet doesn't have yet (19 Tests, 32 ODIs, mostly 2024-26) -> fresh Cricsheet download
  (Stage 2b weekly update); ESPN commentary vs player-v-player (612/905 innings agree) to look at.
- [ ] **Step 6**: build the database (Anadi): `python scripts/build_db.py all`, then send Claude data/build_report.txt
- [ ] Stage 2b (later): weekly Cricsheet update with GitHub Actions + Render auto-deploy
- [ ] Anadi's side task: 100 test questions with correct answers from ESPN (Tests + ODIs, old + new)

### What we learned while downloading (11 Nov 2026)
- Davis's archive runs 1877 to **November 2020** (about 2,390 Tests), in **747 series**. Each series: cover, sessions,
  averages pages; each Test: main page, fall of wickets, ball-by-ball (looks like ONE bbb file per Test, for about 85%
  of Tests; to confirm on a sample). About 16,600 links in all, about 6,000 of them dead (empty Test slots on his pages).
- Real Davis files: about 10,000-11,000.
- ESPN's old archive: one-year folders (e.g. `ARCHIVE/2001/`) hold matches played in England; nothing before about
  the 1980s (`1970-71` is 404); blocked after about 2015-16 (`2020-21` gives 403).
- **Next check (after downloads):** count Davis's Tests per year and compare with ESPN's list of Tests per year
  (stats.cricinfo.com/ci/engine/records/team/match_results_year.html?class=1;id=YEAR;type=year, e.g. 1877 = Test #1, #2).
  Every year must match; any year that doesn't is looked at by hand. ODIs: compare ESPN files with the Kaggle ODI list.

### Step 4 sample files
1. `data/raw/davis/zArchive/contents.pdf`
2. any one `...bbb...pdf` from `data/raw/davis/zArchive/1970s/`
3. any one Davis file from before 1950
4. one Test and one ODI JSON from inside `data/raw/cricsheet/tests_json.zip` and `odis_json.zip`
5. one ESPN `..._BBB-COMMS.html` and one `..._PLYR-V-PLYR.html` from `data/raw/espn/`
6. one ESPN scorecard from the 1980s from `data/raw/espn/`
