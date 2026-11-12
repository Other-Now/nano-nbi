/* tunneld: sysrepo subscriber that turns the nano-tunnels YANG config into
 * kernel VXLAN/GRE links, serves their operational state, and emits a
 * notification when a link's observed state changes.
 *
 * Transaction model (sysrepo two-phase):
 *   SR_EV_CHANGE  apply the diff to the kernel, logging an undo step before
 *                 every kernel operation. If any operation fails, replay the
 *                 undo log, set the kernel's error text on the session and
 *                 fail the event -> sysrepo rejects the edit, the datastore
 *                 is unchanged, and the NETCONF client gets <rpc-error>.
 *   SR_EV_DONE    the datastore committed: adopt the new table.
 *   SR_EV_ABORT   a later subscriber failed: replay the undo log.
 *
 * Startup: flush every link carrying our ifalias, then the SR_SUBSCR_ENABLED
 * event recreates whatever the running datastore holds. The datastore is the
 * source of truth; the kernel is rebuilt from it after any crash.
 *
 * Env knobs used by the tests:
 *   NN_NO_ROLLBACK=1      skip the undo replay (the naive control)
 *   NN_STEP_DELAY_US=n    sleep after each kernel op (lets a kill -9 land
 *                         mid-transaction)
 */
#define _GNU_SOURCE
#include <pthread.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <unistd.h>

#include <libyang/libyang.h>
#include <sysrepo.h>

#include "link.h"

#define MOD "nano-tunnels"
#define MAXT 4096

struct table {
    int n;
    struct tun_cfg t[MAXT];
};

struct undo {
    struct tun_cfg before;
    int existed;
};

static pthread_mutex_t lock = PTHREAD_MUTEX_INITIALIZER;
static struct table applied;  /* what the kernel holds for committed config */
static struct table pending;  /* applied by CHANGE, waiting for DONE/ABORT */
static struct undo undo_log[3 * MAXT];
static int undo_n;

static int no_rollback;
static useconds_t step_delay;
static unsigned long tx_ok, tx_fail, tx_abort, undo_ops;

static sr_conn_ctx_t *conn;
static sr_session_ctx_t *sess;
static volatile sig_atomic_t stop;

static struct tun_cfg *find(struct table *tb, const char *name)
{
    for (int i = 0; i < tb->n; i++)
        if (!strcmp(tb->t[i].name, name)) return &tb->t[i];
    return NULL;
}

/* ---- YANG data -> table ------------------------------------------------ */

static int load_table(sr_session_ctx_t *s, struct table *tb)
{
    sr_data_t *data = NULL;
    tb->n = 0;
    int rc = sr_get_data(s, "/" MOD ":tunnels/tunnel", 0, 0, 0, &data);
    if (rc != SR_ERR_OK) return rc;
    if (!data) return SR_ERR_OK;
    for (struct lyd_node *top = data->tree; top; top = top->next) {
        if (strcmp(LYD_NAME(top), "tunnels")) continue;
        struct lyd_node *tn;
        LY_LIST_FOR(lyd_child(top), tn) {
            if (strcmp(LYD_NAME(tn), "tunnel") || tb->n == MAXT) continue;
            struct tun_cfg *c = &tb->t[tb->n++];
            memset(c, 0, sizeof *c);
            c->dst_port = 4789;
            c->mtu = 1450;
            c->enabled = 1;
            struct lyd_node *l;
            LY_LIST_FOR(lyd_child(tn), l) {
                const char *k = LYD_NAME(l), *v = lyd_get_value(l);
                if (!v) continue;
                if (!strcmp(k, "name")) snprintf(c->name, sizeof c->name, "%s", v);
                else if (!strcmp(k, "type")) c->type = strcmp(v, "gre") ? TUN_VXLAN : TUN_GRE;
                else if (!strcmp(k, "vni")) c->vni = (uint32_t)strtoul(v, NULL, 10);
                else if (!strcmp(k, "dst-port")) c->dst_port = (uint16_t)strtoul(v, NULL, 10);
                else if (!strcmp(k, "local")) snprintf(c->local, sizeof c->local, "%s", v);
                else if (!strcmp(k, "remote")) snprintf(c->remote, sizeof c->remote, "%s", v);
                else if (!strcmp(k, "mtu")) c->mtu = (uint16_t)strtoul(v, NULL, 10);
                else if (!strcmp(k, "enabled")) c->enabled = !strcmp(v, "true");
            }
        }
    }
    sr_release_data(data);
    return SR_ERR_OK;
}

/* ---- applying a diff --------------------------------------------------- */

static void log_undo(const struct tun_cfg *before, const char *name)
{
    struct undo *u = &undo_log[undo_n++];
    if (before) {
        u->before = *before;
        u->existed = 1;
    } else {
        memset(u, 0, sizeof *u);
        snprintf(u->before.name, sizeof u->before.name, "%s", name);
    }
}

static void step_done(void)
{
    if (step_delay) usleep(step_delay);
}

/* Restore each touched name to its pre-transaction state, newest first. */
static void replay_undo(void)
{
    char err[256];
    for (int i = undo_n - 1; i >= 0; i--) {
        struct undo *u = &undo_log[i];
        link_delete(u->before.name, err, sizeof err); /* may not exist: fine */
        if (u->existed && link_create(&u->before, err, sizeof err))
            fprintf(stderr, "tunneld: UNDO FAILED for %s: %s\n", u->before.name, err);
        undo_ops++;
    }
    undo_n = 0;
}

/* Move the kernel from `have` to `want`. Deletes first (frees endpoint pairs
 * a create may need), then in-place changes / recreates, then creates. */
static int apply_diff(struct table *have, const struct table *want, char *err, size_t errlen)
{
    char e[256];
    undo_n = 0;
    for (int i = 0; i < have->n; i++) {
        if (find((struct table *)want, have->t[i].name)) continue;
        log_undo(&have->t[i], NULL);
        if (link_delete(have->t[i].name, e, sizeof e)) goto fail_del;
        step_done();
        continue;
    fail_del:
        snprintf(err, errlen, "tunnel %s: delete: %s", have->t[i].name, e);
        return -1;
    }
    for (int i = 0; i < want->n; i++) {
        const struct tun_cfg *w = &want->t[i];
        struct tun_cfg *h = find(have, w->name);
        if (!h || link_same(h, w)) continue;
        log_undo(h, NULL);
        int rc;
        if (link_needs_recreate(h, w))
            rc = link_delete(w->name, e, sizeof e) || link_create(w, e, sizeof e);
        else
            rc = link_modify(w, e, sizeof e);
        if (rc) {
            snprintf(err, errlen, "tunnel %s: modify: %s", w->name, e);
            return -1;
        }
        step_done();
    }
    for (int i = 0; i < want->n; i++) {
        const struct tun_cfg *w = &want->t[i];
        if (find(have, w->name)) continue;
        log_undo(NULL, w->name);
        if (link_create(w, e, sizeof e)) {
            snprintf(err, errlen, "tunnel %s: create: %s", w->name, e);
            return -1;
        }
        step_done();
    }
    return 0;
}

static int change_cb(sr_session_ctx_t *s, uint32_t sub_id, const char *module, const char *xpath,
                     sr_event_t ev, uint32_t req_id, void *priv)
{
    (void)sub_id; (void)module; (void)xpath; (void)req_id; (void)priv;
    int rc = SR_ERR_OK;
    pthread_mutex_lock(&lock);
    if (getenv("NN_DEBUG"))
        fprintf(stderr, "tunneld: event %d req=%u\n", (int)ev, req_id);
    switch (ev) {
    case SR_EV_CHANGE:
    case SR_EV_ENABLED: {
        char err[512] = "";
        if ((rc = load_table(s, &pending)) != SR_ERR_OK) break;
        if (apply_diff(&applied, &pending, err, sizeof err)) {
            int steps = undo_n;
            if (!no_rollback) replay_undo();
            else undo_n = 0;
            tx_fail++;
            fprintf(stderr, "tunneld: tx REJECTED (%s), %s %d step(s)\n", err,
                    no_rollback ? "NOT rolling back" : "rolled back", steps);
            sr_session_set_error_message(s, "%s", err);
            rc = SR_ERR_OPERATION_FAILED;
        }
        break;
    }
    case SR_EV_DONE:
        applied = pending;
        undo_n = 0;
        tx_ok++;
        fprintf(stderr, "tunneld: tx committed, %d tunnel(s), %lu kernel ops so far\n", applied.n, link_ops);
        break;
    case SR_EV_ABORT:
        replay_undo();
        tx_abort++;
        /* Also delivered when *we* were too slow: if CHANGE outlives the
         * originator's timeout, the edit fails and sysrepo sends us ABORT
         * once we return. Until then the kernel runs ahead of the
         * datastore (measured: test_slow_apply_timeout_is_undone). */
        fprintf(stderr, "tunneld: tx aborted (other subscriber failed or originator timed out), undone\n");
        break;
    default:
        break;
    }
    pthread_mutex_unlock(&lock);
    return rc;
}

/* ---- operational state ------------------------------------------------- */

/* Subscribed at .../tunnel/state, NOT at /tunnels: sysrepo treats the
 * subscribed path as owned by the provider and replaces whatever is stored
 * there with the callback's output. Subscribing at /tunnels made every
 * <get> return tunnels with state but without their configuration.
 * At .../tunnel/state sysrepo calls us once per existing tunnel instance,
 * with *parent = that tunnel list node. */
static int oper_cb(sr_session_ctx_t *s, uint32_t sub_id, const char *module, const char *path,
                   const char *req_xpath, uint32_t req_id, struct lyd_node **parent, void *priv)
{
    (void)s; (void)sub_id; (void)module; (void)path; (void)req_xpath; (void)req_id; (void)priv;
    if (!*parent) return SR_ERR_OK;
    const char *name = NULL;
    struct lyd_node *k;
    LY_LIST_FOR(lyd_child(*parent), k)
        if (!strcmp(LYD_NAME(k), "name")) name = lyd_get_value(k);
    if (!name) return SR_ERR_OK;

    struct tun_state st;
    link_read_state(name, &st);
    char v[32];
#define PUT(leaf, fmt, val)                                                       \
    do {                                                                          \
        snprintf(v, sizeof v, fmt, val);                                          \
        lyd_new_path(*parent, NULL, "state/" leaf, v, LYD_NEW_PATH_UPDATE, NULL); \
    } while (0)
    PUT("oper-status", "%s", oper_str(st.oper));
    if (st.oper != OPER_ABSENT) {
        PUT("if-index", "%u", st.ifindex);
        PUT("rx-packets", "%llu", (unsigned long long)st.rx_packets);
        PUT("tx-packets", "%llu", (unsigned long long)st.tx_packets);
        PUT("rx-bytes", "%llu", (unsigned long long)st.rx_bytes);
        PUT("tx-bytes", "%llu", (unsigned long long)st.tx_bytes);
    }
#undef PUT
    return SR_ERR_OK;
}

/* ---- notifications: poll observed state, report changes ----------------- */

static void *watch_thread(void *arg)
{
    (void)arg;
    static char names[MAXT][NN_NAMELEN];
    static enum oper last[MAXT];
    int known = 0;
    while (!stop) {
        usleep(200 * 1000);
        struct { char name[NN_NAMELEN]; enum oper o; } ev[64];
        int nev = 0;
        pthread_mutex_lock(&lock);
        /* rebuild the watch list from committed config; new entries start
         * from their current state so creation itself is not an event */
        static char nn[MAXT][NN_NAMELEN];
        static enum oper nl[MAXT];
        for (int i = 0; i < applied.n; i++) {
            struct tun_state st;
            link_read_state(applied.t[i].name, &st);
            snprintf(nn[i], NN_NAMELEN, "%s", applied.t[i].name);
            nl[i] = st.oper;
            for (int j = 0; j < known; j++)
                if (!strcmp(names[j], nn[i]) && last[j] != st.oper && nev < 64) {
                    snprintf(ev[nev].name, NN_NAMELEN, "%s", nn[i]);
                    ev[nev++].o = st.oper;
                }
        }
        known = applied.n;
        memcpy(names, nn, sizeof(names[0]) * known);
        memcpy(last, nl, sizeof(last[0]) * known);
        pthread_mutex_unlock(&lock);

        for (int i = 0; i < nev; i++) {
            const struct ly_ctx *ctx = sr_acquire_context(conn);
            struct lyd_node *notif = NULL;
            lyd_new_path(NULL, ctx, "/" MOD ":tunnel-oper-status-change/name", ev[i].name, 0, &notif);
            lyd_new_path(notif, ctx, "/" MOD ":tunnel-oper-status-change/oper-status", oper_str(ev[i].o), 0, NULL);
            int rc = sr_notif_send_tree(sess, notif, 0, 0);
            fprintf(stderr, "tunneld: notif %s -> %s (%s)\n", ev[i].name, oper_str(ev[i].o), sr_strerror(rc));
            lyd_free_all(notif);
            sr_release_context(conn);
        }
    }
    return NULL;
}

static void on_sig(int sig) { (void)sig; stop = 1; }

int main(void)
{
    no_rollback = getenv("NN_NO_ROLLBACK") && atoi(getenv("NN_NO_ROLLBACK"));
    step_delay = getenv("NN_STEP_DELAY_US") ? (useconds_t)atoi(getenv("NN_STEP_DELAY_US")) : 0;
    setvbuf(stderr, NULL, _IOLBF, 0);
    sr_log_stderr(SR_LL_WRN);

    int flushed = link_flush_owned();
    fprintf(stderr, "tunneld: start, flushed %d leftover link(s)%s\n", flushed,
            no_rollback ? ", ROLLBACK DISABLED" : "");

    sr_subscription_ctx_t *sub = NULL;
    int rc;
    if ((rc = sr_connect(0, &conn)) || (rc = sr_session_start(conn, SR_DS_RUNNING, &sess))) {
        fprintf(stderr, "tunneld: sysrepo: %s\n", sr_strerror(rc));
        return 1;
    }
    rc = sr_module_change_subscribe(sess, MOD, NULL, change_cb, NULL, 0, SR_SUBSCR_ENABLED, &sub);
    if (!rc) rc = sr_oper_get_subscribe(sess, MOD, "/" MOD ":tunnels/tunnel/state", oper_cb, NULL, 0, &sub);
    if (rc) {
        fprintf(stderr, "tunneld: subscribe: %s\n", sr_strerror(rc));
        sr_disconnect(conn);
        return 1;
    }

    signal(SIGINT, on_sig);
    signal(SIGTERM, on_sig);
    pthread_t th;
    pthread_create(&th, NULL, watch_thread, NULL);
    fprintf(stderr, "tunneld: ready, %d tunnel(s) applied\n", applied.n);
    while (!stop) pause();

    pthread_join(th, NULL);
    sr_unsubscribe(sub);
    sr_disconnect(conn);
    fprintf(stderr, "tunneld: exit ok=%lu fail=%lu abort=%lu undo_ops=%lu kernel_ops=%lu\n", tx_ok,
            tx_fail, tx_abort, undo_ops, link_ops);
    return 0;
}
