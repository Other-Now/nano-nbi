#!/usr/bin/env bash
# Why is deleting tunnels much slower than creating them?
# For N VXLAN links, time:
#   1. create, one `ip` exec per link        2. delete, one `ip` exec per link
#   3. create via one `ip -batch`            4. delete via one `ip -batch`
#      (one process, still one netlink request per link)
#   5. delete via ONE netlink request: `ip link del group G`
# Run inside scripts/ns.sh. Prints one JSON line.
set -euo pipefail
N=${1:-200}
now() { date +%s%N; }
ms() { echo $(( ($(now) - $1) / 1000000 )); }
add_lines() { for i in $(seq 1 "$N"); do echo "link add name d$i type vxlan id $i remote 10.5.$((i / 250)).$((i % 250 + 1)) dstport 4789"; done; }
del_lines() { for i in $(seq 1 "$N"); do echo "link del dev d$i"; done; }
grp_lines() { for i in $(seq 1 "$N"); do echo "link set dev d$i group 7"; done; }

s=$(now); add_lines | while read -r l; do ip $l; done; c_each=$(ms "$s")
s=$(now); del_lines | while read -r l; do ip $l; done; d_each=$(ms "$s")
s=$(now); add_lines | ip -batch -; c_batch=$(ms "$s")
s=$(now); del_lines | ip -batch -; d_batch=$(ms "$s")
add_lines | ip -batch -; grp_lines | ip -batch -
s=$(now); ip link del group 7; d_group=$(ms "$s")
left=$(ip -o link show | grep -c ' d[0-9]' || true)
echo "{\"n\": $N, \"create_exec_each_ms\": $c_each, \"delete_exec_each_ms\": $d_each, \"create_batch_ms\": $c_batch, \"delete_batch_ms\": $d_batch, \"delete_one_request_group_ms\": $d_group, \"left\": $left}"
