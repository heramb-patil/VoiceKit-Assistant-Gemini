/**
 * Minimal WebGL fragment shader runner — ShaderToy-compatible API.
 * Supports: iTime, iResolution, custom uniforms.
 *
 * The `uniforms` object is read every frame by reference, so the caller
 * can mutate values in-place from a rAF loop without triggering re-renders.
 */

import { useRef, useEffect, type CSSProperties } from "react";

export type UniformValue = { type: string; value: number | number[] };

interface Props {
  fs: string;
  uniforms?: Record<string, UniformValue>;
  style?: CSSProperties;
  dpr?: number;
}

const VS = `attribute vec3 aPos; void main() { gl_Position = vec4(aPos, 1.0); }`;
const FS_WRAP = `\nvoid main() { vec4 c = vec4(0.0,0.0,0.0,1.0); mainImage(c, gl_FragCoord.xy); gl_FragColor = c; }`;

const GLSL_TYPE: Record<string, string> = {
  "1f": "float", "2f": "vec2",  "3f": "vec3",  "4f": "vec4",
  "1i": "int",   "2i": "ivec2", "3i": "ivec3", "4i": "ivec4",
  "1fv":"float", "2fv":"vec2",  "3fv":"vec3",  "4fv":"vec4",
};

function applyUniform(
  gl: WebGLRenderingContext,
  loc: WebGLUniformLocation,
  type: string,
  val: number | number[],
) {
  if (typeof val === "number") return gl.uniform1f(loc, val);
  switch (type) {
    case "2fv": return gl.uniform2fv(loc, val);
    case "3fv": return gl.uniform3fv(loc, val);
    case "4fv": return gl.uniform4fv(loc, val);
    default:    return gl.uniform1fv(loc, val);
  }
}

export function ShaderToy({ fs, uniforms = {}, style, dpr }: Props) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  // Keep a stable ref to the uniforms object — caller mutates values in-place
  const uniformsRef = useRef(uniforms);
  uniformsRef.current = uniforms;

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;

    const pixelRatio = dpr ?? window.devicePixelRatio ?? 1;

    const gl = (
      canvas.getContext("webgl", { alpha: true, premultipliedAlpha: false }) ??
      canvas.getContext("experimental-webgl", { alpha: true, premultipliedAlpha: false })
    ) as WebGLRenderingContext | null;
    if (!gl) { console.error("[ShaderToy] WebGL not supported"); return; }

    // Build full fragment shader — inject uniform declarations
    const uniDecls = Object.entries(uniformsRef.current)
      .map(([n, { type }]) => `uniform ${GLSL_TYPE[type] ?? "float"} ${n};`)
      .join("\n");
    const fullFS = [
      "precision highp float;",
      "uniform vec2 iResolution;",
      "uniform float iTime;",
      uniDecls,
      fs,
      FS_WRAP,
    ].join("\n");

    function compile(type: number, src: string): WebGLShader | null {
      const s = gl!.createShader(type);
      if (!s) return null;
      gl!.shaderSource(s, src);
      gl!.compileShader(s);
      if (!gl!.getShaderParameter(s, gl!.COMPILE_STATUS)) {
        console.error("[ShaderToy] Shader compile error:", gl!.getShaderInfoLog(s));
        return null;
      }
      return s;
    }

    const vs = compile(gl.VERTEX_SHADER, VS);
    const frag = compile(gl.FRAGMENT_SHADER, fullFS);
    if (!vs || !frag) return;

    const prog = gl.createProgram()!;
    gl.attachShader(prog, vs);
    gl.attachShader(prog, frag);
    gl.linkProgram(prog);
    if (!gl.getProgramParameter(prog, gl.LINK_STATUS)) {
      console.error("[ShaderToy] Link error:", gl.getProgramInfoLog(prog));
      return;
    }
    gl.useProgram(prog);

    // Fullscreen quad
    const buf = gl.createBuffer()!;
    gl.bindBuffer(gl.ARRAY_BUFFER, buf);
    gl.bufferData(
      gl.ARRAY_BUFFER,
      new Float32Array([1, 1, 0, -1, 1, 0, 1, -1, 0, -1, -1, 0]),
      gl.STATIC_DRAW,
    );
    const aPos = gl.getAttribLocation(prog, "aPos");
    gl.enableVertexAttribArray(aPos);
    gl.vertexAttribPointer(aPos, 3, gl.FLOAT, false, 0, 0);

    const timeLoc = gl.getUniformLocation(prog, "iTime");
    const resLoc  = gl.getUniformLocation(prog, "iResolution");
    const uniLocs: Record<string, WebGLUniformLocation | null> = {};
    for (const name of Object.keys(uniformsRef.current)) {
      uniLocs[name] = gl.getUniformLocation(prog, name);
    }

    let timer = 0, lastTs = 0, raf = 0;

    function resize() {
      const w = Math.floor(canvas!.clientWidth  * pixelRatio);
      const h = Math.floor(canvas!.clientHeight * pixelRatio);
      if (w === 0 || h === 0) return; // not yet laid out
      if (canvas!.width !== w || canvas!.height !== h) {
        canvas!.width  = w;
        canvas!.height = h;
        gl!.viewport(0, 0, w, h);
      }
    }

    function draw(ts: number) {
      const dt = lastTs ? Math.min((ts - lastTs) / 1000, 0.05) : 0;
      lastTs = ts;
      timer += dt;
      resize();
      gl!.clearColor(0, 0, 0, 0);
      gl!.clear(gl!.COLOR_BUFFER_BIT);
      if (resLoc) gl!.uniform2f(resLoc, canvas!.width, canvas!.height);
      if (timeLoc) gl!.uniform1f(timeLoc, timer);
      // Read latest mutated values from uniformsRef each frame
      for (const [name, { type, value }] of Object.entries(uniformsRef.current)) {
        const loc = uniLocs[name];
        if (loc) applyUniform(gl!, loc, type, value);
      }
      gl!.drawArrays(gl!.TRIANGLE_STRIP, 0, 4);
      raf = requestAnimationFrame(draw);
    }

    resize();
    raf = requestAnimationFrame(draw);

    const ro = new ResizeObserver(resize);
    ro.observe(canvas);

    return () => {
      cancelAnimationFrame(raf);
      ro.disconnect();
      // Do NOT call loseContext() — it permanently destroys the canvas WebGL context
      // in React StrictMode (double-invocation), causing the second mount to get null.
    };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []); // mount once — uniforms are read by ref each frame

  return (
    <canvas
      ref={canvasRef}
      style={{ display: "block", width: "100%", height: "100%", ...style }}
    />
  );
}
