#!/usr/bin/env python3
"""Build a new FIT image (boot.emmc) = original kernel + original DTB +
a gzip initramfs ramdisk, then write it to firmware/extract/boot_dump.emmc.

Keeps kernel/DTB bytes identical; only adds a /images/ramdisk-1 node, references
it from the config, and recomputes hashes (crc32 for kernel/ramdisk, sha256 for dtb).
"""
import hashlib
import os
import struct
import zlib

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "firmware", "extract", "boot.emmc")
RAMDISK = os.path.join(ROOT, "tmp", "boot", "initramfs.cpio.gz")
OUT = os.path.join(ROOT, "firmware", "extract", "boot_dump.emmc")

RAMDISK_LOAD = 0x84000000
KERNEL_LOAD = 0x80108000
FDT_MAGIC = 0xD00DFEED

FDT_BEGIN_NODE, FDT_END_NODE, FDT_PROP, FDT_NOP, FDT_END = 1, 2, 3, 4, 9


def find_kernel(d):
    # kernel Image starts with a known little-endian ARM opcode sequence
    sig = bytes.fromhex("01908fe219ff2fe1")
    i = d.find(sig)
    if i < 0:
        raise SystemExit("kernel signature not found")
    return i


def find_dtb(d):
    pos = 0
    while True:
        i = d.find(struct.pack(">I", FDT_MAGIC), pos)
        if i < 0:
            raise SystemExit("dtb not found")
        total = struct.unpack(">I", d[i + 4:i + 8])[0]
        if 1000 < total < 200000 and i + total <= len(d):
            return i, total
        pos = i + 4


class FdtBuilder:
    def __init__(self):
        self.strings = bytearray()
        self.struct = bytearray()
        self._stroff = {}

    def _stroff_of(self, name):
        if name not in self._stroff:
            self._stroff[name] = len(self.strings)
            self.strings += name.encode() + b"\x00"
        return self._stroff[name]

    def begin(self, name):
        self.struct += struct.pack(">I", FDT_BEGIN_NODE)
        self.struct += name.encode() + b"\x00"
        self.struct += b"\x00" * ((4 - (len(self.struct) % 4)) % 4)

    def end(self):
        self.struct += struct.pack(">I", FDT_END_NODE)

    def prop(self, name, value):
        self.struct += struct.pack(">III", FDT_PROP, len(value), self._stroff_of(name))
        self.struct += value
        self.struct += b"\x00" * ((4 - (len(self.struct) % 4)) % 4)

    def prop_str(self, name, s):
        self.prop(name, s.encode() + b"\x00")

    def prop_u32(self, name, v):
        self.prop(name, struct.pack(">I", v))

    def prop_u64(self, name, v):
        self.prop(name, struct.pack(">Q", v))

    def build(self):
        self.struct += struct.pack(">I", FDT_END)
        rsv = struct.pack(">QQ", 0, 0)  # 16-byte zero memory reserve map
        hdr_len = 40
        off_rsv = hdr_len
        off_struct = off_rsv + len(rsv)
        off_strings = off_struct + len(self.struct)
        total = off_strings + len(self.strings)
        hdr = struct.pack(
            ">10I", FDT_MAGIC, total, off_struct, off_strings, off_rsv,
            17, 16, 0, len(self.strings), len(self.struct))
        return hdr + rsv + bytes(self.struct) + bytes(self.strings)


def main():
    d = open(SRC, "rb").read()
    koff = find_kernel(d)
    doff, dtotal = find_dtb(d)
    kernel = d[koff:koff + 4730240]
    dtb = d[doff:doff + dtotal]
    rd = open(RAMDISK, "rb").read()

    crc_kernel = zlib.crc32(kernel) & 0xFFFFFFFF
    print("kernel @0x%x len=%d crc32=%08x (original expects 729edb39)" % (koff, len(kernel), crc_kernel))
    sha_dtb = hashlib.sha256(dtb).hexdigest()
    print("dtb    @0x%x len=%d sha256=%s" % (doff, len(dtb), sha_dtb))
    print("ramdisk len=%d crc32=%08x" % (len(rd), zlib.crc32(rd) & 0xFFFFFFFF))

    # kernel data must still be exactly 4,730,240 bytes long; sanity check the tail
    if len(kernel) != 4730240:
        raise SystemExit("kernel length mismatch")

    b = FdtBuilder()
    b.begin("")
    b.prop("timestamp", b"ihNG")          # vendor keeps a 4-byte raw value here
    b.prop_str("description", "Various kernels, ramdisks and FDT blobs")
    b.prop_u32("#address-cells", 2)

    b.begin("images")
    b.begin("kernel-1")
    b.prop_str("description", "cvitek kernel")
    b.prop("data", kernel)
    b.prop_str("type", "kernel")
    b.prop_str("arch", "arm")
    b.prop_str("os", "linux")
    b.prop_str("compression", "none")
    b.prop_u64("load", KERNEL_LOAD)
    b.prop_u64("entry", KERNEL_LOAD)
    b.begin("hash-2")
    b.prop("value", struct.pack(">I", crc_kernel))
    b.prop_str("algo", "crc32")
    b.end()
    b.end()

    b.begin("fdt-cv1813ha_wevb_0007a_emmc")
    b.prop_str("description", "cvitek device tree - cv1813ha_wevb_0007a_emmc")
    b.prop("data", dtb)
    b.prop_str("type", "flat_dt")
    b.prop_str("arch", "arm")
    b.prop_str("compression", "none")
    b.begin("hash-1")
    b.prop("value", hashlib.sha256(dtb).digest())
    b.prop_str("algo", "sha256")
    b.end()
    b.end()

    b.begin("ramdisk-1")
    b.prop_str("description", "dsh initramfs (emmc dump)")
    b.prop("data", rd)
    b.prop_str("type", "ramdisk")
    b.prop_str("arch", "arm")
    b.prop_str("os", "linux")
    b.prop_str("compression", "gzip")
    b.prop_u64("load", RAMDISK_LOAD)
    b.prop_u64("entry", RAMDISK_LOAD)
    b.begin("hash-3")
    b.prop("value", struct.pack(">I", zlib.crc32(rd) & 0xFFFFFFFF))
    b.prop_str("algo", "crc32")
    b.end()
    b.end()
    b.end()  # images

    b.begin("configurations")
    b.begin("config-cv1813ha_wevb_0007a_emmc")
    b.prop_str("description", "boot cvitek system with board cv1813ha_wevb_0007a_emmc")
    b.prop_str("kernel", "kernel-1")
    b.prop_str("fdt", "fdt-cv1813ha_wevb_0007a_emmc")
    b.prop_str("ramdisk", "ramdisk-1")
    b.end()
    b.end()

    b.end()  # root

    fit = b.build()
    open(OUT, "wb").write(fit)
    print("fit bytes:", len(fit), "->", OUT)


if __name__ == "__main__":
    main()
