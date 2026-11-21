#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""OTA stage-5 proxy: fully OFFLINE fake OTA server.

Upstream (iotapi.abupdate.com) started rate-limiting us:

    {"msg":"To the maximum number of visits","status":1010,"data":null}

so the proxy no longer needs it for the happy path:

  * *.img                  -> served from local patched package (Range/206)
  * checkVersion           -> always answered with our fabricated 4.24.0 body
  * register               -> answered from a cached real response
  * anything else          -> answered with a generic success
  * kill switch            -> tmp\\patch_off.flag present = pass through to real
"""
import os
import sys
import re
import json
import time
import hashlib
import socket
import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = r"."
IMG = sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, "firmware", "update_4.23.0.img")
TPL = os.path.join(ROOT, "reports", "assets", "device", "ota-checkversion-4.22.0.json")
LOG = os.path.join(ROOT, "reports", "assets", "device", "ota-stage6.log")
FLAG_OFF = os.path.join(ROOT, "tmp", "patch_off.flag")

FAKE_VERSION = sys.argv[2] if len(sys.argv) > 2 else "4.24.0"
REAL_HOST = "iotapi.abupdate.com"
LISTEN = ("0.0.0.0", 80)
CHUNK = 5242880

# real register answer captured at 20:20:50 (our pen)
REG_FALLBACK = (b'{"msg":"success","status":1000,"data":{"deviceSecret":'
                b'"<DEVICE_SECRET>","sha256":'
                b'"5b3c8798523b4747b057a3c41a5e172ae8dbba09c02c9e575c64d12d7f000e85",'
                b'"deviceId":"<DEVICE_ID>"}}')


def log(msg):
    line = "[%s] %s" % (time.strftime("%H:%M:%S"), msg)
    print(line, flush=True)
    try:
        with open(LOG, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except Exception:
        pass


DATA = open(IMG, "rb").read()
PKG_SIZE = len(DATA)
PKG_MD5 = hashlib.md5(DATA).hexdigest()
PKG_SHA = hashlib.sha256(DATA).hexdigest()

SEGMENTS = []
_pos = 0
_num = 0
while _pos < PKG_SIZE:
    _end = min(_pos + CHUNK, PKG_SIZE)
    SEGMENTS.append({
        "num": _num,
        "startpos": _pos,
        "md5": hashlib.md5(DATA[_pos:_end]).hexdigest(),
        "endpos": _end,
    })
    _pos = _end
    _num += 1

TEMPLATE_RAW = open(TPL, "rb").read().decode("utf-8")


def build_fake_body():
    doc = json.loads(TEMPLATE_RAW)
    data = doc["data"]
    data["sha256"] = PKG_SHA
    data["releaseNotes"]["version"] = FAKE_VERSION
    try:
        notes = json.loads(data["releaseNotes"]["content"])
        for item in notes:
            item["content"] = "- rev build"
        data["releaseNotes"]["content"] = json.dumps(notes, ensure_ascii=False)
    except Exception:
        pass
    ver = data["version"]
    ver["versionName"] = FAKE_VERSION
    ver["fileSize"] = PKG_SIZE
    ver["md5sum"] = PKG_MD5
    ver["sha"] = PKG_SHA
    ver["segmentMd5"] = json.dumps(SEGMENTS, separators=(",", ":"))
    return json.dumps(doc, ensure_ascii=False).encode("utf-8")


FAKE_BODY = build_fake_body()


def upstream(host, method, path, headers, body):
    try:
        ip = socket.gethostbyname(host)
    except Exception as exc:
        log("!! resolve %s failed: %s" % (host, exc))
        return None
    conn = http.client.HTTPConnection(ip, 80, timeout=20)
    hdrs = {k: v for k, v in headers.items()
            if k.lower() not in ("host", "connection", "content-length")}
    hdrs["Host"] = host
    try:
        conn.request(method, path, body=body, headers=hdrs)
        resp = conn.getresponse()
        payload = resp.read()
        rhdrs = [(k, v) for k, v in resp.getheaders()]
        return resp.status, rhdrs, payload
    except Exception as exc:
        log("!! upstream %s %s failed: %s" % (method, path, exc))
        return None
    finally:
        try:
            conn.close()
        except Exception:
            pass


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        pass

    def _read_body(self):
        n = int(self.headers.get("Content-Length") or 0)
        return self.rfile.read(n) if n > 0 else b""

    def _send(self, status, body, extra=None):
        self.send_response(status)
        for k, v in (extra or []):
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if body:
            self.wfile.write(body)

    def _serve_img(self):
        rng = self.headers.get("Range")
        total = PKG_SIZE
        start, end = 0, total - 1
        if rng:
            m = re.match(r"bytes=(\d*)-(\d*)", rng.strip())
            if m:
                if m.group(1):
                    start = int(m.group(1))
                if m.group(2):
                    end = min(int(m.group(2)), total - 1)
                if not m.group(1) and m.group(2):
                    start = max(0, total - int(m.group(2)))
        if start > end or start >= total:
            self._send(416, b"", [("Content-Range", "bytes */%d" % total)])
            return
        blob = DATA[start:end + 1]
        if rng:
            log("IMG Range=%s -> %d bytes" % (rng, len(blob)))
            self._send(206, blob, [
                ("Content-Range", "bytes %d-%d/%d" % (start, end, total)),
                ("Accept-Ranges", "bytes"),
                ("Content-Type", "application/octet-stream"),
            ])
        else:
            log("IMG full -> %d bytes" % len(blob))
            self._send(200, blob, [
                ("Accept-Ranges", "bytes"),
                ("Content-Type", "application/octet-stream"),
            ])

    def do_GET(self):
        self._dispatch("GET")

    def do_POST(self):
        self._dispatch("POST")

    def _dispatch(self, method):
        path = self.path
        body = self._read_body()
        if ".img" in path:
            self._serve_img()
            return

        if os.path.exists(FLAG_OFF):
            res = upstream(REAL_HOST, method, path, dict(self.headers), body)
            if res is None:
                self._send(502, b'{"msg":"upstream failed","status":1,"data":null}')
                return
            status, hdrs, payload = res
            log("PASS %s %s -> %s" % (method, path, status))
            keep = [(k, v) for k, v in hdrs
                    if k.lower() not in ("content-length", "transfer-encoding", "connection")]
            self._send(status, payload, keep)
            return

        if "checkVersion" in path:
            log(">> checkVersion  body=%s" % body[:220])
            log("   !!! offline PATCH -> %s (md5=%s size=%d)" % (FAKE_VERSION, PKG_MD5, PKG_SIZE))
            self._send(200, FAKE_BODY, [("Content-Type", "application/json")])
            return

        if "register" in path:
            log(">> register  body=%s" % body[:220])
            log("   << offline cached register (deviceId=<DEVICE_ID>)")
            self._send(200, REG_FALLBACK, [("Content-Type", "application/json")])
            return

        log(">> %s %s  body=%s" % (method, path, body[:160]))
        self._send(200, b'{"msg":"success","status":1000,"data":null}',
                   [("Content-Type", "application/json")])


def main():
    log("=" * 60)
    log("stage6 start  pkg=%s size=%d md5=%s" % (IMG, PKG_SIZE, PKG_MD5))
    log("segments=%d  sha=%s" % (len(SEGMENTS), PKG_SHA))
    log("offline mode: checkVersion/register answered locally")
    srv = ThreadingHTTPServer(LISTEN, Handler)
    srv.daemon_threads = True
    log("listening on %s:%d" % LISTEN)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        log("stop")


if __name__ == "__main__":
    main()
