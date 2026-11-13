"use client";
/* Small shared pieces for the polished look: avatar, source icon, disclosure, page header, stat card, round check. */
import * as React from "react";
import { AnimatePresence, motion } from "framer-motion";
import { ChevronRight, Hash, Mail, Users, Calendar, Ticket, BookOpen, Clock, Mic, CircleDot, AudioLines, GitPullRequest } from "lucide-react";
import { WH } from "@/lib/wh";
import { cn } from "@/lib/utils";
import { Card } from "@/components/ui/card";

const HUES = ["var(--s-slack)", "var(--s-email)", "var(--s-teams)", "var(--s-calendar)", "var(--s-confluence)", "var(--s-meeting)", "var(--ion)", "var(--ok)"];
const hash = (s: string) => { let h = 0; for (let i = 0; i < s.length; i++) h = (h * 31 + s.charCodeAt(i)) | 0; return Math.abs(h); };
const initials = (n: string) => { const w = String(n || "").replace(/[^A-Za-z0-9\u00C0-\u024F\s]/g, " ").trim().split(/\s+/).filter(Boolean); return ((w[0] || "?")[0] + (w.length > 1 ? w[w.length - 1][0] : "")).toUpperCase(); };

/** Initials in a circle; the colour is fixed per name. */
function Avatar({ name, size = 36, icon: Icon, color, className }: { name?: string; size?: number; icon?: any; color?: string; className?: string }) {
  if (/^you$/i.test(String(name || "").trim())) name = WH.me.name || "You";   // their own items: their initials, not "Y"
  const c = color || HUES[hash(String(name || "")) % HUES.length];
  return (
    <span aria-hidden className={cn("grid flex-none select-none place-items-center rounded-full font-display font-semibold", className)}
      style={{ width: size, height: size, fontSize: Math.round(size * 0.36), color: c, background: `color-mix(in srgb, ${c} 14%, var(--card))`, boxShadow: `inset 0 0 0 1px color-mix(in srgb, ${c} 22%, transparent)` }}>
      {Icon ? <Icon size={Math.round(size * 0.46)} strokeWidth={1.8} /> : initials(name || "")}
    </span>
  );
}

const SRC_ICON: Record<string, any> = { slack: Hash, email: Mail, teams: Users, calendar: Calendar, jira: Ticket, confluence: BookOpen, bitbucket: GitPullRequest, meeting: Mic, jarvis: AudioLines, other: CircleDot };
function SrcIcon({ s, size = 13, className }: { s: string; size?: number; className?: string }) {
  const I = SRC_ICON[s] || CircleDot;
  return <I size={size} strokeWidth={2} aria-hidden className={cn("flex-none", className)} style={{ color: `var(--s-${s}, var(--s-other))` }} />;
}
/** Source badge: icon + name on a soft tint of the source colour. */
function SrcBadge({ s, className }: { s: string; className?: string }) {
  const c = `var(--s-${s}, var(--s-other))`;
  return (
    <span className={cn("src inline-flex items-center gap-1.5 rounded-full px-2 py-[3px] before:hidden", className)} style={{ ["--c" as any]: c, background: `color-mix(in srgb, ${c} 10%, var(--card))` }}>
      <SrcIcon s={s} size={12} />{WH.SRC[s] || s}
    </span>
  );
}

/** Animated disclosure (height + opacity) with a rotating chevron. Uncontrolled; `onOpenChange` reports toggles. */
function Disclosure({ summary, children, defaultOpen = false, onOpenChange, className, small }: { summary: React.ReactNode; children: React.ReactNode; defaultOpen?: boolean; onOpenChange?: (o: boolean) => void; className?: string; small?: boolean }) {
  const [open, setOpen] = React.useState(defaultOpen);
  const id = React.useId();
  return (
    <div className={className} data-open={open ? "1" : "0"}>
      <button type="button" aria-expanded={open} aria-controls={id} onClick={() => { setOpen(!open); onOpenChange?.(!open); }}
        className={cn("group inline-flex w-fit items-center gap-1 rounded-md border-0 bg-transparent p-0 text-left font-semibold text-mute transition-colors duration-150 hover:text-ion focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ion", small ? "text-[12.5px]" : "text-[13px]")}>
        <span>{summary}</span>
        <motion.span animate={{ rotate: open ? 90 : 0 }} transition={{ duration: 0.18 }} className="inline-flex"><ChevronRight size={14} strokeWidth={2} /></motion.span>
      </button>
      <AnimatePresence initial={false}>
        {open && (
          <motion.div id={id} key="c" initial={{ height: 0, opacity: 0 }} animate={{ height: "auto", opacity: 1 }} exit={{ height: 0, opacity: 0 }} transition={{ duration: 0.2, ease: [0.2, 0.8, 0.2, 1] }} style={{ overflow: "hidden" }}>
            {children}
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}

/** Page header: mono kicker, big Outfit title, one muted line, optional right side. */
function PageHeader({ kicker, title, sub, right, className, art, film, whole }: { kicker?: React.ReactNode; title: React.ReactNode; sub?: React.ReactNode; right?: React.ReactNode; className?: string; art?: string; film?: string; whole?: boolean }) {
  if (art) {
    // 13 Nov: the header set into a lit studio band (ArtBand in kit.tsx)
    const { ArtBand } = require("@/components/ui/kit");
    return <ArtBand art={art} film={film} whole={whole} className={className}><PageHeader kicker={kicker} title={title} sub={sub} right={right} /></ArtBand>;
  }
  return (
    <motion.header initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.25 }} className={cn("flex flex-wrap items-end justify-between gap-x-6 gap-y-4", className)}>
      <div className="min-w-0">
        {kicker && <div className="font-mono text-[11.5px] font-medium uppercase tracking-[.1em] text-mute">{kicker}</div>}
        <h1 className="mt-1.5 font-display text-[clamp(28px,3.6vw,40px)] font-semibold leading-[1.1] tracking-[-.02em] text-ink">{title}</h1>
        {sub && <div className="mt-2 max-w-[68ch] text-[15px] text-ink-2">{sub}</div>}
      </div>
      {right}
    </motion.header>
  );
}

/** Compact stat card with a tinted icon and a big number. */
function Stat({ tone, icon: Icon, label, children, sub, className }: { tone: "warn" | "ok" | "ion"; icon: any; label: string; children: React.ReactNode; sub?: React.ReactNode; className?: string }) {
  const c = `var(--${tone})`;
  return (
    <Card className={cn("relative min-w-0 overflow-hidden px-4 py-3.5 max-[560px]:px-3", className)} style={{ background: `linear-gradient(160deg, color-mix(in srgb, ${c} 9%, var(--card)), var(--card) 70%)` }} data-stat={label}>
      <div className="flex items-center gap-2 text-[12.5px] font-semibold leading-tight text-ink-2">
        <span className="grid size-6 flex-none place-items-center rounded-[8px]" style={{ color: c, background: `color-mix(in srgb, ${c} 14%, var(--card))` }}><Icon size={14} strokeWidth={2} aria-hidden /></span>
        <span className="min-w-0">{label}</span>
      </div>
      <div className="mt-2.5 truncate font-display text-[clamp(22px,6vw,28px)] font-semibold leading-none tracking-[-.02em] text-ink" data-num>{children}</div>
      {sub && <div className="mt-1.5 truncate text-[12.5px] text-mute">{sub}</div>}
    </Card>
  );
}

/** Round tick box that draws its checkmark. Same input semantics as a checkbox. */
function RoundCheck({ checked, onChange, label }: { checked: boolean; onChange: (v: boolean) => void; label: string }) {
  return (
    <label className="relative mt-px grid size-5 flex-none cursor-pointer place-items-center">
      <input type="checkbox" className="peer absolute inset-0 m-0 cursor-pointer opacity-0" checked={checked} onChange={(e) => onChange(e.target.checked)} aria-label={label} />
      <span className="grid size-5 place-items-center rounded-full border-[1.5px] border-line-3 bg-card transition-[background-color,border-color,box-shadow] duration-150 peer-hover:border-ok peer-checked:border-ok peer-checked:bg-ok peer-focus-visible:ring-2 peer-focus-visible:ring-ion peer-focus-visible:ring-offset-2 peer-focus-visible:ring-offset-[var(--card)]">
        <svg viewBox="0 0 24 24" className="size-3 text-white dark:text-chrome" aria-hidden>
          <motion.path d="M5 12.5l4.5 4.5L19 7.5" fill="none" stroke="currentColor" strokeWidth={3.2} strokeLinecap="round" strokeLinejoin="round"
            initial={false} animate={{ pathLength: checked ? 1 : 0, opacity: checked ? 1 : 0 }} transition={{ duration: 0.22, ease: "easeOut" }} />
        </svg>
      </span>
    </label>
  );
}

export { Avatar, SrcIcon, SrcBadge, Disclosure, PageHeader, Stat, RoundCheck };
