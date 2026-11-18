# Everyball handover - 18 Nov 2026, 5 am (paste this file into the next chat with the zip)

Read first: PLAN.md (UPDATE 18 Nov at the top) and the 17 Nov handover. Anadi's rules unchanged: build only on "build",
plain language, test before handing over, Claude never pushes, one command block, live progress, honest time estimates
(Claude's estimates have run ~3x high on builds and ~2x LOW on the solver; say so).

## Done tonight (careers build) - in everyball-careers-18nov.zip
- add_espn_cards.py (ESPN scorecards + XIs, players joined by ESPN id + match XI, 51 missing matches added, wrong-id
  people split, exact-figure pairing), build_gold.py (ESPN votes, Wisden dropped, XI match counts), publish_gold.py
  (site reads gold), career_check.py, coverage cache, player page matches.
- Tested on the full data here: 26/28 hard questions exact; batting 183,086 lines 99.98%+ equal to ESPN.
- Pack tested: 94 MB in 2 parts; unpacked like Render: Sachin 200/463, Kallis 166/13,289/292, coverage 0.0004 s.
- Anadi uploaded the 67 ESPN files to the GitHub release raw-data. His Codespace command (given in chat, uses
  gh release download) had NOT been confirmed run at 5 am - check GitHub's latest commit first.

## Solver (in progress) - this zip
- scripts/add_espn_fow.py: fall_of_wickets (155,304 rows) + partnerships (163,774) from ESPN, all 19,637 innings.
  1,803 innings' partnerships don't add up to the total (old matches / penalties) -> the solver drops the split there.
- scripts/solver/layered_solver.py (now the full version: known + unknown overs, recorded balls hard/soft, recorded
  order preference, kind_key). Regression: 2011 final level 2 and 3b OPTIMAL, 0 rule problems.
- scripts/solver/fix_innings.py:
  - who_faced (Group B): who_faced_seq = sequence model (every recorded delivery kept in place, strike by the Laws,
    only bat runs may change, as few as possible; batter runs + over totals elastic with heavy price; balls faced /
    4s / 6s soft; complete starting hint from replaying the Laws; pairs at the crease from ESPN partnerships).
    Test: 2011 final, 20% strikers scrambled -> 100% restored, <1 s.
    classify(): of the 797 flagged innings only 464 are "strike/runs wrong" (fixable here); 173 recorded balls
    unreadable (unknown extras codes, mostly Davis); 103 wickets missing/extra (95 are old-ESPN ODIs); 42 wicket given
    to the wrong player; 15 batter not in ESPN's XI; 2 no ESPN fall of wickets -> listed, need a rebuild (gaps-type).
    Trial on 20 of the 464 (before the last fix): 6 fixed (1 clean, 5 with a run bent), 12 failed on a CHECKER
    mismatch (checker used card batting order, solver used ESPN partnerships for the incoming batter) -> fixed in code
    (inf["order"] + replay with that order; errors already in the recorded balls exempted) but NOT re-run yet;
    2 failed on "same bowler twice in a row" = error in Davis's own record (now exempted as a source error).
    NEXT: rerun `python3 scripts/solver/fix_innings.py who_faced --db <db> --time 20 --trial` (table trial30 = the
    30-innings sample) and check; then the full 464.
  - gaps (Group C): 2011 final test: innings 2 OK (kept overs 100%, hidden 15 overs 8.3% exact = level-1 ceiling),
    innings 1 INFEASIBLE in 0 s = BUG, not found yet. Suspects: recorded_by hard constraint / trailing segments /
    bowler balls with unknown overs. NEXT: bisect (hide 0 overs -> must be feasible; then add hidden overs).
  - test: `python3 scripts/solver/fix_innings.py test --db <db>` (add --b-only to skip gaps).
- Output table balls_rebuilt (label recorded/rebuilt, job) + data/reports/fix_<job>.csv. Not yet wired into the site.
- The website still reads `balls` for duels; rebuilt balls go live only after a decision on the "rebuilt" tag display.

## Also asked tonight
- Shot types (e.g. "how many times Sachin pulled"): ESPN has shotType/line/length per ball (2000+ / 2015+) but our
  balls table doesn't store them yet -> small add-on reader (~20-30 min), after the solver.
- Who was bowled most in Tests (works now): Dravid 55, Tendulkar 54, Border 53, Kallis 46, Stokes 45.

## UPDATE 6 am - Group B trial passes
- Bug was in the CHECKER (cricket_facts.strike_problems): it assumed the striker is always the one out. When the
  non-striker is out (run out) or how-out is blank, the new batter may come in at either end. Fixed.
- Trial 30 innings: 30/30 obey the Laws; 6 clean, 24 bend a scorecard number by 1-4 (over totals 34x, batter runs 24x).
- 20 s was too short for long Test innings (T0975: 108 balls' runs changed at 20 s, 39 at 90 s). Now
  seconds = min(--max_time 240, max(--time, balls x --per_ball 0.12)).
- Guard: an answer that bends any number by 5+ (--max_bend) is NOT saved; old rows for failed innings are deleted.
- --shard k/n runs n copies side by side on one DB (busy_timeout); reports fix_who_faced_s<k>.csv.
- Group C (gaps) innings-1 INFEASIBLE bug still open.
- Group C bug FOUND + fixed (layered_solver.py, over-end rule "o2"): to end an over on the other batter's odd
  ball, the batter left on strike was required to have NO strike-keeping balls (wides, dots) in that part. That is
  only true if he was never on strike before the last ball. 2011 final over 43 (a wide to 2213) made the real innings
  "impossible". Now required only when he did not start on strike and hit no odd ball. Real innings now solves with
  recorded overs hard + per-batter counts hard. Old file kept: /tmp/layered_solver.before_overend.py (not in zip).
- Group C kept overs: solver shuffled order inside kept overs / started 2 overs with the wrong batter after a run-out
  where the batters had crossed (2011 final over 48). Now: f["recorded_first"] pins who faced the first ball of each
  kept over (first real part, not a wicket's empty tail; left free if that ball is a wicket) + kept overs are put
  back in recorded order after solving. 2011 final test: kept overs 100% exact both innings; hidden overs 17.5% / 7.3%
  ball-exact (level-1 limit: only the scorecard is known for them).
- Regression after these changes (2011 final, 120 s): 3b 53.4% / 38.5% exact, level 2 35.7% / 26.3%, 0 rule problems
  (before: 47.3/39.5 and 31.2/21.9).
- New option --sample N (random trial, seed 3). Group C real-innings trial (10) was running at the time of this save.

## UPDATE 7 am - where it stands (read this part first)
- GROUP B READY: RUN_GROUP_B.md has the one paste (shards = nproc/2, --resume, live line). ~9 solver-hours total.
- GROUP C NOT READY. Real-innings trial (10 random of the 1,856 innings_gold bbb_complete=0): 0/10.
  - 5: ESPN fall of wickets has the score but not the over (old Tests) -> split needs wickets located by SCORE.
  - 3 (ODIs O1529 i2, O1706 i2, O2805 i1): data disagreements, not missing overs. Missing wickets inside complete
    overs, overs with 5 or 7 legal balls, runs off by 1 against the scorecard. "Kept overs hard" can't fit.
  - 2: Davis extras code 'e' unreadable.
  Needs a REPAIR model, like who_faced_seq: keep recorded balls in place, allow few inserts/deletes/edits (missing
  ball, missing wicket, wrong run), plus whole missing overs from the scorecard, plus wickets placed by score.
  Waiting for Anadi's go-ahead (his rule: no route change without his say). Honest Claude build time ~2-3 h.
- Also new: --resume, --sample N, --shard k/n, --max_bend, time grows with innings length.

## UPDATE 8:45 am - the model (Anadi said "build the model")
- scripts/solver/ball_model.py: P(ball outcome | format, phase, ball number in over, previous ball) from all 6.5M
  balls, smoothed. Train: `python3 scripts/solver/ball_model.py` -> data/model/ball_order.json (15 s, small).
- layered_solver.order_part_ml: exact search over every valid order of a part, takes the MOST LIKELY (before: the
  first valid order, dots first = worse than random). Used when the over has no recorded order. LS.MODEL = "auto"
  loads data/model/ball_order.json; None = old behaviour. f["fmt"] = "ODI"/"Test" (callers set it; default ODI).
- Test (2011 final, match left out of the model) was running at this save.
- MODEL RESULT (honest): the order model LOST. Ordering-only test on 12,945 real complete overs (true balls + who
  faced them given, only the order to choose; tests/order_eval.py):
    ODI  (5,224 overs, ~15 valid orders each): old first-valid 59.0% balls right | random 55.2 | model MAP 55.9 | model "most balls right" 57.8
    Test (7,721 overs, ~8 valid orders each):  old first-valid 77.1%             | random 73.3 | model MAP 73.5 | model "most balls right" 74.7
  Why: the old rule (a batter's dots before his strike-changing single) matches real cricket; ball-number +
  previous-ball patterns are weaker than that. 2011 final with model: 3b 48.6/34.9, L2 32.8/20.3 (worse than
  53.4/38.5, 35.7/26.3 without). So LS.MODEL = None by default (code kept for experiments).
- WHERE THE REAL LOSS IS: layer 1 (who faced how many balls / which runs in each over). With those given, ordering
  alone gets 59% (ODI) / 77% (Test); the full solver gets 26-53% and striker only 50-68% right. Next model target =
  layer-1 costs (balls faced per batter per over, strike rotation patterns). Not built.

## UPDATE 10:30 am - GPU model for Kaggle (Anadi: "start building the model", train on Kaggle)
- scripts/model/export_balls.py (Codespace): data/model_export/ = everyball_balls.npz + innings.csv + players.csv,
  13 MB total, 6,538,291 balls, 15,315 innings, 4,434 players, 418 grounds; split by match crc32%20 (0 test, 1 val).
- scripts/model/everyball_net.py: causal transformer; per ball = batter (batting profile) + bowler (bowling
  profile) + other batter + Test/ODI + era (5 yr) + ground + teams + innings + over + ball of over + score +
  wickets + previous outcome -> chance of 0 1 2 3 4 5 6 W wd nb bye.
- scripts/model/train_everyball.py -> make_notebook.py -> train_everyball.ipynb (net code pasted in). d384 x 8
  layers, ctx 256, 16 epochs, AMP, best-by-val checkpoint, test on never-seen matches vs a how-often yardstick.
- NOT yet: CPU loader (duels, rebuild scoring), CPU smoke test (torch was installing), Kaggle steps doc.
- Added: scripts/model/everyball_model.py (CPU: duel "Gavaskar" "Bumrah" --fmt Test --year 1985; score_overs for
  rebuilds), scripts/model/order_test.py (honest test on never-seen matches: old rule v random v model), and
  KAGGLE_STEPS.md (Codespace export paste + Kaggle clicks). CPU smoke test pending (torch download slow here).
- 11:30 am: CPU smoke test passed (torch 2.15 CPU here): whole notebook runs; small CPU model (0.7M, 800 steps,
  36 s) already beats the yardstick on never-seen matches (1.108 v 1.111); duel + order_test run. Order test with
  that toy model (60 overs): Test old 73.5 / random 68.9 / model 72.7; ODI 58.4 / 58.1 / 56.8 - toy only; the
  real verdict needs the Kaggle-trained model. Keep the model OFF in the solver unless it beats the old rule.
- Next: Anadi runs KAGGLE_STEPS.md -> sends metrics.json + puts everyball_model.pt in release raw-data -> Claude
  runs order_test.py (1,500 overs per format) + duels, then (only if it wins) wires it into layered_solver/gaps.
