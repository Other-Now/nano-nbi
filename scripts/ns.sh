#!/usr/bin/env bash
# Run a command as "root" inside a fresh user+network namespace (no sudo).
# netopeer2, tunneld and the tests all live in here, so the tunnels they
# create are invisible to the host and vanish when the namespace exits.
exec unshare --user --map-root-user --net bash -c 'ip link set lo up && exec "$@"' ns "$@"
