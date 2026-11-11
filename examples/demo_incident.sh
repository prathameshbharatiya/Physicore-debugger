#!/usr/bin/env bash
# End-to-end demo of incident preparation on SYNTHETIC recordings (no ROS 2, no network, no robot).
# Run from the repository root after `pip install -e ".[dev]"`:   bash examples/demo_incident.sh
set -u
W="$(mktemp -d)"; P=examples/profiles/synthetic_fixture.profile.yaml; C="physicore-collector"
mkdir -p "$W/inc"
python - "$W" <<'PY'
import sys; from pathlib import Path
from tests.bagtools import three_way_split, write_bag
write_bag(Path(sys.argv[1]) / "bags" / "run12", three_way_split())   # a 3-segment rosbag2 directory
PY
run() {  # run a command, show the first N lines of its output, report its REAL exit code (no pipe masking it)
  local n=$1; shift; "$@" > "$W/out.txt" 2>&1; local rc=$?; head -n "$n" "$W/out.txt"; echo "[exit $rc]"
}
echo "### 1. a multi-segment bag: point at ONE segment, get the whole bag"
run 14 $C incident prepare "$W/bags/run12/run12_1.mcap" --profile $P --out "$W/inc/run12"
echo; echo "### 2. same command again: idempotent, nothing written"
run 2 $C incident prepare "$W/bags/run12" --profile $P --out "$W/inc/run12"
echo; echo "### 3. a recording with an observed cadence gap: verified bytes, DEGRADED evidence"
run 18 $C incident prepare examples/fixtures/cadence_gap.mcap --profile $P --out "$W/inc/gap"
echo; echo "### 4. list and re-verify offline"
$C incident list "$W/inc"; run 3 $C incident show "$W/inc/run12" --verify
echo; echo "### 5. refuses to overwrite"
run 2 $C incident prepare examples/fixtures/valid.mcap --profile $P --out "$W/inc/run12"
echo; echo "(workspace: $W)"
