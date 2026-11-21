#!/usr/bin/env python3
"""Rebuild the YDIH OTA container with a new boot.emmc payload.

Layout facts (verified against firmware/update_4.23.0.img):
  0x00 u32 magic/flags      0x04 'YDIH'
  0x08 u32 total package size
  0x18 u64 entry stride (0x88)
  0x88 'YDII'               0xa0 u32 entry count (7)
  entry k at 0x124 + k*0x88:
      +0x00 name1[32]   +0x20 name2[32]
      +0x60 u64 payload offset
      +0x68 u64 0xFFFFFFFFFFFFFFFF
      +0x70 u64 payload size
      +0x80 u32 unknown (0x800 | 0x488800)   +0x84 u32 crc
  payload start offsets satisfy  off % 0x800 == 0xC  and are the smallest such
  value >= previous payload end.
"""
import os
import struct
import sys
import zlib

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "firmware", "update_4.23.0.img")
NEW_BOOT = os.path.join(ROOT, "firmware", "extract", "boot_dump.emmc")
OUT = os.path.join(ROOT, "firmware", "update_dumpboot.img")


def align_payload(x):
    """Smallest value >= x with (value % 0x800) == 0xC."""
    r = x % 0x800
    if r <= 0xC:
        return x + (0xC - r)
    return x + (0x800 - r) + 0xC


def main():
    d = bytearray(open(SRC, "rb").read())
    count = struct.unpack_from("<I", d, 0xA0)[0]
    entries = []
    for k in range(count):
        base = 0x124 + k * 0x88
        n2 = bytes(d[base + 0x20:base + 0x40]).split(b"\x00")[0].decode()
        off = struct.unpack_from("<Q", d, base + 0x60)[0]
        size = struct.unpack_from("<Q", d, base + 0x70)[0]
        crc = struct.unpack_from("<I", d, base + 0x84)[0]
        entries.append({"k": k, "name": n2, "off": off, "size": size, "crc": crc,
                        "data": bytes(d[off:off + size])})

    print("original entries:")
    for e in entries:
        print("  %-22s off=0x%08X size=%9d crc=%08X  zlib.crc32=%08X"
              % (e["name"], e["off"], e["size"], e["crc"], zlib.crc32(e["data"]) & 0xFFFFFFFF))

    new_boot = open(NEW_BOOT, "rb").read()
    for e in entries:
        if e["name"] == "boot.emmc":
            print("replacing boot.emmc: %d -> %d bytes" % (e["size"], len(new_boot)))
            e["data"] = new_boot
            e["size"] = len(new_boot)

    # recompute payload offsets in original order
    cur = 0x124 + count * 0x88
    for e in entries:
        e["off"] = align_payload(cur)
        cur = e["off"] + e["size"]

    total = cur
    print("new total size:", total)

    out = bytearray(total)
    # copy the header + entry table verbatim, then patch fields
    hdr_len = 0x124 + count * 0x88
    out[0:hdr_len] = d[0:hdr_len]
    struct.pack_into("<I", out, 0x08, total)
    for e in entries:
        base = 0x124 + e["k"] * 0x88
        struct.pack_into("<Q", out, base + 0x60, e["off"])
        struct.pack_into("<Q", out, base + 0x70, e["size"])
        out[e["off"]:e["off"] + e["size"]] = e["data"]

    open(OUT, "wb").write(out)
    print("wrote", OUT, len(out))
    # verify round-trip
    chk = open(OUT, "rb").read()
    for e in entries:
        got = chk[e["off"]:e["off"] + e["size"]]
        assert got == e["data"], "payload mismatch for " + e["name"]
    print("round-trip verified for all %d payloads" % len(entries))


if __name__ == "__main__":
    main()
