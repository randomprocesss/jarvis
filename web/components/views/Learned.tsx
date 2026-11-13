"use client";
/* Learned (#/learned, 5 Oct 2026, HARNESS.md Phase 5): what Jarvis has learned from him, and the way to undo it. Every change
   went through app/learned.py (origin checked, permissions never learned); each accepted one can be undone here, and every
   rule, note, memory or fact can be retired with one tap. "Label answers" is the weekly 10-minute page: past answers worth a
   look, marked good or bad (G / B / S keys); bad ones teach the style rules. Simple on purpose: lists, one line each. */
import { useEffect, useState } from "react";
import { GraduationCap, Undo2, X, Check, ThumbsUp, ThumbsDown, SkipForward, ChevronDown, ChevronRight } from "lucide-react";
import { WH } from "@/lib/wh";
import { useShell } from "@/lib/useShell";
import { Card } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

const C = (s: any) => WH.clean(s);
const when = (iso?: string) => (iso ? WH.fmtWhen(new Date(iso)) : "");
const TBL: Record<string, string> = { rules: "rule", facts: "fact", memory: "memory", recipient_notes: "note", all: "" };
const ORIGIN: Record<string, string> = { owner_said: "from you", owner_ack: "you confirmed", executed: "Jarvis's own upkeep", external: "from outside content", inferred: "inferred" };
const Head = ({ children }: { children: any }) => <div className="mb-1 font-mono text-[11px] font-semibold uppercase tracking-[.1em] text-mute">{children}</div>;
const XBtn = ({ onClick, title }: { onClick: () => void; title: string }) => (
  <button type="button" onClick={onClick} title={title} aria-label={title} className="mt-[2px] rounded-md p-1 text-mute hover:bg-ink/5 hover:text-ink"><X size={14} /></button>
);

function Labels() {
  const [items, setItems] = useState<any[] | null>(null);
  const [i, setI] = useState(0);
  const [note, setNote] = useState("");
  useEffect(() => { WH.api("/api/labels").then((x: any) => setItems((x && x.items) || [])).catch(() => setItems([])); }, []);
  const cur = items && items[i];
  const mark = async (label: "good" | "bad" | "skip") => {
    if (!cur) return;
    try { await WH.api("/api/learned_label", { trace: cur.trace, label, note, q: cur.q, answer: cur.answer }); } catch {}
    setNote(""); setI(i + 1);
  };
  useEffect(() => {
    const k = (e: KeyboardEvent) => {
      if ((e.target as HTMLElement)?.tagName === "INPUT") return;
      if (e.key === "g") mark("good"); else if (e.key === "b") mark("bad"); else if (e.key === "s") mark("skip");
    };
    window.addEventListener("keydown", k); return () => window.removeEventListener("keydown", k);
  });
  if (!items) return <p className="text-mute">Loading…</p>;
  if (!cur) return <Card className="px-4 py-3 text-[14px] text-ink-2">{items.length ? "Done for this week, thanks." : "Nothing worth a look this week."}</Card>;
  return (
    <Card className="grid gap-3 px-4 py-4">
      <div className="flex items-baseline justify-between"><Head>Answer {i + 1} of {items.length}</Head><span className="text-[12px] text-mute">{when(cur.at)} · {cur.why.join(", ")}</span></div>
      <div className="grid gap-2 text-[14px] leading-snug">
        <div><span className="text-mute">You: </span><span className="text-ink">{C(cur.q)}</span></div>
        <div><span className="text-mute">Jarvis: </span><span className="text-ink [overflow-wrap:anywhere]">{C(cur.answer)}</span></div>
      </div>
      <input value={note} onChange={(e) => setNote(e.target.value)} placeholder="What was wrong (optional)" className="rounded-md border border-line bg-transparent px-3 py-1.5 text-[13.5px] text-ink outline-none focus:border-ion" />
      <div className="flex gap-2">
        <Button size="sm" variant="primary" onClick={() => mark("good")}><ThumbsUp size={14} />Good <kbd className="ml-1 text-[11px] opacity-60">G</kbd></Button>
        <Button size="sm" variant="danger" onClick={() => mark("bad")}><ThumbsDown size={14} />Bad <kbd className="ml-1 text-[11px] opacity-60">B</kbd></Button>
        <Button size="sm" variant="ghost" onClick={() => mark("skip")}><SkipForward size={14} />Skip <kbd className="ml-1 text-[11px] opacity-60">S</kbd></Button>
      </div>
    </Card>
  );
}

export default function Learned({ sub }: { sub: string }) {
  useShell();
  const [d, setD] = useState<any>(null);
  const [allFacts, setAllFacts] = useState(false);
  const load = () => WH.api("/api/learned").then((x: any) => x && !x.error && setD(x)).catch(() => {});
  useEffect(() => { load(); const t = setInterval(() => { if (!document.hidden) load(); }, 30000); return () => clearInterval(t); }, []);
  const undo = async (id: string) => { try { await WH.api("/api/learned_undo", { id }); } catch {} load(); };
  const retire = async (table: string, id: string, recipient?: string, channel?: string) => {
    try { await WH.api("/api/learned_retire", { table, id, recipient, channel }); } catch {} load();
  };
  const tab = sub === "label" ? "label" : "learned";
  return (
    <div className="grid min-w-0 grid-cols-[minmax(0,1fr)] gap-6" data-view="learned">
      <div>
        <h1 className="m-0 flex items-center gap-2.5 font-display text-[26px] font-semibold tracking-[-.02em] text-ink"><GraduationCap size={22} strokeWidth={1.8} className="text-ion" />Learned</h1>
        <p className="mb-0 mt-1.5 max-w-[68ch] text-[14px] text-ink-2">
          What Jarvis has learned from you this week, and how it behaves because of it. Every change can be undone here. Permissions are never learned.
        </p>
        <div className="mt-3 flex gap-2">
          <a href="#/trust/learned" className={cn("rounded-md px-3 py-1 text-[13px] no-underline", tab === "learned" ? "bg-ink/10 text-ink" : "text-mute hover:text-ink")}>What it learned</a>
          <a href="#/trust/learned/label" className={cn("rounded-md px-3 py-1 text-[13px] no-underline", tab === "label" ? "bg-ink/10 text-ink" : "text-mute hover:text-ink")}>Label answers (10 min)</a>
        </div>
      </div>
      {tab === "label" ? <Labels /> : !d ? <p className="text-mute">Loading…</p> : (<>
        <Card className="px-4 py-3">
          <Head>This week · {(d.edits || []).filter((e: any) => e.accepted).length} changes, {(d.edits || []).filter((e: any) => !e.accepted).length} refused</Head>
          {(d.edits || []).length === 0 ? <p className="m-0 py-1 text-[13.5px] text-mute">Nothing new this week.</p> :
          <ul className="m-0 list-none p-0">{d.edits.slice(0, 60).map((e: any) => (
            <li key={e.id} className="flex items-start gap-2 border-t border-line py-2 first:border-t-0">
              <span className={cn("mt-[3px] flex-none", e.accepted ? (e.undone_at ? "text-mute" : "text-ok") : "text-warn")}>{e.accepted ? <Check size={13} /> : <X size={13} />}</span>
              <div className="min-w-0 flex-1 text-[13.5px] leading-snug">
                <span className="text-ink [overflow-wrap:anywhere]">{({ ADD: "Added", CLOSE: "Retired", UPDATE: "Changed" } as any)[e.op] || e.op} {TBL[e.tbl] || e.tbl}{e.text ? ": " + C(e.text) : ""}</span>
                <div className="text-[12px] text-mute">{when(e.at)} · {ORIGIN[e.origin] || e.origin}{!e.accepted && e.why ? " · refused: " + e.why : ""}{e.undone_at ? " · undone" : ""}</div>
              </div>
              {e.undoable && <button type="button" onClick={() => undo(e.id)} className="flex flex-none items-center gap-1 text-[12px] text-mute hover:text-ink"><Undo2 size={12} />Undo</button>}
            </li>
          ))}</ul>}
        </Card>

        <Card className="px-4 py-3">
          <div className="flex items-baseline justify-between">
            <Head>Improving itself{d.autopilot && d.autopilot.paused ? " · paused" : ""}</Head>
            <button type="button" onClick={async () => { try { await WH.api("/api/autopilot", { op: d.autopilot && d.autopilot.paused ? "resume" : "pause" }); } catch {} load(); }}
              className="text-[12px] text-mute hover:text-ink">{d.autopilot && d.autopilot.paused ? "Resume" : "Pause"}</button>
          </div>
          <p className="m-0 mb-1 text-[12.5px] text-mute">From answers you mark bad, your corrections and claims the guard caught: Jarvis fixes itself, tests the fix (the live safety cases 3 times), ships it, watches it for a day, and undoes it if things get worse.</p>
          {((d.autopilot && d.autopilot.tickets) || []).length === 0 ? <p className="m-0 py-1 text-[13.5px] text-mute">Nothing yet.</p> :
          <ul className="m-0 list-none p-0">{d.autopilot.tickets.map((t: any) => (
            <li key={t.id} className="flex items-start gap-2 border-t border-line py-2 first:border-t-0">
              <span className={cn("mt-[2px] flex-none font-mono text-[10.5px] uppercase", t.state === "kept" || t.state === "watching" ? "text-ok" : t.state === "failed" || t.state === "rolled_back" ? "text-warn" : "text-ion")}>{t.state.replace("_", " ")}</span>
              <div className="min-w-0 flex-1 text-[13.5px] leading-snug"><span className="text-ink">{C(t.title)}</span>
                {t.result && <div className="text-[12px] text-mute [overflow-wrap:anywhere]">{C(t.result)}</div>}</div>
              <span className="flex-none text-[11.5px] text-mute">{when(t.at)}</span>
            </li>
          ))}</ul>}
          {(d.staged || []).length > 0 && <p className="m-0 mt-2 text-[12.5px] text-mute">{d.staged.length} learned rule(s) waiting for the safety test before they go live.</p>}
        </Card>

        {(d.skills || []).length > 0 && (
          <Card className="px-4 py-3">
            <Head>Skills Jarvis could add</Head>
            <p className="m-0 mb-1 text-[12.5px] text-mute">Drafted from work you accepted, read-only, safety-scanned. Added to your skills (your own sessions use them too) only when you press Add.</p>
            <ul className="m-0 list-none p-0">{d.skills.map((k: any) => (
              <li key={k.name} className="border-t border-line py-2.5 first:border-t-0">
                <div className="text-[13.5px] leading-snug"><span className="font-mono text-[12.5px] text-ion">{k.name}</span> <span className="text-ink">{C(k.description)}</span></div>
                {k.why && <div className="text-[12.5px] text-ink-2">{C(k.why)}</div>}
                <pre className="mt-1.5 max-h-48 overflow-auto whitespace-pre-wrap rounded border border-line bg-line-2 p-2 font-mono text-[11.5px] leading-snug text-ink-2">{C(k.preview)}</pre>
                <div className="mt-1.5 flex gap-2">
                  <Button size="sm" variant="primary" onClick={async () => { try { await WH.api("/api/skill_decide", { name: k.name, add: true }); } catch {} load(); }}><Check size={14} />Add</Button>
                  <Button size="sm" variant="ghost" onClick={async () => { try { await WH.api("/api/skill_decide", { name: k.name, add: false }); } catch {} load(); }}>Not now</Button>
                </div>
              </li>
            ))}</ul>
          </Card>
        )}

        {(d.tools || []).length > 0 && (
          <Card className="px-4 py-3">
            <Head>Tools Jarvis built</Head>
            <p className="m-0 mb-1 text-[12.5px] text-mute">Helpers its workers wrote and reuse in their own runs. Trusted means unattended jobs may call it too: only when you press it.</p>
            <ul className="m-0 list-none p-0">{d.tools.map((t: any) => (
              <li key={t.name} className="flex items-start gap-2 border-t border-line py-2 first:border-t-0">
                <span className={cn("mt-[2px] flex-none font-mono text-[10.5px] uppercase", t.state === "flagged" ? "text-warn" : "text-ion")}>{t.state}</span>
                <div className="min-w-0 flex-1 text-[13.5px] leading-snug"><span className="font-mono text-[12.5px] text-ink">{t.name}</span> <span className="text-ink-2">{C(t.purpose)}</span>
                  <div className="text-[12px] text-mute [overflow-wrap:anywhere]">used {t.uses || 0}x{t.state === "flagged" && t.scan ? ` · scan: ${t.scan.map((h: any) => h[0]).join(", ")}` : ""}</div></div>
                {t.state === "staged" && <Button size="sm" variant="ghost" onClick={async () => { try { await WH.api("/api/forge_promote", { name: t.name }); } catch {} load(); }}>Make trusted</Button>}
              </li>
            ))}</ul>
          </Card>
        )}

        <Card className="px-4 py-3">
          <Head>How Jarvis behaves · {(d.rules || []).length} of {d.max_rules} rules</Head>
          <ul className="m-0 list-none p-0">{(d.rules || []).map((r: any) => (
            <li key={r.id} className="flex items-start gap-2 border-t border-line py-2 first:border-t-0">
              <span className="mt-[2px] flex-none font-mono text-[10.5px] uppercase text-ion">{r.scope}</span>
              <span className="min-w-0 flex-1 text-[13.5px] leading-snug text-ink">{C(r.text)}</span>
              <XBtn onClick={() => retire("rules", r.id)} title="Retire this rule" />
            </li>
          ))}</ul>
        </Card>

        {(d.recipients || []).length > 0 && (
          <Card className="px-4 py-3">
            <Head>How you write to people (learned from your edits)</Head>
            <ul className="m-0 list-none p-0">{d.recipients.map((r: any) => (
              <li key={r.recipient + r.channel} className="flex items-start gap-2 border-t border-line py-2 first:border-t-0">
                <div className="min-w-0 flex-1 text-[13.5px] leading-snug"><b className="font-semibold text-ink">{C(r.recipient)}</b> <span className="text-ink-2">{C(r.note)}</span>
                  {r.basis_void === "all" && <div className="mt-1 text-[12.5px] font-medium text-warn">This note rests on a recording fault: the reply it was learned from went out as drafted (found 12 Nov). Dropping it is recommended; press the × on the right.</div>}
                  {r.basis_void === "last" && <div className="mt-1 text-[12.5px] text-mute">Its latest change rests on a recording fault (found 12 Nov); the earlier parts came from real edits of yours and stand.</div>}</div>
                <XBtn onClick={() => retire("recipient_notes", "", r.recipient, r.channel)} title="Drop this note" />
              </li>
            ))}</ul>
          </Card>
        )}

        <Card className="px-4 py-3">
          <Head>What you asked me to remember</Head>
          <ul className="m-0 list-none p-0">{(d.memory || []).map((m: any) => (
            <li key={m.id} className="flex items-start gap-2 border-t border-line py-2 first:border-t-0">
              <span className="min-w-0 flex-1 text-[13.5px] leading-snug text-ink">{C(m.text)}</span>
              <XBtn onClick={() => retire("memory", m.id)} title="Forget this" />
            </li>
          ))}</ul>
        </Card>

        <Card className="px-4 py-3">
          <button type="button" onClick={() => setAllFacts(!allFacts)} className="flex items-center gap-1 text-left" aria-expanded={allFacts}>
            {allFacts ? <ChevronDown size={13} className="text-mute" /> : <ChevronRight size={13} className="text-mute" />}<Head>What I know about your work · {(d.facts || []).length}</Head>
          </button>
          {allFacts && <ul className="m-0 list-none p-0">{(d.facts || []).map((f: any) => (
            <li key={f.id} className="flex items-start gap-2 border-t border-line py-2 first:border-t-0">
              <div className="min-w-0 flex-1 text-[13.5px] leading-snug"><span className="text-ink">{C(f.text)}</span>
                <div className="text-[12px] text-mute">since {f.valid_from} · {ORIGIN[f.origin] || f.origin}</div></div>
              <XBtn onClick={() => retire("facts", f.id)} title="Not true any more" />
            </li>
          ))}</ul>}
        </Card>

        {(d.signals || []).length > 0 && (
          <p className="m-0 text-[12.5px] text-mute">Signals this week: {d.signals.map((s: any) => `${s.n} ${s.kind.replace(/_/g, " ")}`).join(" · ")}.</p>
        )}
      </>)}
    </div>
  );
}
