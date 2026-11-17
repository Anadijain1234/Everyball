# ESPN live commentary: what the 66 files hold (survey of every file and every item, 12 Nov 2026)

Files: `espn_full_commentary_part001..063.json.gz` + 3 "(1)" copies (019, 020, 021; made by a resume) +
`espn_full_commentary_redo.json.gz`. 700 MB zipped, 11.7 GB raw. Each file = a JSON **list of match records**
(up to 300). Survey script: `scripts/tools/survey_espn_live.py` (+ `_report.py`).

## Matches
- 7,932 records -> **7,692 unique** (format, number). The 240 repeats (from the "(1)" files) are exact copies.
- Status: Test ok 1,132 / no commentary 1,509; ODI ok 3,385 / no commentary 1,652 / "commentary flag but nothing
  returned" 14 (6 washouts after the toss + 8 played with no commentary).
- Missing from the 66 files: Tests #1548-1549, ODIs #1720-1729 -> fetched by the redo file.

## One record
`format, number, espn_id, start_date, teams, page_url, status, label, feed_extras` and:
- `match`: ESPN's match object (~90 fields): series, ground, teams, toss, result text, umpires, TV umpires, referee,
  debutants, replacements, hours, scheduled overs/days, commentator, scorer, report story id, ballByBallSource...
- `players`: {ESPN player id: player}. **dateOfBirth 100%, battingStyles 100%, bowlingStyles ~90%** (non-bowlers
  have none), playingRoles, countryTeamId, dateOfDeath, names (name, longName, battingName, fieldingName, indexName).
- `scorecard_innings` (all eras, 1877 on): runs, wickets, overs, balls, extras split, lead, target, minutes (patchy);
  `inningBatsmen` (runs, balls, 4s, 6s, minutes, dismissal, **fowOrder/fowWicketNum/fowRuns/fowOvers**),
  `inningBowlers` (overs, maidens, conceded, wickets, dots from ~2000, wides, noballs), **`inningPartnerships`** (every
  innings: pair, runs, balls, each batter's share), **`inningFallOfWickets`** (98-99% of innings in every era),
  `inningOvers` (over-by-over: ~0% before 1990, ~60% from 2005), `inningWickets`, `inningDRSReviews`,
  `inningOverGroups`, catches, catchesDropped (2020+).
- `innings`: {inning number: [ball items]}, **stored newest first (DESC)**: sort before use.

## One ball item (48 fields)
Always: `id, _uid, inningNumber, overNumber, ballNumber, oversActual, oversUnique, totalRuns, batsmanRuns, isFour,
isSix, isWicket, byes, legbyes, wides, noballs, penalties, batsmanPlayerId, bowlerPlayerId, totalInningRuns,
totalInningWickets, title ("Ervine to Saeed Anwar"), modified, wagonX/Y/Zone`.
Sometimes: `nonStrikerPlayerId` 97%, `outPlayerId` + `dismissalType` + `dismissalText{short,long,commentary}` on
wickets, `shotType` + `shotControl` (1 = in control, 2 = not) ~85-90% from 2000, `pitchLine` + `pitchLength` ~90% from
2015 (13% 2010-14), `timestamp` 2020+, `over` (over summary on the last ball of each over: runs, wickets, maiden,
batters and bowlers at the end), `predictions` (2020+), `commentImages`, `commentVideos`, `events`.

Values: pitchLine 5 kinds (OUTSIDE_OFFSTUMP, ON_THE_STUMPS, WIDE_OUTSIDE_OFFSTUMP, DOWN_LEG, WIDE_DOWN_LEG);
pitchLength 6 (GOOD_LENGTH, FULL, SHORT_OF_A_GOOD_LENGTH, SHORT, FULL_TOSS, YORKER); shotType 40 kinds (DEFENDED,
FORWARD_DEFENCE, LEG_GLANCE, OFF_SIDE_DRIVE_ON_FRONT_FOOT, NO_SHOT, FLICK, ON_DRIVE, LEFT_ALONE, COVER_DRIVE, ...);
dismissalType codes 1-13 (1 = caught, 3 = lbw, 4 = run out, 6 = hit wicket; map the rest from dismissalText).

## Text
- `commentTextItems` (the ball's line), `commentPreTextItems` (before the ball), `commentPostTextItems` (after):
  lists of `{type, html}`; types: HTML (almost all), TWITTER, POLL, VIDEO, LIVE_STREAM, YOUTUBE, IFRAME, INSTAGRAM,
  FACEBOOK, PLAYBUZZ, IMAGE.
- ~274 M characters of ball lines + ~68 M before + ~65 M after = **~0.4 GB of real text**.
- Share of balls with a written line: 1990s 62%, **2000-04 12%** (ESPN wrote only notable balls), 2005-09 80%,
  2010s 96%+, 2020+ 83%. Old lines are split into pieces (one per screen line): join them with a space.

## Quirks the reader must handle
1. Items newest first; dedupe by `id`; drop the 240 duplicate records.
2. `wagonX/Y = 0` means "not recorded" (about 70% of 2000s balls).
3. Runs add up to ESPN's own scorecard in ~96% of innings; flag the rest (never fix silently).
4. Players are ESPN ids: join to our people by ESPN id / objectId, then names (as the Kaggle reader does).
5. Keep the HTML exactly as sent (lossless); make a cleaned copy only for search.
