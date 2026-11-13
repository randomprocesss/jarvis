"use client";
/* The machine that follows the day (13 Nov 2026, the look plan, phase 2).
   84 frames of the assembly film (/media/frames/f_001.webp = all parts apart ... f_084.webp = complete), drawn on a canvas.
   `progress` 0..1 says how assembled it is; the drawing glides there, so when he clears something the parts move in.
   Frames load key ones first, then the rest; until a frame is loaded the nearest loaded one is drawn. Stops while hidden
   or off-screen; reduced motion jumps straight to the target. */
import { useEffect, useRef } from "react";
import { WH, useWH } from "@/lib/wh";

const N = 84;
const src = (i: number) => `/media/frames/f_${String(i + 1).padStart(3, "0")}.webp?v=1600`; // ?v: a new set gets a new address, never a cached old frame

export default function Assembly({ progress, className, anchor = 0.62, fit = "cover", from = 0, speed = 1.4, onClick, title }:
  { progress: number; className?: string; anchor?: number; fit?: "cover" | "fill"; from?: number; speed?: number; onClick?: () => void; title?: string }) {
  const cv = useRef<HTMLCanvasElement>(null);
  const target = useRef(progress);
  target.current = Math.max(0, Math.min(1, progress));

  useWH(["me"]);
  const media = !!WH.me.media;                          // the frames are an optional pack: without it nothing is asked for
  useEffect(() => {
    const el = cv.current;
    if (!el || !media) return;
    const ctx = el.getContext("2d");
    if (!ctx) return;
    const imgs: (HTMLImageElement | null)[] = Array(N).fill(null);
    const still = typeof matchMedia !== "undefined" && matchMedia("(prefers-reduced-motion: reduce)").matches;
    let cur = still ? target.current : from, raf = 0, last = 0, seen = true, drawn = -1, alive = true;
    const order = [0, N - 1, Math.round(N * 0.25), Math.round(N * 0.5), Math.round(N * 0.75)];
    for (let i = 0; i < N; i++) if (!order.includes(i)) order.push(i);
    let k = 0;
    const loadNext = () => {
      if (!alive || k >= order.length) return;
      const i = order[k++], im = new Image();
      im.decoding = "async";
      im.onload = () => { imgs[i] = im; drawn = -1; loadNext(); };
      im.onerror = () => loadNext();
      im.src = src(i);
    };
    for (let j = 0; j < 4; j++) loadNext();
    const nearest = (i: number) => {
      for (let d = 0; d < N; d++) {
        if (imgs[i - d]) return imgs[i - d];
        if (imgs[i + d]) return imgs[i + d];
      }
      return null;
    };
    const size = () => {
      const r = el.getBoundingClientRect(), dpr = Math.min(2, window.devicePixelRatio || 1);
      const w = Math.max(1, Math.round(r.width * dpr)), h = Math.max(1, Math.round(r.height * dpr));
      if (el.width !== w || el.height !== h) { el.width = w; el.height = h; drawn = -1; }
    };
    const draw = () => {
      const i = Math.round(cur * (N - 1));
      const im = nearest(i);
      if (!im || i === drawn) return;
      const W = el.width, H = el.height;
      ctx.clearRect(0, 0, W, H);
      ctx.imageSmoothingEnabled = true;
      ctx.imageSmoothingQuality = "high";
      if (fit === "fill") ctx.drawImage(im, 0, 0, W, H);
      else {
        const s = Math.max(W / im.naturalWidth, H / im.naturalHeight), dw = im.naturalWidth * s, dh = im.naturalHeight * s;
        ctx.drawImage(im, (W - dw) * anchor, (H - dh) * 0.55, dw, dh);
      }
      drawn = imgs[i] ? i : -1;
    };
    const tick = (now: number) => {
      raf = requestAnimationFrame(tick);
      if (!seen || document.hidden) return;
      const dt = last ? Math.min(0.1, (now - last) / 1000) : 0;
      last = now;
      const t = target.current;
      cur = still ? t : Math.abs(t - cur) < 0.002 ? t : cur + (t - cur) * Math.min(1, dt * speed);
      draw();
    };
    size();
    const ro = new ResizeObserver(() => { size(); draw(); });
    ro.observe(el);
    const io = new IntersectionObserver(([e]) => { seen = e.isIntersecting; last = 0; }, { threshold: 0 });
    io.observe(el);
    raf = requestAnimationFrame(tick);
    return () => { alive = false; cancelAnimationFrame(raf); ro.disconnect(); io.disconnect(); };
  }, [anchor, fit, from, speed, media]);

  return onClick
    ? <canvas ref={cv} role="button" tabIndex={0} aria-label={title} title={title} className={className} onClick={onClick}
        onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); onClick(); } }} />
    : <canvas ref={cv} aria-hidden className={className} />;
}
