# Everyball

Every Test since 1877. Every ODI since 1971. Ball by ball wherever it exists.

See **PLAN.md** for the full plan and progress.

## Start or resume everything (one command)

```
bash scripts/start.sh
```
Safe to run any time, as often as you like (for example after the Codespace stopped, or in a brand-new Codespace).
It brings back any files saved on GitHub, starts each download that isn't running (each continues where it
stopped), starts the backup to GitHub, and shows the live tracker.

## Safe copy on GitHub (nothing is lost)

Every 20 minutes, new downloaded files are zipped and uploaded to this private repo under
**Releases -> raw-data** (`part-0001.zip`, `part-0002.zip`, ...). The tracker shows a "Backup to GitHub" line.
If the Codespace stops, nothing is lost (its disk keeps the files). Even if it is deleted, `bash scripts/start.sh`
in a new Codespace brings every saved file back first.

```
python scripts/backup.py status     # how much is saved
python scripts/backup.py now        # save right now (e.g. after `retry`)
python scripts/backup.py laptop     # when all is done: EVERYTHING.zip on the Releases page, one click to your laptop
```

## Download the data one by one (Step 3)

`start.sh` does all of this for you. Run these in the Codespace terminal only if you want one source at a time.
Each one is safe to stop (Ctrl+C) and run again: it continues where it left off.

```
python scripts/download.py cricsheet && python scripts/download.py kaggle
```
About 2 minutes.

```
nohup python scripts/download.py davis > data/raw/davis.out 2>&1 &
```
Charles Davis's Test archive (1877 to Nov 2020): about 16,600 links, of which about 6,000 are dead links on his
own pages (e.g. Tests 3-6 of a 2-Test series). Every link is asked for once; "not found" goes to
`data/raw/dead_davis.txt` and is never retried. One request at a time with a 0.75 s pause: about 6-7 hours.

```
nohup python scripts/download.py espn-bbb > data/raw/espn-bbb.out 2>&1 &
```
Cricinfo ball-by-ball, 1998-2002 (can run at the same time as Davis).

```
nohup python scripts/download.py espn-cards > data/raw/espn-cards.out 2>&1 &
```
Old scorecards, two-year seasons 1970-71 to 1996-97 (1-2 hours).

```
nohup python scripts/download.py espn-years > data/raw/espn-years.out 2>&1 &
```
The one-year folders 1971-2002, where the archive keeps matches played in England (1-2 hours).

If a site ever refuses us, the downloaders slow down by themselves and ask again later (the tracker says so).
A refusal is never counted as a dead link.

## Live tracker
```
python scripts/tracker.py
```
Shows each download: done / target found so far, speed, time left. Refreshes every 30 s. Ctrl+C closes only the board.

On your phone: run `python -m http.server 8080 --directory data/raw` in a second terminal, open the **Ports** tab,
open port **8080** in the browser and add `/status.html` (it refreshes by itself). The link only works for you
(logged in to GitHub).

When everything has finished, try the few failed files once more:
```
python scripts/download.py retry
```

If the Codespace stops (idle timeout), just open it again and run `bash scripts/start.sh`.

## Build the database (Step 5)

One-time setup (the PDF reader): `sudo apt-get update && sudo apt-get install -y poppler-utils`

```
python scripts/build_db.py all
```
Reads everything in `data/raw/` into one database file, `data/everyball.db` (never sent to GitHub; it can always be
rebuilt). Steps can also run one at a time, in this order: `davis`, `cricsheet`, `book`, `kaggle`, `espn`, `checks`
(Kaggle gives every ODI its number; ESPN's files are then matched to those numbers by date and teams, so ESPN's
domestic, tour and women's games are left out).
Your Wisden book data goes in `data/raw/book/` (the CSV files from cricket-project/data/).

The last step writes `data/build_report.txt`: what was read, what didn't add up, which Tests/ODIs are missing
compared with ESPN's own year-by-year lists, and famous facts the database must get exactly right.

## Ask the database
```
python scripts/ask.py duel Tendulkar McGrath          # every ball between them
python scripts/ask.py duel Tendulkar McGrath Test
python scripts/ask.py player Dravid
python scripts/ask.py sql "SELECT player, SUM(runs) FROM bat WHERE format='Test' GROUP BY 1 ORDER BY 2 DESC LIMIT 10"
```
Easy tables: `bat`, `bowl`, `ball` (every ball, with names), `matches`, `players`.

## Tests
```
python tests/test_download.py && python tests/test_backup.py && python tests/test_build.py
```

## Credits
Data from Charles Davis's Test Match Database (sportstats.com.au), Cricsheet (cricsheet.org), ESPNcricinfo,
and the Kaggle ODI dataset by bhuvaneshprasad. Used with permission and thanks.
