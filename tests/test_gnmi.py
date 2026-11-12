"""gNMI and NETCONF are two views of one datastore: a change made through
either is visible through the other and reaches the kernel through the same
tunneld transaction."""
import json

import pytest
from pygnmi.client import gNMIclient

from nbi import (config, connect, consistent_kernel, delete_all_xml, kernel_tunnels,
                 running_tunnels, tunnel_xml)

T = "nano-tunnels:tunnels"


@pytest.fixture()
def g():
    n = connect()
    n.edit_config(target="running", config=delete_all_xml())
    with gNMIclient(target=("127.0.0.1", 57400), insecure=True) as c:
        yield c, n
    n.edit_config(target="running", config=delete_all_xml())
    n.close_session()


def tun(name, **kw):
    d = {"name": name, "type": "vxlan", "vni": 10, "local": "10.0.0.1", "remote": "10.0.0.2"}
    d.update(kw)
    return d


def test_capabilities(g):
    c, _ = g
    caps = c.capabilities()
    assert any(m["name"] == "nano-tunnels" for m in caps["supported_models"])
    assert "json_ietf" in caps["supported_encodings"]


def test_set_via_gnmi_visible_via_netconf(g):
    c, n = g
    c.set(update=[(f"{T}/tunnel[name=gv1]", tun("gv1", vni=500))], encoding="json_ietf")
    run = running_tunnels(n)
    assert run["gv1"]["vni"] == "500"
    assert consistent_kernel(run)


def test_netconf_change_visible_via_gnmi_get(g):
    c, n = g
    n.edit_config(target="running", config=config(tunnel_xml("nv1", vni=600, mtu=1400)))
    r = c.get(path=[f"{T}/tunnel[name=nv1]/mtu"], encoding="json_ietf")
    assert r["notification"][0]["update"][0]["val"] == 1400
    st = c.get(path=[f"{T}/tunnel[name=nv1]/state"], encoding="json_ietf")
    assert st["notification"][0]["update"][0]["val"]["oper-status"] == "up"


def test_gnmi_leaf_update_and_delete(g):
    c, n = g
    c.set(update=[(f"{T}/tunnel[name=a]", tun("a"))], encoding="json_ietf")
    c.set(update=[(f"{T}/tunnel[name=a]/mtu", 1300)], encoding="json_ietf")
    assert kernel_tunnels()["a"]["mtu"] == "1300"
    c.set(delete=[f"{T}/tunnel[name=a]"], encoding="json_ietf")
    assert kernel_tunnels() == {} and running_tunnels(n) == {}


def test_gnmi_set_is_atomic_on_kernel_reject(g):
    """3 updates in one SetRequest, the last one refused by the kernel:
    nothing may remain, in the datastore or in the kernel."""
    c, n = g
    ups = [(f"{T}/tunnel[name=a1]", tun("a1", vni=1)),
           (f"{T}/tunnel[name=b1]", {"name": "b1", "type": "gre", "local": "10.0.0.1", "remote": "10.0.0.3"}),
           (f"{T}/tunnel[name=z9]", {"name": "z9", "type": "gre", "local": "10.0.0.1", "remote": "10.0.0.3"})]
    with pytest.raises(Exception) as e:
        c.set(update=ups, encoding="json_ietf")
    assert "File exists" in str(e.value)
    assert running_tunnels(n) == {} and kernel_tunnels() == {}


def test_gnmi_yang_reject(g):
    c, n = g
    with pytest.raises(Exception):
        c.set(update=[(f"{T}/tunnel[name=bad]", {"name": "bad", "type": "gre", "vni": 5,
                                                 "local": "10.0.0.1", "remote": "10.0.0.2"})],
              encoding="json_ietf")
    assert running_tunnels(n) == {} and kernel_tunnels() == {}
