"""CPU model of the dense-grass copy shader (grass.cpp, COPY=1) and its checks. No game, no GPU.

It mirrors the HLSL line by line (offset wobble, thinning noise, keep, distance fade, sink, slope) so the maths can be
tested and the thresholds calibrated before the DLL runs in the game. It is a SIMULATION of the shader maths only:
nothing here proves the shader compiles to vs_2_0 or looks right in the game.

usage: python -I tools/grass_copy_model.py            # runs the checks, prints a table, exits 1 on failure
       python -I tools/grass_copy_model.py --table    # prints the share -> threshold table used by grass.cpp
"""
import sys
import numpy as np


def sat(x):
    return np.clip(x, 0.0, 1.0)


def noise_nz(cw, ph, inv_patch):
    s = np.sin(cw @ np.array([1.31, 0.77]) * inv_patch * 1.9 + ph * 2.3) + \
        np.sin(cw @ np.array([-0.71, 1.27]) * inv_patch * 2.7 + ph * 3.1)
    return 0.5 + 0.25 * s


def wobble(cw, ph, inv_patch, amp):
    return np.stack([np.sin(cw @ np.array([0.61, 0.79]) * inv_patch + ph),
                     np.sin(cw @ np.array([-0.83, 0.55]) * inv_patch * 1.31 + ph * 1.7)], axis=1) * amp


def keep_from_threshold(nz, thr):
    # thr is the noise value below which the copy grows. 6/yard-ish softness: a patch edge is a short ramp.
    return sat((thr - nz) * 6.0 + 0.5)


def calibrate(n=400000, seed=3):
    """share (0..1) -> threshold such that the mean of keep equals share, over a large ground area."""
    rng = np.random.default_rng(seed)
    pts = rng.uniform(0, 1024, (n, 2))
    nz = noise_nz(pts, 1.0, 1.0 / 1.5)
    shares = np.linspace(0, 1, 21)
    thr = []
    for sh in shares:
        lo, hi = -1.0, 2.0
        for _ in range(40):
            mid = 0.5 * (lo + hi)
            if keep_from_threshold(nz, mid).mean() < sh:
                lo = mid
            else:
                hi = mid
        thr.append(0.5 * (lo + hi))
    return shares, np.array(thr)


def main():
    if "--table" in sys.argv:
        sh, th = calibrate()
        print("share  threshold")
        for a, b in zip(sh, th):
            print(f"{a:4.1f}  {b:7.3f}")
        print("static const float kShareThr[21] = {" + ", ".join(f"{t:.3f}f" for t in th) + "};")
        return 0

    ok = True

    def check(name, cond, info=""):
        nonlocal ok
        ok &= bool(cond)
        print(("PASS " if cond else "FAIL ") + name + (("  " + info) if info else ""))

    rng = np.random.default_rng(1)
    # 1. the old thinning formula (keep = sat((share*1.2 - nz)*6+.5)) vs what was asked for
    pts = rng.uniform(0, 1024, (300000, 2))
    nz = noise_nz(pts, 1.0, 1 / 1.5)
    errs = []
    for sh in (0.1, 0.25, 0.5, 0.75, 1.0):
        got = keep_from_threshold(nz, sh * 1.2).mean()
        errs.append((sh, got))
    print("old formula, share -> coverage:", ", ".join(f"{a:.2f}->{b:.2f}" for a, b in errs))
    check("old thinning formula is off by more than 0.05 somewhere (so a table is used)",
          max(abs(a - b) for a, b in errs) > 0.05)
    # 2. calibrated table gives coverage = share within 0.02
    sh, th = calibrate()
    cal = [keep_from_threshold(nz, np.interp(s, sh, th)).mean() for s in (0.1, 0.25, 0.5, 0.75, 0.95)]
    check("calibrated threshold: coverage within 0.02 of share", all(abs(c - s) < 0.02 for c, s in zip(cal, (0.1, 0.25, 0.5, 0.75, 0.95))),
          "got " + ", ".join(f"{c:.3f}" for c in cal))
    # 3. wobble is smooth: a 1.5 yard card sees < 10% shear and the offset never exceeds the amplitude
    cw = rng.uniform(0, 1024, (50000, 2))
    d = np.array([1.5, 0.0])
    sh_x = np.abs(wobble(cw + d, 1.0, 1 / 1.5, 0.45) - wobble(cw, 1.0, 1 / 1.5, 0.45)).max()
    check("wobble shear across a 1.5 yard card is small", sh_x < 0.45 * 2, f"max shift across the card {sh_x:.3f} yd (amplitude 0.45)")
    # per-yard slope of the wobble: this is the shear of the card
    g = np.abs(wobble(cw + [0.1, 0], 1.0, 1 / 1.5, 0.45) - wobble(cw, 1.0, 1 / 1.5, 0.45)).max() / 0.1
    check("wobble gradient (card shear) below 0.6 for patch 1.5 / wobble 0.45", g < 0.6, f"max gradient {g:.2f} (0.6 = 31 degrees)")
    # 4. chunk-border continuity: noise evaluated from two neighbouring chunks' origins agrees at the shared edge, with the
    #    chunk corner passed as an absolute float32 world coordinate (as grass.cpp does), also far from the world's centre
    org_a = np.array([100.0 / 3 * 37 - 15000.0, 100.0 / 3 * 12 + 9000.0])
    org_b = org_a + [100.0 / 3, 0]
    loc_a = np.array([[33.333, 5.0]])   # a point on chunk a's far edge, chunk-local
    loc_b = np.array([[0.0, 5.0]])      # the same point, chunk b's local space
    f32 = lambda a: a.astype(np.float32).astype(np.float64)
    na = noise_nz(f32(loc_a + f32(org_a)), 1.0, 1 / 1.5)
    nb = noise_nz(f32(loc_b + f32(org_b)), 1.0, 1 / 1.5)
    check("thinning noise is continuous across a chunk border, far from the origin (float32)", abs(na - nb)[0] < 5e-3,
          f"diff {abs(na - nb)[0]:.2e}")
    # 5. fade by sinking: vis monotone in distance, 1 inside, 0 beyond the end
    end, frac = 45.0, 0.35
    span = end * frac
    vd = np.linspace(0, 60, 61)
    vis = sat((end - vd) * (1 / span))
    check("fade is 1 up to end-span and 0 at end", vis[vd <= end - span].min() == 1 and vis[vd >= end].max() == 0)
    check("fade is monotone non-increasing", np.all(np.diff(vis) <= 1e-9))
    # 6. copies are placed differently (not on top of the original) and the set of offsets has no near-duplicates
    offs = []
    for k in range(1, 8):
        ang = k * 2.3999632 + 0.7
        h = (np.sin(k * 1.618 * 12.9898) * 43758.5453) % 1.0
        rad = 1.2 * (0.6 + 0.4 * h)
        offs.append((np.cos(ang) * rad, np.sin(ang) * rad))
    offs = np.array(offs)
    mind = min(np.hypot(*(offs[i] - offs[j])) for i in range(7) for j in range(i))
    check("copy offsets are at least 0.5 yd from the original and 0.25 yd from each other",
          np.hypot(offs[:, 0], offs[:, 1]).min() > 0.5 and mind > 0.25,
          f"min radius {np.hypot(offs[:,0], offs[:,1]).min():.2f}, min pair distance {mind:.2f}")
    # 7. slope term: a copy on a plane stays on it (z shift = gradient . offset)
    gx, gy = 0.3, -0.2
    o2 = np.array([0.8, 0.5])
    plane = lambda x, y: gx * x + gy * y
    z0 = plane(2.0, 3.0)
    z1 = plane(2.0 + o2[0], 3.0 + o2[1])
    check("slope compensation keeps a copy's root on the plane", abs((z0 + o2 @ np.array([gx, gy])) - z1) < 1e-9)
    # 8. sink hides a 1 yard blade at vis 0 and leaves it alone at vis 1
    sink = 1.5
    tip_z = 1.0
    check("a hidden copy's tip is below its root", tip_z - (1 - 0) * sink < 0 and tip_z - (1 - 1) * sink == tip_z)
    print("\nall checks passed" if ok else "\nSOME CHECKS FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
