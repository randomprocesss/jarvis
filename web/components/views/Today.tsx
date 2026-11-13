"use client";
/* Today (redesign, 12 Nov 2026): what needs you first, then what Jarvis did, what is owed, and what it is trying from
   outside research. The lists and the approve / decline / open actions are the ones the old Today used (Home.tsx). */
import { useEffect, useReducer, useRef, useState } from "react";
import { Check, ArrowRight, Coffee, Sparkles } from "lucide-react";
import { WH, useWH } from "@/lib/wh";
import { useShell, useTick } from "@/lib/useShell";
import { cn } from "@/lib/utils";
import { Card, Empty, SkeletonList } from "@/components/ui/card";
import { Button, PillButton } from "@/components/ui/button";
import { Avatar, SrcIcon } from "@/components/ui/bits";
import { ShieldCheck } from "lucide-react";
import { needsList, didList, pendingWork, sinceSeen, doorNumbers } from "@/components/views/Home";
import { useApi, Panel, Chip, cut, when } from "@/components/ui/kit";
import { HeldList } from "@/components/views/Trust";
import Assembly from "@/components/landing/Assembly";

const C = (s) => WH.clean(s);

function Tile({ label, value, sub, tone, href }: { label: string; value: React.ReactNode; sub?: React.ReactNode; tone?: "warn" | "ok" | "ion" | "bad"; href?: string }) {
  const body = (
    <Card lift={!!href} className="min-w-0 px-4 py-3.5">
      <div className="text-[12.5px] font-medium text-mute">{label}</div>
      <div className={cn("mt-1 text-[28px] font-semibold leading-none tracking-[-.02em] tabular-nums", tone === "warn" ? "text-warn" : tone === "bad" ? "text-bad" : tone === "ok" ? "text-ok" : "text-ink")}>{value}</div>
      {sub && <div className="mt-1.5 truncate text-[12.5px] text-mute">{sub}</div>}
    </Card>
  );
  return href ? <a href={href} className="block min-w-0 text-inherit no-underline">{body}</a> : body;
}

export default function Today() {
  const [spine] = useApi<any>("/api/spine", 10000);
  const [kb] = useApi<any>("/api/kb", 120000);
  const [res] = useApi<any>("/api/research", 60000);
  const [auto, reloadAuto] = useApi<any>("/api/autonomy", 20000);
  const heldNow = ((auto && auto.held) || []).filter((e) => e.state === "held");
  useWH(["inbox", "tickets", "approvals", "me"]);
  const [, force] = useReducer((x) => x + 1, 0);
  useTick(60000, force);
  useShell();
  const since = useRef<Date>(null as any);
  if (!since.current) since.current = sinceSeen();
  const [all, setAll] = useState(false);
  const [kick, setKick] = useState(false);       // a click on the machine: it comes apart for a moment, then shows what holds it apart

  const loaded = WH.store.inboxLoaded, now = new Date();
  const needs = loaded ? needsList() : [], did = loaded ? didList(since.current) : [], busy = loaded ? pendingWork() : 0;
  const shown = all ? needs : needs.slice(0, 8);
  const running = spine && Array.isArray(spine.running) ? spine.running : null;
  const { check } = doorNumbers();
  const promises = (kb && kb.promises) || [];
  const mine = (p) => WH.isMe(p.who);
  const owe = promises.filter(mine), owed = promises.filter((p) => !mine(p));
  const findings = (res && res.findings) || [], trying = findings.filter((f) => f.trial_state === "active" || f.trial_state === "running"), tried = findings.filter((f) => f.state === "tried");

  const approve = async (id, choice?) => { if (await WH.setApproval(id, "approved", choice)) WH.toast("Approved. " + WH.whenApproved(), WH.store.approvals.executor ? "ok" : "info", 4500); };
  const decline = async (id) => { if (await WH.setApproval(id, "declined")) WH.toast("Declined. Nothing will happen.", "info", 2500); };
  const focus = (k) => { WH.focusItem = k; WH.go("inbox/todo/" + encodeURIComponent(k)); };
  const line = !loaded ? "" : needs.length ? `${WH.plural(needs.length, "thing needs", "things need")} you.` : busy ? `Jarvis is working on ${WH.plural(busy, "new item")}.` : "Nothing needs you right now.";

  return (
    <div className="grid grid-cols-[minmax(0,1fr)] gap-6">
      {/* 13 Nov (plan phase 2): the day as a machine. It is as assembled as the day is clear: every thing that needs him holds
          some parts apart, and they glide in as he clears them. Dark in both themes, like a lit studio set into the page. */}
      <header className="jv-dayband relative isolate min-h-[188px] overflow-hidden rounded-[20px] max-[700px]:min-h-[220px]">
        <Assembly progress={kick ? 0 : loaded ? 1 - 0.85 * Math.min(needs.length, 6) / 6 : 0} anchor={0.7} speed={kick ? 4 : 1.4}
          title={needs.length ? "See what is holding the machine apart" : "Everything is in place"}
          onClick={() => {
            setKick(true);
            window.setTimeout(() => setKick(false), 900);
            if (needs.length) document.querySelector('[data-section="needs"]')?.scrollIntoView({ behavior: "smooth", block: "start" });
          }}
          className="absolute inset-y-0 right-0 z-[1] h-full w-[68%] cursor-pointer outline-none focus-visible:ring-2 focus-visible:ring-[#7fd6e6] max-[700px]:w-full max-[700px]:opacity-60" />
        <div aria-hidden className="jv-dayband-fade pointer-events-none absolute inset-0 z-[2]" />
        <div className="pointer-events-none relative z-[3] flex min-h-[188px] max-w-[52%] flex-col justify-center px-7 py-6 max-[700px]:max-w-none">
          <div className="text-[12.5px] font-medium text-[#9aa7b2]">{now.toLocaleDateString("en-GB", { weekday: "long", day: "numeric", month: "long" })}</div>
          <h1 className="mt-1 text-[30px] font-semibold leading-tight tracking-[-.02em] text-white">{WH.hello(now)}</h1>
          <p className="mt-1 text-[15px] text-[#c8d2da]" data-testid="today-line">{line || " "}</p>
          {loaded && <p className="mt-2.5 font-mono text-[10.5px] uppercase tracking-[.2em] text-[#7fd6e6]">{needs.length ? "Clear them and the machine comes together" : "Everything is in place"}</p>}
        </div>
      </header>

      {heldNow.length > 0 && (
        <Panel title="Going out soon" n={heldNow.length} right="held 10 minutes, then sent unless you stop it" pad={false}><HeldList rows={heldNow} reload={reloadAuto} compact /></Panel>
      )}

      <div className="grid grid-cols-4 gap-3 max-[900px]:grid-cols-2">
        <Tile label="Needs you" value={loaded ? needs.length : "–"} tone={needs.length ? "warn" : "ok"} sub={needs.length ? "decisions, approvals, replies to send" : "all clear"} />
        <Tile label="Working now" value={running ? running.length : "–"} sub={running && running[0] ? cut(running[0].title, 44) : WH.lastRoundup() ? "inbox checked " + WH.fmtWhen(WH.lastRoundup()) : ""} href="#/activity" />
        <Tile label="Done since you left" value={loaded ? did.length : "–"} sub={"since " + WH.fmtWhen(since.current)} href="#/activity" />
        <Tile label="You owe" value={kb ? owe.length : "–"} sub={kb ? `${owed.length} owed to you` : ""} href="#/brain/promises" />
      </div>

      <div className="grid items-start gap-6 min-[1100px]:grid-cols-[minmax(0,1.6fr)_minmax(0,1fr)]">
        <section className="min-w-0" data-section="needs" aria-label="Needs you">
          <div className="mb-2.5 flex items-baseline gap-2.5">
            <h2 className="m-0 text-[17px] font-semibold tracking-[-.01em]">Needs you</h2>
            {loaded && <Chip tone={needs.length ? "warn" : "ok"}>{needs.length}</Chip>}
          </div>
          {!loaded ? <SkeletonList rows={3} />
            : !needs.length ? (busy ? <Empty icon={Sparkles} title="Nothing yet">Jarvis is working on {WH.plural(busy, "new item")}. Anything that needs you shows up here.</Empty> : <Empty icon={Coffee} tone="ok" title="Nothing needs you right now">New asks appear here as soon as Jarvis has read them.</Empty>)
            : (
              <div className="grid gap-2">
                {shown.map((x) => {
                  const opts = (x.ap && x.ap.options) || [], stack = !!(x.ap && opts.length);
                  return (
                    <Card key={x.id} data-row="need" data-kind={x.kind} className="relative overflow-hidden">
                      <span aria-hidden className="absolute inset-y-0 left-0 w-[3px]" style={{ background: x.c }} />
                      <div className={cn("grid items-center gap-x-3.5 gap-y-2.5 py-3 pl-5 pr-4", stack ? "grid-cols-[32px_minmax(0,1fr)]" : "grid-cols-[32px_minmax(0,1fr)_auto] max-[680px]:grid-cols-[32px_minmax(0,1fr)]")}>
                        {x.ap ? <Avatar size={32} icon={ShieldCheck} color="var(--ion)" /> : <Avatar size={32} name={x.who} />}
                        <div className="min-w-0">
                          <div className="flex items-center gap-2"><b className="block min-w-0 text-[14.5px] font-semibold leading-snug text-ink">{x.t}</b>{x.src && <SrcIcon s={x.src} />}</div>
                          {x.s && <small className="mt-0.5 line-clamp-2 block text-[13.5px] leading-snug text-mute">{cut(x.s, 200)}</small>}
                        </div>
                        <div className={cn("flex flex-wrap justify-end gap-1.5", stack ? "col-start-2 justify-start" : "max-[680px]:col-start-2 max-[680px]:justify-start")}>
                          {x.ap ? (<>
                            <Button size="sm" variant="ghost" onClick={() => decline(x.ap.id)}>Not now</Button>
                            {opts.length ? opts.map((v) => <Button key={v.id} size="sm" className="h-auto whitespace-normal py-1.5 text-left" variant={v.recommended ? "primary" : "default"} onClick={() => approve(x.ap.id, v.id)}>{C(v.label)}</Button>)
                              : <Button size="sm" variant="primary" onClick={() => approve(x.ap.id)}><Check size={14} strokeWidth={2} />Go ahead</Button>}
                          </>) : <Button size="sm" variant={x.kind === "reply" ? "primary" : "default"} onClick={() => focus(x.focus)}>{x.kind === "reply" ? "Review and send" : "Open"}</Button>}
                        </div>
                      </div>
                    </Card>
                  );
                })}
                {needs.length > shown.length && <button type="button" onClick={() => setAll(true)} className="mt-1 inline-flex w-fit items-center gap-1.5 border-0 bg-transparent p-0 text-[13.5px] font-semibold text-ion hover:underline">Show {needs.length - shown.length} more <ArrowRight size={14} /></button>}
              </div>
            )}
        </section>

        <div className="grid min-w-0 gap-4">
          <Panel title="What Jarvis did" right={"since " + WH.fmtWhen(since.current)}>
            {!loaded ? <div className="skel h-4 w-2/3" /> : !did.length ? <p className="m-0 text-[13.5px] text-mute">Nothing new yet.</p> : (
              <ol className="m-0 grid list-none gap-2.5 p-0" data-list="did">
                {did.slice(0, 7).map((x, i) => (
                  <li key={i} className="grid grid-cols-[52px_minmax(0,1fr)] gap-x-3 text-[13.5px] leading-snug text-ink-2 [&_b]:font-semibold [&_b]:text-ink">
                    <time className="pt-px font-mono text-[11.5px] text-mute">{x.at ? WH.fmtWhen(x.at) : ""}</time>
                    <div className="min-w-0"><span>{x.body}</span>
                      {x.open ? <PillButton className="ml-2 align-middle" onClick={() => WH.openOutput(x.open)}>Open</PillButton> : x.go ? <PillButton className="ml-2 align-middle" asChild><a href={"#/" + x.go}>View</a></PillButton> : null}
                    </div>
                  </li>
                ))}
              </ol>
            )}
            <a href="#/activity" className="mt-3 inline-flex items-center gap-1.5 text-[13px] font-semibold text-ion no-underline hover:underline">All activity <ArrowRight size={13} /></a>
          </Panel>

          <Panel title="You owe" n={kb ? owe.length : undefined} right={<a href="#/brain/promises" className="text-ion no-underline hover:underline">All promises</a>}>
            {!kb ? <div className="skel h-4 w-2/3" /> : !owe.length ? <p className="m-0 text-[13.5px] text-mute">Nothing on record.</p> : (
              <ul className="m-0 grid list-none gap-2 p-0">{owe.slice(0, 5).map((p) => (
                <li key={p.id} className="text-[13.5px] leading-snug text-ink-2"><b className="font-semibold text-ink">{C(p.to_whom || "someone")}</b>: {cut(C(String(p.statement).replace(/^.*? owes .*?: /, "")), 130)}</li>
              ))}</ul>
            )}
          </Panel>

          <Panel title="From outside research" right={<a href="#/brain/research" className="text-ion no-underline hover:underline">All findings</a>}>
            {!res ? <div className="skel h-4 w-2/3" /> : !findings.length ? <p className="m-0 text-[13.5px] text-mute">No findings yet. Jarvis looks outside once a week and tries each finding by itself.</p> : (
              <div className="grid gap-2 text-[13.5px] text-ink-2">
                <div>{WH.plural(trying.length, "trial")} running, {tried.length} tried, {findings.length - trying.length - tried.length} waiting their turn.</div>
                {tried.slice(0, 3).map((f) => (
                  <div key={f.id} className="rounded-[9px] border border-line-2 bg-card-2 px-3 py-2">
                    <b className="block text-[13.5px] font-semibold text-ink">{cut(f.title, 90)}</b>
                    <div className="mt-0.5 text-[13px]"><span className="text-mute">Usable:</span> {cut(f.usable, 90)}</div>
                    <div className="text-[13px]"><span className="text-mute">Improves:</span> {cut(f.improves, 130)}</div>
                  </div>
                ))}
              </div>
            )}
          </Panel>

          <Panel title="This week">
            <div className="grid gap-1.5 text-[13.5px] text-ink-2">
              <a href="#/tickets" className="flex items-center justify-between gap-3 text-inherit no-underline hover:text-ion"><span>Jira tickets to check</span><b className="font-semibold tabular-nums text-ink">{check}</b></a>
            </div>
          </Panel>
        </div>
      </div>
    </div>
  );
}
