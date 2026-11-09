# Dense grass: in-game benchmark and visual test procedure

Status: **a procedure, no results.** Nothing below has been run in the game. Every number must come from `Logs\comfyatmos.log` or the screenshots you take; do not quote anything else.

Needs the test DLL from the CI artifact (`comfyatmos-dense-grass-x86`, see `comfy/INSTALL-TEST.txt`).

## 0. What the existing instrumentation gives (from ComfyAtmosphere v0.17.1-alpha source)

| Tool | Gives | Use |
|---|---|---|
| `/atmos framelog 30` | Times every frame for 30 s, then logs: frames, **ms a frame, fps, slowest 1 %, worst**; our CPU in all (hooks + passes); **"the grass's CPU a frame, its draws included"** (the copies run inside this timer, so its rise with density *is* the CPU cost of the extra draws); the client's buffer-lock time; our GPU time and **the GPU's whole frame** (timestamp queries) | the CPU/GPU numbers |
| `/atmos probe` (or F12 with Developer keys) | One frame of diagnostics; for grass: draws and vertices last frame, `grass: dense:` line with **extra draws last frame**, batches without ground slope, copy shader state; the first grass draw also logs the client's alpha/blend/depth states | draw-call count, shader state |
| `/atmos grass.density N` etc. | Sets a key live (reloads settings; `/atmos reset` drops it) | switching conditions without a restart |
| `DXVK_HUD=fps,frametimes,drawcalls,memory,gpuload` (environment variable before launching; DXVK's own HUD) | Whole-frame draw calls, frame-time graph, GPU memory | draw calls (all), VRAM. Not yet checked with this launcher. |
| F7 | EqShaders (ReShade) on/off | rule ReShade in or out |

`/atmos framelog` prints no density, so the procedure calls `/atmos probe` right after each run and `tools/parse_framelog.py` joins the two. The log has no timestamps: keep the runs in order.

## 1. Fixed conditions (write them down once, keep them for every run)

1. Same build, same ini except `[grass]`. Note the version line in the log (it ends `-dirty`: the patched build).
2. Same character, **time of day** (use `[time]` fixed hour or `Ctrl+End` day), weather, zone. Same resolution and graphics settings. **vsync off, no frame cap** (`/console maxfps 0` is believed to work in 1.12: confirm the fps rises above your monitor's refresh): Comfy's own notes record a cap hiding the GPU-bound difference.
3. The native grass setting (`Foliage Density` = the client's `frillDensity`) fixed at one value for the whole test; note it.
4. Camera: stand still on a flat grassy spot, set the view, then `/script SaveView(2)`. Before each run `/script SetView(2)` (these two functions are believed present in 1.12: if not, keep the camera still by hand and take a reference screenshot to align). Do not touch the mouse during a run.
5. Warm the GPU first (2 minutes in the scene): laptop GPUs slow as they warm. Run conditions in the order **1, 2, 3, 4, 4, 3, 2, 1** (mirror order) and average each pair: this cancels drift.
6. Two spots at least: (a) flat open grass, (b) a **slope** with grass (also for the floating/sinking check). Optional: (c) a spot beside a road/path, (d) the Redridge grass Comfy measured at 213 draws / 130,516 vertices, for comparison with its own number.

## 2. Matrix

Distance 45 yd unless stated.

| id | density | densityDist | purpose |
|---|---|---|---|
| A | 1 | 45 | baseline: must look and cost as before (dense off draws nothing) |
| B | 2 | 45 | one full copy |
| C | 3 | 45 | two copies |
| D | 4 | 45 | three copies |
| E | 6 | 45 | only if D's cost is acceptable and coverage still looks open |
| F | 3 | 25 and 70 | distance sensitivity |
| G | 3 | 45, `alphaRef = 96` | does an alpha test remove overlap holes, and at what cost |
| H | 3 | 45, `copyDebug = 1` | shows exactly where copies are drawn (flat red to yellow by copy number) |

## 3. Per-run steps

```
/atmos grass.density 3          (and any other key for the row)
/script SetView(2)              wait 10 s: copies fade in, shaders compile once
/atmos framelog 30              wait 30 s, camera still
/atmos probe                    immediately after the framelog ends
```
Then take a screenshot from the saved view (`/script Screenshot()` or the usual key; ReShade's PrtScn saves the post-effect image) named `<spot>_<id>.jpg`. At the end:

```bash
python -I tools/parse_framelog.py D:\Octowow\Logs\comfyatmos.log --csv grass_results.csv
```
The table prints ms/frame, fps, slowest 1 %, worst, grass CPU and its **delta against density 1**, extra draws, grass draws, GPU frame. Keep the log file and the screenshots.

### CPU-bound and GPU-bound
Run the matrix twice: at your normal resolution, and at a **low resolution** (e.g. 1280x720, same settings). If fps barely moves between resolutions the scene is CPU-bound (the client's single thread): there the grass CPU delta and ms/frame delta are what matter. At high resolution, a GPU frame rising faster than the grass CPU means overdraw. Compare "the GPU's whole frame" with ms/frame: near equal = GPU-bound.

### Measuring the draw overhead, not assuming it
- `grass CPU ms` rows B..E minus A = the CPU cost of the copies per frame. Divide by `extra draws` for the cost per extra draw. Expectation to **check**, not assume: Comfy's 213 original draws cost 0.12 ms.
- If that per-draw cost times the draws you want exceeds your CPU budget, the next step is stream-frequency instancing (one draw for all copies): not implemented; the number above decides whether it is worth building.
- `DXVK_HUD ... drawcalls` shows total calls per frame: the rise from A to C should equal the probe's `extra draws`.
- VRAM: copies allocate no buffers (they re-draw the client's own vertex buffer). Check the HUD's memory figure is flat across A..E; a rise is a finding.

## 4. Checklist (tick per spot; attach screenshots)

| Check | How to see it | If it fails |
|---|---|---|
| Shader compile failure | log line `the copy shader failed to compile, so dense grass is off`; `grass: dense:` says `FAILED`; no copies visible at density 3 | grass stays native; send the log |
| Config applied | `/atmos grass.density` prints the value and `from ini` / `set with /atmos`; probe shows the same density | send the log |
| Alpha/depth overlap | rectangular or card-shaped cut-outs in dense grass where sky or ground shows through; dark/clear rectangles; worse with more density | set `alphaRef = 96` (row G) and compare |
| Floating | on the slope, from a low camera: copies hovering above the ground, a gap under their roots | try `chunkSign = 1`; note whether the log says `batches without ground slope` (slope needs the map-file reader: it runs only with sun shadows, water or volumetric light on) |
| Sinking | on the slope, copies short or missing on the uphill side, roots buried | same |
| Inappropriate surfaces | copies on roads, paths, dirt patches, building floors, river banks, beyond the native grass edge (expect a bleed of up to `spread + wobble` = about 1.7 yd; more is a finding) | reduce `spread`; note exact place |
| Distance fade | stand still and watch the band near `densityDist` for a ring where copies shrink/appear, or visible "growing" when you walk | lengthen `densityFade`; note distance |
| Repetition | twin clumps at a fixed offset, a regular lattice, a visible chunk-sized (33 yd) pattern | raise `wobble` or `patch` |
| Excessive GPU overdraw | GPU frame rising steeply with density at high resolution while grass CPU is small | lower `densityDist` or density |
| Frame-time spikes | `worst` and `slowest 1 %` from framelog; also run forward 10 s through new chunks at A and C and compare | note the chunk-load spikes |
| Existing features | wind sways the copies like the originals; the parting around your character; fog and lighting colour of copies match the originals; atmosphere, water and shadows unchanged | compare screenshots A vs C outside the grass |
| Native vegetation | flowers, red grass and other native doodads still present | |

## 5. Report back
`grass_results.csv`, `Logs\comfyatmos.log` (zip it), the screenshots, and for each failed check the spot and the setting. The decision rule agreed in the assessment: keep a density only if the 1 %-low and ms/frame deltas are acceptable in the **CPU-bound** run.
