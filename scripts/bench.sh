#!/usr/bin/env bash
# Run both benchmarks in a fresh namespace; JSON goes to results/$RESULTS_TAG.
# env: N (tx per consistency run, default 300), SEED (default 1), RESULTS_TAG (default wsl)
set -euo pipefail
source "$(dirname "$0")/env.sh"
mkdir -p "$NN_HOME/log"; : >"$NN_HOME/log/tunneld.log"
export PY=${NN_PY:-$HOME/nn-venv/bin/python} N=${N:-300} SEED=${SEED:-1} RESULTS_TAG=${RESULTS_TAG:-wsl}
exec "$(dirname "$0")/ns.sh" bash "$NN_PROJ/scripts/bench-inner.sh"
