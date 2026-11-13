#!/bin/bash
# Keeps the lock UI running: restarts it after a crash, stops when an admin
# chooses "Exit app" (exit code 0).  Extra arguments are passed to main.py.
cd "$(dirname "$0")/.." || exit 1
while true; do
    python3 main.py "$@"
    status=$?
    [ "$status" -eq 0 ] && exit 0
    echo "smartlock exited with status $status, restarting in 3 s" >&2
    sleep 3
done
