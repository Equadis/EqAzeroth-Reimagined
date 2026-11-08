# EqAzeroth-Reimagined: Phase 1 handoff (2026-11-08)

**Bottom line:** Phase 1 built the whole rendering stack and most of the look for OctoWoW (Classic 1.12.1), but little of it is confirmed in-game, and several workstreams write to the same DLL, so patches get lost. Start Phase 2 by confirming what the live `comfyatmos.dll` contains, then work down section 6.

The old repository (`Equadis/EqShaders`, earlier `Equadis/PhotonWoW`) is **retired**. This repository carries its full history. Older notes are `HANDOFF.md` (v0.3, 2026-10-26, the ReShade port) and `EQSHADERS_COMFY_FINDINGS.md`. This file replaces them as the starting point.

Status words used below: **confirmed** = seen working in the game by the user or in a capture I read; **built** = compiled or patched, not seen working; **open** = not done. Where a session reported something I did not see myself, it says "reported".

## 1. Goal

A realistic, vibrant, shadowy, modernised Classic WoW. Not painterly, not too blue, not milky. The HUD must stay untouched by world effects. Defaults should look right without per-zone tuning.

## 2. The stack

```
WoW.exe (32-bit 1.12.1) <- VanillaFixes.exe loads the DLLs in dlls.txt
  -> d3d9.dll = DXVK 3.1.1 (Direct3D 9 to Vulkan)
    -> Vulkan, ReShade 6.8.0 Addon as an implicit layer (only when launched by Launch_EqShaders.bat)
      -> EqShaders.fx (post-processing)
```

Live game folder: `D:\Octowow`. Repository: `D:\Octowow\PhotonWoW`.

**Launchers** (in `D:\Octowow`)
- `Launch_EqShaders.bat`: full look. Sets `VK_ADD_IMPLICIT_LAYER_PATH` (this is what turns ReShade on), then starts `VanillaFixes.exe`.
- `Launch_Stock.bat`: stock look plus the other mods (nampower, SuperWoW, UnitXP and so on). Starts `VanillaFixes.exe` with a `dlls.txt` that leaves out `comfyatmos.dll`, `EqPixelBridge.dll` and `EqLoadingScreens.dll`, with no ReShade layer, then restores `dlls.txt`. Also moves `Data\patch-Y.mpq` (grass) aside. Not run by me; the Grass session reported that after running it `patch-Y.mpq` is moved aside and `dlls.txt` is restored, and had not yet seen the stock look in-game. `Data\patch-Z.mpq` (audio) still loads in stock mode.
- `VanillaFixes.exe` may show a "dlls.txt changed" confirmation window. Click OK.
- `WoW.exe` alone: no mods at all.

**`dlls.txt`** (full): VanillaMultiMonitorFix, transmogfix, VanillaHelpers, SuperWoWhook, nampower, ClassicAPI, AuctionQueryThrottle, UnitXP_SP3, mods/OctoLogin, EqPixelBridge, EqLoadingScreens, comfyatmos.

**Hard requirements**
- MSAA must stay 0 or 1. Higher breaks Comfy's INTZ depth copy.
- ReShade keys: Home (overlay), F7 (effects on/off), F8 (reload). Comfy keys (F11 reload, F12 probe) work only with `[general] hotkeys = 1`.

## 3. Components

| Part | Where | What it does |
|---|---|---|
| **EqShaders** | `EqShaders/Shaders/EqShaders.fx` and `EqShaders/`, `PhotonWoW/` includes | Depth linearise, prep (sRGB to linear, LDR to HDR), two-scale AO, lighting, bloom pyramid, composite (look layer, tonemap), final (sharpen, depth-guided anti-aliasing, dither). `AntiTile.fxh` adds world-space ground variation and fades sharpening with distance. `Wet.fxh` is the rain/puddle effect. |
| **ComfyAtmosphere** | upstream `aloofbit/comfyatmosphere` v0.17.1-alpha (GPL-3.0), not vendored here | The renderer backend: fog, volumetric light, sun shadows, water, clouds, lamps. Its HLSL is plain text inside the DLL, compiled at start-up. |
| **Comfy patch scripts** | `tools/patch_comfy_*.py` | Same-length text patches of that HLSL (section 4). |
| **EqProfiles** | `EqShaders/AddOns/EqProfiles/` | Lua 5.0 addon. Layers Global, Biome, Zone, Subzone, Interior, plus Night, and fades between them over 5 s. 73+ CVars in `ProfileSchema.lua`; values in `ProfileData.lua`. Command `/eqprof [status|on|off|capture <layer>|rejected|fade <s>|biome [name]]`. Also paints a pixel strip (indoor flag, daylight) that `Bridge.fxh` reads. |
| **EqPixelBridge** | `addons/EqPixelBridge/` | Native strip of camera and weather values in the frame, read by the shader. |
| **EqUIGate, EqBridge, EqFog** | `addons/` | Experiments to keep the HUD out of the effects. EqUIGate hung the game; all three are disabled. See section 6. |
| Not in the repo yet | see section 7 | EqLoadingScreens, EqGrass, foliage wind, EqAmbience (audio), rain splash, lamp and fog patches. |

## 4. How the Comfy patching works (read this before touching the DLL)

- The DLL carries its shaders as text. A patch replaces a block with new text of **exactly the same length**, padded with spaces inside a comment. Each `tools/patch_comfy_*.py` has `--check <dll>` (reports patched, original or unknown) and never modifies its input.
- Patches in this repo, in the order I applied them: `water` (bed colour), `shore` (opacity ramp, scaled by slope), `wavetile` (wave directions, wandering crests, strength variation), `reflect` (reflection sees half the wave slope), `shoref` (sky and glint fade out in the shallows; supersedes `reflect`'s block), `glint`, `fogfar`, `splash`, `cloudvol`. Others (fog, lamps, foliage) are in the working tree only.
- **The main hazard:** at least six sessions installed their own whole DLL, and patches were dropped at least twice. Before any test, run each patch's `--check` against `D:\Octowow\comfyatmos.dll`. `tools/merge_comfy_patches.sh` (untracked) merges them. A mingw-built DLL from source exists as `comfyatmos.dll.mingw-test`; it was never run in the game.
- A patch cannot add many registers to the water shader (it sits near the ps_3_0 limit). If the water breaks, restore the backup in `EqShaders\backups\`.
- Backups: `D:\Octowow\EqShaders\backups\` (DLLs, INIs, `Config.wtf`, `dlls.txt`). Patch outputs are `EqShaders\comfyatmos.dll.*patch`.

## 5. Settings model (this caught us repeatedly)

- Comfy sliders are CVars saved in `WTF\Config.wtf` and **override the `comfyatmos.ini`** values. Editing the INI changes nothing for a slider. Check `Config.wtf` or ask the game.
- EqProfiles is switched on per character in `WTF\Account\<acct>\<realm>\<char>\AddOns.txt`. It was **disabled for Nue** until the user enabled it on 2026-10-27, so edits to `ProfileData.lua` had no effect until then. Once on, it writes values only when the zone changes, so a value set by a chat command can linger.
- The shadow problem that looked like a shader bug was `comfySunShadowStrength` = 100 in `Config.wtf`. About 60 reads well. Zone values are now about 51 to 79.
- The saved water settings in the Global profile: edge line 0, lake foam 0, swash height 0, foam size and reach 0, wet sand 20.

## 6. Status by workstream (as of 2026-11-08)

| Workstream | Status | Next step |
|---|---|---|
| Outdoor shadow and ambient balance | **confirmed** at one Lakeshire facade: strength 60 keeps texture. Per-biome values rescaled; AO and contrast softened; a hue-preserving shadow floor added. | Look at other biomes after `/reload` |
| Water: wave stripes | Stripes were the sky reflection (confirmed by toggling bend and reflect). `reflect` patch **confirmed** at one view. | Check low angle toward the sun |
| Water: shoreline | Edge line, foam, swash off: pale rim mostly gone (one frame). Slope-aware ramp and `shoref` **built**. | Confirm `shoref` is in the live DLL, then check several banks |
| Ground anti-tiling | **built**, compiles and loads. Not validated on paths, roads, sand, rock or from high up. | Validation pass |
| Interior lighting | Comfy already tracks real light positions in world space (SetLight hooks plus lights read from map files, kept off-screen). A world-space indoor darkness is in Comfy's source but **unbuilt**. My screen-luminance version was removed. | Build Comfy from source or patch text; test in an inn |
| Camera-height darkening | Not understood. The strength fix removed the symptom at one facade. A fork softened rays and fog by angle (reported, untested). | Restart and compare |
| HUD excluded from world effects | **open**. EqUIGate's mid-frame render hung the game. Depth cannot separate the HUD. | Needs a pre-UI hook; see `HANDOFF.md` section 4 |
| Lamps | Falloff patch **built** (reported), not installed | Clean before-frame, install |
| Fog and horizon, rain visibility | Build v6 installed (reported), not checked | Read log line "fog pass uses the world copies" |
| Underwater | Cyan is the game's own underwater fog (reported). Diagnostics installed. | Probe underwater |
| Clouds | Volume shader pushed. Flight parallax unconfirmed; sunset, night and rain decks not started. | Parallax test |
| Foliage wind | Pause-while-moving fix staged (reported) | Restart and test around trees |
| Grass | New grass in `patch-Y.mpq`; stock launcher moves it aside (reported) | Confirm both launches at Northshire |
| Rain splash | Atlas texture is 4x4; ground uses rows 2 and 3 (reported) | Install diagnostic |
| Reflections on wet ground | Ray-start fix compiled (reported), not seen | Press F8 at a road |
| Loading screens | **Confirmed** at 3440x1440 (reported) | Other resolutions simulated only |
| Ambient audio (Sonniss 2024) | `patch-Z.mpq` holds a rain file and test tones. Nothing heard in-game. | Needs user at login |
| Character models | `patch-L.mpq` is eight loading-screen textures, not models (reported) | none |

**Not started anywhere:** campfire and torch shadows, fog rectangle and hard bands, white mountains in rain, camera-distance brightness stability.

## 7. Not in this repository yet

Left out on purpose because other sessions were still editing them: `EqLoadingScreens/`, `addons/EqLoadingGate/`, `foliagewind/`, `assets/`, `build/`, `tools/eqgrass_*.py`, `tools/eq_rainsplash.py`, `tools/patch_comfy_fog*.py`, `tools/patch_comfy_lamp*.py`, `tools/patch_comfy_lightshape.py`, `tools/merge_comfy_patches.sh`, `EQFOLIAGEWIND_FINDINGS.md`. Two tracked files (`EqPixelBridge.cpp`, `EqUIGate.cpp`) have uncommitted edits. Game-side files (`Data\patch-*.mpq`, `comfyatmos.dll`, `WTF\`) are not versioned.

## 8. Working rules that saved time

- The user types their password. Do not automate login. The user stands at the test spot and holds the camera still; a test with a moving camera is worthless.
- Keystrokes into the game only reach it when WoW is the foreground window. Opening chat first and typing a `/script` line works, but any concurrent typing garbles it and can leave a Lua error. Prefer asking the user to type one line.
- Git Bash rewrites arguments that start with `/` (it turned `/eqprof` into a Windows path). Use PowerShell for chat helpers.
- Test one thing at a time with a live CVar change, and always restore it. Several tests left `comfyWaveHeight`, `comfyWaterBend` or `comfyShadowOnWater` at 0.
- Only one session at a time should replace `comfyatmos.dll` or launch the game. Two WoW windows were running at once more than once.
- Photon licence (`PHOTON_LICENSE.txt`): personal use only; no money; no redistribution on monetised platforms. The sound bundle licence forbids redistributing the converted audio.
