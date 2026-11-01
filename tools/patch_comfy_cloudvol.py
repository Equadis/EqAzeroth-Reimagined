"""Patch ComfyAtmosphere's cloud pass (clouds.cpp, kPsHlsl) into a Stage 1 volumetric slab (see cloud_ps_vol.hlsl beside this file).

The cloud pixel shader is plain text inside comfyatmos.dll and is compiled at run time (ps_3_0), so it can be replaced without a rebuild
as long as the new text has exactly the old length (the rest is padded with spaces). The pass, its constants (c0..c46), its draw order and
its blend are untouched: it still draws where the game drew its cloud layer, so terrain, water and UI stay in front and no sky mask is needed.
New: the camera ray is clipped to a slab (base = [clouds] layerHeight over the camera, 1100 yards thick), marched in 8 steps through a
density built from the same tiling noise texture (two sizes + puffs), sheared with height so each slice differs (a 3D field from a 2D
texture), with a vertical profile, Beer-law opacity and a one-tap sun shadow per step. The shaped clouds (animals) are kept.
usage:  python -I patch_comfy_cloudvol.py <in comfyatmos.dll> <out comfyatmos.dll>     (input never modified)
        python -I patch_comfy_cloudvol.py --check <dll>
"""
import sys, os, hashlib
START = b"sampler2D sNoise  : register(s0);"
NEWSTART = START
HERE = os.path.dirname(os.path.abspath(__file__))
def new_text(seg_len):
    t = open(os.path.join(HERE, "cloud_ps_vol.hlsl"), "rb").read().replace(b"\r\n", b"\n")
    if len(t) > seg_len: raise SystemExit("new shader is %d bytes too long" % (len(t) - seg_len))
    return t + b" " * (seg_len - len(t))
def locate(b):
    i = b.find(START)
    if i < 0 or b.count(START) != 1: return None
    return i, b.find(b"\x00", i)
def main():
    if len(sys.argv) >= 3 and sys.argv[1] == "--check":
        b = open(sys.argv[2], "rb").read(); loc = locate(b)
        if not loc: print("unknown"); return
        seg = b[loc[0]:loc[1]]
        print("patched" if b"EqShaders Stage 1 volumetric slab" in seg else ("original" if b"float4 Read(float2 p" in seg else "unknown")); return
    if len(sys.argv) != 3: raise SystemExit(__doc__)
    b = open(sys.argv[1], "rb").read(); loc = locate(b)
    if not loc: raise SystemExit("cloud shader not found exactly once")
    seg = b[loc[0]:loc[1]]
    assert b"float4 Read(float2 p" in seg or b"EqShaders Stage 1" in seg   # an already patched file is re-patched with the current shader text
    out = b[:loc[0]] + new_text(len(seg)) + b[loc[1]:]
    assert len(out) == len(b)
    open(sys.argv[2], "wb").write(out); print("wrote", sys.argv[2], hashlib.sha256(out).hexdigest()[:16])
main()
