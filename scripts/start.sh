#!/usr/bin/env bash
# Start or resume EVERYTHING with one command. Safe to run any time, as often as you like:
#   bash scripts/start.sh
# 1. If this is a new/reset Codespace, brings back every file already saved on GitHub (Releases -> raw-data).
# 2. Starts each download that is not already running (each one continues where it stopped).
# 3. Starts the backup to GitHub (every 20 min) if it is not already running.
# 4. Shows the live tracker (Ctrl+C closes only the board; downloads and backup keep running).
cd "$(dirname "$0")/.." || exit 1
mkdir -p data/raw

python scripts/backup.py restore || echo "(Could not check GitHub for saved files right now; carrying on.)"

for job in davis espn-bbb espn-cards espn-years; do
  if pgrep -f "[d]ownload.py $job" >/dev/null; then
    echo "already running: $job"
  else
    nohup python scripts/download.py "$job" > "data/raw/$job.out" 2>&1 &
    echo "started: $job"
  fi
done

if pgrep -f "[b]ackup.py watch" >/dev/null; then
  echo "already running: backup to GitHub"
else
  nohup python scripts/backup.py watch > data/raw/backup.out 2>&1 &
  echo "started: backup to GitHub (every 20 min)"
fi

sleep 3
python scripts/tracker.py
