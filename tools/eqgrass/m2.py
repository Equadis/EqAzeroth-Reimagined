"""Vanilla M2 (MD20, version 256/257) reader and a conservative geometry editor for ground-clutter (detail doodad) models.

Offsets used here are the ones ComfyAtmosphere's mapm2.cpp reads from real 1.12 files (in-game verified there):
  header: +0x44 nVertices, +0x48 ofsVertices, +0x4C nViews, +0x50 ofsViews, +0x5C nTextures/+0x60 ofs
  vertex: 48 bytes: pos f3 (+0), bone weights 4B (+12), bone indices 4B (+16), normal f3 (+20), uv0 f2 (+32), uv1 f2 (+40)
  view (44 bytes): +0 nIndices +4 ofsIndices (u16 view-local -> vertex), +8 nTriangles(indices) +12 ofsTriangles (u16 -> local),
                   +24 nSubmeshes +28 ofs, +32 nTextureUnits +36 ofs
  submesh (32 bytes): +2 level (high start bits), +4 startVertex, +6 nVertices, +8 startTriangle, +10 nTriangles
  texture (16 bytes): +0 type, +8 name length, +12 name offset
Anything else in the file (bones, animations, bounds) is left byte for byte as it was. The edit APPENDS new arrays and
repoints the header and the view: the old arrays stay in the file as unreferenced bytes.
Not verified against a real file by this code (no assets here); tests/test_eqgrass.py checks it on synthetic M2s that
follow the layout above.
"""
import math
import struct


class M2Error(Exception):
    pass


def u32(d, o):
    return struct.unpack_from("<I", d, o)[0]


class M2:
    def __init__(self, data):
        d = bytes(data)
        if len(d) < 0x100 or d[:4] != b"MD20":
            raise M2Error("not an MD20 file")
        self.version = u32(d, 4)
        if self.version not in (256, 257):
            raise M2Error(f"version {self.version} (only the vanilla 256/257 layout is handled)")
        self.d = d
        self.n_vert, self.ofs_vert = u32(d, 0x44), u32(d, 0x48)
        self.n_views, self.ofs_views = u32(d, 0x4C), u32(d, 0x50)
        if not self.n_views or self.ofs_views + 44 > len(d) or self.ofs_vert + self.n_vert * 48 > len(d):
            raise M2Error("no view, or vertices out of range")
        v = self.ofs_views
        self.n_index, self.ofs_index = u32(d, v), u32(d, v + 4)
        self.n_tris, self.ofs_tris = u32(d, v + 8), u32(d, v + 12)
        self.n_sub, self.ofs_sub = u32(d, v + 24), u32(d, v + 28)
        self.n_units, self.ofs_units = u32(d, v + 32), u32(d, v + 36)
        if self.n_tris % 3 or self.ofs_index + self.n_index * 2 > len(d) or self.ofs_tris + self.n_tris * 2 > len(d):
            raise M2Error("index arrays out of range")
        self.verts = [list(struct.unpack_from("<3f4B4B3f2f2f", d, self.ofs_vert + i * 48)) for i in range(self.n_vert)]
        self.index = list(struct.unpack_from(f"<{self.n_index}H", d, self.ofs_index))
        self.tris = list(struct.unpack_from(f"<{self.n_tris}H", d, self.ofs_tris))
        if any(g >= self.n_vert for g in self.index) or any(t >= self.n_index for t in self.tris):
            raise M2Error("an index points outside its array")

    # -- facts -----------------------------------------------------------------------------------------------
    def used_vertices(self):
        return sorted({self.index[t] for t in self.tris})

    def bounds(self):
        used = self.used_vertices()
        xs = [self.verts[i][0] for i in used]; ys = [self.verts[i][1] for i in used]; zs = [self.verts[i][2] for i in used]
        return (min(xs), min(ys), min(zs)), (max(xs), max(ys), max(zs))

    def top(self):
        return self.bounds()[1][2]

    def width(self):
        lo, hi = self.bounds()
        return max(hi[0] - lo[0], hi[1] - lo[1])

    def texture_names(self):
        d, out = self.d, []
        n, o = u32(d, 0x5C), u32(d, 0x60)
        for i in range(n):
            t = o + i * 16
            if t + 16 > len(d):
                break
            if u32(d, t) == 0:
                ln, no = u32(d, t + 8), u32(d, t + 12)
                if no + ln <= len(d):
                    out.append(d[no:no + ln].split(b"\0")[0].decode("latin-1"))
        return out

    def summary(self):
        lo, hi = self.bounds()
        return dict(verts=len(self.used_vertices()), tris=self.n_tris // 3, submeshes=self.n_sub, units=self.n_units,
                    top=round(hi[2], 3), width=round(max(hi[0] - lo[0], hi[1] - lo[1]), 3), textures=self.texture_names())

    # -- editing ---------------------------------------------------------------------------------------------
    def edit(self, widen=1.0, height=1.0, copies=0, rot_step_deg=60.0):
        """Return new file bytes. widen scales x,y about the model origin; height scales z; `copies` extra copies of the
        geometry are added, copy k turned about z by k * rot_step_deg (a crossed clump). Normals turn with them.
        Vertex tuple layout: pos 0..2, bone weights 3..6, bone indices 7..10, normal 11..13, uv0 14..15, uv1 16..17."""
        if self.n_sub != 1:
            raise M2Error(f"{self.n_sub} submeshes: only a single-submesh model is edited (the copies would need their own)")
        total = copies + 1
        if self.n_vert * total > 65535 or self.n_index * total > 65535 or self.n_tris * total > 65535:
            raise M2Error("too many vertices, indices or triangles for 16-bit fields")
        base = [list(v) for v in self.verts]
        for v in base:
            v[0] *= widen
            v[1] *= widen
            v[2] *= height
        new_verts = [list(v) for v in base]
        new_index, new_tris = list(self.index), list(self.tris)
        for k in range(1, copies + 1):
            a = math.radians(rot_step_deg * k)
            c, s = math.cos(a), math.sin(a)
            for v in base:
                w = list(v)
                w[0], w[1] = v[0] * c - v[1] * s, v[0] * s + v[1] * c
                w[11], w[12] = v[11] * c - v[12] * s, v[11] * s + v[12] * c
                new_verts.append(w)
            new_index += [g + k * self.n_vert for g in self.index]
            new_tris += [t + k * self.n_index for t in self.tris]
        out = bytearray(self.d)

        def align():
            while len(out) % 16:
                out.append(0)

        align()
        ofs_v = len(out)
        for v in new_verts:
            out += struct.pack("<3f4B4B3f2f2f", *v)
        align()
        ofs_i = len(out)
        out += struct.pack(f"<{len(new_index)}H", *new_index)
        align()
        ofs_t = len(out)
        out += struct.pack(f"<{len(new_tris)}H", *new_tris)
        struct.pack_into("<II", out, 0x44, len(new_verts), ofs_v)
        struct.pack_into("<IIII", out, self.ofs_views, len(new_index), ofs_i, len(new_tris), ofs_t)
        # the one submesh now covers everything: level 0, starts at 0 (the copies sit after the original vertices)
        so = self.ofs_sub
        struct.pack_into("<H", out, so + 2, 0)
        struct.pack_into("<HHHH", out, so + 4, 0, len(new_verts), 0, len(new_tris))
        return bytes(out)
