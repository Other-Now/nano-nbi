#!/usr/bin/env bash
# Build libyang, sysrepo, libnetconf2 and netopeer2 from source into
# $NN_ROOT, without sudo, at the exact commits this project was tested with.
#
# Needs dev headers for libssh, libcurl, openssl and pcre2. On a box without
# sudo, put their .debs in ~/nn-deps/debs (apt-get download libssh-dev
# libcurl4-openssl-dev) and they are unpacked into $NN_DEPS; in CI they come
# from apt.
set -euo pipefail
SRC=${NN_SRC:-$HOME/nn-src}
NN_ROOT=${NN_ROOT:-$HOME/nn/root}
NN_DEPS=${NN_DEPS:-$HOME/nn-deps/root}
REPO=${NN_REPO:-$HOME/nn/repo}
J=$(nproc)

# name  url  commit (tag)
PINS=(
  "libyang     https://github.com/CESNET/libyang      f7e0cc1bc99ec9280af1010a94ba8dd8ef448f6d"  # v6.4.13
  "sysrepo     https://github.com/sysrepo/sysrepo     fe58e2b46f617bd4e13ce986cd54db6e418f3bcf"  # v5.2.27
  "libnetconf2 https://github.com/CESNET/libnetconf2  f191449dd20f792479fe95dbc10db41b4845efbf"  # v4.6.4
  "netopeer2   https://github.com/CESNET/netopeer2    92e18139e996fe9333f441e3521d514b0d5282d9"  # 2.8.21
)
mkdir -p "$SRC" "$NN_ROOT" "$REPO"
for p in "${PINS[@]}"; do
  read -r name url sha <<<"$p"
  if [ ! -d "$SRC/$name/.git" ]; then
    git init -q "$SRC/$name"
    git -C "$SRC/$name" fetch -q --depth 1 "$url" "$sha"
    git -C "$SRC/$name" checkout -q FETCH_HEAD
  fi
  have=$(git -C "$SRC/$name" rev-parse HEAD)
  [ "$have" = "$sha" ] || { echo "$name is at $have, expected $sha"; exit 1; }
done

EXTRA=()
L=$NN_DEPS/usr/lib/x86_64-linux-gnu
if compgen -G "$HOME/nn-deps/debs/*.deb" >/dev/null; then
  mkdir -p "$NN_DEPS"
  for d in "$HOME"/nn-deps/debs/*.deb; do dpkg -x "$d" "$NN_DEPS"; done
  # the dev .so symlinks point at runtime libs that live in /usr
  ln -sf /usr/lib/x86_64-linux-gnu/libssh.so.4 "$L/libssh.so"
  ln -sf /usr/lib/x86_64-linux-gnu/libcurl.so.4 "$L/libcurl.so"
  EXTRA=(-DLIBSSH_INCLUDE_DIR="$NN_DEPS/usr/include" -DLIBSSH_LIBRARY="$L/libssh.so"
         -DCURL_INCLUDE_DIR="$NN_DEPS/usr/include/x86_64-linux-gnu" -DCURL_LIBRARY="$L/libcurl.so")
fi

export PKG_CONFIG_PATH=$NN_ROOT/lib/pkgconfig:$L/pkgconfig
export LD_LIBRARY_PATH=$NN_ROOT/lib
COMMON=(-DCMAKE_INSTALL_PREFIX="$NN_ROOT" -DCMAKE_INSTALL_LIBDIR=lib
        -DCMAKE_BUILD_TYPE=RelWithDebInfo -DCMAKE_PREFIX_PATH="$NN_ROOT"
        -DENABLE_TESTS=OFF -DENABLE_VALGRIND_TESTS=OFF)

b() { local n=$1; shift
  echo "=== $n"
  cmake -S "$SRC/$n" -B "$SRC/$n/build" "${COMMON[@]}" "$@" >"$SRC/$n.cfg.log" 2>&1 \
    || { tail -30 "$SRC/$n.cfg.log"; exit 1; }
  cmake --build "$SRC/$n/build" -j"$J" >"$SRC/$n.build.log" 2>&1 || { tail -30 "$SRC/$n.build.log"; exit 1; }
  cmake --install "$SRC/$n/build" >"$SRC/$n.install.log" 2>&1 || { tail -30 "$SRC/$n.install.log"; exit 1; }
}
b libyang
b sysrepo -DREPO_PATH="$REPO" -DSR_PLUGINS_PATH="$NN_ROOT/lib/sysrepo/plugins" \
          -DSRPD_PLUGINS_PATH="$NN_ROOT/lib/sysrepo-plugind/plugins"
b libnetconf2 "${EXTRA[@]}" -DENABLE_PAM=OFF
# SYSREPO_SETUP=OFF: module install + server config is done by setup-repo.sh
b netopeer2 "${EXTRA[@]}" -DSYSREPO_SETUP=OFF -DPIDFILE_PREFIX="$HOME/nn/run" -DSERVER_DIR="$HOME/nn/srv"
echo BUILD-OK
