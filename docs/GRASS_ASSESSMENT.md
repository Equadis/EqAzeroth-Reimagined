# Dense grass: assessment, comparison, recommendation (2026-11-08)

Status vocabulary as in `HANDOFF_PHASE1.md`: **confirmed** (seen in repo files or a measurement I ran), **recalled** (WoW 1.12 format knowledge, not checked against this client, verify before relying), **open**.

## 0. Scope of this session (read first)

- The repository holds shaders, ReShade add-ons, Lua and patch scripts only. It holds **no** WoW.exe, MPQ, DBC, M2, BLP, ADT, `comfyatmos.dll`, and the grass work (`patch-Y.mpq`, `tools/eqgrass_*.py`, foliage wind) is listed in `HANDOFF_PHASE1.md` §7 as living outside it.
- This machine is Linux with no Windows toolchain (no mingw, no fxc, no WoW). So **nothing here was run in the game, no FPS/frame-time/VRAM number exists, and no in-game prototype was built.** Claiming otherwise would be invention.
- What was built and measured: `tools/grass_coverage_sim.py`, an offline coverage-versus-cost simulator (§4). Its results drive the recommendation. They are geometric, on flat ground, with assumed card sizes, and are a design guide, not a benchmark.

## 1. Phase 1: the existing system

### Confirmed from the repo
| Fact | Source |
|---|---|
| ComfyAtmosphere already intercepts the client's grass draws and draws them through its own vertex shader (wind, parting around the player). Identification: fixed-function, world matrix without rotation, `stride=36`, `primType=4` (TRIANGLELIST), `minVerts=64` (excludes ~65 small doodad draws). | `EqShaders/Presets/comfyatmos.ini` `[grass]` |
| The client builds grass **on the CPU**: a fill loop writes each instance's vertices (and per-vertex colour) into a dynamic buffer; Comfy patches two instructions at `WoW.exe+0x006B26CD` (1.12.1.5875) so each vertex carries its height up the model. Grass seen as "one draw per chunk with baked instances" is consistent with the draw filter, but chunk granularity is **recalled, not captured**. | same; `addons/EqPixelBridge/EqPixelBridge.cpp` header (build 5875) |
| Native-instance CPU cost therefore scales with instances x vertices per model, and denser native grass raises CPU time in a client that is already single-thread bound. | inferred from the above; **open**: measure |
| ReShade runs as a Vulkan layer over DXVK 3.1.1. It sees post-translation Vulkan, not fixed-function state, and cannot add scene geometry. Screen-space tools exist (`AntiTile.fxh`: world-space ground variation from depth + EqPixelBridge camera). | `PORT_MAP.md`, `HANDOFF.md`, `AntiTile.fxh` |
| In-process D3D9 hook DLLs are an established pattern here (EqPixelBridge: `LoadLibraryA` import -> `Direct3DCreate9` -> `CreateDevice` -> device vtable patch, delayed 8 s to sit outside Comfy's hook). `addons/build.ps1` builds them with llvm-mingw. | `EqPixelBridge.cpp`, `build.ps1` |
| Comfy's HLSL is text in the DLL, patched same-length (`tools/patch_comfy_*.py`). It cannot gain draw logic that way, only change shader text. Whole-DLL replacement by other sessions has dropped patches before. | `HANDOFF_PHASE1.md` §4 |
| Clutter-related Comfy cvars exist (`comfyGrassWind/Speed/WaveLength/Lean`), set per biome in `ProfileData.lua`. | `EqProfiles` |

### Recalled, verify with a capture before building on it
- `GroundEffectTexture.dbc`: per terrain-texture row, up to 4 `GroundEffectDoodad` ids with `Amount[4]` weights, plus a terrain type. `GroundEffectDoodad.dbc`: model file name and flags. Each ADT `MCNK` carries a chunk effect id (from its layer texture) and a 64-bit "no effect doodad" mask over its 8x8 cells. Placement is per cell, deterministic by position, so roads/buildings/water are already masked by the engine.
- The in-game "ground clutter" slider writes the `frillDensity` cvar. Its cap, whether it scales instance counts per cell only, and the distance limit are **unknown for this client**. The distance cut is probably a client constant, not a cvar; extending it needs a validated memory patch (not done, not proposed without addresses).
- Vanilla grass M2s are small low-poly cards (v256 M2, BLP textures).

### Answer to "modify native, or separate renderer?"
- Native system alone **can** raise apparent coverage via models and DBC weights (no new code), but it pays CPU per instance and per vertex, and distance stays at the client's limit. It cannot reach a dense look cheaply on the CPU side.
- A separate terrain-driven renderer (ADT + masks + heights) means re-implementing MPQ/ADT reading and chunk lifetime inside a hook, and risks duplicating the engine's own masks. Costly and fragile.
- The cheapest route to density on the GPU is to **reuse the engine's placement** (it already encodes masks, texture types, slopes, flowers) and multiply it on the GPU. See §3 and §5.

## 2. Phase 2: approach comparison

Cost scale: Low / Med / High. Everything not simulated is judgement, marked as such.

| | Coverage / look | CPU | GPU | Draw calls | VRAM | Complexity | Stability risk |
|---|---|---|---|---|---|---|---|
| **A. Native + better models/DBC** | Good up to the client's cap; sim: wide clump cards reach ~87% at 40-60 yd with 0.25 inst/yd2 | Med: fill loop scales with instances x verts | Low-Med | unchanged | negligible | Low (data only; fits `patch-Y.mpq` workstream) | Low |
| **B. GPU-instanced (own placement)** | Best control | Low once built | Low-Med | ~1 per chunk batch | Low (instance buffers) | High: needs heights, masks, chunk lifetime | High |
| **C. Terrain-driven procedural** | Natural, deterministic | Med at chunk load (spikes) | Low | few | Low | Highest: ADT/MPQ in-process | High |
| **D. Shell/fur** | Soft carpet, poor silhouettes | Low | **High**: sim hit counts show layered overdraw; a shell stack at 20+ layers equals the 20+ hits/ray rows below | 1-2 | Low | Med | Med |
| **E. ReShade only** | Screen-space tint/texture/noise on ground: can hide tiling, **cannot make geometry**, no silhouettes, wrong at grazing angles | none | Low | 0 | 0 | Low | Low |
| **F. Hybrid: A + GPU multiplication of native instances + E for ground** | Best per cost | Low (no new placement) | Low-Med | ~1 extra per batch, **or** 0 extra if the vertex shader replays geometry | Low | Med | Med (hook ordering with Comfy) |

Direct3D 9 instancing: available only with a vertex shader (SM3, `SetStreamSourceFreq`); not with the fixed-function path the client uses. Comfy already substitutes its own VS for grass, so it is possible there. DXVK implements stream frequency, but **I have not verified this in DXVK 3.1.1 in this setup: open, needs a test draw.** Fallback: `DrawIndexedPrimitive` per copy (N-1 extra calls per chunk), which costs CPU and must be measured.

## 3. Design choice: multiply native instances

Idea: for each native grass draw (already identified by Comfy's filter), draw it N times with small per-copy offset, yaw and scale, applied in the vertex shader. Properties:
- Inherits engine placement: roads, buildings, water, flowers, non-grass terrain stay empty (offset keeps copies near an engine-approved spot). Native flowers and red grass remain native; they pass the same filter only if their model matches, so the filter needs a model allow-list (open).
- No new CPU generation or terrain reading; cost is one extra instanced/replayed draw per chunk.
- Risks: copies on slopes float or sink by about `offset x slope` (mitigate: clamp offset, sink roots ~0.1 yd, shrink the offset on steep chunks); edge bleed of `offset` yards past masks; repetition (mitigate: hash jitter of offset/yaw/scale/colour from world position).

Simulator check of the clustering concern (§4.2): offset copies at r >= 1 yd match independent density within the sim noise.

## 4. Measured: coverage versus cost (offline simulator)

`python -I tools/grass_coverage_sim.py --pitch 12` (and `--pitch 45 --eye 6`). Flat ground, vertical alpha-cutout quads, assumed sizes, geometric coverage (no lighting). 192x108 rays, one random seed: **expect about +-5 points noise**, more in sparse bands.

### 4.1 Cost to hide the ground (typical third-person view, pitch 12, eye 3 yd)
| card (assumed size) | inst/yd2 | verts/yd2 | hidden 0-15 yd | 15-40 yd | 40-60 yd | quad hits/ray 0-15 / 15-40 / 40-60 |
|---|---|---|---|---|---|---|
| tuft, 1 quad 0.6x0.5 | 4 | 16 | 51% | 90% | 98% | 2.1 / 6.6 / 13.0 |
| tuft, 2 crossed | 4 | 32 | 70% | 98% | 100% | 4.1 / 13.1 / 26.0 |
| clump, 3 quads 0.9x0.6 | 1 | 12 | 61% | 87% | 98% | 3.4 / 8.4 / 18.1 |
| clump | 4 | 48 | **94%** | 100% | 100% | 11.1 / 35.2 / 70.0 |
| wide 3 quads 1.4x0.7 | 1 | 12 | 81% | 98% | 100% | 6.1 / 15.2 / 32.8 |
| wide | 2 | 24 | **92%** | 100% | 100% | 10.9 / 31.8 / 65.8 |
| wide | 0.25 | 3 | 34% | 64% | 87% | 1.5 / 3.9 / 8.0 |

Pitch 45, eye 6 (high orbit camera): near ground is harder (clump 4/yd2: 74% at 0-15 yd; wide 2/yd2: 88%), because vertical cards foreshorten from above.

### 4.2 Native x4 multiplication vs true density (clump, base 1/yd2)
| scheme | hidden 0-15 | 15-40 | 40-60 |
|---|---|---|---|
| native only | 61% | 87% | 98% |
| x4, offset 0.4 yd | 89% | 98% | 100% |
| x4, offset 1.0 yd | 94% | 99.5% | 100% |
| x4, offset 2.0 yd | 95% | 99.9% | 100% |
| x4 independent | 96% | 100% | 100% |

(At pitch 45: 73% / 90% / 85% / 82% for the same offsets; read as "no clear loss versus independent beyond noise".)

### 4.3 What the numbers say
1. **Width and clumping beat count.** 1 wide card/yd2 (12 verts/yd2) hides more near ground (81%) than 4 single-quad tufts/yd2 (16 verts/yd2, 51%). Same or fewer vertices, so the better model is free CPU-wise.
2. **Density can fall about 16x with distance if cards widen**: near ~2-4 clumps/yd2, mid ~1/yd2, far ~0.25/yd2 still gives 87% by 40-60 yd. This gives the LOD bands (§5).
3. **Overdraw is the real far-field cost**: dense far grass stacks 30-70 alpha-tested quad hits per pixel. Do not render dense grass far away; fade to ground texture (hybrid with E). This is also the argument against shells (D).
4. **~90% hidden near the player needs about 48 verts/yd2 (clump x4) or 24 (wide x2).** Past that the extra instances add overdraw, not visible coverage.
5. Multiplying engine instances (x4, r >= 1 yd) reaches true-density coverage in the sim, so no new placement system is needed for the CPU side.

## 5. Recommendation

Hybrid **F**, delivered in stages; each stage is independently shippable and gated by a measurement.

1. **Stage 1, data only (no code)**: replace the grass M2s with wide clump cards (3 crossed quads, many blades per card, height/width variation by vertex colour), better alpha-cutout BLPs with mip-aware alpha, and raise `Amount` weights in `GroundEffectTexture.dbc` only for rows verified to be plain grass. Keep flower/red-grass rows untouched. Goes in `patch-Y.mpq` with the existing eqgrass workstream; coordinate before editing it. Measure CPU frame time versus the stock model at the same `frillDensity`.
2. **Stage 2, GPU multiplication (needs a Windows build)**: `EqGrass.dll` in the EqPixelBridge pattern (kill switch `EqGrass.ini Enabled=0`, delayed vtable patch, log, no game-memory writes). Pass 1: log-only capture proving the grass draws' chunk granularity, vertex counts per draw, and DXVK instancing support. Pass 2: N-copy replay with offset/yaw/scale hash in a vertex shader, density/distance cvars, Comfy wind preserved (same VS family), distance fade by dithered alpha. Not built here: no toolchain and no capture to design against.
3. **Stage 3, ground blend in ReShade**: extend `AntiTile.fxh` to push distant grass-textured ground (high ground-normal, green hue) toward a darker, noisier "grass-floor" shading so the bare-ground look fades gracefully past the grass distance. Screen-space only, cannot fake geometry.

Do not pursue: shell/fur (overdraw, §4.3), a from-scratch ADT/heightmap renderer (cost/risk, duplicates engine masks), memory patches for distance without validated addresses.

Starting LOD bands (to tune by measurement, from §4): 0-15 yd clump x3-4 per native instance; 15-40 yd x1-2 with wider cards; beyond that native only, fading into the ground blend. All smooth: per-copy dither fade over the band edge, not a hard switch.

## 6. Benchmark protocol (to run; no results exist)

Fixed spots (Northshire, Mulgore plain, a slope, a road edge, a dense-flora zone), camera held still (see `HANDOFF_PHASE1.md` §8), three resolutions (one low so CPU-bound, 3440x1440 GPU-bound), 60 s each, stock launcher versus modded.
- FPS and frame time: DXVK HUD (`DXVK_HUD=fps,frametimes,drawcalls,memory,gpuload`), or PresentMon for 1% lows. Draw calls: HUD counter, or the log pass of EqGrass.
- CPU vs GPU bound: same spot at 25% render scale; if FPS does not move, CPU bound.
- Spikes: log frame time while running through chunk boundaries and teleporting.
- Matrix: native `frillDensity` low/mid/max; Stage 1 models; Stage 2 at multiplier 1/2/4 and distance 20/40/60.
Report average, 1% low, max frame time, draw calls, VRAM. Accept a stage only if the 1% low drops by less than an agreed budget (suggest 3%) in the CPU-bound case.

## 7. Limitations
- 32-bit client: address space is the hard cap for anything resident; instance buffers must stay small (the multiplication design keeps none).
- D3D9 via DXVK: instancing needs a vertex shader, and fixed-function state is invisible to ReShade. DXVK stream-frequency behaviour unverified here.
- Hook order with Comfy: whoever patches the device vtable last wins; the 8 s delayed-patch trick is fragile.
- Hardcoded addresses (`fillAddr`, camera, object manager) are for 1.12.1.5875 only.
- Alpha-tested grass has no MSAA benefit; MSAA must stay 0/1 anyway (Comfy INTZ requirement). Aliasing of thin blades relies on `Sharpen`/AA passes already in EqShaders.

## 8. Needed to continue
1. A frame capture (RenderDoc on DXVK/Vulkan or apitrace on D3D9) of a grass scene: per-draw vertex count, VS/FFP, instances per draw, `frillDensity` effect.
2. Extracted `GroundEffectTexture.dbc`, `GroundEffectDoodad.dbc`, the grass M2/BLP files, or the `tools/eqgrass_*.py` scripts, so Stage 1 can be done and checked.
3. A Windows build environment (llvm-mingw per `addons/build.ps1`) for Stage 2.
4. Confirmation of who owns `patch-Y.mpq`, to avoid the DLL-style overwrite problem.
