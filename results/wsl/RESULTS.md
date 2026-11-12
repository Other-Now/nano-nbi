## nano-nbi results (wsl)

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
| raw_ip_create_delete_3_execs | 200 | 44.82 | 59.17 |
| netconf_yang_reject | 200 | 118.06 | 119.79 |
| netconf_get_config | 200 | 101.39 | 102.69 |
| netconf_get_with_state | 200 | 118.23 | 120.56 |
| tick1ms_netconf_yang_reject | 200 | 2.52 | 3.12 |
| tick1ms_netconf_get_config | 200 | 2.22 | 2.89 |
| tick1ms_netconf_get_with_state | 200 | 3.32 | 4.12 |
| tick1ms_netconf_create_1 | 200 | 5.93 | 7.41 |
| netconf_create_1 | 200 | 108.8 | 119.42 |
| netconf_delete_1 | 200 | 135.7 | 145.8 |
| netconf_kernel_reject_undo2 | 50 | 171.47 | 188.16 |
| gnmi_create_1 | 100 | 7.26 | 9.78 |
| gnmi_get_1 | 100 | 3.54 | 4.6 |

### One NETCONF edit-config creating k tunnels

| k | create (s) | per tunnel (ms) | delete all (s) |
|---|---|---|---|
| 1 | 0.123 | 123.37 | 0.155 |
| 10 | 0.152 | 15.19 | 0.346 |
| 100 | 0.417 | 4.17 | 2.654 |
| 500 | 1.674 | 3.35 | 12.607 |

### Kernel cost: creating vs deleting 200 VXLAN links (ms, per run)

| run | create, exec each | delete, exec each | create, ip -batch | delete, ip -batch | delete, one request (`ip link del group`) |
|---|---|---|---|---|---|
| 1 | 494 | 4707 | 29 | 3497 | 49 |
| 2 | 299 | 5106 | 50 | 5572 | 70 |
| 3 | 459 | 4797 | 38 | 3427 | 63 |
