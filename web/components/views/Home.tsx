"use client";
/* Today — the dashboard. Three questions, in order: what needs me, what did Claude do while I was away,
   and where do I go next. Everything else lives one click deeper. */
import { useEffect, useReducer, useRef, useState } from "react";
import { AnimatePresence, motion } from "framer-motion";
import { Check, Inbox, Ticket, Clock, ArrowRight, CircleAlert, CheckCheck, CalendarClock, ShieldCheck, Sparkles, Coffee } from "lucide-react";
import { WH, useWH } from "@/lib/wh";
import { useShell, useTick } from "@/lib/useShell";
import { cn } from "@/lib/utils";
import { Card, Empty, SkeletonList, Skeleton } from "@/components/ui/card";
import { Button, PillButton } from "@/components/ui/button";
import { Count } from "@/components/ui/tag";
import { Avatar, SrcIcon, Stat } from "@/components/ui/bits";
import { NumberTicker } from "@/components/motion";

const MAX_NEEDS = 6, MAX_DID = 7;
const C = (s) => WH.clean(s);
const toDate = (s) => { const d = s ? new Date(s) : null; return d && !isNaN(+d) ? d : null; };
const who = (a) => C(a.who || "Someone");
// When Claude made a round-up: the time in its id (YYYY-MM-DD-HHMM-…); a meeting's window end is the meeting itself.
const rTime = (r) => ((m) => (m ? new Date(`${m[1]}T${m[2]}:${m[3]}:00`) : null))(String(r.id || "").match(/^(\d{4}-\d{2}-\d{2})-(\d{2})(\d{2})/)) || toDate(r.window_end);
const cut = (s, n = 110) => { s = String(s || "").replace(/\s+/g, " ").trim(); return s.length > n ? s.slice(0, n - 1).replace(/\s+\S*$/, "") + "…" : s; };
const hhmm = (d) => (d ? d.toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit" }) : "");

/* Everything that is waiting on the owner, most urgent first. */
export function needsList() {
  const out: any[] = [];
  WH.pendingApprovals().forEach((a) => out.push({ id: "ap|" + a.id, kind: "ok", c: "var(--ion)", t: C(a.title), s: C(a.why || ""), ap: a }));
  // A point already asked about through an approval is not listed twice.
  const asked = new Set(WH.pendingApprovals().map((a) => a.related && a.related.rid + "|" + (a.related.point || a.related.aid)).filter(Boolean));
  const open = WH.openItems();
  open.forEach(({ r, a }) => {
    const w = WH.workFor(r.id, a.id); if (!w) return;
    const pts = (w.points || []).filter((p) => !WH.ticked(r.id, p.key) && (p.state === "decide" || p.state === "needs_you") && !asked.has(r.id + "|" + p.key) && !asked.has(r.id + "|" + a.id));
    // One row per item: a card with six invites is one thing to deal with, not six.
    if (pts.length) { const dec = pts.some((p) => p.state === "decide"); out.push({ id: "n|" + r.id + "|" + a.id, kind: dec ? "decide" : "needs_you", c: "var(--warn)", who: who(a), src: WH.srcOf(a, r), t: `${who(a)} · ${pts.length > 1 ? pts.length + (dec ? " decisions" : " things need you") : dec ? "your decision" : "needs you"}`, s: C(pts[0].note || pts[0].answer || ""), focus: r.id + "|" + a.id }); }
    if (!pts.length && !(w.points || []).length && (w.needs_you || []).length) out.push({ id: "q|" + r.id + "|" + a.id, kind: "needs_you", c: "var(--warn)", who: who(a), src: WH.srcOf(a, r), t: `${who(a)} · needs you`, s: C(w.needs_you[0]), focus: r.id + "|" + a.id });
  });
  open.forEach(({ r, a }) => {
    const w = WH.workFor(r.id, a.id), rep = w && w.reply; if (!rep || !rep.text || rep.sent_at || w.replied_by_owner) return;
    if (WH.approvalsFor(r.id, a.id).some((x) => x.kind === "slack_send" && x.status !== "declined")) return;
    out.push({ id: "r|" + r.id + "|" + a.id, kind: "reply", c: "var(--ok)", who: who(a), src: WH.srcOf(a, r), t: `Reply to ${who(a)} is ready`, s: cut(C(rep.text)), focus: r.id + "|" + a.id });
  });
  return out;
}

/* What Claude did since the last visit, newest first. */
export function didList(since: Date) {
  const out: any[] = [], m = WH.inboxModel(), after = (d) => d && d > since;
  const worked = m.actions.map(({ r, a }) => ({ r, a, w: WH.workFor(r.id, a.id) })).filter((x) => x.w && after(toDate(x.w.updated)));
  const files = (WH.store.inbox.roundups || []).filter((r) => after(rTime(r)));
  const newItems = files.reduce((n, r) => n + (r.actions || []).length, 0);
  if (files.length) out.push({ at: new Date(Math.max(...files.map((r) => +(rTime(r) || 0)))), body: <>Caught up on {files.some((r) => r.kind === "meeting") ? "your sources and a meeting transcript" : "your sources"}: <b>{WH.plural(newItems, "new item")}</b></> });
  if (worked.length) {
    const drafts = worked.filter((x) => x.w.reply && x.w.reply.text).length;
    out.push({ at: new Date(Math.max(...worked.map((x) => +toDate(x.w.updated)!))), body: <>Worked on <b>{WH.plural(worked.length, "item")}</b>{drafts ? <> and drafted <b>{WH.plural(drafts, "reply", "replies")}</b></> : null}</> });
  }
  worked.forEach(({ a, w }) => (w.done_work || []).forEach((d) => out.push({ at: toDate(w.updated), body: <>Wrote <b>{C(d.title || "a write-up")}</b> for {who(a)}</>, open: d.path })));
  worked.forEach(({ r, a, w }) => (w.points || []).filter((p) => p.state === "done" && WH.ticked(r.id, p.key) && p.note).forEach((p) => out.push({ at: toDate(w.updated), body: <>Done for {who(a)}: {cut(C(p.note), 120)}</> })));
  WH.store.approvals.items.filter((x) => x.status === "done" && after(toDate(x.done_at))).forEach((x) => out.push({ at: toDate(x.done_at), body: <>{x.kind === "slack_send" ? "Sent" : "Did"}: <b>{C(x.title)}</b></> }));
  m.actions.forEach(({ r, a }) => { const rb = (WH.workFor(r.id, a.id) || {}).replied_by_owner; if (rb && after(toDate(rb.at))) out.push({ at: toDate(rb.at), body: <>Noticed you already replied to {who(a)}, and updated the item</> }); });
  const tix = WH.store.tickets.items.filter((t) => t.status === "draft" && after(toDate(t.updated))).length;
  if (tix) out.push({ at: since, body: <>Proposed <b>{WH.plural(tix, "Jira ticket")}</b> for analysis work</>, go: "tickets" });
  return out.sort((x, y) => (y.at || 0) - (x.at || 0));
}

/* Open items Claude has not written anything for yet (a run is still working on them). */
export const pendingWork = () => WH.openItems().filter((x) => !WH.workFor(x.r.id, x.a.id)).length;

/** "Since you were last here" = when you last left the app (not when you switched tabs inside it). */
export const sinceSeen = () => toDate(WH.pref.get("wh_seen_at")) || new Date(Date.now() - 12 * 36e5);

/** Ticket and week numbers shared by Today and the landing page. */
export function doorNumbers() {
  const items = WH.store.tickets.items, check = items.filter((t) => t.status === "draft" || (t.status === "ready" && !t.approved_at)).length, created = items.filter((t) => t.status === "created").length;
  return { check, created };
}

function SecHead({ title, n, right }: { title: string; n?: number; right?: React.ReactNode }) {
  return (
    <div className="mb-3 flex items-baseline gap-2.5">
      <h2 className="m-0 font-display text-[19px] font-semibold tracking-[-.01em]">{title}</h2>
      {n != null && <Count n={n} tone={n ? "warn" : "ok"} />}
      {right && <small className="ml-auto text-[13px] text-mute">{right}</small>}
    </div>
  );
}

export default function Home() {
  const [spineRun, setSpineRun] = useState<number | null>(null);
  useEffect(() => {
    let live = true;
    const load = () => WH.api("/api/spine").then((d: any) => { if (live && d && Array.isArray(d.running)) setSpineRun(d.running.length); }).catch(() => {});
    load(); const t = setInterval(() => { if (!document.hidden) load(); }, 10000);
    return () => { live = false; clearInterval(t); };
  }, []);
  useWH(["inbox", "tickets", "approvals", "me"]);
  const [, force] = useReducer((x) => x + 1, 0);
  useTick(60000, force);
  useShell();
  const since = useRef<Date>(null as any);
  if (!since.current) since.current = sinceSeen();

  const loaded = WH.store.inboxLoaded, now = new Date();
  const needs = loaded ? needsList() : [], did = loaded ? didList(since.current) : [], busy = loaded ? pendingWork() : 0;
  const shownNeeds = needs.slice(0, MAX_NEEDS), shownDid = did.slice(0, MAX_DID);
  const open = WH.openItems(), last = WH.lastRoundup(), next = WH.nextWork(), running = WH.workRunning();
  const sinceTxt = WH.fmtWhen(since.current);
  const line = !loaded ? "" : needs.length
    ? `${WH.plural(needs.length, "thing needs", "things need")} you${busy ? `, and Claude is still working on ${WH.plural(busy, "item")}` : did.length ? `, and Claude got ${WH.plural(did.length, "thing")} done since ${sinceTxt}` : ""}.`
    : busy ? `Claude is working on ${WH.plural(busy, "new item")}. You'll see what needs you here.`
    : did.length ? `You're all caught up. Claude got ${WH.plural(did.length, "thing")} done since ${sinceTxt}.` : "You're all caught up.";
  const { check, created } = doorNumbers();

  const approve = async (id, choice?) => { if (await WH.setApproval(id, "approved", choice)) WH.toast("Approved. " + WH.whenApproved(), WH.store.approvals.executor ? "ok" : "info", 4500); };
  const decline = async (id) => { if (await WH.setApproval(id, "declined")) WH.toast("Declined. Nothing will happen.", "info", 2500); };
  const focus = (k) => { WH.focusItem = k; WH.go("inbox/todo/" + encodeURIComponent(k)); };

  const doors: any[] = [
    { href: "#/inbox", c: "var(--ion)", Icon: Inbox, t: "Inbox", s: open.length ? `${WH.plural(open.length, "item")} open` : "Nothing open", n: open.length },
    { href: "#/tickets", c: "var(--s-jira)", Icon: Ticket, t: "Jira tickets", s: check ? `${check} to check` : created ? `${created} created` : "Nothing to check", n: check },
  ];
  const nextDay = next && next.toDateString() !== now.toDateString() ? WH.fmtWhen(next).replace(/\s*\d\d:\d\d$/, "") : "";

  return (
    <div className="mx-auto grid w-full max-w-[1180px] grid-cols-[minmax(0,1fr)] gap-7">
      <header className="grid items-end gap-x-8 gap-y-5 min-[1180px]:grid-cols-[minmax(0,1fr)_minmax(0,520px)]">
        <motion.div className="min-w-0" initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.3 }}>
          <div className="font-mono text-[11.5px] font-medium uppercase tracking-[.1em] text-mute">{now.toLocaleDateString("en-GB", { weekday: "long", day: "numeric", month: "long" })}</div>
          <h1 className="mt-2 font-display text-[clamp(30px,4.4vw,48px)] font-semibold leading-[1.05] tracking-[-.025em] text-ink">{WH.hello(now)}</h1>
          <p className="mt-2.5 max-w-[60ch] text-[16px] text-ink-2" data-testid="today-line">{line || " "}</p>
        </motion.div>
        <motion.div className="grid min-w-0 grid-cols-3 gap-3 max-[420px]:gap-2" initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.3, delay: 0.06 }}>
          <Stat tone="warn" icon={CircleAlert} label="Needs you">{loaded ? <NumberTicker value={needs.length} /> : <Skeleton className="h-7 w-10" />}</Stat>
          <Stat tone="ok" icon={CheckCheck} label="Done since you left" sub={"since " + sinceTxt}>{loaded ? <NumberTicker value={did.length} /> : <Skeleton className="h-7 w-10" />}</Stat>
          {/* 5 Oct: the clock routines are retired; this tile shows the live workers (the spine's lanes) instead of a next run */}
          <Stat tone="ion" icon={CalendarClock} label="Working now" sub={last ? "inbox checked " + WH.fmtWhen(last) : ""}>
            {spineRun === null ? <Skeleton className="h-7 w-10" /> : <NumberTicker value={spineRun} />}
          </Stat>
        </motion.div>
      </header>

      <div className="grid items-start gap-6 min-[1180px]:grid-cols-[minmax(0,1.55fr)_minmax(0,1fr)]">
        <section className="min-w-0 [overflow-wrap:anywhere]" data-section="needs" aria-label="Needs you">
          <SecHead title="Needs you" n={needs.length} />
          {!loaded ? <SkeletonList rows={3} />
            : !needs.length ? (busy ? <Empty icon={Sparkles} title="Nothing yet">Claude is working on {WH.plural(busy, "new item")}. Anything that needs you shows up here.</Empty> : <Empty art="/media/screens/rest.jpg" icon={Coffee} tone="ok" title="Nothing needs you right now">Claude has the rest in hand.</Empty>)
            : (
              <div className="grid gap-2.5">
                <AnimatePresence initial={true}>
                  {shownNeeds.map((x, i) => {
                    const opts = (x.ap && x.ap.options) || [], stack = !!(x.ap && opts.length);
                    return (
                      <motion.div key={x.id} layout="position" data-row="need" data-kind={x.kind}
                        initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0, transition: { duration: 0.26, delay: Math.min(i, 6) * 0.05, ease: [0.2, 0.8, 0.2, 1] } }} exit={{ opacity: 0, scale: 0.98, transition: { duration: 0.18 } }}
                        className="relative overflow-hidden glass-card rounded-[16px] border border-line shadow-card transition-[box-shadow,translate,border-color] duration-200 hover:-translate-y-px hover:border-line-3 hover:shadow-card-hover motion-reduce:hover:translate-y-0">
                        <span aria-hidden className="absolute inset-y-0 left-0 w-[3px]" style={{ background: x.c }} />
                        <div className={cn("grid items-center gap-x-3.5 gap-y-3 py-3.5 pl-5 pr-4", stack ? "grid-cols-[36px_minmax(0,1fr)]" : "grid-cols-[36px_minmax(0,1fr)_auto] max-[680px]:grid-cols-[36px_minmax(0,1fr)]")}>
                          {x.ap ? <Avatar icon={ShieldCheck} color="var(--ion)" /> : <Avatar name={x.who} />}
                          <div className="min-w-0">
                            <div className="flex items-center gap-2">
                              <b className="block min-w-0 font-display text-[15px] font-semibold leading-snug text-ink">{x.t}</b>
                              {x.src && <SrcIcon s={x.src} />}
                            </div>
                            {x.s && <small className="mt-0.5 line-clamp-2 block text-[13.5px] leading-snug text-mute">{cut(x.s, 180)}</small>}
                          </div>
                          <div className={cn("flex flex-wrap justify-end gap-1.5", (stack ? "col-start-2 justify-start" : "max-[680px]:col-start-2 max-[680px]:justify-start"))}>
                            {x.ap ? (<>
                              <Button size="sm" variant="ghost" onClick={() => decline(x.ap.id)}>Not now</Button>
                              {opts.length ? opts.map((v) => <Button key={v.id} size="sm" className="h-auto whitespace-normal py-1.5 text-left" variant={v.recommended ? "primary" : "default"} onClick={() => approve(x.ap.id, v.id)}>{C(v.label)}</Button>)
                                : <Button size="sm" variant="primary" onClick={() => approve(x.ap.id)}><Check size={14} strokeWidth={1.8} />Go ahead</Button>}
                            </>) : <Button size="sm" variant={x.kind === "reply" ? "primary" : "default"} onClick={() => focus(x.focus)}>{x.kind === "reply" ? "Review and send" : "Open"}</Button>}
                          </div>
                        </div>
                      </motion.div>
                    );
                  })}
                </AnimatePresence>
              </div>
            )}
          {needs.length > shownNeeds.length && <a href="#/inbox" className="mt-3 inline-flex items-center gap-1.5 text-[13.5px] font-semibold text-ion no-underline hover:underline">{needs.length - shownNeeds.length} more in the Inbox <ArrowRight size={14} /></a>}
        </section>

        <Card className="min-w-0 px-5 pb-3 pt-4 [overflow-wrap:anywhere]" data-section="did">
          <SecHead title="While you were away" right={"since " + sinceTxt} />
          {!loaded ? (
            <div className="grid gap-3 pb-2" role="status" aria-label="Loading">{[0, 1, 2].map((i) => <div key={i} className="grid grid-cols-[44px_minmax(0,1fr)] gap-3"><Skeleton className="h-3 w-9" /><Skeleton className="h-3" /></div>)}</div>
          ) : !did.length ? <Empty bare icon={Clock} tone="mute" className="py-6" title="Nothing new yet">{WH.nextRoundup() ? "Claude checks again " + WH.fmtWhen(WH.nextRoundup()) + "." : ""}</Empty>
          : (
            <ol className="relative m-0 list-none p-0" data-list="did">
              <span aria-hidden className="absolute bottom-4 left-[60.5px] top-4 w-px bg-line" />
              {shownDid.map((x, i) => (
                <motion.li key={i} initial={{ opacity: 0, x: -4 }} animate={{ opacity: 1, x: 0, transition: { delay: 0.08 + i * 0.04, duration: 0.22 } }}
                  className="relative grid grid-cols-[44px_14px_minmax(0,1fr)] gap-x-2.5 py-2 text-[14px] leading-snug text-ink-2 [&_b]:font-semibold [&_b]:text-ink">
                  <time className="whitespace-pre-line pt-px text-right font-mono text-[11.5px] leading-tight text-mute">{x.at ? WH.fmtWhen(x.at).replace(" ", "\n") : ""}</time>
                  <span aria-hidden className="relative mt-[5px] size-[9px] justify-self-center rounded-full bg-ok shadow-[0_0_0_3px_var(--card)]" />
                  <div className="min-w-0">
                    <span>{x.body}</span>
                    {x.open ? <PillButton className="ml-2 align-middle" onClick={() => WH.openOutput(x.open)}>Open</PillButton> : x.go ? <PillButton className="ml-2 align-middle" asChild><a href={"#/" + x.go}>View</a></PillButton> : null}
                  </div>
                </motion.li>
              ))}
            </ol>
          )}
          {did.length > shownDid.length && <a href="#/inbox" className="mt-1 inline-flex items-center gap-1.5 pb-1 text-[13.5px] font-semibold text-ion no-underline hover:underline">and {did.length - shownDid.length} more <ArrowRight size={14} /></a>}
        </Card>
      </div>

      <nav aria-label="Sections" className="grid grid-cols-3 gap-3.5 max-[760px]:grid-cols-1">
        {doors.map((d, i) => (
          <motion.a key={d.href} href={d.href} data-door={d.t} initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0, transition: { delay: 0.12 + i * 0.05, duration: 0.26 } }}
            className="group flex min-w-0 flex-col gap-4 glass-card rounded-[16px] border border-line p-5 text-inherit no-underline shadow-card transition-[box-shadow,translate,border-color] duration-200 hover:-translate-y-px hover:border-line-3 hover:shadow-card-hover motion-reduce:hover:translate-y-0"
            style={{ ["--c" as any]: d.c }}>
            <div className="flex items-start justify-between gap-3">
              <span className="grid size-11 flex-none place-items-center rounded-[12px] text-[var(--c)]" style={{ background: "color-mix(in srgb, var(--c) 11%, var(--card))" }}><d.Icon size={21} strokeWidth={1.8} /></span>
              <span className="font-display text-[32px] font-semibold leading-none tracking-[-.02em] text-ink" data-num><NumberTicker value={d.n} format={d.fmt} unit={d.unit} /></span>
            </div>
            <div className="flex items-end justify-between gap-3">
              <div className="min-w-0">
                <b className="block font-display text-[16px] font-semibold">{d.t}</b>
                <small className="block truncate text-[13px] text-mute">{d.s}</small>
              </div>
              <ArrowRight size={18} strokeWidth={1.8} className="flex-none text-mute transition-[translate,color] duration-200 group-hover:translate-x-1 group-hover:text-[var(--c)]" />
            </div>
          </motion.a>
        ))}
      </nav>
    </div>
  );
}
