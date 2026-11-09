"""Turns Logs\\comfyatmos.log into a table of the dense-grass benchmark runs.

Each run in docs/GRASS_BENCHMARK.md is: set a density, wait, `/atmos framelog 30`, then right after it ends `/atmos probe`.
This script walks the log in order and joins every `framelog:` block with the `grass: dense:` and `grass: ...last frame` lines
the probe wrote next, so each row knows the density it was measured at. Nothing is invented: a figure the log did not print is
left blank. Deltas are against the first row with density 1.

usage: python -I tools/parse_framelog.py <comfyatmos.log> [--csv out.csv] [--label "text"]     (several logs: run it once per log)
"""
import csv
import re
import sys

PATTERNS = {
    "head": re.compile(r"framelog: (\d+) frames over ([\d.]+) s: ([\d.]+) ms a frame \(([\d.]+) fps\), slowest 1% ([\d.]+) ms, worst ([\d.]+) ms"),
    "ours": re.compile(r"framelog: our CPU in all a frame ([\d.]+) ms: ([\d.]+) ms in the hooks"),
    "grass_cpu": re.compile(r"framelog: the grass's CPU a frame: ([\d.]+) ms"),
    "locks": re.compile(r"framelog: the client's buffer locks a frame: ([\d.]+) ms"),
    "gpu": re.compile(r"the GPU's whole frame ([\d.]+) ms \((\d+) frames timed\)"),
    "dense": re.compile(r"grass: dense: density ([\d.]+) to (\d+) yards, spread ([\d.]+); (\d+) extra draws last frame \((\d+) batches without ground "
                        r"slope\); the copy shader ([^;]+); ground slopes cached (\d+)"),
    "grass": re.compile(r"grass: (?:on|off)[^;]*; last frame (\d+) draws, (\d+) vertices"),
    "states": re.compile(r"grass: render states (.*)"),
    "ver": re.compile(r"(v\d+\.\d+\.\d+[-\w.]*)"),
}
COLS = ["run", "density", "dist", "frames", "seconds", "ms_frame", "fps", "ms_slowest_1pct", "ms_worst", "our_cpu_ms", "grass_cpu_ms",
        "gpu_frame_ms", "grass_draws", "grass_verts", "extra_draws", "slope_missing_batches", "copy_shader"]


def parse(path):
    rows, cur, pending = [], None, None
    for line in open(path, encoding="utf-8", errors="replace"):
        line = line.strip()
        m = PATTERNS["head"].search(line)
        if m:
            cur = dict(frames=int(m[1]), seconds=float(m[2]), ms_frame=float(m[3]), fps=float(m[4]), ms_slowest_1pct=float(m[5]), ms_worst=float(m[6]))
            rows.append(cur)
            continue
        if cur is not None:
            for key, col in (("ours", "our_cpu_ms"), ("grass_cpu", "grass_cpu_ms"), ("gpu", "gpu_frame_ms")):
                m = PATTERNS[key].search(line)
                if m:
                    cur[col] = float(m[1])
        m = PATTERNS["dense"].search(line)
        if m:
            # the probe comes right after its framelog: attach to the latest row that has no density yet
            target = next((r for r in reversed(rows) if "density" not in r), None)
            if target is not None:
                target.update(density=float(m[1]), dist=int(m[2]), extra_draws=int(m[4]), slope_missing_batches=int(m[5]), copy_shader=m[6].strip())
            continue
        m = PATTERNS["grass"].search(line)
        if m:
            target = next((r for r in reversed(rows) if "grass_draws" not in r), None)
            if target is not None:
                target.update(grass_draws=int(m[1]), grass_verts=int(m[2]))
    out = [r for r in rows if "density" in r]
    for i, r in enumerate(out, 1):
        r["run"] = i
    return out, len(rows) - len(out)


def main():
    a = sys.argv[1:]
    if not a or a[0].startswith("-"):
        raise SystemExit(__doc__)
    rows, unmatched = parse(a[0])
    base = next((r for r in rows if r["density"] == 1.0), None)
    print("| run | density | dist | ms/frame | fps | slowest 1% ms | worst ms | grass CPU ms | d grass CPU | d ms/frame | extra draws | grass draws | GPU frame ms | copy shader |")
    print("|---|---|---|---|---|---|---|---|---|---|---|---|---|---|")
    g = lambda r, k: r.get(k, "")
    for r in rows:
        dg = f"{r['grass_cpu_ms'] - base['grass_cpu_ms']:+.2f}" if base and "grass_cpu_ms" in r and "grass_cpu_ms" in base else ""
        df = f"{r['ms_frame'] - base['ms_frame']:+.2f}" if base else ""
        print(f"| {r['run']} | {r['density']:.2f} | {r['dist']} | {r['ms_frame']:.2f} | {r['fps']:.1f} | {r['ms_slowest_1pct']:.2f} | {r['ms_worst']:.2f} | "
              f"{g(r, 'grass_cpu_ms')} | {dg} | {df} | {g(r, 'extra_draws')} | {g(r, 'grass_draws')} | {g(r, 'gpu_frame_ms')} | {g(r, 'copy_shader')} |")
    if unmatched:
        print(f"\n{unmatched} framelog block(s) had no `/atmos probe` after them, so no density: not listed.")
    if "--csv" in a:
        with open(a[a.index("--csv") + 1], "w", newline="") as f:
            w = csv.DictWriter(f, COLS, extrasaction="ignore")
            w.writeheader()
            w.writerows(rows)


if __name__ == "__main__":
    main()
