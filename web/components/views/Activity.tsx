"use client";
/* Activity (new in the redesign, 12 Nov 2026): what Jarvis is doing, what it finished and with what result, and every
   outward action it took (the action ledger). Read-only: it shows the records, it starts nothing. */
import { useState } from "react";
import { WH } from "@/lib/wh";
import { useShell } from "@/lib/useShell";
import { Empty } from "@/components/ui/card";
import { useApi, Panel, Chip, Table, PageTitle, Loading, when, cut } from "@/components/ui/kit";

const KIND = { item: "Inbox item", initiative: "Own check", investigation: "Investigation", "review-fix": "Fix after review", roundup: "Inbox check", background: "Background" };
const day = (s) => { const d = new Date(s); return isNaN(+d) ? "" : d.toLocaleDateString("en-GB", { weekday: "long", day: "numeric", month: "short" }); };

export default function Activity() {
  useShell();
  const [hours, setHours] = useState(72);
  const [d] = useApi<any>(`/api/activity?hours=${hours}`, 20000);
  const open = (t) => { if (t.rid && t.aid) { WH.focusItem = t.rid + "|" + t.aid; WH.go("inbox/todo/" + encodeURIComponent(t.rid + "|" + t.aid)); } };
  const groups: Record<string, any[]> = {};
  ((d && d.tasks) || []).forEach((t) => { const k = day(t.ended || t.started); (groups[k] = groups[k] || []).push(t); });
  return (
    <div className="grid grid-cols-[minmax(0,1fr)] gap-6">
      <PageTitle title="Activity" art="/media/screens/activity.jpg" film="/media/screens/activity.mp4" sub="What Jarvis is doing, what it finished, and every action it took outside this Mac. Nothing here starts anything."
        right={<div className="flex gap-1">{[[24, "24 h"], [72, "3 days"], [168, "7 days"]].map(([h, l]) => (
          <button key={h} type="button" onClick={() => setHours(+h)} className={`rounded-full border px-3 py-1 text-[13px] font-medium ${hours === h ? "border-ion bg-ion-soft text-ion" : "border-line bg-card text-mute hover:text-ink"}`}>{l}</button>))}</div>} />

      <Panel title="Running now" n={d ? d.running.length : undefined} pad={false}>
        {!d ? <Loading rows={2} /> : !d.running.length ? <p className="m-0 px-5 py-4 text-[13.5px] text-mute">Nothing is running.</p> : (
          <Table cols={[["What"], ["Kind", "w-[140px]"], ["State", "w-[100px]"], ["Since", "w-[110px]"]]}>
            {d.running.map((t) => (<tr key={t.id}><td className="font-medium text-ink">{cut(t.title, 140)}</td><td className="text-mute">{KIND[t.kind] || t.kind}</td>
              <td><Chip tone={t.state === "running" ? "ion" : "mute"}>{t.state}</Chip></td><td className="font-mono text-[12px] text-mute">{when(t.started)}</td></tr>))}
          </Table>
        )}
      </Panel>

      <Panel title="Finished" n={d ? d.tasks.length : undefined} right={d ? `${d.counts.done} done, ${d.counts.failed} failed or stopped` : ""} pad={false}>
        {!d ? <Loading /> : !d.tasks.length ? <Empty bare title="Nothing finished in this period" /> : Object.entries(groups).map(([k, ts]) => (
          <div key={k}>
            <div className="border-b border-line-2 bg-card-2 px-5 py-1.5 text-[12px] font-semibold text-mute">{k}</div>
            <Table cols={[["When", "w-[70px]"], ["What"], ["Kind", "w-[130px]"], ["Result", "w-[110px]"]]}>
              {ts.map((t) => (
                <tr key={t.id} className={t.rid && t.aid ? "cursor-pointer hover:bg-card-2" : ""} onClick={() => open(t)}>
                  <td className="font-mono text-[12px] text-mute">{new Date(t.ended || t.started).toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit" })}</td>
                  <td><div className="font-medium text-ink">{cut(t.title, 130)}</div>{t.summary && <div className="mt-0.5 text-[13px] text-mute">{cut(t.summary, 220)}</div>}</td>
                  <td className="text-mute">{KIND[t.kind] || t.kind}{t.origin === "owner" || t.lane === "asked" ? " · you asked" : ""}</td>
                  <td>{t.state === "done" ? (t.needs_you ? <Chip tone="warn">needs you</Chip> : <Chip tone="ok">done</Chip>) : <Chip tone="bad">{t.state}</Chip>}</td>
                </tr>
              ))}
            </Table>
          </div>
        ))}
      </Panel>

      <Panel title="Actions outside this Mac" n={d ? d.actions.length : undefined} right="sends, posts and writes, each with its tier and what happened" pad={false}>
        {!d ? <Loading /> : !d.actions.length ? <p className="m-0 px-5 py-4 text-[13.5px] text-mute">No outward action in this period.</p> : (
          <Table cols={[["When", "w-[120px]"], ["Action"], ["Tier", "w-[60px]"], ["Asked by", "w-[110px]"], ["State", "w-[100px]"], ["Why or note"]]}>
            {d.actions.map((a) => (<tr key={a.id}><td className="font-mono text-[12px] text-mute">{when(a.at)}</td><td className="font-medium text-ink">{String(a.type || "").replace(/_/g, " ")}</td>
              <td className="font-mono text-[12px]">{a.tier}</td><td className="text-mute">{a.origin}</td>
              <td><Chip tone={a.state === "done" ? "ok" : a.state === "refused" || a.state === "failed" ? "bad" : "mute"}>{a.state}</Chip></td><td className="text-mute">{cut(a.why, 120)}</td></tr>))}
          </Table>
        )}
      </Panel>
    </div>
  );
}
