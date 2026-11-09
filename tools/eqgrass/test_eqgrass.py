"""Tests for the eqgrass asset tools. Everything is synthetic: no game files are needed, and a pass does NOT prove the tools
work on the real client's archives (the layouts follow ComfyAtmosphere's in-game-verified readers, see m2.py / mpq.py).
run: python -I tools/eqgrass/test_eqgrass.py
"""
import os
import struct
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import mpq                  # noqa: E402
from dbc import Dbc         # noqa: E402
from m2 import M2, M2Error  # noqa: E402


def make_m2(width=0.6, height=0.5, quads=1, name="World\\NoDXT\\Detail\\Test.blp", submeshes=1):
    """A vanilla-layout M2 with `quads` crossed vertical cards: header, vertices, view, submesh, unit, texture, name."""
    verts, index, tris = [], [], []
    for q in range(quads):
        a = q * 3.14159265 / quads
        c, s = 0.0, 0.0
        import math
        c, s = math.cos(a), math.sin(a)
        base = len(verts)
        for (x, z, u, v) in ((-width / 2, 0, 0, 1), (width / 2, 0, 1, 1), (-width / 2, height, 0, 0), (width / 2, height, 1, 0)):
            verts.append((x * c, x * s, z, 255, 0, 0, 0, 0, 0, 0, 0, -s, c, 0.0, u, v, 0.0, 0.0))
        index += [base, base + 1, base + 2, base + 3]
        tris += [4 * q, 4 * q + 1, 4 * q + 2, 4 * q + 2, 4 * q + 1, 4 * q + 3]
    hdr = bytearray(0x130)
    hdr[0:4] = b"MD20"
    struct.pack_into("<I", hdr, 4, 256)
    body = bytearray()
    off = len(hdr)

    def put(b):
        nonlocal off
        while (off + len(body)) % 16:
            body.append(0)
        pos = off + len(body)
        body.extend(b)
        return pos

    ov = put(b"".join(struct.pack("<3f4B4B3f2f2f", *v) for v in verts))
    oi = put(struct.pack(f"<{len(index)}H", *index))
    ot = put(struct.pack(f"<{len(tris)}H", *tris))
    subs = b"".join(struct.pack("<HHHHHH", 0, 0, 0, len(verts), 0, len(tris)) + bytes(20) for _ in range(submeshes))
    osub = put(subs)
    ounit = put(bytes(24))
    oname = put(name.encode() + b"\0")
    otex = put(struct.pack("<IIII", 0, 0, len(name) + 1, oname))
    view = struct.pack("<IIIIIIIIII", len(index), oi, len(tris), ot, 0, 0, submeshes, osub, 1, ounit) + bytes(4)
    oview = put(view)
    struct.pack_into("<II", hdr, 0x44, len(verts), ov)
    struct.pack_into("<II", hdr, 0x4C, 1, oview)
    struct.pack_into("<II", hdr, 0x5C, 1, otex)
    return bytes(hdr) + bytes(body)


def make_dbc(rows, strings=b"\0"):
    nf = len(rows[0])
    out = b"WDBC" + struct.pack("<IIII", len(rows), nf, nf * 4, len(strings))
    for r in rows:
        out += struct.pack(f"<{nf}I", *r)
    return out + strings


class MpqTests(unittest.TestCase):
    def test_hash_known_values(self):
        # the well-known keys of the two tables
        self.assertEqual(mpq.hash_string("(hash table)", 3), 0xC3AF3770)
        self.assertEqual(mpq.hash_string("(block table)", 3), 0xEC83B3A3)

    def test_roundtrip_and_stack_priority(self):
        with tempfile.TemporaryDirectory() as t:
            big = os.urandom(300) + b"a" * 20000 + os.urandom(100)
            mpq.write_archive(os.path.join(t, "patch.MPQ"), {"A\\one.txt": b"base", "A\\big.bin": big, "A\\empty": b""})
            mpq.write_archive(os.path.join(t, "patch-Y.mpq"), {"A\\one.txt": b"override"})
            mpq.write_archive(os.path.join(t, "patch-2.mpq"), {"A\\one.txt": b"two", "A\\only2": b"x"})
            st = mpq.Stack(t)
            self.assertEqual([os.path.basename(a.path) for a in st.archives], ["patch-Y.mpq", "patch-2.mpq", "patch.MPQ"])
            self.assertEqual(st.read("a/ONE.TXT"), b"override")          # case and slash insensitive, best archive wins
            self.assertEqual(st.read("A\\big.bin"), big)
            self.assertEqual(st.read("A\\only2"), b"x")
            self.assertEqual(st.read("A\\missing"), None)
            self.assertEqual(st.holders("A\\one.txt"), ["patch-Y.mpq", "patch-2.mpq", "patch.MPQ"])
            st.close()


class DbcTests(unittest.TestCase):
    def test_roundtrip(self):
        b = make_dbc([[1, 2, 3], [4, 5, 6]], b"\0abc\0")
        d = Dbc(b)
        self.assertEqual(d.rows[1], [4, 5, 6])
        self.assertEqual(d.to_bytes(), b)
        self.assertEqual(d.string(1), "abc")


class M2Tests(unittest.TestCase):
    def test_parse(self):
        m = M2(make_m2(0.6, 0.5, quads=2))
        s = m.summary()
        self.assertEqual((s["verts"], s["tris"], s["submeshes"]), (8, 4, 1))
        self.assertAlmostEqual(s["top"], 0.5, 3)
        self.assertEqual(s["textures"], ["World\\NoDXT\\Detail\\Test.blp"])

    def test_edit_widen_height_copies(self):
        raw = make_m2(0.6, 0.5, quads=1)
        m = M2(raw)
        new = M2(m.edit(widen=1.5, height=1.2, copies=2, rot_step_deg=60))
        s = new.summary()
        self.assertEqual((s["verts"], s["tris"]), (12, 6))                 # 3 clumps of one card
        self.assertAlmostEqual(s["top"], 0.6, 3)
        self.assertAlmostEqual(s["width"], 0.6 * 1.5, 2)                   # the unrotated card is the widest along x
        # triangles stay valid and each copy's triangles refer to its own vertices
        for k in range(3):
            tri = new.tris[k * 6:(k + 1) * 6]
            self.assertTrue(all(k * 4 <= new.index[t] < (k + 1) * 4 for t in tri))
        # the copy turned by 60 degrees: its first vertex is the original rotated
        import math
        v0, v1 = new.verts[0], new.verts[4]
        a = math.radians(60)
        self.assertAlmostEqual(v1[0], v0[0] * math.cos(a) - v0[1] * math.sin(a), 4)
        self.assertAlmostEqual(v1[1], v0[0] * math.sin(a) + v0[1] * math.cos(a), 4)
        # normals turn with the copy and keep their length
        n = new.verts[4]
        self.assertAlmostEqual(math.hypot(n[11], n[12]), 1.0, 4)
        # everything outside the fields the edit owns is byte-identical (the original arrays stay where they were)
        out = m.edit(widen=1.5, height=1.2, copies=2)
        for lo, hi in ((0, 0x44), (0x4C, len(raw))):
            seg_old, seg_new = raw[lo:hi], out[lo:hi]
            diff = [i for i in range(len(seg_old)) if seg_old[i] != seg_new[i]]
            # only the view (counts, offsets) and the submesh fields may differ in this range
            for i in diff:
                p = lo + i
                self.assertTrue(m.ofs_views <= p < m.ofs_views + 16 or m.ofs_sub <= p < m.ofs_sub + 32 or
                                m.ofs_vert <= p < m.ofs_vert + m.n_vert * 48 or m.ofs_index <= p < m.ofs_index + 2 * m.n_index
                                or m.ofs_tris <= p < m.ofs_tris + 2 * m.n_tris, f"unexpected change at 0x{p:X}")

    def test_refusals(self):
        with self.assertRaises(M2Error):
            M2(make_m2(submeshes=2)).edit(copies=1)
        with self.assertRaises(M2Error):
            M2(make_m2(quads=2)).edit(copies=20000)
        with self.assertRaises(M2Error):
            M2(b"MD21" + bytes(0x200))


class WorkflowTest(unittest.TestCase):
    def test_inspect_build_pack(self):
        with tempfile.TemporaryDirectory() as t:
            data = os.path.join(t, "Data")
            os.makedirs(data)
            names = [("BadGra01", 0.5), ("BadGra02", 0.2), ("BadRoc01", 0.6), ("ElwFlo01", 0.5), ("ElwGra09", 0.45)]
            strings, offs = b"\0", {}
            for n, _ in names:
                offs[n] = len(strings)
                strings += (n + ".mdx").encode() + b"\0"
            doo = make_dbc([[i + 1, offs[n], 0] for i, (n, _) in enumerate(names)], strings)
            tex = make_dbc([[1, 1, 2, 5, 0, 30, 30, 20, 0, 1], [2, 4, 3, 0, 0, 50, 50, 0, 0, 2]])
            files = {"DBFilesClient\\GroundEffectTexture.dbc": tex, "DBFilesClient\\GroundEffectDoodad.dbc": doo}
            for n, h in names:
                files[f"World\\NoDXT\\Detail\\{n}.m2"] = make_m2(0.6, h, quads=2)
            mpq.write_archive(os.path.join(data, "patch.MPQ"), files)
            # an override archive replaces one model (BadGra01 taller), as patch-Y.mpq would
            mpq.write_archive(os.path.join(data, "patch-Y.mpq"), {"World\\NoDXT\\Detail\\BadGra01.m2": make_m2(0.6, 0.8, quads=2)})
            out = os.path.join(t, "out")
            run = lambda *a: subprocess.run([sys.executable, "-I", os.path.join(HERE, "eqgrass.py"), *a], capture_output=True, text=True)
            r = run("inspect", "--data", data, "--out", out)
            self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
            self.assertIn("5 models found, 2 classified as grass", r.stdout)       # Gra01 and Gra09; Gra02 too low, Roc, Flo excluded
            with open(os.path.join(out, "models.csv")) as f:
                csv_txt = f.read()
            self.assertIn("patch-Y.mpq+patch.MPQ", csv_txt)                         # the override is what is read first
            r = run("build", "--data", data, "--out", out, "--widen", "1.4", "--copies", "1", "--apply")
            self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
            built = os.path.join(out, "build", "World", "NoDXT", "Detail")
            self.assertEqual(sorted(os.listdir(built)), ["BadGra01.m2", "ElwGra09.m2"])
            rd = lambda n: open(os.path.join(built, n), "rb").read()
            self.assertAlmostEqual(M2(rd("BadGra01.m2")).top(), 0.8, 3)   # built on the override
            self.assertEqual(M2(rd("ElwGra09.m2")).summary()["verts"], 16)
            arch = os.path.join(out, "patch-Y.mpq")
            r = run("pack", "--out", out, "--archive", arch)
            self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
            a = mpq.Archive(arch)
            self.assertEqual(M2(a.read("World\\NoDXT\\Detail\\ElwGra09.m2")).summary()["verts"], 16)
            a.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
