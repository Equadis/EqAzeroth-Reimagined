"""Windows only. Compiles ComfyAtmosphere's grass vertex shader (the R"HLSL(...)HLSL" text in src/grass.cpp) as vs_2_0 in both
variants (the wind shader, and COPY=1 for the dense-grass copies) with Microsoft's D3DCompiler, and prints the instruction
slots used (vs_2_0 allows 256). The offline glslang check used on Linux validates syntax only, not this limit.

usage: python -I tools/check_grass_vs.py <path to comfyatmosphere\\src\\grass.cpp>
"""
import ctypes, re, sys
from ctypes import wintypes


class Macro(ctypes.Structure):
    _fields_ = [("name", ctypes.c_char_p), ("definition", ctypes.c_char_p)]


def blob(p):
    vt = ctypes.cast(ctypes.cast(p, ctypes.POINTER(ctypes.c_void_p))[0], ctypes.POINTER(ctypes.c_void_p))
    ptr = ctypes.WINFUNCTYPE(ctypes.c_void_p, ctypes.c_void_p)(vt[3])(p)
    size = ctypes.WINFUNCTYPE(ctypes.c_size_t, ctypes.c_void_p)(vt[4])(p)
    return ctypes.string_at(ptr, size)


def main():
    src = open(sys.argv[1], encoding="utf-8").read()
    hlsl = re.search(r'R"HLSL\((.*?)\)HLSL"', src, re.S).group(1).encode()
    d3d = ctypes.windll.LoadLibrary("D3DCompiler_47.dll")
    d3d.D3DCompile.argtypes = [ctypes.c_char_p, ctypes.c_size_t, ctypes.c_char_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_char_p,
                               ctypes.c_char_p, wintypes.UINT, wintypes.UINT, ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(ctypes.c_void_p)]
    d3d.D3DCompile.restype = ctypes.c_long
    d3d.D3DDisassemble.argtypes = [ctypes.c_void_p, ctypes.c_size_t, wintypes.UINT, ctypes.c_char_p, ctypes.POINTER(ctypes.c_void_p)]
    d3d.D3DDisassemble.restype = ctypes.c_long
    bad = 0
    for label, defs in (("wind (COPY undefined)", None), ("copy (COPY=1)", (Macro * 2)(Macro(b"COPY", b"1"), Macro(None, None)))):
        code, err = ctypes.c_void_p(), ctypes.c_void_p()
        hr = d3d.D3DCompile(hlsl, len(hlsl), b"grass", ctypes.cast(defs, ctypes.c_void_p) if defs else None, None, b"main", b"vs_2_0", 0, 0,
                            ctypes.byref(code), ctypes.byref(err))
        if hr < 0:
            bad += 1
            print("FAIL", label, blob(err).decode(errors="replace") if err.value else hr)
            continue
        bc = blob(code)
        asm = ctypes.c_void_p()
        d3d.D3DDisassemble(bc, len(bc), 0, None, ctypes.byref(asm))
        m = re.search(r"approximately (\d+) instruction slots? used", blob(asm).decode(errors="replace"))
        n = int(m.group(1)) if m else -1
        ok = 0 < n <= 256
        bad += not ok
        print(("OK   " if ok else "FAIL ") + f"{label}: {n} instruction slots (limit 256), {len(bc)} bytes")
    sys.exit(1 if bad else 0)


main()
