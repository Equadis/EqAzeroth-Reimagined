// Offline test that the dense-grass keys in [grass] are read, clamped and applied by ComfyAtmosphere's own config.cpp
// (compiled in, not copied). Needs no game, no graphics device. Build with the patched source tree:
//   cl /nologo /EHsc /std:c++17 /I<comfy>\src grass_config_test.cpp <comfy>\src\config.cpp /Fe:grass_config_test.exe
// usage: grass_config_test.exe [path\to\a\comfyatmos.ini ...]   (each extra ini is loaded and its density printed)
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <string>
#include "config.h"

static int g_fail = 0;
#define CHECK(c) do { if (!(c)) { std::printf("FAIL line %d: %s\n", __LINE__, #c); ++g_fail; } } while (0)
static bool Near(float a, float b) { return std::fabs(a - b) < 1e-4f; }

static std::wstring Wide(const std::string& s)
{
    std::wstring w(s.size() + 1, 0);
    w.resize(MultiByteToWideChar(CP_ACP, 0, s.c_str(), -1, &w[0], static_cast<int>(w.size())) - 1);
    return w;
}

static std::wstring WriteIni(const char* name, const char* text)
{
    char dir[MAX_PATH];
    GetTempPathA(MAX_PATH, dir);
    const std::string path = std::string(dir) + name;
    FILE* f = std::fopen(path.c_str(), "wb");
    std::fputs(text, f);
    std::fclose(f);
    return Wide(path);
}

static const ConfigKey* Find(const char* section, const char* key)
{
    for (const ConfigKey& k : ConfigKeys())
        if (k.section == section && k.key == key)
            return &k;
    return nullptr;
}

int main(int argc, char** argv)
{
    // 1. nothing in the ini: the defaults. Density 1 means dense grass is off.
    {
        const std::wstring ini = WriteIni("eqgrass_t1.ini", "[grass]\r\nenabled = 1\r\n");
        LoadSettings(ini.c_str());
        const GrassSettings& g = g_cfg.grass;
        CHECK(Near(g.density, 1.0f));
        CHECK(Near(g.densityDist, 45.0f) && Near(g.densityFade, 0.35f) && Near(g.spread, 1.2f));
        CHECK(g.alphaRef == -1 && g.chunkSign == -1 && g.copyDebug == 0);
        const ConfigKey* k = Find("grass", "density");
        CHECK(k && k->source == kFromDefault);   // noted as read, from the default
    }
    // 2. every key set: all read, with trailing comments as the shipped ini has them
    {
        const std::wstring ini = WriteIni("eqgrass_t2.ini",
            "[grass]\r\ndensity = 3.5 ; comment\r\ndensityDist = 60\r\ndensityFade = 0.5\r\nspread = 2\r\nwobble = 0.3\r\n"
            "patch = 2.5\r\nheightVar = 0.1\r\ntintVar = 0.2\r\nalphaRef = 96\r\nchunkSign = 1\r\ncopyDebug = 1\r\n");
        LoadSettings(ini.c_str());
        const GrassSettings& g = g_cfg.grass;
        CHECK(Near(g.density, 3.5f) && Near(g.densityDist, 60.0f) && Near(g.densityFade, 0.5f) && Near(g.spread, 2.0f));
        CHECK(Near(g.wobble, 0.3f) && Near(g.patch, 2.5f) && Near(g.heightVar, 0.1f) && Near(g.tintVar, 0.2f));
        CHECK(g.alphaRef == 96 && g.chunkSign == 1 && g.copyDebug == 1);
        const ConfigKey* k = Find("grass", "density");
        CHECK(k && k->source == kFromIni && std::atof(k->value.c_str()) == 3.5);
        // 3. /atmos overrides win over the ini (what `/atmos grass.density 2` does), also after a reload
        ConfigOverrides()["grass.density"] = "2";
        LoadSettings(ini.c_str());
        CHECK(Near(g_cfg.grass.density, 2.0f));
        k = Find("grass", "density");
        CHECK(k && k->source == kFromTune);
        ConfigOverrides().clear();
        LoadSettings(ini.c_str());
        CHECK(Near(g_cfg.grass.density, 3.5f));
    }
    // 4. out-of-range values are clamped
    {
        const std::wstring ini = WriteIni("eqgrass_t3.ini",
            "[grass]\r\ndensity = 99\r\ndensityDist = 1\r\ndensityFade = 0\r\nspread = 0\r\nwobble = 9\r\npatch = 0.1\r\n"
            "heightVar = 5\r\ntintVar = 5\r\nchunkSign = -7\r\n");
        LoadSettings(ini.c_str());
        const GrassSettings& g = g_cfg.grass;
        CHECK(Near(g.density, 8.0f) && Near(g.densityDist, 5.0f) && Near(g.densityFade, 0.05f) && Near(g.spread, 0.1f));
        CHECK(Near(g.wobble, 2.0f) && Near(g.patch, 0.5f) && Near(g.heightVar, 1.0f) && Near(g.tintVar, 0.5f) && g.chunkSign == -1);
        const std::wstring low = WriteIni("eqgrass_t4.ini", "[grass]\r\ndensity = 0.2\r\n");
        LoadSettings(low.c_str());
        CHECK(Near(g_cfg.grass.density, 1.0f));   // below 1 is 1: no way to thin the client's own grass
    }
    // 5. the original wind settings are untouched by all of the above
    {
        const std::wstring ini = WriteIni("eqgrass_t5.ini", "[grass]\r\ndensity = 4\r\nscale = 1.5\r\nlean = 0.2\r\n");
        LoadSettings(ini.c_str());
        CHECK(Near(g_cfg.grass.scale, 1.5f) && Near(g_cfg.grass.lean, 0.2f) && g_cfg.grass.enabled && g_cfg.grass.parting);
    }
    // 6. shipped ini files given on the command line
    for (int i = 1; i < argc; ++i)
    {
        const std::wstring ini = Wide(argv[i]);
        if (GetFileAttributesW(ini.c_str()) == INVALID_FILE_ATTRIBUTES) { std::printf("FAIL: %s not found\n", argv[i]); ++g_fail; continue; }
        LoadSettings(ini.c_str());
        const GrassSettings& g = g_cfg.grass;
        std::printf("%s: density %.2f, densityDist %.0f, fade %.2f, spread %.2f, alphaRef %d, chunkSign %d, enabled %d\n", argv[i],
                    g.density, g.densityDist, g.densityFade, g.spread, g.alphaRef, g.chunkSign, g.enabled);
        const ConfigKey* k = Find("grass", "density");
        CHECK(k && k->source == kFromIni);          // the key is in the file and was read from it
    }
    std::printf(g_fail ? "%d FAILED\n" : "all config checks passed\n", g_fail);
    return g_fail ? 1 : 0;
}
