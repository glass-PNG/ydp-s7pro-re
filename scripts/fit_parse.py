"""Minimal FDT (flattened device tree) parser/dumper for boot.emmc (FIT image)."""
import struct
import sys
import pathlib

FDT_MAGIC = 0xD00DFEED


class Fdt:
    def __init__(self, data: bytes):
        self.data = data
        (magic, totalsize, off_struct, off_strings, off_rsv, version,
         last_comp, boot_cpuid, size_strings, size_struct) = struct.unpack(">10I", data[:40])
        assert magic == FDT_MAGIC, hex(magic)
        self.totalsize = totalsize
        self.off_struct = off_struct
        self.off_strings = off_strings
        self.size_strings = size_strings
        self.size_struct = size_struct
        self.version = version
        self.last_comp = last_comp
        self.boot_cpuid = boot_cpuid
        self.off_rsv = off_rsv
        self.strings = data[off_strings:off_strings + size_strings]
        self.nodes = []  # (path, [(name, raw_value_bytes)])
        self._parse()

    def _str(self, off):
        end = self.strings.index(b"\0", off)
        return self.strings[off:end].decode("latin1")

    def _parse(self):
        d = self.data
        p = self.off_struct

        def align4(x):
            return (x + 3) & ~3
        path = []
        cur = None
        while True:
            tok, = struct.unpack(">I", d[p:p + 4]); p += 4
            if tok == 1:  # BEGIN_NODE
                end = d.index(b"\0", p)
                name = d[p:end].decode("latin1"); p = align4(end + 1)
                if name:
                    path.append(name)
                cur = ("/" + "/".join(path), [])
                self.nodes.append(cur)
            elif tok == 2:  # END_NODE
                if path:
                    path.pop()
            elif tok == 3:  # PROP
                ln, nameoff = struct.unpack(">II", d[p:p + 8]); p += 8
                val = d[p:p + ln]; p = align4(p + ln)
                if cur is not None:
                    cur[1].append((self._str(nameoff), val))
            elif tok == 4:  # NOP
                pass
            elif tok == 9:  # END
                break
            else:
                raise ValueError("bad token %d at %d" % (tok, p))

    def show(self, only=None, maxval=64):
        for path, props in self.nodes:
            if only and only not in path:
                continue
            print(path)
            for k, v in props:
                if len(v) <= maxval and all(32 <= b < 127 or b == 0 for b in v):
                    s = v.split(b"\0")[0].decode("latin1")
                    print("    %-28s = %r  (%d B)" % (k, s, len(v)))
                else:
                    print("    %-28s = <%d B> %s" % (k, len(v), v[:24].hex()))
        print("totalsize", self.totalsize, "version", self.version)


if __name__ == "__main__":
    p = sys.argv[1] if len(sys.argv) > 1 else r".\firmware\extract\boot.emmc"
    only = sys.argv[2] if len(sys.argv) > 2 else None
    fdt = Fdt(pathlib.Path(p).read_bytes())
    fdt.show(only)
