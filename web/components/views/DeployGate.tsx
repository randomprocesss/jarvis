"use client";
/* Deploy gate (#/deploy; shown when settings say modules.deploy_gate). Every change on its way out of the watched
   repositories is reviewed by the gate first: diff, build and tests, a local run, behaviour against the ticket, a local
   stress test. This screen reads what the gate wrote (gate/gate.py, in the data home); its presses start a review on
   this computer, or hold and resume the automatic ones. Nothing here pushes, merges or deploys anything. */
import { useEffect, useState } from "react";
import { ShieldCheck, AlertTriangle, Loader2, CheckCircle2, ChevronDown, ChevronRight, RotateCcw, GitBranch, Tag, GitMerge, PauseCircle, Play, FileCode2, UserRound, Clock3, History } from "lucide-react";
import { WH, useWH } from "@/lib/wh";
import { useShell } from "@/lib/useShell";
import { Card, SkeletonList } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { PageTitle, Note } from "@/components/ui/kit";
import { cn } from "@/lib/utils";

const when = (iso?: string) => (iso ? WH.fmtWhen(new Date(iso)) : "");
const KIND: Record<string, [string, any]> = {
  deploy: ["Pushed to a deploy branch", GitMerge],
  tag: ["Release tag", Tag],
  branch: ["About to be merged", GitBranch],
};
const TONE: Record<string, string> = {
  "NO-GO": "bg-bad-soft text-bad",
  "NEEDS-INFO": "bg-warn-soft text-warn",
  stuck: "bg-warn-soft text-warn",
  "GO-WITH-NOTES": "bg-ok-soft text-ok",
  GO: "bg-ok-soft text-ok",
  running: "bg-ion-soft text-ion",
  pending: "bg-ink/5 text-mute",
  retry: "bg-ink/5 text-mute",
  note: "bg-ink/5 text-mute",
};
const state = (c: any) => (c.status === "done" ? c.verdict : c.status);

function Pill({ v }: { v: string }) {
  return <span className={cn("inline-flex h-[22px] flex-none items-center rounded-full px-2 font-mono text-[10.5px] font-semibold uppercase tracking-[.06em]", TONE[v] || "bg-ink/5 text-mute")}>{v === "retry" ? "trying again" : v === "pending" ? "waiting" : v}</span>;
}

// bold and `code` inside one line (finding titles and verdict text)
const inline = (s: string) =>
  String(s || "").split(/(\*\*[^*]+\*\*|`[^`]+`)/g).map((p, i) =>
    p.startsWith("**") ? <b key={i} className="font-semibold text-ink">{p.slice(2, -2)}</b>
      : p.startsWith("`") ? <code key={i} className="rounded bg-ink/5 px-1 font-mono text-[.86em]">{p.slice(1, -1)}</code>
      : <span key={i}>{p}</span>);

// Small markdown: headings, bullets, tables, bold, code. Verdicts are written by the reviewer in a fixed shape.
function Md({ text }: { text: string }) {
  const out: any[] = [];
  const lines = text.split("\n");
  for (let i = 0; i < lines.length; i++) {
    const l = lines[i];
    if (l.startsWith("|")) {
      const rows: string[][] = [];
      while (i < lines.length && lines[i].startsWith("|")) {
        if (!/^\|[\s|:-]+\|?$/.test(lines[i])) rows.push(lines[i].split("|").slice(1, -1).map((x) => x.trim()));
        i++;
      }
      i--;
      out.push(
        <div key={i} className="my-2 overflow-x-auto">
          <table className="w-full border-collapse text-[12.5px]">
            <tbody>{rows.map((r, j) => (
              <tr key={j} className={cn("border-t border-line align-top", j === 0 && "font-mono text-[10.5px] uppercase tracking-[.08em] text-mute")}>
                {r.map((cell, k) => <td key={k} className={cn("py-1.5 pr-3", k === 1 && j > 0 && /FAIL|NOT RUN|FLAGS/.test(cell) && "text-warn", k === 1 && j > 0 && /PASS/.test(cell) && "text-ok")}>{inline(cell)}</td>)}
              </tr>))}</tbody>
          </table>
        </div>);
    } else if (l.startsWith("# ")) {
      continue; // the title is already the row
    } else if (l.startsWith("## ")) {
      out.push(<div key={i} className="mt-3 font-mono text-[11px] font-semibold uppercase tracking-[.1em] text-mute">{l.slice(3)}</div>);
    } else if (/^\s*([-*]|\d+\.)\s/.test(l)) {
      const depth = l.match(/^\s*/)![0].length >= 2 ? "pl-5" : "pl-1";
      out.push(<div key={i} className={cn("flex gap-1.5 py-[1px] text-[13px] leading-snug text-ink-2", depth)}><span className="text-mute">{/^\s*\d+\./.test(l) ? l.trim().split(" ")[0] : "·"}</span><span className="min-w-0 [overflow-wrap:anywhere]">{inline(l.replace(/^\s*([-*]|\d+\.)\s/, ""))}</span></div>);
    } else if (l.trim()) {
      out.push(<p key={i} className="m-0 py-[1px] text-[13px] leading-snug text-ink-2 [overflow-wrap:anywhere]">{inline(l)}</p>);
    }
  }
  return <div>{out}</div>;
}

const verdictText = async (id: string) => { try { const r: any = await WH.api("/api/deploy_gate_verdict?id=" + encodeURIComponent(id)); return r?.text || ""; } catch { return ""; } };

function ChangeRow({ c, onReview }: { c: any; onReview: (id: string) => void }) {
  const [open, setOpen] = useState(false);
  const [text, setText] = useState<string | null>(null);
  const [kindLabel, KindIcon] = KIND[c.kind] || [c.kind, GitBranch];
  const toggle = async () => {
    const next = !open;
    setOpen(next);
    if (next && text === null && c.has_verdict) setText(await verdictText(c.id));
  };
  const waited = c.first_seen ? Date.now() - new Date(c.first_seen).getTime() : 0;
  return (
    <li className="border-t border-line first:border-t-0">
      <div className="flex items-start gap-2 py-2.5">
        <button type="button" onClick={toggle} aria-expanded={open} className="mt-[3px] text-mute hover:text-ink" aria-label="Details">
          {open ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
        </button>
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <Pill v={state(c)} />
            <span className="text-[14px] font-medium text-ink">{c.repo}</span>
            <span className="font-mono text-[12px] text-ink-2 [overflow-wrap:anywhere]">{c.ref}</span>
            <span className="font-mono text-[11px] text-mute">@{String(c.sha).slice(0, 8)}</span>
            {(c.tickets || []).slice(0, 3).map((t: string) => (WH.ticketUrl(t)
              ? <a key={t} href={WH.ticketUrl(t)} target="_blank" rel="noreferrer" className="font-mono text-[11px] text-ion no-underline hover:underline">{t}</a>
              : <span key={t} className="font-mono text-[11px] text-mute">{t}</span>))}
          </div>
          <p className="m-0 mt-1 flex flex-wrap items-center gap-x-2 text-[12.5px] text-mute">
            <span className="inline-flex items-center gap-1"><KindIcon size={12} />{kindLabel}</span>
            <span>· {c.stage}</span>
            {c.author && <span>· {c.author}</span>}
            {c.reviewed_at ? <span>· reviewed {when(c.reviewed_at)}</span> : c.first_seen ? <span>· seen {when(c.first_seen)}</span> : null}
            {/* a queue that has stopped moving shows here instead of hiding behind "waiting" */}
            {["pending", "retry"].includes(c.status) && waited > 86400e3 ? <span className="text-warn">· waiting {Math.floor(waited / 86400e3)} d</span> : null}
          </p>
          <PriorLine c={c} />
          {c.error && <p className="m-0 mt-1 text-[12.5px] text-warn">{c.status === "stuck" ? "Stuck after " + c.attempts + " tries: " : "Last try: "}{c.error}</p>}
          {open && (
            <div className="mt-2 rounded-[10px] border border-line px-3 py-2">
              {!c.has_verdict ? <p className="m-0 text-[13px] text-mute">No verdict yet.</p>
                : text === null ? <p className="m-0 text-[13px] text-mute">Loading…</p>
                : <Md text={text} />}
              <div className="mt-2 flex justify-end">
                <Button variant="ghost" size="sm" onClick={() => onReview(c.id)} disabled={c.status === "running"}>
                  <RotateCcw size={13} />{c.has_verdict ? "Review again" : "Review now"}
                </Button>
              </div>
            </div>
          )}
        </div>
      </div>
    </li>
  );
}

// "Fixed" only when the review showed every earlier finding fixed (its P rows), never because a newer push got a GO.
function PriorLine({ c }: { c: any }) {
  const pr = c.prior, r = c.prior_results;
  const was = pr ? `${pr.verdict} @${String(pr.sha).slice(0, 7)}` : "";
  // the same code on the same base as a commit already reviewed (a branch merged with a merge commit): the verdict was carried
  if (c.same_code_as && c.status === "done") {
    const src = String(c.same_code_as).split(":").slice(2).join(":").replace(/@(\w{7})\w*$/, " @$1");
    return <p className="m-0 mt-1 text-[12.5px] text-mute">Same code as {src}: its verdict was carried, not reviewed again</p>;
  }
  if (pr && r && c.status === "done") {
    const parts = [r.pass && `${r.pass} fixed`, r.noted && `${r.noted} noted`, r.na && `${r.na} not applicable`,
                   r.fail && `${r.fail} still failing`, r.not_verified && `${r.not_verified} not checked`].filter(Boolean);
    return (
      <p className="m-0 mt-1 text-[12.5px] text-mute">
        Earlier {was}: {parts.join(", ")}
        {c.fixed_prior && ["GO", "GO-WITH-NOTES"].includes(c.verdict) ? <span className="text-ok"> · fixed by this push</span> : null}
        {r.fail || r.not_verified ? <span className="text-warn"> · not fixed yet</span> : null}
      </p>);
  }
  if (pr && ["pending", "running", "retry"].includes(c.status))
    return <p className="m-0 mt-1 text-[12.5px] text-ion">Checks {(pr.findings || []).length} findings of {was} again</p>;
  if (pr && c.rereview_stuck)
    return <p className="m-0 mt-1 text-[12.5px] text-warn">The second review got stuck; the earlier verdict still stands</p>;
  if ((c.replaces || []).length > 0)
    return <p className="m-0 mt-1 text-[12.5px] text-mute">Replaces {(c.replaces as any[]).map((x) => `${x.verdict} @${String(x.sha).slice(0, 7)}`).join(", ")}</p>;
  return null;
}

function SectionHead({ icon: Icon, title, count, hint, tone, right }: { icon: any; title: string; count?: number; hint?: string; tone?: string; right?: any }) {
  return (
    <div className="flex flex-wrap items-center gap-x-2.5 gap-y-1.5">
      <Icon size={16} strokeWidth={1.9} className={tone || "text-mute"} aria-hidden />
      <h2 className="m-0 text-[16px] font-semibold tracking-[-.01em] text-ink">{title}</h2>
      {typeof count === "number" && <span className="rounded-full bg-ink/5 px-2 py-[1px] font-mono text-[11.5px] font-semibold tabular-nums text-ink-2">{count}</span>}
      {hint && <span className="text-[12.5px] text-mute max-[700px]:basis-full">{hint}</span>}
      {right && <span className="ml-auto flex flex-wrap items-center gap-1">{right}</span>}
    </div>
  );
}

const short = (p: string) => { const a = String(p).split("/"); return a.length > 2 ? "…/" + a.slice(-2).join("/") : p; };
const ago = (iso?: string) => { if (!iso) return ""; const h = (Date.now() - new Date(iso).getTime()) / 3600e3; return h < 1 ? `${Math.max(1, Math.round(h * 60))} min` : h < 48 ? `${Math.round(h)} h` : `${Math.floor(h / 24)} days`; };
const jump = (id: string) => document.getElementById(id)?.scrollIntoView({ behavior: "smooth", block: "start" });

function Kpi({ n, label, sub, tone, onClick }: { n: number; label: string; sub?: string; tone: "bad" | "ok" | "ion" | "warn"; onClick?: () => void }) {
  const c = { bad: "var(--bad)", ok: "var(--ok)", ion: "var(--ion)", warn: "var(--warn)" }[tone];
  return (
    <button type="button" onClick={onClick} className="group min-w-0 rounded-[14px] border border-line bg-card px-4 py-3.5 text-left transition-colors hover:border-line-3 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ion">
      <div className="flex items-center justify-between gap-2 text-[12.5px] font-semibold text-ink-2">{label}<ChevronRight size={14} className="text-mute transition-transform duration-200 group-hover:translate-x-0.5" aria-hidden /></div>
      <div className="mt-1.5 text-[34px] font-semibold leading-none tracking-[-.02em] tabular-nums" style={{ color: n ? c : "var(--mute)" }}>{n}</div>
      {sub && <div className="mt-1.5 text-[12px] text-mute">{sub}</div>}
    </button>
  );
}

// A finding is one row that says what is wrong, in the reviewer's own sentence; the line of code, why it still counts,
// every review's wording and the review's notes open under it.
function IssueRow({ i, onReview }: { i: any; onReview: (id: string) => void }) {
  const [open, setOpen] = useState(false);
  const [notes, setNotes] = useState(false);
  const [text, setText] = useState<string | null>(null);
  const showNotes = async () => {
    const next = !notes; setNotes(next);
    if (next && text === null) setText(await verdictText(i.candidate_id));
  };
  const tell: string[] = (i.tell || []).map((t: string) => t.replace(/\s*\(.*\)$/, "")).filter((v: string, k: number, a: string[]) => a.indexOf(v) === k);
  const reviews = (i.cids || []).length, places: string[] = i.places || [];
  return (
    <li className="border-t border-line first:border-t-0">
      <button type="button" onClick={() => setOpen(!open)} aria-expanded={open} className="group grid w-full grid-cols-[minmax(0,1fr)_auto] items-start gap-3 rounded-[10px] px-2 py-3 text-left transition-colors hover:bg-line-2/50">
        <span className="min-w-0">
          <span className="flex flex-wrap items-center gap-1.5">
            <Pill v={i.severity === "note" ? "note" : i.verdict} />
            {(i.branches || []).map((b: string) => <span key={b} className="rounded-full bg-ion-soft px-2 py-[1px] font-mono text-[10.5px] font-semibold text-ion">{b}</span>)}
            {i.label && <span className="rounded-full border border-line px-2 py-[1px] text-[11px] text-mute">{i.label}</span>}
            {reviews > 1 && <span className="text-[11.5px] text-mute">found by {reviews} reviews</span>}
          </span>
          <span className="mt-1.5 block text-[14.5px] font-medium leading-snug text-ink [overflow-wrap:anywhere]">{inline(i.title)}</span>
          <span className="mt-1.5 flex flex-wrap items-center gap-x-3 gap-y-1 text-[12px] text-mute">
            {places.length > 0 && <span className="inline-flex min-w-0 items-center gap-1"><FileCode2 size={12} aria-hidden /><code title={places.join("\n")} className="min-w-0 truncate font-mono text-[11.5px] text-ink-2">{short(places[0])}</code>{places.length > 1 && <span>+{places.length - 1}</span>}</span>}
            {tell.length > 0 && <span className="inline-flex items-center gap-1" title="Who wrote the line as it is now"><UserRound size={12} aria-hidden />{tell.join(", ")}</span>}
            <span className="inline-flex items-center gap-1"><Clock3 size={12} aria-hidden />reviewed {when(i.reviewed)}</span>
          </span>
        </span>
        <ChevronDown size={16} className={cn("mt-1 flex-none text-mute transition-transform duration-200", open && "rotate-180")} aria-hidden />
      </button>
      {open && (
        <div className="mx-2 mb-3 grid grid-cols-[minmax(0,1fr)] gap-2.5 rounded-[12px] border border-line bg-line-2/40 px-3.5 py-3 text-[12.5px] text-ink-2">
          {places.length > 0 && <div className="flex flex-wrap gap-1.5">{places.map((w) => <code key={w} title={w} className="min-w-0 break-all rounded bg-ink/5 px-1.5 py-[1px] font-mono text-[11.5px] text-ink-2">{short(w)}</code>)}</div>}
          {i.code && <code className="block overflow-x-auto whitespace-pre rounded-[8px] border border-line bg-card px-2.5 py-1.5 font-mono text-[11.5px] text-ink">{i.code}</code>}
          <p className="m-0"><b className="font-medium text-ink">Why it still counts:</b> {i.how || "the cited code is still in the code as it is now"}. Reviewed on {i.ref} @{String(i.sha).slice(0, 8)}.</p>
          {(i.also || []).length > 0 && (
            <div>
              <div className="font-medium text-ink">The same place in the code in the other review{i.also.length === 1 ? "" : "s"}:</div>
              <ul className="m-0 mt-1 list-none p-0">{i.also.map((a: any, k: number) => (
                <li key={k} className="flex gap-2 py-[2px]"><span className="flex-none font-mono text-[11px] text-mute">{a.ref}</span><span className="min-w-0 [overflow-wrap:anywhere]">{inline(a.title)}</span></li>))}</ul>
            </div>)}
          <div className="flex flex-wrap gap-2">
            <Button variant="default" size="sm" onClick={showNotes}>{notes ? <ChevronDown size={13} /> : <ChevronRight size={13} />}Review notes</Button>
            <Button variant="ghost" size="sm" onClick={() => onReview(i.candidate_id)}><RotateCcw size={13} />Review it again</Button>
          </div>
          {notes && <div className="rounded-[10px] border border-line bg-card px-3 py-2">{text === null ? <p className="m-0 text-[13px] text-mute">Loading…</p> : <Md text={text} />}</div>}
        </div>)}
    </li>
  );
}

function Fold({ id, title, hint, icon: Icon, count, children, tone, open, onToggle }: { id: string; title: string; hint?: string; icon: any; count: number; children: any; tone?: string; open: boolean; onToggle: () => void }) {
  if (!count) return null;
  return (
    <Card id={id} className="min-w-0 scroll-mt-20 px-5 py-3.5">
      <button type="button" onClick={onToggle} aria-expanded={open} className="group flex w-full flex-wrap items-center gap-x-2.5 gap-y-1 text-left">
        <Icon size={16} strokeWidth={1.9} className={tone || "text-mute"} aria-hidden />
        <span className="text-[15px] font-semibold tracking-[-.01em] text-ink">{title}</span>
        <span className="rounded-full bg-ink/5 px-2 py-[1px] font-mono text-[11.5px] font-semibold tabular-nums text-ink-2">{count}</span>
        {hint && <span className="text-[12.5px] text-mute max-[700px]:basis-full">{hint}</span>}
        <ChevronDown size={16} className={cn("ml-auto text-mute transition-transform duration-200 group-hover:text-ink", open && "rotate-180")} aria-hidden />
      </button>
      {open && <div className="mt-2.5">{children}</div>}
    </Card>
  );
}

export default function DeployGate() {
  useShell();
  useWH(["me"]);
  const on = (WH.me.modules || []).includes("deploy_gate");
  const [d, setD] = useState<any>(null);
  const [busy, setBusy] = useState(false);
  const [repo, setRepo] = useState<string>("ALL");
  const [folds, setFolds] = useState<Record<string, boolean>>({});
  const load = () => WH.api("/api/deploy_gate").then((x: any) => x && !x.error && setD(x)).catch(() => {});
  useEffect(() => { if (!on) return; load(); const t = setInterval(() => { if (!document.hidden) load(); }, 20000); return () => clearInterval(t); }, [on]);
  const review = async (id?: string) => {
    setBusy(true);
    try {
      const r: any = await WH.api("/api/deploy_gate_review", id ? { id } : {});
      if (r && r.ok) WH.toast(id ? "Review started for that change." : "Started. The gate looks at the repositories, then reviews what is due.", "ok", 3200);
      else WH.toast((r && r.error) || "The review could not be started.", "err", 6000);
    } catch {}
    setTimeout(() => { load(); setBusy(false); }, 1500);
  };
  const title = <span className="inline-flex items-center gap-2.5"><ShieldCheck size={24} strokeWidth={1.8} className="text-ion" aria-hidden />Deploy gate</span>;
  if (WH.me.loaded && !on) {
    return (
      <div className="grid grid-cols-[minmax(0,1fr)] gap-5" data-view="deploy">
        <PageTitle title={title} sub="Reviews every change that is about to go out of your repositories, on this computer, and says GO or NO-GO. It never pushes, merges or deploys anything." />
        <Note>The deploy gate is an optional part and it is switched off. Switch it on in <a className="font-medium text-ion no-underline hover:underline" href="#/setup">Setup</a>, under Optional parts; it starts with Jarvis the next time Jarvis starts.</Note>
      </div>
    );
  }
  if (!d) return <div className="grid gap-5" data-view="deploy"><SkeletonList rows={4} /></div>;
  if (!d.ready) {
    return (
      <div className="grid grid-cols-[minmax(0,1fr)] gap-5" data-view="deploy">
        <PageTitle title={title} sub="Reviews every change that is about to go out of your repositories, on this computer, and says GO or NO-GO. It never pushes, merges or deploys anything." />
        <Note tone="warn"><b className="font-semibold">The gate has nothing to watch yet.</b> {d.why ? d.why.replace(/^not set up: /, "It is not set up: ") + "." : ""} Name the repositories in the settings file, under [gate]; the guide (docs/DEPLOY-GATE.md) lists every setting.</Note>
      </div>
    );
  }

  const cs: any[] = d.candidates || [];
  const byDate = (a: any, b: any) => String(b.reviewed_at || b.date || "").localeCompare(String(a.reviewed_at || a.date || ""));
  const inReview = cs.filter((c) => ["running", "pending", "retry"].includes(c.status)).sort((a, b) => (a.status === "running" ? -1 : b.status === "running" ? 1 : 0));
  const reviews = cs.filter((c) => c.status === "done" || c.status === "stuck").sort(byDate);
  const blocking = (i: any) => i.severity === "blocking";
  const inRepo = (i: any) => repo === "ALL" || i.repo === repo;
  const allIssues: any[] = (d.issues || []).filter(blocking);
  const issues = allIssues.filter(inRepo);
  const notes: any[] = (d.issues || []).filter((i: any) => !blocking(i) && inRepo(i));
  const fixed: any[] = d.fixed || [], unchecked: any[] = d.unchecked || [], aboutPush: any[] = d.about_push || [];
  const repoNames: string[] = Array.from(new Set(allIssues.map((i) => i.repo)));
  repoNames.sort((a, b) => allIssues.filter((i) => i.repo === b).length - allIssues.filter((i) => i.repo === a).length || a.localeCompare(b));
  const groups = repoNames.map((r) => ({ r, rows: issues.filter((i) => i.repo === r) })).filter((g) => g.rows.length);
  const isOpen = (k: string) => !!folds[k];
  const toggle = (k: string) => setFolds((f) => ({ ...f, [k]: !f[k] }));
  const openAndJump = (k: string) => { setFolds((f) => ({ ...f, [k]: true })); setTimeout(() => jump(k), 60); };
  const chip = (on: boolean) => cn("rounded-full px-2.5 py-[3px] text-[12px] font-medium transition-colors", on ? "bg-ion-soft text-ion" : "text-mute hover:bg-line-2 hover:text-ink");

  // a pause is a state of the gate, shown with its age, its reason and what waits behind it
  const paused = d.paused, waiting: any[] = inReview.filter((c) => c.status !== "running");
  const limits = d.settings?.limits || {};
  const resume = async () => {
    const list = waiting.map((c) => `• ${c.repo} · ${c.ref}${c.author ? ` (${c.author})` : ""}`).join("\n");
    const ok = await WH.ask("Resume automatic reviews?",
      `The gate will review what is waiting at its next run (within 30 minutes), ${limits.parallel || 1} at a time. Each review uses Claude usage.\n\n${list || "Nothing is waiting right now."}\n\nThe note on file says: ${paused?.why || "no reason written"}`, "Resume");
    if (!ok) return;
    try { await WH.api("/api/deploy_gate_pause", { on: false }); WH.toast("Resumed. The gate reviews the waiting changes at its next run.", "ok", 4500); } catch {}
    load();
  };
  const hold = async () => {
    const ok = await WH.ask("Hold automatic reviews?", "The gate keeps looking at the repositories and lists what is new, but reviews nothing by itself until you resume. Review now on a single change still works.", "Hold");
    if (!ok) return;
    try { await WH.api("/api/deploy_gate_pause", { on: true, why: "held from the Deploy gate screen" }); } catch {}
    load();
  };
  const hw = String(d.health?.waiting || "");
  const stateChip: [string, string, any, boolean] = d.running ? ["Reviewing now", "border-ion/40 bg-ion-soft text-ion", Loader2, true]
    : paused ? [`Paused ${ago(paused.since)}`, "border-warn/40 bg-warn-soft text-warn", PauseCircle, false]
    : hw ? [`Waiting: ${hw.slice(0, 90)}`, "border-line bg-card-2 text-ink-2", Clock3, false]
    : ["Idle · looks every 30 min", "border-line bg-card-2 text-ink-2", CheckCircle2, false];
  const StateIcon: any = stateChip[2];
  const watched: string[] = d.settings?.repos || [];

  return (
    <div className="grid grid-cols-[minmax(0,1fr)] gap-5" data-view="deploy">
      <PageTitle title={title}
        sub={<>{allIssues.length
          ? <span data-testid="dg-headline">{allIssues.length} blocking finding{allIssues.length === 1 ? " is" : "s are"} still in the code as it is now.</span>
          : <span data-testid="dg-headline">No blocking finding is open in the code as it is now.</span>}
          {" "}Watching {watched.join(", ")}. It never pushes, merges or deploys anything.</>}
        right={<div className="flex flex-wrap items-center gap-2">
          <span className={cn("inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-[12px] font-medium", stateChip[1])} data-testid="dg-state">
            <StateIcon size={13} className={stateChip[3] ? "animate-spin" : ""} aria-hidden />{stateChip[0]}
          </span>
          {!paused && <Button variant="ghost" size="sm" onClick={hold}><PauseCircle size={14} />Hold</Button>}
          <Button onClick={() => review()} disabled={busy || d.running}>
            {busy || d.running ? <Loader2 size={15} className="animate-spin" /> : <RotateCcw size={15} />}Review now
          </Button>
        </div>} />
      <p className="-mt-3 mb-0 text-[12px] text-mute">
        {[d.recheck_at && `Findings checked again ${when(d.recheck_at)}`, d.scanned_at && `repositories looked at ${when(d.scanned_at)}`].filter(Boolean).join(" · ")}
      </p>

      {(d.settings?.problems || []).length > 0 && (
        <Note tone="warn">{(d.settings.problems as string[]).map((p, k) => <div key={k}>{p}</div>)}</Note>
      )}

      {paused && (
        <Card className="min-w-0 border-[color-mix(in_srgb,var(--warn)_50%,transparent)] bg-warn-soft px-5 py-4" data-testid="dg-paused">
          <div className="flex flex-wrap items-start gap-x-4 gap-y-3">
            <PauseCircle size={20} strokeWidth={1.9} className="mt-0.5 flex-none text-warn" aria-hidden />
            <div className="min-w-0 flex-1 basis-[420px] text-[13.5px] leading-snug text-ink">
              <b className="text-[15.5px] font-semibold">Automatic reviews have been paused for {ago(paused.since)}</b>
              <span className="text-ink-2"> (since {when(paused.since)})</span>
              <p className="m-0 mt-1 text-ink-2">New pushes are found and listed, but nothing reviews them until the pause is lifted. Review now on a single change still works.</p>
              {paused.why && <p className="m-0 mt-1 text-[12.5px] text-mute">Note on file: {paused.why}</p>}
              {waiting.length > 0 && (
                <div className="mt-2">
                  <div className="text-[12.5px] font-semibold text-ink">{waiting.length} change{waiting.length === 1 ? " is" : "s are"} waiting:</div>
                  <ul className="m-0 mt-1 grid list-none grid-cols-[minmax(0,1fr)] gap-0.5 p-0 text-[12.5px] text-ink-2">{waiting.map((c) => (
                    <li key={c.id} className="flex flex-wrap items-baseline gap-x-2">
                      <span className="font-medium text-ink">{c.repo}</span><span className="font-mono text-[11.5px] [overflow-wrap:anywhere]">{c.ref}</span>
                      <span className="text-mute">{c.author ? `${c.author} · ` : ""}waiting {ago(c.first_seen)}</span>
                    </li>))}</ul>
                </div>)}
            </div>
            <Button variant="primary" onClick={resume}><Play size={15} />Resume automatic reviews</Button>
          </div>
        </Card>
      )}

      {/* a network drop is one calm line that clears itself on the next good fetch, never one error per repository */}
      {d.offline && (
        <Card className="min-w-0 px-5 py-3.5 text-[13.5px] text-ink-2">
          <b className="font-semibold text-ink">The code host has not answered since {when(d.offline.since)}</b> ({d.offline.why}).
          {" "}Showing the copy fetched {d.fetched_at ? when(d.fetched_at) : "earlier"}. The gate tries again on its next run and this note goes away by itself.
        </Card>
      )}

      {Object.keys(d.problems || {}).length > 0 && (
        <Card className="min-w-0 border-[color-mix(in_srgb,var(--warn)_40%,transparent)] px-5 py-3.5 text-[13.5px] text-ink">
          {Object.entries(d.problems || {}).map(([r, e]: any) => <div key={r}><b className="font-medium">{r}</b>: {String(e).slice(0, 160)} (using the last copy)</div>)}
        </Card>
      )}

      <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
        <Kpi n={allIssues.length} label="Still in the code" sub={notes.length ? `${notes.length} notes apart` : "blocking findings"} tone="bad" onClick={() => { setRepo("ALL"); jump("dg-issues"); }} />
        <Kpi n={fixed.length} label="Changed since review" sub="later commits changed the lines" tone="ok" onClick={() => openAndJump("dg-fixed")} />
        <Kpi n={unchecked.length} label="Needs a new run" sub="only running the code can tell" tone="ion" onClick={() => openAndJump("dg-rerun")} />
        <Kpi n={waiting.length} label="Waiting for review" sub={inReview.length > waiting.length ? "one is being reviewed now" : "changes found, not reviewed yet"} tone="warn" onClick={() => jump("dg-queue")} />
      </div>

      <Card id="dg-issues" className="min-w-0 scroll-mt-20 px-5 py-4">
        <SectionHead icon={AlertTriangle} tone="text-bad" title="Still in the code as it is now" count={issues.length} hint="blocking findings of a review that the newest code still has" />
        {repoNames.length > 1 && (
          <div className="mt-2 flex flex-wrap items-center gap-1">
            <button type="button" onClick={() => setRepo("ALL")} aria-pressed={repo === "ALL"} className={chip(repo === "ALL")}>Every repository</button>
            {repoNames.map((r) => (
              <button key={r} type="button" onClick={() => setRepo(r)} aria-pressed={repo === r} className={chip(repo === r)}>{r} <span className="tabular-nums opacity-70">{allIssues.filter((i) => i.repo === r).length}</span></button>))}
          </div>)}
        {groups.length ? groups.map((g) => (
          <div key={g.r} className="mt-3" data-repo={g.r}>
            <div className="flex flex-wrap items-baseline gap-x-2.5 border-b border-line pb-1.5">
              <span className="text-[14.5px] font-semibold text-ink">{g.r}</span>
              <span className="text-[12px] text-mute">{g.rows.length} finding{g.rows.length === 1 ? "" : "s"}</span>
            </div>
            <ul className="m-0 list-none p-0">{g.rows.map((i) => <IssueRow key={i.candidate_id + "|" + i.n} i={i} onReview={review} />)}</ul>
          </div>))
          : <p className="m-0 mt-2 py-1 text-[13px] text-mute">Nothing{repo === "ALL" ? "" : ` for ${repo}`}.</p>}
      </Card>

      <Card id="dg-queue" className="min-w-0 scroll-mt-20 px-5 py-4">
        <SectionHead icon={paused ? PauseCircle : Loader2} title={paused ? "Waiting for review (paused)" : "In review"} count={inReview.length} />
        {inReview.length ? <ul className="m-0 mt-1 list-none p-0">{inReview.map((c) => <ChangeRow key={c.id} c={c} onReview={review} />)}</ul>
          : <p className="m-0 py-1 text-[13px] text-mute">Nothing is waiting. A new branch, a push to a deploy branch or a release tag shows up here after the next look.</p>}
      </Card>

      <Fold id="dg-notes" open={isOpen("dg-notes")} onToggle={() => toggle("dg-notes")} title="Notes" hint="small and accepted findings that are still in the code" icon={CheckCircle2} count={notes.length}>
        <ul className="m-0 list-none p-0">{notes.map((i) => <IssueRow key={i.candidate_id + "|" + i.n} i={i} onReview={review} />)}</ul>
      </Fold>

      <Fold id="dg-fixed" open={isOpen("dg-fixed")} onToggle={() => toggle("dg-fixed")} title="Changed since review" hint="nobody needs telling" icon={CheckCircle2} count={fixed.length} tone="text-ok">
        <ul className="m-0 list-none p-0">{fixed.map((f, k) => (
          <li key={k} className="border-t border-line py-2.5 text-[12.5px] text-ink-2 first:border-t-0">
            <div className="flex flex-wrap items-center gap-2"><Pill v={f.verdict} /><span className="font-medium text-ink">{f.repo}</span><span className="text-mute">{f.ref} @{String(f.sha).slice(0, 8)}</span></div>
            <div className="mt-1 text-[13.5px] text-ink">{inline(f.title)}</div>
            <div className="mt-0.5 text-mute">{f.fixed_by ? `Changed in ${f.fixed_by.commit} by ${f.fixed_by.author} on ${f.fixed_by.date}` : "The cited lines are no longer in the code"} · confirmed at the next review</div>
          </li>))}</ul>
      </Fold>

      <Fold id="dg-rerun" open={isOpen("dg-rerun")} onToggle={() => toggle("dg-rerun")} title="Needs the code run again" hint="seen when the code ran, on code that has changed since" icon={RotateCcw} count={unchecked.length} tone="text-warn">
        <p className="m-0 mb-1 text-[12.5px] text-mute">Behaviour seen when the code ran (an error, a slow call, a broken page) on code that has changed since. Only running it again can tell; one review checks all of a change's findings.</p>
        <ul className="m-0 list-none p-0">{unchecked.map((f, k) => (
          <li key={k} className="border-t border-line py-2 text-[12.5px] text-ink-2 first:border-t-0">
            <div className="flex flex-wrap items-center gap-2"><Pill v={f.verdict} /><span className="font-medium text-ink">{f.repo}</span><span className="min-w-0 flex-1 text-[13px] text-ink [overflow-wrap:anywhere]">{inline(f.title)}</span>
              <Button variant="ghost" size="sm" onClick={() => review(f.candidate_id)}><RotateCcw size={12} />Run again</Button></div>
            {f.how ? <div className="mt-0.5 text-mute">{f.how}</div> : null}
          </li>))}</ul>
      </Fold>

      <Fold id="dg-push" open={isOpen("dg-push")} onToggle={() => toggle("dg-push")} title="About how a push was made" hint="not about code" icon={GitBranch} count={aboutPush.length}>
        <ul className="m-0 list-none p-0">{aboutPush.map((f: any, k: number) => (
          <li key={k} className="flex flex-wrap items-center gap-2 border-t border-line py-2 text-[12.5px] text-ink-2 first:border-t-0">
            <Pill v={f.verdict} /><span className="font-medium text-ink">{f.repo}</span><span className="text-mute">{f.ref}</span><span className="min-w-0 flex-1 [overflow-wrap:anywhere]">{inline(f.title)}</span>
          </li>))}</ul>
      </Fold>

      <Fold id="dg-history" open={isOpen("dg-history")} onToggle={() => toggle("dg-history")} title="All reviews" hint="every verdict, newest first" icon={History} count={reviews.length}>
        <ul className="m-0 list-none p-0">{reviews.map((c) => <ChangeRow key={c.id} c={c} onReview={review} />)}</ul>
      </Fold>
    </div>
  );
}
