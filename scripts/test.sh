#!/usr/bin/env bash
# Fresh repo + full test suite inside a throwaway namespace.
# usage: scripts/test.sh [pytest args]
set -euo pipefail
source "$(dirname "$0")/env.sh"
mkdir -p "$NN_HOME/log"; : >"$NN_HOME/log/tunneld.log"; : >"$NN_HOME/log/np2.log"
export PY=${NN_PY:-$HOME/nn-venv/bin/python}
exec "$(dirname "$0")/ns.sh" bash -c '"$NN_PROJ/scripts/setup-repo.sh" >/dev/null && cd "$NN_PROJ/tests" && exec "$PY" -m pytest -q -p no:cacheprovider "$@"' t "$@"
