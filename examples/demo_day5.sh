#!/usr/bin/env bash
# Day 5 demo: clean capture, an interrupted capture whose recorder outlives its supervisor, deferred preparation
# while another capture records, and the later recovery. STAND-IN recorder (no ROS 2), local disk, no systemd.
# Run from the repository root after `pip install -e ".[dev]"`:   bash examples/demo_day5.sh
set -u
W="$(mktemp -d)"; C="physicore-collector"; mkdir -p "$W/bin" "$W/caps"
python - "$W" <<'PY'
import sys; from pathlib import Path
from tests.recorder_doubles import make_fake_ros2
from tests.test_supervisor import PROFILE_11
make_fake_ros2(Path(sys.argv[1]) / "bin"); (Path(sys.argv[1]) / "p.yaml").write_text(PROFILE_11)
PY
export PATH="$W/bin:$PATH" FAKE_ROS2_EARLY_BAG=1
run() { local n=$1; shift; "$@" > "$W/o.txt" 2>&1; local rc=$?; head -n "$n" "$W/o.txt"; echo "[exit $rc]"; }
pid() { python -c "import json,sys;print(json.load(open('$W/caps/$1/capture_health.json'))['$2']['identity']['pid'])"; }
waitbag() { for _ in $(seq 100); do [ -d "$W/caps/$1/bag" ] && return; sleep 0.1; done; }

echo "### 1. clean capture: package prepared automatically (capture exit code is the capture's own)"
run 60 $C capture run $W/p.yaml --out $W/caps --duration 1 --session-id clean | grep -E "state:|Incident package|^\[exit"
echo; echo "### 2. interrupted capture: the supervisor is killed, its recorder keeps running"
$C capture start $W/p.yaml --out $W/caps --session-id crashed --no-auto-prepare > /dev/null; waitbag crashed
SUP=$(pid crashed supervisor); REC=$(pid crashed recorder); kill -9 $SUP; sleep 0.5
echo "supervisor $SUP killed; recorder $REC still alive: $(kill -0 $REC 2>/dev/null && echo yes || echo no)"
$C incident pending list $W/caps
echo; echo "### 3. preparing it now would package a recording that may still be written: deferred, recorder untouched"
run 4 $C incident prepare $W/caps/crashed
echo "recorder $REC still alive: $(kill -0 $REC 2>/dev/null && echo yes || echo no)"
echo; echo "### 4. a new capture starts; draining the queue now must not compete with it"
$C capture start $W/p.yaml --out $W/caps --session-id live --no-auto-prepare > /dev/null; waitbag live
run 3 $C incident pending run $W/caps
echo; echo "### 5. the live capture stops; the orphan is stopped ONLY on explicit request (identity-verified), then prepared"
$C capture stop $W/caps/live > /dev/null; sleep 0.5
run 6 $C incident pending run $W/caps --stop-orphan
echo; echo "### 6. what the package says about it: degraded, interrupted, bytes verified now"
$C incident show $W/caps/crashed --verify | grep -E "^# Incident|Capture health|Artifact integrity|VERIFIED NOW|TAMPERED|Why this"
echo; $C incident list $W/caps --verify | cut -c1-150
echo; echo "(workspace: $W)"
