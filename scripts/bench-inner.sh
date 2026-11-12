#!/usr/bin/env bash
# Called by bench.sh inside the namespace.
set -euo pipefail
"$NN_PROJ/scripts/setup-repo.sh" >/dev/null
"$NN_PROJ/scripts/stack.sh" start >/dev/null
trap '"$NN_PROJ/scripts/stack.sh" stop >/dev/null' EXIT
R="$NN_PROJ/results/$RESULTS_TAG"; mkdir -p "$R"
if [ -z "${LATENCY_ONLY:-}" ]; then
: >"$R/consistency.jsonl"
for s in $(seq "$SEED" $((SEED + 2))); do
  "$PY" "$NN_PROJ/bench/consistency.py" "$N" "$s"               | tee -a "$R/consistency.jsonl"
  "$PY" "$NN_PROJ/bench/consistency.py" "$N" "$s" --no-rollback | tee -a "$R/consistency.jsonl"
done
fi
"$PY" "$NN_PROJ/bench/latency.py" 200 | tee "$R/latency.json"
: >"$R/del_cost.jsonl"
for _ in 1 2 3; do unshare --net bash -c 'ip link set lo up; "$0"/bench/del_cost.sh 200' "$NN_PROJ" | tee -a "$R/del_cost.jsonl"; done
