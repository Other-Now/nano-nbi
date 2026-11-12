#!/usr/bin/env bash
# start|stop netopeer2-server, tunneld and the gNMI gateway (inside ns.sh).
# Logs in $NN_HOME/log. NP2_TIMEOUT (s): sysrepo callback timeout for NETCONF edits
# (default 20: below ncclient's 30 s client timeout, above a 500-tunnel apply; sysrepo's own 5 s default is shorter than a 500-tunnel apply).
source "$(dirname "$0")/env.sh"
LOG=$NN_HOME/log; RUN=$NN_HOME/run
mkdir -p "$LOG" "$RUN" "$NN_HOME/srv"
PY=${PY:-$HOME/nn-venv/bin/python}
GNMI_PORT=${GNMI_PORT:-57400}
up() { (exec 3<>"/dev/tcp/127.0.0.1/$1") 2>/dev/null; }
case "${1:-}" in
start)
  "$NN_BUILD/tunneld" >>"$LOG/tunneld.log" 2>&1 & echo $! >"$RUN/tunneld.pid"
  netopeer2-server -d -v1 -t "${NP2_TIMEOUT:-20}" -p "$RUN/np2.pid" >>"$LOG/np2.log" 2>&1 & echo $! >"$RUN/np2.pid.bg"
  "$PY" "$NN_PROJ/gnmi/gnmi_gateway.py" --listen "127.0.0.1:$GNMI_PORT" >>"$LOG/gnmi.log" 2>&1 & echo $! >"$RUN/gnmi.pid"
  for _ in $(seq 100); do
    up "$NN_PORT" && up "$GNMI_PORT" && grep -q "tunneld: ready" "$LOG/tunneld.log" && { echo "stack: up"; exit 0; }
    sleep 0.1
  done
  echo "stack: FAILED to come up"; tail -20 "$LOG"/*.log; exit 1 ;;
stop)
  for p in gnmi.pid tunneld.pid np2.pid.bg; do [ -f "$RUN/$p" ] && kill "$(cat "$RUN/$p")" 2>/dev/null; done
  sleep 0.3; echo "stack: down" ;;
*) echo "usage: $0 start|stop"; exit 2 ;;
esac
