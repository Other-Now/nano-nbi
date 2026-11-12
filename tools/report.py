"""results/<tag>/{consistency.jsonl,latency.json} -> markdown."""
import json
import sys
from pathlib import Path

d = Path(sys.argv[1])
print(f"## nano-nbi results ({d.name})\n")

rows = [json.loads(l) for l in (d / "consistency.jsonl").read_text().splitlines() if l.strip()]
print("### Datastore vs kernel after every randomized transaction\n")
print("| mode | seed | tx | committed | YANG reject | kernel reject (conflict) | rejected: tunneld's own state stale | tx after which kernel != running | max orphans | max missing | delete-all via NBI afterwards |")
print("|---|---|---|---|---|---|---|---|---|---|---|")
for r in rows:
    print(f"| {r['mode']} | {r['seed']} | {r['n']} | {r['committed']} | {r['yang_reject']} | "
          f"{r['kernel_reject']} | {r.get('stale_reject', 0)} | {r['diverged_after']} | {r['max_orphans']} | "
          f"{r['max_missing']} | {r.get('delete_all_via_nbi', '?')} |")
for mode in ("rollback", "no-rollback"):
    rs = [r for r in rows if r["mode"] == mode]
    if rs:
        print(f"\n**{mode}** total: {sum(r['n'] for r in rs)} tx, "
              f"{sum(r['kernel_reject'] for r in rs)} kernel rejects, "
              f"{sum(r['diverged_after'] for r in rs)} divergent states")

lat = json.loads((d / "latency.json").read_text())
print("\n### Latency (median / p90, ms)\n")
print("| operation | n | median | p90 |")
print("|---|---|---|---|")
for k, v in lat.items():
    if "median_ms" in v:
        print(f"| {k} | {v['n']} | {v['median_ms']} | {v['p90_ms']} |")
print("\n### One NETCONF edit-config creating k tunnels\n")
print("| k | create (s) | per tunnel (ms) | delete all (s) |")
print("|---|---|---|---|")
for k, v in lat["netconf_batch_create"].items():
    print(f"| {k} | {v['create_s']} | {v['per_tunnel_ms']} | {v['delete_all_s']} |")

dc = d / "del_cost.jsonl"
if dc.exists():
    rs = [json.loads(l) for l in dc.read_text().splitlines() if l.strip()]
    print("\n### Kernel cost: creating vs deleting 200 VXLAN links (ms, per run)\n")
    print("| run | create, exec each | delete, exec each | create, ip -batch | delete, ip -batch | delete, one request (`ip link del group`) |")
    print("|---|---|---|---|---|---|")
    for i, r in enumerate(rs, 1):
        print(f"| {i} | {r['create_exec_each_ms']} | {r['delete_exec_each_ms']} | {r['create_batch_ms']} | "
              f"{r['delete_batch_ms']} | {r['delete_one_request_group_ms']} |")
