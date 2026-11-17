# Everyball: the complete build specification (written 13 Nov 2026)

**Who this is for:** any new Claude chat (or any person) that builds Everyball. Read PLAN.md (rules, decisions,
progress), docs/ESPN_LIVE_SURVEY.md (how ESPN's full commentary files are written), then this file, all of it.
Everything below was checked against the real files on 12-13 Nov 2026; numbers are counts, not guesses.
If something here disagrees with PLAN.md or with Anadi, ask Anadi, then fix both files.

Contents: 0 working rules · 1 the goal · 2 every raw source, file by file · 3 getting the raw data · 4 the two
databases and every table · 5 readers, one per source · 6 joining people · 7 choosing between sources · 8 commentary
· 9 labels · 10 weather and grounds · 11 checks and the report · 12 the website · 13 storage and hosting ·
14 the weekly robot · 15 the model · 16 the build, step by step · 17 definition of done · 18 known facts to test ·
19 risks and limits · 20 open items

---------------------------------------------------------------------------------------------------------------------

## 0. Working rules (Anadi's; never break them)
- Build only after Anadi says "build". Explain first, plain simple language. Commands: few copy-paste blocks
  chained with &&. No Codespaces Ports/localhost preview; deploy straight to Render.
- **Claude never pushes, commits or changes anything on GitHub.** Read/download only, when access is given.
- Keys (Groq, Sarvam, GitHub, Hindsight) never on GitHub, never pasted in chat: Render env / GitHub secrets / .env.
- Raw data never in git history (Releases are fine). Never write "hackathon" in public content.
- Never present made-up data as real. Estimates live in their own labelled place, with ranges.
- Every piece of data that exists is caught. **Nothing with words is ever deleted.** Nothing dropped silently.
- Anadi does not check data by hand: Claude sorts the report, fixes what visitors would see, logs the rest, and asks
  Anadi only for decisions (5-10 per build at most).
- Respect sources: Davis up to 300 req/min (credit); ESPNcricinfo up to 5,000 req/min (credit "Commentary:
  ESPNcricinfo" next to commentary, credit page, send ESPN the site link when live); Wisden book: numbers only.
- One Credits page (in the menu), no footer credits. Men's Tests and ODIs only; **no T20s** (Anadi: T20 is not the
  real format of the game for him, integrating it would be a headache, and Tests + ODIs are already huge).
- Steps 3-7 of PLAN.md "Next steps" are done as ONE build, not in stages.

## 1. The goal in one paragraph
Every men's Test since 1877 and every ODI since 1971, ball by ball wherever it exists, with every word of ESPN's
commentary, every scorecard from every source, fall of wickets and partnerships for every innings, every player's
birth date and styles, conditions (pitch, weather, dew, wind) and tactics labels, a site that answers any question
in plain English/Hindi from the database (never invented numbers), Legendary Duels, and a model (Stage 3-4, the
"Dravid v Steyn" model) that estimates what was never recorded, always labelled as an estimate with a range.

---------------------------------------------------------------------------------------------------------------------

## 2. Every raw source, file by file (68,229 files in the release + the ESPN live files)

### 2.1 Charles Davis, Test Match Database (sportstats.com.au) - 10,719 files, 1.29 GB, 1877 to Nov 2020
Folder `data/raw/davis/zArchive/<era>/<series folder>/`. Series code = year + teams, e.g. `1886AE` (1886, A v E).
| Kind (file name) | Count | What it is | Read today? |
|---|---|---|---|
| `1886AE1.pdf` (Test page) | 3,084 | scorecard of Test n of the series (incl. "Scores of 50+" pages, e.g. `1903AE50.pdf`) | yes |
| `1886AE1bbb1.pdf` | 2,008 | ball-by-ball of the Test | yes |
| `1886AE1fow.pdf` | 2,020 | fall of wickets of the Test | **no** |
| `1886AEsess.pdf` | ~742 | session-by-session scores | **no** |
| `1886AEav.pdf` | ~742 | series averages | **no** (cross-check only) |
| `1886AEcov.pdf` | ~742 | series cover page (+ player list: name, born, age, career, role) | yes |
| `1886AEtext.pdf`, `...text1.pdf` | 80 | Davis's notes on the series / reconstruction | **no** |
| era/index PDFs (`10.pdf`...), `1920sseries.pdf`, milestones (`50partnmilestones19711999.pdf`) | ~745 | indexes, lists | **no** |
| series web pages `.html/.htm` | 553 | series pages | **no** |
| specials: `1893field.pdf`, `1975WI3time.pdf`, `hanif337.htm`, `ANoteonScoreReconstruction.htm` | few | special pages | **no** |
Davis covers ~2,390 Tests in 747 series. Today's database: Davis scorecards for 2,337 of 2,341 Tests in his period;
ball-by-ball for 2,001 (98.6% of balls proven against over totals). Some old ball-by-ball is reconstructed from
newspapers (`matches.confidence = 'reconstructed'`).

### 2.2 Cricsheet (cricsheet.org, ODC-BY) - 4 files, 40.7 MB
`tests_json.zip`, `odis_json.zip` (one JSON per match), `people.csv` (18,698 people; identifier, name, unique_name,
key_cricinfo, key_cricinfo_2, key_cricinfo_3, key_bcci, key_cricbuzz, ...), `names.csv` (7,551 name variants).
Match JSON: `info` = balls_per_over, city, dates, event, gender, match_type, match_type_number, officials,
outcome, player_of_match, players, registry, season, team_type, teams, toss, venue; `innings[]` = team, overs,
declared, absent_hurt, miscounted_overs; delivery = batter, bowler, non_striker, runs, extras, wickets (with
fielders), review (DRS), replacements, actual_delivery.
Read today: fielders, registry, venue, event, officials, outcome, toss. **Not read:** review, replacements,
player_of_match, absent_hurt, miscounted_overs.

### 2.3 Kaggle ODI dataset (bhuvaneshprasad) - 7 files, 1971 to Mar 2024
| File | Rows | Columns | Read today? |
|---|---|---|---|
| odi_Matches_Data.csv | 4,745 | ODI Match No, Match ID (ESPN), Match Name, Series ID, Series Name, Match Date, teams, captains, runs, wickets, extras, venue, city, ... | yes |
| odi_Batting_Card.csv | 103,225 | Match ID, innings, team, batsman, runs, balls, fours, sixes, strikeRate, isOut, wicketType, fielders, bowler | yes |
| odi_Bowling_Card.csv | 56,514 | Match ID, innings, team, opposition, bowler id, overs, balls, maidens, conceded, wickets, economy, dots, fours, sixes, wides, noballs | yes |
| odi_Fow_Card.csv | 67,963 | Match ID, innings, team, player, wicket, over, runs | **no** |
| odi_Partnership_Card.csv | 73,826 | Match ID, innings, for wicket, team, opposition, player1, player2, player1 runs, player2 runs, player1 balls, player2 balls, partnership runs, partnership balls | **no** |
| players_info.csv | 6,701 | player_id, player_object_id, player_name, dob, dod, gender, batting_style, bowling_style, country_id, image_url, image_metadata | yes |
| odi_scorecards_1971_2024.zip | - | the original download | - |
Licence: TODO (note it from the Kaggle page; credit the uploader).

### 2.4 The Wisden Book of Test Cricket Vol. 1 (bought copy; numbers only) - 8 CSVs, 1877-1970
matches (673), innings (2,442), batting (26,862), bowling (13,165), **fall_of_wickets (20,746: test_no, innings_no,
team, wicket, score)**, **appearances (14,805: test_no, team, player, captain, keeper)**, **debuts (1,212)**,
**players (1,428: player, team, tests, first_test, last_test, note)**. Read today: matches, innings, batting,
bowling only. Never store the book's pages, layout or text.

### 2.5 ESPNcricinfo old archive (i.imgci.com, Oct 1998-2002 ball-by-ball; scorecards 1970-71 on)
`data/raw/espn/ARCHIVE/<season>/...` 7,003 pages: scorecard pages 2,195; PLYR-V-PLYR 1,345; **MR (match report)
1,226**; BBB-COMMS 867; **RR-COMPARISON 573**; **GRAPH 527**; **WAGON 221 + WAGON-FULL 3**; **BBB 25**;
media AUDIO 12, PICTURES 4, IMAGES 2, SLIDESHOW 2, SSHOW 1. Plus `espn_lists/` (412 year lists, ESPN's own
numbering) and `espn/_index/` (50,496 navigation pages: folders, domestic matches, squads, pictures; raw only).
Today's database uses men's Tests/ODIs only: 1,645 scorecards (used only when every innings adds up), 637 matches of
ball-by-ball commentary (~592,000 balls), 435 player-v-player tables (cross-check). **Not read:** MR, RR-COMPARISON,
GRAPH, WAGON, BBB.

### 2.6 ESPNcricinfo live site: the full commentary (12 Nov 2026) - see docs/ESPN_LIVE_SURVEY.md
66 files `espn_full_commentary_part001..063.json.gz` (+3 "(1)" copies, exact duplicates) + `..._redo.json.gz`;
700 MB zipped, 11.7 GB raw; 7,692 unique matches (all Tests and ODIs ESPN lists, minus none). 4,529 with
commentary (4,517 + 12 from the redo); ~40 lakh balls; **73.3 million words** (ball lines 50.1 M, before-ball 11.3 M,
after-ball 10.8 M, dismissal text 1.2 M); 831,497 between-ball pieces. Every match also carries ESPN's scorecard
(fall of wickets 98-99% of innings in every era, partnerships every innings, over-by-over ~60% from 2005, DRS),
match facts and every player (date of birth 100%, batting style 100%, bowling style ~90%). Kept on Anadi's laptop
in `Downloads\ESPN full commentary\`. The download scripts: `scripts/browser/everyball_espn_full_commentary.js`
(must be copied from the laptop into the repo) and `scripts/browser/everyball_espn_redo.js`.

### 2.7 To add (new)
- **Measured weather** for every match day since 1940 (Open-Meteo historical archive, ERA5 reanalysis; free).
- **ESPN live match report stories** (`match.reportStoryId` / `reportStory`; Stage 3 pitch tags).
- Grounds with coordinates (from ESPN ground objects + a one-time geocode list).

---------------------------------------------------------------------------------------------------------------------

## 3. Getting the raw data where it is needed
- **The Codespace** holds `data/raw/` (68,229 files) and is where the official build runs. A stopped Codespace is
  deleted after 30 days unused: the release is the safe copy (`scripts/backup.py`, `bash scripts/start.sh` restores).
- **The release `raw-data`**: `part-0001.zip` ... `part-0018.zip` (~1.45 GB). Part 1 = Cricsheet + full Kaggle + part of
  Davis; the rest = Davis PDFs + ESPN old archive + index pages.
- **Claude's workspace** (for testing the whole build before Anadi runs it): while the repo is public, Claude can
  download the parts directly (`curl -L https://github.com/Anadijain1234/Everyball/releases/download/raw-data/part-00NN.zip`:
  all 18 in about a minute). When private: GitHub linked to Claude (read only), or copy from the laptop (`Downloads\Github
  Releases\`; ~0.4 MB/s on PG WiFi, ~1 hour; max 400 MB per file), or run an inventory script in the Codespace and send
  the small report.
- **The ESPN live files** (700 MB): add them to the release as new assets (`espn_full_commentary_*.json.gz`), then
  `gh release download raw-data -p 'espn_full_commentary_*' -D data/raw/espn_full/` in the Codespace.
- **Two copies always:** release + laptop (or an external drive). Optional third: a private Hugging Face dataset
  (100 GB free private storage).

---------------------------------------------------------------------------------------------------------------------

## 4. The two databases and every table
**(a) Archive database** (`everyball_archive.db`): everything, every source's version, every word. For research, the
model and rebuilds. Lives in the release (packed; split if a file passes 2 GB). Expected ~2 GB plain.
**(b) Website database** (`everyball.db`): only what the site and the Ask box use (chosen versions + views +
pre-computed tables + commentary). Downloaded by Render at deploy. If too big for Render: commentary moves to its own
file/plan B (private HF dataset, fetched per match).

Medallion layers stay: bronze = raw files (never changed); silver = every source's rows tagged with `source`; gold =
one chosen version per match + views + pre-computed tables. Every table below keeps a `source` column where rows can
come from more than one source. Existing tables (scripts/common.py) are kept; new columns/tables are marked NEW.

| Table | Key columns (NEW in bold) |
|---|---|
| matches | match_id (T0674/O3817 = ESPN's own number), format, number, start_date, season, team1, team2, ground, result, winner, toss, umpires, balls_per_over, series, sources, card_source, ball_source, confidence, **espn_id, espn_series_id, ground_id, toss_choice, result_text, margin, day_night, scheduled_overs, tv_umpires, referee, reserve_umpire, player_of_match_ids, hours_info, coverage_note** |
| players | player_id, name, team, full_name, born, role, dob, batting_style, bowling_style, cricsheet_id, cricinfo_id, sources, **espn_id (internal), espn_object_id, dod, long_batting_style, long_bowling_style, playing_roles, country_team_id, gender** |
| **player_names** | player_id, name_as_written, source, match_id (where seen) - every spelling ever seen, so a name is never lost |
| innings | match_id, inn, team, runs, wickets, overs, extras, declared, source, **byes, legbyes, wides, noballs, penalties, lead, target, minutes, follow_on** |
| batting | (existing) + **dismissal_text, fow_order, minutes, strike_rate** |
| bowling | (existing) + **dots, fours, sixes, wides, noballs** |
| played | match_id, team, player_id, source, **captain, keeper, debut, substitute/replacement flag** |
| balls | match_id, inn, seq, over_no, ball, bowler_id, batter_id, non_striker_id, bat_runs, extras, extra_type, legal, wicket, kind, out_id, sure, source, **espn_ball_id, timestamp, line, length, shot_type, shot_control, wagon_x, wagon_y, wagon_zone (NULL if ESPN wrote 0), dismissal_code, fielder_ids, speed_kph (from text), angle (over/around; carried forward from the last angle line), is_free_hit** |
| **ball_labels** | match_id, inn, seq, label, value, method (espn / rule / ai / weather), confidence, model_version, created - every extra label, never overwriting anything |
| **commentary** (replaces today's small table) | id, match_id, inn, seq (ball it belongs to; NULL if not tied to a ball), piece_order, kind (ball_line / before / after / over_summary / dismissal / old_bbb / report), type (HTML / TWITTER / POLL / VIDEO / ...), html (exactly as sent), text (cleaned: tags removed, entities decoded, split old lines joined), tags (promo, reader_comment, field_change, angle_change, match_state, insight), source |
| **commentary_fts** | SQLite FTS5 index on commentary.text (search "reverse swing" across 25 years) |
| **match_reports** | match_id, source (espn_old_mr / espn_live_story), title, html, text |
| **fall_of_wickets** | match_id, inn, wicket_no, runs, overs, over_no, ball, batter_out_id, source (espn_live / davis / kaggle / book / computed) |
| **partnerships** | match_id, inn, for_wicket, batter1_id, batter2_id, runs, balls, b1_runs, b1_balls, b2_runs, b2_balls, unbroken, source |
| **overs** | match_id, inn, over_no, runs, wickets, bowler_ids, maiden, total_runs, total_wickets, required_rr, source (espn_live / espn_rr_comparison / computed) |
| **sessions** | match_id, day, session, inn, runs, wickets, overs, source (davis_sess) |
| **series_averages** | series_code, player_id, batting/bowling figures as printed (Davis av; cross-check) |
| **drs_reviews** | match_id, inn, over_no, ball, team, batter_id, bowler_id, umpire, decision, outcome (struck down / upheld / umpire's call), source (cricsheet / espn) |
| **replacements** | match_id, team, player_in, player_out, reason (concussion etc.), source |
| **old_wagon** | match_id, batter_id, zone, runs, scoring shots (ESPN old WAGON pages) |
| **source_notes** | source, series_code / match_id, title, text (Davis's notes, "A Note on Score Reconstruction"; credited) |
| **weather** | match_id, date, ground_id, temperature (min/max), humidity, dew point, wind speed, wind direction, gusts, rain, cloud, source ('open-meteo era5'), measured_or_estimated flag |
| **grounds** | ground_id, name, town, country, latitude, longitude, home teams, SENA flag |
| notes, pvp, ref_matches | existing |
| checks, build_log | existing |
| **counts** | source, file, match_id, pieces_in_raw, rows_stored, rows_dropped (empty shells), reason - the proof nothing was lost |
| **dropped** | every dropped empty shell: file, match_id, item id, type, reason |
| views bat / bowl / ball / inns, player_stats | existing (gold) + **duels** (every batter-bowler pair: balls, runs, outs, dots, 4s, 6s, by format and year) pre-computed |

---------------------------------------------------------------------------------------------------------------------

## 5. Readers, one per source (how each is written and tested)
Same method for every reader: (1) look at real files of every kind; (2) rules that turn each field into rows;
(3) run on ALL files, log anything not understood (file + reason) in build_log; (4) fix and rerun until every
leftover is explained; (5) counts table: what went in = what came out. Readers never invent values; unknown = NULL.

- **read_davis.py** (exists): Test pages, ball-by-ball (replay innings, check every over against Davis's over totals
  -> `sure`), covers/player lists. ADD: save fall of wickets (FOW_TOK already parses it) + read the `...fow.pdf`
  files; `...sess.pdf` -> sessions; `...av.pdf` -> series_averages (cross-check only); `...text.pdf` + notes ->
  source_notes; series web pages -> matches facts if missing; clean names before saving (strip wicket marks "W W",
  trailing dots, daggers). Davis's short names ('M Jayaward.', 'Javed M.') resolved via match context (section 6).
- **read_cricsheet.py** (exists): ADD review -> drs_reviews, replacements -> replacements, player_of_match ->
  matches, absent_hurt, miscounted_overs -> notes; people.csv keys (key_cricinfo...) -> players ids.
- **read_kaggle.py** (exists): ADD odi_Fow_Card -> fall_of_wickets, odi_Partnership_Card -> partnerships; keep
  Match ID / Series ID (ESPN ids) on matches.
- **read_book.py** (exists): ADD fall_of_wickets.csv, appearances.csv -> played (captain, keeper), debuts.csv ->
  played.debut, players.csv -> cross-check of first/last Test.
- **read_espn.py** (old archive, exists): ADD MR -> match_reports; RR-COMPARISON -> overs (source espn_rr_comparison);
  WAGON/WAGON-FULL -> old_wagon; BBB (25) -> balls if not duplicates; GRAPH -> check once, raw only unless it holds
  numbers we lack. Media pages: raw only.
- **read_espn_live.py** (NEW): see docs/ESPN_LIVE_SURVEY.md. Per record: dedupe by (format, number) (drop the 240
  "(1)" copies; keep the version with most items); match facts -> matches; players -> players (+ player_names);
  scorecard_innings -> innings/batting/bowling (source 'espn_live'), fall_of_wickets, partnerships, overs,
  drs_reviews; innings items (stored newest first: sort by inningNumber, oversUnique) -> balls (+ labels) and
  commentary pieces (section 8). wagon 0 -> NULL. dismissalType codes: 1 caught, 3 lbw, 4 run out, 6 hit wicket;
  map the rest from dismissalText.short and record the mapping. ESPN's `predictions` -> ball_labels (method 'espn',
  label 'espn_win_probability'; never a fact).

---------------------------------------------------------------------------------------------------------------------

## 6. Joining people (Anadi's player-id system, plus match context)
Each source writes names its own way; the people step links them to one player_id. Order of evidence:
1. ESPN ids (espn object id = Cricsheet key_cricinfo = Kaggle player_object_id).
2. Cricsheet identifier.
3. **Match context (NEW):** ESPN's files give the XI with ESPN ids for all 7,692 matches since 1877. A name from any
   source in match M for team T is joined to the player in T's XI for M whose surname/initials fit. One match ->
   no guessing. ("McGrath" bowling for Australia in a Test where Glenn McGrath is in the XI = him.)
4. Full name; then surname + first name/initial (only when unique in that team and era).
Never merge two real people (Mark/Steve Waugh, Saleem/Shoaib Malik stay apart). After joining: empty records are
removed; records with rows but no id are listed in the report. Known leftovers (13 Nov): "W W GD McGrath",
"GD McGrath." (empty), "McGrath" (30 Davis balls). Every spelling goes into player_names.

## 7. Choosing between sources (gold)
- Ball-by-ball priority: **Cricsheet > Davis > ESPN live > ESPN old archive**, but only a source whose balls add up to
  its scorecard (runs, wickets, legal balls) can be chosen. Others stay in silver for cross-checks.
- Scorecard: the first source (Davis, Cricsheet-derived, ESPN live, Kaggle, book, ESPN old) whose batters + extras =
  total and bowlers = total; disagreements recorded in checks.
- ESPN live text and labels attach to the chosen balls by lining up deliveries: same innings, over, ball order,
  bowler, batter, runs. A ball that cannot be lined up keeps its text on ESPN's own ball row and is flagged.
- Fall of wickets / partnerships: from balls when the match has chosen balls; else the first source that adds up
  (ESPN live, Davis, book, Kaggle); all versions kept.

---------------------------------------------------------------------------------------------------------------------

## 8. Commentary: stored word for word (decided 12-13 Nov from the real data)
- Every piece -> one row in `commentary`, tied to its ball (seq) and ordered (piece_order). `html` exactly as sent;
  `text` = cleaned copy for search and the model (HTML tags removed, entities decoded, old split lines joined with a
  space). Nothing is reworded.
- Kinds: ball_line (commentTextItems), before (commentPreTextItems), after (commentPostTextItems), over_summary
  (the `over` object), dismissal (dismissalText.commentary), old_bbb (old archive lines), report (match reports).
- **Dropped: only empty shells with no words** (video players, live-stream links, iframes, empty HTML: ~2,800 empty +
  ~460 embeds). Polls and tweets keep their text. Every drop is logged in `dropped`.
- **Promo is not deleted, and is used (Anadi, 13 Nov: "peanuts in 39 crore characters, include them").** The ~4,600 pieces with promo words (app, download, click, subscribe, fantasy, watch live,
  http, poll, vote...) are judged one by one by an AI -> tag 'promo' (the site may hide them; training keeps them, tagged) or left as content.
  Evidence (12 Nov): the most repeated between-ball lines are gold ("Around the wicket" in 635 matches, "Two slips and a
  gully", "Drinks", "Last over before lunch", "Free hit"); "click" appears in fan lines ("if Warner and Starc click").
- Other tags by exact rules: angle_change ("over/around/round the wicket"), field_change (slips, gully, sweeper,
  short leg...), match_state (drinks, lunch, tea, stumps, innings break, rain, bad light), reader_comment
  ('Name: "..."' pattern), insight (AI, section 9).
- Count proof per match: pieces in raw = rows stored + rows dropped, else the build stops and flags the match.
- Facts to keep in mind: text on 12% of balls in 2000-04 (ESPN wrote only notable balls), 62% 1990s, 80% 2005-09,
  96%+ from 2010; 13,710 between-ball pieces discuss line/length/what to bowl.

## 9. Labels (cricket factors) - never overwrite, always say where they came from
**From ESPN fields (exact):** line, length (2015+, ~90%), shot type + control (2000+, ~85-90%), wagon (real when not 0),
dismissal type, timestamp (2020+).
**Exact text rules (code), each with a test list of examples:**
- speed: `(\d{2,3}(?:\.\d)?)\s?(kph|km/h|kmph|mph)` (mph converted, both kept);
- angle: `\b(over|around|round) the wicket\b` (carried forward to the bowler's next balls until changed);
- slips/field: `\b(one|two|three|four|five|\d) slips?\b`, gully, short leg, silly point, leg slip, sweeper, deep
  square, fine leg, third man, long on/off, "packed off side", "leg-side field";
- match state: drinks, lunch, tea, stumps, innings break, rain, bad light, floodlights, new ball taken;
- DRS: review, "umpire's call", "struck down", "overturned", "stays";
- milestone: fifty, hundred, five-for;
- dismissal fielding position: fielder name from the dismissal text ("c Smith b Cummins") + position from the
  commentary ("taken at second slip", "top edge to fine leg") -> label `dismissal_position` (rule first, AI for the rest),
  so the Ask box can answer "where does X get out most?" (position, line, length, shot, bowling type).
**AI labels (meaning; Stage 3 method):** the AI labels a sample, a small model learns from it and labels the rest;
accuracy measured on **Anadi's 500 hand-labelled lines**; a keyword method is the baseline to beat; a label is
used only if it beats the baseline. Each AI label has a confidence. Taxonomy (allowed values fixed in advance):
- delivery: line/length (for pre-2015 balls), pace/spin, variation (bouncer, yorker, slower ball, cutter, knuckle,
  googly, doosra, carrom, arm ball, flipper), movement (inswing, outswing, reverse, seam in, seam away, turn in,
  turn away, drift), bounce (extra, low, uneven, skid);
- shot: front/back foot, edge (inside/outside/top), beaten, left alone, mistimed, appeal;
- conditions: pitch (green, dry, cracks, turning, two-paced, flat, footmarks, variable bounce), weather (overcast,
  sunny, humid, hot, rain, bad light), dew, wind (direction relative to the pitch/batter, strength), ball (new, old,
  reverse, soft, changed);
- tactics: bowling plan (outside off, packed off side, short-ball plan, wide yorkers, leg theory, attack the stumps),
  batting intent (attack, defend, rotate), field intent (attacking/defensive);
- insight: a piece that explains why something works (e.g. wind to leg + right-hander -> bowl outside off, pack the
  off side).
"Not mentioned" = unknown, never "no".

## 10. Weather and grounds
- grounds: from ESPN ground objects (name, town, country) + coordinates (one-time list; check each by hand-free
  geocoding against the town).
- weather: Open-Meteo historical archive (ERA5 reanalysis, from 1940; free; verify the API and its terms before use):
  daily and hourly temperature, humidity, dew point, wind speed/direction/gusts, rain, cloud for each ground and match
  day. Stored as measured (reanalysis) values with that label. Run from the Codespace (Claude's workspace may not
  reach the API).

---------------------------------------------------------------------------------------------------------------------

## 11. Checks and the report (`data/build_report.txt`, short, in plain words)
1. counts: every file read or listed with a reason; pieces in = rows out (+ dropped shells) per source and match;
2. cricket maths: batters + extras = total; bowlers' runs = total - byes - legbyes (- penalties); wickets; legal
   balls = overs; fall of wickets increasing and last <= total; partnerships sum = total; balls add up to scorecard;
3. source v source: Davis v Cricsheet (22,052/22,054 batters agreed on 12 Nov), ESPN live v Cricsheet/Davis ball by
   ball, ESPN scorecard v Davis/Kaggle/book, ESPN commentary v player-v-player (612/905 innings agreed; look at it);
4. lists: Tests/ODIs per year v ESPN's year lists; Test/ODI number gaps;
5. people: empty records, records without ids, possible splits (same team, same years, name variants);
6. famous facts (section 18);
7. the 14 ESPN "commentary flag but nothing" matches, the 8 ODIs without an ESPN scorecard, Test #1541.
Triage: anything that changes a number a visitor sees -> fix; tiny things -> logged; decisions -> Anadi.

## 12. The website (Flask, app/; exists: Ask box, Duels, player pages, coverage by decade, Credits, light/dark, phone)
ADD with the new data: commentary under every ball in Duels and match pages ("Commentary: ESPNcricinfo" next to it);
fall of wickets + partnerships on every scorecard; search over all commentary (the Ask box can also search the text and use the commentary labels, e.g. "where did
  Kohli get out most", "what did commentators say about the pitch in Perth 2018"); "struggles where" pages (line, length,
shot, bowling type, ground, country, SENA, era); same-age comparisons (dates of birth); pressure meter; bunny finder
(with a luck check); 10x10 heatmap; dismissal profiles; false-shot %; pitch maps by length zone; DRS stats; debutants;
match reports; conditions per match (pitch, weather, dew, wind; measured weather shown as "measured"); coverage meter
on every match; a "known gaps" page. Ask box: Groq `openai/gpt-oss-120b` writes one read-only SQL query; the
database counts; the answer is written only from the returned rows; "How it was counted" shows the query; Hindi
via Sarvam `mayura:v1`; 6 questions/min and 60/day per visitor; same question = cached.

## 13. Storage and hosting (no paid database needed)
- Growth: ~150-200 new Tests/ODIs a year = ~30 MB raw + ~20 MB text a year. The 150-year history is the big part
  and is done once.
- GitHub Releases: no limit on total size or bandwidth; each file < 2 GiB; up to 1,000 files per release (start a
  new release per year if needed). Files stay until deleted; GitHub does not weaken or expire them. Still keep two
  copies (release + laptop/external drive), because one provider is one point of failure.
- Database files go to a release (`db-YYYY-MM-DD`), not into git history (each weekly push would add ~100 MB).
- Render free: 512 MB RAM (fine: SQLite reads from disk), 0.1 CPU (heavy counting done at build time: player_stats,
  duels pre-computed), ephemeral disk (database downloaded in the build command with a token in Render env; disk
  size not published -> test), spins down after 15 min (pinger: UptimeRobot / cron-job.org), 750 free hours/month,
  bandwidth limit (check the current figure).
- **Hugging Face (Anadi, 13 Nov):** a private HF dataset (100 GB free) can hold BOTH databases and the raw files, as
  well as or instead of the release; Render's build downloads from it with a token in Render env. HF stores files;
  it does not run our site for free (free Docker/Gradio Spaces need PRO since Jul 2026), so the site stays on Render.
- Hosted databases are not needed: Neon free is 512 MB (ours ~2 GB); Render Postgres free expires after 30 days.
  Only if Render cannot hold the database: plan B = commentary in a private Hugging Face dataset (100 GB free),
  fetched per match; paid option last (e.g. Render Starter with a disk) and only with Anadi's yes.
- The repo should be **private** (ESPN's permission covers the site with credit, not republishing its raw pages).

## 14. The weekly robot (Stage 2b, after the CIEs)
- Cricsheet: GitHub Actions, weekly: download new matches -> build -> checks -> new database to a release -> Render
  deploy hook. Emails only on failure.
- ESPN: GitHub servers are refused by ESPN (403), so a self-hosted runner on Anadi's laptop runs the browser script
  for the week's new matches (needs the laptop on; confirm with ESPN that a small weekly update is covered).
- New raw files are added to the release as new assets; nothing is overwritten.

## 15. The model (Stage 3-4; trained on Kaggle's free GPU/CPU)
**Training data (clean subset; nothing deleted to get it):** balls from Cricsheet, Davis balls with sure = 1 (98.6%),
ESPN innings that add up; labels from ESPN (line/length 2015+, shot 2000+) as targets; ball lines as input text;
angle/field changes as features for following balls; insights, pitch/weather/dew/wind labels and measured weather
feed the conditions model; reader comments and promo are used too, tagged (the model can weigh them). Every change to the data or the model is kept
only if it scores better on the **fixed hidden test set** (chosen once, e.g. a set of complete series 2015-2024, never
trained on).
**Models:**
1. Line/length/shot labeller for 2001-2014 balls from text (~1 million balls), confidence per ball.
2. **Conditions number** per match (and per day/session): spin-friendly, seam-friendly, pitch difficulty from how
   everyone did v their usual, plus text labels and measured weather.
3. **Duel model ("Dravid v Steyn", expected v actual):** start from "batter v everyone" + "bowler v everyone",
   adjusted for conditions, era, home/away, ball age, phase, pressure; Bayesian hierarchical, so small duels borrow
   strength and move only when the real duel clearly differs; output a range ("Dravid won, 21-41"); verdict =
   actual v expected.
4. **Estimates where no ball-by-ball exists** (Gavaskar v Imran, Border v Hadlee, 1971-98 ODIs): from scorecards,
   who got out to whom, fall of wickets, partnerships, when each batter got out, each player's style from their real
   data (IPF / Bayesian MCMC / Markov + multinomial), tested on hidden modern matches; always labelled estimate.
5. Wicket predictor (chance of a wicket on this ball), pressure meter, bunny finder, "struggles where".
**Retraining:** weekly robot adds labelled balls; monthly Kaggle run (Kaggle API) trains a new version; it replaces
the old one only if better on the hidden test set; then re-labels old balls. Accuracy shown on the site. Paper with a
professor (Stage 4).
Honest limits: no ball speed (except when commentators say it), no Hawk-Eye metres, no 3D simulation; "not mentioned"
!= "absent"; small duels give wide ranges; nothing is 100%.

---------------------------------------------------------------------------------------------------------------------

## 16. The build, step by step (one build after the CIEs)
0. Anadi: make the repo private; commit this save zip; copy `everyball_espn_full_commentary.js` into scripts/browser/.
1. Raw data into Claude's workspace (section 3) + the ESPN live files (already on the laptop).
2. Claude: audit = docs/FIELD_MAP.md (every field of every source -> table/column or "raw only" + reason).
3. Claude: readers (section 5), people (6), choosing (7), commentary (8), exact labels (9), new tables (4), counts
   and checks (11), the two databases (4), packing.
4. Claude tests the WHOLE build in its workspace: known-answer tests, every reader on every file, counts in = out,
   sums, source v source, famous facts; fix every flag; repeat until clean.
5. Anadi: one command block in the Codespace (unzip, tests, build); sends `data/build_report.txt`.
6. Claude fixes what the report shows (expect 1-2 rounds); then databases to a release; Render deploy (+ pinger);
   ESPN gets the link.
7. Then: AI labels (promo check, meaning labels), weather, model, robot.

## 17. Definition of done (for the build)
- Every raw file read or listed with a reason; counts proof passes for every source and match.
- Every famous fact passes; source-v-source agreement at least today's level (Davis v Cricsheet 99.99% batters).
- Every commentary piece with words is in the database (counts), with its kind and ball.
- Fall of wickets and partnerships for every innings that any source has.
- Every player has one record (no splits), with ESPN date of birth and styles where ESPN has them.
- The site answers the 100 test questions (Anadi's side task) correctly; the report's "needs a human" list is short.

## 18. Known facts to test (must pass)
Bradman 6,996 Test runs, average 99.94, 29 hundreds; Tendulkar 15,921 Test runs (51 hundreds) and 18,426 ODI runs
(49 hundreds); Muralitharan 800 Test wickets; Laker 19/90 (Old Trafford 1956); Kumble 10/74 (Delhi 1999); Lara 400*
(Antigua 2004); Hayden 380 (Perth 2003); Sobers 365*; Hutton 364; New Zealand beat Ireland by 290 runs (Aberdeen,
1 Jul 2008, ODI #2727). From 13 Nov (our data): Sachin v McGrath in Tests: 9 matches, 286 balls, 88 runs, out 6 times
(all ball-by-ball); in ODIs: 23 matches, 18 with ball-by-ball: 202 balls, 179 runs, out 5 times (7 times in all
scorecards); ESPN v our database matched exactly in 14 of 15 shared matches (ODI #1633 differs by one ball).

## 19. Risks and limits
- ESPN can change its site or pass maker (the browser script searches for it); ESPN's permission is per email.
- AI labels are estimates: separate columns, confidence, measured accuracy; never mixed with facts.
- Render free limits may change: measure, plan B ready.
- Laptop is the only copy of the ESPN live files until they are added to the release: add them early.
- Usage limits: build in 2-3 sessions; save PLAN.md + this file at the end of each.

## 20. Open items
Test #1541 ball-by-ball (none found); Kaggle licence note; 100 test questions (Anadi); 500 hand-labelled lines
(Anadi, Stage 3); ESPN confirmation for the weekly update; ESPN live report stories download; T20s: not doing.
