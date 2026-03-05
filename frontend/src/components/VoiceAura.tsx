/**
 * VoiceAura — WebGL shader-based voice state visualizer.
 *
 * Uses the exact LiveKit Aura shader (turb() + SDF circle + 36-iteration
 * accumulation) to produce the same organic undulating ring aesthetic.
 *
 * Shader originally developed for Unicorn Studio.
 * Licensed under Polyform Non-Resale License 1.0.0
 * https://polyformproject.org/licenses/non-resale/1.0.0/
 * © 2026 UNCRN LLC
 *
 * State → uniform mapping derived from LiveKit's useAgentAudioVisualizerAura hook.
 */

import { useRef, useEffect } from "react";
import { useTurnState, TurnState } from "../contexts/TurnStateContext";
import { ShaderToy } from "../lib/ShaderToy";
import "./VoiceAura.scss";

// ── Shader source (LiveKit Aura, verbatim) ─────────────────────────────────

const SHADER = `
const float TAU = 6.283185;

vec2 randFibo(vec2 p) {
  p = fract(p * vec2(443.897, 441.423));
  p += dot(p, p.yx + 19.19);
  return fract((p.xx + p.yx) * p.xy);
}

vec3 Tonemap(vec3 x) {
  x *= 4.0;
  return x / (1.0 + x);
}

float luma(vec3 color) {
  return dot(color, vec3(0.299, 0.587, 0.114));
}

vec3 rgb2hsv(vec3 c) {
  vec4 K = vec4(0.0, -1.0 / 3.0, 2.0 / 3.0, -1.0);
  vec4 p = mix(vec4(c.bg, K.wz), vec4(c.gb, K.xy), step(c.b, c.g));
  vec4 q = mix(vec4(p.xyw, c.r), vec4(c.r, p.yzx), step(p.x, c.r));
  float d = q.x - min(q.w, q.y);
  float e = 1.0e-10;
  return vec3(abs(q.z + (q.w - q.y) / (6.0 * d + e)), d / (q.x + e), q.x);
}

vec3 hsv2rgb(vec3 c) {
  vec4 K = vec4(1.0, 2.0 / 3.0, 1.0 / 3.0, 3.0);
  vec3 p = abs(fract(c.xxx + K.xyz) * 6.0 - K.www);
  return c.z * mix(K.xxx, clamp(p - K.xxx, 0.0, 1.0), c.y);
}

float sdCircle(vec2 st, float r) {
  return length(st) - r;
}

float getSdf(vec2 st) {
  return sdCircle(st, uScale);
}

vec2 turb(vec2 pos, float t, float it) {
  mat2 rotation = mat2(0.6, -0.25, 0.25, 0.9);
  mat2 layerRotation = mat2(0.6, -0.8, 0.8, 0.6);
  float frequency = mix(2.0, 15.0, uFrequency);
  float amplitude = uAmplitude;
  float frequencyGrowth = 1.4;
  float animTime = t * 0.1 * uSpeed;
  const int LAYERS = 4;
  for (int i = 0; i < LAYERS; i++) {
    vec2 rotatedPos = pos * rotation;
    vec2 wave = sin(frequency * rotatedPos + float(i) * animTime + it);
    pos += (amplitude / frequency) * rotation[0] * wave;
    rotation *= layerRotation;
    amplitude *= mix(1.0, max(wave.x, wave.y), uVariance);
    frequency *= frequencyGrowth;
  }
  return pos;
}

const float ITERATIONS = 36.0;

void mainImage(out vec4 fragColor, in vec2 fragCoord) {
  vec2 uv = fragCoord / iResolution.xy;
  vec3 pp = vec3(0.0);
  vec3 bloom = vec3(0.0);
  float t = iTime * 0.5;
  vec2 pos = uv - 0.5;

  vec2 prevPos = turb(pos, t, 0.0 - 1.0 / ITERATIONS);
  float spacing = mix(1.0, TAU, uSpacing);

  for (float i = 1.0; i < ITERATIONS + 1.0; i++) {
    float iter = i / ITERATIONS;
    vec2 st = turb(pos, t, iter * spacing);
    float d = abs(getSdf(st));
    float pd = distance(st, prevPos);
    prevPos = st;
    float dynamicBlur = exp2(pd * 2.0 * 1.4426950408889634) - 1.0;
    float ds = smoothstep(0.0, uBlur * 0.05 + max(dynamicBlur * uSmoothing, 0.001), d);

    vec3 color = uColor;
    if (uColorShift > 0.01) {
      vec3 hsv = rgb2hsv(color);
      hsv.x = fract(hsv.x + (1.0 - iter) * uColorShift * 0.3);
      color = hsv2rgb(hsv);
    }

    float invd = 1.0 / max(d + dynamicBlur, 0.001);
    pp += (ds - 1.0) * color;
    bloom += clamp(invd, 0.0, 250.0) * color;
  }

  pp *= 1.0 / ITERATIONS;

  bloom = bloom / (bloom + 2e4);
  vec3 color = (-pp + bloom * 3.0 * uBloom) * 1.2;
  color += (randFibo(fragCoord).x - 0.5) / 255.0;
  color = Tonemap(color);
  float alpha = luma(color) * uMix;
  fragColor = vec4(color * uMix, alpha);
}`;

// ── State → uniform config ──────────────────────────────────────────────────

interface Cfg {
  speed: number;
  amplitude: number;
  frequency: number;
  scale: number;
  brightness: number;
  bloom: number;
  r: number; g: number; b: number;
}

// Values match LiveKit's useAgentAudioVisualizerAura hook, with color + bloom per state.
// uScale is 0.7× the original LiveKit values (canvas 400px vs 280px, ring pixel size unchanged).
const STATE_CFG: Record<string, Cfg> = {
  idle:         { speed: 10, amplitude: 1.2,  frequency: 0.40, scale: 0.14, brightness: 1.2, bloom: 0.6, r: 0.31, g: 0.76, b: 0.97 },
  speaking:     { speed: 14, amplitude: 0.75, frequency: 1.25, scale: 0.21, brightness: 1.8, bloom: 1.2, r: 0.0,  g: 0.90, b: 1.0  },
  listening:    { speed: 20, amplitude: 1.0,  frequency: 0.70, scale: 0.21, brightness: 2.0, bloom: 0.8, r: 1.0,  g: 0.72, b: 0.30 },
  thinking:     { speed: 30, amplitude: 0.5,  frequency: 1.00, scale: 0.21, brightness: 1.5, bloom: 0.6, r: 0.70, g: 0.53, b: 1.0  },
  disconnected: { speed:  5, amplitude: 0.8,  frequency: 0.30, scale: 0.13, brightness: 0.5, bloom: 0.2, r: 0.40, g: 0.40, b: 0.40 },
};

function stateKey(s: TurnState): string {
  if (s === TurnState.MODEL_SPEAKING) return "speaking";
  if (s === TurnState.USER_SPEAKING)  return "listening";
  if (
    s === TurnState.MODEL_THINKING ||
    s === TurnState.WAITING_TOOLS  ||
    s === TurnState.TOOL_EXECUTING
  ) return "thinking";
  if (s === TurnState.IDLE) return "idle";
  return "disconnected";
}

const lerp = (a: number, b: number, t: number) => a + (b - a) * t;

// ── Component ───────────────────────────────────────────────────────────────

export function VoiceAura() {
  const { currentState } = useTurnState();
  const keyRef = useRef("idle");

  useEffect(() => {
    keyRef.current = stateKey(currentState);
  }, [currentState]);

  // Uniform objects — created once, values mutated in-place by the lerp loop
  const uniforms = useRef({
    uSpeed:      { type: "1f",  value: 10   as number },
    uAmplitude:  { type: "1f",  value: 1.2  as number },
    uFrequency:  { type: "1f",  value: 0.4  as number },
    uScale:      { type: "1f",  value: 0.2  as number },
    uMix:        { type: "1f",  value: 1.0  as number },
    uBloom:      { type: "1f",  value: 0.0  as number },
    uBlur:       { type: "1f",  value: 0.2  as number },
    uColorShift: { type: "1f",  value: 0.3  as number },
    uVariance:   { type: "1f",  value: 0.1  as number },
    uSmoothing:  { type: "1f",  value: 1.0  as number },
    uSpacing:    { type: "1f",  value: 0.5  as number },
    uColor:      { type: "3fv", value: [0.31, 0.76, 0.97] as number[] },
  }).current;

  // Lerp loop — smoothly interpolates all uniforms toward target config (~0.4s transition)
  useEffect(() => {
    let cfg: Cfg = { ...STATE_CFG.idle };
    let timeAccum = 0;
    let prevTs = 0;
    let raf = 0;

    function loop(ts: number) {
      const dt = prevTs ? Math.min((ts - prevTs) / 1000, 0.05) : 0;
      prevTs = ts;
      timeAccum += dt;

      const f = 0.035; // lerp factor per frame (~0.7s transition)
      const target = STATE_CFG[keyRef.current];

      cfg.speed      = lerp(cfg.speed,      target.speed,      f);
      cfg.amplitude  = lerp(cfg.amplitude,  target.amplitude,  f);
      cfg.frequency  = lerp(cfg.frequency,  target.frequency,  f);
      cfg.scale      = lerp(cfg.scale,      target.scale,      f);
      cfg.bloom      = lerp(cfg.bloom,      target.bloom,      f);
      cfg.r          = lerp(cfg.r,          target.r,          f);
      cfg.g          = lerp(cfg.g,          target.g,          f);
      cfg.b          = lerp(cfg.b,          target.b,          f);

      // Thinking state: pulse brightness between 0.5 and 2.5 (like LiveKit's mirror animation)
      let brightness = target.brightness;
      if (keyRef.current === "thinking") {
        brightness = 1.5 + Math.sin(timeAccum * 4.0) * 1.0;
      }
      cfg.brightness = lerp(cfg.brightness, brightness, f);

      // Mutate uniform values in-place — ShaderToy reads these each draw frame
      uniforms.uSpeed.value     = cfg.speed;
      uniforms.uAmplitude.value = cfg.amplitude;
      uniforms.uFrequency.value = cfg.frequency;
      uniforms.uScale.value     = cfg.scale;
      uniforms.uMix.value       = cfg.brightness;
      uniforms.uBloom.value     = cfg.bloom;
      const col = uniforms.uColor.value as number[];
      col[0] = cfg.r;
      col[1] = cfg.g;
      col[2] = cfg.b;

      raf = requestAnimationFrame(loop);
    }

    prevTs = performance.now();
    raf = requestAnimationFrame(loop);
    return () => cancelAnimationFrame(raf);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []); // run once — reads keyRef each frame

  return (
    <div className="voice-aura" aria-hidden="true">
      <ShaderToy fs={SHADER} uniforms={uniforms} />
    </div>
  );
}
