"use client";
/* Jarvis core (landing page, 2 Oct): the JarvisOrb from jarvis-ai-web-animation (MIT, Three.js/WebGL; the owner picked it,
   "take orb from here") inside a quiet ticked HUD ring. It follows Jarvis's state: idle; listening (more energy, a little
   larger); thinking (the orb's own thinking mood); speaking (brighter, faster). The orb pauses itself off-screen and has a
   CSS fallback under reduced motion. Click = talk (push-to-talk in the app). The earlier canvas orb: Core.tsx.bak-20261002-2120. */
import dynamic from "next/dynamic";

export type CoreState = "idle" | "listening" | "thinking" | "speaking";

// WebGL only in the browser (the page is a static export)
const JarvisOrb = dynamic<any>(() => import("jarvis-ai-web-animation").then((m: any) => m.JarvisOrb), { ssr: false });

const STATES: Record<CoreState, any> = {
  idle: "idle",
  thinking: "thinking",
  listening: { energy: 1.4, rotationSpeed: 0.6, particleSpeed: 1.6, shellRadius: 1.08, ringSpread: 1.0, filamentOpacity: 0.7, coreScale: 1.1, bloom: 0.9 },
  speaking: { energy: 1.75, rotationSpeed: 0.8, particleSpeed: 2.1, shellRadius: 1.12, ringSpread: 1.08, filamentOpacity: 0.85, coreScale: 1.18, bloom: 1.1 },
};

// 13 Nov: at small sizes the idle orb is too faint to read; a little more energy, core and bloom
const SMALL_IDLE = { energy: 1.15, rotationSpeed: 0.45, particleSpeed: 1.2, shellRadius: 1.0, ringSpread: 1.0, filamentOpacity: 0.7, coreScale: 1.14, bloom: 1.0 };

export default function Core({ state, size = 300, reduce, onClick, label }: { state: CoreState; size?: number; reduce: boolean; onClick?: () => void; label?: string }) {
  const ticks = Array.from({ length: 48 }, (_, i) => i);
  const live = state !== "idle";
  return (
    <div className="relative select-none" style={{ width: size, height: size }}>
      <svg viewBox="-100 -100 200 200" className={reduce ? "absolute inset-0" : "absolute inset-0 jv-spin-slow"} aria-hidden>
        {ticks.map((i) => {
          const a = (i / 48) * Math.PI * 2, L = i % 4 === 0 ? 6 : 3;
          return <line key={i} x1={Math.cos(a) * 92} y1={Math.sin(a) * 92} x2={Math.cos(a) * (92 - L)} y2={Math.sin(a) * (92 - L)} stroke="#9fe7ff" strokeOpacity={i % 4 === 0 ? 0.4 : 0.14} strokeWidth={i % 4 === 0 ? 0.8 : 0.5} />;
        })}
        <circle r="96" fill="none" stroke="#9fe7ff" strokeOpacity=".12" strokeWidth=".5" />
      </svg>
      <svg viewBox="-100 -100 200 200" className={reduce ? "absolute inset-0" : "absolute inset-0 jv-spin-rev"} aria-hidden>
        <circle r="84" fill="none" stroke="#9fe7ff" strokeOpacity={live ? 0.5 : 0.22} strokeWidth="1" strokeDasharray="38 490" strokeLinecap="round" />
      </svg>
      <button type="button" onClick={onClick} aria-label={label || "Talk to Jarvis"} title={label || "Talk to Jarvis"}
        className="absolute inset-[10%] rounded-full outline-none focus-visible:ring-2 focus-visible:ring-[#9fe7ff]/60">
        <span className="block h-full w-full">
          {/* 13 Nov: the library's preset must match the pixel size ("avatar" is tuned for 64-128 px: higher DPR, fewer, larger
              particles); "panel" at 50 px rendered a murky dot */}
          <JarvisOrb size={size <= 150 ? "avatar" : size >= 420 ? "hero" : "panel"} state={size <= 150 && state === "idle" ? SMALL_IDLE : STATES[state]} palette="cyan"
            quality={size <= 150 ? "high" : "auto"} breathing={state === "idle"} ariaLabel={label || "Jarvis"} style={{ width: "100%", height: "100%" }} />
        </span>
      </button>
    </div>
  );
}
