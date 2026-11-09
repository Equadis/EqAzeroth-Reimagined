"""EqGrass asset workflow: inspect the game's ground clutter, rebuild the grass models wider/denser, pack an MPQ.

  python -I tools/eqgrass/eqgrass.py archives --data D:\\Octowow\\Data
  python -I tools/eqgrass/eqgrass.py inspect --data D:\\Octowow\\Data --out grass_out --zip
  python -I tools/eqgrass/eqgrass.py build   --data D:\\Octowow\\Data --out grass_out [--widen 1.35] [--copies 1] [--apply]
  python -I tools/eqgrass/eqgrass.py pack    --out grass_out --archive grass_out\\patch-Y.mpq

inspect: reads GroundEffectTexture.dbc and GroundEffectDoodad.dbc through the archive stack (so what the game really loads,
         patch-Y.mpq included), resolves each doodad's M2, and writes grass_out/models.csv and grass_out/original/ (DBCs,
         M2s, BLPs, PNG previews when Pillow is installed). Changes nothing.
build:   without --apply, prints the plan only. With --apply, writes edited M2s to grass_out/build/<archive path>.
pack:    packs grass_out/build into an MPQ (see docs/GRASS_WORKFLOW.md for the load order).
Nothing here touches the game folder.
"""
import argparse
import csv
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import mpq as mpqmod          # noqa: E402
from dbc import Dbc           # noqa: E402
from m2 import M2, M2Error    # noqa: E402

DBC_TEX = "DBFilesClient\\GroundEffectTexture.dbc"
DBC_DOO = "DBFilesClient\\GroundEffectDoodad.dbc"
CANDIDATE_DIRS = ["", "World\\NoDXT\\Detail\\", "World\\Detail\\", "World\\Generic\\Detail\\", "World\\NoDXT\\detail\\"]
DEFAULT_INCLUDE = r"gra"                       # a model whose file name holds this is a grass candidate
DEFAULT_EXCLUDE = r"roc|bon|she|peb|mus|flo|sto|bru"   # rocks, bones, shells, pebbles, mushrooms, flowers, stones, brush
MIN_GRASS_TOP = 0.31                           # yards: Comfy measured every rock/shell/low tuft below 0.3 and grass from 0.31


def leaf(path):
    """File name of an archive path (backslashes) on any OS."""
    return path.replace("\\", "/").split("/")[-1]


def find_columns(tex, doo):
    """Detect the columns by content, not by position, and refuse if it does not add up."""
    name_col = next((c for c in range(1, doo.fields) if doo.is_string_field(c)), None)
    if name_col is None:
        raise SystemExit("GroundEffectDoodad.dbc: no column holds file names; dump it with 'inspect --dump-dbc' and report the layout")
    ids = {r[0] for r in doo.rows}
    cols = [c for c in range(1, tex.fields)
            if all(r[c] == 0 or r[c] in ids for r in tex.rows) and any(r[c] for r in tex.rows)]
    if len(cols) < 4:
        raise SystemExit(f"GroundEffectTexture.dbc: expected 4 doodad-id columns, found {cols}")
    return name_col, cols[:4]


def resolve(stack, raw_name):
    """The model's archive path: as named if it has a directory, else under the usual detail-doodad folders (unverified:
    the tiles say .mdx, the archives hold .m2, as mapm2.cpp does)."""
    base = re.sub(r"\.(mdx|mdl)$", ".m2", raw_name.replace("/", "\\"), flags=re.I)
    if not base.lower().endswith(".m2"):
        base += ".m2"
    leaf = base.split("\\")[-1]
    tried = ([base] if "\\" in base else []) + [d + leaf for d in CANDIDATE_DIRS if d]
    for p in tried:
        data = stack.read(p)
        if data is not None:
            return p, data
    return None, None


def load(args):
    if not os.path.isdir(args.data):
        raise SystemExit(f"--data {args.data!r} is not a folder (give the game's Data folder, e.g. D:\\Octowow\\Data)")
    stack = mpqmod.Stack(args.data)
    if not stack.archives:
        raise SystemExit(f"no readable MPQ in {args.data}: {stack.skipped}")
    try:
        tex_b, doo_b = stack.read(DBC_TEX), stack.read(DBC_DOO)
    except mpqmod.MpqError as e:
        raise SystemExit(f"cannot read the DBC: {e}\nNeeds support for this archive's compression: send the archive's name (and which patch holds the DBC) from "
                         f"'python -I tools/eqgrass/eqgrass.py archives --data <Data>'")
    if tex_b is None or doo_b is None:
        raise SystemExit("GroundEffectTexture.dbc / GroundEffectDoodad.dbc not found in any archive")
    return stack, tex_b, doo_b


def models_table(stack, tex_b, doo_b):
    tex, doo = Dbc(tex_b), Dbc(doo_b)
    name_col, id_cols = find_columns(tex, doo)
    used = {}                 # doodad id -> number of ground-effect rows using it
    for r in tex.rows:
        for c in id_cols:
            if r[c]:
                used[r[c]] = used.get(r[c], 0) + 1
    rows = []
    for r in doo.rows:
        raw = doo.string(r[name_col]) or ""
        path, data = resolve(stack, raw)
        info = {}
        if data:
            try:
                info = M2(data).summary()
            except M2Error as e:
                info = {"error": str(e)}
        rows.append(dict(doodad_id=r[0], name=raw, path=path or "", holders="+".join(stack.holders(path)) if path else "",
                         used_by_rows=used.get(r[0], 0), **info, _data=data))
    return tex, doo, rows


def classify(row, include, exclude):
    if "error" in row or not row.get("path"):
        return "skip: unreadable"
    base = leaf(row["name"]).lower()
    if re.search(exclude, base):
        return "skip: excluded name"
    if not re.search(include, base):
        return "skip: not a grass name"
    if row.get("top", 0) < MIN_GRASS_TOP:
        return "skip: lower than %.2f yd" % MIN_GRASS_TOP
    if not row.get("textures"):
        return "skip: no texture"
    if row.get("submeshes") != 1:
        return "skip: %s submeshes" % row.get("submeshes")
    return "grass"


def cmd_archives(a):
    """List the archive stack in load order (highest priority first): what the client would read, and what could not be opened."""
    stack = mpqmod.Stack(a.data)
    print("load order, highest priority first:")
    for x in stack.archives:
        print(f"  {os.path.basename(x.path):20} {os.path.getsize(x.path):>12,d} bytes  holds the two ground-effect DBCs: "
              f"{'yes' if x.has(DBC_TEX) or x.has(DBC_DOO) else 'no'}")
    for n, why in stack.skipped:
        print(f"  NOT READABLE {n}: {why}")
    stack.close()


def write_report(a, stack, tex, doo, rows, name_col, id_cols, textures, missing):
    by_class = {}
    for r in rows:
        by_class.setdefault(r["class"], []).append(r)
    L = ["# EqGrass inspection report", "",
         f"Game Data folder: `{a.data}`", "",
         "## Archives, highest priority first (what the client reads first)", "", "| archive | bytes | holds a ground-effect DBC |", "|---|---|---|"]
    for x in stack.archives:
        L.append(f"| {os.path.basename(x.path)} | {os.path.getsize(x.path):,d} | {'yes' if x.has(DBC_TEX) or x.has(DBC_DOO) else ''} |")
    for n, why in stack.skipped:
        L.append(f"| {n} | | NOT READABLE: {why} |")
    L += ["", "## Ground-effect tables", "",
          f"- GroundEffectTexture.dbc: {len(tex.rows)} rows x {tex.fields} fields; doodad-id columns detected: {id_cols}",
          f"- GroundEffectDoodad.dbc: {len(doo.rows)} rows x {doo.fields} fields; file-name column detected: {name_col}",
          f"- models resolved to an M2: {sum(1 for r in rows if r['path'])} of {len(rows)}; "
          f"texture files read: {len(textures) - len(missing)} of {len(textures)}", "",
          "## Classification (`--include " + a.include + "`, `--exclude " + a.exclude + "`)", "", "| class | models |", "|---|---|"]
    for c, rs in sorted(by_class.items(), key=lambda kv: -len(kv[1])):
        L.append(f"| {c} | {len(rs)} |")
    L += ["", "## Grass candidates (what `build` would edit)", "",
          "| doodad | model path | read from | verts | tris | top (yd) | width (yd) | rows using it | texture |", "|---|---|---|---|---|---|---|---|---|"]
    for r in by_class.get("grass", []):
        L.append(f"| {leaf(r['name'])} | {r['path']} | {r['holders'].split('+')[0]} | {r['verts']} | {r['tris']} | {r['top']} | {r['width']} | "
                 f"{r['used_by_rows']} | {r['textures']} |")
    L += ["", "## Everything else, so the exclusions can be checked", "", "| doodad | class | verts | top (yd) |", "|---|---|---|---|"]
    for r in rows:
        if r["class"] != "grass":
            L.append(f"| {leaf(r['name'])} | {r['class']} | {r.get('verts', '')} | {r.get('top', '')} |")
    if missing:
        L += ["", "## Textures not found in any archive", ""] + [f"- `{m}`" for m in missing]
    L += ["", "## Next", "", "Review `models.csv` (`class` column). Wrong picks: change `--include` / `--exclude` and run `inspect` again."]
    return "\n".join(L) + "\n"


def cmd_inspect(a):
    stack, tex_b, doo_b = load(a)
    out = a.out
    os.makedirs(os.path.join(out, "original", "models"), exist_ok=True)
    tex, doo, rows = models_table(stack, tex_b, doo_b)
    name_col, id_cols = find_columns(tex, doo)
    open(os.path.join(out, "original", "GroundEffectTexture.dbc"), "wb").write(tex_b)
    open(os.path.join(out, "original", "GroundEffectDoodad.dbc"), "wb").write(doo_b)
    cols = ["doodad_id", "name", "path", "holders", "used_by_rows", "verts", "tris", "submeshes", "top", "width", "textures", "error", "class"]
    for r in rows:
        r["class"] = classify(r, a.include, a.exclude)
        r["textures"] = ";".join(r.get("textures", []))
        if r["_data"]:
            open(os.path.join(out, "original", "models", leaf(r["path"])), "wb").write(r["_data"])
    with open(os.path.join(out, "models.csv"), "w", newline="") as f:
        w = csv.DictWriter(f, cols, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    textures = sorted({t for r in rows for t in r["textures"].split(";") if t})
    missing = []
    if a.textures or a.zip:
        os.makedirs(os.path.join(out, "original", "textures"), exist_ok=True)
        for t in textures:
            d = stack.read(t)
            if d is None:
                missing.append(t)
                continue
            fn = os.path.join(out, "original", "textures", t.replace("\\", "__"))
            open(fn, "wb").write(d)
            try:
                from PIL import Image
                import io
                Image.open(io.BytesIO(d)).convert("RGBA").save(fn + ".png")
            except Exception:        # Pillow missing, or a BLP variant it cannot read: the BLP itself is still saved
                pass
    report = write_report(a, stack, tex, doo, rows, name_col, id_cols, textures, missing)
    open(os.path.join(out, "inspection-report.md"), "w", encoding="utf-8").write(report)
    with open(os.path.join(out, "archives.txt"), "w") as f:
        f.write(f"python {sys.version.split()[0]} on {sys.platform}\n")
        for x in stack.archives:
            f.write(f"{os.path.basename(x.path)}\t{os.path.getsize(x.path)}\t{int(os.path.getmtime(x.path))}\n")
        for n, why in stack.skipped:
            f.write(f"NOT READABLE\t{n}\t{why}\n")
    print(report.split("## Ground-effect tables")[1].split("## Classification")[0].strip())
    print(f"\nreport: {os.path.join(out, 'inspection-report.md')}\ncsv:    {os.path.join(out, 'models.csv')}")
    if a.zip:
        import zipfile
        zp = os.path.join(out, "grass_analysis.zip")
        with zipfile.ZipFile(zp, "w", zipfile.ZIP_DEFLATED) as z:
            for fn in ("models.csv", "inspection-report.md", "archives.txt"):
                z.write(os.path.join(out, fn), fn)
            for d, _, names in os.walk(os.path.join(out, "original")):
                for n in names:
                    full = os.path.join(d, n)
                    z.write(full, os.path.relpath(full, out).replace(os.sep, "/"))
        print(f"\nSend this one file: {zp} ({os.path.getsize(zp):,d} bytes: the two DBCs, the ground-effect models and their textures, and the report; "
              f"no other game data)")
    stack.close()


def cmd_build(a):
    stack, tex_b, doo_b = load(a)
    tex, doo, rows = models_table(stack, tex_b, doo_b)
    plan = [r for r in rows if classify(r, a.include, a.exclude) == "grass"]
    print(f"{len(plan)} of {len(rows)} models would be edited: widen x{a.widen}, height x{a.height}, +{a.copies} copies "
          f"turned {a.rot_step} deg each")
    print(f"{'model':40} {'verts':>5} -> {'new':>5}  {'top':>5} -> {'new':>5}  {'width':>5} -> {'new':>5}")
    out_dir = os.path.join(a.out, "build")
    for r in plan:
        m = M2(r["_data"])
        nv = (r["verts"] * (a.copies + 1))
        print(f"{leaf(r['name']):40} {r['verts']:5d} -> {nv:5d}  {r['top']:5.2f} -> {r['top'] * a.height:5.2f}  "
              f"{r['width']:5.2f} -> {r['width'] * a.widen:5.2f}")
        if a.apply:
            dst = os.path.join(out_dir, *r["path"].split("\\"))
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            edited = m.edit(a.widen, a.height, a.copies, a.rot_step)
            M2(edited)       # must parse again
            open(dst, "wb").write(edited)
    if not a.apply:
        print("\ndry run: add --apply to write the edited models to", out_dir)
    stack.close()


def cmd_pack(a):
    root = os.path.join(a.out, "build")
    files = {}
    for d, _, names in os.walk(root):
        for n in names:
            full = os.path.join(d, n)
            files[os.path.relpath(full, root).replace("/", "\\")] = open(full, "rb").read()
    if not files:
        raise SystemExit(f"nothing in {root}: run build --apply first")
    mpqmod.write_archive(a.archive, files)
    print(f"{len(files)} files -> {a.archive}")
    chk = mpqmod.Archive(a.archive)
    for name, data in files.items():
        assert chk.read(name) == data, name
    print("read back and compared: identical")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name, fn in (("archives", cmd_archives), ("inspect", cmd_inspect), ("build", cmd_build), ("pack", cmd_pack)):
        p = sub.add_parser(name)
        p.set_defaults(fn=fn)
        if name != "archives":
            p.add_argument("--out", default="grass_out")
        if name == "archives":
            p.add_argument("--data", required=True)
        if name not in ("pack", "archives"):
            p.add_argument("--data", required=True, help="the game's Data folder")
            p.add_argument("--include", default=DEFAULT_INCLUDE)
            p.add_argument("--exclude", default=DEFAULT_EXCLUDE)
        if name == "inspect":
            p.add_argument("--textures", action="store_true", help="also extract the textures (BLP) and PNG previews")
            p.add_argument("--zip", action="store_true", help="also write grass_analysis.zip (implies --textures): the only file to send")
        if name == "build":
            p.add_argument("--widen", type=float, default=1.35, help="scale x,y (card width), default 1.35")
            p.add_argument("--height", type=float, default=1.0)
            p.add_argument("--copies", type=int, default=1, help="extra copies, each turned by --rot-step")
            p.add_argument("--rot-step", type=float, default=60.0)
            p.add_argument("--apply", action="store_true")
        if name == "pack":
            p.add_argument("--archive", required=True)
    a = ap.parse_args()
    a.fn(a)


if __name__ == "__main__":
    main()
