"""MPQ v1 reader and writer (WoW 1.12 archives). Pure Python 3, no dependencies.

Reader: a port of ComfyAtmosphere's src/mpq.cpp (which is in daily use against real 1.12 archives): header at a 512
aligned offset, encrypted hash/block tables, stored or zlib sectors (mask 0x02), single-unit files. Additions over
that code: bzip2 sectors (0x10, via the stdlib) and an archive stack that applies the client's load order
(base archives < patch.MPQ < patch-0..9 < patch-A..Z). Refused with a clear error: encrypted files, PKWARE implode,
huffman/ADPCM sectors.

Writer: stores each file as zlib sectors, or plain if that is smaller. Round-trip tested against this reader only;
a hand-built archive has NOT been loaded by a 1.12 client by this code. See docs/GRASS_WORKFLOW.md.
"""
import bz2
import os
import re
import struct
import zlib

FLAG_IMPLODE, FLAG_COMPRESS, FLAG_ENCRYPTED = 0x100, 0x200, 0x10000
FLAG_FIX_KEY, FLAG_SINGLE, FLAG_DELETED, FLAG_EXISTS = 0x20000, 0x1000000, 0x2000000, 0x80000000
M32 = 0xFFFFFFFF


def _build_crypt():
    t = [0] * 0x500
    seed = 0x00100001
    for i1 in range(0x100):
        i2 = i1
        for _ in range(5):
            seed = (seed * 125 + 3) % 0x2AAAAB
            t1 = (seed & 0xFFFF) << 16
            seed = (seed * 125 + 3) % 0x2AAAAB
            t[i2] = t1 | (seed & 0xFFFF)
            i2 += 0x100
    return t


CRYPT = _build_crypt()


def hash_string(s, kind):
    """kind 0 = table offset, 1 = name A, 2 = name B, 3 = file key."""
    s1, s2 = 0x7FED7FED, 0xEEEEEEEE
    for c in s.encode("latin-1"):
        if 97 <= c <= 122:
            c -= 32
        if c == 47:
            c = 92
        s1 = (CRYPT[(kind << 8) + c] ^ (s1 + s2)) & M32
        s2 = (c + s1 + s2 + ((s2 << 5) & M32) + 3) & M32
    return s1


def decrypt(words, key):
    out, s2 = [], 0xEEEEEEEE
    for w in words:
        s2 = (s2 + CRYPT[0x400 + (key & 0xFF)]) & M32
        ch = w ^ ((key + s2) & M32)
        out.append(ch)
        key = ((((~key) & M32) << 0x15) + 0x11111111 | (key >> 0x0B)) & M32
        s2 = (ch + s2 + ((s2 << 5) & M32) + 3) & M32
    return out


def encrypt(words, key):
    out, s2 = [], 0xEEEEEEEE
    for ch in words:
        s2 = (s2 + CRYPT[0x400 + (key & 0xFF)]) & M32
        w = ch ^ ((key + s2) & M32)
        out.append(w)
        key = ((((~key) & M32) << 0x15) + 0x11111111 | (key >> 0x0B)) & M32
        s2 = (ch + s2 + ((s2 << 5) & M32) + 3) & M32
    return out


class MpqError(Exception):
    pass


class Archive:
    def __init__(self, path):
        self.path = path
        self.f = open(path, "rb")
        self.f.seek(0, 2)
        size = self.f.tell()
        off = 0
        while off + 32 <= size:
            self.f.seek(off)
            if self.f.read(4) == b"MPQ\x1a":
                break
            off += 512
        else:
            raise MpqError(f"{path}: no MPQ header")
        self.base = off
        self.f.seek(off)
        hdr = self.f.read(32)
        (_, _hsize, _asize, ver, shift, hpos, bpos, hcount, bcount) = struct.unpack("<4sIIHHIIII", hdr)
        if ver != 0:
            raise MpqError(f"{path}: MPQ format version {ver} (only v1/0 is handled)")
        if shift > 20 or hcount == 0 or hcount > 1 << 20 or bcount > 1 << 20:
            raise MpqError(f"{path}: implausible header")
        self.sector = 512 << shift
        self.f.seek(off + hpos)
        raw = self.f.read(hcount * 16)
        self.hash = decrypt(struct.unpack(f"<{hcount * 4}I", raw), hash_string("(hash table)", 3))
        self.f.seek(off + bpos)
        raw = self.f.read(bcount * 16)
        self.block = decrypt(struct.unpack(f"<{bcount * 4}I", raw), hash_string("(block table)", 3))

    def find(self, name):
        count = len(self.hash) // 4
        start = hash_string(name, 0) % count
        a, b = hash_string(name, 1), hash_string(name, 2)
        for i in range(count):
            e = (start + i) % count
            h = self.hash[e * 4:e * 4 + 4]
            if h[3] == M32:
                return -1
            if h[0] == a and h[1] == b and h[3] != 0xFFFFFFFE:
                return h[3]
        return -1

    def has(self, name):
        bi = self.find(name)
        if bi < 0 or bi >= len(self.block) // 4:
            return False
        flags = self.block[bi * 4 + 3]
        return bool(flags & FLAG_EXISTS) and not flags & FLAG_DELETED

    def read(self, name):
        """File bytes, or None if the archive does not hold it. Raises MpqError if it does but cannot be read."""
        bi = self.find(name)
        if bi < 0 or bi >= len(self.block) // 4:
            return None
        pos, csize, fsize, flags = self.block[bi * 4:bi * 4 + 4]
        if not flags & FLAG_EXISTS or flags & FLAG_DELETED:
            return None
        if flags & FLAG_ENCRYPTED:
            raise MpqError(f"{name}: encrypted file (not supported)")
        self.f.seek(self.base + pos)
        raw = self.f.read(csize)
        if len(raw) != csize:
            raise MpqError(f"{name}: truncated")
        if not flags & (FLAG_COMPRESS | FLAG_IMPLODE):
            return raw[:fsize]

        def sector(p, want):
            if len(p) >= want:
                return p[:want]
            if flags & FLAG_IMPLODE and not flags & FLAG_COMPRESS:
                raise MpqError(f"{name}: PKWARE implode (not supported)")
            m = p[0]
            body = p[1:]
            if m == 0x02:
                out = zlib.decompress(body)
            elif m == 0x10:
                out = bz2.decompress(body)
            else:
                raise MpqError(f"{name}: sector compression 0x{m:02X} (only zlib and bzip2 are supported)")
            if len(out) != want:
                raise MpqError(f"{name}: sector size mismatch")
            return out

        if flags & FLAG_SINGLE:
            return sector(raw, fsize)
        n = (fsize + self.sector - 1) // self.sector
        if (n + 1) * 4 > len(raw):
            raise MpqError(f"{name}: sector table out of range")
        tab = struct.unpack(f"<{n + 1}I", raw[:(n + 1) * 4])
        out = bytearray()
        for i in range(n):
            want = min(self.sector, fsize - len(out))
            if tab[i] > tab[i + 1] or tab[i + 1] > len(raw):
                raise MpqError(f"{name}: bad sector table")
            out += sector(raw[tab[i]:tab[i + 1]], want)
        return bytes(out)

    def listfile(self):
        try:
            d = self.read("(listfile)")
        except MpqError:
            return []
        return d.decode("latin-1").split("\r\n") if d else []

    def close(self):
        self.f.close()


def _rank(fname):
    low = fname.lower()
    if low == "patch.mpq":
        return 1
    m = re.fullmatch(r"patch-([0-9a-z])\.mpq", low)
    if m:
        c = m.group(1).upper()
        return 2 + ord(c)
    return 0


class Stack:
    """Every archive in a Data folder, best (highest rank) first, as the client would stack them."""

    def __init__(self, data_dir):
        names = [n for n in os.listdir(data_dir) if n.lower().endswith(".mpq")]
        names.sort(key=lambda n: (-_rank(n), n.lower()))
        self.archives = []
        self.skipped = []
        for n in names:
            try:
                self.archives.append(Archive(os.path.join(data_dir, n)))
            except (MpqError, OSError, struct.error) as e:
                self.skipped.append((n, str(e)))

    def read(self, name):
        for a in self.archives:
            d = a.read(name)
            if d is not None:
                return d
        return None

    def holders(self, name):
        return [os.path.basename(a.path) for a in self.archives if a.has(name)]

    def names(self):
        """Names known from the (listfile)s. Archives without one contribute nothing."""
        s = set()
        for a in self.archives:
            s.update(x for x in a.listfile() if x)
        return sorted(s)

    def close(self):
        for a in self.archives:
            a.close()


def write_archive(path, files, sector_shift=3, compress=True):
    """files: {archive path: bytes}. Writes an MPQ v1 with a (listfile). Overwrites path."""
    files = dict(files)
    files["(listfile)"] = ("\r\n".join(sorted(k for k in files if k != "(listfile)")) + "\r\n").encode("latin-1")
    sector = 512 << sector_shift
    n = len(files)
    hcount = 16
    while hcount < n * 2:      # load factor <= 0.5
        hcount *= 2
    body = bytearray()
    block = []
    hdr_size = 32
    hashes = [[M32, M32, M32, M32] for _ in range(hcount)]
    for idx, (name, data) in enumerate(sorted(files.items())):
        pos = hdr_size + len(body)
        if not data:
            block.append((pos, 0, 0, FLAG_EXISTS))
        else:
            nsec = (len(data) + sector - 1) // sector
            parts = []
            for i in range(nsec):
                chunk = data[i * sector:(i + 1) * sector]
                z = b"\x02" + zlib.compress(chunk, 9) if compress else None
                parts.append(z if z is not None and len(z) < len(chunk) else chunk)
            if compress and any(len(p) != min(sector, len(data) - i * sector) for i, p in enumerate(parts)):
                tab, off = [], (nsec + 1) * 4
                for p in parts:
                    tab.append(off)
                    off += len(p)
                tab.append(off)
                blob = struct.pack(f"<{nsec + 1}I", *tab) + b"".join(parts)
                if len(blob) < len(data):
                    body += blob
                    block.append((pos, len(blob), len(data), FLAG_EXISTS | FLAG_COMPRESS))
                    _put_hash(hashes, name, idx)
                    continue
            body += data
            block.append((pos, len(data), len(data), FLAG_EXISTS))
        _put_hash(hashes, name, idx)
    hpos = hdr_size + len(body)
    bpos = hpos + hcount * 16
    hwords = [w for h in hashes for w in h]
    bwords = [w for b in block for w in b]
    hraw = struct.pack(f"<{len(hwords)}I", *encrypt(hwords, hash_string("(hash table)", 3)))
    braw = struct.pack(f"<{len(bwords)}I", *encrypt(bwords, hash_string("(block table)", 3)))
    total = bpos + len(braw)
    header = struct.pack("<4sIIHHIIII", b"MPQ\x1a", 32, total, 0, sector_shift, hpos, bpos, hcount, len(block))
    with open(path, "wb") as f:
        f.write(header + bytes(body) + hraw + braw)


def _put_hash(hashes, name, block_index):
    count = len(hashes)
    start = hash_string(name, 0) % count
    for i in range(count):
        e = hashes[(start + i) % count]
        if e[3] == M32:
            e[0], e[1] = hash_string(name, 1), hash_string(name, 2)
            e[2] = 0          # locale (neutral) and platform 0
            e[3] = block_index
            return
    raise MpqError("hash table full")
