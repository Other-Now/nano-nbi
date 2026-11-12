"""End-to-end tests: ncclient -> netopeer2 (NETCONF/SSH) -> sysrepo ->
tunneld -> kernel, all inside one user+net namespace (scripts/ns.sh).

The invariant checked everywhere: the links tunneld owns in the kernel are
exactly what the running datastore says (consistent()).
"""
import os
import subprocess
import threading
import time

import pytest

from nbi import (RPCError, config, connect, delete_all_xml, expected_kernel, get_state,
                 kernel_tunnels, restart_tunneld, running_tunnels, stack, start_tunneld,
                 tunnel_xml, wait_for)

TLOG = os.path.expanduser("~/nn/log/tunneld.log")


@pytest.fixture()
def m():
    s = connect()
    s.edit_config(target="running", config=delete_all_xml())
    assert kernel_tunnels() == {}
    yield s
    if not s.connected:  # the test restarted the server
        s = connect()
    try:
        s.edit_config(target="running", config=delete_all_xml())
    finally:
        s.close_session()


def consistent(m):
    run = running_tunnels(m)
    assert kernel_tunnels() == expected_kernel(run)
    return run


def tx_lines():
    with open(TLOG) as f:
        return sum(1 for l in f if "tunneld: tx " in l)


# ---- 1. YANG rejects bad config before tunneld ever sees it --------------

BAD = {
    "vni on gre": tunnel_xml("g1", type="gre", vni=7),
    "vxlan without vni": tunnel_xml("v1"),
    "vni out of range": tunnel_xml("v1", vni=16777216),
    "local == remote": tunnel_xml("v1", vni=1, remote="10.0.0.1"),
    "name too long": tunnel_xml("averyverylongname", vni=1),
    "name uppercase": tunnel_xml("VX1", vni=1),
    "mtu too small": tunnel_xml("v1", vni=1, mtu=10),
    "bad address": tunnel_xml("v1", vni=1, remote="10.0.0.300"),
}


@pytest.mark.parametrize("case", sorted(BAD))
def test_yang_rejects(m, case):
    before = tx_lines()
    with pytest.raises(RPCError):
        m.edit_config(target="running", config=config(BAD[case]))
    assert tx_lines() == before, "tunneld must not be called for invalid data"
    assert consistent(m) == {}


def test_yang_rejects_duplicate_vni(m):
    before = tx_lines()
    with pytest.raises(RPCError) as e:
        m.edit_config(target="running",
                      config=config(tunnel_xml("v1", vni=5), tunnel_xml("v2", vni=5, remote="10.0.0.9")))
    assert "unique" in str(e.value).lower() or "vni" in str(e.value)
    assert tx_lines() == before
    assert consistent(m) == {}


# ---- 2. lifecycle ------------------------------------------------------

def test_lifecycle(m):
    m.edit_config(target="running", config=config(tunnel_xml("v1", vni=10), tunnel_xml("g1", type="gre", remote="10.0.0.3")))
    run = consistent(m)
    assert set(run) == {"v1", "g1"}
    idx = get_state(m)["v1"]["state"]["if-index"]

    m.edit_config(target="running", config=config(tunnel_xml("v1", vni=10, mtu=1400)))  # in place
    consistent(m)
    assert get_state(m)["v1"]["state"]["if-index"] == idx, "mtu change must not recreate the link"

    m.edit_config(target="running", config=config(tunnel_xml("v1", vni=10, mtu=1400, remote="10.0.0.7")))  # recreate
    consistent(m)
    assert get_state(m)["v1"]["state"]["if-index"] != idx, "endpoint change recreates the link"

    m.edit_config(target="running", config=config(tunnel_xml("g1", type="gre", remote="10.0.0.3", enabled=False)))
    consistent(m)
    assert get_state(m)["g1"]["state"]["oper-status"] == "down"

    m.edit_config(target="running", config=config(tunnel_xml("v1", operation="delete")))
    assert set(consistent(m)) == {"g1"}


# ---- 3. kernel refuses -> whole transaction rolls back --------------------

# z9 has the same GRE endpoints as b1 -> the kernel says EEXIST. Named so it
# sorts last: tunneld applies in datastore (key) order, not document order.
CONFLICT = (tunnel_xml("a1", vni=1), tunnel_xml("b1", type="gre", remote="10.0.0.3"),
            tunnel_xml("z9", type="gre", remote="10.0.0.3"))


def test_kernel_reject_rolls_back(m):
    m.edit_config(target="running", config=config(tunnel_xml("keep", vni=99, remote="10.0.0.50")))
    with pytest.raises(RPCError) as e:
        m.edit_config(target="running", config=config(*CONFLICT))
    assert "File exists" in str(e.value), "kernel error text must reach the client"
    assert set(consistent(m)) == {"keep"}


def test_no_rollback_control_leaves_orphans(m):
    """Same transaction against a tunneld with rollback disabled: the
    datastore still rejects it, but the kernel keeps the links created
    before the failure. This is the bug the undo log exists to prevent."""
    restart_tunneld({"NN_NO_ROLLBACK": "1"})
    try:
        with pytest.raises(RPCError):
            m.edit_config(target="running", config=config(*CONFLICT))
        assert running_tunnels(m) == {}
        orphans = kernel_tunnels()
        assert set(orphans) == {"a1", "b1"}
    finally:
        restart_tunneld()
    assert consistent(m) == {}, "restart flushes and rebuilds from the datastore"


# ---- 4. candidate + confirmed-commit ----------------------------------

def test_candidate_commit(m):
    m.discard_changes()
    m.edit_config(target="candidate", config=config(tunnel_xml("c1", vni=300)))
    assert kernel_tunnels() == {}, "candidate must not touch the kernel"
    m.validate(source="candidate")
    m.commit()
    assert set(consistent(m)) == {"c1"}


def test_confirmed_commit_times_out_and_reverts(m):
    m.edit_config(target="running", config=config(tunnel_xml("base", vni=1)))
    m.discard_changes()
    m.edit_config(target="candidate", config=config(tunnel_xml("risky", vni=2, remote="10.0.0.66")))
    m.commit(confirmed=True, timeout="2")
    assert set(consistent(m)) == {"base", "risky"}
    # no confirming <commit>: after 2 s the server rolls running back,
    # which reaches tunneld as an ordinary change and deletes the link
    assert wait_for(lambda: set(kernel_tunnels()) == {"base"}, 10)
    assert set(consistent(m)) == {"base"}


def test_confirmed_commit_confirmed(m):
    m.discard_changes()
    m.edit_config(target="candidate", config=config(tunnel_xml("ok", vni=3)))
    m.commit(confirmed=True, timeout="2")
    m.commit()  # confirm
    time.sleep(3)
    assert set(consistent(m)) == {"ok"}


# ---- 5. operational state + notifications --------------------------------

def test_get_returns_config_and_state(m):
    """<get> must merge config with tunneld's state. (Regression: with the
    oper subscription at /tunnels, state replaced the config leaves.)"""
    m.edit_config(target="running", config=config(tunnel_xml("s1", vni=42, mtu=1400)))
    t = get_state(m)["s1"]
    assert (t["type"], t["vni"], t["remote"], t["mtu"]) == ("vxlan", "42", "10.0.0.2", "1400")
    assert t["state"]["oper-status"] == "up" and int(t["state"]["if-index"]) > 0


def test_oob_change_notifies(m):
    m.edit_config(target="running", config=config(tunnel_xml("n1", vni=40)))
    n = connect()
    try:
        # (ncclient puts <filter> in the wrong namespace for
        # create-subscription, so subscribe to the whole stream and skip
        # netopeer2's own netconf-rpc-execution events)
        n.create_subscription(stream_name="NETCONF")
        time.sleep(0.5)  # let tunneld's watcher learn the link
        subprocess.run(["ip", "link", "set", "n1", "down"], check=True)
        ours, end = None, time.time() + 5
        while ours is None and time.time() < end:
            msg = n.take_notification(timeout=1)
            if msg is not None and "tunnel-oper-status-change" in msg.notification_xml:
                ours = msg.notification_xml
        assert ours is not None, "no tunnel-oper-status-change within 5 s"
        assert "<name>n1</name>" in ours and "<oper-status>down</oper-status>" in ours
        assert get_state(m)["n1"]["state"]["oper-status"] == "down"
    finally:
        n.close_session()


# ---- 6. crashes --------------------------------------------------------

def test_kill9_mid_transaction_then_restart(m):
    restart_tunneld({"NN_STEP_DELAY_US": "20000"})  # 20 ms per kernel op
    big = config(*[tunnel_xml(f"k{i}", vni=1000 + i, remote=f"10.1.0.{i + 1}") for i in range(50)])
    result = {}

    def send():
        try:
            m2 = connect()
            m2.edit_config(target="running", config=big)
            result["ok"] = True
        except Exception as e:  # noqa: BLE001
            result["err"] = str(e)
    th = threading.Thread(target=send)
    th.start()
    assert wait_for(lambda: len(kernel_tunnels()) >= 10, 10)
    pid = int(open(os.path.expanduser("~/nn/run/tunneld.pid")).read())
    os.kill(pid, 9)
    th.join(60)
    orphans = len(kernel_tunnels())
    assert "err" in result, "commit must fail when the subscriber dies"
    assert running_tunnels(m) == {}, "datastore unchanged"
    assert orphans > 0, "partial apply is visible until restart"
    start_tunneld()
    assert consistent(m) == {}
    print(f"\n  kill -9 mid-tx: {orphans} orphan links before restart, 0 after; error: {result['err'][:80]}")


def test_restart_reconciles_drift(m):
    m.edit_config(target="running", config=config(tunnel_xml("d1", vni=7), tunnel_xml("d2", vni=8, remote="10.0.0.8")))
    subprocess.run(["ip", "link", "del", "d1"], check=True)                       # lost
    subprocess.run(["ip", "link", "set", "d2", "mtu", "1300"], check=True)         # drifted
    subprocess.run(["ip", "link", "add", "zz", "type", "vxlan", "id", "77", "remote", "10.0.0.77",
                    "dstport", "4789"], check=True)
    subprocess.run(["ip", "link", "set", "zz", "alias", "nano-nbi"], check=True)  # stray, ours by alias
    restart_tunneld()
    assert set(consistent(m)) == {"d1", "d2"}


@pytest.mark.parametrize("name", ["a", "up", "mtu", "type", "dev", "help", "name"])
def test_iproute2_keyword_names(m, name):
    """YANG allows these names; iproute2 would read them as (abbreviated)
    keywords unless tunneld passes `name`/`dev` explicitly."""
    m.edit_config(target="running", config=config(tunnel_xml(name, vni=77)))
    assert set(consistent(m)) == {name}
    m.edit_config(target="running", config=config(tunnel_xml(name, vni=77, mtu=1300, enabled=False)))
    consistent(m)
    m.edit_config(target="running", config=config(tunnel_xml(name, operation="delete")))
    assert consistent(m) == {}


def test_slow_apply_timeout_is_undone(m):
    """If tunneld's apply outlives the NETCONF server's sysrepo timeout, the
    client gets an error and the datastore is unchanged, but tunneld is
    still creating links. sysrepo sends it SR_EV_ABORT once it returns,
    and the undo log removes them. The window is real; it closes."""
    subprocess.run([os.path.join(os.path.dirname(__file__), "..", "scripts", "stack.sh"), "stop"],
                   check=True, stdout=subprocess.DEVNULL)
    env = dict(os.environ, NP2_TIMEOUT="2")
    subprocess.run([os.path.join(os.path.dirname(__file__), "..", "scripts", "stack.sh"), "start"],
                   check=True, stdout=subprocess.DEVNULL, env=env)
    try:
        restart_tunneld({"NN_STEP_DELAY_US": "250000"})  # 12 creates * 0.25 s > 2 s
        m2 = connect()
        t = time.time()
        with pytest.raises(RPCError) as e:
            m2.edit_config(target="running", config=config(
                *[tunnel_xml(f"s{i}", vni=500 + i, remote=f"10.3.0.{i + 1}") for i in range(12)]))
        assert "timed out" in str(e.value)
        assert running_tunnels(m2) == {}
        ahead = len(kernel_tunnels())
        assert wait_for(lambda: kernel_tunnels() == {}, 15), "late apply must be undone by ABORT"
        print(f"\n  timeout after {time.time() - t:.1f}s; kernel ahead of datastore by {ahead} links, then 0")
        m2.close_session()
    finally:
        stack("stop")
        stack("start")
