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
Charles Davis's Test archive: about 11,400 PDFs, one every 1.5 seconds (his site is small, so we go slowly):
about 9-10 hours in the background.

```
nohup python scripts/download.py espn-bbb > data/raw/espn-bbb.out 2>&1 &
```
Cricinfo ball-by-ball, 1998-2002 (can run at the same time as Davis).

```
nohup python scripts/download.py espn-cards > data/raw/espn-cards.out 2>&1 &
```
Old scorecards, 1970-71 to 1996-97 (2-4 hours).

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

## Tests
```
python tests/test_download.py && python tests/test_backup.py
```

## Credits
Data from Charles Davis's Test Match Database (sportstats.com.au), Cricsheet (cricsheet.org), ESPNcricinfo,
and the Kaggle ODI dataset by bhuvaneshprasad. Used with permission and thanks.
