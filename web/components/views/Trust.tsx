"use client";
/* Trust (new in the redesign, 12 Nov 2026): how far Jarvis may act by itself and what that rests on. The ladder per kind
   of action (its level today, your ceiling, the evidence), the scorecard, the plan, what the gate said about recent
   replies, and what Jarvis has learned from you. Levels are shown, not changed here: nothing acts on them yet. */
import { useEffect, useState } from "react";
import { WH } from "@/lib/wh";
import { useShell } from "@/lib/useShell";
import { Button } from "@/components/ui/button";
import { useApi, Panel, Chip, Table, PageTitle, HashTabs, Loading, Note, when, cut } from "@/components/ui/kit";
import LearnedView from "@/components/views/Learned";

const LV = { L0: "off", L1: "drafts only", L2: "you approve each", L3: "holds, then sends", L4: "acts, you can undo", L5: "acts, audited" };

/** Replies waiting out their 10 minutes before they go out by themselves, with a Stop that always wins. */
export function HeldList({ rows, reload, compact }: { rows: any[]; reload: () => void; compact?: boolean }) {
  const [, tick] = useState(0);
  useEffect(() => { const t = setInterval(() => tick((x) => x + 1), 5000); return () => clearInterval(t); }, []);
  const live = rows.filter((e) => e.state === "held"), past = rows.filter((e) => e.state !== "held").slice(0, compact ? 0 : 8);
  const stop = async (id) => { const r = await WH.api("/api/held_stop", { id }); if (r && r.ok) WH.toast("Stopped. It will not be sent, and this kind of action is back to your approval.", "ok", 5000); else WH.toast((r && (r.why || r.error)) || "That did not work.", "err"); reload(); };
  const left = (e) => { const s = Math.max(0, Math.round((+new Date(e.send_at) - Date.now()) / 1000)); return s >= 60 ? `${Math.ceil(s / 60)} min` : `${s} s`; };
  const tone = { sent: "ok", stopped: "warn", refused: "warn", cancelled: "warn", failed: "bad", unclear: "bad", gone: "mute" } as const;
  if (!live.length && !past.length) return compact ? null : <p className="m-0 px-5 py-4 text-[13.5px] text-mute">Nothing is waiting to go out, and nothing has gone out by itself.</p>;
  return (
    <div className="grid gap-2 px-5 py-4">
      {live.map((e) => (
        <div key={e.id} className="flex flex-wrap items-center gap-3 rounded-[10px] border border-warn/35 bg-warn-soft px-3.5 py-2.5">
          <div className="min-w-0 flex-1"><b className="block text-[14px] font-semibold text-ink">To {e.to || e.where || "someone"}, in {left(e)}</b><div className="truncate text-[13px] text-ink-2">{e.first_line}</div></div>
          <Button size="sm" variant="danger" onClick={() => stop(e.id)}>Stop</Button>
        </div>
      ))}
      {past.map((e) => (
        <div key={e.id} className="flex flex-wrap items-center gap-3 text-[13px] text-ink-2">
          <Chip tone={tone[e.state] || "mute"}>{e.state}</Chip><span className="min-w-0 flex-1 truncate">To {e.to || e.where || "someone"}: {e.first_line}</span>
          <span className="text-mute">{e.why ? cut(e.why, 70) + " · " : ""}{when(e.held_at)}</span>
        </div>
      ))}
    </div>
  );
}

function Levels({ d, reload }: { d: any; reload: () => void }) {
  const native = typeof window !== "undefined" && !!(window as any).jarvisApp?.native;
  const raise = async (c) => {
    if (!(await WH.ask(`Let Jarvis send “${c.label}” by itself?`, `From now on a reply of this kind that passes every check is held for 10 minutes and then sent, unless you stop it. One edit or stop puts it back to your approval. ${c.evidence}`, "Yes, hold then send"))) return;
    const r = await WH.api("/api/ladder_raise", { cls: c.cls });
    if (r && r.ok) WH.toast(`${c.label}: now ${r.level}.`, "ok", 5000); else WH.toast((r && (r.why || r.error)) || "That did not work.", "err", 6500);
    reload();
  };
  const lower = async (c) => { const r = await WH.api("/api/ladder_lower", { cls: c.cls }); if (r && r.ok) WH.toast(`${c.label}: back to ${r.level}.`, "ok"); else WH.toast((r && (r.why || r.error)) || "That did not work.", "err"); reload(); };
  return (
    <div className="grid gap-4">
      <Note tone={d.acting ? "warn" : "ion"}><b className="font-semibold">{d.acting ? "Some replies go out by themselves." : "Nothing goes out by itself today."}</b> {d.acting_note} Your ceilings: replies to your leads at most “holds, then sends”; a customer always needs your tap; approvals on this Mac only.</Note>
      <Panel title="Going out soon, and what went out by itself" n={(d.held || []).filter((e) => e.state === "held").length} pad={false}>
        <HeldList rows={d.held || []} reload={reload} />
      </Panel>
      <Panel title="What Jarvis may do, by kind of action" right={d.ladder_at ? "counted " + when(d.ladder_at) : ""} pad={false}>
        <Table cols={[["Kind of action"], ["Today", "w-[150px]"], ["Your ceiling", "w-[150px]"], ["As drafted, in a row", "w-[140px]"], ["Evidence"], ["Your switch", "w-[190px]"]]}>
          {d.ladder.map((c) => (
            <tr key={c.cls}>
              <td className="font-medium text-ink">{c.label}<div className="mt-0.5 text-[12.5px] font-normal text-mute">If it goes wrong: {c.undo}</div></td>
              <td><Chip tone={c.level >= "L3" ? "warn" : "mute"}>{c.level}</Chip> <span className="text-[12.5px] text-mute">{LV[c.level]}</span></td>
              <td><Chip tone={c.ceiling === c.level ? "mute" : "ion"}>{c.ceiling}</Chip> <span className="text-[12.5px] text-mute">{LV[c.ceiling]}</span></td>
              <td>{c.need ? <><div className="h-1.5 w-[110px] overflow-hidden rounded-full bg-line-2"><div className="h-full rounded-full bg-ion" style={{ width: `${Math.min(100, (100 * (c.run || 0)) / c.need)}%` }} /></div><div className="mt-1 text-[12.5px] tabular-nums text-mute">{c.run || 0} of {c.need}</div></> : <span className="text-mute">–</span>}</td>
              <td className="text-[13px] text-mute">{c.evidence}</td>
              <td>
                {c.can_lower ? <Button size="sm" onClick={() => lower(c)}>Back to my approval</Button>
                  : c.can_raise ? <Button size="sm" variant="primary" disabled={!native} title={native ? "" : "Only in the Jarvis app window"} onClick={() => raise(c)}>Let it hold, then send</Button>
                  : <span className="text-[12.5px] text-mute">{c.ceiling <= "L2" ? "always your tap" : c.release_path === false ? "no automatic path for this kind yet" : `not earned yet (${c.run || 0} of ${c.need || 20})`}</span>}
              </td>
            </tr>
          ))}
        </Table>
      </Panel>
      <Panel title="What the gate said about recent replies" n={d.gate_log.length} right={`advisory · proof gate: ${d.proof_mode}`} pad={false}>
        {!d.gate_log.length ? <p className="m-0 px-5 py-4 text-[13.5px] text-mute">No reply judged yet.</p> : (
          <Table cols={[["When", "w-[120px]"], ["Kind"], ["Level", "w-[70px]"], ["Would go out", "w-[110px]"], ["Checks that failed"]]}>
            {d.gate_log.slice(0, 15).map((g, i) => (<tr key={i}><td className="font-mono text-[12px] text-mute">{when(g.at)}</td><td>{String(g.cls || "").replace(/_/g, " ")}</td><td className="font-mono text-[12px]">{g.level}</td>
              <td><Chip tone={g.allow ? "ok" : "warn"}>{g.allow ? "yes" : "no"}</Chip></td><td className="text-[13px] text-mute">{cut(String(g.failed || "").replace(/[\[\]"]/g, ""), 160) || "none"}</td></tr>))}
          </Table>
        )}
      </Panel>
    </div>
  );
}

function Scorecard({ d }: { d: any }) {
  const ok = d.scorecard.filter((m) => m.ok).length;
  return (
    <Panel title="Scorecard" right={`${ok} of ${d.scorecard.length} at target · counted ${when(d.scorecard_at)}`} pad={false}>
      <Table cols={[["Measure"], ["Now", "w-[230px]"], ["Target", "w-[190px]"], ["", "w-[90px]"], ["Counted from"]]}>
        {d.scorecard.map((m) => (<tr key={m.key}><td className="font-medium text-ink">{m.label}</td><td className="tabular-nums">{m.show}</td><td className="text-mute">{m.target}</td>
          <td>{m.ok == null ? <Chip tone="mute">no target</Chip> : m.ok ? <Chip tone="ok">at target</Chip> : <Chip tone="warn">not yet</Chip>}</td><td className="text-[12.5px] text-mute">{m.src}</td></tr>))}
      </Table>
    </Panel>
  );
}

function Plan({ d }: { d: any }) {
  const tone = { done: "ok", running: "ion", waiting: "mute", blocked: "warn" } as const;
  return (
    <Panel title="The autonomy plan" right={`${d.plan.filter((p) => p.state === "done").length} of ${d.plan.length} done`} pad={false}>
      <Table cols={[["", "w-[56px]"], ["Work item"], ["State", "w-[100px]"], ["Where it stands"]]}>
        {d.plan.map((p) => (<tr key={p.id}><td className="font-mono text-[12px] text-mute">{p.id}</td><td className="font-medium text-ink">{p.name || p.title}</td>
          <td><Chip tone={tone[p.state] || "mute"}>{p.state}</Chip></td><td className="text-[13px] text-mute">{p.detail}</td></tr>))}
      </Table>
    </Panel>
  );
}

export default function Trust({ sub }: { sub: string }) {
  useShell();
  const tab = (sub || "levels").split("/")[0], rest = (sub || "").split("/").slice(1).join("/");
  const [d, reload] = useApi<any>("/api/autonomy", 30000);
  return (
    <div className="grid grid-cols-[minmax(0,1fr)] gap-5">
      <PageTitle title="Trust" art="/media/screens/trust.jpg" film="/media/screens/trust.mp4" sub="How far Jarvis may act by itself, and what that rests on: the record of what you sent as drafted, the checks every reply must pass, and the measures it is held to." />
      <HashTabs base="trust" current={tab} tabs={[["levels", "Levels", d ? d.ladder.length : ""], ["scorecard", "Scorecard", d ? `${d.scorecard.filter((m) => m.ok).length}/${d.scorecard.length}` : ""], ["plan", "Plan", d ? `${d.plan.filter((p) => p.state === "done").length}/${d.plan.length}` : ""], ["learned", "Learned from you"]]} />
      {tab === "learned" ? <LearnedView sub={rest} /> : !d ? <Panel pad={false}><Loading rows={5} /></Panel> : tab === "scorecard" ? <Scorecard d={d} /> : tab === "plan" ? <Plan d={d} /> : <Levels d={d} reload={reload} />}
    </div>
  );
}
