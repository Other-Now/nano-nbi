import sys, os
sys.path.insert(0, os.path.dirname(__file__))
from nbi import *
from nbi import get_state, expected_kernel
m = connect()
print("caps:", [c for c in m.server_capabilities if "nano" in c or "candidate" in c or "confirmed" in c])
m.edit_config(target="running", config=config(tunnel_xml("vx1", vni=100), tunnel_xml("g1", type="gre", remote="10.0.0.3")))
print("running:", running_tunnels(m))
print("kernel :", kernel_tunnels())
print("state  :", {k: v.get("state") for k, v in get_state(m).items()})
try:
    m.edit_config(target="running", config=config(tunnel_xml("g2", type="gre", remote="10.0.0.3")))
except RPCError as e:
    print("REJECTED:", e.message)
print("after reject kernel:", sorted(kernel_tunnels()), "running:", sorted(running_tunnels(m)))
