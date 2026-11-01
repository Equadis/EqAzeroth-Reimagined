sampler2D sNoise  : register(s0);
float4 gInv0  : register(c0);
float4 gInv1  : register(c1);
float4 gInv2  : register(c2);
float4 gInv3  : register(c3);
float4 gSun   : register(c4);
float4 gLit   : register(c5);
float4 gShade : register(c6);
float4 gP     : register(c7);
float4 gL0    : register(c8);
float4 gL1    : register(c9);
float4 gL2    : register(c10);
float4 gL3    : register(c11);
float4 gHaze  : register(c12);
float4 gMisc  : register(c13);
float4 gS[32] : register(c14);
float4 gQ     : register(c46);
struct I { float2 uv : TEXCOORD0; };
sampler2D sShapes : register(s1);
// EqShaders Stage 1 volumetric slab (v5: the puff lattice is warped by the medium billows and crossed with a 45-degree copy; offsets stay whole-tile so the field is seamless and anchored): base gP.x yards over the camera (layerHeight), 1100 yards thick, 8 samples along the ray.
float4 main(I i) : COLOR
{
    float4 h = (i.uv.x * 2.0 - 1.0) * gInv0 + (1.0 - i.uv.y * 2.0) * gInv1 + 0.5 * gInv2 + gInv3;
    float3 d = normalize(h.xyz / h.w);
    float fade = smoothstep(0.0, gMisc.x, d.z);
    [branch] if (fade <= 0.0)
        return float4(0.0, 0.0, 0.0, 0.0);
    if (gP.w > 1.5)
        return float4(1.0, 1.0, 1.0, 1.0);
    float soft = gMisc.y;
    float edge = gP.y * (1.0 + soft * 3.0);
    float hb = gP.x;
    float th = 1100.0;
    float dz = max(d.z, 0.03);
    float t0 = hb / dz;
    float len = min(th / dz, 5000.0);
    float dt = len * 0.125;
    float jit = 0.5;
    float2 pm = d.xy * (t0 + len * 0.5);
    float cm = max(0.0, gSun.w + (tex2Dlod(sNoise, float4(pm * gL3.z + gL3.xy, 0.0, 3.0)).a - 0.5) * 0.6);
    float4 tm0 = tex2Dlod(sNoise, float4(pm * gL0.z + gL0.xy, 0.0, 2.0));
    float f = 0.5 + (tm0.r * 0.45 + tm0.b * 0.55 - 0.5) * 1.3;
    float lod0 = clamp(log2(max(dt * gL0.z * 256.0, 1.0)) + 0.3, 0.0, 6.0);
    float lod1 = clamp(log2(max(dt * gL1.z * 256.0, 1.0)) + 0.3, 0.0, 6.0);
    float trans = 1.0, lit = 0.0;
    [unroll] for (int n = 0; n < 8; ++n)
    {
        float hh = (float(n) + jit) * 0.125;
        float3 q = d * (t0 + hh * len);
        float prof = smoothstep(0.0, 0.25, hh) * (1.0 - smoothstep(0.55, 1.0, hh));
        float2 sh = float2(0.12, 0.07) * hh;
        float2 v1 = q.xy * gL1.z + gL1.xy + sh * 1.7;
        float  a1 = tex2Dlod(sNoise, float4(float2(v1.x + v1.y, v1.y - v1.x) + float2(0.19, 0.43), 0.0, lod1 + 0.5)).g;
        float2 u0 = q.xy * gL0.z + gL0.xy + sh + float2(0.6, 0.8) * (a1 - 0.5) * 0.10;
        float4 a0 = tex2Dlod(sNoise, float4(u0, 0.0, lod0));
        float  pb = tex2Dlod(sNoise, float4(float2(u0.x + u0.y, u0.y - u0.x) + float2(0.31, 0.57), 0.0, lod0 + 0.5)).b;
        float  pf = a0.b * 0.55 + pb * 0.45;
        float  fs = 0.5 + (lerp(a0.r * 0.65 + a1 * 0.35, a0.r * 0.45 + pf * 0.55, gMisc.z) - 0.5) * (1.0 + soft * 0.6);
        float  dn = saturate((fs - (1.0 - cm) + 0.18 * (1.0 - prof)) / edge);
        float  ls = tex2Dlod(sNoise, float4(u0 + gSun.xy * 0.12, 0.0, lod0 + 1.0)).r;
        float  ds = saturate((ls - (1.0 - cm)) / edge) * prof;
        float  a = 1.0 - exp(-dn * dt * 7.0 / th);
        float  lstep = lerp(0.22, 1.0, hh) * exp(-3.0 * ds);
        lit += lstep * a * trans;
        trans *= 1.0 - a;
    }
    float dens = 1.0 - trans;
    float L = lit / max(dens, 1e-3);
    float thick = dens;

    float lump = smoothstep(0.2, 0.8, f);
    float shapeA = 0.0, near = 0.0, shapeThick = 0.0;
    [unroll] for (int n = 0; n < 8; ++n)   // unrolled: a loop indexed the constants by a chain of compares
    {
        float4 s0 = gS[n * 4], s1 = gS[n * 4 + 1], s2 = gS[n * 4 + 2], s3 = gS[n * 4 + 3];
        float  dn = dot(d, s0.xyz);
        float2 s  = float2(dot(d, s1.xyz), dot(d, s2.xyz)) * (s0.w / max(dn, 0.05));
        float2 cuv = clamp(s3.xy + (float2(s.x, -s.y) * 0.5 + 0.5) * 0.25, s3.xy + 0.002, s3.xy + 0.248);
        float  sd = tex2Dlod(sShapes, float4(cuv, 0.0, 0.5)).r;
        float  e0 = (sd - 0.5) * 0.75;   // sheet units from the outline, + inside: 0.375 reach either way
        float  e  = e0 + (f - 0.5) * s3.z;
        float  w  = s1.w * step(0.05, dn);
        shapeA = 1.0 - (1.0 - shapeA) * (1.0 - saturate(e / gQ.x) * w * lerp(0.55, 1.0, lump));
        shapeThick = max(shapeThick, saturate(e / (gQ.x * 3.0)) * w * lump);
        float  cut = tex2Dlod(sShapes, float4(cuv, 0.0, 0.0)).a - 0.5;
        near = max(near, w * smoothstep(-0.05, 0.35, cut + s3.w + (f - 0.5) * 0.12));
    }
    dens *= 1.0 - gQ.y * near;
    L = lit / max(dens, 1e-3);
    thick = max(dens, shapeThick);

    float3 col = lerp(gShade.rgb, gLit.rgb, saturate(L));
    float fwd = pow(saturate(dot(d, gSun.xyz)), 8.0);
    col += gLit.rgb * fwd * (1.0 - max(dens, shapeA)) * 0.8;
    float hz = saturate(t0 * gHaze.w);
    hz *= hz;
    col = lerp(col, gHaze.rgb, hz * 0.8);
    col *= 1.0 - gQ.w * (0.3 + 0.3 * thick);
    float alpha = (1.0 - (1.0 - dens * gLit.w) * (1.0 - shapeA * gQ.z)) * fade * (1.0 - hz * 0.5);
    if (gP.w > 0.0)
        return float4(shapeA * fade, 0.0, dens * fade, 1.0);
    return float4(col * alpha, alpha * (1.0 - gShade.w * (1.0 - gQ.w * 0.8) * (1.0 - saturate(L))));
}
