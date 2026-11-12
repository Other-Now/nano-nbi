"""Helpers shared by the tests and benchmarks: NETCONF session, XML
builders, and a view of what the kernel actually holds."""
import json
import os
import subprocess
import time

from ncclient import manager
from ncclient.operations.rpc import RPCError
from ncclient.xml_ import to_ele

NS = "urn:nano-nbi:tunnels"
PORT = int(os.environ.get("NN_PORT", "8300"))
KEY = os.environ.get("NN_KEY", os.path.expanduser("~/nn/client_ed25519"))
PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

__all__ = ["connect", "tunnel_xml", "config", "delete_all_xml", "running_tunnels",
           "kernel_tunnels", "RPCError", "stack", "restart_tunneld", "wait_for", "NS", "PROJ"]


def connect():
    return manager.connect(host="127.0.0.1", port=PORT, username="root", key_filename=KEY,
                           hostkey_verify=False, allow_agent=False, look_for_keys=False,
                           timeout=30)


def tunnel_xml(name, type="vxlan", vni=None, local="10.0.0.1", remote="10.0.0.2",
               mtu=None, enabled=None, operation=None):
    op = f' xmlns:nc="urn:ietf:params:xml:ns:netconf:base:1.0" nc:operation="{operation}"' if operation else ""
    s = f"<tunnel{op}><name>{name}</name>"
    if operation != "delete":
        s += f"<type>{type}</type>"
        if vni is not None:
            s += f"<vni>{vni}</vni>"
        s += f"<local>{local}</local><remote>{remote}</remote>"
        if mtu is not None:
            s += f"<mtu>{mtu}</mtu>"
        if enabled is not None:
            s += f"<enabled>{'true' if enabled else 'false'}</enabled>"
    return s + "</tunnel>"


def config(*tunnels, operation=None):
    op = f' xmlns:nc="urn:ietf:params:xml:ns:netconf:base:1.0" nc:operation="{operation}"' if operation else ""
    return (f'<config xmlns="urn:ietf:params:xml:ns:netconf:base:1.0">'
            f'<tunnels xmlns="{NS}"{op}>{"".join(tunnels)}</tunnels></config>')


def delete_all_xml():
    return config(operation="remove")


def running_tunnels(m, source="running"):
    """{name: {leaf: value}} from a NETCONF get-config."""
    flt = f'<tunnels xmlns="{NS}"/>'
    r = m.get_config(source=source, filter=("subtree", flt))
    return _parse(r.data_ele)


def _parse(ele):
    out = {}
    for t in ele.iter(f"{{{NS}}}tunnel"):
        d = {}
        for c in t:
            tag = c.tag.split("}")[1]
            if tag == "state":
                d["state"] = {s.tag.split("}")[1]: s.text for s in c}
            else:
                d[tag] = c.text
        out[d["name"]] = d
    return out


def get_state(m):
    r = m.get(filter=("subtree", f'<tunnels xmlns="{NS}"/>'))
    return _parse(r.data_ele)


def kernel_tunnels():
    """{name: {type, vni, local, remote, mtu, up}} for links tunneld owns
    (ifalias nano-nbi), read straight from the kernel with `ip -d -j`."""
    raw = subprocess.run(["ip", "-d", "-j", "link", "show"], capture_output=True, text=True,
                         check=True).stdout
    # Ubuntu's iproute2 6.1 carries the "ubuntu-fan" patch, which prints a
    # bare `fan-map ` token into the JSON of every VXLAN link
    # ("remote":"10.0.0.2"fan-map ,"local":...). Strip it or json fails.
    raw = raw.replace('"fan-map ,', '",').replace('fan-map ,', ',')
    out = {}
    for l in json.loads(raw):
        if l.get("ifalias") != "nano-nbi":
            continue
        info = l.get("linkinfo", {})
        kind = info.get("info_kind")
        data = info.get("info_data", {})
        d = {"type": kind, "local": data.get("local"), "remote": data.get("remote"),
             "mtu": str(l["mtu"]), "up": "UP" in l.get("flags", [])}
        if kind == "vxlan":
            d["vni"] = str(data.get("id"))
        out[l["ifname"]] = d
    return out


def expected_kernel(running):
    out = {}
    for n, t in running.items():
        d = {"type": t["type"], "local": t["local"], "remote": t["remote"],
             "mtu": t.get("mtu", "1450"), "up": t.get("enabled", "true") == "true"}
        if t["type"] == "vxlan":
            d["vni"] = t["vni"]
        out[n] = d
    return out


def stack(cmd):
    subprocess.run([os.path.join(PROJ, "scripts", "stack.sh"), cmd], check=True,
                   stdout=subprocess.DEVNULL)


class TunneldStartError(RuntimeError):
    pass


def stop_tunneld(kill9=False):
    run = os.path.expanduser("~/nn/run/tunneld.pid")
    pid = int(open(run).read())
    subprocess.run(["kill", "-9" if kill9 else "-TERM", str(pid)], stderr=subprocess.DEVNULL)
    wait_for(lambda: not os.path.exists(f"/proc/{pid}") or
             open(f"/proc/{pid}/stat").read().split()[2] == "Z", 5)


def restart_tunneld(env=None, kill9=False):
    stop_tunneld(kill9)
    return start_tunneld(env)


def start_tunneld(env=None):
    """Start tunneld and wait for "ready". Raises TunneldStartError if it
    exits first (e.g. its startup apply of the running config fails) or
    never gets ready. (An earlier version returned silently, and a
    benchmark ran with no tunneld at all: sysrepo commits edits that have
    no subscriber, so they never reached the kernel.)"""
    log = os.path.expanduser("~/nn/log/tunneld.log")
    start = os.path.getsize(log) if os.path.exists(log) else 0
    e = dict(os.environ)
    e.pop("NN_NO_ROLLBACK", None)
    e.pop("NN_STEP_DELAY_US", None)
    e.update(env or {})
    p = subprocess.Popen([os.path.join(os.environ.get("NN_BUILD", os.path.expanduser("~/nn-build")), "tunneld")], env=e,
                         stdout=open(log, "a"), stderr=subprocess.STDOUT)
    open(os.path.expanduser("~/nn/run/tunneld.pid"), "w").write(str(p.pid))

    def tail():
        with open(log) as f:
            f.seek(start)
            return f.read()
    if not wait_for(lambda: "tunneld: ready" in tail() or p.poll() is not None, 15) \
            or "tunneld: ready" not in tail():
        p.kill()
        raise TunneldStartError(tail().strip().splitlines()[-1] if tail().strip() else "no output")
    return p


def wait_for(pred, timeout, step=0.05):
    end = time.time() + timeout
    while time.time() < end:
        if pred():
            return True
        time.sleep(step)
    return False


def consistent_kernel(running):
    return kernel_tunnels() == expected_kernel(running)
