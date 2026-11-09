"""WDBC (1.12 DBC) reader/writer: every field is a u32 (ints, floats as bit patterns, string offsets)."""
import struct


class Dbc:
    def __init__(self, data):
        if data[:4] != b"WDBC":
            raise ValueError("not a WDBC file")
        n, nf, rs, ss = struct.unpack_from("<IIII", data, 4)
        if rs != nf * 4:
            raise ValueError(f"record size {rs} != fields {nf} x 4 (a field wider than 4 bytes is not handled)")
        end = 20 + n * rs
        if end + ss > len(data):
            raise ValueError("truncated")
        self.fields = nf
        self.rows = [list(struct.unpack_from(f"<{nf}I", data, 20 + i * rs)) for i in range(n)]
        self.strings = bytes(data[end:end + ss])

    def string(self, off):
        if off >= len(self.strings):
            return None
        e = self.strings.find(b"\0", off)
        return self.strings[off:e].decode("latin-1") if e >= 0 else None

    def is_string_field(self, col):
        """True if every non-zero value in the column is the start of a NUL-terminated printable string."""
        seen = 0
        for r in self.rows:
            v = r[col]
            if v == 0:
                continue
            s = self.string(v)
            if s is None or not s or any(ord(c) < 32 or ord(c) > 126 for c in s) or (v > 0 and self.strings[v - 1:v] != b"\0"):
                return False
            seen += 1
        return seen > 0

    def to_bytes(self):
        out = bytearray(b"WDBC")
        out += struct.pack("<IIII", len(self.rows), self.fields, self.fields * 4, len(self.strings))
        for r in self.rows:
            out += struct.pack(f"<{self.fields}I", *r)
        return bytes(out) + self.strings
