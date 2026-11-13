"use client";
/* The living backdrop behind every screen (13 Nov 2026, the owner: "make jarvis look and feel much better").
   One full-screen WebGL canvas: slow domain-warped light fields in the palette's colours, a faint lean toward the
   pointer, a touch of grain. Soft over the light canvas, a glow over the dark one; colours come from CSS variables, so it
   follows the Mac setting. It renders at reduced resolution, at most ~30 frames a second, stops while the window is
   hidden, and draws one still frame when the Mac asks for reduced motion. It also feeds the cursor position to the
   card under the pointer (--mx/--my), which the card spotlight in globals.css reads. */
import * as React from "react";

const VERT = `attribute vec2 p; void main(){ gl_Position = vec4(p, 0.0, 1.0); }`;
const FRAG = `precision mediump float;
uniform vec2 r; uniform float t; uniform vec3 base; uniform vec3 c1; uniform vec3 c2; uniform vec3 c3; uniform float k; uniform vec2 m;
vec3 mod289(vec3 x){ return x - floor(x * (1.0 / 289.0)) * 289.0; }
vec2 mod289(vec2 x){ return x - floor(x * (1.0 / 289.0)) * 289.0; }
vec3 permute(vec3 x){ return mod289(((x * 34.0) + 1.0) * x); }
float snoise(vec2 v){
  const vec4 C = vec4(0.211324865405187, 0.366025403784439, -0.577350269189626, 0.024390243902439);
  vec2 i = floor(v + dot(v, C.yy)); vec2 x0 = v - i + dot(i, C.xx);
  vec2 i1 = (x0.x > x0.y) ? vec2(1.0, 0.0) : vec2(0.0, 1.0);
  vec4 x12 = x0.xyxy + C.xxzz; x12.xy -= i1; i = mod289(i);
  vec3 p = permute(permute(i.y + vec3(0.0, i1.y, 1.0)) + i.x + vec3(0.0, i1.x, 1.0));
  vec3 mm = max(0.5 - vec3(dot(x0, x0), dot(x12.xy, x12.xy), dot(x12.zw, x12.zw)), 0.0); mm = mm * mm; mm = mm * mm;
  vec3 x = 2.0 * fract(p * C.www) - 1.0; vec3 h = abs(x) - 0.5; vec3 ox = floor(x + 0.5); vec3 a0 = x - ox;
  mm *= 1.79284291400159 - 0.85373472095314 * (a0 * a0 + h * h);
  vec3 g; g.x = a0.x * x0.x + h.x * x0.y; g.yz = a0.yz * x12.xz + h.yz * x12.yw; return 130.0 * dot(mm, g);
}
void main(){
  vec2 uv = gl_FragCoord.xy / r; float asp = r.x / r.y; vec2 p = vec2(uv.x * asp, uv.y);
  float tt = t * 0.028;
  vec2 q = vec2(snoise(p * 0.85 + vec2(tt, -tt * 0.7)), snoise(p * 0.85 + vec2(-tt * 0.8, tt * 0.5) + 3.1));
  vec2 w = p + 0.55 * q;
  float a = smoothstep(-0.25, 0.95, snoise(w * 0.70 + vec2(tt * 0.6, 0.0)));
  float b = smoothstep(-0.15, 1.00, snoise(w * 0.55 + vec2(5.2, tt * 0.5)));
  float c = smoothstep(-0.05, 1.00, snoise(w * 0.48 + vec2(-tt * 0.4, 9.7)));
  vec3 col = base;
  col = mix(col, c1, k * a * (0.35 + 0.65 * uv.y));
  col = mix(col, c2, k * 0.85 * b * (0.25 + 0.75 * uv.x));
  col = mix(col, c3, k * 0.75 * c * (1.0 - uv.x) * (1.0 - 0.5 * uv.y));
  vec2 d = (uv - m) * vec2(asp, 1.0);
  col = mix(col, c1, k * 0.55 * exp(-dot(d, d) * 5.0));
  float v = smoothstep(1.25, 0.35, length((uv - 0.5) * vec2(asp, 1.0)));
  col = mix(col, base, 0.35 * (1.0 - v));
  float g = fract(sin(dot(gl_FragCoord.xy + fract(t) * 91.0, vec2(12.9898, 78.233))) * 43758.5453);
  col += (g - 0.5) * 0.016;
  gl_FragColor = vec4(col, 1.0);
}`;

function rgb(css: string): [number, number, number] {
  const el = document.createElement("span");
  el.style.color = css; el.style.display = "none"; document.body.appendChild(el);
  const m = getComputedStyle(el).color.match(/[\d.]+/g) || ["0", "0", "0"];
  el.remove();
  return [Number(m[0]) / 255, Number(m[1]) / 255, Number(m[2]) / 255];
}

export function Ambient() {
  const ref = React.useRef<HTMLCanvasElement>(null);
  React.useEffect(() => {
    const cv = ref.current;
    if (!cv) return;
    const gl = cv.getContext("webgl", { antialias: false, premultipliedAlpha: false, powerPreference: "low-power" });
    if (!gl) { cv.style.display = "none"; return; }
    const sh = (type: number, src: string) => { const s = gl.createShader(type)!; gl.shaderSource(s, src); gl.compileShader(s); return s; };
    const pr = gl.createProgram()!;
    gl.attachShader(pr, sh(gl.VERTEX_SHADER, VERT)); gl.attachShader(pr, sh(gl.FRAGMENT_SHADER, FRAG)); gl.linkProgram(pr);
    if (!gl.getProgramParameter(pr, gl.LINK_STATUS)) { cv.style.display = "none"; return; }
    gl.useProgram(pr);
    const buf = gl.createBuffer(); gl.bindBuffer(gl.ARRAY_BUFFER, buf);
    gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1, -1, 3, -1, -1, 3]), gl.STATIC_DRAW);
    const loc = gl.getAttribLocation(pr, "p"); gl.enableVertexAttribArray(loc); gl.vertexAttribPointer(loc, 2, gl.FLOAT, false, 0, 0);
    const U = (n: string) => gl.getUniformLocation(pr, n);
    const u = { r: U("r"), t: U("t"), base: U("base"), c1: U("c1"), c2: U("c2"), c3: U("c3"), k: U("k"), m: U("m") };

    const palette = () => {
      const cs = getComputedStyle(document.documentElement);
      const v = (n: string, f: string) => (cs.getPropertyValue(n).trim() || f);
      gl.uniform3fv(u.base, rgb(v("--canvas", "#f5f6f8")));
      gl.uniform3fv(u.c1, rgb(v("--amb-1", "#2563c9")));
      gl.uniform3fv(u.c2, rgb(v("--amb-2", "#0ea5a4")));
      gl.uniform3fv(u.c3, rgb(v("--amb-3", "#7c5cff")));
      gl.uniform1f(u.k, parseFloat(v("--amb-k", "0.14")) || 0.14);
    };
    const SCALE = 0.5;
    const size = () => {
      const w = Math.max(1, Math.round(innerWidth * SCALE)), h = Math.max(1, Math.round(innerHeight * SCALE));
      if (cv.width !== w || cv.height !== h) { cv.width = w; cv.height = h; gl.viewport(0, 0, w, h); }
      gl.uniform2f(u.r, w, h);
    };
    const still = matchMedia("(prefers-reduced-motion: reduce)");
    let mx = 0.5, my = 0.6, tx = 0.5, ty = 0.6, raf = 0, last = 0;
    const t0 = performance.now() - 40000;
    const draw = (now: number) => {
      mx += (tx - mx) * 0.04; my += (ty - my) * 0.04;
      gl.uniform1f(u.t, (now - t0) / 1000); gl.uniform2f(u.m, mx, my);
      gl.drawArrays(gl.TRIANGLES, 0, 3);
    };
    const loop = (now: number) => {
      raf = requestAnimationFrame(loop);
      if (document.hidden || now - last < 33) return;
      last = now; draw(now);
    };
    const start = () => { cancelAnimationFrame(raf); size(); palette(); if (still.matches) draw(performance.now()); else raf = requestAnimationFrame(loop); };
    const onMove = (e: PointerEvent) => {
      tx = e.clientX / innerWidth; ty = 1 - e.clientY / innerHeight;
      const card = (e.target as Element | null)?.closest?.(".glass-card") as HTMLElement | null;
      if (card) { const b = card.getBoundingClientRect(); card.style.setProperty("--mx", `${e.clientX - b.left}px`); card.style.setProperty("--my", `${e.clientY - b.top}px`); }
    };
    const onResize = () => { size(); if (still.matches) draw(performance.now()); };
    const mo = new MutationObserver(() => { palette(); if (still.matches) draw(performance.now()); });
    mo.observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme", "data-scene"] });
    addEventListener("resize", onResize); addEventListener("pointermove", onMove, { passive: true });
    still.addEventListener("change", start);
    start();
    return () => {
      cancelAnimationFrame(raf); mo.disconnect(); removeEventListener("resize", onResize); removeEventListener("pointermove", onMove);
      still.removeEventListener("change", start); gl.getExtension("WEBGL_lose_context")?.loseContext();
    };
  }, []);
  return <canvas ref={ref} aria-hidden className="jv-ambient" />;
}
