#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Fast TCP connect scan of the pen, to look for any pre-existing debug service
(ssh/telnet/adb-tcp/http/...) before we touch its flash.

Usage: python scripts/scan_pen_ports.py [host] [--full]
       default host 192.168.1.76, default scans the 1-10000 range quickly.
"""
import socket
import sys
import threading
import queue
import time

HOST = "192.168.1.76"
PORTS = list(range(1, 10241))
TIMEOUT = 0.35
THREADS = 256


def worker(q, open_ports, host):
    while True:
        try:
            port = q.get_nowait()
        except queue.Empty:
            return
        s = socket.socket()
        s.settimeout(TIMEOUT)
        try:
            if s.connect_ex((host, port)) == 0:
                try:
                    banner = s.recv(64)
                except Exception:
                    banner = b""
                open_ports.append((port, banner))
                print("OPEN %5d %r" % (port, banner[:48]), flush=True)
        except Exception:
            pass
        finally:
            s.close()


def main():
    host = HOST
    ports = PORTS
    for a in sys.argv[1:]:
        if a == "--full":
            ports = list(range(1, 65536))
        elif not a.startswith("-"):
            host = a

    print("scan %s ports=%d..%d threads=%d" % (host, ports[0], ports[-1], THREADS), flush=True)
    t0 = time.time()
    q = queue.Queue()
    for p in ports:
        q.put(p)
    found = []
    ts = [threading.Thread(target=worker, args=(q, found, host), daemon=True) for _ in range(THREADS)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    print("done in %.1fs, open=%s" % (time.time() - t0, sorted(p for p, _ in found)))


if __name__ == "__main__":
    main()
