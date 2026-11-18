# Everyball: plan and progress

> **For any new chat with Claude:** read this file first, all of it (and docs/ESPN_LIVE_SURVEY.md before ESPN work). **Before building, read docs/BUILD_SPEC.md: the complete build specification (every source, table, rule, check and step).** It says what is decided, what is done, what is
> still missing and the exact next step. Anadi also keeps the key decisions in Claude's memory. If something here and
> a chat disagree, ask Anadi; then fix this file. Update this file at the end of every working session.

## UPDATE 18 Nov 2026 (latest) - careers made right; solver works
**Careers build (Anadi said build, 18 Nov night)** - ESPN's own scorecards for all 7,704 Tests/ODIs are now a scorecard
source ('espn_live'), the Wisden book is dropped, and the website shows the gold (voted) scorecards:
- scripts/add_espn_cards.py: ESPN scorecards (batting, bowling, innings, XIs) from the 67 files; players joined by ESPN id
  AND the match XI. Found + fixed: 51 matches missing from our list (19 Tests incl. #1541 = Kallis's missing Test, #998,
  #1510, Afghanistan's Tests; 32 ODIs 2024-26); 168 split records merged (e.g. Davis "PJP Burge" + book "P JjJ.P.Burge");
  wrong ESPN ids on old players (our Imran Khan 1971-92 carried a modern Imran Khan's id; MJK Smith, JR Reid, Alan v Ali
  Brown...) -> 1,210 player-matches moved to the right person; 428 lines filed under the wrong person moved (only when
  figures were identical); ODI XIs filled from Kaggle "Playing 11" (319 players, rain-hit games).
- scripts/tools/build_gold.py: ESPN votes (priority cricsheet > espn_live > espn > davis > kaggle), book dropped, matches
  count the whole XI. scripts/tools/publish_gold.py: gold lines -> batting/bowling/innings (source 'gold'),
  matches.card_source = 'gold', player_stats from gold (site + Ask box read these).
- scripts/tools/career_check.py: every line v ESPN. Result: batting 183,086 lines 99.98-99.999% equal per field;
  bowling 111,493 lines 99.88-100%; hard questions 26/28 exact (Kallis 13,289/292/166, Sachin 200 Tests + 463 ODIs,
  Border 156, maidens of Murali/Warne/McGrath). Left: Murali balls +1, Kumble maidens -1; 15 careers still mix two people
  (mostly associates, e.g. "Ali Khan"); 2,109 lines ESPN lacks are listed in data/reports/espn_players_joined.csv;
  802 player-matches in our XI but not ESPN's (ESPN's files miss XIs of rain-hit games; ours usually right, e.g.
  McCullum 260 ODIs = official) - all in data/reports/career_differences.csv.
- Home page "Counting..." fixed: coverage numbers computed at pack time (coverage_cache). Player page "matches" = whole XI.
**Solver (Part E)**: scripts/solver/layered_solver.py (counts per partnership-over first, then the order inside the
over) finds answers in 2-180 s with 0 rule problems (old models: no answer in 10 min). 2011 WC final, exactly right per
ball: level 2 31%/22%, 3a 34%/30%, 3b (fake extended highlights) 47%/40%; level 1 innings 1 no answer yet (bowler plan).
layered_solver_wip.py = unfinished next step (some overs known, "stay close to recorded balls"), untested.
scripts/solver/count_versions.py: even with every other ball known, an over has median 6 valid versions; random pick
= 51% (L2) / 65% (L3b); forced balls 17% / 39%. A "most likely order" model (Part F) is the only way past that.
NEXT: Groups B/C with the solver (727 wrong-batter innings, Davis 1,155 / ESPN-old 357 partial innings, 34 ESPN gaps:
needs fall of wickets + partnerships tables: Davis fow + Kaggle Fow/Partnership + ESPN scorecards), then commentary (B),
notes (C), site pages (D), model (F).

## UPDATE 17 Nov 2026 (latest; read HANDOVER_everyball_17nov.md for full detail)
- Stage 1 + 1b DONE: ESPN live ball-by-ball merged (ODIs with bbb 70.4%), redo2 re-fetch (34 matches with real ESPN
  gaps -> solver later), duplicate players merged (50 look-alikes left, listed), Sehwag spelling fix, duel wording,
  render.yaml health check removed.
- Part E solver: whole-innings CP-SAT failed (no answer / 1%); split-at-wickets Joint CP-SAT (split_solver.py) is
  correct (the real 2011 final satisfies every constraint at levels 1/2/3a/3b) but found no first answer in 10 min
  on Azure; plain-Python over-by-over (over_solver.py) gets stuck near over 25. NEXT: install ortools in the chat,
  test here first, then over-by-over with OR-Tools + look-ahead (see handover section 5).

## MASTER PLAN (14 Nov 2026, 5:45 am) - read this first after the CIEs; Anadi will say "build" for all of it at once
One build, in this order (details in the sections below and docs/BUILD_SPEC.md):
A. DATABASE 99%+ CLEAN (gold layer, already built once - rerun with every fix):
   every match verified against every backup source that has it (Cricsheet, Davis, ESPN, Kaggle, Wisden book);
   fixes: Wisden's 30 mislinked scorecards relinked; Davis 1,155 partial innings checked (20 by hand vs his PDFs,
   then all); 727 "who faced" unsure innings solved with the rules engine; 12 anomalies; Kallis's missing Test;
   every remaining disagreement in a report, never silently chosen. Cricket rules engine (rules.py) used everywhere.
   + MATCH STAGE column (added 14 Nov, Anadi said yes): matches.stage = group / quarter / semi / final / playoff /
     place-playoff / bilateral, + final_no for best-of-3 finals. Source: Kaggle "Match Name" (abbreviations Sf, Qf,
     Pqf, "1st Final"...) up to ODI #4745 (Mar 2024), Cricsheet info.event.stage after; ESPN notes as the third vote;
     Tests: WTC final 2021/2023/2025. Check: 13 WC finals + 51 WC knockouts must come out exact (tested 14 Nov: they
     do; 217 ODI finals, 301 knockouts in all). Unlocks "runs in finals/knockouts" (Sachin 1,851 in finals, 2,456 in
     knockouts; Gilchrist 260 in WC finals).
   + FULL PLAYING XI: 92 team sides have <11 players (79 short, 13 empty), mostly rain-hit games where the team never
     batted (e.g. India v Pak, Colombo 20 Jul 1997: only 2 Indians; Wellington 1982: Border missing). Fill from ESPN
     match pages / Kaggle "Playing 11" columns. Test: Border's consecutive-Test streak must read 153 and Sachin's
     Test+ODI streak 239 (both correct only once filled).
B. COMMENTARY, ALL 66 FILES, NOTHING DROPPED: every line stored raw (count check per match); ESPN's ball fields used
   as ball-by-ball; Commentary lookup: "Sachin's six off McGrath at Nairobi" -> the exact commentator words of that
   ball (+ ball facts), with "Commentary: ESPNcricinfo". Note: 2000-04 has text on only ~12% of balls (Nairobi 2000
   may be thin); 2005+ full. Keep the files off public GitHub (ESPN's text, not ours to republish).
C. ESPN MATCH NOTES + DETAILS: finish the notes download (snippet resumes); notes -> facts (code for common
   patterns, AI for the rest, raw always kept); umpires -> umpire stats (most matches; DRS-era review outcomes).
D. WEBSITE V1 (end Nov, Render + pinger): Ask box (Groq writes the query, Sarvam Hindi, database counts),
   Legendary Duels, player pages, top lists, same-age, struggles-where, fall of wickets + partnerships, coverage
   meter, commentary lookup, Credits (ESPN, Davis), every estimate labelled.
E. FILL THE GAPS (after v1): SOLVER (OR-Tools: scorecard + FoW + partnerships + notes facts) + MODEL (picks the most
   likely order) -> ball-by-ball for matches without it, each ball labelled real / forced / estimate.
   Video: YouTube finder + Gemini checker list (running daily) -> Tesseract + Gemini + merge for the best ~50 innings.
F. THE MODEL (Kaggle): commentary -> labels (5 layers, spec section 9); line/length/shot for older balls; duels
   (Dravid v Steyn as expected-vs-actual with ranges); time machine (Gavaskar v Bumrah); conditions (pitch, weather
   from Open-Meteo, wind, dew); "where to bowl to Root" plans from commentary; scouting later (domestic data).
   Retrains monthly, replaces itself only if better on a fixed hidden test.
G. AUTOMATION (after v1, no hand work): a match ends today (e.g. India v NZ) ->
   1. ESPN job (laptop runner; ESPN refuses GitHub's servers) fetches its scorecard, full commentary, match notes,
      details; Cricsheet job (GitHub Actions) fetches Cricsheet's file when it appears (usually within days);
   2. the same gold build runs on the new match (votes, rules check, official totals) and adds it to the database;
   3. the website reads the database, so every stat (Kohli v Henry, records, duels) updates at once - nothing to retrain;
   4. new ESPN-labelled balls go into the training set; the model retrains monthly on Kaggle (Kaggle API) and
      replaces itself only if it beats the old one on the fixed hidden test; then re-labels old balls;
   5. emails Anadi only when something fails. Commentary -> AI numbers is NOT needed for v1 (ESPN already gives
      line/length 2015+ and shot/control 2000+ as fields); text -> numbers for older balls is part of the model (F).
H. THE 12 BIG IDEAS + Restore from TV (spec section 21) and the stat ideas list - after v1.
Not yet downloaded: ESPN match reports (pitch tags), Open-Meteo weather. Later: Ranji. Never: T20s.

## What we are building
A cricket history site: **every Test since 1877, every ODI since 1971**, ball by ball wherever it exists.
- Ask in plain words (English + Hindi) and get exact numbers, with a "how it was counted" line. The AI only turns the question into a database query; **the database does the counting** and never invents numbers.
- **Legendary Duels**: the great batter vs the great bowler of each era, ball by ball, with a coverage meter and a "who won" verdict.
- Player pages, top lists, "same age" comparisons (Gill at 27 vs Gavaskar at 27), eras.
- **Every ball's commentary** where ESPNcricinfo has it: every commentator line, the text between balls (insights,
  quotes, interviews), over summaries, line / length / shot type / scoring zone. Shown with "Commentary: ESPNcricinfo"
  next to it (ESPN's condition).
- **Fall of wickets and partnerships** for every innings (exact from the balls where we have them; from the printed
  scorecards where we don't).
- **"Which player struggles where"**: by line, length, bowling type, ground, country (SENA etc.), era.
- **A model on top of the data** (Stage 3-4): estimates for what doesn't exist anywhere, always shown as estimates.
- Men's Tests and ODIs only. **No T20s** (Anadi, 13 Nov: not the real format for him; integrating it is a headache; Tests + ODIs are already huge).
- **Stat ideas agreed (11-12 Nov):** pressure meter (chance of getting out after 3-5 dots in a row), first 10 balls vs
  set, new vs old ball, powerplay / middle / death, batting first vs chasing, home vs away, year by year, bunny finder
  (with a luck check), 10x10 batter-v-bowler heatmap, how each player gets out (bowled/LBW/caught behind...), false-shot %
  (ESPN shotControl), pitch maps by length zone (ESPN line/length labels), DRS reviews, debutants.
- **Dravid v Steyn = the "expected vs actual" duel model (Stage 3):** every match gets a "conditions" number; start from
  "batter v everyone" + "bowler v everyone", move only when the real duel clearly differs (Bayesian, borrows strength);
  answer as a range ("Dravid won, 21-41"); tested on hidden modern matches.
- **Commentary labelling (Stage 3):** Qwen labels 5,000 lines, a small model learns the rest; Anadi hand-labels 500
  lines to measure accuracy; a keyword method is the baseline to beat. The model learns from ALL text, not only ball lines.
- **Big ideas (all 12 accepted, 13 Nov; details in docs/BUILD_SPEC.md section 21):** time machine, captain's
  notebook, setup detector, drama index, myth-buster research, what if DRS existed, era translator, find the next
  Dravid, draw-your-own pitch map, clutch index, commentator time machine, changing commentary styles. Labels: five
  detailed layers (spec section 9).
- **Later:** ESPN match reports for pitch tags (not downloaded yet); Ranji (adjusted stats, Ranji form -> Test form,
  "ready for India" list).
- **Honest limits (never promise):** no ball speed or swing data, no Hawk-Eye pitch maps in metres, no 3D simulation.

## Rules (Anadi's, never break them)
- **Build only when Anadi says "build".** Explain the plan and answer questions first. Plain, simple language.
  Terminal commands bundled into a few copy-paste blocks (chained with &&). No Codespaces Ports/localhost preview:
  deploy straight to Render.
- **Every piece of data that exists should be caught.** No subsets. Keep the "still missing" list below up to date and
  fill it even if Anadi forgets to remind.
- **Nothing is dropped silently.** Raw files are never changed or deleted; every field of every source has a written
  decision (which table/column, or "raw only" with a reason); every reader counts what went in and what came out.
- **Never present made-up data as real.** Model estimates live in their own clearly labelled place, with ranges.
- **Respect every source's limits and conditions**: Davis up to 300 requests/min; ESPNcricinfo up to 5,000/min (raised
  from 1,000 in Nov 2026), credit ESPN and send them the site link once it's live; the Wisden book: numbers only.
- **Keys** (Groq, Sarvam, GitHub, Hindsight) never on GitHub and never pasted in chat: Render env, GitHub secrets or
  `.env` (gitignored) only. Anadi never pastes GITHUB_TOKEN. Never push to the Hindsight repo. **Claude never pushes, commits or changes anything on GitHub** (Anadi, 13 Nov): it may only read/download (e.g. the raw-data release) when access is given; Anadi commits and pushes himself. Never write "hackathon"
  in public content. Raw data (`data/raw/`) never in GitHub history.

## Data sources (all with permission)
| Source | What we take | Years | Permission / credit |
|---|---|---|---|
| **Charles Davis**, Test Match Database (sportstats.com.au) | Test ball-by-ball (about 82% of all Tests) + Test scorecards | 1877 to 2010s | Permission by email (Anadi). Raw files may be shared too. Credit on the Credits page. |
| **Cricsheet** (cricsheet.org) | Test + ODI ball-by-ball | about 2002 to now | Free (ODC-BY): credit |
| **ESPNcricinfo old archive** (i.imgci.com) | ODI/Test ball-by-ball commentary + "player v player" files; old scorecards (who took each wicket) | Oct 1998 to 2002; scorecards 1970-71 on | Permission by email (Anadi), with credit |
| **ESPNcricinfo live site** (www.cricinfo.com, its commentary feed) | The **whole** commentary of every Test and ODI that has it, every item untrimmed; PLUS for every one of 7,692 matches since 1877: ESPN's scorecard (fall of wickets, partnerships, over-by-over, DRS), match facts, and every player (date of birth, batting/bowling style, role) | as far back as ESPN has it (some recreated: RetroLive 2020) | Permission by email (Anadi): up to 5,000 requests/min, credit + send them the site link |
| **The Wisden Book of Test Cricket, Vol. 1** (bought copy) | Numbers only (scorecards, results), cross-checked against Davis | 1877-1970 | Never its pages, layout or text; credit |
| **Kaggle ODI dataset** (bhuvaneshprasad) | ODI scorecards, balls faced, partnerships, fall of wickets; players' bowling style + date of birth | 1971 to Mar 2024 | Credit the uploader (TODO: note its licence from the Kaggle page) |
| Our own lists | nicknames, ground to country (SENA, home/away) | | ours |

Not used: CricketArchive (paid, scorecards only). Cricmetric (no API). Usenet rec.sport.cricket 1991-97 (fans'
over-by-over posts, patchy, manual only). ACS scorebooks (paper). Rob Moody's videos (footage, not data).
Kaggle "subhrajyotinath" ODI ball-by-ball (probably Cricsheet-based; not checked). "Milan Foundation": nothing found.

**Priority when sources overlap:** Cricsheet, then Davis, then the ESPN files, then scorecards. Every match's balls must add up to its scorecard, or the match is flagged.

**Credits:** one Credits page only (in the menu). No footer credits.

## Coverage (12 Nov 2026 night; ESPN's own lists: 2,643 Tests, 5,061 ODIs)
Ball-by-ball, all sources together (our database + ESPN full commentary + redo):

| Decade | Tests | ODIs |
|---|---|---|
| before 1970 | 489 / 669 | - |
| 1970s | 139 / 198 | 0 / 82 |
| 1980s | 203 / 266 | 0 / 516 |
| 1990s | 312 / 347 | 130 / 933 |
| 2000s | 462 / 464 | 1,320 / 1,405 |
| 2010s | 433 / 433 | 1,284 / 1,287 (3 = washouts) |
| 2020s | 266 / 266 | 835 / 838 |
| **Total** | **2,304 (87.2%)** | **3,569 (70.5%)** |

- ESPN added 16 Tests + 486 ODIs that had no ball-by-ball before (incl. ODIs #1722, #1724 from the redo).
- Scorecards: ESPN has one for every match except 8 ODIs; plus Davis, Kaggle, the book.
- Not available anywhere digital: ODI ball-by-ball before Oct 1998 (paper only), most pre-2000 Tests without Davis.
- ESPN labels: shot type + shot control ~85-90% of balls from 2000; line & length ~90% from 2015 (13% 2010-14,
  none before); wagon wheel real mostly recent (0 = not recorded); timestamps 2020+; text on 12% of balls in 2000-04,
  62% 1990s, 80% 2005-09, 96%+ from 2010.

## Stages
1. **Data foundation**: downloader, readers, one database, cross-checks, coverage counts.
2. **The site**: Ask, Legendary Duels, players, top lists, Credits, coverage meter. Render free plan + a pinger
   (UptimeRobot / cron-job.org) so it never sleeps. Then **2b: the weekly robot** (see "Automation").
3. **Context model**: pitch/spin/seam indices from match numbers, pitch tags from match reports, pressure, matchup
   ("expected vs actual") verdicts with ranges.
4. **Research + the model** (trained on Kaggle, see "The model"): estimate head-to-heads for matches without
   ball-by-ball from scorecards (IPF, Bayesian/MCMC, Markov + multinomial), tested on hidden modern matches; paper with
   a professor.

## The model (Anadi's vision, 12 Nov 2026)
Trained on Kaggle (free GPU) on everything we have. What it should do:
- **Line / length / shot for older balls**: learn from 2015+ balls (ESPN recorded both the words and the labels), then
  read the 2001-2014 commentary text ("full and wide outside off, driven") and fill those in. ~1 million more balls.
- **Head-to-heads where no ball-by-ball exists** (e.g. Gavaskar v Imran, Border v Hadlee): use each player's style from
  all their real data plus the hard facts of each match (scorecard, who got out to whom, fall of wickets, partnerships,
  when each batter got out) to estimate e.g. "about 180-230 runs off Imran". Dismissals by bowler are real (scorecards).
- **Predict** line/length tendencies and matchups; **which player struggles where**; later **scout domestic players**
  (needs domestic data).
- **Retrains itself**: the weekly robot adds new labelled balls; a monthly Kaggle run (Kaggle API) trains a new
  version; it replaces the old one **only if it scores better on a fixed hidden test set**; then it re-labels the old
  balls.
- **Always labelled "estimate"** (with a range), never mixed silently with real data. Accuracy shown. This is the
  research paper.
- Not a model job: exact stats (Sachin's runs at the Basin Reserve, SENA splits, fall of wickets). The Ask box answers
  those from the database (Groq writes the query, Sarvam for Hindi); nothing to train, new matches show up at once.

## Automation (Stage 2b)
- **Cricsheet**: GitHub Actions robot, weekly: download new matches, build, run the famous-facts checks, pack, push.
  Render redeploys by itself.
- **ESPN**: GitHub's servers are data-centre computers, which ESPN refuses (the Codespace got 403). So the ESPN job
  runs on Anadi's laptop as a **self-hosted runner**: a hidden browser opens cricinfo.com, borrows Cricinfo's own pass
  maker (see "ESPN commentary feed"), downloads the week's new matches, builds and pushes. Runs whenever the laptop is
  on. (First: a 1-minute test of whether ESPN refuses a GitHub robot; and confirm with ESPN that a small weekly
  automatic update is covered.)
- The robot emails only when something fails (ESPN moved the pass maker, Cricsheet changed format, a new name doesn't
  match).

## ESPN commentary feed: how it works (learned 12 Nov 2026)
- Feed: `https://hs-consumer-api.cricinfo.com/v1/pages/match/comments?lang=en&seriesId=..&matchId=..&inningNumber=N`
  `&commentType=ALL&sortDirection=DESC[&fromInningOver=X]`; ~3 overs per answer; next page from `nextInningOver`.
- Every call needs header `x-hsci-auth-token: exp=..~hmac=..`, made from that exact path by Cricinfo's own code
  (webpack item 764, export `L`, module `akamai_hmac`) and valid 60 s. Our browser scripts borrow that function
  through `self.webpackChunk_N_E` (and search for it if the number changes). Without it: 403.
- Match pages: `/ci/engine/match/<ESPN id>.html` -> `__NEXT_DATA__` (match, hasCommentary, series.objectId,
  innings with players).
- Items keep fields like `commentTextItems`, `commentPreTextItems`, `commentPostTextItems`, `commentImages`, ball
  fields (overNumber, ballNumber, oversActual, runs, extras, isWicket, dismissalType, batsmanPlayerId,
  bowlerPlayerId, pitchLine, pitchLength, shotType, shotControl, wagonZone...). The reader must be written from a
  **survey of all files** (every field, every item type, counts by year), not from samples.
- Speed: speed = doors x (60 / seconds per request), capped by the smallest of: our limit, the line, Chrome (~100 at
  once to one site), the laptop (keep the tab **visible** and RAM free: Chrome slows hidden tabs). 80 doors for 5,000/min.

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
- **Growth is small:** ~150-200 new Tests/ODIs a year = ~30 MB of new raw files and ~20 MB of new commentary text a
  year. Releases are enough (no limit on total size; 2 GB per file; new raw files added as weekly/monthly assets).
  No hosted database (Neon etc.) needed: their free tiers are smaller than our database, and a database file the site
  downloads at deploy is free and fast.
- Finished database: today in the repo (data/web, 773 MB -> 95 MB xz). **Move it to a Release** before the weekly robot,
  or every update adds ~100 MB to the code history. Render's build downloads it from the Release (token in Render env).
- Expected size with all commentary: ~1.5-2 GB plain (text ~0.4 GB + search index). Render free: 512 MB RAM (fine,
  SQLite reads from disk), 0.1 CPU (so heavy counting is done at build time), disk size not published -> test it.
  Plan B: commentary in a private Hugging Face dataset (100 GB free), fetched per match when opened.
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
- [x] **12 Nov 2026: catching what's missing.** Browser check of ESPN's site for all 2,333 matches without our
  ball-by-ball: 515 have commentary (40 old ODIs: RetroLive recreations + Cricinfo's own 1999-2002 live text; 0 old
  Tests). `everyball_espn_commentary.js` (trimmed fields) downloaded 502 of them: 320 add up exactly, 109 within 1-5
  runs, 28 with gaps, 45 brand-new 2024-26 matches. 13 came back empty. Davis dead-link recheck: 0 found so far.
- [x] **12 Nov 2026: the WHOLE ESPN commentary downloaded** (`everyball_espn_full_commentary.js`, 80 doors, up to
  5,000/min): 66 files `espn_full_commentary_part001..063.json.gz` (+ three "(1)" copies made by a resume; their 240
  records are exact duplicates), 700 MB zipped, 11.7 GB raw. 7,692 matches: 4,517 with commentary, 3,175 none.
  Kept on Anadi's laptop: Downloads\ESPN full commentary\. **The script itself is NOT in the repo yet: copy it into
  scripts/browser/.**
- [x] **12 Nov: survey of every file and every item** (`scripts/tools/survey_espn_live.py`): see docs/ESPN_LIVE_SURVEY.md.
- [x] **12 Nov: redo of 20 matches** (`scripts/browser/everyball_espn_redo.js`, ids checked against ESPN's numbers):
  the 12 the full run missed (Tests #1548-1549, ODIs #1720-1729) + 8 played ODIs that came back empty. Result
  `espn_full_commentary_redo.json.gz` (1.3 MB): 12 with commentary (9,451 items), 8 still empty (ESPN never wrote any;
  scorecards + players kept). Runs add up in 31/32 innings, legal balls in 29/32.
- [x] 12 Nov: Davis dead links re-checked: really dead. Closed.
- [x] 12 Nov: repo zip reviewed. Repo had an older PLAN.md (this file replaces it).

## Data verdicts (12 Nov 2026: audit of the three "Database inspiration" zips + the ESPN survey)
Rule: "remove" = not in the database; it still stays in the raw files forever. Nothing is deleted.

**Raw data we have but the database does NOT use yet (must be added):**
- Book `fall_of_wickets.csv` (20,746 rows, 1877-1970), `debuts.csv` (1,212), `appearances.csv` (14,805: every XI),
  `players.csv` (1,428: first/last Test). Only matches/innings/batting/bowling are read today.
- **Davis (full archive checked 13 Nov: 10,719 files, 1.29 GB).** Read today: Test pages (3,084), ball-by-ball
  (2,008), series covers (~742). **Never read:** fall-of-wickets PDFs (2,020), session-by-session PDFs (~742), series
  averages (~742), Davis's notes ("text", 80), series/era lists and index PDFs (~740, e.g. "10.pdf"), series web
  pages (553), specials (1893field, 1975WI3time, hanif337, "A Note on Score Reconstruction"). Read them all (credit).
- ESPN `_index` (50,496 pages, 1.44 GB): the downloader's folder/navigation pages, incl. domestic matches, squads and
  picture pages. Raw only, except squads/tour pages if they add players we lack (check once).
- Kaggle `odi_Fow_Card.csv` (67,963 rows) + `odi_Partnership_Card.csv` (73,826 rows): never read today.
- ESPN old archive: **1,226 match reports (MR)**, 573 run-rate comparisons (over-by-over, 1990s-2002),
  224 wagon-wheel pages (scoring zones 1999-2002), 527 graph pages and 25 BBB pages (check what they add).
- Cricsheet: DRS reviews (`review`), concussion/other replacements, player of the match, absent hurt,
  miscounted overs.
- ESPN live: everything (balls, labels, all text, scorecards with fall of wickets/partnerships/overs/DRS, players).

**GOLD (keep and use, no choice):** every ball's facts from every source (every version, disagreements kept);
every piece of commentary text (ball lines, before/after text incl. lunch chatter and interviews, over summaries,
dismissal text, old BBB-COMMS lines, match reports); every scorecard from every source; fall of wickets and
partnerships from every source; ESPN labels (line, length, shot, shot control, real wagon wheel, dismissal type);
players (date of birth, styles, role, country, death date, every name form, every id); match facts (toss, result,
ground, umpires, referee, player of the match, debutants, replacements, DRS, absent hurt).

**KEEP (useful, lower value; in the archive, used when needed):** over-by-over (ESPN live `inningOvers`, old
RR-COMPARISON), minutes batted, ball timestamps (2020+, for sessions), hours/day-night/series/commentator/scorer
names, ESPN's win `predictions` (2020+; ESPN's own model, always labelled "ESPN estimate", never mixed with facts),
tweets/polls embedded in commentary (their text + link), old GRAPH pages (only if they hold numbers we lack).

**REMOVE from the database (raw files only):** player/team images, captions, credits, sizes, logos, colours; old
AUDIO/PICTURES/IMAGES/SLIDESHOW pages; video metadata (playback links, expiry, genre, country codes), live-stream
and ad/iframe/Playbuzz/Instagram/Facebook embeds; site internals (slugs, scribe ids, page flags like hasGalleries,
fantasy story ids, languages, "modified" times); the 240 duplicate ESPN records.

**For the model:** all text is used, but every piece keeps its kind (ball line / before / after / over summary /
report), so the line-length-shot labeller learns from ball lines while insights, pitch and weather talk feed the
"conditions" and duel (Dravid v Steyn) models. **Promo vs gold: decided from the real data (12 Nov, every one of the 831,497 between-ball pieces checked)**
- The most repeated between-ball lines are GOLD, not promo: "Around the wicket" (635 matches), "Two slips and a
  gully", "Drinks", "Last over before lunch", "Free hit". They give bowling angle and field changes for the next balls.
  So "repeated = promo" is wrong and is NOT used.
- Promo is tiny and mostly one-off: only 4,637 pieces (0.56%) even contain promo words, and many of those are gold
  ("if Warner and Starc click..."). Word rules alone would delete gold, so they are NOT used either.
- 13,710 pieces talk line/length/what to bowl (the "yahan tappa pakdo" insights): always kept.
**Final rule (Claude's call, 12 Nov, after Anadi's push): nothing with words is ever deleted.**
1. Dropped: only empty shells with no words at all (video players, live-stream links, iframes, empty pieces).
   Polls and tweets keep their text.
2. Promo lines (~4,600 candidates, <1 MB) are NOT deleted: an AI judges each one and tags it "promo"; the site hides
   them and training skips them. Deleting them would save almost nothing and risk real cricket talk.
3. Every piece is kept and tagged by kind: ball line / before / after / over summary / dismissal / angle or field
   change / match state (drinks, lunch, tea) / reader comment ("Andy: ...") / tweet / poll.
4. Count check per match: raw pieces = stored + dropped shells, or the build stops.

## Cricket factors (labels) from the commentary and other sources (12 Nov, Anadi's "everything matters")
Every label keeps where it came from (ESPN field / exact text rule / AI with a confidence / measured weather) and
never overwrites the original. "Not mentioned" is stored as unknown, never as "no".
- **Delivery:** line, length (ESPN 2015+, AI from text before), pace/spin, variation (bouncer, yorker, slower ball,
  cutter, knuckle, googly, doosra, carrom, arm ball), movement (inswing, outswing, reverse, seam in/away, turn in/away),
  bounce (extra, low, uneven), speed when said ("142kph": exact rule).
- **Shot:** type and control (ESPN), front/back foot, direction (wagon), edges (inside/outside/top), beaten,
  left alone, mistimed, appeal, DRS and its outcome (umpire's call).
- **Field and angle:** over/around the wicket, slips and gully count, field changes (sweeper, leg-side field,
  packed off side), dropped catches, misfields, fielder positions named.
- **Conditions:** pitch (green, dry, cracks, turning, two-paced, flat, footmarks), weather (overcast, sunny, humid,
  rain, bad light), **dew**, **wind (direction and strength)**, ball (new, old, reverse, ball change), session,
  floodlights.
- **Tactics and pressure:** bowling plans (outside off, packed off side, short-ball plan, wide yorkers, leg theory),
  batting intent, dots in a row, required rate, partnership stage, injuries.
- **Measured weather (new source to add):** free historical weather archives (e.g. Open-Meteo, back to 1940) give
  temperature, humidity, wind speed/direction and rain for each ground and match day: measured (reanalysis) values,
  labelled as such, for matches with no commentary too.
- **Match reports** (ESPN old MR 1,226 + ESPN live report stories) add pitch and weather per match for older games.
- Exact rules (code) for: speeds, over/around the wicket, slip counts, drinks/lunch/tea, DRS outcomes. AI (labels
  a sample, a small model learns the rest, checked on Anadi's 500 hand-labelled lines) for meaning: pitch behaviour,
  movement, tactics, wind and dew talk.
- The model uses these as factors: e.g. "wind towards leg side + right-hander + leg-side boundary" -> the duel and
  conditions models learn what bowlers did and what worked.

**Training data (the model sees only clean, verified data; nothing is deleted to get it):**
- Balls: Cricsheet; Davis balls marked proven (98.6%); ESPN balls in innings that add up to the scorecard.
  Unproven or disagreeing balls stay in the database, flagged, and are left out of training.
- Labels: ESPN line/length (2015+, ~90%) and shot/control (2000+, ~85-90%) are the targets; ball lines are the input
  text; angle/field changes become features for the following balls; expert insights feed the conditions model;
  reader comments and promo candidates are not used for training.
- Scorecards, fall of wickets, partnerships (Davis, book, Kaggle, ESPN): the version that adds up, for the
  no-ball-by-ball estimates and the conditions number. Players: ESPN dates of birth and styles, checked with Kaggle.
- Every change to the training data is kept only if it scores better on the fixed hidden test set.

**How commentary is sorted (decided 12 Nov):** code, not an AI, stores and sorts every piece, because ESPN's own
fields already say which ball, which kind (ball line / before / after / over summary / dismissal) and which type
(HTML / tweet / poll / video): exact, nothing guessed, nothing dropped. A count check proves it: for every match,
pieces in the raw files = rows in the database, or the build stops and flags the match. An AI is used only where
meaning is needed (promo tags, and line/length/shot for older balls in Stage 3), and its answers go into **separate
label columns with a confidence**; it never edits or removes the original text. Reading all ~5 million pieces with an
AI (~100 million tokens) is too slow and costly, and an AI can silently drop or change words. 2000-04 has text on only 12% of balls.

## Next steps (START HERE: open START_HERE.md first - it has the push command, what to give Claude, and the prompt to paste; resume here after the CIEs; Anadi, 12 Nov: do steps 3-7 as ONE build, not in stages)
0. **Release layout (all 18 parts checked 13 Nov; 68,229 files):** raw-data = part-0001..0018.zip (~1.45 GB; part 1 = Cricsheet + full Kaggle +
   part of Davis; the rest = Davis PDFs + ESPN old archive incl. its index pages). Copying all 18 from the laptop
   takes ~1 hour on PG WiFi, so for the build either link GitHub (read only) or copy them in one sitting.
0. **Before the build:** Anadi puts the original Kaggle zip (as downloaded from Kaggle) in
   `Downloads\Database inspiration\` next to the three for_claude zips. Also the FULL Davis folder (all ~10,700 files; the
   for_claude_davis zip has only 6,532 pages as text), packed in the Codespace into parts under 350 MB:
   `cd /workspaces/Everyball/data/raw && tar czf - davis | split -b 350m - /workspaces/Everyball/davis_raw.tgz.part_`
   (download each part, put them in the same folder, then delete them from the Codespace so they never reach GitHub).
   Cricsheet (full), ESPN old archive, ESPN year lists and the book CSVs are already in the for_claude zips. With those + the ESPN folder, Claude has
   every raw source and runs and tests the WHOLE build in its own workspace first (tests on known matches, every
   reader on every file, counts in = out, sums, source-vs-source, famous facts, then fix every flag and run again).
   Only then does the zip go to the Codespace for the real build.
1. **Commit this save zip** (PLAN.md, docs, scripts) + copy the full-commentary .js from the laptop into scripts/browser/.
2. **Get the 67 commentary files into the Codespace**: drag them onto the Releases page (`raw-data`), then
   `gh release download raw-data -p 'espn_full_commentary_*' -D data/raw/espn_full/`. (Release = permanent copy +
   backup; the laptop must not be the only copy.)
3. **Data audit ("field map")** (first pass done 12 Nov from the samples: see "Data verdicts"; Kaggle's extra CSVs still to list in the Codespace): for every source (Davis, Cricsheet, Kaggle, book, ESPN old, ESPN live) list every
   field in the raw files and where it lives in the database, or why it is raw-only. Known gaps already: Davis fall of
   wickets (parsed, not saved), Davis session pages, Kaggle FoW + partnership CSVs (not read), ESPN live (not read yet).
   Output: docs/FIELD_MAP.md + a "raw vs database" count report per source.
   **Small leftover found 13 Nov (the player-id joining works):** the real Glenn McGrath record holds all his
   51,426 balls and 915 bowling rows; but 3 stray name records remain: "W W GD McGrath" and "GD McGrath." (empty)
   and "McGrath" (30 Davis balls not joined). **The missing piece is match context:** the ESPN files give every
   match's XI with ESPN ids (7,692 matches, 1877 on). So every name from every source is joined by "which player of
   this team, in this match's XI, has this surname/initials", not by the name alone ("McGrath" bowling for Australia
   in a Test where Glenn McGrath is in the XI = him). Readers also clean names first (strip wicket marks "W W",
   trailing dots). After joining: empty records are removed; anything still unjoined is listed in the report, never
   guessed.
   Cross-check that worked: Sachin v McGrath ball by ball, ESPN vs our database, exact in 14 of 15 matches.
4. **ESPN live reader** (`read_espn_live.py`): balls (labels: line, length, shot, control, wagon, timestamp), every
   piece of text stored exactly as sent (HTML kept; a cleaned copy for search), kinds: ball line / before / after /
   over summary / dismissal; tweets, polls, videos as type + link. ESPN scorecards as a 4th scorecard source; players
   joined by ESPN ids (DOB, styles, roles). Balls lined up with Cricsheet/Davis (same over, ball, bowler, batter, runs);
   disagreements kept and flagged, never overwritten.
5. **New tables**: fall_of_wickets, partnerships, overs, drs_reviews, commentary (full), ball labels; from every source
   that has them (ESPN, Davis, Kaggle, book, computed from balls).
   Anadi asked (12 Nov): read **Kaggle's fall-of-wickets and partnership CSVs** and **save Davis's fall of wickets**
   (the Davis reader already parses it). Both raw sets are in the Release / Codespace.
6. **Two databases (proposed 12 Nov, confirm with Anadi):** (a) the **full archive** = every field of every source,
   every version where sources disagree, all text exactly as sent; lives in a Release (~2 GB plain, a few hundred MB
   packed), used for research, the model and rebuilds; (b) the **website database** = only what the site and the Ask
   box use (chosen scorecards and balls + labels, fall of wickets, partnerships, players, commentary), slim enough for
   Render. Raw-only items (image links, video metadata, ad embeds) stay in the raw files with a written reason.
7. Rebuild in the Codespace, checks + famous facts, measure size; DB to a Release; deploy to Render (+ pinger); send
   ESPN the link.
8. After the CIEs: Stage 2b robot (Cricsheet weekly; ESPN on a self-hosted runner), then the model.

## Still missing (keep this list current)
- Test #1541 (WI v SA, Mar 2001): no ball-by-ball anywhere yet.
- ESPN "commentary flag but nothing": 6 washed out after the toss (ODIs #5061, #4971, #4098, #3890, #3584, #2141:
  nothing to get) + 8 played with no commentary ever written (ODIs #2784, #2722, #2727, #2505, #2511, #2516, #2400,
  #1364): scorecards + players kept.
- 8 ODIs with no ESPN scorecard (other sources cover them; list them in the audit).
- The ESPN full-commentary browser script and the Kaggle licence note: not in the repo yet.
- ESPN match reports (pitch tags, Stage 3): not downloaded.
- Pre-2001 Tests/ODIs without ball-by-ball: only estimates possible (the model), never real.

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

## Video reconstruction ("Restore from TV", idea #13) - 13 Nov 2026 night
- Method that works: Google AI Studio (free) + Gemini 3.8 Flash, YouTube link, clip by start/end, FPS 2, agentic off, grounding/URL context off, ~9 overs per chunk. Settings + prompt: `scripts/video/prompt_template.txt`. Free daily quota runs out after ~3-4 chunks.
- Checker: `scripts/video/check_bbb.py` replays every ball with the scoring rules (odd runs swap strike incl. byes/leg-byes/runs on wides; wides not legal and not faced; no-balls not legal but faced; 6 legal balls per over then swap; bowled/lbw/stumped = new batter on strike, caught+crossed (pre-2022) = non-striker, run outs by hand; unseen balls still count, with known hidden runs). It stops on any mismatch with score box / scorecard.
- ODI #1065 (1996 WC, Mumbai, India v Australia): India overs 1-18 done -> `data/video/odi1065_india_overs1-18_checked.csv` (status per line: video 88, corrected 8, reconstructed 6, not shown 10, uncertain 1, dropped 1). Matches every checkpoint: 7/1, 7/2, 13/2 (7 ov), 30/2 (9), 40/2 (10), 70/3, 89/3 after 18 (confirm with next chunk's first line). Raw Gemini output kept next to it. Next: overs 19-27, start 3h25m, end 4h00m (Sachin st Healy b ME Waugh at 143/4). Overs 1 & 10, 11.1-11.2, 17.3-17.4 are not in the video. Loose end: one record says Sachin's 50 came off 43 balls; TV count gives ~45.
- Accuracy test (2011 WC final, India overs 1-10 vs the real ball-by-ball): 51/61 balls exactly right (84%); over totals 10/10; wickets and bowlers 100%. Errors: runs put on the neighbouring ball, one missed ball, a leg-bye called bat runs; Gemini marked 8 of 10 wrong balls "high" (its confidence is not reliable). The checker would catch the missed ball and the shift (balls faced) -> ~90% after checks. Agentic-mode test still pending (needs a trimmed video file under the token limit).
- Website label for these matches: "rebuilt from TV video, checked against the scorecard". Never mix with original ball-by-ball without the label.
- Better pipeline (to build later, Windows/Codespaces, free): ffmpeg 1 frame/s -> OCR the score box (Tesseract or Windows OCR) -> every change = one ball with EXACT runs/wickets/overs; delivery moments by matching the bowler's-end camera shot; faster-whisper transcript; then send Gemini only small per-over "ball cards" (3 frames + transcript per ball) for bowler/what happened/bat vs leg-bye -> far fewer tokens; checker; human watches only the flagged balls (auto-cut 10 s clips). Only for videos we are allowed to use.
- YouTube official search API (free key in `.env` as YOUTUBE_API_KEY, ~100 searches/day): find full-match uploads (>2 h) for matches without ball-by-ball; sample 100 first, then India's matches.

## Facts found 13 Nov 2026 (for the build)
- One row per innings: batting/bowling hold a row per source. Summing all sources double-counts (McGrath Tests 53,000 balls); picking Cricsheet > Davis > ESPN gives exactly the official 29,248 balls / 12,186 runs / 563 wkts (ODIs 12,970 / 8,391 / 381).
- Era rules in checks: before the mid-1980s wides and no-balls were not charged to the bowler (with today's rule the 1972/73 Eng-in-Pak Tests showed 17 false mismatches; with the era rule 2).
- Official career totals (Statsguru-style) are a CHECK, not a source: compare every player's computed totals; any difference = missing/wrong match.
- Davis coverage: 1970-77 series with England or Australia are complete (MCC/ACB tour scorebooks), others mostly not. 336 Tests have no ball-by-ball anywhere: `data/lists/tests_without_ball_by_ball.csv` (1950s 61, 60s 49, 70s 59, 80s 62, 90s 35, 2000+ 0). Davis is still active (latest post 28 Oct 2026) but adds ~1 classic at a time.
- ODIs: 5,029; with ball-by-ball 3,538 (DB + ESPN download); without 1,491 (5 abandoned). Before 2005: 1,425 (70s 82, 80s 516, 90s 803, 2000-04 24); 2005+: 61 (Kenya, Bermuda, Canada, Netherlands...). Priority for video: India 381 (India v Pakistan 69, India v Australia 50).
- Desert Storm 22 Apr 1998 (ODI #1325): no ball-by-ball anywhere (video candidate). The final 24 Apr 1998 (#1327) has ESPN commentary.
- Duels checked from data: Dravid v Warne (Tests, 13/13 bbb: 609 balls, 244 runs, 8 outs); Sachin v McGrath Tests (286 balls, 88 runs, 6 outs) and ODIs (202 balls + 16 from the 1996 video); Gavaskar v WI quicks (1 of 27 Tests bbb; dismissals from scorecards: Holding 11, Marshall 8, Roberts 3, Garner 2, Croft never met); Lillee 69/70 Tests bbb (Knott out 12 times); Donald 70 Tests bbb; Border v Marshall/Holding/Hadlee/Imran/Garner (Tests bbb, 1980s ODIs none).
- YouTube finder BUILT (13 Nov): `scripts/tools/youtube_search.py` (stdlib only). Reads `data/lists/missing_matches.csv` (1,822 matches without ball-by-ball: priority 1 India ODIs 381, 2 other ODIs 1990+ 675, 3 ODIs before 1990 430, 4 Tests 336; newest first). Official YouTube Data API, key from `.env` YOUTUBE_API_KEY (never printed), ~95 searches/day, resumes from `data/lists/youtube_progress.json`, writes `data/lists/youtube_found.csv` (verdict full_match / full_innings / extended / short + how_sure). Guards: both teams + year in title = high; one team + year or year + city = medium; a title naming another match's city of the same series is rejected. Tested offline with a fake API (resume, quota stop, series clash).
- Verdict on highlights (13 Nov): standard highlights = no; extended highlights = not for stats (biased toward boundaries/wickets) - at most a "key moments" layer for iconic matches; full innings = yes. 40-match YouTube sample (India ODIs before 2005): 0 full matches, 2 full team innings, 2 every-ball cuts of one star innings, 10 extended, 10 short, 16 nothing (`data/lists/yt_sample20_results.csv`). Video can rebuild tens of matches, not hundreds.
- YouTube finder v2 (13 Nov, after the first real run of 95 India ODIs): v1 matched too loosely (259 of 489 videos were given to several matches; Test videos given to ODIs; one Sharjah clip to 9 matches). v2 rules: reject titles naming a team not in the match, Test-vs-ODI mismatch, no year, another match's city, a different tournament (when ours is known, from Kaggle series names), a different stage (final/semi/quarter) or match number (from Kaggle match names); bonus for city, tournament, stage, match number and any score/total from our scorecard in the title (`numbers` column, e.g. "143(131)", "Chase 316"). `--review` rebuilds `data/lists/youtube_candidates.csv`: each video given to the one match it fits best (ties marked ambiguous). On the first 95: 98 candidate videos for 60 matches; real gems: ODI-1227 Sahara Cup 1997 game 1 full match (6h25m), ODI-1209 Anwar 194 (117m), ODI-1325 Sachin 143 every ball (19m), 1998 Independence Cup final full innings (62m, which final to confirm).
- Bradman (Tests): 52 Tests, 46 with ball-by-ball (missing: 1930-31 v WI x3 found missing + 1931-32 v SA x3); 10,078 balls / 5,904 runs ball-by-ball. Checks against folklore pass: Verity dismissed him 8 times (930 balls, avg 49.8), Bedser 6 (avg 43.7), Larwood 5 (SR 78 - he scored fast even vs Bodyline).

## Cricket rules + video overs 1-30 (13 Nov 2026, evening)
- docs/CRICKET_RULES.md: every scoring rule by era, with sources (overs 4/5/6/8 by country and year; 1980-Code
  no-ball/wide penalty only if no other runs, +1 plus runs from 30 Sep 1998, byes off a no-ball = byes from Oct 2017;
  wides/no-balls charged to bowlers 1983-1992 by country; caught & crossed before 1 Oct 2022; free hits 2007/2015;
  dismissals allowed off wide/no-ball; Mankad, runners, concussion subs).
- scripts/video/rules.py + check_bbb.py: replay any ball-by-ball file under the rules of its date; keeps both
  possibilities when the rules allow two. The build's ball replay (BUILD_SPEC 22.4) must use rules.py.
- make_pack.py now takes the whole video: --start 2:22:00 --end 4:20:00 = India overs 1-30 of ODI #1065; --probe
  tests OCR in 1 minute. Not run on the real video yet.

## Video rebuild - final method (14 Nov 2026, 2 am)
Tested on ODI #1065 India innings (1996 WC), measured against our checked overs 2-9:
- Gemini on video alone: 84% / Gemini on photo PDF: 31% / Gemini on video + Tesseract facts: 76% (bowler 51/51, wickets 51/51)
- MERGE (scripts/video/merge_bbb.py): 82% exact, bowler 50/51, striker 48/51, wickets 50/51. Remaining misses = balls the TV
  showed unclearly (start of over 2, a leg-bye 4 read as a bat 4, a no-ball read as a wide).
Pipeline per innings:
 1. make_pack.py (FFmpeg 1 photo/s + Tesseract) -> events.csv: exact runs/wickets/overs per score change (--from-readings to redo events in seconds)
 2. Gemini on the VIDEO in ~35-min parts, prompt = events list as facts (prompt_partN.txt) -> bowler, wicket type, extras type, shot
 3. merge_bbb.py: runs from Tesseract, bowler = Gemini (one per over), striker = cricket rules (rules.py), Gemini only where rules can't decide
 4. check_bbb.py (rules) + scorecard; compare.py where a truth exists
Next: Gemini parts 2-3 for #1065 -> merge overs 1-30 -> check vs scorecard. Optional: Whisper (audio, timestamps) as backup for bowler/striker.
Realistic target: 20-50 full innings (full broadcasts of 1990s ODIs are rare on YouTube); ~1-1.5 h Codespace per innings.
Park video after this test; v1 build (gold layer, ESPN notes, website) comes first.
- 14 Nov 3:30 am, all 30 overs merged (data/video/odi1065/merged_all.csv): overs 2-18 vs truth 79% exact, bowler 94/97,
  striker 89/97, wickets 94/97. All 30 bowlers fit the scorecard (Lee exactly 3 overs, 24 runs vs card 23). Sachin st Healy
  b Mark Waugh off a wide in over 30 found. OPEN: runs placement - merged total after 30 overs 125 vs real ~142:
  10 score changes not lined up with Gemini lines. Next fix: build the ball list from the Tesseract skeleton itself
  (one row per score change), take only bowler / extras type / wicket type from Gemini.

## Gap-filling solver (CP-SAT) - built 15 Nov 2026 (scripts/solver/)
- rebuild.py + cricket_facts.py + db_load.py. Hard Laws (over length, wides/no-balls as extra deliveries, one
  bowler per over / never two in a row, crossing on odd runs run, ends change each over, batting order, which
  dismissals are possible off wides / no-balls, striker-only dismissals, caught-crossed before 1 Oct 2022, run-out
  ends, "b X" bowled it, all-out last ball, era rules for wide / no-ball penalties and byes off no-balls, extras to
  the bowler from 1983). Soft facts (kept unless they contradict each other, then reported): total, extras,
  batter R/B/4s/6s, bowler balls/runs/maidens/wd/nb/dots/4s/6s, FoW score + over, partnerships (+ each batter's
  share), video anchors, over-by-over. If the FoW order is unknown the solver also decides who fell when.
- Verified: on 8 full real innings the true ball-by-ball satisfies every model rule with 0 broken facts (no rule
  is too strict); DB + Kaggle facts for O3148 equal the facts from the real balls; the replay checker re-checks
  every rebuilt innings. Not modelled (refused): retired / timed out, mid-over bowler change, short runs.
- Next: python3 scripts/solver/rebuild.py test20 in Codespaces -> data/solver/test_results.csv (accuracy by level).

### Stage 1 leftovers (17 Nov 2026, Anadi: keep in plan)
- 182 ESPN-live matches added as "partial" (sure=0). Checked: 148 are only 0-1 ball short or a few runs off ESPN's own
  scorecard (small ESPN-side slips). 34 have real gaps: a few innings miss a whole block at the start (overs 1-17,
  1-20) = likely the download stopped paging early -> re-download just those matches first; the rest miss single or
  alternate overs (one bowler's overs) = gaps in ESPN's own feed -> rebuild the missing overs with the CP-SAT solver
  (fixed balls kept, only the gaps solved, scorecard + fall of wickets as facts). Report: data/reports/espn_live_added.csv.
- 51 look-alike players not merged (data/reports/players_merged.csv). Two kinds: (a) real separate old players with no
  ESPN id (Imtiaz Ahmed, Maqsood Ahmed, Kripal Singh, Hanumant Singh, Ali Bacher...) -> keep, just attach their ESPN id;
  (b) true duplicates written differently (Salim/Saleem Malik, Magsood/Maqsood, bare "Silva", "Singh", "Ahmed") ->
  settle match by match from a scorecard that carries ESPN ids (old ESPN archive / Kaggle player ids), by team +
  batting position. Whatever is still unsure (expected under ~10) goes to Anadi as a short list with ESPN links.

## YouTube finder (14 Nov 2026, 5 am)
- 15 Nov: SPEEDOMETER TEST replaces the 3-window Gemini check (scripts/tools/youtube_speedo.py). Gemini only READS
  the score box at 8 two-second clips (one request, ~1,500 tokens vs ~140,000); our code measures balls per minute of
  video: <=3.2 every ball, 3.2-10 extended, >10 short highlights; mixed = partial with the overs covered. Misread
  snapshots dropped (chain must not go down), innings split on reset/team change, teams must match. Test first on 3
  known videos (--test). Extended highlights are KEPT as anchors only (exact balls at exact positions), never used
  for stats (they over-show boundaries); the gaps are filled by the solver + model (part E), every ball labelled
  real / forced / estimate. One key only (YouTube policy: no multiple projects for one service). Free tier: Google
  caps YouTube input at 8 hours of video per day - if clipped requests count the whole video, use the paid tier
  (all ~1,500 videos for well under Rs 100 in tokens) or --mode sweep.
- youtube_search.py: no daily cap (stops when YouTube says quota used up); ODIs only by default; fixed: "only test"
  videos and "highlights" titles no longer counted as full ODIs.
- youtube_channels.py: lists every upload of cricket-archive channels (1 unit / 50 videos). First run: 226 channels,
  ~19,000 videos read. YouTube lists only the newest 20,000 uploads of a channel (Star Sports has 35,758).
- First real result: ~20 full-match ODI videos (1987 WC final, 1992 WC x5, 1996 WC x8, Sahara Cup 1997, ...).
- youtube_verify.py: Gemini API watches 3 x 8-min windows of each candidate straight from the YouTube link
  (no download) -> every_ball / partial / highlights / wrong_match / unclear (data/lists/youtube_verified.csv).

## Two engines for matches without ball-by-ball (keep both in the plan)
- The SOLVER (Google OR-Tools, free): hard facts only - scorecard, fall of wickets, partnerships, bowler figures,
  ESPN match notes ("first 3 overs maidens", "98/2 at drinks") - lists every ball order that fits ALL facts.
- The MODEL (Kaggle): picks the most likely of those orders, learned from the ~4,500 matches with real ball-by-ball.
  Every ball labelled: real / forced by facts / model estimate.
## What the ESPN commentary is for (with or without the model)
1. A ball-by-ball SOURCE: each ESPN ball already has bowler, batter, runs, extras, wicket as numbers - no AI needed.
2. The words, shown untrimmed on match pages and searchable.
3. Later: words -> numbers (length, line, shot, e.g. "short, pulled for four" -> short / pull) with keywords + AI,
   to show "who struggles where" and to train the model.
