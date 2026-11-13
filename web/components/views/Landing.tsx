"use client";
/* Landing page: "Portal" (12 Nov 2026). the owner: "a landing page, where we click the button and then we enter the app",
   then "we need something really fancy and nice and awesome" (and, of the 5 Oct page put back unchanged: "you made it
   exactly same?"). So this is a new composition, not the old one:
   - the core (the WebGL orb, his pick of 2 Oct) in the middle with a halo and two sweeping rings; click it to talk;
   - the greeting as one large line under it, the status line, and ONE way in: "Enter Jarvis" (or the Enter key),
     which plays a short warp and opens Today;
   - four live numbers as glass tiles (each goes straight to its screen);
   - behind everything, the map of what Jarvis knows turning slowly, soft drifting light, and a floor grid running
     toward the horizon; the scene leans a little with the pointer;
   - "Explore the map" (bottom left) clears the stage and makes the map a tool: search, click a node, open it.
   Full screen, no sidebar, always in its own dark scene (<html data-scene="core">, globals.css) whatever the Mac setting.
   Earlier pages: state/oneoff/landing_calm_bak_1112/ (the calm one, and Mission control of 5 Oct). */
import { useEffect, useLayoutEffect, useMemo, useReducer, useRef, useState } from "react";
import { motion } from "framer-motion";
import { ArrowLeft, ArrowRight, Radar } from "lucide-react";
import { WH, useWH } from "@/lib/wh";
import { useShell, useTick } from "@/lib/useShell";
import { cn } from "@/lib/utils";
import { NumberTicker } from "@/components/motion";
import { needsList, pendingWork, doorNumbers } from "@/components/views/Home";
import { useJarvis, talk } from "@/lib/jarvis";
import BrainMap, { KINDS, type Brain, type BNode, type MapHandle } from "@/components/landing/BrainMap";
import MapUI from "@/components/landing/MapUI";
import Core, { type CoreState } from "@/components/landing/Core";
import Assembly from "@/components/landing/Assembly";

// 13 Nov (the owner: "is it possible to make it interactive?", then "go ahead complete everything"): the machine is the way in.
// Hold anywhere on the stage (or scroll) to take it apart, let go and it locks back together; its parts lead to the screens.
// Positions are fractions of the 16:9 frame, read off the still (landing-still.jpg).
const PARTS: { id: string; x: number; y: number; part: string; what: string; to: string }[] = [
  { id: "cutter", x: 0.17, y: 0.57, part: "Cutter", what: "takes the work in", to: "inbox" },
  { id: "gearbox", x: 0.615, y: 0.47, part: "Gearbox", what: "what Jarvis knows", to: "brain" },
  { id: "engine", x: 0.8, y: 0.53, part: "Engine", what: "what is running", to: "activity" },
  { id: "cab", x: 0.86, y: 0.385, part: "Controls", what: "how far Jarvis may act", to: "trust" },
  { id: "tracks", x: 0.72, y: 0.77, part: "Tracks", what: "what goes out", to: "deploy" },
];
import { useApi, cut } from "@/components/ui/kit";

const C = (s: any) => WH.clean(s);
const OUT = [0.16, 1, 0.3, 1] as const;
// The Enter fly-in clip (/media/landing-enter.mp4, plan F3) is not made yet: off, so the page does not ask for a missing file.
const ENTER_CLIP = false;

export default function Landing() {
  useWH(["inbox", "tickets", "approvals", "theme", "me"]);
  const media = !!WH.me.media;                          // the film and the stills are an optional pack
  const [, force] = useReducer((x: number) => x + 1, 0);
  useTick(30000, force);
  useShell();
  const J = useJarvis();
  const [brain] = useApi<Brain>("/api/brain", 120000);
  const [spine] = useApi<any>("/api/spine", 15000);
  const [explore, setExplore] = useState(false), [q, setQ] = useState(""), [sel, setSel] = useState<BNode | null>(null);
  const [leaving, setLeaving] = useState(false);
  // 13 Nov (the owner: "the feel the way people have created next level websites using higgsfield"): the landing's film.
  // /media/landing-loop.mp4 plays full-bleed behind the hero; /media/landing-enter.mp4 is the fly-in played on Enter.
  // Neither is required: until a clip loads, the page is exactly the WebGL scene it was.
  const [flyOk, setFlyOk] = useState(false), [flying, setFlying] = useState(false);
  const fly = useRef<HTMLVideoElement>(null);
  // 13 Nov (the owner: "a machine sort of gets joined, parts coming in ... ultra realistic and premium"): the opening is a film of
  // the parts flying in and locking into the machine (Veo 3.1 in Google Flow, played backwards: /media/landing-assemble.mp4),
  // once per app open; then the sharp still of the finished machine (/media/landing-still.jpg) holds the stage with a slow
  // drift. The greeting arrives as the last parts seat. Reduced motion, an earlier play this session, or a film that will not
  // start go straight to the still; no still either = the WebGL scene as before. The frame is shown whole (never cropped), so
  // the provider's mark stays visible.
  const [film, setFilm] = useState<"off" | "play" | "still">("off"), [heroIn, setHeroIn] = useState(true), [filmGone, setFilmGone] = useState(false);
  const filmRef = useRef<HTMLVideoElement>(null);
  const settle = () => { setFilm("still"); setHeroIn(true); try { sessionStorage.setItem("jv_film", "1"); } catch {} };
  // the interaction: apart = how far it is taken apart (0 complete .. 1 all parts out); live = the frame canvas is showing
  const [apart, setApart] = useState(0), [live, setLive] = useState(false), [hint, setHint] = useState(false), [hot, setHot] = useState("");
  const holdT = useRef(0), idleT = useRef(0), liveT = useRef(0), wheelV = useRef(0);
  const [vh, setVh] = useState(() => (typeof window !== "undefined" ? window.innerHeight : 900));
  // 13 Nov: the map as a tool has its own filters (kinds, open items only) and a camera the panels can steer
  const [show, setShow] = useState<Set<string>>(() => new Set(Object.keys(KINDS)));
  const [openOnly, setOpenOnly] = useState(false);
  const mapRef = useRef<MapHandle | null>(null);
  const [wide, setWide] = useState(true);
  useEffect(() => { const on = () => setWide(window.innerWidth > 900); on(); window.addEventListener("resize", on); return () => window.removeEventListener("resize", on); }, []);
  const exploreRef = useRef(false);
  useEffect(() => { exploreRef.current = explore; }, [explore]);
  const reduce = useMemo(() => typeof matchMedia !== "undefined" && matchMedia("(prefers-reduced-motion: reduce)").matches, []);
  const root = useRef<HTMLDivElement>(null);

  // the scene's colours live on <html> while this page is open
  useLayoutEffect(() => {
    document.documentElement.dataset.scene = "core";
    return () => { delete document.documentElement.dataset.scene; };
  }, []);
  useEffect(() => {
    let seen = false;
    try { seen = sessionStorage.getItem("jv_film") === "1"; } catch {}
    if (!media) { setFilm("off"); setFilmGone(true); setHeroIn(true); return; }   // no pictures: the page stands without them
    if (reduce || seen) { setFilm("still"); setFilmGone(true); return; }
    setFilm("play"); setHeroIn(false);
    const t = window.setTimeout(() => setHeroIn(true), 8000);
    return () => window.clearTimeout(t);
  }, [reduce, media]);
  useEffect(() => {
    if (film !== "still") return;
    let used = false;
    try { used = localStorage.getItem("jv_apart_used") === "1"; } catch {}
    if (!used) { const t = window.setTimeout(() => setHint(true), 1200); return () => window.clearTimeout(t); }
  }, [film]);
  useEffect(() => {
    if (film !== "play") return;
    const v = filmRef.current;
    if (v) v.play().catch(() => { settle(); setFilmGone(true); });
  }, [film]);
  useEffect(() => { const on = () => setVh(window.innerHeight); window.addEventListener("resize", on); return () => window.removeEventListener("resize", on); }, []);

  // the way in: a short warp, then Today (or the screen of the tile he pressed)
  const goIn = (to = "today") => {
    if (leaving) return;
    if (reduce) { WH.go(to); return; }
    setLeaving(true);
    const v = fly.current;
    if (flyOk && v && v.readyState >= 3) {
      setFlying(true); v.currentTime = 0; v.play().catch(() => {});
      const ms = Math.max(900, Math.min(4000, (v.duration || 2) * 1000 - 350));
      window.setTimeout(() => WH.go(to), ms);
      return;
    }
    // 12 Nov (the owner: "glow that comes in when clicking enter needs to be better"): light bursts from the core (a white-hot
    // bloom, two shockwave rings, light streaks), fills the window with the app's own background colour, the app opens
    // under it, and the light fades off the app. It lives on <body>, so it is not cut off when this page goes.
    const o = root.current?.querySelector("[data-orb]")?.getBoundingClientRect();
    const el = document.createElement("div");
    el.className = "jv-warp2"; el.setAttribute("aria-hidden", "true");
    el.style.setProperty("--wx", (o ? o.left + o.width / 2 : window.innerWidth / 2) + "px");
    el.style.setProperty("--wy", (o ? o.top + o.height / 2 : window.innerHeight * 0.4) + "px");
    el.style.setProperty("--wfill", document.documentElement.dataset.theme === "dark" ? "#0f1317" : "#f5f6f8");
    el.innerHTML = '<i class="w-rays"></i><i class="w-bloom"></i><i class="w-ring"></i><i class="w-ring is-2"></i><i class="w-fill"></i>';
    document.body.appendChild(el);
    window.setTimeout(() => WH.go(to), 560);
    window.setTimeout(() => el.classList.add("is-out"), 640);
    window.setTimeout(() => el.remove(), 1400);
  };
  const canPlay = film === "still" && !explore && !leaving && !reduce;
  const takeApart = (v: number) => {
    window.clearTimeout(liveT.current);
    setLive(true); setApart(Math.max(0, Math.min(1, v)));
    if (hint) { setHint(false); try { localStorage.setItem("jv_apart_used", "1"); } catch {} }
  };
  const putBack = () => {
    setApart(0);
    window.clearTimeout(liveT.current);
    liveT.current = window.setTimeout(() => setLive(false), 1500);     // the canvas has glided home: back to the sharp still
  };
  const onStageDown = (e: React.PointerEvent) => {
    if (!canPlay || e.button !== 0) return;
    if ((e.target as HTMLElement).closest("a,button,input,textarea,[role=button],[data-no-apart]")) return;
    window.clearTimeout(holdT.current);
    holdT.current = window.setTimeout(() => takeApart(1), 140);        // a short press is not a hold
  };
  const onStageUp = () => { window.clearTimeout(holdT.current); if (apart > 0) putBack(); };
  const onStageWheel = (e: React.WheelEvent) => {
    if (!canPlay) return;
    wheelV.current = Math.max(0, Math.min(1, (live ? wheelV.current : 0) + e.deltaY / 900));
    takeApart(wheelV.current);
    window.clearTimeout(idleT.current);
    idleT.current = window.setTimeout(() => { wheelV.current = 0; putBack(); }, 1800);
  };
  const goRef = useRef(goIn);
  goRef.current = goIn;
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const el = document.activeElement as HTMLElement | null;
      if (e.key !== "Enter" || e.metaKey || e.ctrlKey || e.altKey || exploreRef.current) return;
      if (el && (el.tagName === "INPUT" || el.tagName === "TEXTAREA" || el.tagName === "BUTTON" || el.tagName === "A" || el.isContentEditable || el.closest("[role=dialog]"))) return;
      e.preventDefault(); goRef.current();
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, []);
  // the scene leans a little with the pointer
  useEffect(() => {
    if (reduce) return;
    let raf = 0;
    const on = (e: MouseEvent) => {
      cancelAnimationFrame(raf);
      raf = requestAnimationFrame(() => {
        const el = root.current;
        if (!el) return;
        el.style.setProperty("--mx", (e.clientX / window.innerWidth - 0.5).toFixed(3));
        el.style.setProperty("--my", (e.clientY / window.innerHeight - 0.5).toFixed(3));
      });
    };
    window.addEventListener("mousemove", on);
    return () => { window.removeEventListener("mousemove", on); cancelAnimationFrame(raf); };
  }, [reduce]);

  const loaded = WH.store.inboxLoaded, now = new Date();
  const needs = loaded ? needsList() : [], busy = loaded ? pendingWork() : 0, open = WH.openItems();
  const { check } = doorNumbers();
  const running = spine && Array.isArray(spine.running) ? spine.running.length : null;
  const line = !loaded ? "Waking up…" : needs.length ? `${WH.plural(needs.length, "thing needs", "things need")} you.` : busy ? `Jarvis is working on ${WH.plural(busy, "new item")}.` : "Nothing needs you right now.";
  const core: CoreState = J.speaking ? "speaking" : J.thinking ? "thinking" : J.awake ? "listening" : "idle";
  const stateWord = J.speaking ? "Speaking" : J.thinking ? "Thinking" : J.awake ? "Listening" : "Online";
  const staged = film !== "off" && !explore;
  const orb = staged ? Math.round(Math.max(84, Math.min(116, vh * 0.115))) : Math.round(Math.max(190, Math.min(330, vh * 0.32)));
  const stats: [string, any, string, string][] = [
    ["Needs you", loaded ? needs.length : "–", "today", needs.length ? "text-[#ffcf7a]" : "text-[#8fe3b4]"],
    ["Open items", loaded ? open.length : "–", "inbox", "text-glass-ink"],
    ["Working now", running ?? "–", "activity", "text-glass-ink"],
    ["Tickets to check", check, "tickets", "text-glass-ink"],
  ];
  const enter = (d: number) => ({ "data-in": String(d) });
  return (
    <div ref={root} data-view="landing" data-layout="portal" data-film={film} data-apart={apart > 0 ? "1" : undefined}
      onPointerDown={onStageDown} onPointerUp={onStageUp} onPointerLeave={onStageUp} onPointerCancel={onStageUp} onWheel={onStageWheel}
      className="relative h-[100dvh] min-h-[620px] overflow-hidden text-glass-ink"
      style={{ background: "radial-gradient(120% 90% at 50% 40%, #06202f 0%, #020a10 52%, #010406 100%)" }}>
      {/* the map of what Jarvis knows: ambience behind the hero, a tool in explore mode */}
      <div className={cn("absolute inset-0 transition-opacity duration-500", explore ? "opacity-100" : staged ? "pointer-events-none opacity-0" : "pointer-events-none opacity-70")}>
        {(!staged || explore) && <BrainMap ref={mapRef} data={brain} show={show} query={explore ? q : ""} holo={!explore} selected={sel?.id || null} onSelect={(n) => { setSel(n); if (n && explore) mapRef.current?.focus(n.id); }} reduce={reduce}
          openOnly={explore && openOnly} fit={explore && wide ? { l: 340, t: 80, r: 360, b: 96 } : { l: 60, t: 70, r: 60, b: 70 }} quiet={!explore} />}
      </div>
      {media && film !== "off" && (
        <div className={cn("jv-stage absolute inset-0 transition-opacity duration-700", explore ? "pointer-events-none opacity-0" : "opacity-100")}>
          <img src="/media/landing-ext.jpg" alt="" aria-hidden decoding="async" className="jv-stage-ext pointer-events-none absolute inset-0 h-full w-full object-cover" />
          <div className="jv-lean pointer-events-none absolute inset-0 grid place-items-center">
            <div className="jv-frame relative">
              <div className={cn("absolute inset-0", film === "still" && !reduce && !live && "jv-drift")}>
                <img src="/media/landing-still.jpg" alt="" aria-hidden decoding="async" onError={() => setFilm("off")}
                  className={cn("jv-still absolute inset-0 h-full w-full object-cover transition-opacity duration-500", live ? "opacity-0" : "opacity-100")} />
                {film === "still" && !reduce && (
                  <Assembly progress={1 - apart} from={1} speed={3.2} fit="fill"
                    className={cn("absolute inset-0 h-full w-full transition-opacity duration-300", live ? "opacity-100" : "opacity-0")} />
                )}
                {!filmGone && !reduce && (
                  <video ref={filmRef} src="/media/landing-assemble.mp4?v=1440" poster="/media/landing-poster.jpg?v=1440" muted playsInline preload="auto" disablePictureInPicture disableRemotePlayback aria-hidden
                    className={cn("jv-film absolute inset-0 h-full w-full object-cover transition-opacity duration-[1400ms]", film === "play" ? "opacity-100" : "opacity-0")}
                    onTimeUpdate={(e) => {
                      const t = e.currentTarget.currentTime;
                      if (t > 4.6) setHeroIn(true);
                      // the machine is fully together and still from about 6.1 s: hand over to the sharp 2K still now
                      if (t > 6.1 && film === "play") { settle(); window.setTimeout(() => setFilmGone(true), 1600); }
                    }}
                    onEnded={() => { settle(); window.setTimeout(() => setFilmGone(true), 1600); }}
                    onError={() => { settle(); setFilmGone(true); }} />
                )}
              </div>
              {/* the machine's parts lead to the screens */}
              {film === "still" && heroIn && !explore && (
                <div className={cn("absolute inset-0 transition-opacity duration-300", apart > 0.02 ? "opacity-0" : "opacity-100")}>
                  {PARTS.map((p) => (
                    <button key={p.id} type="button" data-no-apart className={cn("jv-hot pointer-events-auto", hot === p.id && "is-on")}
                      style={{ left: `${p.x * 100}%`, top: `${p.y * 100}%` }} aria-label={`${p.part}: ${p.what}`}
                      onMouseEnter={() => setHot(p.id)} onMouseLeave={() => setHot("")} onFocus={() => setHot(p.id)} onBlur={() => setHot("")}
                      onClick={(e) => { e.stopPropagation(); goIn(p.to); }}>
                      <i aria-hidden />
                      <span className="jv-hot-label"><b>{p.part}</b>{p.what}</span>
                    </button>
                  ))}
                </div>
              )}
            </div>
          </div>
          <div aria-hidden className="jv-stage-shade pointer-events-none absolute inset-0" />
        </div>
      )}
      {!staged && <div aria-hidden className="jv-par-1 pointer-events-none absolute inset-0"><div className="jv-aurora" /></div>}
      {!staged && <div aria-hidden className={cn("jv-floor transition-opacity duration-500", explore && "opacity-0")} />}
      {!staged && <div aria-hidden className="jv-grain pointer-events-none absolute inset-[-10%] z-[5] opacity-40" />}
      <div aria-hidden className={cn("pointer-events-none absolute inset-0 transition-opacity duration-500", (explore || staged) && "opacity-0")}
        style={{ background: "radial-gradient(58% 60% at 50% 44%, rgba(1,4,6,0) 0%, rgba(1,4,6,.45) 62%, rgba(1,4,6,.86) 100%)" }} />

      <div className="absolute inset-x-0 top-0 z-20 flex items-center gap-4 px-6 py-5 font-mono text-[10.5px] uppercase tracking-[.22em] text-glass-mute">
        <span className="font-semibold tracking-[.36em] text-glass-ink">Jarvis</span>
        <span className="hidden min-[760px]:inline">{brain ? `${brain.nodes.length} things · ${brain.links.length} connections` : "reading the map"}</span>
        <span className="ml-auto flex items-center gap-2">
          <i className={cn("arc-dot", (J.thinking || !!running) && "is-live")} />{stateWord}
          <span className="hidden min-[560px]:inline">· {now.toLocaleDateString("en-GB", { weekday: "short", day: "numeric", month: "short" })} · {now.toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit" })}</span>
        </span>
      </div>

      {/* the hero */}
      {/* 13 Nov: while the map is the tool the faded greeting is inert: it cannot be clicked, tabbed to, or take Enter */}
      <div inert={explore} className={cn("jv-par-2 absolute inset-0 z-10", (explore || staged) && "pointer-events-none")}>
        <motion.div className={cn("flex h-full flex-col items-center px-6 text-center", staged ? "justify-between pb-[clamp(60px,8.5vh,92px)] pt-[clamp(34px,4.6vh,52px)]" : "justify-center pb-12 pt-10")}
          animate={leaving ? { opacity: 0, scale: 2.1, filter: "blur(14px)" } : explore ? { opacity: 0, scale: 0.96, filter: "blur(8px)" } : { opacity: 1, scale: 1, filter: "blur(0px)" }}
          transition={{ duration: leaving ? 0.56 : 0.4, ease: leaving ? [0.6, 0, 0.8, 0.4] : OUT }}>
          <div className={cn(!explore && "pointer-events-auto", "flex flex-col items-center transition-[opacity,transform,filter] duration-[1100ms] ease-out", !heroIn ? "translate-y-3 opacity-0 blur-[6px]" : apart > 0.05 ? "opacity-25" : "opacity-100")}>
          <motion.div {...enter(0)} data-orb className="jv-in relative flex-none" style={{ width: orb, height: orb }}>
            {/* his orb (picked 2 Oct); over the machine it is smaller and drawn with the library's small-size preset */}
            <span aria-hidden className="jv-halo" /><span aria-hidden className="jv-sweep" /><span aria-hidden className="jv-sweep is-2" />
            {(heroIn || !staged) && <Core state={core} size={orb} reduce={reduce} onClick={J.native ? talk : undefined} label={J.native ? "Talk to Jarvis" : "Jarvis"} />}
          </motion.div>
          <motion.h1 {...enter(0.1)} className={cn("jv-in jv-d1 jv-hero-title m-0", staged ? "mt-2 !text-[clamp(34px,4.6vw,64px)]" : "mt-[clamp(8px,2.2vh,24px)]")}>{WH.hello(now)}</motion.h1>
          <motion.p {...enter(0.18)} className="jv-in jv-d2 m-0 mt-3 text-[clamp(15px,1.5vw,19px)] text-glass-ink-2">{line}</motion.p>
          <motion.div {...enter(0.28)} className={cn("jv-in jv-d3 flex flex-col items-center gap-3", staged ? "mt-[clamp(12px,2.2vh,22px)]" : "mt-[clamp(16px,3.2vh,32px)]")}>
            <a href="#/today" data-enter className="btn-arc btn-enter" onClick={(e) => { e.preventDefault(); goIn(); }}>Enter Jarvis <ArrowRight size={18} strokeWidth={2} /></a>
            {!staged && <span aria-hidden className="font-mono text-[10.5px] uppercase tracking-[.22em] text-glass-mute">or press Enter ↵</span>}
          </motion.div>
          </div>
          <div className="flex w-full flex-col items-center gap-3">
          {/* the hint sits over the floor, above the tiles, so it never runs into the machine under the greeting */}
          {staged && film === "still" && !reduce && (
            <span aria-hidden className={cn("font-mono text-[10.5px] uppercase tracking-[.22em] text-[#8fdcea] [text-shadow:0_1px_10px_rgba(0,0,0,.95)] transition-opacity duration-700", hint && apart === 0 ? "opacity-90" : "opacity-0")}>
              Hold anywhere to take it apart · hover a part to open it
            </span>
          )}
          {/* 13 Nov: while the map is the tool, the faded greeting and tiles must not catch the pointer over it */}
          <motion.div {...enter(0.4)} className={cn(!explore && "pointer-events-auto", "jv-in jv-d4 grid w-full max-w-[780px] grid-cols-4 gap-3 transition-[opacity,transform] duration-[1100ms] ease-out max-[640px]:grid-cols-2", staged ? "" : "mt-[clamp(16px,3.4vh,38px)]", heroIn ? "opacity-100" : "translate-y-4 opacity-0")}>
            {stats.map(([label, value, to, tone]) => (
              <a key={label} href={"#/" + to} onClick={(e) => { e.preventDefault(); goIn(to); }} className="glass-panel jv-stat px-4 py-3 text-left no-underline">
                <div className="font-mono text-[10px] uppercase tracking-[.2em] text-glass-mute">{label}</div>
                <div className={cn("mt-1.5 text-[30px] font-semibold leading-none tracking-[-.02em] tabular-nums", tone)}>
                  {typeof value === "number" ? <NumberTicker value={value} from={0} duration={0.9} /> : value}
                </div>
              </a>
            ))}
          </motion.div>
          </div>
        </motion.div>
      </div>

      {!reduce && ENTER_CLIP && (
        <video ref={fly} aria-hidden className={cn("pointer-events-none fixed inset-0 z-[60] h-full w-full object-cover transition-opacity duration-300", flying ? "opacity-100" : "opacity-0")}
          src="/media/landing-enter.mp4" muted playsInline preload="auto" onCanPlayThrough={() => setFlyOk(true)} onError={() => setFlyOk(false)} />
      )}

      {/* the map as a tool */}
      <button type="button" onClick={() => { exploreRef.current = !explore; setExplore((v) => !v); setSel(null); setQ(""); setShow(new Set(Object.keys(KINDS))); setOpenOnly(false); }}
        aria-label={explore ? "Back" : "Explore the map"} className="absolute bottom-5 left-6 z-30 inline-flex items-center gap-2 rounded-full border border-[rgba(159,231,255,.2)] bg-[rgba(6,18,26,.62)] px-3.5 py-2 font-mono text-[10.5px] uppercase tracking-[.2em] text-glass-ink-2 backdrop-blur transition-colors hover:text-glass-ink">
        {explore ? <><ArrowLeft size={13} /> Back</> : <><Radar size={13} /><span className="max-[560px]:hidden">Explore the map</span></>}
      </button>
      {explore && <MapUI brain={brain} sel={sel} onPick={setSel} q={q} setQ={setQ} show={show} setShow={setShow} openOnly={openOnly} setOpenOnly={setOpenOnly} map={mapRef} />}
    </div>
  );
}
