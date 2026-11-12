# Source me. Paths for the from-source NETCONF stack (see build-deps.sh).
export NN_ROOT=${NN_ROOT:-$HOME/nn/root}
export NN_HOME=${NN_HOME:-$HOME/nn}
export NN_BUILD=${NN_BUILD:-$HOME/nn-build}
export NN_PROJ=${NN_PROJ:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}
export PATH=$NN_ROOT/bin:$NN_ROOT/sbin:$PATH
export LD_LIBRARY_PATH=$NN_ROOT/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}
export PKG_CONFIG_PATH=$NN_ROOT/lib/pkgconfig
export SYSREPO_REPOSITORY_PATH=${SYSREPO_REPOSITORY_PATH:-$NN_HOME/repo}
export SYSREPO_SHM_PREFIX=${SYSREPO_SHM_PREFIX:-nn}
export NN_PORT=${NN_PORT:-8300}
export NN_KEY=$NN_HOME/client_ed25519
