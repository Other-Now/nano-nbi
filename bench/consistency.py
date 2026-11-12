"""Randomized transactions against the live stack; after every one, compare
the kernel with the running datastore.

Conflicts are not injected: GRE tunnels draw endpoints from a pool of 4
remotes, so two GRE tunnels with the same (local, remote) occur naturally
and the kernel refuses the second with EEXIST. VNIs come from a pool of 60,
so duplicate VNIs (rejected by YANG `unique`) occur too.

usage: consistency.py N SEED [--no-rollback]   (inside scripts/ns.sh, stack up)
"""
import json
import os
import random
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tests"))
from nbi import (RPCError, config, connect, delete_all_xml, expected_kernel,  # noqa: E402
                 kernel_tunnels, restart_tunneld, running_tunnels, tunnel_xml)

N, SEED = int(sys.argv[1]), int(sys.argv[2])
NO_RB = "--no-rollback" in sys.argv
rng = random.Random(SEED)
NAMES = [f"t{i}" for i in range(40)]
REMOTES = [f"10.0.1.{i}" for i in range(1, 5)]


def rand_tunnel(name):
    if rng.random() < 0.5:
        return tunnel_xml(name, type="gre", remote=rng.choice(REMOTES), mtu=rng.choice([1400, 1450, 1476]))
    return tunnel_xml(name, vni=rng.randint(1, 60), remote=rng.choice(REMOTES), mtu=rng.choice([1400, 1450]))


def main():
    restart_tunneld({"NN_NO_ROLLBACK": "1"} if NO_RB else None)
    m = connect()
    m.edit_config(target="running", config=delete_all_xml())
    s = dict(mode="no-rollback" if NO_RB else "rollback", n=N, seed=SEED, committed=0,
             yang_reject=0, kernel_reject=0, stale_reject=0, diverged_after=0, first_divergence=None,
             max_orphans=0, max_missing=0)
    t0 = time.time()
    for i in range(N):
        run = running_tunnels(m)
        ops, seen = [], set()
        for _ in range(rng.randint(1, 4)):
            if run and rng.random() < 0.25:
                name = rng.choice(list(run))
                x = tunnel_xml(name, operation="delete")
            else:
                name = rng.choice(NAMES)
                x = rand_tunnel(name)
            if name not in seen:  # one operation per name per edit
                seen.add(name)
                ops.append(x)
        try:
            m.edit_config(target="running", config=config(*ops))
            s["committed"] += 1
        except RPCError as e:
            msg = str(e)
            if "Cannot find device" in msg:
                # tunneld's own bookkeeping is wrong: it tried to delete or
                # modify a link it believes exists. Only possible after an
                # earlier divergence.
                s["stale_reject"] += 1
            elif "RTNETLINK" in msg:
                s["kernel_reject"] += 1
            else:
                s["yang_reject"] += 1
        want, have = expected_kernel(running_tunnels(m)), kernel_tunnels()
        if want != have:
            s["diverged_after"] += 1
            if s["first_divergence"] is None:
                s["first_divergence"] = i
            s["max_orphans"] = max(s["max_orphans"], len(set(have) - set(want)))
            s["max_missing"] = max(s["max_missing"], len(set(want) - set(have)))
    s["seconds"] = round(time.time() - t0, 1)
    # can the operator still clean up through the NBI?
    try:
        m.edit_config(target="running", config=delete_all_xml())
        s["delete_all_via_nbi"] = "ok"
    except RPCError as e:
        s["delete_all_via_nbi"] = "rejected: " + str(e)[:80]
    print(json.dumps(s), flush=True)
    restart_tunneld()  # flush + rebuild from the datastore
    m.edit_config(target="running", config=delete_all_xml())
    assert kernel_tunnels() == {}


main()
