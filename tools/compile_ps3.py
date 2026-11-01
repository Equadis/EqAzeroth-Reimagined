"""Compile one HLSL pixel shader (entry `main`) as ps_3_0 with D3DCompiler_47 and report errors and the instruction-slot count.
usage: python -I compile_ps3.py <file.hlsl> [-DNAME=VALUE ...]   (what Comfy's own compile does for its embedded shaders: ps_3_0)"""
import ctypes, re, sys
from ctypes import wintypes
def blob_bytes(p):
    if not p.value: return b""
    vt = ctypes.cast(ctypes.cast(p, ctypes.POINTER(ctypes.c_void_p))[0], ctypes.POINTER(ctypes.c_void_p))
    get_ptr = ctypes.WINFUNCTYPE(ctypes.c_void_p, ctypes.c_void_p)(vt[3]); get_size = ctypes.WINFUNCTYPE(ctypes.c_size_t, ctypes.c_void_p)(vt[4])
    return ctypes.string_at(get_ptr(p), get_size(p))
src = open(sys.argv[1], "rb").read()
defs = "".join("#define %s\n" % a[2:].replace("=", " ", 1) for a in sys.argv[2:] if a.startswith("-D")).encode()
src = defs + src
d3d = ctypes.windll.LoadLibrary("D3DCompiler_47.dll")
d3d.D3DCompile.argtypes = [ctypes.c_char_p, ctypes.c_size_t, ctypes.c_char_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_char_p, ctypes.c_char_p,
                           wintypes.UINT, wintypes.UINT, ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(ctypes.c_void_p)]
d3d.D3DCompile.restype = ctypes.c_long
code, err = ctypes.c_void_p(), ctypes.c_void_p()
hr = d3d.D3DCompile(src, len(src), b"cloud.hlsl", None, None, b"main", b"ps_3_0", 0, 0, ctypes.byref(code), ctypes.byref(err))
if hr < 0:
    print("FAIL"); print(blob_bytes(err).decode("utf-8", "replace")[:3000]); sys.exit(1)
d3d.D3DDisassemble.argtypes = [ctypes.c_void_p, ctypes.c_size_t, wintypes.UINT, ctypes.c_char_p, ctypes.POINTER(ctypes.c_void_p)]
d3d.D3DDisassemble.restype = ctypes.c_long
raw = blob_bytes(code); buf = ctypes.create_string_buffer(raw, len(raw)); dis = ctypes.c_void_p()
d3d.D3DDisassemble(buf, len(raw), 0, None, ctypes.byref(dis))
txt = blob_bytes(dis).decode("utf-8", "replace")
m = re.search(r"approximately (\d+) instruction slots used", txt)
print("OK ps_3_0", "slots:", m.group(1) if m else "?", "| bytes:", len(raw))
