"use client";
/* Left sidebar (redesign, 12 Nov 2026): the screens grouped by what he does (work, what Jarvis knows, how far it may act,
   admin), with count badges. Colours come from the palette, so it reads the same in light and dark. On phones: a drawer. */
import * as DialogPrimitive from "@radix-ui/react-dialog";
import { motion } from "framer-motion";
import { Home as HomeIcon, LayoutDashboard, Inbox, Ticket, Clock, Search, PanelLeftClose, PanelLeftOpen, X, Activity, BrainCircuit, ShieldCheck, Gauge, SlidersHorizontal } from "lucide-react";
import { WH, useWH } from "@/lib/wh";
import { useJ, talk, setListening } from "@/lib/jarvis";
import { cn } from "@/lib/utils";

/* [key, href, label, icon, group] */
export const NAV: [string, string, string, any, string][] = [
  ["home", "#/", "Home", HomeIcon, "Work"],
  ["today", "#/today", "Today", LayoutDashboard, "Work"],
  ["inbox", "#/inbox", "Inbox", Inbox, "Work"],
  ["activity", "#/activity", "Activity", Activity, "Work"],
  ["brain", "#/brain", "Brain", BrainCircuit, "Knowledge"],
  ["trust", "#/trust", "Trust", Gauge, "Control"],
  ["setup", "#/setup", "Setup", SlidersHorizontal, "Control"],
  ["deploy", "#/deploy", "Deploy gate", ShieldCheck, "Control"],
  ["tickets", "#/tickets", "Jira tickets", Ticket, "Admin"],
];

/* A screen of an optional part is listed only while settings have that part switched on (GET /api/me: modules). */
const OPTIONAL: Record<string, string> = { deploy: "deploy_gate" };
export const navShown = () => NAV.filter(([k]) => (!OPTIONAL[k] || (WH.me.modules || []).includes(OPTIONAL[k])) && (k !== "tickets" || WH.hasTickets()));

export function navCounts(): Record<string, number> {
  return {
    today: WH.pendingApprovals().length,
    inbox: WH.openItems().length,
    tickets: WH.store.tickets.items.filter((t) => t.status === "draft" || t.status === "ready").length,
  };
}

export function runText() {
  const last = WH.lastRoundup(), next = WH.nextWork(), running = WH.workRunning();
  return { running, text: `${last ? "Checked " + WH.fmtWhen(last) : ""}${last && (next || running) ? " · " : ""}${running ? "Jarvis is working on items now" : next ? "next work run " + WH.fmtWhen(next) : ""}` };
}

export function BrandMark({ size = 20, layoutId }: { size?: number; layoutId?: string }) {
  return <motion.span aria-hidden data-brand-mark layoutId={layoutId} className="arc-mark" style={{ width: size, height: size }} />;
}

/** In the Jarvis app the brand mark is push-to-talk: a ring while Jarvis listens, breathing while it thinks. */
function TalkMark() {
  const awake = useJ((s) => s.awake), thinking = useJ((s) => s.thinking);
  return (
    <button type="button" onClick={talk} aria-label="Talk to Jarvis" title="Talk to Jarvis" data-jarvis-talk
      className="relative grid size-8 flex-none cursor-pointer place-items-center rounded-full border-0 bg-transparent p-0 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ion/60">
      <span className={cn("grid place-items-center", thinking && "jv-think")}><BrandMark /></span>
      {awake && <span aria-hidden className="jv-ring pointer-events-none absolute inset-0 rounded-full" />}
    </button>
  );
}

const ROW = "flex h-9 items-center gap-3 rounded-[9px] border-0 bg-transparent text-[13.5px] text-mute transition-colors hover:bg-line-2 hover:text-ink focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ion/60";

function ListenSwitch({ collapsed }: { collapsed: boolean }) {
  const on = useJ((s) => s.listening), model = useJ((s) => s.model), mic = useJ((s) => s.mic);
  return (
    <>
      <button type="button" role="switch" aria-checked={on} onClick={() => setListening(!on)} title={on ? "Listening: on" : "Listening: off"} data-jarvis-listen
        className={cn(ROW, collapsed ? "justify-center px-0" : "px-3")}>
        <span className="grid size-4 flex-none place-items-center"><i className={cn("size-[7px] rounded-full", on ? "live-dot bg-ok text-ok" : "bg-mute/40")} /></span>
        {!collapsed && <>
          <span className="flex-1 text-left">Listening</span>
          <span aria-hidden className={cn("relative h-[18px] w-[30px] flex-none rounded-full transition-colors duration-200", on ? "bg-ion" : "bg-line-3")}>
            <span className={cn("absolute top-[2px] size-[14px] rounded-full bg-white shadow-sm transition-[left] duration-200", on ? "left-[14px]" : "left-[2px]")} />
          </span>
        </>}
      </button>
      {!collapsed && on && (mic === "denied"
        ? <p className="m-0 px-3 pb-1 text-[11.5px] leading-snug text-mute">The microphone is off for Jarvis in System Settings.</p>
        : model === "hey_jarvis" ? <p className="m-0 px-3 pb-1 text-[11.5px] leading-snug text-mute">Say “Hey Jarvis” until you teach me your voice.</p> : null)}
    </>
  );
}

function NavBody({ name, collapsed, id, onNav, onCollapse }: { name: string; collapsed: boolean; id: string; onNav?: () => void; onCollapse?: () => void }) {
  useWH(["inbox", "tickets", "approvals", "theme", "me"]);
  const counts = navCounts(), run = runText();
  const native = useJ((s) => s.native);
  let group = "";
  return (
    <div className="flex h-full flex-col">
      <div className={cn("flex h-14 flex-none items-center gap-2.5 text-[15px] font-semibold tracking-[-.01em] text-ink", collapsed ? "justify-center px-0" : "px-4")}>
        {native ? <TalkMark /> : <a href="#/" aria-label="Jarvis home" onClick={onNav} className="grid size-8 place-items-center no-underline"><BrandMark /></a>}
        {!collapsed && <a href="#/" onClick={onNav} className="text-ink no-underline">Jarvis</a>}
      </div>
      <nav aria-label="Sections" className={cn("grid gap-0.5", collapsed ? "px-2.5" : "px-3")}>
        {navShown().map(([k, href, label, Icon, g]) => {
          const on = k === name, n = counts[k], head = g !== group; group = g;
          return (
            <div key={k} className="contents">
              {head && (collapsed ? <div aria-hidden className="mx-2 my-2 h-px bg-line first:hidden" />
                : <div className="px-3 pb-1 pt-4 text-[11px] font-semibold uppercase tracking-[.08em] text-mute/80 first:pt-1">{g}</div>)}
              <a href={href} title={collapsed ? label : undefined} aria-current={on ? "page" : undefined} onClick={onNav} data-nav={k}
                className={cn("group relative flex h-9 items-center gap-3 rounded-[9px] text-[14px] no-underline outline-none transition-colors duration-150 focus-visible:ring-2 focus-visible:ring-ion/60",
                  collapsed ? "justify-center px-0" : "px-3", on ? "bg-ion-soft font-semibold text-ion" : "font-medium text-ink-2 hover:bg-line-2 hover:text-ink")}>
                {on && <motion.span layoutId={"nav-on-" + id} className="absolute bottom-2 left-0 top-2 w-[3px] rounded-full bg-ion" transition={{ type: "spring", stiffness: 500, damping: 40 }} />}
                <Icon size={17} strokeWidth={1.8} aria-hidden className="relative flex-none" />
                {!collapsed && <span className="relative min-w-0 flex-1 truncate">{label}</span>}
                {n ? (
                  <span data-badge={k} className={cn("relative rounded-full font-mono font-medium leading-none tabular-nums", on ? "bg-ion text-primary-foreground" : "bg-line-2 text-ink-2",
                    collapsed ? "absolute -right-0.5 top-0 min-w-4 px-1 py-[3px] text-center text-[10px]" : "min-w-[20px] px-1.5 py-[4px] text-center text-[11px]")}>{n}</span>
                ) : null}
              </a>
            </div>
          );
        })}
      </nav>
      <div className="flex-1" />
      <div className={cn("grid gap-0.5 border-t border-line py-3", collapsed ? "px-2.5" : "px-3")}>
        {native && <ListenSwitch collapsed={collapsed} />}
        {run.text && (
          <div title={run.text} className={cn("mb-1 flex items-start gap-2.5 text-[12px] leading-snug text-mute", collapsed ? "justify-center py-1" : "px-3 py-1.5")} data-run>
            <i className={cn("mt-[5px] size-[7px] flex-none rounded-full", run.running ? "live-dot bg-warn text-warn" : "bg-ok")} />
            {!collapsed && <span className="min-w-0">{run.text}</span>}
          </div>
        )}
        <button type="button" onClick={() => { onNav?.(); WH.emit("palette", "toggle"); }} title="Commands (⌘K)" className={cn(ROW, collapsed ? "justify-center px-0" : "px-3")}>
          <Search size={16} strokeWidth={1.8} aria-hidden />{!collapsed && <><span className="flex-1 text-left">Search and commands</span><kbd className="rounded-[6px] border border-line-3 px-1.5 py-0.5 font-mono text-[10.5px]">⌘K</kbd></>}
        </button>
        {onCollapse && (
          <button type="button" onClick={onCollapse} aria-label={collapsed ? "Expand sidebar" : "Collapse sidebar"} title={collapsed ? "Expand sidebar" : "Collapse sidebar"} className={cn(ROW, collapsed ? "justify-center px-0" : "px-3")}>
            {collapsed ? <PanelLeftOpen size={16} strokeWidth={1.8} /> : <PanelLeftClose size={16} strokeWidth={1.8} />}{!collapsed && <span>Collapse</span>}
          </button>
        )}
      </div>
    </div>
  );
}

/** Desktop sidebar (900px and up). */
export function Sidebar({ name, collapsed, onCollapse }: { name: string; collapsed: boolean; onCollapse: () => void }) {
  return (
    <aside className={cn("fixed inset-y-0 left-0 z-40 hidden jv-chrome border-r border-line bg-chrome text-ink transition-[width] duration-200 ease-out min-[900px]:block", collapsed ? "w-[68px]" : "w-[232px]")}>
      <NavBody name={name} collapsed={collapsed} id="side" onCollapse={onCollapse} />
    </aside>
  );
}

/** Phone drawer with the same contents. */
export function MobileNav({ name, open, onOpenChange }: { name: string; open: boolean; onOpenChange: (o: boolean) => void }) {
  return (
    <DialogPrimitive.Root open={open} onOpenChange={onOpenChange}>
      <DialogPrimitive.Portal>
        <DialogPrimitive.Overlay className="wh-overlay fixed inset-0 z-[55] bg-black/40 backdrop-blur-[2px]" />
        <DialogPrimitive.Content aria-describedby={undefined} className="wh-drawer fixed inset-y-0 left-0 z-[56] w-[min(280px,86vw)] bg-chrome text-ink shadow-lg outline-none">
          <DialogPrimitive.Title className="sr-only">Sections</DialogPrimitive.Title>
          <DialogPrimitive.Close aria-label="Close menu" className="absolute right-3 top-3 grid size-8 place-items-center rounded-[9px] border-0 bg-transparent text-mute hover:bg-line-2 hover:text-ink"><X size={16} /></DialogPrimitive.Close>
          <NavBody name={name} collapsed={false} id="drawer" onNav={() => onOpenChange(false)} />
        </DialogPrimitive.Content>
      </DialogPrimitive.Portal>
    </DialogPrimitive.Root>
  );
}
