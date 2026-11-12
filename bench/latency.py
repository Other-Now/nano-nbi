"""Where the time goes in one north-bound change. Run inside scripts/ns.sh
with the stack up. Prints JSON. WSL2 numbers: use them for ratios, not as
absolute claims."""
import json
import os
import statistics as st
import subprocess
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tests"))
from nbi import RPCError, config, connect, delete_all_xml, kernel_tunnels, tunnel_xml  # noqa: E402
from pygnmi.client import gNMIclient  # noqa: E402

N = int(sys.argv[1]) if len(sys.argv) > 1 else 200
T = "nano-tunnels:tunnels"
FLT = ("subtree", '<tunnels xmlns="urn:nano-nbi:tunnels"/>')


def summ(xs):
    xs = sorted(xs)
    return {"n": len(xs), "median_ms": round(st.median(xs) * 1e3, 2),
            "p90_ms": round(xs[int(0.9 * (len(xs) - 1))] * 1e3, 2)}


def timed(f, n):
    out = []
    for i in range(n):
        t = time.perf_counter()
        f(i)
        out.append(time.perf_counter() - t)
    return summ(out)


def ip(*a):
    subprocess.run(["ip", *a], check=True)


def raw(_):
    """The 3 `ip` runs tunneld does for one create + one delete."""
    ip("link", "add", "name", "r0", "type", "vxlan", "id", "9", "remote", "10.9.9.9", "dstport", "4789")
    ip("link", "set", "dev", "r0", "mtu", "1450", "alias", "nano-nbi", "up")
    ip("link", "del", "dev", "r0")


def main():
    m = connect()
    m.edit_config(target="running", config=delete_all_xml())
    res = {"raw_ip_create_delete_3_execs": timed(raw, N)}

    def yang_rej(_):
        try:
            m.edit_config(target="running", config=config(tunnel_xml("g", type="gre", vni=1)))
        except RPCError:
            pass
    res["netconf_yang_reject"] = timed(yang_rej, N)

    m.edit_config(target="running", config=config(tunnel_xml("base", vni=1)))
    res["netconf_get_config"] = timed(lambda _: m.get_config(source="running", filter=FLT), N)
    res["netconf_get_with_state"] = timed(lambda _: m.get(filter=FLT), N)

    # ncclient's session thread waits in select(timeout=TICK=0.1 s); a
    # request queued by the caller is only written when that select
    # returns, so every RPC pays up to 100 ms on the client. Same RPCs with
    # the tick at 1 ms (TICK is a module global read on every loop):
    import ncclient.transport.session as ncs
    old = ncs.TICK
    ncs.TICK = 0.001
    try:
        f = connect()
        f.edit_config(target="running", config=config(tunnel_xml("base", vni=1)))

        def yang_rej_fast(_):
            try:
                f.edit_config(target="running", config=config(tunnel_xml("g", type="gre", vni=1)))
            except RPCError:
                pass
        res["tick1ms_netconf_yang_reject"] = timed(yang_rej_fast, N)
        res["tick1ms_netconf_get_config"] = timed(lambda _: f.get_config(source="running", filter=FLT), N)
        res["tick1ms_netconf_get_with_state"] = timed(lambda _: f.get(filter=FLT), N)
        fc = []
        for _ in range(N):
            t = time.perf_counter()
            f.edit_config(target="running", config=config(tunnel_xml("x", vni=2, remote="10.0.0.9")))
            fc.append(time.perf_counter() - t)
            f.edit_config(target="running", config=config(tunnel_xml("x", operation="delete")))
        res["tick1ms_netconf_create_1"] = summ(fc)
        f.close_session()
    finally:
        ncs.TICK = old

    cre, dele = [], []
    for _ in range(N):
        t = time.perf_counter()
        m.edit_config(target="running", config=config(tunnel_xml("x", vni=2, remote="10.0.0.9")))
        cre.append(time.perf_counter() - t)
        t = time.perf_counter()
        m.edit_config(target="running", config=config(tunnel_xml("x", operation="delete")))
        dele.append(time.perf_counter() - t)
    res["netconf_create_1"] = summ(cre)
    res["netconf_delete_1"] = summ(dele)

    def rollback(_):
        try:
            m.edit_config(target="running", config=config(
                tunnel_xml("a1", vni=3), tunnel_xml("b1", type="gre", remote="10.0.0.3"),
                tunnel_xml("z9", type="gre", remote="10.0.0.3")))
        except RPCError:
            pass
    res["netconf_kernel_reject_undo2"] = timed(rollback, max(20, N // 4))
    k = kernel_tunnels()
    assert set(k) == {"base"}, f"after rollback loop kernel={sorted(k)}"

    with gNMIclient(target=("127.0.0.1", 57400), insecure=True) as g:
        gs = []
        for _ in range(N // 2):
            t = time.perf_counter()
            g.set(update=[(f"{T}/tunnel[name=y]", {"name": "y", "type": "vxlan", "vni": 4,
                                                    "local": "10.0.0.1", "remote": "10.0.0.8"})],
                  encoding="json_ietf")
            gs.append(time.perf_counter() - t)
            g.set(delete=[f"{T}/tunnel[name=y]"], encoding="json_ietf")
        res["gnmi_create_1"] = summ(gs)
        res["gnmi_get_1"] = timed(lambda _: g.get(path=[f"{T}/tunnel[name=base]"], encoding="json_ietf"), N // 2)

    m.edit_config(target="running", config=delete_all_xml())
    batch = {}
    for k in (1, 10, 100, 500):
        xml = config(*[tunnel_xml(f"b{i}", vni=100 + i, remote=f"10.2.{i // 250}.{i % 250 + 1}") for i in range(k)])
        t = time.perf_counter()
        m.edit_config(target="running", config=xml)
        c = time.perf_counter() - t
        assert len(kernel_tunnels()) == k
        t = time.perf_counter()
        m.edit_config(target="running", config=delete_all_xml())
        d = time.perf_counter() - t
        batch[k] = {"create_s": round(c, 3), "per_tunnel_ms": round(c / k * 1e3, 2), "delete_all_s": round(d, 3)}
    res["netconf_batch_create"] = batch
    print(json.dumps(res, indent=1))


main()
