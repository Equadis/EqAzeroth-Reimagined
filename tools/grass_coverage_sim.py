"""Offline grass coverage / cost simulator (no game needed).

Question it answers: how much of the ground is hidden from a WoW-style third-person camera per unit of rendering cost,
for different grass card types, densities, and "multiply the native instances with offset copies" schemes.

Model (deliberately simple, all assumptions are parameters):
- flat ground z=0, camera at eye height `--eye` yards, pitch `--pitch` degrees below horizontal, 16:9, vertical FOV `--fov`.
- a grass instance = N vertical alpha-cutout quads sharing a root, evenly spread in yaw, each quad w x h yards with
  `blades` tapered blades. Alpha is evaluated per ray (a hit counts only where a blade is opaque).
- one ray per sample pixel; "covered" = ray meets an opaque blade before the ground.
- cost proxies: instances/yd2, vertices/yd2 (4 per quad: CPU copy cost of the native fill loop, and vertex work),
  quad hits per ray (alpha-test fragment overdraw proxy, bands by ground distance).
Not modelled: lighting, mip/alpha-to-coverage, slopes, texture detail, aliasing. Coverage is geometric upper-bound-ish.

usage: python -I tools/grass_coverage_sim.py [--pitch 12] [--out report.md]
"""
import argparse, math, sys
import numpy as np
from numba import njit

BANDS = [(0, 15), (15, 40), (40, 60)]
AREA_X = (-45.0, 45.0)
AREA_Y = (-10.0, 65.0)


@njit(cache=True)
def blade_alpha(u, v, blades, seed):
    # u in [0,1] across the card, v in [0,1] up the card. tapered blades, jittered centres, width shrinks to the tip
    if v < 0.0 or v > 1.0 or u < 0.0 or u > 1.0:
        return False
    pitch = 1.0 / blades
    k = int(u / pitch)
    if k >= blades:
        k = blades - 1
    # deterministic per-blade jitter from (k, seed)
    h = (k * 73856093 ^ seed * 19349663) & 0xFFFF
    jit = (h / 65535.0 - 0.5) * 0.5 * pitch
    c = (k + 0.5) * pitch + jit
    bh = 0.7 + 0.3 * (((h >> 3) & 255) / 255.0)  # blade height share
    if v > bh:
        return False
    half = 0.5 * pitch * 0.9 * (1.0 - v / bh)
    return abs(u - c) <= half


@njit(cache=True)
def build_grid(cx, cy, cell, nx, ny):
    n = cx.shape[0]
    counts = np.zeros(nx * ny, np.int32)
    for i in range(n):
        gx = int((cx[i] - AREA_X[0]) / cell)
        gy = int((cy[i] - AREA_Y[0]) / cell)
        if 0 <= gx < nx and 0 <= gy < ny:
            counts[gy * nx + gx] += 1
    start = np.zeros(nx * ny + 1, np.int32)
    for i in range(nx * ny):
        start[i + 1] = start[i] + counts[i]
    fill = start[:-1].copy()
    order = np.zeros(n, np.int32)
    for i in range(n):
        gx = int((cx[i] - AREA_X[0]) / cell)
        gy = int((cy[i] - AREA_Y[0]) / cell)
        if 0 <= gx < nx and 0 <= gy < ny:
            order[fill[gy * nx + gx]] = i
            fill[gy * nx + gx] += 1
    return start, order


@njit(cache=True)
def trace(cx, cy, yaw0, scale, quads, w, h, blades, seed, start, order, cell, nx, ny,
          eye, pitch, fov, W, H):
    # returns per-ray: ground distance (-1 = sky/out of area), covered flag, quad hits
    out_d = np.full(W * H, -1.0)
    out_c = np.zeros(W * H, np.uint8)
    out_q = np.zeros(W * H, np.int32)
    tanh = math.tan(fov * 0.5)
    aspect = W / H
    cp, sp = math.cos(pitch), math.sin(pitch)
    for py in range(H):
        for px in range(W):
            sx = ((px + 0.5) / W * 2 - 1) * tanh * aspect
            sy = (1 - (py + 0.5) / H * 2) * tanh
            # camera looks along +y, pitched down; right = +x, up' = (0, sp, cp)
            dx = sx
            dy = cp - sy * sp
            dz = -sp + sy * cp
            if dz >= -1e-6:
                continue
            tg = eye / -dz
            gx_ = dx * tg
            gy_ = dy * tg
            if gx_ < AREA_X[0] or gx_ > AREA_X[1] or gy_ < AREA_Y[0] or gy_ > AREA_Y[1]:
                continue
            out_d[py * W + px] = math.sqrt(gx_ * gx_ + gy_ * gy_)
            ox, oy, oz = 0.0, 0.0, eye
            step = cell * 0.5
            t = 0.0
            covered = False
            qh = 0
            while t < tg:
                t1 = min(t + step, tg)
                mx = ox + dx * (t + t1) * 0.5
                my = oy + dy * (t + t1) * 0.5
                gcx = int((mx - AREA_X[0]) / cell)
                gcy = int((my - AREA_Y[0]) / cell)
                for jy in range(gcy - 1, gcy + 2):
                    if jy < 0 or jy >= ny:
                        continue
                    for jx in range(gcx - 1, gcx + 2):
                        if jx < 0 or jx >= nx:
                            continue
                        c = jy * nx + jx
                        for s in range(start[c], start[c + 1]):
                            i = order[s]
                            for q in range(quads):
                                a = yaw0[i] + q * math.pi / quads
                                ax, ay = math.cos(a), math.sin(a)
                                # plane through (cx,cy) with normal (-ay, ax)
                                denom = dx * (-ay) + dy * ax
                                if abs(denom) < 1e-6:
                                    continue
                                tt = ((cx[i] - ox) * (-ay) + (cy[i] - oy) * ax) / denom
                                if tt < t or tt >= t1:
                                    continue  # only count in the step that owns the hit: no duplicates
                                z = oz + dz * tt
                                sc = scale[i]
                                if z < 0.0 or z > h * sc:
                                    continue
                                hx = ox + dx * tt - cx[i]
                                hy = oy + dy * tt - cy[i]
                                u = (hx * ax + hy * ay) / (w * sc) + 0.5
                                if u < 0.0 or u > 1.0:
                                    continue
                                qh += 1
                                if not covered and blade_alpha(u, z / (h * sc), blades, seed + i * 7 + q):
                                    covered = True
                t = t1
            out_c[py * W + px] = 1 if covered else 0
            out_q[py * W + px] = qh
    return out_d, out_c, out_q


def make_instances(rng, dens, copies=1, radius=0.0):
    """dens instances/yd2 uniform; each base instance optionally gets (copies-1) extra copies within `radius` yards
    (the 'multiply native instances with an offset' scheme). radius<=0 with copies>1 = independent uniform."""
    ax = AREA_X[1] - AREA_X[0]
    ay = AREA_Y[1] - AREA_Y[0]
    n_base = int(dens * ax * ay)
    bx = rng.uniform(*AREA_X, n_base)
    by = rng.uniform(*AREA_Y, n_base)
    xs, ys = [bx], [by]
    for _ in range(copies - 1):
        if radius > 0:
            r = radius * np.sqrt(rng.uniform(0, 1, n_base))
            th = rng.uniform(0, 2 * np.pi, n_base)
            xs.append(bx + r * np.cos(th)); ys.append(by + r * np.sin(th))
        else:
            xs.append(rng.uniform(*AREA_X, n_base)); ys.append(rng.uniform(*AREA_Y, n_base))
    cx = np.concatenate(xs); cy = np.concatenate(ys)
    yaw = rng.uniform(0, np.pi, cx.shape[0])
    scale = rng.uniform(0.75, 1.3, cx.shape[0])
    return cx, cy, yaw, scale


def run(cfg, pitch_deg, eye, fov_deg, W=192, H=108, seed=1):
    rng = np.random.default_rng(seed)
    cx, cy, yaw, scale = make_instances(rng, cfg["dens"], cfg.get("copies", 1), cfg.get("radius", 0.0))
    cell = 1.0
    nx = int((AREA_X[1] - AREA_X[0]) / cell) + 1
    ny = int((AREA_Y[1] - AREA_Y[0]) / cell) + 1
    start, order = build_grid(cx, cy, cell, nx, ny)
    d, c, q = trace(cx, cy, yaw, scale, cfg["quads"], cfg["w"], cfg["h"], cfg["blades"], seed, start, order, cell, nx, ny,
                    eye, math.radians(pitch_deg), math.radians(fov_deg), W, H)
    res = []
    for lo, hi in BANDS:
        m = (d >= lo) & (d < hi)
        res.append((c[m].mean() if m.any() else float("nan"), q[m].mean() if m.any() else float("nan")))
    area = (AREA_X[1] - AREA_X[0]) * (AREA_Y[1] - AREA_Y[0])
    inst_per_yd2 = cx.shape[0] / area
    return inst_per_yd2, inst_per_yd2 * cfg["quads"] * 4, res


CARDS = {
    # name: quads, width, height, blades per quad.  Dimensions are ASSUMPTIONS for the sweep, not measured vanilla values.
    "tuft1  (1 quad, 4 blades, 0.6x0.5)": dict(quads=1, w=0.6, h=0.5, blades=4),
    "tuftX  (2 quads, 4 blades, 0.6x0.5)": dict(quads=2, w=0.6, h=0.5, blades=4),
    "clump  (3 quads, 7 blades, 0.9x0.6)": dict(quads=3, w=0.9, h=0.6, blades=7),
    "wide   (3 quads, 9 blades, 1.4x0.7)": dict(quads=3, w=1.4, h=0.7, blades=9),
}
DENS = [0.25, 0.5, 1, 2, 4, 8]


def fmt(x):
    return "  -  " if x != x else f"{100 * x:5.1f}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pitch", type=float, default=12.0, help="camera pitch below horizontal, degrees")
    ap.add_argument("--eye", type=float, default=3.0, help="camera height above ground, yards (third person)")
    ap.add_argument("--fov", type=float, default=60.0)
    ap.add_argument("--out", default="")
    a = ap.parse_args()
    L = []
    P = L.append
    P(f"### Coverage sweep: pitch {a.pitch} deg, eye {a.eye} yd, vFOV {a.fov} deg\n")
    P("Columns: instances/yd2 | verts/yd2 | % ground hidden (0-15 / 15-40 / 40-60 yd) | alpha-tested quad hits per ray (same bands)\n")
    P("| card | inst/yd2 | verts/yd2 | cover 0-15 | cover 15-40 | cover 40-60 | hits 0-15 | hits 15-40 | hits 40-60 |")
    P("|---|---|---|---|---|---|---|---|---|")
    for name, c in CARDS.items():
        for d in DENS:
            inst, verts, r = run(dict(c, dens=d), a.pitch, a.eye, a.fov)
            P(f"| {name} | {inst:.2f} | {verts:.1f} | " + " | ".join(fmt(x[0]) for x in r) + " | " +
              " | ".join(f"{x[1]:.2f}" for x in r) + " |")
        P("| | | | | | | | | |")
    P("\n### Independent density vs 'native instances + offset copies' (clump card, base 1 inst/yd2)\n")
    P("| scheme | inst/yd2 | cover 0-15 | cover 15-40 | cover 40-60 |")
    P("|---|---|---|---|---|")
    clump = CARDS["clump  (3 quads, 7 blades, 0.9x0.6)"]
    for label, kw in [("native only (x1)", dict(copies=1)),
                      ("x4 copies, offset r=0.4 yd", dict(copies=4, radius=0.4)),
                      ("x4 copies, offset r=1.0 yd", dict(copies=4, radius=1.0)),
                      ("x4 copies, offset r=2.0 yd", dict(copies=4, radius=2.0)),
                      ("x4 independent (true 4 inst/yd2)", dict(copies=4, radius=0.0))]:
        inst, verts, r = run(dict(clump, dens=1.0, **kw), a.pitch, a.eye, a.fov)
        P(f"| {label} | {inst:.1f} | " + " | ".join(fmt(x[0]) for x in r) + " |")
    txt = "\n".join(L)
    print(txt)
    if a.out:
        open(a.out, "w", encoding="utf-8").write(txt + "\n")


if __name__ == "__main__":
    main()
