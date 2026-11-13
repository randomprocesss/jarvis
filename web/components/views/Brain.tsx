"use client";
/* Brain (new in the redesign, 12 Nov 2026): what Jarvis knows about the project and what is still open. Ask the fact
   ledger; open questions judged still open; promises; records in conflict; what it is trying from outside research; what
   the project waits for (the old Watching screen); the knowledge base's own health. An answer here is a lead: a number
   that goes to anyone is checked against its source first. */
import { useEffect, useState } from "react";
import { Search } from "lucide-react";
import { WH } from "@/lib/wh";
import { useShell } from "@/lib/useShell";
import { Button } from "@/components/ui/button";
import { Empty } from "@/components/ui/card";
import { useApi, Panel, Chip, Table, PageTitle, HashTabs, Loading, Note, when, cut } from "@/components/ui/kit";
import WatchingView from "@/components/views/Watching";

const C = (s) => WH.clean(s);
const ws = (s) => String(s || "").replace(/-/g, " ");

function Ask() {
  const [q, setQ] = useState(""), [res, setRes] = useState<any>(null), [busy, setBusy] = useState(false);
  const run = async (t: string) => { if (!t) return; setBusy(true); try { setRes(await WH.api("/api/kb?q=" + encodeURIComponent(t))); } finally { setBusy(false); } };
  const go = async (e?) => { e?.preventDefault(); run(q.trim()); };
  useEffect(() => { const t = WH.pref.get("wh_ask"); if (t) { WH.pref.set("wh_ask", null); setQ(t); run(t); } }, []);   // a question handed over from Home
  return (
    <div className="grid gap-4">
      <form onSubmit={go} className="flex gap-2">
        <label className="relative min-w-0 flex-1">
          <Search size={16} className="pointer-events-none absolute left-3.5 top-1/2 -translate-y-1/2 text-mute" />
          <input value={q} onChange={(e) => setQ(e.target.value)} autoFocus placeholder="Ask the ledger: a number, a decision, a ticket ..."
            className="h-11 w-full rounded-[10px] border border-line-3 bg-card pl-10 pr-3 text-[15px] text-ink outline-none placeholder:text-mute focus:border-ion focus:shadow-[0_0_0_3px_var(--ion-soft)]" />
        </label>
        <Button variant="primary" size="lg" busy={busy} onClick={() => go()}>Look up</Button>
      </form>
      {!res ? <Note>Each result is one checked statement from the ledger with the note it came from. <b className="font-semibold">Current</b> is the value in force; a <b className="font-semibold">replaced</b> one shows what replaced it; a record <b className="font-semibold">in conflict</b> shows the other side. These are leads: check a number against its source before it goes to anyone.</Note>
        : !res.records?.length ? <Empty title="No record matches">Try other words, a ticket key or a material number.</Empty> : (
          <div className="grid gap-2">
            {res.records.map((r) => (
              <div key={r.id} className="rounded-[12px] border border-line bg-card px-4 py-3">
                <div className="flex flex-wrap items-center gap-2 text-[12.5px] text-mute">
                  {r.origin === "jarvis" ? <Chip tone="ion">{r.kind === "open" ? "open question" : r.kind === "promise" ? "promise owed" : "goal"}</Chip>
                    : r.status !== "current" ? <Chip tone="mute">replaced</Chip> : r.conflicts?.length ? <Chip tone="warn">in conflict</Chip> : <Chip tone="ok">current</Chip>}
                  <span>{r.kind}</span><span>·</span><span>{ws(r.workstream) || "no workstream"}</span>{r.cutoff && <><span>·</span><span>{r.origin === "jarvis" ? "noted" : "data to"} {r.cutoff}</span></>}
                  <span className="ml-auto font-mono text-[11.5px]">{r.id}</span>
                </div>
                <p className={`m-0 mt-1.5 text-[14.5px] leading-relaxed ${r.status !== "current" ? "text-mute line-through decoration-line-3" : "text-ink"}`}>{r.statement}</p>
                {(r.in_force || []).map((x) => <p key={x.id} className="m-0 mt-1.5 rounded-[8px] bg-ok-soft px-3 py-2 text-[14px] leading-relaxed text-ink"><b className="font-semibold text-ok">In force now:</b> {x.statement} <span className="font-mono text-[11.5px] text-mute">{x.id}</span></p>)}
                {(r.conflicts || []).map((x) => <p key={x.id} className="m-0 mt-1.5 rounded-[8px] bg-warn-soft px-3 py-2 text-[14px] leading-relaxed text-ink"><b className="font-semibold text-warn">Conflicts with:</b> {x.statement} <span className="font-mono text-[11.5px] text-mute">{x.id}</span></p>)}
                <div className="mt-1.5 text-[12.5px] text-mute">from {String(r.note).replace(/^jarvis:/, "Jarvis's own record, ")}</div>
              </div>
            ))}
          </div>
        )}
    </div>
  );
}

function OpenQuestions({ d }: { d: any }) {
  const by: Record<string, any[]> = {};
  d.open_questions.forEach((q) => (by[q.workstream || "no workstream"] = by[q.workstream || "no workstream"] || []).push(q));
  const tone = { high: "bad", medium: "warn", low: "mute" } as const;
  const groups = Object.entries(by).sort((a, b) => b[1].filter((q) => q.priority === "high").length - a[1].filter((q) => q.priority === "high").length || b[1].length - a[1].length);
  const c = d.open_counts || {};
  return (
    <div className="grid gap-4">
      <Note>{d.open_questions.length} questions were checked against the knowledge base and judged still open. {c.unclear || 0} more could not be judged from what is written, and {c["not triaged"] || 0} were noted since the last check; those are in Jarvis's records but not listed here.</Note>
      {groups.map(([k, qs]) => (
        <Panel key={k} title={ws(k)} n={qs.length} pad={false}>
          <Table cols={[["", "w-[84px]"], ["Open question"], ["Why it matters"], ["Noted", "w-[96px]"]]}>
            {qs.map((q) => (<tr key={q.id}><td><Chip tone={tone[q.priority] || "mute"}>{q.priority || "–"}</Chip></td><td className="text-ink">{C(q.statement)}</td><td className="text-[13px] text-mute">{cut(C(q.why), 200)}</td><td className="font-mono text-[12px] text-mute">{q.as_of}</td></tr>))}
          </Table>
        </Panel>
      ))}
    </div>
  );
}

function Promises({ d }: { d: any }) {
  const mine = (p) => WH.isMe(p.who);
  const owe = d.promises.filter(mine), owed = d.promises.filter((p) => !mine(p)), c = d.promise_counts || {};
  const list = (rows, col) => (
    <Table cols={[[col, "w-[170px]"], ["What"], ["Due", "w-[130px]"], ["Noted", "w-[96px]"]]}>
      {rows.map((p) => (<tr key={p.id}><td className="font-medium text-ink">{C(col === "To" ? p.to_whom : p.who) || "–"}</td><td>{C(String(p.statement).replace(/^.*? owes .*?: /, "").replace(/ \(due: .*\)$/, ""))}</td><td className="text-mute">{C(p.due) || "not said"}</td><td className="font-mono text-[12px] text-mute">{p.as_of}</td></tr>))}
    </Table>
  );
  return (
    <div className="grid gap-4">
      <Note>These promises were checked against the knowledge base and are still owed. {c.unclear || 0} more could not be judged, and {c["not triaged"] || 0} were noted since the last check.</Note>
      <Panel title="You owe" n={owe.length} pad={false}>{owe.length ? list(owe, "To") : <p className="m-0 px-5 py-4 text-[13.5px] text-mute">Nothing on record.</p>}</Panel>
      <Panel title="Owed to you" n={owed.length} pad={false}>{owed.length ? list(owed, "By") : <p className="m-0 px-5 py-4 text-[13.5px] text-mute">Nothing on record.</p>}</Panel>
    </div>
  );
}

function Conflicts({ d }: { d: any }) {
  return (
    <div className="grid gap-3">
      <Note tone="warn">{d.conflicts.length} sets of records answer the same question with different values, and neither the dates nor the source text say which holds. Each needs a recount or a question to someone. Until then both show as “in conflict” wherever they come up.</Note>
      {d.conflicts.map((g, i) => (
        <Panel key={i} title={`${ws(g.records[0]?.workstream)}${g.records.some((r) => r.workstream !== g.records[0]?.workstream) ? " and others" : ""}`} right={`${g.ids.length} records`}>
          <ul className="m-0 grid list-none gap-2 p-0">{g.records.map((r) => (<li key={r.id} className="text-[14px] leading-relaxed text-ink"><span className="mr-2 font-mono text-[11.5px] text-mute">{r.id}</span>{r.statement}<span className="ml-2 text-[12.5px] text-mute">({r.note})</span></li>))}</ul>
          <p className="m-0 mt-2.5 text-[13px] text-mute">{cut(String(g.why).replace(/^\(.*?\)\s*/, ""), 380)}</p>
        </Panel>
      ))}
    </div>
  );
}

// 13 Nov (the owner: "show the verdict there also"): the overall verdict across all trials (state/research_verdict.json).
const DECISION = { adopt: ["ok", "adopt as a check"], adopted: ["ok", "adopted"], no: ["mute", "don't adopt"], lead: ["warn", "lead to follow"], open: ["ion", "verdict pending"] };
function DecisionChip({ d }: { d: any }) {
  const [tone, label] = DECISION[d.call] || ["mute", d.call];
  return <Chip tone={tone as any}>{label}</Chip>;
}
function Verdict({ v }: { v: any }) {
  const block = (title, items) => items && items.length ? (
    <div className="grid gap-1.5">
      <div className="text-[12px] font-semibold uppercase tracking-[.06em] text-mute">{title}</div>
      <ul className="m-0 grid list-none gap-1.5 p-0 text-[14px] leading-relaxed">
        {items.map((t, i) => <li key={i} className="grid grid-cols-[14px_minmax(0,1fr)] gap-2"><span className="text-mute">–</span><span className="text-ink-2">{t}</span></li>)}
      </ul>
    </div>) : null;
  return (
    <Panel title="Overall verdict" right={<span>{v.by} · {when(v.at)}</span>}>
      <p className="m-0 mb-3 text-[15.5px] font-medium leading-relaxed text-ink">{v.headline}</p>
      <div className="grid gap-4">
        {block("Adopted as checks", v.adopted)}
        {block("Not adopted", v.not_adopted)}
        {block("Leads to follow", v.leads)}
        {block("Still running", v.open)}
      </div>
      {v.caveat && <p className="m-0 mt-3 text-[13px] text-mute">{v.caveat}</p>}
    </Panel>
  );
}

function Research() {
  const [d, reload] = useApi<any>("/api/research", 30000);
  const mark = async (id, verdict) => { const r = await WH.api("/api/research_mark", { id, verdict }); if (r && r.ok === false) WH.toast(r.error || "That did not work.", "err"); reload(); };
  if (!d) return <Panel pad={false}><Loading rows={4} /></Panel>;
  // 13 Nov (the owner: "Are these running?"): the chip says whether a step is running NOW; "in trial · 0 of 6" read as if it were
  const st = (f) => f.state === "tried" ? <Chip tone="ok">tried</Chip> : f.trial_state === "running" ? <Chip tone="ion"><i className="live-dot mr-1.5 inline-block size-[6px] rounded-full bg-ion align-middle" />running now · step {(f.steps_done || 0) + 1} of {f.budget}</Chip>
    : f.trial_state === "active" ? ((f.steps_done || 0) > 0 ? <Chip tone="ion">between steps · {f.steps_done} of {f.budget} done</Chip> : <Chip tone="mute">opened, not started · two run at a time</Chip>)
    : f.trial_state === "question" ? <Chip tone="warn">trial needs an answer</Chip> : <Chip tone="mute">waiting its turn</Chip>;
  return (
    <div className="grid gap-3">
      <Note tone="ion">Jarvis looks outside once a week for papers, techniques and tools that fit the project's open problems, then <b className="font-semibold">tries every finding by itself</b>: the same parts, window and measure as the method in use, fixed before any result is seen. The verdict says whether it can be used here, whether it improves anything (a worse or equal result is reported as plainly), and what adopting it would take. Trials read the project's data and code and change nothing.</Note>
      {d.verdict && <Verdict v={d.verdict} />}
      {!d.findings.length ? <Empty title="No findings yet" /> : d.findings.map((f) => (
        <Panel key={f.id} title={<a href={f.url} target="_blank" rel="noreferrer" className="text-ink no-underline hover:text-ion hover:underline">{f.title}</a>} right={<span className="flex items-center gap-2">{f.decision && <DecisionChip d={f.decision} />}{st(f)}<span>{f.kind} · {f.published || "undated"}</span></span>}>
          {f.decision?.note && <p className="m-0 mb-3 rounded-[10px] border border-line-2 px-3 py-2 text-[13.5px] leading-relaxed text-ink-2"><b className="font-semibold text-ink">Verdict: </b>{f.decision.note}</p>}
          {f.state === "tried" ? (
            <>{f.plain && <p className="m-0 mb-3 text-[15px] leading-relaxed text-ink">{f.plain}</p>}
            <dl className="m-0 grid grid-cols-[110px_minmax(0,1fr)] gap-x-4 gap-y-1.5 text-[14px]">
              <dt className="text-mute">Usable here</dt><dd className="m-0 font-medium text-ink">{f.usable || "–"}</dd>
              <dt className="text-mute">Improves</dt><dd className="m-0 font-medium text-ink">{f.improves || "–"}</dd>
              <dt className="text-mute">To adopt</dt><dd className="m-0 text-ink-2">{f.adopt || "–"}</dd>
              <dt className="text-mute">Confidence</dt><dd className="m-0 text-ink-2">{f.confidence || "–"}</dd>
              {f.followup && <><dt className="text-mute">Follow-up</dt><dd className="m-0 text-ink-2">{f.followup_result || (f.followup_state === "running" || f.followup_state === "active" ? `a second trial is testing the project's own outcome (${f.followup_steps || 0} steps done)` : "ended without a result")}</dd></>}
            </dl></>
          ) : (
            <dl className="m-0 grid grid-cols-[110px_minmax(0,1fr)] gap-x-4 gap-y-1.5 text-[13.5px]">
              <dt className="text-mute">It claims</dt><dd className="m-0 text-ink-2">{f.claim}</dd>
              <dt className="text-mute">Here</dt><dd className="m-0 text-ink-2">{f.applies}</dd>
              <dt className="text-mute">Caveat</dt><dd className="m-0 text-ink-2">{f.caveat || "–"}</dd>
            </dl>
          )}
          {f.steps && f.steps.some((s) => s.passes) && (
            <details className="mt-3 border-t border-line-2 pt-3" open={f.state !== "tried"}>
              <summary className="mb-1.5 cursor-pointer select-none text-[12px] font-semibold uppercase tracking-[.06em] text-mute hover:text-ink">{f.state === "tried" ? "The trial, step by step" : "Found so far"} · {f.steps.filter((s) => s.passes).length} of {f.steps.length} steps</summary>
              <ol className="m-0 grid list-none gap-1.5 p-0 text-[13px] leading-relaxed">
                {f.steps.filter((s) => s.passes).map((s, i) => (
                  <li key={i} className="grid grid-cols-[18px_minmax(0,1fr)] gap-2"><span className="tabular-nums text-mute">{i + 1}</span><span className="text-ink-2">{s.found}</span></li>
                ))}
                {f.steps.filter((s) => !s.passes).slice(0, 1).map((s, i) => (
                  <li key={"n" + i} className="grid grid-cols-[18px_minmax(0,1fr)] gap-2 text-mute"><span>›</span><span>Next: {s.do}</span></li>
                ))}
              </ol>
            </details>
          )}
          <div className="mt-3 flex flex-wrap items-center gap-2 text-[12.5px] text-mute">
            <span>For: {cut(f.problem, 110)}</span>
            <span className="ml-auto flex gap-1.5">
              {f.folder && <Button size="sm" variant="ghost" onClick={() => WH.openOutput(f.folder)}>Open the trial folder</Button>}
              {!f.investigation && <Button size="sm" onClick={() => mark(f.id, "worth")}>Try this first</Button>}
              {f.state !== "tried" && <Button size="sm" variant="ghost" onClick={() => mark(f.id, "not")}>Not useful</Button>}
            </span>
          </div>
        </Panel>
      ))}
    </div>
  );
}

function Health({ d }: { d: any }) {
  const pct = (a, b) => (b ? `${((100 * a) / b).toFixed(1)}%` : "–");
  const ex = d.exam || [];
  return (
    <div className="grid gap-4">
      <div className="grid grid-cols-4 gap-3 max-[900px]:grid-cols-2">
        {[["Records", d.records.toLocaleString("en-GB"), `${d.current.toLocaleString("en-GB")} current`], ["Replaced", d.superseded.toLocaleString("en-GB"), `${pct(d.with_pointer, d.superseded)} name what replaced them`],
          ["In conflict", d.conflicts.length, "sets not settled"], ["Last exam", ex.length ? `${Math.round(ex[ex.length - 1].score * 100)}%` : "–", ex.length ? `${ex[ex.length - 1].n} questions · ${when(ex[ex.length - 1].at)}` : ""]].map(([l, v, s]) => (
          <div key={l as string} className="rounded-[14px] border border-line bg-card px-4 py-3.5"><div className="text-[12.5px] font-medium text-mute">{l}</div><div className="mt-1 text-[26px] font-semibold leading-none tracking-[-.02em] tabular-nums text-ink">{v}</div><div className="mt-1.5 text-[12.5px] text-mute">{s}</div></div>))}
      </div>
      <Panel title="Current records by workstream" right={"ledger rebuilt " + when(d.refreshed)} pad={false}>
        <Table cols={[["Workstream"], ["Current records", "w-[160px] text-right"]]}>
          {d.workstreams.map((w) => (<tr key={w.name}><td className="text-ink">{ws(w.name)}</td><td className="text-right tabular-nums">{w.current.toLocaleString("en-GB")}</td></tr>))}
        </Table>
      </Panel>
      <Panel title="Exam history" pad={false}>
        <Table cols={[["When", "w-[170px]"], ["Questions", "w-[110px]"], ["Score", "w-[90px]"], ["How it was graded"]]}>
          {ex.slice().reverse().map((e, i) => (<tr key={i}><td className="font-mono text-[12px] text-mute">{when(e.at)}</td><td className="tabular-nums">{e.n}</td><td className="font-medium tabular-nums text-ink">{Math.round(e.score * 100)}%</td><td className="text-[13px] text-mute">{Object.entries(e.grades || {}).map(([k, v]) => `${v} ${k}`).join(", ")}</td></tr>))}
        </Table>
      </Panel>
    </div>
  );
}

export default function Brain({ sub }: { sub: string }) {
  useShell();
  const tab = (sub || "ask").split("/")[0];
  const [d] = useApi<any>("/api/kb", 120000);
  useEffect(() => { window.scrollTo({ top: 0 }); }, [tab]);
  const body = tab === "ask" ? <Ask /> : tab === "research" ? <Research /> : tab === "watching" ? <WatchingView /> : !d ? <Panel pad={false}><Loading rows={5} /></Panel>
    : tab === "questions" ? <OpenQuestions d={d} /> : tab === "promises" ? <Promises d={d} /> : tab === "conflicts" ? <Conflicts d={d} /> : <Health d={d} />;
  return (
    <div className="grid grid-cols-[minmax(0,1fr)] gap-5">
      <PageTitle title="Brain" art="/media/screens/brain.jpg" film="/media/screens/brain.mp4" sub="What Jarvis knows about the project and what is still open. Everything here is a lead with its source; a number that goes to anyone is checked against that source first." />
      <HashTabs base="brain" current={tab} tabs={[["ask", "Ask"], ["questions", "Open questions", d ? d.open_questions.length : ""], ["promises", "Promises", d ? d.promises.length : ""], ["conflicts", "Conflicts", d ? d.conflicts.length : ""],
        ["research", "Outside research"], ["watching", "Waiting for"], ["health", "Health"]]} />
      {body}
    </div>
  );
}
