/* Kernel side of nano-nbi: create / modify / delete overlay tunnel links.
 * Every link we own carries ifalias "nano-nbi" so we can find leftovers. */
#ifndef NN_LINK_H
#define NN_LINK_H

#include <stdint.h>
#include <stddef.h>

#define NN_ALIAS "nano-nbi"
#define NN_NAMELEN 16

enum tun_type { TUN_VXLAN = 0, TUN_GRE = 1 };

struct tun_cfg {
    char name[NN_NAMELEN];
    enum tun_type type;
    uint32_t vni;        /* vxlan only */
    uint16_t dst_port;   /* vxlan only */
    char local[16];
    char remote[16];
    uint16_t mtu;
    int enabled;
};

enum oper { OPER_UP = 0, OPER_DOWN = 1, OPER_ABSENT = 2 };

struct tun_state {
    enum oper oper;
    uint32_t ifindex;
    uint64_t rx_packets, tx_packets, rx_bytes, tx_bytes;
};

/* All return 0 on success, -1 on failure with a message in err. */
int link_create(const struct tun_cfg *c, char *err, size_t errlen);
int link_delete(const char *name, char *err, size_t errlen);
/* Change old -> new in place (mtu / admin state). Caller must have checked
 * that link_needs_recreate() is false. */
int link_modify(const struct tun_cfg *c, char *err, size_t errlen);
int link_needs_recreate(const struct tun_cfg *a, const struct tun_cfg *b);
int link_same(const struct tun_cfg *a, const struct tun_cfg *b);

void link_read_state(const char *name, struct tun_state *st);
const char *oper_str(enum oper o);

/* Delete every link whose ifalias is NN_ALIAS. Returns number deleted. */
int link_flush_owned(void);
/* Count links whose ifalias is NN_ALIAS (used by tests via tunneld -c). */
int link_count_owned(void);

/* Number of kernel operations (ip invocations) since start. */
extern unsigned long link_ops;

#endif
