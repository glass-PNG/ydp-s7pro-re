#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""假 DNS：把所有 *.abupdate.com / *.youdao.com / *.mayitek.com 指到本机 192.168.1.66，
其余查询原样转发给路由器 192.168.1.1。全过程记录到 reports\assets\device\dns-hit.log
用法: python scripts\fake_dns_ota.py
"""
import socket
import struct
import sys
import time

MY_IP = "192.168.1.66"
UPSTREAM = ("192.168.1.1", 53)
HIT = ("abupdate", "mayitek")
LOG = r".\reports\assets\device\dns-hit.log"


def log(msg):
    line = "[%s] %s" % (time.strftime("%H:%M:%S"), msg)
    print(line, flush=True)
    try:
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def parse_qname(data):
    """返回 (qname, offset_after_qname)"""
    i = 12
    labels = []
    while True:
        if i >= len(data):
            return None, None
        ln = data[i]
        if ln == 0:
            i += 1
            break
        labels.append(data[i + 1:i + 1 + ln].decode("latin-1"))
        i += 1 + ln
    return ".".join(labels), i


def build_answer(query, qname_end, ip):
    tid = query[:2]
    flags = b"\x81\x80"
    qd = b"\x00\x01"
    an = b"\x00\x01"
    ns = ar = b"\x00\x00"
    question = query[12:qname_end + 4]
    ans = b"\xc0\x0c" + b"\x00\x01" + b"\x00\x01" + struct.pack("!I", 30) + b"\x00\x04" + socket.inet_aton(ip)
    return tid + flags + qd + an + ns + ar + question + ans


def main():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind(("0.0.0.0", 53))
    log("===== fake DNS up on :53, hijack -> %s, upstream %s =====" % (MY_IP, UPSTREAM[0]))
    while True:
        try:
            data, addr = s.recvfrom(2048)
        except Exception as e:
            log("recv err %r" % (e,))
            continue
        qname, qend = parse_qname(data)
        if qname is None:
            continue
        low = qname.lower()
        log("query %-45s from %s:%d" % (qname, addr[0], addr[1]))
        if any(h in low for h in HIT):
            s.sendto(build_answer(data, qend, MY_IP), addr)
            log("  -> hijacked to %s" % MY_IP)
            continue
        try:
            u = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            u.settimeout(3)
            u.sendto(data, UPSTREAM)
            resp, _ = u.recvfrom(4096)
            u.close()
            s.sendto(resp, addr)
        except Exception as e:
            log("  !! upstream fail %r" % (e,))


if __name__ == "__main__":
    main()
