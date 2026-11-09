"""PKWARE Data Compression Library ("implode") decoder, a port of Mark Adler's contrib/blast/blast.c from zlib (zlib licence).
Used for MPQ sectors stored with compression mask 0x08 (and whole-file implode). Tested against blast's own test vector."""

MAXBITS = 13
LITLEN = [11, 124, 8, 7, 28, 7, 188, 13, 76, 4, 10, 8, 12, 10, 12, 10, 8, 23, 8, 9, 7, 6, 7, 8, 7, 6, 55, 8, 23, 24, 12, 11, 7, 9, 11, 12,
          6, 7, 22, 5, 7, 24, 6, 11, 9, 6, 7, 22, 7, 11, 38, 7, 9, 8, 25, 11, 8, 11, 9, 12, 8, 12, 5, 38, 5, 38, 5, 11, 7, 5, 6, 21,
          6, 10, 53, 8, 7, 24, 10, 27, 44, 253, 253, 253, 252, 252, 252, 13, 12, 45, 12, 45, 12, 61, 12, 45, 44, 173]
LENLEN = [2, 35, 36, 53, 38, 23]
DISTLEN = [2, 20, 53, 230, 247, 151, 248]
BASE = [3, 2, 4, 5, 6, 7, 8, 9, 10, 12, 16, 24, 40, 72, 136, 264]
EXTRA = [0, 0, 0, 0, 0, 0, 0, 0, 1, 2, 3, 4, 5, 6, 7, 8]


class PkwareError(Exception):
    pass


def _construct(rep):
    length = []
    for r in rep:
        length += [r & 15] * ((r >> 4) + 1)
    count = [0] * (MAXBITS + 1)
    for l in length:
        count[l] += 1
    offs = [0] * (MAXBITS + 2)
    for l in range(1, MAXBITS):
        offs[l + 1] = offs[l] + count[l]
    symbol = [0] * len(length)
    for s, l in enumerate(length):
        if l:
            symbol[offs[l]] = s
            offs[l] += 1
    return count, symbol


_LIT, _LEN, _DIST = _construct(LITLEN), _construct(LENLEN), _construct(DISTLEN)


def explode(data, expected=None):
    pos, bitbuf, bitcnt = 0, 0, 0
    n = len(data)

    def bit():
        nonlocal pos, bitbuf, bitcnt
        if bitcnt == 0:
            if pos >= n:
                raise PkwareError("ran out of input")
            bitbuf, pos, bitcnt = data[pos], pos + 1, 8
        b = bitbuf & 1
        bitbuf >>= 1
        bitcnt -= 1
        return b

    def bits(k):
        v = 0
        for i in range(k):
            v |= bit() << i
        return v

    def decode(h):
        count, symbol = h
        code = first = index = 0
        for ln in range(1, MAXBITS + 1):
            code |= bit() ^ 1
            c = count[ln]
            if code < first + c:
                return symbol[index + (code - first)]
            index += c
            first = (first + c) << 1
            code <<= 1
        raise PkwareError("bad code")

    lit = bits(8)
    if lit > 1:
        raise PkwareError("bad literal flag")
    dict_bits = bits(8)
    if dict_bits < 4 or dict_bits > 6:
        raise PkwareError("bad dictionary size")
    out = bytearray()
    while True:
        if bit():
            sym = decode(_LEN)
            ln = BASE[sym] + bits(EXTRA[sym])
            if ln == 519:
                break
            shift = 2 if ln == 2 else dict_bits
            dist = (decode(_DIST) << shift) + bits(shift) + 1
            if dist > len(out):
                raise PkwareError("distance too far back")
            for _ in range(ln):
                out.append(out[-dist])
        else:
            out.append(decode(_LIT) if lit else bits(8))
    if expected is not None and len(out) != expected:
        raise PkwareError(f"size {len(out)} != expected {expected}")
    return bytes(out)
