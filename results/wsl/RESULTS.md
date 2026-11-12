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
| raw_ip_create_delete_3_execs | 200 | 44.0 | 52.45 |
| netconf_yang_reject | 200 | 118.11 | 118.87 |
| netconf_get_config | 200 | 118.07 | 119.44 |
| netconf_get_with_state | 200 | 118.27 | 119.46 |
| tick1ms_netconf_yang_reject | 200 | 2.36 | 2.75 |
| tick1ms_netconf_get_config | 200 | 2.16 | 2.41 |
| tick1ms_netconf_get_with_state | 200 | 3.08 | 3.59 |
| tick1ms_netconf_create_1 | 200 | 5.91 | 7.01 |
| netconf_create_1 | 200 | 107.86 | 120.4 |
| netconf_delete_1 | 200 | 135.3 | 145.0 |
| netconf_kernel_reject_undo2 | 50 | 164.03 | 176.37 |
| gnmi_create_1 | 100 | 7.45 | 11.09 |
| gnmi_get_1 | 100 | 3.74 | 4.15 |

### One NETCONF edit-config creating k tunnels

| k | create (s) | per tunnel (ms) | delete all (s) |
|---|---|---|---|
| 1 | 0.13 | 129.68 | 0.134 |
| 10 | 0.136 | 13.6 | 0.329 |
| 100 | 0.423 | 4.23 | 2.482 |
| 500 | 1.841 | 3.68 | 11.557 |

### Kernel cost: creating vs deleting 200 VXLAN links (ms, per run)

| run | create, exec each | delete, exec each | create, ip -batch | delete, ip -batch | delete, one request (`ip link del group`) |
|---|---|---|---|---|---|
| 1 | 366 | 5931 | 32 | 3541 | 52 |
| 2 | 413 | 4606 | 27 | 3599 | 45 |
| 3 | 368 | 4782 | 37 | 3617 | 36 |
