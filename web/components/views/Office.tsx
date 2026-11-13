"use client";
/* Office — Jarvis on the external monitor (2 Oct). The app puts the window there as a slim side panel when a monitor is
   connected (Office.swift); widened, the same view becomes the big-screen dashboard. Four questions, at a glance:
   what needs me, what is running, how is the week going, and what did Jarvis just say (captions while it is quiet). */
import { useEffect, useState } from "react";
import { motion } from "framer-motion";
import { Check, CircleAlert, ShieldCheck, Clock, Cpu, Headphones, MicOff, Mic, Captions, Volume2, LayoutDashboard, ArrowUpRight } from "lucide-react";
import { WH, useWH } from "@/lib/wh";
import { useJarvis, setOfficeQuiet, talk } from "@/lib/jarvis";
import { useShell, useTick } from "@/lib/useShell";
import { cn } from "@/lib/utils";
import { Card, Empty } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Avatar, SrcIcon } from "@/components/ui/bits";
import { ArtBand } from "@/components/ui/kit";
import Core from "@/components/landing/Core";
import { needsList } from "@/components/views/Home";

const C = (s: any) => WH.clean(s);
const cut = (s: any, n = 120) => { s = String(s || "").replace(/\s+/g, " ").trim(); return s.length > n ? s.slice(0, n - 1).replace(/\s+\S*$/, "") + "…" : s; };
const mins = (a?: string, b?: string) => { const t0 = a ? +new Date(a) : 0, t1 = b ? +new Date(b) : Date.now(); return t0 ? Math.max(0, Math.round((t1 - t0) / 60000)) : 0; };
const dur = (m: number) => (m >= 60 ? `${Math.floor(m / 60)} h ${m % 60} min` : `${m} min`);

function Chip({ icon: Icon, children, tone = "mute", title }: { icon: any; children: React.ReactNode; tone?: "mute" | "ok" | "warn" | "ion"; title?: string }) {
  const c = tone === "mute" ? "var(--mute)" : `var(--${tone})`;
  return (
    <span title={title} className="inline-flex min-w-0 items-center gap-1.5 rounded-full border border-line px-2.5 py-1 text-[12px] font-medium text-ink-2" style={{ background: `color-mix(in srgb, ${c} 10%, var(--card))` }}>
      <Icon size={13} strokeWidth={2} style={{ color: c }} aria-hidden /><span className="truncate">{children}</span>
    </span>
  );
}

function Tile({ label, value, sub, tone }: { label: string; value: React.ReactNode; sub?: string; tone: "warn" | "ok" | "ion" }) {
  const c = `var(--${tone})`;
  return (
    <Card className="min-w-0 px-3.5 py-3" style={{ background: `linear-gradient(160deg, color-mix(in srgb, ${c} 9%, var(--card)), var(--card) 70%)` }}>
      <div className="truncate text-[12px] font-semibold text-ink-2">{label}</div>
      <div className="mt-1.5 truncate font-display text-[26px] font-semibold leading-none tracking-[-.02em] text-ink" style={{ fontVariantNumeric: "tabular-nums" }}>{value}</div>
      {sub && <div className="mt-1 truncate text-[12px] text-mute">{sub}</div>}
    </Card>
  );
}

const Head = ({ icon: Icon, title, n }: { icon: any; title: string; n?: number }) => (
  <div className="mb-2.5 flex items-center gap-2 font-mono text-[11.5px] font-semibold uppercase tracking-[.1em] text-mute">
    <Icon size={13} strokeWidth={2} aria-hidden />{title}{typeof n === "number" && n > 0 && <span className="text-ink-2">· {n}</span>}
  </div>
);

export default function OfficeView() {
  const [, force] = useState(0);
  useWH(["inbox", "week", "approvals"]);
  useTick(30000, () => force((x) => x + 1));
  useShell();
  const J = useJarvis(), o = J.office || {};
  const [jobs, setJobs] = useState<any[]>([]);
  useEffect(() => {
    let live = true;
    const load = () => WH.api("/api/jobs").then((d: any) => { if (live && d && Array.isArray(d.jobs)) setJobs(d.jobs); }).catch(() => {});
    load(); const t = setInterval(() => { if (!document.hidden) load(); }, 20000);
    return () => { live = false; clearInterval(t); };
  }, []);

  const now = new Date(), today = WH.iso(now);
  const needs = needsList(), approvals = WH.pendingApprovals();
  const running = jobs.filter((j) => j.state === "running" || j.state === "starting");
  const approve = async (id: string, choice?: string) => { if (await WH.setApproval(id, "approved", choice)) WH.toast("Approved. " + WH.whenApproved(), "ok", 4000); };
  const decline = async (id: string) => { if (await WH.setApproval(id, "declined")) WH.toast("Declined. Nothing will happen.", "info", 2500); };
  const open = (k: string) => { WH.focusItem = k; WH.go("inbox/todo/" + encodeURIComponent(k)); };
  const said = J.caption && (J.capKind === "say" || J.capKind === "heard" || J.capKind === "info" || J.capKind === "err");

  return (
    <div className="min-h-screen bg-canvas px-4 py-4" data-view="office">
      <div className="mx-auto grid w-full max-w-[1500px] grid-cols-[minmax(0,1fr)] gap-4">
        <ArtBand art="/media/screens/rest.jpg" whole>
        <header className="grid gap-3">
          <div className="flex items-center justify-between gap-3">
            <div className="flex min-w-0 items-center gap-4">
              {/* his orb, as on Home: it follows listening / thinking / speaking */}
              <Core size={64} reduce={false} state={J.speaking ? "speaking" : J.thinking ? "thinking" : J.awake ? "listening" : "idle"}
                onClick={J.native ? talk : undefined} label={J.native ? "Talk to Jarvis" : "Jarvis"} />
              <div className="min-w-0">
                <div className="font-mono text-[11px] font-semibold uppercase tracking-[.14em] text-mute">Jarvis · Office</div>
                <h1 className="mt-1 truncate font-display text-[24px] font-semibold leading-tight tracking-[-.02em] text-ink">{WH.hello(now)}</h1>
              </div>
            </div>
            <div className="relative z-[1] flex-none rounded-xl bg-black/45 px-3 py-1 text-right font-mono text-[22px] font-semibold text-ink-2 backdrop-blur-sm" style={{ fontVariantNumeric: "tabular-nums" }}>{now.toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit" })}</div>
          </div>
          <div className="flex flex-wrap gap-1.5">
            {o.quiet ? <Chip icon={Captions} tone="ion" title="People around: answers are shown here instead of spoken. Connect headphones to hear them.">Quiet · captions</Chip>
              : <Chip icon={o.headphones ? Headphones : Volume2} tone="ok">{o.headphones ? "Speaking in your headphones" : "Speaking out loud"}</Chip>}
            {o.no_mic ? <Chip icon={MicOff} tone="warn">No microphone</Chip> : o.input ? <Chip icon={Mic} title="The microphone Jarvis listens through">{o.input}</Chip> : null}
            {o.clamshell && <Chip icon={MicOff} tone={o.no_mic ? "warn" : "mute"} title="With the lid closed the laptop's own mic is off">Lid closed</Chip>}
          </div>
          {o.no_mic && (
            <Card className="border-[color:var(--warn)] px-3.5 py-2.5 text-[13.5px] text-ink-2">
              {o.clamshell ? "The lid is closed, so the laptop's mic is off. Plug in a headset or AirPods, or pick the monitor's mic in Sound settings." : "No microphone is available right now."}
            </Card>
          )}
        </header>
        </ArtBand>

        {(said || J.thinking) && (
          <motion.div initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.2 }}>
            <Card className="px-4 py-3" data-testid="office-caption">
              <div className="font-mono text-[10.5px] font-semibold uppercase tracking-[.12em] text-mute">{J.capKind === "heard" ? "You said" : J.thinking && !said ? "Jarvis" : "Jarvis says"}</div>
              <p className={cn("mt-1 text-[15.5px] leading-snug", J.capKind === "heard" ? "text-ink-2" : "text-ink")}>{said ? J.caption : "Thinking…"}</p>
            </Card>
          </motion.div>
        )}

        <div className="grid grid-cols-2 gap-2.5 min-[1100px]:grid-cols-3">
          <Tile label="Needs you" value={needs.length} tone="warn" sub={needs.length ? cut(needs[0].t, 40) : "all clear"} />
          <Tile label="Approvals" value={approvals.length} tone="ion" sub={approvals.length ? "waiting for your OK" : "none waiting"} />
          <Tile label="Running" value={running.length} tone="ok" sub={running.length ? "jobs right now" : "nothing running"} />
        </div>

        <div className="grid items-start gap-4 min-[1100px]:grid-cols-[minmax(0,1.5fr)_minmax(0,1fr)]">
          <section className="min-w-0 [overflow-wrap:anywhere]" aria-label="Needs you">
            <Head icon={CircleAlert} title="Needs you" n={needs.length} />
            {!needs.length ? <Empty icon={Check} tone="ok" title="Nothing needs you">Jarvis has the rest in hand.</Empty> : (
              <div className="grid gap-2">
                {needs.slice(0, 10).map((x) => (
                  <Card key={x.id} className="relative overflow-hidden px-3.5 py-3" data-row="office-need">
                    <span aria-hidden className="absolute inset-y-0 left-0 w-[3px]" style={{ background: x.c }} />
                    <div className="grid grid-cols-[30px_minmax(0,1fr)] items-start gap-2.5">
                      {x.ap ? <Avatar icon={ShieldCheck} color="var(--ion)" size={30} /> : <Avatar name={x.who} size={30} />}
                      <div className="min-w-0">
                        <div className="flex items-center gap-1.5"><b className="min-w-0 truncate text-[14px] font-semibold text-ink">{x.t}</b>{x.src && <SrcIcon s={x.src} />}</div>
                        {x.s && <small className="mt-0.5 line-clamp-2 block text-[12.5px] leading-snug text-mute">{cut(x.s, 160)}</small>}
                        <div className="mt-2 flex flex-wrap gap-1.5">
                          {x.ap ? (<>
                            <Button size="sm" variant="ghost" onClick={() => decline(x.ap.id)}>Not now</Button>
                            {(x.ap.options || []).length ? x.ap.options.map((v: any) => <Button key={v.id} size="sm" variant={v.recommended ? "primary" : "default"} onClick={() => approve(x.ap.id, v.id)}>{C(v.label)}</Button>)
                              : <Button size="sm" variant="primary" onClick={() => approve(x.ap.id)}><Check size={14} strokeWidth={1.8} />Go ahead</Button>}
                          </>) : <Button size="sm" variant={x.kind === "reply" ? "primary" : "default"} onClick={() => open(x.focus)}>{x.kind === "reply" ? "Review and send" : "Open"}<ArrowUpRight size={13} strokeWidth={1.8} /></Button>}
                        </div>
                      </div>
                    </div>
                  </Card>
                ))}
              </div>
            )}
          </section>

          <section className="min-w-0" aria-label="Running">
            <Head icon={Cpu} title="Running" n={running.length} />
            {!jobs.length ? <Empty icon={Clock} title="Nothing running">Long jobs (scenario runs, notebooks) show here with their progress.</Empty> : (
              <div className="grid gap-2">
                {jobs.map((j) => {
                  const live = j.state === "running" || j.state === "starting", pct = j.total ? Math.round(((j.done || 0) / j.total) * 100) : null;
                  const tone = live ? "var(--ion)" : j.state === "done" ? "var(--ok)" : "var(--warn)";
                  return (
                    <Card key={j.dir} className="px-3.5 py-3" data-row="office-job">
                      <div className="flex items-baseline justify-between gap-2">
                        <b className="min-w-0 truncate text-[13.5px] font-semibold text-ink">{cut(C(j.label), 70)}</b>
                        <span className="flex-none text-[12px] font-medium" style={{ color: tone }}>{live ? dur(mins(j.started)) : j.state === "done" ? "done" : "failed"}</span>
                      </div>
                      {pct !== null && (
                        <div className="mt-2">
                          <div className="h-1.5 overflow-hidden rounded-full bg-line"><div className="h-full rounded-full transition-[width] duration-500" style={{ width: `${pct}%`, background: tone }} /></div>
                          <div className="mt-1 text-[11.5px] text-mute" style={{ fontVariantNumeric: "tabular-nums" }}>{j.done} of {j.total} done</div>
                        </div>
                      )}
                      {!live && j.check && <div className="mt-1 truncate text-[11.5px] text-mute">{cut(C(j.check), 90)}</div>}
                    </Card>
                  );
                })}
              </div>
            )}
          </section>
        </div>

        <footer className="flex flex-wrap items-center gap-2 pt-1">
          <Button size="sm" variant="ghost" onClick={() => setOfficeQuiet(!(o.quiet_setting ?? true))} title="At the office without headphones: captions instead of speech">
            {(o.quiet_setting ?? true) ? <Volume2 size={14} strokeWidth={1.8} /> : <Captions size={14} strokeWidth={1.8} />}{(o.quiet_setting ?? true) ? "Speak out loud at the office" : "Captions at the office"}
          </Button>
          <Button size="sm" variant="ghost" onClick={() => WH.go("today")}><LayoutDashboard size={14} strokeWidth={1.8} />Full app</Button>
        </footer>
      </div>
    </div>
  );
}
