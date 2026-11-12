"""Minimal gNMI (gRPC) front end over the same sysrepo datastore that
netopeer2 serves over NETCONF. Two north-bound interfaces, one source of
truth: a gNMI Set goes through the exact same sysrepo transaction and
tunneld callback (validation, kernel apply, rollback) as a NETCONF commit.

Supported: Capabilities, Get (JSON_IETF), Set (delete/replace/update; the
whole SetRequest is one sysrepo edit, so it is all-or-nothing as gNMI
requires). Not supported: Subscribe, other encodings, TLS, auth.

Datastore access is via the `sysrepocfg` CLI (one process per RPC). That is
the simplest thing that works; it costs ~tens of ms per call and would be
replaced by the sysrepo C API in anything real.
"""
import argparse
import json
import os
import subprocess
import tempfile
import time
from concurrent import futures

import grpc
from pygnmi.spec.v080 import gnmi_pb2 as pb
from pygnmi.spec.v080 import gnmi_pb2_grpc as pbg

MODULE = "nano-tunnels"
OP = "ietf-netconf:operation"


def elems(prefix, path):
    """gNMI Path(s) -> [(name, {key: val})], module-qualifying the first."""
    out = []
    for p in (prefix, path):
        if p is None:
            continue
        for e in p.elem:
            out.append((e.name, dict(e.key)))
    if out and ":" not in out[0][0]:
        out[0] = (f"{MODULE}:{out[0][0]}", out[0][1])
    return out


def to_xpath(es):
    s = ""
    for name, keys in es:
        s += "/" + name + "".join(f"[{k}='{v}']" for k, v in sorted(keys.items()))
    return s or f"/{MODULE}:*"


def walk(tree, es):
    """Return the JSON subtree that sits at path es, or None."""
    cur = tree
    for name, keys in es:
        if not isinstance(cur, dict):
            return None
        nxt = cur.get(name, cur.get(name.split(":")[-1]))
        if nxt is None:
            return None
        if keys:
            nxt = next((x for x in nxt if all(str(x.get(k)) == v for k, v in keys.items())), None)
            if nxt is None:
                return None
        cur = nxt
    return cur


def build(doc, es, value, op):
    """Merge one Set operation into the edit document `doc`."""
    cur = doc
    for i, (name, keys) in enumerate(es):
        last = i == len(es) - 1
        if keys:
            lst = cur.setdefault(name, [])
            ent = next((x for x in lst if all(str(x.get(k)) == v for k, v in keys.items())), None)
            if ent is None:
                ent = dict(keys)
                lst.append(ent)
            cur = ent
            if last:
                if op:
                    cur["@"] = {OP: op}
                if isinstance(value, dict):
                    cur.update(value)
        elif last:
            if isinstance(value, dict) or value is None:
                node = cur.setdefault(name, {})
                if value:
                    node.update(value)
                if op:
                    node["@"] = {OP: op}
            else:
                cur[name] = value
                if op:
                    cur["@" + name] = {OP: op}
        else:
            cur = cur.setdefault(name, {})
    return doc


def sysrepocfg(*args, stdin=None):
    r = subprocess.run(["sysrepocfg", *args], capture_output=True, text=True, input=stdin)
    return r.returncode, r.stdout, r.stderr.strip()


class Gnmi(pbg.gNMIServicer):
    def Capabilities(self, request, context):
        return pb.CapabilityResponse(
            supported_models=[pb.ModelData(name=MODULE, organization="nano-nbi", version="2026-11-11")],
            supported_encodings=[pb.Encoding.JSON_IETF],
            gNMI_version="0.8.0")

    def Get(self, request, context):
        if request.encoding not in (pb.Encoding.JSON_IETF, pb.Encoding.JSON):
            context.abort(grpc.StatusCode.UNIMPLEMENTED, "only JSON_IETF")
        ds = "running" if request.type == pb.GetRequest.DataType.CONFIG else "operational"
        notifs = []
        for path in request.path:
            es = elems(request.prefix if request.HasField("prefix") else None, path)
            rc, out, err = sysrepocfg("-X", "-d", ds, "-f", "json", "-x", to_xpath(es))
            if rc:
                context.abort(grpc.StatusCode.INTERNAL, err)
            val = walk(json.loads(out) if out.strip() else {}, es)
            if val is None:
                context.abort(grpc.StatusCode.NOT_FOUND, f"no data at {to_xpath(es)}")
            notifs.append(pb.Notification(
                timestamp=time.time_ns(),
                update=[pb.Update(path=path, val=pb.TypedValue(json_ietf_val=json.dumps(val).encode()))]))
        return pb.GetResponse(notification=notifs)

    def Set(self, request, context):
        pre = request.prefix if request.HasField("prefix") else None
        doc, results = {}, []
        for p in request.delete:
            build(doc, elems(pre, p), None, "remove")
            results.append(pb.UpdateResult(path=p, op=pb.UpdateResult.DELETE))
        for kind, ups, op in (("replace", request.replace, pb.UpdateResult.REPLACE),
                              ("update", request.update, pb.UpdateResult.UPDATE)):
            for u in ups:
                raw = u.val.json_ietf_val or u.val.json_val
                val = json.loads(raw) if raw else _scalar(u.val)
                build(doc, elems(pre, u.path), val, "replace" if kind == "replace" else None)
                results.append(pb.UpdateResult(path=u.path, op=op))
        # one edit == one sysrepo transaction == all-or-nothing
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
            json.dump(doc, f)
        try:
            rc, _, err = sysrepocfg(f"--edit={f.name}", "-d", "running", "-f", "json", "-m", MODULE)
        finally:
            os.unlink(f.name)
        if rc:
            context.abort(grpc.StatusCode.ABORTED, err or "edit rejected")
        return pb.SetResponse(timestamp=time.time_ns(), response=results)


def _scalar(tv):
    for f in ("string_val", "int_val", "uint_val", "bool_val"):
        if tv.HasField(f):
            return getattr(tv, f)
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--listen", default="127.0.0.1:57400")
    a = ap.parse_args()
    srv = grpc.server(futures.ThreadPoolExecutor(max_workers=4))
    pbg.add_gNMIServicer_to_server(Gnmi(), srv)
    srv.add_insecure_port(a.listen)
    srv.start()
    print(f"gnmi-gateway: listening on {a.listen}", flush=True)
    srv.wait_for_termination()


if __name__ == "__main__":
    main()
