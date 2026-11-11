# Everyball

Every Test since 1877. Every ODI since 1971. Ball by ball wherever it exists.

See **PLAN.md** for the full plan and progress.

## Download the data (Step 3)

Run these in the Codespace terminal. Each one is safe to stop (Ctrl+C) and run again: it continues where it left off.

```
python scripts/download.py cricsheet && python scripts/download.py kaggle
```
About 2 minutes.

```
nohup python scripts/download.py davis > data/raw/davis.out 2>&1 &
```
Charles Davis's Test archive. Runs by itself in the background for 2-5 hours.

```
nohup python scripts/download.py espn-bbb > data/raw/espn-bbb.out 2>&1 &
```
Cricinfo ball-by-ball, 1998-2002 (can run at the same time as Davis).

```
nohup python scripts/download.py espn-cards > data/raw/espn-cards.out 2>&1 &
```
Old scorecards, 1970-71 to 1996-97 (2-4 hours).

Check progress any time:
```
python scripts/download.py status
```

If the Codespace stops (idle timeout), just open it again and re-run the same command.

## Tests
```
python tests/test_download.py
```

## Credits
Data from Charles Davis's Test Match Database (sportstats.com.au), Cricsheet (cricsheet.org), ESPNcricinfo,
and the Kaggle ODI dataset by bhuvaneshprasad. Used with permission and thanks.
