"use client";
/* Watching (#/watching, 5 Oct 2026, HARNESS.md Phase 2): what the project is waiting for, and what Jarvis does about it.
   Needs come from his memory notes, terminal sessions and voice; each says what number or answer it must produce. Jarvis
   matches them against every data source it can see (your folders and the stores it can read) and, when something
   could answer one, checks it by itself (read-only) and puts the result in To do. One tap dismisses anything; a dismissed
   need never acts again. Simple on purpose: lists, one line each, details on demand. */
import { useEffect, useState } from "react";
import { Radar, Database, HelpCircle, Hourglass, KeyRound, Scale, Handshake, Activity, ChevronDown, ChevronRight, X, RotateCcw, Target } from "lucide-react";
import { WH } from "@/lib/wh";
import { useShell } from "@/lib/useShell";
import { Card } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

const C = (s: any) => WH.clean(s);
const KINDS: [string, string, any][] = [
  ["data_need", "Data the project is missing", Database],
  ["answer_need", "Questions nobody has answered", HelpCircle],
  ["event_need", "Waiting for something to happen", Hourglass],
  ["access_need", "Access or logins needed", KeyRound],
  ["open_decision", "Open decisions", Scale],
];
const SYS: Record<string, string> = { gcs: "bucket", local: "local", db: "database" };
const srcLabel = (id: string) => { const [sys, ...rest] = id.split(":"); const name = rest.join(":").replace(/^(dev|uat):/, (m) => m.toUpperCase()); return `${name} · ${SYS[sys] || sys}`; };
const when = (iso?: string) => (iso ? WH.fmtWhen(new Date(iso)) : "");

function NeedRow({ n, onDismiss }: { n: any; onDismiss: () => void }) {
  const [openRow, setOpen] = useState(false);
  const has = n.candidates && n.candidates.length > 0;
  return (
    <li className="border-t border-line first:border-t-0">
      <div className="flex items-start gap-2 py-2.5">
        <button type="button" onClick={() => setOpen(!openRow)} aria-expanded={openRow} className="mt-[3px] text-mute hover:text-ink" aria-label="Details">
          {openRow ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
        </button>
        <div className="min-w-0 flex-1">
          <p className="m-0 text-[14px] leading-snug text-ink [overflow-wrap:anywhere]">{C(n.statement)}</p>
          {has && <p className="m-0 mt-1 text-[12.5px] text-ion">Data to check: {n.candidates.slice(0, 3).map(srcLabel).join(", ")}</p>}
          {openRow && (
            <div className="mt-2 grid gap-1 text-[12.5px] leading-snug text-ink-2">
              {n.target && <div><b className="font-semibold">Must produce:</b> {C(n.target)}</div>}
              {n.why && <div><b className="font-semibold">Why:</b> {C(n.why)}</div>}
              {n.assumed && <div><b className="font-semibold">Assumed meanwhile:</b> {C(n.assumed)}</div>}
              <div className="text-mute">From {String(n.source_ref || "").replace(/^memory:/, "note: ").replace(/^cc:/, "session ")} · {n.origin === "owner_said" ? "you said it" : n.origin === "external" ? "someone else said it" : "from your work"}{n.created ? " · noted " + when(n.created) : ""}</div>
            </div>
          )}
        </div>
        <button type="button" onClick={onDismiss} title="Not needed any more" className="mt-[2px] rounded-md p-1 text-mute hover:bg-ink/5 hover:text-ink" aria-label="Dismiss"><X size={14} /></button>
      </div>
    </li>
  );
}

// "4 findings, 1 question, 2 drafts · your brief Fri 23 Oct"
function deskLine(k: any): string {
  const c = k.counts || {}, n = (x: string) => c[x] || 0;
  const parts = ([["finding", "findings"], ["question", "questions"], ["draft", "drafts"], ["anomaly", "data changes"], ["research", "papers"]] as const)
    .filter(([x]) => n(x)).map(([x, w]) => `${n(x)} ${n(x) === 1 ? w.replace(/s$/, "") : w}`);
  const d = new Date(k.next_brief + "T09:00:00");
  const day = d.toLocaleDateString("en-GB", { weekday: "short", day: "numeric", month: "short" });
  return (parts.length ? parts.join(", ") : "Nothing new yet") + ` · your brief ${day}`;
}

export default function Watching() {
  useShell();
  const [d, setD] = useState<any>(null);
  const [showDismissed, setShowDismissed] = useState(false);
  const load = () => WH.api("/api/watching").then((x: any) => x && !x.error && setD(x)).catch(() => {});
  useEffect(() => { load(); const t = setInterval(() => { if (!document.hidden) load(); }, 30000); return () => clearInterval(t); }, []);
  const act = async (kind: string, id: string, op: "dismiss" | "restore") => {
    try { await WH.api("/api/watch", { kind, id, op }); } catch {}
    load();
  };
  if (!d) return <p className="text-mute">Loading…</p>;
  const needs: any[] = d.needs || [];
  const withData = needs.filter((n) => n.candidates && n.candidates.length).length;

  return (
    <div className="grid min-w-0 grid-cols-[minmax(0,1fr)] gap-6" data-view="watching">
      <div>
        <h1 className="m-0 flex items-center gap-2.5 font-display text-[26px] font-semibold tracking-[-.02em] text-ink"><Radar size={22} strokeWidth={1.8} className="text-ion" />Watching</h1>
        <p className="mb-0 mt-1.5 max-w-[68ch] text-[14px] text-ink-2">
          What the project is waiting for: {needs.length} open needs, {withData} with data Jarvis can check, {(d.commitments || []).length} promises.
          When something that could answer one turns up, Jarvis checks it by itself and puts the result in To do.
          {d.recognizers && d.recognizers.none ? ` ${d.recognizers.none} of them name nothing data could show (decisions, people's answers): those wait for a person, not a table.` : ""}
        </p>
        {d.not_work && Object.keys(d.not_work).length > 0 && (
          <p className="mb-0 mt-1 max-w-[68ch] text-[13px] text-mute">
            Not shown, not project work: {Object.entries(d.not_work).map(([k, n]: any) => `${n} ${({ admin: "admin", setup: "machine and tool setup", jarvis: "about building Jarvis", personal: "personal" } as any)[k] || k}`).join(" · ")}.
          </p>
        )}
      </div>

      {(d.logins || []).length > 0 && (
        <Card className="border-[color-mix(in_srgb,var(--warn)_40%,transparent)] px-4 py-3 text-[13.5px] text-ink">
          Part of your data is out of sight until you log in again: <b>{d.logins.join(", ")}</b>{d.logins.includes("gcloud") ? " (run gcloud auth login)." : "."}
        </Card>
      )}

      {(d.goals || []).length > 0 && (
        <Card className="px-4 py-3">
          <div className="mb-1 flex items-center gap-2 font-mono text-[11px] font-semibold uppercase tracking-[.1em] text-mute"><Target size={13} />Your goals (from Jira)</div>
          <ul className="m-0 list-none p-0">{d.goals.map((g: any) => { let src: any = {}; try { src = JSON.parse(g.source || "{}"); } catch {} return (
            <li key={g.id} className="flex items-start gap-2 border-t border-line py-2.5 first:border-t-0">
              <div className="min-w-0 flex-1 text-[14px] leading-snug text-ink">{C(g.statement)}{src.key && <span className="ml-2 font-mono text-[11.5px] text-mute">{src.key}</span>}</div>
              <button type="button" onClick={() => act("goal", g.id, "dismiss")} title="Not a goal any more" className="rounded-md p-1 text-mute hover:bg-ink/5 hover:text-ink" aria-label="Dismiss"><X size={14} /></button>
            </li>); })}</ul>
        </Card>
      )}

      {d.desk && (
        // Jarvis's own desk (9 Oct): what it noticed by itself stays with it and comes to him in the fortnightly brief
        <Card className="px-4 py-3">
          <div className="mb-1 flex items-center gap-2 font-mono text-[11px] font-semibold uppercase tracking-[.1em] text-mute"><Activity size={13} />On Jarvis's desk</div>
          <p className="m-0 text-[13.5px]">{deskLine(d.desk)}</p>
        </Card>
      )}

      {KINDS.map(([k, title, Icon]) => {
        const rows = needs.filter((n) => n.kind === k).sort((a, b) => (b.candidates?.length ? 1 : 0) - (a.candidates?.length ? 1 : 0));
        if (!rows.length) return null;
        return (
          <Card key={k} className="px-4 py-3">
            <div className="mb-1 flex items-center gap-2 font-mono text-[11px] font-semibold uppercase tracking-[.1em] text-mute"><Icon size={13} />{title}<span className="text-ink-2">· {rows.length}</span></div>
            <ul className="m-0 list-none p-0">{rows.map((n) => <NeedRow key={n.id} n={n} onDismiss={() => act("need", n.id, "dismiss")} />)}</ul>
          </Card>
        );
      })}

      {(d.commitments || []).length > 0 && (
        <Card className="px-4 py-3">
          <div className="mb-1 flex items-center gap-2 font-mono text-[11px] font-semibold uppercase tracking-[.1em] text-mute"><Handshake size={13} />Promises</div>
          <ul className="m-0 list-none p-0">{d.commitments.map((c: any) => (
            <li key={c.id} className="flex items-start gap-2 border-t border-line py-2.5 first:border-t-0">
              <div className="min-w-0 flex-1 text-[14px] leading-snug text-ink">{C(c.what)}{c.due && <span className="ml-2 text-[12px] text-mute">due {c.due}</span>}
                {c.check_verdict && <div className={cn("text-[12px]", c.check_verdict === "not_yet" ? "text-warn" : "text-mute")}>{c.check_verdict === "not_yet" ? "Checked: not kept yet" : "Checked: could not tell"}{c.check_note ? ` · ${C(c.check_note)}` : ""}{c.nudged_at ? " · a nudge is drafted in To do" : ""}</div>}</div>
              <button type="button" onClick={() => act("commitment", c.id, "dismiss")} title="Done or not needed" className="rounded-md p-1 text-mute hover:bg-ink/5 hover:text-ink" aria-label="Dismiss"><X size={14} /></button>
            </li>
          ))}</ul>
        </Card>
      )}

      {(d.anomalies || []).length > 0 && (
        <Card className="border-[color-mix(in_srgb,var(--warn)_40%,transparent)] px-4 py-3">
          <div className="mb-1 font-mono text-[11px] font-semibold uppercase tracking-[.1em] text-mute">Data that stopped or shifted</div>
          <ul className="m-0 grid list-none gap-1 p-0 text-[13.5px] text-ink">{d.anomalies.map((e: any, i: number) => { let x: any = {}; try { x = JSON.parse(e.detail || "{}"); } catch {} return (
            <li key={i} className="flex gap-2"><span className="min-w-0 flex-1">{e.kind === "feed_stopped" ? `${x.name}: nothing new since ${x.last} (${x.late_days} days)` : e.kind === "quality_shift" ? `${x.name}: ${x.metric} ${Math.round((x.was || 0) * 1000) / 10}% → ${Math.round((x.now || 0) * 1000) / 10}%` : `${x.name}: rows ${x.from} → ${x.to}`}</span><span className="flex-none text-[12px] text-mute">{when(e.at)}</span></li>); })}</ul>
        </Card>
      )}

      {(d.org || []).length > 0 && (
        <Card className="px-4 py-3">
          <div className="mb-1 font-mono text-[11px] font-semibold uppercase tracking-[.1em] text-mute">Around the project</div>
          <ul className="m-0 list-none p-0">{d.org.map((o: any) => (
            <li key={o.id} className="flex items-start gap-2 border-t border-line py-2 first:border-t-0">
              <span className="mt-[2px] flex-none font-mono text-[10.5px] uppercase text-ion">{o.kind}</span>
              <div className="min-w-0 flex-1 text-[13.5px] leading-snug"><span className="text-ink">{C(o.what)}</span>
                <div className="text-[12px] text-mute">{[o.who, C(o.place)].filter(Boolean).join(" · ")}</div></div>
            </li>
          ))}</ul>
        </Card>
      )}

      {(d.investigations || []).length > 0 && (
        <Card className="px-4 py-3">
          <div className="mb-1 font-mono text-[11px] font-semibold uppercase tracking-[.1em] text-mute">Investigations Jarvis runs by itself</div>
          <ul className="m-0 list-none p-0">{d.investigations.map((v: any) => (
            <li key={v.id} className="flex items-start gap-2 border-t border-line py-2 first:border-t-0">
              <span className={cn("mt-[2px] flex-none font-mono text-[10.5px] uppercase", v.state === "question" ? "text-warn" : "text-ion")}>{v.state === "running" ? "step running" : v.state}</span>
              <div className="min-w-0 flex-1 text-[13.5px] leading-snug"><span className="text-ink [overflow-wrap:anywhere]">{C(v.goal)}</span>
                <div className="text-[12px] text-mute">{v.steps_done} of {v.budget} steps used · {v.passed} settled{v.question ? ` · Question: ${C(v.question)}` : ""}</div></div>
            </li>
          ))}</ul>
          <p className="mb-0 mt-2 text-[12px] text-mute">Read-only, one step per session, overnight or in unused time. Each ends in your To do: a brief, a question, or where it stands.</p>
        </Card>
      )}

      {(d.docs_read || []).length > 0 && (
        <Card className="px-4 py-3">
          <div className="mb-1 font-mono text-[11px] font-semibold uppercase tracking-[.1em] text-mute">Documents Jarvis read</div>
          <ul className="m-0 list-none p-0">{d.docs_read.map((x: any) => (
            <li key={x.id} className="border-t border-line py-2 first:border-t-0 text-[13.5px] leading-snug">
              <span className="text-ink [overflow-wrap:anywhere]">{C(x.name)}</span> <span className="text-[12px] text-mute">· {when(x.read_at)}</span>
              <div className="text-[12.5px] text-ink-2">{C(x.summary)}</div>
              {(x.mentions || []).length > 0 && <div className="text-[12px] text-mute">{x.mentions.map(C).join(" · ")}</div>}
            </li>
          ))}</ul>
          <p className="mb-0 mt-2 text-[12px] text-mute">Project documents the org watch found, opened and read (up to 4 a day), so they can be matched to open needs by what they say.</p>
        </Card>
      )}

      {(d.research || []).length > 0 && (
        <Card className="px-4 py-3">
          <div className="mb-1 font-mono text-[11px] font-semibold uppercase tracking-[.1em] text-mute">From research outside</div>
          <ul className="m-0 list-none p-0">{d.research.map((f: any) => (
            <li key={f.id} className="border-t border-line py-2.5 first:border-t-0">
              <div className="flex items-start gap-2">
                <span className="mt-[2px] flex-none font-mono text-[10.5px] uppercase text-ion">{f.kind}</span>
                <div className="min-w-0 flex-1 text-[13.5px] leading-snug">
                  <a href={f.url} target="_blank" rel="noreferrer" className="text-ink underline decoration-line underline-offset-2 [overflow-wrap:anywhere]">{f.title}</a>
                  {f.published && <span className="text-[12px] text-mute"> · {f.published}</span>}
                  <div className="mt-1 text-[12.5px] text-ink-2">For: {C(f.problem)}</div>
                  <div className="text-[12.5px] text-ink-2">Claims: {f.claim}{f.evidence ? ` (${f.evidence})` : ""}</div>
                  <div className="text-[12.5px] text-ink-2">Here: {C(f.applies)}{f.effort ? ` · Effort: ${f.effort}` : ""}</div>
                  {f.caveat && <div className="text-[12px] text-mute">Caveat: {f.caveat}</div>}
                </div>
              </div>
              <div className="mt-1.5 flex gap-2 pl-[52px]">{f.state === "worth" ? <span className="text-[12px] text-ion">Being checked against our data</span> : <>
                <Button size="sm" variant="primary" onClick={async () => { try { await WH.api("/api/research_mark", { id: f.id, verdict: "worth" }); } catch {} load(); }}>Worth trying</Button>
                <Button size="sm" variant="ghost" onClick={async () => { try { await WH.api("/api/research_mark", { id: f.id, verdict: "not" }); } catch {} load(); }}>Not useful</Button></>}</div>
            </li>
          ))}</ul>
          <p className="mb-0 mt-2 text-[12px] text-mute">Weekly, from your open problems. Outside content: nothing is tried until you say so, and then read-only first.</p>
        </Card>
      )}

      {(d.stale || []).length > 0 && (
        <Card className="px-4 py-3">
          <div className="mb-1 font-mono text-[11px] font-semibold uppercase tracking-[.1em] text-mute">Newer data than your notebooks used</div>
          <ul className="m-0 grid list-none gap-1 p-0 text-[13px] text-ink-2">{d.stale.map((a: any) => (
            <li key={a.artifact}><span className="text-ink">{a.artifact.split("/").slice(-2).join("/")}</span>: {a.tables.join(", ")} (data to {a.newest}; notebook last changed {String(a.changed).slice(0, 10)})</li>
          ))}</ul>
          <p className="mb-0 mt-2 text-[12px] text-mute">A notice only: nothing is refreshed by itself.</p>
        </Card>
      )}

      {(d.changes || []).length > 0 && (
        <Card className="px-4 py-3">
          <div className="mb-1 font-mono text-[11px] font-semibold uppercase tracking-[.1em] text-mute">New in your data</div>
          <ul className="m-0 grid list-none gap-1 p-0 text-[13px] text-ink-2">{d.changes.map((e: any, i: number) => (
            <li key={i} className="flex gap-2"><span className="min-w-0 flex-1 truncate">{srcLabel(e.source_id)}: {e.kind.replace("_", " ")}</span><span className="flex-none text-mute">{when(e.at)}</span></li>
          ))}</ul>
          <p className="mb-0 mt-2 text-[12px] text-mute">Seeing {(d.catalog || []).map((c: any) => `${c.n} ${SYS[c.system.replace(/_.*/, "")] || c.system} ${c.system.startsWith("db_") ? "(" + c.system.slice(3).toUpperCase() + ")" : ""}`).join(" · ")} sources.</p>
        </Card>
      )}

      {(d.dismissed || []).length > 0 && (
        <div>
          <Button variant="ghost" size="sm" onClick={() => setShowDismissed(!showDismissed)}>{showDismissed ? "Hide" : "Show"} dismissed ({d.dismissed.length})</Button>
          {showDismissed && <ul className="mt-2 grid list-none gap-1 p-0">{d.dismissed.map((n: any) => (
            <li key={n.id} className="flex items-center gap-2 text-[13px] text-mute"><span className="min-w-0 flex-1 truncate">{C(n.statement)}</span>
              <button type="button" onClick={() => act("need", n.id, "restore")} className="flex items-center gap-1 text-[12px] hover:text-ink"><RotateCcw size={12} />Bring back</button></li>
          ))}</ul>}
        </div>
      )}
    </div>
  );
}
