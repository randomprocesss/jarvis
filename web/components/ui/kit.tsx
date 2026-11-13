"use client";
/* Small parts shared by the redesigned screens (12 Nov 2026): a data hook, a titled panel, hash tabs, a status chip,
   a plain table. Palette colours only, so light and dark both work. */
import * as React from "react";
import { WH, useWH } from "@/lib/wh";
import { cn } from "@/lib/utils";
import { Card } from "@/components/ui/card";

/** GET a JSON route now and every `every` ms while the page is visible. Returns [data, reload, error]. */
export function useApi<T = any>(url: string | null, every = 0): [T | null, () => void, string] {
  const [data, setData] = React.useState<T | null>(null), [err, setErr] = React.useState("");
  const load = React.useCallback(() => {
    if (!url) return;
    WH.api(url).then((d: any) => { if (d && d.error && Object.keys(d).length === 1) setErr(String(d.error)); else { setData(d); setErr(""); } }).catch(() => setErr("Jarvis is not answering"));
  }, [url]);
  React.useEffect(() => {
    setData(null); load();
    if (!every) return;
    const t = setInterval(() => { if (!document.hidden) load(); }, every);
    return () => clearInterval(t);
  }, [load, every]);
  return [data, load, err];
}

export function Panel({ title, n, right, children, className, pad = true }: { title?: React.ReactNode; n?: number | string; right?: React.ReactNode; children: React.ReactNode; className?: string; pad?: boolean }) {
  return (
    <Card className={cn("min-w-0 [overflow-wrap:anywhere]", className)}>
      {(title || right) && (
        <div className="flex items-baseline gap-2.5 border-b border-line-2 px-5 py-3">
          {title && <h2 className="m-0 text-[15px] font-semibold tracking-[-.005em] text-ink">{title}</h2>}
          {n != null && <span className="rounded-full bg-line-2 px-2 py-px font-mono text-xs font-medium tabular-nums text-ink-2">{n}</span>}
          {right && <div className="ml-auto text-[12.5px] text-mute">{right}</div>}
        </div>
      )}
      <div className={pad ? "px-5 py-4" : ""}>{children}</div>
    </Card>
  );
}

/** Tabs that live in the address (#/brain/ask), so a tab can be linked and the back button works. */
export function HashTabs({ base, tabs, current }: { base: string; tabs: [string, string, (number | string)?][]; current: string }) {
  return (
    <div role="tablist" className="flex max-w-full flex-wrap items-center gap-1 border-b border-line">
      {tabs.map(([k, label, n]) => {
        const on = k === current;
        return (
          <a key={k} role="tab" aria-selected={on} href={`#/${base}/${k}`}
            className={cn("relative -mb-px inline-flex items-center gap-2 border-b-2 px-3 py-2 text-[14px] no-underline transition-colors", on ? "border-ion font-semibold text-ion" : "border-transparent font-medium text-mute hover:text-ink")}>
            {label}{n != null && n !== "" && <span className={cn("rounded-full px-1.5 py-px font-mono text-[11px] tabular-nums", on ? "bg-ion-soft text-ion" : "bg-line-2 text-mute")}>{n}</span>}
          </a>
        );
      })}
    </div>
  );
}

const TONE = { ok: "bg-ok-soft text-ok", warn: "bg-warn-soft text-warn", bad: "bg-bad-soft text-bad", ion: "bg-ion-soft text-ion", mute: "bg-line-2 text-mute" } as const;
export function Chip({ tone = "mute", children, className, title }: { tone?: keyof typeof TONE; children: React.ReactNode; className?: string; title?: string }) {
  return <span title={title} className={cn("inline-flex items-center gap-1.5 whitespace-nowrap rounded-full px-2 py-[2px] text-[12px] font-semibold leading-[1.4]", TONE[tone], className)}>{children}</span>;
}

/** A plain table: header row in small caps, hairlines, no zebra. `cols` = [label, className?]. */
export function Table({ cols, children, className }: { cols: [string, string?][]; children: React.ReactNode; className?: string }) {
  return (
    <div className={cn("overflow-x-auto", className)}>
      {/* 13 Nov: a floor width, so on a phone the table scrolls inside its card instead of squeezing a column to one letter */}
      <table className="w-full border-collapse text-[13.5px]" style={cols.length >= 3 ? { minWidth: cols.length * 130 } : undefined}>
        <thead><tr>{cols.map(([l, c], i) => <th key={i} className={cn("whitespace-nowrap border-b border-line px-3 py-2 text-left text-[11.5px] font-semibold uppercase tracking-[.06em] text-mute first:pl-5 last:pr-5", c)}>{l}</th>)}</tr></thead>
        <tbody className="[&_td]:border-b [&_td]:border-line-2 [&_td]:px-3 [&_td]:py-2.5 [&_td]:align-top [&_td:first-child]:pl-5 [&_td:last-child]:pr-5 [&_tr:last-child_td]:border-b-0">{children}</tbody>
      </table>
    </div>
  );
}

/** 13 Nov 2026 (look plan, phase 3): a screen's header set into a lit studio band, the same metal and cyan as the machine on
    Home. `art` is a still under /media/screens/; `film` (optional) a muted loop of it, played only while on screen and never
    under reduced motion. Inside the band the palette's text colours are redefined light, so any header content reads.
    The picture is cropped from the left and top only, so the provider's mark (bottom right) always shows; `whole` shows the
    entire picture instead, for one whose subject sits left of centre. */
export function ArtBand({ art, film, whole, children, className }: { art: string; film?: string; whole?: boolean; children: React.ReactNode; className?: string }) {
  const v = React.useRef<HTMLVideoElement>(null);
  React.useEffect(() => {
    const el = v.current;
    if (!el) return;
    if (typeof matchMedia !== "undefined" && matchMedia("(prefers-reduced-motion: reduce)").matches) return;
    el.muted = true;
    // plays while on screen and the window is visible; either can change first, so both are watched
    let seen = false;
    const sync = () => { if (seen && !document.hidden) el.play().catch(() => {}); else el.pause(); };
    const io = new IntersectionObserver(([e]) => { seen = e.isIntersecting; sync(); });
    io.observe(el);
    { const r = el.getBoundingClientRect(); seen = r.bottom > 0 && r.top < innerHeight; sync(); } // start now if already in view
    document.addEventListener("visibilitychange", sync);
    el.addEventListener("canplay", sync);
    return () => { io.disconnect(); document.removeEventListener("visibilitychange", sync); el.removeEventListener("canplay", sync); };
  }, [film]);
  useWH(["me"]);
  if (!WH.me.media) return <div className={className}>{children}</div>;   // the pictures are an optional pack: without it, the plain header
  return (
    <div className={cn("jv-artband relative isolate overflow-hidden rounded-[20px]", className)}>
      <img src={art} alt="" aria-hidden decoding="async" className={cn("jv-artband-img absolute inset-y-0 right-0 h-full object-cover max-[700px]:opacity-50", whole ? "is-whole" : "w-[52%] max-[700px]:w-full")} />
      {film && <video ref={v} src={film} poster={art} muted loop playsInline preload="metadata" aria-hidden disablePictureInPicture disableRemotePlayback
        className={cn("jv-artband-img absolute inset-y-0 right-0 h-full object-cover max-[700px]:opacity-50", whole ? "is-whole" : "w-[52%] max-[700px]:w-full")} />}
      <div aria-hidden className="jv-dayband-fade pointer-events-none absolute inset-0" />
      <div className="relative z-[1] px-7 py-7 max-[560px]:px-5">{children}</div>
    </div>
  );
}

export function PageTitle({ title, sub, right, art, film }: { title: React.ReactNode; sub?: React.ReactNode; right?: React.ReactNode; art?: string; film?: string }) {
  useWH(["me"]);
  if (!WH.me.media) { art = undefined; film = undefined; }
  const head = (
    <header className="flex flex-wrap items-end justify-between gap-x-6 gap-y-3">
      <div className={cn("min-w-0", art && "max-w-[56%] max-[700px]:max-w-none")}>
        <h1 className={cn("m-0 font-semibold leading-tight tracking-[-.02em] text-ink", art ? "text-[30px]" : "text-[26px]")}>{title}</h1>
        {sub && <p className="mt-1.5 max-w-[78ch] text-[14.5px] text-ink-2">{sub}</p>}
      </div>
      {right}
    </header>
  );
  return art ? <ArtBand art={art} film={film}>{head}</ArtBand> : head;
}

export const when = (s?: string | null) => { if (!s) return ""; const d = new Date(s); return isNaN(+d) ? String(s) : WH.fmtWhen(d); };
export const cut = (s: any, n = 160) => { s = String(s || "").replace(/\s+/g, " ").trim(); return s.length > n ? s.slice(0, n - 1).replace(/\s+\S*$/, "") + "…" : s; };
export function Loading({ rows = 3 }: { rows?: number }) {
  return <div className="grid gap-2 px-5 py-4" role="status" aria-label="Loading">{Array.from({ length: rows }).map((_, i) => <div key={i} className="skel h-4" style={{ width: `${88 - i * 14}%` }} />)}</div>;
}
export function Note({ children, tone = "mute" }: { children: React.ReactNode; tone?: "mute" | "warn" | "ion" }) {
  const c = tone === "warn" ? "border-warn/30 bg-warn-soft text-ink" : tone === "ion" ? "border-ion/25 bg-ion-soft text-ink" : "border-line bg-card-2 text-ink-2";
  return <div className={cn("rounded-[10px] border px-4 py-3 text-[13.5px] leading-relaxed", c)}>{children}</div>;
}
