"""Windows only. Compiles ComfyAtmosphere's grass vertex shader (the R"HLSL(...)HLSL" text in src/grass.cpp) with Microsoft's
D3DCompiler_47 as vs_2_0, in both variants the grass uses:
  wind  (COPY undefined): the client's grass with wind and parting, what is drawn for the original batch
  copy  (COPY=1):         the dense-grass copies (offset, thinning, tint, tip height, distance fade)
and checks, from the compiler's own disassembly:
  - it compiles for vs_2_0 (no errors; warnings are printed)
  - instruction slots <= 256, temporary registers <= 12, constant registers <= 256 (vs_2_0 limits)
  - the copy variant reads every constant it is meant to (c22..c26) and the wind variant none of them
  - with --baseline <original grass.cpp>: the wind variant's instructions are IDENTICAL to the unpatched upstream shader's,
    i.e. the dense-grass patch did not change the original grass path at all
Writes <out>/wind.asm, copy.asm and report.json. Exit status 1 on any failure.

usage: python -I tools/check_grass_vs.py <src\\grass.cpp> [--baseline <original src\\grass.cpp>] [--out <dir>]
"""
import ctypes
import json
import os
import re
import sys
from ctypes import wintypes

LIMIT_SLOTS, LIMIT_TEMPS, LIMIT_CONSTS = 256, 12, 256
COPY_ONLY = [22, 23, 24, 25, 26]


class Macro(ctypes.Structure):
    _fields_ = [("name", ctypes.c_char_p), ("definition", ctypes.c_char_p)]


def blob(p):
    vt = ctypes.cast(ctypes.cast(p, ctypes.POINTER(ctypes.c_void_p))[0], ctypes.POINTER(ctypes.c_void_p))
    ptr = ctypes.WINFUNCTYPE(ctypes.c_void_p, ctypes.c_void_p)(vt[3])(p)
    size = ctypes.WINFUNCTYPE(ctypes.c_size_t, ctypes.c_void_p)(vt[4])(p)
    return ctypes.string_at(ptr, size)


def load_hlsl(path):
    src = open(path, encoding="utf-8", errors="replace").read()
    m = re.search(r'R"HLSL\((.*?)\)HLSL"', src, re.S)
    if not m:
        raise SystemExit(f"{path}: no R\"HLSL(...)HLSL\" block")
    return m.group(1).encode()


class Compiler:
    def __init__(self):
        d3d = ctypes.windll.LoadLibrary("D3DCompiler_47.dll")
        d3d.D3DCompile.argtypes = [ctypes.c_char_p, ctypes.c_size_t, ctypes.c_char_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_char_p,
                                   ctypes.c_char_p, wintypes.UINT, wintypes.UINT, ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(ctypes.c_void_p)]
        d3d.D3DCompile.restype = ctypes.c_long
        d3d.D3DDisassemble.argtypes = [ctypes.c_void_p, ctypes.c_size_t, wintypes.UINT, ctypes.c_char_p, ctypes.POINTER(ctypes.c_void_p)]
        d3d.D3DDisassemble.restype = ctypes.c_long
        self.d3d = d3d

    def compile(self, hlsl, copy):
        defs = (Macro * 2)(Macro(b"COPY", b"1"), Macro(None, None)) if copy else None
        code, err = ctypes.c_void_p(), ctypes.c_void_p()
        hr = self.d3d.D3DCompile(hlsl, len(hlsl), b"grass", ctypes.cast(defs, ctypes.c_void_p) if defs else None, None, b"main",
                                 b"vs_2_0", 0, 0, ctypes.byref(code), ctypes.byref(err))
        msgs = blob(err).decode(errors="replace") if err.value else ""
        if hr < 0 or not code.value:
            return None, None, msgs or f"HRESULT {hr:#x}"
        bc = blob(code)
        asm = ctypes.c_void_p()
        self.d3d.D3DDisassemble(bc, len(bc), 0, None, ctypes.byref(asm))
        return bc, blob(asm).decode(errors="replace"), msgs


def analyse(asm):
    slots = re.search(r"approximately (\d+) instruction slots? used", asm)
    body = [l.strip() for l in asm.splitlines() if l.strip() and not l.strip().startswith("//")]
    code = [l for l in body if not l.startswith(("def ", "defi ", "defb ", "dcl", "vs_"))]
    # `def cN, ...` places a literal in a constant register the program did not declare: it counts towards the register
    # limit but is not a read of the grass constants, so it is kept out of the "which constants are read" sets.
    temps = {int(x) for x in re.findall(r"\br(\d+)\b", " ".join(code))}
    read = {int(x) for x in re.findall(r"\bc(\d+)\b", " ".join(code))}
    defined = {int(x) for x in re.findall(r"\bc(\d+)\b", " ".join(l.split(",")[0] for l in body if l.startswith("def ")))}
    every = read | defined
    return dict(slots=int(slots.group(1)) if slots else -1, temps=(max(temps) + 1) if temps else 0,
                const_max=max(every) if every else -1, consts=sorted(read - defined), literal_consts=sorted(defined), instructions=len(code))


def main():
    args = sys.argv[1:]
    if not args or args[0].startswith("-"):
        raise SystemExit(__doc__)
    path = args[0]
    baseline = args[args.index("--baseline") + 1] if "--baseline" in args else None
    out = args[args.index("--out") + 1] if "--out" in args else "."
    os.makedirs(out, exist_ok=True)
    cc = Compiler()
    hlsl = load_hlsl(path)
    report, bad = {}, 0
    asms = {}
    for label, copy in (("wind", False), ("copy", True)):
        bc, asm, msgs = cc.compile(hlsl, copy)
        if bc is None:
            print(f"FAIL {label}: does not compile as vs_2_0\n{msgs}")
            report[label] = {"compiled": False, "errors": msgs}
            bad += 1
            continue
        a = analyse(asm)
        asms[label] = asm
        open(os.path.join(out, label + ".asm"), "w").write(asm)
        problems = []
        if not 0 < a["slots"] <= LIMIT_SLOTS:
            problems.append(f"instruction slots {a['slots']} (limit {LIMIT_SLOTS})")
        if a["temps"] > LIMIT_TEMPS:
            problems.append(f"temporary registers {a['temps']} (limit {LIMIT_TEMPS})")
        if a["const_max"] >= LIMIT_CONSTS:
            problems.append(f"constant register c{a['const_max']} (limit c{LIMIT_CONSTS - 1})")
        used = set(a["consts"])
        if copy:
            missing = [c for c in COPY_ONLY if c not in used]
            if missing:
                problems.append("copy constants never read (optimised away?): " + ", ".join(f"c{c}" for c in missing))
        else:
            leaked = [c for c in COPY_ONLY if c in used]
            if leaked:
                problems.append("wind variant reads copy constants: " + ", ".join(f"c{c}" for c in leaked))
        a.update(compiled=True, bytes=len(bc), warnings=msgs.strip(), problems=problems)
        report[label] = a
        print(("OK   " if not problems else "FAIL ") + f"{label}: {a['slots']} slots (limit {LIMIT_SLOTS}), {a['temps']} temps (limit {LIMIT_TEMPS}), "
              f"constants up to c{a['const_max']}, {len(bc)} bytes" + (" | " + "; ".join(problems) if problems else ""))
        if msgs.strip():
            print("     compiler warnings:", msgs.strip().replace("\n", "\n     "))
        bad += bool(problems)
    if baseline and "wind" in asms:
        bc0, asm0, msgs0 = cc.compile(load_hlsl(baseline), False)
        if bc0 is None:
            print("FAIL baseline does not compile:", msgs0)
            bad += 1
        else:
            strip = lambda t: [l.strip() for l in t.splitlines() if l.strip() and not l.strip().startswith("//")]
            same = strip(asm0) == strip(asms["wind"])
            report["wind_identical_to_baseline"] = same
            print(("OK   " if same else "FAIL ") + f"wind variant instructions identical to the unpatched upstream shader ({len(strip(asm0))} lines)")
            bad += not same
    json.dump(report, open(os.path.join(out, "report.json"), "w"), indent=1)
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
