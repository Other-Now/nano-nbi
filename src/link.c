/* Applies tunnel config to the kernel by running iproute2 (`ip`) with an
 * argv vector (no shell). stderr of `ip` is captured and returned as the
 * error text, so a kernel refusal reaches the NETCONF client verbatim. */
#define _GNU_SOURCE
#include "link.h"

#include <net/if.h>
#include <sys/ioctl.h>
#include <sys/socket.h>
#include <errno.h>
#include <spawn.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/wait.h>
#include <unistd.h>

extern char **environ;
unsigned long link_ops;

/* Always pass "name"/"dev" explicitly: iproute2 accepts abbreviated
 * keywords, so `ip link add a type vxlan` reads "a" as "address" and a
 * YANG-valid tunnel named "a" (or "up", "mtu", ...) would fail. */
static int run_ip(char *err, size_t errlen, char *const argv[])
{
    int pfd[2];
    if (pipe(pfd) < 0) {
        snprintf(err, errlen, "pipe: %s", strerror(errno));
        return -1;
    }
    posix_spawn_file_actions_t fa;
    posix_spawn_file_actions_init(&fa);
    posix_spawn_file_actions_adddup2(&fa, pfd[1], 2);
    posix_spawn_file_actions_addopen(&fa, 1, "/dev/null", 0, 0);
    posix_spawn_file_actions_addclose(&fa, pfd[0]);
    pid_t pid;
    int rc = posix_spawnp(&pid, "ip", &fa, NULL, argv, environ);
    posix_spawn_file_actions_destroy(&fa);
    close(pfd[1]);
    if (rc != 0) {
        close(pfd[0]);
        snprintf(err, errlen, "spawn ip: %s", strerror(rc));
        return -1;
    }
    link_ops++;
    char buf[512];
    ssize_t n, tot = 0;
    while ((n = read(pfd[0], buf + tot, sizeof buf - 1 - tot)) > 0 && tot < (ssize_t)sizeof buf - 1)
        tot += n;
    buf[tot] = 0;
    close(pfd[0]);
    int st;
    waitpid(pid, &st, 0);
    if (WIFEXITED(st) && WEXITSTATUS(st) == 0)
        return 0;
    /* first line of ip's stderr is the useful part */
    char *nl = strchr(buf, '\n');
    if (nl) *nl = 0;
    snprintf(err, errlen, "%s", buf[0] ? buf : "ip failed");
    return -1;
}

int link_create(const struct tun_cfg *c, char *err, size_t errlen)
{
    char vni[16], port[8], mtu[8];
    snprintf(vni, sizeof vni, "%u", c->vni);
    snprintf(port, sizeof port, "%u", c->dst_port);
    snprintf(mtu, sizeof mtu, "%u", c->mtu);
    char name[NN_NAMELEN];
    snprintf(name, sizeof name, "%s", c->name);
    char local[16], remote[16];
    snprintf(local, sizeof local, "%s", c->local);
    snprintf(remote, sizeof remote, "%s", c->remote);

    int rc;
    if (c->type == TUN_VXLAN) {
        char *a[] = {"ip", "link", "add", "name", name, "type", "vxlan", "id", vni, "local", local,
                     "remote", remote, "dstport", port, NULL};
        rc = run_ip(err, errlen, a);
    } else {
        char *a[] = {"ip", "link", "add", "name", name, "type", "gre", "local", local, "remote", remote, NULL};
        rc = run_ip(err, errlen, a);
    }
    if (rc) return rc;
    char *s[] = {"ip", "link", "set", "dev", name, "mtu", mtu, "alias", NN_ALIAS,
                 c->enabled ? "up" : "down", NULL};
    if (run_ip(err, errlen, s)) {
        /* don't leave a half-configured link behind */
        char e2[64];
        link_delete(c->name, e2, sizeof e2);
        return -1;
    }
    return 0;
}

int link_delete(const char *name_in, char *err, size_t errlen)
{
    char name[NN_NAMELEN];
    snprintf(name, sizeof name, "%.15s", name_in);
    char *a[] = {"ip", "link", "del", "dev", name, NULL};
    return run_ip(err, errlen, a);
}

int link_modify(const struct tun_cfg *c, char *err, size_t errlen)
{
    char name[NN_NAMELEN], mtu[8];
    snprintf(name, sizeof name, "%s", c->name);
    snprintf(mtu, sizeof mtu, "%u", c->mtu);
    char *a[] = {"ip", "link", "set", "dev", name, "mtu", mtu, c->enabled ? "up" : "down", NULL};
    return run_ip(err, errlen, a);
}

int link_needs_recreate(const struct tun_cfg *a, const struct tun_cfg *b)
{
    return a->type != b->type || strcmp(a->local, b->local) || strcmp(a->remote, b->remote) ||
           (a->type == TUN_VXLAN && (a->vni != b->vni || a->dst_port != b->dst_port));
}

int link_same(const struct tun_cfg *a, const struct tun_cfg *b)
{
    return !link_needs_recreate(a, b) && a->mtu == b->mtu && a->enabled == b->enabled;
}

/* Everything below must see *this process's* network namespace. sysfs
 * (/sys/class/net) does not: it shows the namespace of whoever mounted it,
 * so inside `unshare --net` without a sysfs remount it lists the host's
 * interfaces. ioctl() on a socket, if_nametoindex() and /proc/net/dev are
 * all resolved against the caller's netns. */
void link_read_state(const char *name, struct tun_state *st)
{
    memset(st, 0, sizeof *st);
    struct ifreq ifr;
    memset(&ifr, 0, sizeof ifr);
    snprintf(ifr.ifr_name, sizeof ifr.ifr_name, "%s", name);
    int fd = socket(AF_INET, SOCK_DGRAM | SOCK_CLOEXEC, 0);
    int rc = fd < 0 ? -1 : ioctl(fd, SIOCGIFFLAGS, &ifr);
    if (fd >= 0) close(fd);
    if (rc < 0) {
        st->oper = OPER_ABSENT;
        return;
    }
    st->oper = (ifr.ifr_flags & IFF_UP) ? OPER_UP : OPER_DOWN;
    st->ifindex = if_nametoindex(name);

    FILE *f = fopen("/proc/net/dev", "r");
    if (!f) return;
    char line[512];
    size_t nl = strlen(name);
    while (fgets(line, sizeof line, f)) {
        char *p = line;
        while (*p == ' ') p++;
        if (strncmp(p, name, nl) || p[nl] != ':') continue;
        unsigned long long v[16] = {0};
        sscanf(p + nl + 1, "%llu %llu %llu %llu %llu %llu %llu %llu %llu %llu", &v[0], &v[1], &v[2],
               &v[3], &v[4], &v[5], &v[6], &v[7], &v[8], &v[9]);
        st->rx_bytes = v[0];
        st->rx_packets = v[1];
        st->tx_bytes = v[8];
        st->tx_packets = v[9];
        break;
    }
    fclose(f);
}

const char *oper_str(enum oper o)
{
    return o == OPER_UP ? "up" : o == OPER_DOWN ? "down" : "absent";
}

/* Owned links carry ifalias NN_ALIAS; `ip -o link show` prints it as
 * "alias nano-nbi" on the link's line. */
static int walk_owned(int del)
{
    FILE *p = popen("ip -o link show", "r");
    if (!p) return 0;
    char line[2048], names[256][NN_NAMELEN];
    int n = 0;
    while (fgets(line, sizeof line, p) && n < 256) {
        if (!strstr(line, "alias " NN_ALIAS)) continue;
        /* "12: vx1: <...>" or "13: g1@NONE: <...>" */
        char *a = strchr(line, ' ');
        if (!a) continue;
        a++;
        size_t len = strcspn(a, ":@");
        if (len == 0 || len >= NN_NAMELEN) continue;
        memcpy(names[n], a, len);
        names[n++][len] = 0;
    }
    pclose(p);
    for (int i = 0; del && i < n; i++) {
        char err[128];
        if (link_delete(names[i], err, sizeof err))
            fprintf(stderr, "tunneld: flush %s: %s\n", names[i], err);
    }
    return n;
}

int link_flush_owned(void) { return walk_owned(1); }
int link_count_owned(void) { return walk_owned(0); }
