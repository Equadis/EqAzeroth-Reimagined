# Dense grass: implementation, workflow and validation status (2026-11-09)

Builds on `GRASS_ASSESSMENT.md`. Status words: **implemented** (code exists, checked offline as stated), **simulated** (maths run on a CPU model), **untested** (never run in the game or on real archives). Nothing here has run in WoW.

## 1. What the draw interception really is (read from ComfyAtmosphere v0.17.1-alpha source)

Source: `aloofbit/comfyatmosphere` tag `v0.17.1-alpha` (= commit `73bebaa`; the live DLL's base), `src/grass.cpp`, `NOTES.md` "The grass in the wind".

| Question | Finding |
|---|---|
| What is the draw? | The client (1.12 has no grass shader) fills a **CPU-built dynamic vertex buffer** per map chunk (fill loop at `0x006B2600`, per instance, per vertex) and draws it **fixed-function**: triangle list, stride 36 (pos f3, normal f3, colour, uv f2), no vertex shader, no pixel shader. World matrix = pure translation (chunk corner minus camera); `OnChunkGrid` confirms the corner lies on the 100/3 yard grid. Redridge: 213 draws, 130,516 vertices a frame. |
| Can the geometry be read back? | **No.** The buffer is WRITEONLY, uncached memory (about 20 MB/s reads; three CPU designs failed on it). So any duplication is a GPU re-draw of the same buffer, never a CPU copy. |
| Is there already an interception? | **Yes.** `hkDrawIndexedPrimitive` -> `GrassTake` -> `GrassDraw` swaps in Comfy's own vs_2_0 (wind, parting, FFP lighting and fog re-done in the shader) with the fixed-function pixel pipeline, draws through `GrassCalls` (the device's calls past every hook), then restores the client's constants. Extra draws are made at this one point: **no second hook is needed or added**. |
| Fixed-function compatibility | Already solved by Comfy: D3D9 allows a vertex shader with the FFP pixel stage. Copies reuse the same constants, lighting and fog, so they match the original's shading. A draw with a pixel shader (buildings with the same format) is left out, as before. |
| Does re-drawing give a different placement? | **No, by itself**: the same buffer with the same constants lands pixel-identically. Different placement has to come from the vertex shader. The batch carries **no blade root**, so a copy can only be a *translation* of the whole batch, not a per-blade rotation or scale. |
| Instancing (`SetStreamSourceFreq`) instead of N draws? | Needs a vertex shader (Comfy has one), a custom vertex declaration with a second stream, and new device-call pointers in `GrassCalls`. It would make extra copies cost 0 additional draws. **Not implemented**: DXVK 3.1.1's behaviour for this stream setup is unverified, and one more state-juggling layer should wait until the simple version's draw cost is measured. |
| Draw cost known? | Comfy measured 0.12 ms CPU for the 213 existing grass draws (including its constants). The cost of extra draws is **unmeasured**; `/atmos framelog` ("the grass's CPU a frame, its draws included") reads it directly, because the copies run inside `GrassTake`'s timer. |
| Alpha/depth | Comfy notes the grass is blended **and writes depth over the whole card** (see-through parts too). More overlapping cards may therefore cut holes in each other. The DLL now logs the client's alpha test, blend and depth states at the first grass draw, and `alphaRef` (copies only) enables an alpha test to test the remedy. |

## 2. Dense-grass copies (the GPU side)

**Implemented** as `comfy/0001-dense-grass-copies.patch`, a patch against Comfy `v0.17.1-alpha`. It is a derivative of GPL-3.0 code, so keep the licence. Applies cleanly (`git apply --check` on a pristine tag checkout).

Per grass batch, after the client's own draw (always drawn, untouched), for each copy k within distance:
- a second vertex shader, the **same HLSL text compiled with `COPY=1`** (the wind shader's compile and text are unchanged for the original; if the copy shader fails to compile, no copies are drawn and the log says so);
- offset: a base vector per copy (golden-angle spiral, radius `spread`) plus a smooth wobble (`wobble`, wavelength of `patch` yards), from the **absolute world position** so it does not repeat per chunk;
- thinning: a copy exists in smooth patches (`patch`); fractional `density` (2.5 = one full copy, one half copy) uses a table calibrated so coverage equals the share (simulated, see below);
- tint (warmer/greener) and tip height variation per copy; ground contact: z follows the chunk's slope plane (from `MapGroundHeight`, four samples, cached), roots stay on the plane;
- distance: copy k ends at `densityDist / (1 + 0.35 (k-1))`, fading over `densityFade` of that by **sinking into the ground** (no pop, no alpha cost). Batches whose nearest possible vertex is beyond that distance get no draw at all (CPU cull). Beyond `densityDist` there are no copies: no far grass.
- the wind, the parting and the fog/lighting run unchanged on every copy.

Controls (`[grass]` in `comfyatmos.ini`; `EqShaders/Presets/comfyatmos.ini` has them with `density = 3.0`): `density` (1 = off, nothing extra drawn, the default in code), `densityDist`, `densityFade`, `spread`, `wobble`, `patch`, `heightVar`, `tintVar`, `alphaRef`, `chunkSign`, `copyDebug`. F11 reloads. Disable: `density = 1` or `[grass] enabled = 0`, or restore the previous DLL. In-game sliders for these were **not** added (they need the addon/cvar plumbing).

Checks done here:
| Check | Result |
|---|---|
| HLSL, both variants (`COPY` undefined and `COPY=1`) | glslang (HLSL front end) accepts both: syntax and types only. The vs_2_0 **instruction-slot limit (256) is unchecked**: run `python -I tools/check_grass_vs.py <comfy>\src\grass.cpp` on Windows. |
| C++ (`grass.cpp`, `config.cpp`) | clang syntax-check against mingw headers: no errors from the new code. The two remaining errors (`WriteJump` with MSVC naked-function pointers) are in unchanged upstream code and are a clang/MSVC difference. `comfyatmos.cpp` (one-line change) could not be checked here (mingw header clash). A real build needs MSVC (`cmake -B build -A Win32`). |
| Shader maths | **Simulated** by `tools/grass_copy_model.py` (10 checks pass): thinning share to coverage within 0.02 after calibration (the naive formula was off by up to 0.16), wobble shear 0.33 or less, noise continuous across a chunk border with float32 absolute coordinates, fade monotone, offsets at least 0.5 yd from the original, slope compensation exact on a plane, a hidden copy's tip is below its root. |
| Visual result, overdraw holes, FPS | **Untested.** |

Assumptions to confirm in the game (each has a switch or a log line):
1. A chunk extends from its corner towards lower x and y (`chunkSign = -1`, from mapterrain.cpp's "corner is the largest x and y"). Only the slope term uses it. The probe's last line (`F12`, `grass: dense:`) reports batches with no slope.
2. Slope needs the map-file reader, which runs only while sun shadows, water or volumetric light read the terrain; otherwise slope = 0 and copies keep the original's height (slightly floating or sunk on steep ground).
3. Copies of a batch are translations only (no blade root); the smooth wobble hides the twin-pattern but a close look may still show pairs.

## 3. Asset workflow (the CPU/data side)

Does the repo hold the grass assets or tools? **No**: no MPQ/M2/BLP/DBC and no `eqgrass_*.py` (they live in other sessions' trees, `HANDOFF_PHASE1.md` §7), and `tools/blp_dump.py` imports an `mpq.py` from `D:\Octowow\EqAmbience\tools`, also outside the repo. So `tools/eqgrass/` is new and self-contained (Python 3, standard library; Pillow only for PNG previews):

| File | Purpose | Status |
|---|---|---|
| `mpq.py` | MPQ v1 reader (port of Comfy's `mpq.cpp`: zlib/stored/bzip2, stacked in the client's load order) and a simple writer | Hash/decrypt **differentially tested against a C transliteration of Comfy's code**, known table keys `0xC3AF3770` / `0xEC83B3A3` match; round trip and priority tested. A written archive has **not** been loaded by a 1.12 client. |
| `dbc.py` | WDBC read/write | Synthetic tests only |
| `m2.py` | Vanilla M2 (v256/257) parse and edit: widen x/y, scale z, add rotated copies. Offsets are those Comfy's `mapm2.cpp` reads from real files. | Synthetic tests only; edits append new arrays and leave the rest byte-identical |
| `eqgrass.py` | `inspect`, `build`, `pack` | End-to-end synthetic test passes (override archive wins, only grass names above 0.31 yd are edited, build on top of the live patch stack) |

Expected effect (from the simulator, assumed card sizes): widening a card by about 1.4x raised near-ground coverage from 61% to 81% at the same vertex count; a three-card clump needs fewer instances than single tufts for the same coverage. Vertex count drives the client's CPU fill cost, so `--copies` costs CPU; `--widen` is free.

### Run it (Windows; reads the game folder, writes only to `grass_out\`, changes nothing in the game folder)
Needs Python 3.9+ (Pillow optional, for PNG previews of the textures).
```bash
python -I tools/eqgrass/test_eqgrass.py
python -I tools/eqgrass/eqgrass.py archives --data D:\Octowow\Data
python -I tools/eqgrass/eqgrass.py inspect  --data D:\Octowow\Data --out grass_out --zip
```
- `archives` lists the archive stack in the client's load order (base < `patch.MPQ` < `patch-0..9` < `patch-A..Z`), so you can see `patch-Y.mpq` outranks `patch.MPQ`, and which archives could not be read.
- `inspect` reads the two ground-effect DBCs through that stack, resolves every doodad's M2, classifies it, and writes `grass_out\inspection-report.md`, `models.csv`, `archives.txt`, `original\` (the DBCs, the models, their textures as BLP and PNG).
- **`--zip` writes `grass_out\grass_analysis.zip`: the one file to send.** It contains those outputs only (a few MB), never an archive and never your whole installation. The test suite checks the package holds no `.mpq`.
- Open `inspection-report.md`: the `class` table must pick grass and spare flowers, red grass, rocks. If `inspect` stops with "no column holds file names" or "expected 4 doodad-id columns", the DBC layout differs from the assumption; if it reports an unsupported compression, send the archive name that `archives` prints. Tune `--include` / `--exclude` (regexes on the model file name) and run `inspect` again.
Then, if the picks are right:
```bash
python -I tools/eqgrass/eqgrass.py build --data D:\Octowow\Data --out grass_out --widen 1.35 --copies 0
python -I tools/eqgrass/eqgrass.py build --data D:\Octowow\Data --out grass_out --widen 1.35 --copies 0 --apply
python -I tools/eqgrass/eqgrass.py pack  --out grass_out --archive grass_out\patch-Y.mpq
```
(The first `build` is a dry run printing old and new vertex counts.) `build` reads the models through the whole stack, so it edits **what the game currently loads, including the existing `patch-Y.mpq` grass**. Install: rename the current `Data\patch-Y.mpq` to `patch-X.mpq` (still loaded, now lower priority) and put the new file in as `patch-Y.mpq`, which then overrides only the models it holds. `Launch_Stock.bat` must move `patch-X.mpq` aside as well, or stock mode keeps the new grass. If the other session's packer works, use it on `grass_out\build` instead of `pack`. The MPQ writer is round-trip tested only; the game has not loaded one it wrote.

Tested on synthetic archives only (`test_eqgrass.py`: 10 tests, including a PKWARE decoder checked against zlib's blast test vector, the stack priority, the report and the zip contents). Not tested on the real game files.

### The test DLL: built by GitHub Actions
`.github/workflows/comfy-dense-grass.yml` (runner `windows-2022`, MSVC, `-A Win32`, actions pinned by commit SHA, source pinned to commit `73bebaa`) checks out ComfyAtmosphere `v0.17.1-alpha`, applies `comfy/0001-dense-grass-copies.patch` (`git apply --check` first), builds the config test and the DLL, checks the DLL is a 32-bit PE, runs `tools/check_grass_vs.py` (Microsoft D3DCompiler: both `vs_2_0` variants, instruction/register limits, the wind variant identical to upstream's), runs `tools/check_comfy_patches.py` (all EqAzeroth text patches, in order, against the built DLL), and uploads `comfyatmos-dense-grass-x86` (DLLs, example ini, install notes, checksums, shader and patch reports) and `build-logs`. It never replaces anything: the DLLs are named `comfyatmos.dense-grass*.dll`. Install and rollback: `comfy/INSTALL-TEST.txt`. Benchmark: `GRASS_BENCHMARK.md`.

### Test sequence (summary; the full procedure is `GRASS_BENCHMARK.md`)
1. `density = 1`: confirm the grass is exactly as before; log line `grass: render states ...` appears.
2. F12 probe: `grass: dense:` line says the copy shader is `ready` once density > 1; if `FAILED`, the log has the compile error and the grass stays native.
3. `density = 2`, `copyDebug = 1` (copies flat red/yellow), then `0`. Walk the camera over a slope: floating or sunk copies mean `chunkSign` or the slope source is wrong (try `chunkSign = 1`).
4. Holes or squares in dense grass: set `alphaRef = 96`.
5. Cost: `/atmos framelog` at one spot with `density` 1, 2, 3, 4 and `densityDist` 25/45/70: read "the grass's CPU a frame" and the fps; GPU-bound vs CPU-bound as in `HANDOFF_PHASE1.md` §2. Only measured numbers go into reports.

## 4. Simulated evidence

`tools/grass_coverage_sim.py` (design guidance, assumed sizes, flat ground) and `tools/grass_copy_model.py` as above. Neither says how the game looks.

## 5. Open items and genuine blockers

- **Needs Windows + MSVC**: build the DLL, check the vs_2_0 slot count, run in the game. No Windows toolchain exists in this environment; MSVC-style inline assembly in upstream `grass.cpp` also does not build with mingw.
- **Needs your game's `Data` folder** (or the extracted `GroundEffectTexture.dbc`, `GroundEffectDoodad.dbc`, the grass `.m2`/`.blp` files, and a copy of `patch-Y.mpq`): to validate the DBC layout, model path guess (`World\NoDXT\Detail\`), model classification and the M2 edit on real files. How to get them: run `inspect` (above) and zip `grass_out\original` plus `models.csv`.
- **Alpha/depth behaviour** of overlapping cards is unknown until the first-draw log line is read.
- Not implemented: stream-frequency instancing, in-game sliders (cvars), BLP re-authoring (a denser blade atlas needs the real textures), DBC `Amount` scaling (its meaning is unverified), extending the client's own clutter distance (needs a validated address).
