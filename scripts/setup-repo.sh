#!/usr/bin/env bash
# netopeer2 scripts call `su` when uid==0 and $USER is set; inside the
# user namespace that fails, so they are run with USER unset.
# (Re)create the sysrepo repository: netopeer2's modules, a host key, our
# YANG model, and a NETCONF-over-SSH endpoint on 127.0.0.1:$NN_PORT that
# accepts user "root" with a generated ed25519 key. Run inside ns.sh.
set -euo pipefail
source "$(dirname "$0")/env.sh"
NP2=${NN_SRC:-$HOME/nn-src}/netopeer2/scripts

rm -rf "$SYSREPO_REPOSITORY_PATH"; mkdir -p "$SYSREPO_REPOSITORY_PATH"
rm -f /dev/shm/${SYSREPO_SHM_PREFIX}*

NP2_MODULE_DIR=$NN_ROOT/share/yang/modules/netopeer2 NP2_MODULE_PERMS=600 \
LN2_MODULE_DIR=$NN_ROOT/share/yang/modules/libnetconf2 \
  env -u USER bash "$NP2/setup.sh" >"$NN_HOME/setup.log" 2>&1
env -u USER bash "$NP2/merge_hostkey.sh" >>"$NN_HOME/setup.log" 2>&1

sysrepoctl -i "$NN_PROJ/yang/nano-tunnels@2026-11-11.yang" -s "$NN_PROJ/yang" -v1

[ -f "$NN_KEY" ] || ssh-keygen -q -t ed25519 -N "" -f "$NN_KEY"
PUB=$(awk '{print $2}' "$NN_KEY.pub")

sysrepocfg --edit -d startup -f xml -m ietf-netconf-server -v1 <<XML
<netconf-server xmlns="urn:ietf:params:xml:ns:yang:ietf-netconf-server">
  <listen><endpoints><endpoint>
    <name>nbi-ssh</name>
    <ssh>
      <tcp-server-parameters><local-bind>
        <local-address>127.0.0.1</local-address><local-port>$NN_PORT</local-port>
      </local-bind></tcp-server-parameters>
      <ssh-server-parameters>
        <server-identity><host-key><name>hk</name><public-key>
          <central-keystore-reference>genkey</central-keystore-reference>
        </public-key></host-key></server-identity>
        <client-authentication><users><user>
          <name>root</name>
          <public-keys><inline-definition><public-key>
            <name>ci</name>
            <public-key-format xmlns:ct="urn:ietf:params:xml:ns:yang:ietf-crypto-types">ct:ssh-public-key-format</public-key-format>
            <public-key>$PUB</public-key>
          </public-key></inline-definition></public-keys>
        </user></users></client-authentication>
      </ssh-server-parameters>
    </ssh>
  </endpoint></endpoints></listen>
</netconf-server>
XML
sysrepocfg -C startup -m ietf-netconf-server -v1
echo "setup-repo: ok ($(sysrepoctl -l | grep -c '|') modules)"
