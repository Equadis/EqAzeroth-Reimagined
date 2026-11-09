"""Compatibility check of EqAzeroth's binary text patches (tools/patch_comfy_*.py) against a comfyatmos.dll.

The patches replace HLSL text inside the DLL with same-length text. The dense-grass change is a SOURCE patch (comfy/*.patch)
applied before the DLL is compiled, so the two never touch the same bytes unless they edit the same shader text; this
script proves it on the real build rather than assuming it:
  1. baseline: every script's --check against the untouched DLL
  2. apply all patches in the documented order, checking each one right after it is applied
  3. the patched DLL has exactly the same size, and the dense-grass shader text is still intact in it
Order (HANDOFF_PHASE1.md section 4): water, shore, wavetile, reflect, shoref, glint, fogfar, splash, cloudvol.
Scripts that exist only in other sessions' trees (fog, lamp, foliage patches) are NOT covered and are listed as such.
A later patch may rewrite a block an earlier one wrote, so after the full chain an earlier --check can say "unknown":
that is reported as "superseded", not as a failure, as long as the patch reported "patched" right after it was applied.

usage: python -I tools/check_comfy_patches.py <comfyatmos.dll> [--out <dir>] [--expect-grass]
Writes <dir>/comfyatmos.eqpatched.dll (the full chain) and <dir>/patch-report.md. Never modifies its input. Exit 1 on failure.
"""
import hashlib
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ORDER = ["water", "shore", "wavetile", "reflect", "shoref", "glint", "fogfar", "splash", "cloudvol"]
NOT_IN_REPO = ["patch_comfy_fog*.py", "patch_comfy_lamp*.py", "patch_comfy_lightshape.py (foliage/lamp/fog work, HANDOFF_PHASE1.md section 7)"]
GRASS_MARKERS = [b"gCp0 : register(c22)", b"#if COPY"]          # present only in the dense-grass shader source


def run(*args):
    # -W ignore: some scripts have a harmless SyntaxWarning in a docstring that would otherwise reach stderr
    r = subprocess.run([sys.executable, "-I", "-W", "ignore", *args], capture_output=True, text=True)
    return r.returncode, r.stdout.strip(), r.stderr.strip()


def check(name, dll):
    rc, out, err = run(os.path.join(HERE, f"patch_comfy_{name}.py"), "--check", dll)
    return out.splitlines()[-1].strip() if out else f"(no output, rc={rc}: {err[-120:]})"


def changed(a, b):
    return sum(1 for x, y in zip(a, b) if x != y)


MAX_STAGE_BYTES = 16384      # a text patch of one shader; more means a patch rewrote something it should not have


def main():
    a = sys.argv[1:]
    if not a or a[0].startswith("-"):
        raise SystemExit(__doc__)
    dll = a[0]
    out = a[a.index("--out") + 1] if "--out" in a else "."
    expect_grass = "--expect-grass" in a
    os.makedirs(out, exist_ok=True)
    src = open(dll, "rb").read()
    lines, bad = [], 0
    lines.append(f"# EqAzeroth patch compatibility\n\ninput `{os.path.basename(dll)}` {len(src)} bytes, sha256 `{hashlib.sha256(src).hexdigest()[:16]}`\n")
    lines.append("## Baseline: each --check against the untouched DLL\n\n| patch | state |\n|---|---|")
    base = {}
    for n in ORDER:
        base[n] = check(n, dll)
        lines.append(f"| {n} | {base[n]} |")
    lines.append("\n`original` = the text the patch expects is present exactly once. `unknown` for `shoref` is expected: it builds on `reflect`'s block.\n")
    if expect_grass:
        miss = [m.decode() for m in GRASS_MARKERS if m not in src]
        lines.append(f"Dense-grass shader text in the input: {'present' if not miss else 'MISSING ' + str(miss)}\n")
        bad += bool(miss)
    lines.append("## Applying in order\n\n| step | applies | state right after | bytes changed | state after the whole chain |\n|---|---|---|---|---|")
    cur, steps = dll, []
    for i, n in enumerate(ORDER):
        nxt = os.path.join(out, f"stage{i + 1}_{n}.dll")
        rc, msg, err = run(os.path.join(HERE, f"patch_comfy_{n}.py"), cur, nxt)
        msg = msg or err
        ok = rc == 0 and os.path.exists(nxt)
        right_after = check(n, nxt) if ok else "-"
        nch = changed(open(cur, "rb").read(), open(nxt, "rb").read()) if ok else 0
        steps.append((n, ok, right_after, msg, nch))
        if not ok:
            bad += 1
            lines.append(f"| {n} | **FAILED** | - | - | - |")
            lines.append(f"\n```\n{msg}\n```")
            break
        if right_after != "patched" or nch > MAX_STAGE_BYTES:
            bad += 1
        cur = nxt
    final = open(cur, "rb").read() if cur != dll else src
    for n, ok, right_after, msg, nch in steps:
        if ok:
            end = check(n, cur)
            note = "superseded by a later patch" if (right_after == "patched" and end == "unknown") else end
            flag = " **(too many: a shader text patch should change under %d)**" % MAX_STAGE_BYTES if nch > MAX_STAGE_BYTES else ""
            lines.append(f"| {n} | yes | {right_after} | {nch}{flag} | {note} |")
    sized = len(final) == len(src)
    lines.append(f"\nSize unchanged by the whole chain: **{'yes' if sized else 'NO'}** ({len(src)} -> {len(final)} bytes). Bytes changed: {sum(1 for x, y in zip(src, final) if x != y)}.")
    bad += not sized
    if expect_grass:
        miss = [m.decode() for m in GRASS_MARKERS if m not in final]
        lines.append(f"Dense-grass shader text after the chain: {'intact' if not miss else 'DAMAGED ' + str(miss)}.")
        bad += bool(miss)
    lines.append("\n## Not covered\n\nPatch scripts that are not in this repository, so not checked: " + "; ".join(NOT_IN_REPO) + ".")
    dst = os.path.join(out, "comfyatmos.eqpatched.dll")
    if cur != dll:
        open(dst, "wb").write(final)
        lines.append(f"\nFull chain written to `{os.path.basename(dst)}` sha256 `{hashlib.sha256(final).hexdigest()[:16]}`.")
    for f in os.listdir(out):
        if f.startswith("stage") and f.endswith(".dll"):
            os.remove(os.path.join(out, f))
    open(os.path.join(out, "patch-report.md"), "w").write("\n".join(lines) + "\n")
    print("\n".join(lines))
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
