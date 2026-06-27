#!/usr/bin/env bash
# WI-OH-4 real-boot verification: boot the REAL backend lifespan with the
# flagged repo config on an isolated port + isolated user-data dir, capture
# the production startup log, and grep for the curation wiring event.
# This runs the actual lifespan (main.py:2648) — NOT a script replay.
set -u

REPO="/path/to/deskpet"
VENV_PY="$REPO/backend/.venv/Scripts/python.exe"
UD="$REPO/.oh4-verify-userdata"
LOG="$REPO/plans/oh4-verify-boot.log"

rm -rf "$UD"; mkdir -p "$UD"
rm -f "$LOG"

export DESKPET_CONFIG="$REPO/config.toml"          # flagged config (curation_nudge=true)
export DESKPET_BACKEND_PORT="8137"                  # isolated, avoid prod 8100
export DESKPET_USER_DATA_DIR="$UD"                  # isolated state.db
export DESKPET_DEV_MODE="1"

cd "$REPO/backend" || exit 3
# uvicorn blocks; kill after the startup lifespan has run (oh4 logs at boot).
"$VENV_PY" main.py > "$LOG" 2>&1 &
PID=$!
# poll up to 60s for the wiring (or skip) event, then stop.
for i in $(seq 1 60); do
  if grep -qE "oh4_curation_(nudge_wired|skipped)" "$LOG" 2>/dev/null; then
    break
  fi
  if ! kill -0 "$PID" 2>/dev/null; then
    echo "[verify] backend exited early" >> "$LOG"
    break
  fi
  sleep 1
done
kill "$PID" 2>/dev/null
sleep 1
kill -9 "$PID" 2>/dev/null

echo "=================== OH4 / facts events ==================="
grep -nE "oh4_curation|p4_fact_extractor_ready|p4_services_registered|memory_tools.bind|p4_services_registration_failed|curation_nudge" "$LOG" 2>/dev/null
echo "=================== tail (last 8 lines) ==================="
tail -8 "$LOG" 2>/dev/null
