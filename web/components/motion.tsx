"use client";
import { useEffect, useRef, useState } from "react";
import { animate, useReducedMotion } from "framer-motion";

/** Number that eases from its previous value to the new one (instant with reduced motion). */
/** Optional `from` makes the first show count up from that value (landing cards); `duration` defaults to 0.56 s. */
export function NumberTicker({ value, format = (x: number) => String(Math.round(x)), unit, from, duration = 0.56 }: { value: number; format?: (x: number) => string; unit?: string; from?: number; duration?: number }) {
  const reduce = useReducedMotion();
  const [shown, setShown] = useState(reduce ? value : from ?? value), prev = useRef(from ?? value);
  useEffect(() => {
    const from = prev.current; prev.current = value;
    if (reduce || from === value) { setShown(value); return; }
    const c = animate(from, value, { duration, ease: [0.33, 1, 0.68, 1], onUpdate: setShown });
    return () => c.stop();
  }, [value, reduce, duration]);
  return <>{format(shown)}{unit ? <span className="ml-[3px] text-[.55em] font-medium text-mute">{unit}</span> : null}</>;
}

/** Shared enter/exit for list items (cards, rows). */
export const itemMotion = {
  layout: "position" as const,
  initial: { opacity: 0, y: 8 },
  animate: { opacity: 1, y: 0 },
  exit: { opacity: 0, scale: 0.98, transition: { duration: 0.18 } },
  transition: { duration: 0.22, ease: [0.2, 0.8, 0.2, 1] as any },
};

/** Shared landing <-> sidebar elements (layoutId) run only on wide screens (the sidebar is hidden on phones) and with motion allowed. */
export const wideLayout = () => typeof window !== "undefined" && window.innerWidth >= 900 && !matchMedia("(prefers-reduced-motion: reduce)").matches;

/** Coming from the landing page, the new page's panels "materialize": blur + slight depth to sharp, one thin arc light line passing
 *  over each once, staggered. Pure CSS (.mz in globals.css), removed again after it has played. */
export function materialize(root: Element | null) {
  if (!root || matchMedia("(prefers-reduced-motion: reduce)").matches) return;
  const all = Array.from(root.querySelectorAll(".glass-card")).filter((el) => !el.parentElement?.closest(".glass-card"));
  const shown = all.filter((el) => el.getBoundingClientRect().top < window.innerHeight).slice(0, 12);
  shown.forEach((el, i) => { (el as HTMLElement).style.setProperty("--mz-i", String(Math.min(i, 5))); el.classList.add("mz"); });
  setTimeout(() => shown.forEach((el) => { el.classList.remove("mz"); (el as HTMLElement).style.removeProperty("--mz-i"); }), 1700);
}
