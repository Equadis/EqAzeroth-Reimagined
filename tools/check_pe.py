"""Prints a PE file's machine type and whether it is a DLL; exits 1 unless it is a 32-bit (x86) DLL.
usage: python -I tools/check_pe.py <file.dll>"""
import struct, sys

d = open(sys.argv[1], "rb").read()
if d[:2] != b"MZ":
    raise SystemExit("not a PE file")
pe = struct.unpack_from("<I", d, 0x3C)[0]
if d[pe:pe + 4] != b"PE\0\0":
    raise SystemExit("no PE signature")
machine, nsec, _, _, _, optsize, chars = struct.unpack_from("<HHIIIHH", d, pe + 4)
magic = struct.unpack_from("<H", d, pe + 24)[0]
name = {0x14C: "x86 (i386)", 0x8664: "x64", 0x1C4: "ARM", 0xAA64: "ARM64"}.get(machine, hex(machine))
print(f"{sys.argv[1]}: machine {name}, optional header {'PE32' if magic == 0x10B else 'PE32+' if magic == 0x20B else hex(magic)}, "
      f"{'DLL' if chars & 0x2000 else 'not a DLL'}, {len(d)} bytes")
sys.exit(0 if machine == 0x14C and magic == 0x10B and chars & 0x2000 else 1)
