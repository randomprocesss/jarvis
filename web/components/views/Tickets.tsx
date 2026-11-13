"use client";
/* Jira tickets — simple: Jarvis proposes tickets for analysis/development work; check them; create them in one go.
   13 Nov: compact rows (title, who asked, how old) that open to edit, and a summary strip; the edit form is unchanged. */
import { useEffect, useReducer, useRef, useState } from "react";
import { cn } from "@/lib/utils";
import { AnimatePresence, motion } from "framer-motion";
import { Info, Send, X, Clock, Ticket as TicketIcon, ExternalLink, ChevronDown, Sparkles, Link2 } from "lucide-react";
import { WH, useWH } from "@/lib/wh";
import { useShell } from "@/lib/useShell";
import { Card, Empty, SkeletonList } from "@/components/ui/card";
import { Avatar, PageHeader } from "@/components/ui/bits";
import { Button, PillButton } from "@/components/ui/button";
import { Tag } from "@/components/ui/tag";
import { Textarea, Input } from "@/components/ui/textarea";
import { itemMotion } from "@/components/motion";

const TYPES = ["Task", "Story", "Bug", "Sub-task"];
const C = (s) => WH.clean(s);

// the day a proposal was made, from its id (t-YYYY-MM-DD-…) or its round-up id
const born = (t) => { const m = String(t.id || "").match(/(\d{4}-\d{2}-\d{2})/) || String((t.source || {}).rid || "").match(/(\d{4}-\d{2}-\d{2})/); return m ? new Date(m[1] + "T12:00:00") : null; };
const ageDays = (t) => { const d = born(t); return d ? Math.max(0, Math.floor((Date.now() - +d) / 86400000)) : null; };
const fromJarvis = (t) => /jarvis/i.test(String((t.source || {}).who || ""));

function Note({ children }) {
  return <div className="flex max-w-[92ch] items-start gap-2 rounded-[10px] bg-info-soft px-3 py-2.5 text-[13.5px] text-ink-2"><Info size={13} strokeWidth={1.8} className="mt-0.5 flex-none text-info" /><span>{children}</span></div>;
}

export default function Tickets() {
  useWH(["tickets", "jira"]);
  const [, force] = useReducer((x) => x + 1, 0);
  const unselected = useRef(new Set<string>()).current, edits = useRef(new Map<string, any>()).current;   // id -> {field: value}
  const timer = useRef<any>(null), createBtn = useRef<HTMLButtonElement>(null);
  const [creating, setCreating] = useState(false);
  const [open, setOpen] = useState<string | null>(null);   // the one proposal opened for editing

  const cur = (t) => ({ ...t, ...(edits.get(t.id) || {}) });
  const save = async () => {
    timer.current = null; if (!edits.size) return;
    const snap = new Map([...edits].map(([k, v]) => [k, { ...v }]));
    const items = WH.clone(WH.store.tickets.items).map((t) => (snap.has(t.id) ? { ...t, ...snap.get(t.id), updated: new Date().toISOString() } : t));
    const ok = await WH.saveTickets(items);
    // Keep anything typed while the save was in flight.
    if (ok) snap.forEach((v, id) => { const now = edits.get(id); if (now && Object.keys(now).every((f) => now[f] === v[f])) edits.delete(id); });
  };
  const queue = (id, field, value) => { edits.set(id, { ...(edits.get(id) || {}), [field]: value }); force(); clearTimeout(timer.current); timer.current = setTimeout(save, 700); };
  const setStatus = async (id, patch) => { await save(); const items = WH.clone(WH.store.tickets.items).map((t) => (t.id === id ? { ...t, ...patch, updated: new Date().toISOString() } : t)); await WH.saveTickets(items); };

  useShell(() => [{ l: "Create the selected tickets in Jira", k: "jira create", ic: "send", run: () => createBtn.current?.click() }], save);
  useEffect(() => { WH.loadTickets(); return () => { if (timer.current) { clearTimeout(timer.current); save(); } }; }, []);   // eslint-disable-line react-hooks/exhaustive-deps

  async function create() {
    await save();
    const ids = WH.store.tickets.items.filter((t) => (t.status === "draft" || t.status === "ready") && !unselected.has(t.id)).map((t) => t.id);
    if (!ids.length) return;
    if (!(WH.jira.status && WH.jira.status.ok)) {
      const list = WH.store.tickets.items.filter((t) => ids.includes(t.id)).map((t) => `• ${t.summary}${t.reuse_key ? `  (logged under ${t.reuse_key})` : ""}`).join("\n");
      if (!(await WH.ask(`Create ${ids.length} Jira ${ids.length === 1 ? "ticket" : "tickets"}?`, list + `\n\nJarvis creates them in ${WH.me.projects[0] || "your project"} now, assigned to you. The keys appear here.`, "Approve"))) return;
      const at = new Date().toISOString();
      await WH.saveTickets(WH.clone(WH.store.tickets.items).map((t) => (ids.includes(t.id) ? { ...t, status: "ready", approved_at: at } : t)));
      try { await WH.api("/api/run_now", { what: "tickets" }); } catch {}            // he pressed Create: it happens now, not at the next update
      WH.toast(`Approved. Jarvis is creating ${ids.length === 1 ? "it" : "them"} now; the ${ids.length === 1 ? "key appears" : "keys appear"} here.`, "ok", 4500);
      return;
    }
    await WH.saveTickets(WH.clone(WH.store.tickets.items).map((t) => (ids.includes(t.id) ? { ...t, status: "ready" } : t)));
    setCreating(true);
    const res = await WH.confirmRun("/api/jira/create_tickets", { ids }, {
      title: `Create ${ids.length} Jira ${ids.length === 1 ? "ticket" : "tickets"}?`, yes: "Create in Jira",
      describe: (d) => (d.plan || []).map((p) => `• ${p.summary}${p.reuse_key ? `  (reuses ${p.reuse_key})` : ""}${p.type && p.type !== "Task" ? `  [${p.type}${p.parent ? " under " + p.parent : ""}]` : ""}`).join("\n") + "\n\nEach is assigned to you in project " + (WH.me.projects[0] || "of yours") + ".",
      done: (r) => { const ok = (r.result || []).filter((x) => x.ok), bad = (r.result || []).filter((x) => !x.ok); return `Created ${ok.map((x) => x.key).join(", ") || "none"}${bad.length ? ` · ${bad.length} failed (see Activity log)` : ""}.`; },
    });
    setCreating(false);
    await WH.loadTickets();
    if (!res) await WH.saveTickets(WH.clone(WH.store.tickets.items).map((t) => (ids.includes(t.id) && t.status === "ready" ? { ...t, status: "draft" } : t)));
  }

  const connected = !!(WH.jira.status && WH.jira.status.ok);
  const all = WH.store.tickets.items.map(cur);
  const queued = all.filter((t) => t.status === "ready" && t.approved_at && !t.key);
  const todo = all.filter((t) => (t.status === "draft" || t.status === "ready") && !queued.includes(t)), made = all.filter((t) => t.status === "created"), gone = all.filter((t) => t.status === "dismissed");
  const picked = todo.filter((t) => !unselected.has(t.id));

  return (
    <div className="grid grid-cols-[minmax(0,1fr)] gap-7">
      <PageHeader kicker="Jira tickets" title="Tickets to create" art="/media/screens/tickets.jpg" film="/media/screens/tickets.mp4" whole sub={<p className="m-0">Jarvis proposes a ticket for each analysis or development task it finds. Check them, then create them in Jira in one go. Questions get replies, not tickets.</p>} />
      {!WH.store.ticketsLoaded ? <SkeletonList rows={3} /> : (
        <div className="grid grid-cols-[minmax(0,1fr)] gap-3.5">
          {todo.length ? (<>
            {(() => {
              const ages = todo.map(ageDays).filter((x) => x !== null) as number[], oldest = ages.length ? Math.max(...ages) : null;
              const mine = todo.filter(fromJarvis).length, people = todo.length - mine, stale = ages.filter((a) => a > 21).length;
              return (
                <div className="flex flex-wrap items-center gap-2 text-[13px] text-ink-2" data-testid="tickets-summary">
                  <span className="rounded-full border border-line bg-card px-3 py-1"><b className="font-semibold text-ink">{todo.length}</b> proposed</span>
                  <span className="rounded-full border border-line bg-card px-3 py-1">{people} from people's asks</span>
                  {mine > 0 && <span className="rounded-full border border-line bg-card px-3 py-1">{mine} noticed by Jarvis</span>}
                  {stale > 0 && <span className="rounded-full border border-[color:var(--warn)]/40 bg-warn-soft px-3 py-1 text-ink">{stale} older than 3 weeks{oldest !== null ? ` · oldest ${oldest} days` : ""}</span>}
                  <span className="flex-1" />
                  <button type="button" className="rounded-full px-2.5 py-1 font-medium text-ion hover:bg-line-2"
                    onClick={() => { if (unselected.size) unselected.clear(); else todo.forEach((t) => unselected.add(t.id)); force(); }}>
                    {unselected.size ? "Select all" : "Select none"}
                  </button>
                </div>
              );
            })()}
            <div className="grid gap-2" data-list="tickets">
              <AnimatePresence initial={false} mode="popLayout">
                {todo.map((t) => {
                  const src = t.source || {}, type = t.type || "Task", isOpen = open === t.id, age = ageDays(t), off = unselected.has(t.id);
                  return (
                    <motion.article key={t.id} {...itemMotion} data-id={t.id} className={cn("grid grid-cols-[24px_minmax(0,1fr)] gap-3.5 rounded-[14px] border bg-card px-5 shadow-card transition-[box-shadow,border-color,opacity] duration-200 [overflow-wrap:anywhere] hover:border-line-3 hover:shadow-card-hover max-[560px]:px-4", isOpen ? "border-ion/40 py-[18px]" : "border-line py-3.5", off && !isOpen && "opacity-60")}>
                      <label className={cn("cb sq", isOpen ? "mt-2" : "mt-0.5")}><input type="checkbox" checked={!off} onChange={(e) => { if (e.target.checked) unselected.delete(t.id); else unselected.add(t.id); force(); }} aria-label="Include this ticket" /><span /></label>
                      {!isOpen ? (
                        <button type="button" onClick={() => setOpen(t.id)} className="grid min-w-0 grid-cols-[minmax(0,1fr)_auto] items-center gap-3 text-left" aria-expanded={false} title="Open to edit">
                          <span className="min-w-0">
                            <span className="block font-display text-[15px] font-semibold leading-snug text-ink [display:-webkit-box] [-webkit-box-orient:vertical] [-webkit-line-clamp:2] overflow-hidden">{C(t.summary || "Untitled")}</span>
                            <span className="mt-1 flex min-w-0 flex-wrap items-center gap-x-2 gap-y-1 text-[12.5px] text-mute">
                              {fromJarvis(t) ? <Sparkles size={13} strokeWidth={1.8} className="text-ion" aria-hidden /> : <Avatar name={src.who || "Inbox"} size={18} />}
                              <span className="truncate">{C(src.who || "your inbox")}{src.where ? " · " + C(src.where) : ""}{src.when ? " · " + C(src.when) : ""}</span>
                              {t.reuse_key && <span className="inline-flex items-center gap-1 rounded-full bg-info-soft px-2 py-0.5 text-[11.5px] text-ink-2"><Link2 size={11} strokeWidth={2} />reuses {t.reuse_key}</span>}
                              {t.claude_note && !t.reuse_key && <span className="inline-flex items-center gap-1 rounded-full bg-info-soft px-2 py-0.5 text-[11.5px] text-ink-2"><Info size={11} strokeWidth={2} />note</span>}
                            </span>
                          </span>
                          <span className="flex flex-none items-center gap-2">
                            <span className="rounded-md border border-line px-2 py-0.5 text-[12px] font-medium text-ink-2 max-[560px]:hidden">{type}</span>
                            {age !== null && <span className={cn("rounded-md px-2 py-0.5 font-mono text-[11.5px]", age > 21 ? "bg-warn-soft text-ink" : "bg-line-2 text-mute")} title={`Proposed ${age} days ago`}>{age === 0 ? "today" : `${age}d`}</span>}
                            <ChevronDown size={16} strokeWidth={1.8} className="text-mute" aria-hidden />
                          </span>
                        </button>
                      ) : (
                      <div className="grid min-w-0 gap-2.5">
                        <Input className="rounded-[10px] font-display text-[15.5px] font-semibold" value={t.summary || ""} onChange={(e) => queue(t.id, "summary", e.target.value)} aria-label="Summary" placeholder="What needs doing" />
                        <Textarea className="min-h-16 rounded-[10px] text-[14px] text-ink-2" value={t.description || ""} onChange={(e) => queue(t.id, "description", e.target.value)} aria-label="Description" />
                        {t.reuse_key && <Note>An open ticket of yours, {t.reuse_key}, already covers this. Creating will reuse it. <PillButton onClick={() => setStatus(t.id, { reuse_key: null })}>Make a new ticket instead</PillButton></Note>}
                        {t.claude_note && !t.reuse_key && <Note>{C(t.claude_note)}</Note>}
                        <div className="flex flex-wrap items-center gap-2.5 text-[13px] text-mute">
                          <select className="sel rounded-[8px] border border-line-3 bg-card py-1.5 pl-2.5 text-[13px] font-medium text-ink" value={type} aria-label="Type"
                            onChange={(e) => { queue(t.id, "type", e.target.value); save().then(force); }}>
                            {TYPES.map((x) => <option key={x}>{x}</option>)}
                          </select>
                          {type === "Sub-task" && <Input className="w-[120px] rounded-md py-1.5 font-mono text-[13px]" value={t.parent || ""} onChange={(e) => queue(t.id, "parent", e.target.value)} placeholder={"Parent " + (WH.me.projects[0] || "KEY") + "-…"} aria-label="Parent ticket" />}
                          <Avatar name={src.who || "Inbox"} size={22} /><span className="-ml-1">From {C(src.who || "your inbox")}{src.where ? " · " + C(src.where) : ""}{src.when ? " · " + C(src.when) : ""}</span>
                          <span className="flex-1" />
                          <Button size="sm" variant="ghost" onClick={() => setStatus(t.id, { status: "dismissed" })}><X size={14} strokeWidth={1.8} />Remove</Button>
                          <Button size="sm" variant="default" onClick={() => { save(); setOpen(null); }}>Done</Button>
                        </div>
                      </div>
                      )}
                    </motion.article>
                  );
                })}
              </AnimatePresence>
            </div>
            <Card className="sticky bottom-4 z-10 flex flex-wrap items-center justify-between gap-3 bg-card/85 px-5 py-4 shadow-lg backdrop-blur-md backdrop-saturate-150">
              <span className="text-[14px] text-ink-2"><b className="font-display font-semibold text-ink">{picked.length} of {todo.length}</b> selected · assigned to you{WH.me.projects[0] ? " in " + WH.me.projects[0] : ""}{connected ? "" : " · Jarvis creates them as soon as you press Create"}</span>
              <Button ref={createBtn} variant="primary" size="lg" disabled={!picked.length} busy={creating} onClick={create}><Send size={16} strokeWidth={1.8} />Create {picked.length} {picked.length === 1 ? "ticket" : "tickets"}</Button>
            </Card>
          </>) : <Empty icon={TicketIcon} title="No tickets to create">When Jarvis finds analysis or development work in your inbox or meetings, it proposes a ticket here.</Empty>}

          {queued.length > 0 && (<>
            <h2 className="mb-0 mt-4 font-display text-[19px] font-semibold tracking-[-.01em]">Approved · Jarvis is creating these now</h2>
            <Card className="grid grid-cols-[minmax(0,1fr)] overflow-hidden">
              {queued.map((t) => (
                <div key={t.id} className="flex items-baseline gap-3 border-t border-line px-5 py-3 text-[14.5px] first:border-t-0">
                  <span className="flex-none text-ion"><Clock size={14} strokeWidth={1.8} /></span>
                  <span className="min-w-0 flex-1 truncate">{C(t.summary || "")}</span>
                  <PillButton onClick={() => setStatus(t.id, { status: "draft", approved_at: null })}>Undo</PillButton>
                </div>
              ))}
            </Card>
          </>)}
          {made.length > 0 && (<>
            <h2 className="mb-0 mt-4 font-display text-[19px] font-semibold tracking-[-.01em]">Created</h2>
            <Card className="grid grid-cols-[minmax(0,1fr)] overflow-hidden" data-list="created">
              {made.slice().reverse().map((t) => (
                <a key={t.id} href={WH.ticketUrl(t.key) || undefined} target="_blank" rel="noopener" className="flex items-baseline gap-3 border-t border-line px-5 py-3 text-[14.5px] text-inherit no-underline first:border-t-0 hover:bg-card-2">
                  <span className="flex-none font-mono text-[13.5px] font-semibold text-ion">{t.key}</span>
                  <span className="min-w-0 flex-1 truncate">{C(t.summary || "")}</span>
                  {t.reused && <Tag tone="mute">reused</Tag>}
                  <ExternalLink size={13} strokeWidth={1.8} className="flex-none self-center text-mute" aria-hidden />
                </a>
              ))}
            </Card>
          </>)}
          {gone.length > 0 && (
            <details>
              <summary className="list-none px-1 py-3.5 text-[14px] font-semibold text-mute hover:text-ion">Removed ({gone.length}) ›</summary>
              <Card className="grid grid-cols-[minmax(0,1fr)] overflow-hidden">
                {gone.map((t) => (
                  <div key={t.id} className="flex items-baseline gap-3 border-t border-line px-5 py-3 text-[14.5px] first:border-t-0">
                    <span className="min-w-0 flex-1 truncate">{C(t.summary || "")}</span>
                    <PillButton onClick={() => setStatus(t.id, { status: "draft" })}>Restore</PillButton>
                  </div>
                ))}
              </Card>
            </details>
          )}
        </div>
      )}
    </div>
  );
}
