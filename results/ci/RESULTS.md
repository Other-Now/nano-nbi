## nano-nbi results (ci)

### Datastore vs kernel after every randomized transaction

| mode | seed | tx | committed | YANG reject | kernel reject (conflict) | rejected: tunneld's own state stale | tx after which kernel != running | max orphans | max missing | delete-all via NBI afterwards |
|---|---|---|---|---|---|---|---|---|---|---|
| rollback | 1 | 300 | 134 | 47 | 119 | 0 | 0 | 0 | 0 | ok |
| no-rollback | 1 | 300 | 26 | 61 | 38 | 175 | 299 | 5 | 23 | rejected: tunnel t10: delete: Cannot find device "t10" |
| rollback | 2 | 300 | 132 | 21 | 147 | 0 | 0 | 0 | 0 | ok |
| no-rollback | 2 | 300 | 20 | 50 | 60 | 170 | 298 | 13 | 18 | rejected: tunnel t0: delete: Cannot find device "t0" |
| rollback | 3 | 300 | 127 | 41 | 132 | 0 | 0 | 0 | 0 | ok |
| no-rollback | 3 | 300 | 23 | 50 | 41 | 186 | 296 | 10 | 18 | rejected: tunnel t0: delete: Cannot find device "t0" |

**rollback** total: 900 tx, 398 kernel rejects, 0 divergent states

**no-rollback** total: 900 tx, 139 kernel rejects, 893 divergent states

### Latency (median / p90, ms)

| operation | n | median | p90 |
|---|---|---|---|
| raw_ip_create_delete_3_execs | 200 | 16.99 | 20.95 |
| netconf_yang_reject | 200 | 101.24 | 101.38 |
| netconf_get_config | 200 | 101.12 | 101.23 |
| netconf_get_with_state | 200 | 101.93 | 102.05 |
| tick1ms_netconf_yang_reject | 200 | 1.83 | 1.91 |
| tick1ms_netconf_get_config | 200 | 1.67 | 1.76 |
| tick1ms_netconf_get_with_state | 200 | 2.33 | 2.39 |
| tick1ms_netconf_create_1 | 200 | 4.53 | 4.74 |
| netconf_create_1 | 200 | 112.1 | 114.71 |
| netconf_delete_1 | 200 | 128.02 | 130.78 |
| netconf_kernel_reject_undo2 | 50 | 130.62 | 136.95 |
| gnmi_create_1 | 100 | 5.36 | 5.76 |
| gnmi_get_1 | 100 | 2.31 | 2.45 |

### One NETCONF edit-config creating k tunnels

| k | create (s) | per tunnel (ms) | delete all (s) |
|---|---|---|---|
| 1 | 0.117 | 117.04 | 0.133 |
| 10 | 0.125 | 12.52 | 0.214 |
| 100 | 0.323 | 3.23 | 1.154 |
| 500 | 1.224 | 2.45 | 5.274 |

### Kernel cost: creating vs deleting 200 VXLAN links (ms, per run)

| run | create, exec each | delete, exec each | create, ip -batch | delete, ip -batch | delete, one request (`ip link del group`) |
|---|---|---|---|---|---|
| 1 | 258 | 2070 | 31 | 1240 | 29 |
| 2 | 259 | 2249 | 31 | 1246 | 33 |
| 3 | 264 | 2138 | 30 | 1227 | 31 |
