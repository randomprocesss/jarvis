"use client";
import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { Ambient } from "@/components/shell/Ambient";
import { MotionConfig, motion } from "framer-motion";
import { WH, VIEWS } from "@/lib/wh";
import { TopBar } from "@/components/shell/TopBar";
import { Sidebar, MobileNav } from "@/components/shell/Sidebar";
import { cn } from "@/lib/utils";
import { Palette } from "@/components/shell/Palette";
import { AskDialog } from "@/components/shell/AskDialog";
import { Toaster } from "@/components/ui/sonner";
import Jarvis from "@/components/jarvis/Jarvis";
import Today from "@/components/views/Today";
import Landing from "@/components/views/Landing";
import ActivityView from "@/components/views/Activity";
import BrainView from "@/components/views/Brain";
import TrustView from "@/components/views/Trust";
import InboxView from "@/components/views/Inbox";
import TicketsView from "@/components/views/Tickets";
import OfficeView from "@/components/views/Office";
import SetupView from "@/components/views/Setup";
import { useJarvis, jarvis } from "@/lib/jarvis";

/** In the demo: says so on every screen, and leads back to the owner's own Jarvis. */
function DemoRibbon() {
  const [on, setOn] = useState(!!WH.me.demo);
  useEffect(() => WH.on("me", () => setOn(!!WH.me.demo)), []);
  useEffect(() => { document.documentElement.classList.toggle("jv-demo", on); }, [on]);      // the page makes room for it (globals.css)
  if (!on) return null;
  const leave = async () => { const r = await WH.api("/api/demo_leave", {}).catch(() => null); if (r && r.url) window.location.href = r.url; };
  return (
    <div role="status" data-demo-ribbon className="fixed inset-x-0 top-0 z-[60] flex h-[var(--demo-h)] items-center justify-center gap-3 overflow-hidden bg-warn px-4 text-[13px] font-semibold text-white">
      <span>Demo: a made-up person's week. Nothing here is real, and nothing is sent.</span>
      <button type="button" onClick={leave} className="rounded-full bg-white/20 px-3 py-0.5 text-[12.5px] font-semibold text-white hover:bg-white/30">Leave the demo</button>
    </div>
  );
}

function ViewHost({ name, sub }: { name: string; sub: string }) {
  // A new view starts at the top (moving between tabs of the same view does not).
  useLayoutEffect(() => { window.scrollTo({ top: 0, behavior: "instant" as ScrollBehavior }); }, []);
  if (name === "home") return <Landing />;
  if (name === "inbox") return <InboxView sub={sub} />;
  if (name === "activity") return <ActivityView />;
  if (name === "brain") return <BrainView sub={sub} />;
  if (name === "trust") return <TrustView sub={sub} />;
  if (name === "tickets") return <TicketsView />;
  if (name === "office") return <OfficeView />;
  if (name === "setup") return <SetupView />;
  return <Today />;
}

export default function App() {
  const [route, setRoute] = useState(() => WH.parse());
  const nameRef = useRef(route.name);
  const [collapsed, setCollapsed] = useState(() => WH.pref.get("wh_sb") === "1");
  const [menu, setMenu] = useState(false);
  const mainRef = useRef<HTMLElement>(null);
  const toggleSb = () => setCollapsed((c) => { WH.pref.set("wh_sb", c ? null : "1"); return !c; });

  // Office mode (Office.swift): with the external monitor in, the app makes Jarvis a slim side panel there, and the page
  // shows the Office view; it goes back to where it was when the monitor is unplugged. "Full app" from the panel is
  // respected until the next time the monitor connects.
  const J = useJarvis(), officeOn = !!(J.office && J.office.active);
  const before = useRef("#/today"), leftOffice = useRef(false), wasOn = useRef(false);
  useEffect(() => {
    const narrow = () => window.innerWidth < 760;
    const apply = () => {
      const r = WH.parse();
      if (officeOn && !wasOn.current) leftOffice.current = false;
      if (officeOn && narrow() && r.name !== "office" && !leftOffice.current) { before.current = location.hash || "#/today"; location.hash = "#/office"; }
      // back only when the monitor goes away (opening the Office view by hand without a monitor stays put)
      if (!officeOn && wasOn.current && r.name === "office") location.hash = before.current && before.current !== "#/office" ? before.current : "#/today";
      wasOn.current = officeOn;
    };
    apply();
    window.addEventListener("resize", apply);
    return () => window.removeEventListener("resize", apply);
  }, [officeOn]);
  useEffect(() => { if (officeOn && route.name !== "office") leftOffice.current = true; }, [route.name, officeOn]);

  useEffect(() => {
    WH.applyTheme();
    let lastHash = location.hash || "#/", reverting = false;
    const onHash = async () => {
      if (reverting) { reverting = false; return; }
      const next = WH.parse();
      if (next.name === nameRef.current) { lastHash = location.hash; setRoute(next); return; }
      if (WH.shell.dirty && !(await WH.ask("Discard unsaved changes?", "You have edits that are not saved. Leaving this view discards them.", "Discard and leave", true))) {
        reverting = true; location.hash = lastHash; return;
      }
      lastHash = location.hash || "#/"; nameRef.current = next.name; WH.resetShell(); setRoute(next);
    };
    const onKey = (e: KeyboardEvent) => {
      const mod = e.metaKey || e.ctrlKey, k = (e.key || "").toLowerCase();
      if (mod && k === "k") { e.preventDefault(); WH.emit("palette", "toggle"); }
      else if (mod && k === "s") { e.preventDefault(); if (WH.shell.save) WH.shell.save(); }
    };
    const onUnload = (e: BeforeUnloadEvent) => { if (WH.shell.dirty) { e.preventDefault(); e.returnValue = ""; } };
    // Leaving the app (window hidden or closed) is what "away" means; moving between Jarvis pages is not.
    const markSeen = () => WH.pref.set("wh_seen_at", new Date().toISOString());
    const onVis = () => { if (document.hidden) markSeen(); else { WH.loadInbox(); WH.loadTickets(); } };
    window.addEventListener("hashchange", onHash);
    document.addEventListener("keydown", onKey);
    window.addEventListener("beforeunload", onUnload);
    document.addEventListener("visibilitychange", onVis);
    window.addEventListener("pagehide", markSeen);
    // a new install opens on Setup: until the owner has said who they are, every other screen would be empty
    WH.loadMe().then(() => { if (WH.me.loaded && !WH.me.set_up && WH.parse().name !== "setup") location.hash = "#/setup"; });
    WH.loadInbox(); WH.loadTickets(); WH.loadJiraStatus(); WH.loadApprovals();
    // The page key check (Phase 3): only the Jarvis app window can send, approve or write; say so plainly if this one can't.
    if ((window as any).jarvisApp?.native) WH.api("/api/key_check", {}).then((r: any) => { if (!r || !r.ok) WH.toast("Sending is blocked in this window: " + ((r && r.error) || "no answer") + " Quit and reopen Jarvis.", "err", 12000); });
    let lastV = 0, lastUi = 0;
    const iv = [
      setInterval(() => { if (!document.hidden) WH.loadApprovals(); }, 15000),
      setInterval(() => { if (!document.hidden) WH.loadInbox(); }, 20000),
      setInterval(() => { if (!document.hidden) WH.loadTickets(); }, 12000),
      // a routine or worker wrote something (a decision it needs, a finished draft): re-read within 2 s, hidden or not
      setInterval(async () => {
        try {
          const c = await fetch("/api/changed", { cache: "no-store" }).then((r) => r.json()), v = c.v;
          if (lastV && v !== lastV) { WH.loadInbox(); WH.loadApprovals(); WH.loadTickets(); }
          lastV = v;
          // a new page build: reload once he is idle (nothing unsaved, not typing, Jarvis not thinking or speaking)
          if (lastUi && c.ui && c.ui !== lastUi) {
            const j = jarvis.get(), el = document.activeElement as HTMLElement | null;
            const typing = !!el && (el.tagName === "TEXTAREA" || el.tagName === "INPUT" || el.isContentEditable);
            if (!WH.shell.dirty && !typing && !j.thinking && !j.speaking && !j.pending && !j.awake) location.reload();
          } else if (c.ui) lastUi = c.ui;
        } catch {}
      }, 2000),
    ];
    return () => {
      window.removeEventListener("hashchange", onHash); document.removeEventListener("keydown", onKey);
      window.removeEventListener("beforeunload", onUnload); document.removeEventListener("visibilitychange", onVis);
      window.removeEventListener("pagehide", markSeen); iv.forEach(clearInterval);
    };
  }, []);

  useEffect(() => { const t = VIEWS[route.name].title; document.title = route.name === "home" ? "Jarvis" : `${t} · Jarvis`; }, [route.name]);

  return (
    <MotionConfig reducedMotion="user">
      {route.name === "office" ? (
        /* the side panel / big-screen dashboard on the external monitor: full-bleed, no sidebar */
        <motion.div key="office" initial={{ opacity: 0 }} animate={{ opacity: 1 }} transition={{ duration: 0.2 }}>
          <ViewHost name="office" sub="" />
        </motion.div>
      ) : route.name === "home" ? (
        /* the landing page (the owner, 12 Nov: "a landing page, where we click the button and then we enter the app"):
           full screen, no sidebar; "Enter Jarvis" goes to Today */
        <motion.div key="home" initial={{ opacity: 0 }} animate={{ opacity: 1 }} transition={{ duration: 0.35, ease: "easeOut" }}>
          <ViewHost name="home" sub="" />
        </motion.div>
      ) : (<>
        <Ambient />
        <DemoRibbon />
        <Sidebar name={route.name} collapsed={collapsed} onCollapse={toggleSb} />
        <MobileNav name={route.name} open={menu} onOpenChange={setMenu} />
        <div className={cn("jv-shell min-h-screen transition-[padding] duration-200 ease-out", collapsed ? "min-[900px]:pl-[68px]" : "min-[900px]:pl-[232px]")}>
          <TopBar name={route.name} onMenu={() => setMenu(true)} />
          <main ref={mainRef} className="relative mx-auto max-w-[1280px] px-[clamp(16px,3vw,36px)] pb-12 pt-6">
            {/* Enter-only page transition: the old view unmounts at once, so a page never waits on an exit animation. */}
            <motion.div key={route.name} className="jv-rise grid grid-cols-[minmax(0,1fr)] gap-7"
              initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.22, ease: "easeOut" }}>
              <ViewHost name={route.name} sub={route.sub} />
            </motion.div>
          </main>
        </div>
      </>)}
      <Palette name={route.name} />
      <AskDialog />
      <Jarvis />
      <Toaster />
    </MotionConfig>
  );
}
