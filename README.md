# nano-nbi

A small, data-driven **north-bound interface** for overlay tunnels:

```
 ncclient ──NETCONF/SSH──► netopeer2 ─┐
                                      ├─► sysrepo (running / candidate / operational)
 pygnmi  ───gNMI/gRPC───► gateway  ───┘        │ change / oper-get / notif
                                               ▼
                                 tunneld (C) ──ip──► kernel VXLAN / GRE links
```

* **YANG model** [`nano-tunnels`](yang/nano-tunnels@2026-11-11.yang): a list of
  VXLAN/GRE tunnels (name, type, vni, endpoints, mtu, enabled), with `when`,
  `must` and `unique` constraints, a `config false` state container
  (oper-status, ifindex, counters) and a `tunnel-oper-status-change`
  notification.
* **netopeer2 / libnetconf2 / sysrepo / libyang** (CESNET, built from source at
  pinned commits) serve the model over NETCONF: `edit-config`, `get`,
  candidate + `commit`, `confirmed-commit`, `validate`, notifications.
* **tunneld** ([src/tunneld.c](src/tunneld.c), ~350 lines of C) is the
  sysrepo subscriber that makes the config real:
  * `SR_EV_CHANGE`: diff the new config against what it last applied, run
    the `ip link` operations, and log an **undo step before every kernel
    operation**. If the kernel refuses anything, it replays the undo log and
    fails the event. sysrepo then rejects the edit, the datastore is
    unchanged, and the client's `<rpc-error>` carries the kernel's own text
    (`RTNETLINK answers: File exists`).
  * `SR_EV_DONE` adopts the new table; `SR_EV_ABORT` replays the undo log.
  * Operational state comes from an oper-get callback that reads the kernel.
    A watcher thread sends a notification when a link's observed state
    changes, including changes made outside the NBI.
  * On start it flushes every link it owns (tagged with ifalias `nano-nbi`)
    and rebuilds from the running datastore. The datastore is the source of
    truth.
* **gNMI gateway** ([gnmi/gnmi_gateway.py](gnmi/gnmi_gateway.py)) is a second
  north-bound interface over the **same** datastore. It supports
  Capabilities, Get and Set with JSON_IETF. A whole SetRequest becomes one
  sysrepo edit, so it goes through the same tunneld transaction and is
  all-or-nothing.

Everything runs unprivileged inside `unshare --user --map-root-user --net`
([scripts/ns.sh](scripts/ns.sh)): no sudo, and no tunnel ever touches the
host.

## Run it

```bash
scripts/build-deps.sh          # libyang, sysrepo, libnetconf2, netopeer2 -> ~/nn/root
source scripts/env.sh && cmake -S . -B ~/nn-build && cmake --build ~/nn-build
python3 -m venv ~/nn-venv && ~/nn-venv/bin/pip install -r requirements.txt
scripts/test.sh                # fresh sysrepo repo + stack + pytest, in a throwaway netns
scripts/bench.sh               # randomized consistency + latency -> results/wsl
```

## What the tests check

The invariant is checked after every step: **the links tunneld owns in the
kernel are exactly what the running datastore says.** The kernel side is read
with `ip -d -j link`, not through tunneld.

| area | tests |
|---|---|
| YANG rejects bad data before tunneld is called | VNI on GRE, VXLAN without VNI, VNI out of range, duplicate VNI (`unique`), local == remote (`must`), bad name / mtu / address. The test also checks tunneld's log shows no transaction. |
| lifecycle | create; mtu change is in place (same ifindex); endpoint change recreates (new ifindex); disable → oper `down`; delete |
| kernel refuses part of a transaction | 3 tunnels in one edit, the 3rd has the same GRE endpoints as the 2nd → `File exists` reaches the client, nothing is left behind |
| control: rollback disabled | same edit with `NN_NO_ROLLBACK=1`: datastore rejects it, kernel keeps 2 orphan links |
| candidate / confirmed-commit | candidate edits don't touch the kernel; an unconfirmed `confirmed-commit` times out and the server's revert removes the link; a confirmed one stays |
| state + notifications | `<get>` returns config **and** state; `ip link set down` by hand → `tunnel-oper-status-change` notification |
| kill -9 tunneld mid-transaction | commit fails (sysrepo callback timeout), running unchanged, orphans visible until restart, 0 after |
| restart reconciles drift | link deleted by hand, MTU changed by hand, a stray owned link → restart fixes all three |
| iproute2 keyword names | tunnels named `a`, `up`, `mtu`, `type`, `dev`, `help`, `name` |
| gNMI | Capabilities; Set via gNMI visible via NETCONF and vice versa; leaf update; delete; atomic reject of a 3-update SetRequest; YANG reject |

| slow apply outlives the server's sysrepo timeout | client gets `timed out`, datastore unchanged, kernel briefly ahead (9 links), then sysrepo's `SR_EV_ABORT` → undo → 0 |

Full suite: **33 passed**, both with a release tunneld and with every tunneld
instance built with ASan + UBSan (0 sanitizer reports, LeakSanitizer on 8
clean exits).

## Results (WSL2, Ubuntu 24.04; full tables in [results/wsl/RESULTS.md](results/wsl/RESULTS.md))

**Kernel vs datastore after every one of 900 randomized transactions**
(3 seeds × 300; 1–4 creates / deletes / changes each; GRE endpoint pairs
and VNIs drawn from small pools, so conflicts happen naturally rather than
being injected):

| | rollback (tunneld) | control: rollback disabled |
|---|---|---|
| committed / YANG-rejected / kernel-rejected | 393 / 109 / 398 | 69 / 161 / 139 |
| rejected because tunneld's *own* bookkeeping was stale | 0 | **531** |
| transactions after which kernel ≠ running | **0 / 900** | **893 / 900** (first after tx 1–4) |
| worst state | — | 13 orphan links, 23 missing |
| "delete all tunnels" over NETCONF at the end | ok | **rejected** (`Cannot find device "t0"`) |

The control does not just leak a few links. After the first failure its
idea of what is applied is wrong, so every later diff starts from a wrong
baseline: deletes hit links that are not there, creates collide with links
that are. In the end the operator cannot even clear the config through the
NBI; only a restart, which rebuilds from the datastore, recovers it.

**Latency, one NETCONF change** (median of 200):

| | ncclient default | ncclient `TICK` = 1 ms |
|---|---|---|
| edit-config rejected by YANG | 118 ms | **2.5 ms** |
| get-config | 101 ms | **2.2 ms** |
| get (config + tunneld's state) | 118 ms | **3.3 ms** |
| edit-config creating 1 tunnel (incl. 2 `ip` execs) | 109 ms | **5.9 ms** |
| gNMI Set creating 1 tunnel (gateway forks `sysrepocfg`) | 7.3 ms | |

One edit-config creating k tunnels: 1 → 0.12 s, 100 → 0.42 s,
500 → 1.67 s (3.3 ms/tunnel). **Deleting** them: 500 → 12.6 s.

## Findings from the measurements

5. **About 95% of NETCONF latency was the client's poll interval.**
   Every NETCONF operation took ≥ 100 ms, even a YANG reject that never
   reaches tunneld, while gNMI, which forks a process per call, took 7 ms.
   ncclient's session thread sits in `select(timeout=TICK)` with
   `TICK = 0.1`, and a request queued by the caller is written only when
   that select returns. With `TICK` at 1 ms the same RPCs take 2–6 ms. The
   server stack (netopeer2 → sysrepo → tunneld → kernel) was never the
   bottleneck.
6. **Deleting a link costs ~10× more than creating one, inside the
   kernel.** For 200 VXLAN links: creating them via one `ip -batch` takes
   29–50 ms; deleting them via one `ip -batch` takes 3.4–5.6 s. The batch
   removes fork cost, so this is per netlink request. Deleting the same 200
   with **one** request (`ip link del group 7`) takes 49–70 ms. That is
   consistent with netdev unregistration paying a fixed synchronization cost
   per request, which a single many-device request shares. (I have not
   traced the kernel to confirm the mechanism.) tunneld still deletes one
   link per request; grouping the deletes of a transaction is the obvious
   next step.
7. **A slow apply makes the kernel briefly run ahead.** sysrepo's default
   callback timeout is 5 s; a 500-tunnel apply exceeded it. The client got
   `processing timed out` and the datastore was not changed, while tunneld
   was still creating links. sysrepo does send the late subscriber
   `SR_EV_ABORT` when it returns, and the undo log removes them. So the
   divergence is real but closes by itself
   (`test_slow_apply_timeout_is_undone`: 9 links ahead, then 0). netopeer2
   now runs with `-t 20`, below ncclient's 30 s client timeout. With 60 s
   the client gave up first while the server still held a sysrepo lock, and
   every following test failed on `Locking a rwlock failed`.

## Bugs found while building it

1. **The oper-state subscription hid the configuration.** tunneld first
   registered its oper-get callback at `/nano-tunnels:tunnels`. sysrepo
   treats a provider's path as owned by it and *replaces* stored data there
   with the callback's output. Every NETCONF `<get>` therefore returned
   tunnels that had `state` but no type, VNI or endpoints. The first tests
   didn't catch it because they only looked at `state`. The gNMI gateway's
   first Get on a config leaf (`.../mtu`) from the operational datastore
   came back empty. The fix was to subscribe at `.../tunnel/state`, so
   sysrepo calls once per tunnel with that list node as the parent.
   `test_get_returns_config_and_state` guards it.
2. **sysfs showed the host's interfaces.** `link_read_state` and the
   startup flush read `/sys/class/net`, and `<get>` reported every live
   tunnel as `absent`. Inside `unshare --net`, without a sysfs remount,
   `/sys/class/net` lists the netns of *whoever mounted sysfs*: here,
   `eth0 dummy0` from the host. `/proc/net/dev` correctly listed `lo gre0 …`.
   The startup flush would have silently found nothing to clean up. The fix
   uses `ioctl(SIOCGIFFLAGS)`, `if_nametoindex()`, `/proc/net/dev` and
   `ip -o link`, all of which resolve against the caller's netns.
3. **A valid tunnel name broke iproute2.** The gNMI test used a tunnel
   named `a`. `ip link add a type vxlan …` failed with `"type" is invalid
   lladdr`, because iproute2 accepts abbreviated keywords and reads `a` as
   `address`. YANG allows `a`, `up`, `mtu`, `dev`… The fix is to always pass
   `name`/`dev` explicitly.
4. **Ubuntu's iproute2 emits invalid JSON for VXLAN.** `ip -d -j link show`
   prints `"remote":"10.0.0.2"fan-map ,"local":…` for every VXLAN link. This
   comes from Ubuntu's `ubuntu-fan` patch on iproute2 6.1; upstream doesn't
   have it. The test helper strips the token. tunneld never parses `ip`
   output as JSON.

The control experiment also showed a second-order effect. With rollback
disabled, tunneld's "applied" table no longer matches the kernel after the
first failure, so later diffs are computed against a wrong baseline and
**divergence compounds**. It is not one orphan per failure.

## Limits

* Kernel changes go through `ip` (posix_spawn, argv, no shell), not netlink.
  This is simple and readable, but each change costs a fork+exec (see the
  latency table).
* A tunneld restart flushes and recreates its links (a traffic hit), rather
  than adopting matching ones in place.
* The gNMI gateway shells out to `sysrepocfg` per RPC. It has no
  Subscribe, no TLS and no auth. It exists to show two NBIs sharing one
  transactional datastore, not as a production gNMI server.
* NETCONF auth is one ed25519 key for user `root` (inside the user
  namespace, uid 0 is the NACM recovery user). NACM rules are not
  exercised.
* IPv4 endpoints only; VXLAN and GRE only; no underlay routing. These are
  management-plane tests and no traffic is sent.
* WSL2 latency numbers are noisy. Use them for ratios.

## Versions

libyang v6.4.13 · sysrepo v5.2.27 · libnetconf2 v4.6.4 · netopeer2 2.8.21
(commits pinned in [scripts/build-deps.sh](scripts/build-deps.sh)) ·
ncclient 0.7.1 · pygnmi 0.8.15 · Ubuntu 24.04, iproute2 6.1.
