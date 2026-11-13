"use client";
/* Inbox — list + reading pane: what is waiting on you on the left, the selected item with Claude's work on the right,
   one button to send it yourself. #/inbox/todo/<item> selects an item; j/k move through the list. Phones push list -> detail. */
import { useEffect, useReducer, useRef, useState } from "react";
import { AnimatePresence, motion } from "framer-motion";
import { Check, Clock, Info, Sparkles, Hourglass, Send, Copy, FileText, Ticket, ArrowLeft, Search, Coffee, Inbox as InboxIcon, Users, MousePointerClick, Link2, Play } from "lucide-react";
import { WH, useWH } from "@/lib/wh";
import { useShell, useTick } from "@/lib/useShell";
import { cn } from "@/lib/utils";
import { Card, Empty, SkeletonList } from "@/components/ui/card";
import { Button, PillButton } from "@/components/ui/button";
import { Tag } from "@/components/ui/tag";
import { Textarea } from "@/components/ui/textarea";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Avatar, SrcBadge, SrcIcon, Disclosure, PageHeader, RoundCheck } from "@/components/ui/bits";
import { itemMotion } from "@/components/motion";
import { registerScreen } from "@/lib/jarvis";

const TABS: [string, string][] = [["todo", "To do"], ["waiting", "Waiting on others"], ["fyi", "FYI"]];
const valid = (s) => TABS.some((t) => t[0] === s);
const C = (s) => WH.clean(s);
const ic = (I, s = 14) => <I size={s} strokeWidth={1.8} aria-hidden />;
const WIDE = "(min-width: 1180px)";

// What each point's status looks like. `state` comes from Claude's work file; ticks and approvals override it.
const ST: Record<string, [string, "ok" | "ion" | "warn" | "mute", any]> = {
  done: ["Done by Claude", "ok", Check], you: ["You answered", "ok", Check], manual: ["Marked done", "mute", Check],
  ok: ["Needs your OK", "ion", Clock], running: ["Approved · Claude is on it", "ion", Clock],
  decide: ["Needs your decision", "warn", Info], needs_you: ["Needs you", "warn", Info],
  drafted: ["Answered in the draft", "ion", Sparkles], working: ["Running now", "ion", Clock], queued: ["Queued for Claude", "ion", Clock], checked: ["Claude checked it", "ion", Sparkles], open: ["Not started yet", "mute", null],
};
const GROUP = { done: "done", you: "done", manual: "done", ok: "ok", running: "ok", decide: "need", needs_you: "need", drafted: "draft", checked: "draft", working: "draft", queued: "draft", open: "open" };
const WHERE = { slack: "Slack", email: "Email", teams: "Teams", calendar: "Calendar", jira: "Jira", confluence: "Confluence", sharepoint: "SharePoint",
  claude_sessions: "Your Claude sessions", memory: "Your notes", repo: "Code", data: "Data", jarvis: "Earlier Jarvis items", live: "Live system" };
const firstLine = (s) => { s = C(s); const m = s.match(/^.{30,}?[.;](?=\s|$)/); return (m ? m[0] : s).slice(0, 220); };
const own = (a) => (a.points || []).map((p, i) => (WH.isWait(p) ? null : `${a.id}.${i}`)).filter(Boolean);
const keysFor = (a) => ((a.points || []).length ? own(a) : [a.id]);
const DOT = { done: "bg-ok", ok: "bg-ion", draft: "bg-ion", need: "bg-warn", open: "bg-line-3" };
const keyOf = (x) => x.r.id + "|" + x.a.id;
// always-true answers to anything Claude asks, next to the routine's own options
const GENERIC = [{ label: "Already done", plan: "You did it yourself; Claude checks and closes it", generic: true }, { label: "Skip", plan: "Not needed; Claude drops it", generic: true }] as any[];
/** "Title. Details..." -> [title, details]: the first sentence (or question) is the title, nothing is dropped. */
const splitQ = (t) => { const s = String(t || "").trim(); const m = s.match(/^(.{12,180}?[?.:])(\s+|$)([\s\S]*)$/); return m ? [m[1].replace(/:$/, ""), m[3].trim()] : [s, ""]; };
/** The questions of a work entry: `asks` (routines from 2 Oct: {id, q, detail, options}) or its `needs_you` lines; an
    item-level decision (`options`) goes with its first real question. */

/* A result that starts "VERDICT: yes|partly|no — reason (numbers; more numbers). More." becomes a badge, one lead line and
   short bullets (5 Oct: the owner, "see how messy it looks, there is no structure"). */
const VERDICT = { yes: ["Answered", "ok"], partly: ["Partly answered", "ion"], no: ["Not answered by this data", "warn"] } as const;
function splitResult(text: string) {
  // a sentence ends at . ! ? followed by a space and a capital (never inside "$12.50" or "e.g. x")
  const sentences = String(text || "").replace(/\s+/g, " ").trim().split(/(?<=[.!?])\s+(?=[A-Z])/).map((x) => x.trim()).filter(Boolean);
  const bullets: string[] = [];
  const lead = (sentences.shift() || "").replace(/\s*\(([^)]*)\)/g, (_m, inner) => { inner.split(/;\s*/).forEach((b: string) => b.trim() && bullets.push(b.trim())); return ""; });
  sentences.forEach((x) => bullets.push(x.replace(/\.$/, "")));
  return { lead: lead.replace(/\s+([,.])/g, "$1"), bullets };
}
function ResultLine({ text, verdict }: { text: string; verdict?: string }) {
  const m = String(text || "").match(/^\s*VERDICT:\s*(yes|partly|no)\b\s*[—–-]?\s*([\s\S]*)$/i);
  const stored = verdict && verdict in VERDICT ? (verdict as keyof typeof VERDICT) : null;
  const v = m ? (m[1].toLowerCase() as keyof typeof VERDICT) : stored;
  // the badge says it, so the words "Answered:" / "Computed." do not open the text too
  const body = (m ? m[2] : String(text || "")).replace(/^\s*(?:answered|computed|done|checked)\s*[:.]\s*/i, "");
  const { lead: lead0, bullets } = splitResult(body), lead = lead0.charAt(0).toUpperCase() + lead0.slice(1);
  return (
    <div className="grid gap-1.5">
      <div className="flex flex-wrap items-baseline gap-2">
        {v && <Tag tone={VERDICT[v][1] as any}>{VERDICT[v][0]}</Tag>}
        <span className="text-ink">{C(lead)}</span>
      </div>
      {bullets.length > 0 && <ul className="m-0 grid gap-0.5 pl-[18px] text-[13.5px] text-ink-2 marker:text-mute">{bullets.slice(0, 6).map((b, i) => <li key={i}>{C(b)}</li>)}</ul>}
    </div>
  );
}
/* How a sender reads (19 Oct: "I dont like the way it writes You(Jarvis)"): his own asks are "You" with his initials, Jarvis's
   own items "Jarvis"; the source chip already says Jarvis, so a "Jarvis" place label is not repeated. Old items keep their
   stored labels and read the same way. */
const nameOf = (a: any) => { const w = String((a && a.who) || ""); return /^you\b/i.test(w) ? "You" : /^jarvis\b/i.test(w) ? "Jarvis" : w; };
const placeOf = (a: any) => (a && (a.src === "jarvis" || a.src === "noticed") && /^jarvis$/i.test(String(a.where || "")) ? "" : (a && a.where) || "");
const avatarOf = (a: any) => (/^you\b/i.test(String((a && a.who) || "")) ? (WH.me.name || "You") : a && a.who);
const sameRef = (a: any) => (/^you\b/i.test(String((a && a.who) || "")) ? "your earlier ask" : `${nameOf(a)}'s ${placeOf(a) || "message"}`);
const cutWord = (t: string, n: number) => (t.length <= n ? t : t.slice(0, n).replace(/\s+\S*$/, "").replace(/[,;:]$/, "") + "…");
/* What Jarvis noticed by itself, as labelled rows; the worker's brief (the point text) is never shown. */
/* A task he gave Jarvis: the card shows his asks, not the worker's brief (12 Oct: "why does the ask come as a paragraph").
   One long point = an older item: its first sentence, the rest folded. Several points: the checklist, the full brief folded. */
const longBrief = (a: any, pts: any[]) => a.src === "jarvis" && !a.noticed && pts.length === 1 && WH.ptText(pts[0]).length > 240;
const firstSentence = (s: string) => { const m = s.match(/^.{20,240}?[.!?](?=\s|$)/); return m ? m[0] : s.slice(0, 200) + "…"; };
/* The brief as a list (12 Oct: "even the details part should be structured"): one line per instruction; a sentence that is
   "label: a, b, c" becomes the bold label with its items underneath. Lines the brain already wrote (newlines, "- ") are kept. */
function splitTop(s: string, sep: string) {
  const out: string[] = []; let d = 0, cur = "";
  for (const ch of s) {
    if (ch === "(" || ch === "[") d++; else if ((ch === ")" || ch === "]") && d > 0) d--;
    if (ch === sep && d === 0) { out.push(cur); cur = ""; } else cur += ch;
  }
  out.push(cur); return out.map((x) => x.trim()).filter(Boolean);
}
function briefLines(t: string): string[] {
  const lines = t.split(/\n+/).map((x) => x.replace(/^\s*[-•*]\s+/, "").trim()).filter(Boolean);
  if (lines.length > 1) return lines;
  return t.split(/(?<=[.!?])\s+(?=[A-Z~"'(])/).map((x) => x.trim()).filter(Boolean);
}
function BriefList({ text, skipFirst }: { text: string; skipFirst?: boolean }) {
  const lines = briefLines(String(text || "")).slice(skipFirst ? 1 : 0);
  return (
    <ul className="m-0 mt-2 grid max-w-[92ch] gap-1.5 pl-[18px] text-[13.5px] text-ink-2 marker:text-mute">
      {lines.map((ln, i) => {
        const c = splitTop(ln, ":"), label = c.length > 1 ? c[0] : "", rest = c.length > 1 ? c.slice(1).join(": ") : "";
        const items = rest ? splitTop(rest.replace(/[.]\s*$/, ""), ",") : [];
        if (label && label.length <= 60 && items.length >= 3)
          return <li key={i}><b className="font-semibold text-ink">{label}:</b><ul className="mt-1 grid gap-0.5 pl-[18px] marker:text-mute">{items.map((x, j) => <li key={j}>{x.replace(/^and\s+/, "")}</li>)}</ul></li>;
        if (label && label.length <= 60) return <li key={i}><b className="font-semibold text-ink">{label}:</b> {rest}</li>;
        return <li key={i}>{ln}</li>;
      })}
    </ul>
  );
}

function briefFold(a: any, pts: any[]) {
  if (a.src !== "jarvis" || a.noticed) return null;
  const full = String(a.text || "");
  const show = longBrief(a, pts) || (!!full && full !== WH.ptText(pts[0]));   // 19 Oct: a single clean ask folds the brief too
  if (!show) return null;
  return (
    <details className="mt-2 text-[13px] text-mute">
      <summary className="cursor-pointer select-none hover:text-ink-2">Full brief Claude works from</summary>
      <BriefList text={C(full)} skipFirst={longBrief(a, pts)} />
    </details>
  );
}

function NoticedBrief({ n }: { n: any }) {
  const blank = (x: any) => !x || /^(not stated|none|n\/a|-)$/i.test(String(x).trim());
  const rows: [string, string][] = [["Needed", n.need], ["Why it matters", n.why], ["Assumed now", blank(n.assumed) ? "" : n.assumed],
    [n.verdict ? "Started from" : "Will check", (n.checked || []).join(" · ")]];
  return (
    <div className="grid gap-1.5">
      <span className="text-[12.5px] text-mute">Jarvis noticed this by itself{n.origin ? `, from ${n.origin}` : ""}. Nobody asked today.</span>
      <dl className="m-0 grid grid-cols-[max-content_minmax(0,1fr)] gap-x-3 gap-y-1 text-[13.5px] max-[560px]:grid-cols-1">
        {rows.filter(([, v]) => v && String(v).trim()).map(([k, v]) => (
          <div key={k} className="contents"><dt className="text-mute">{k}</dt><dd className="m-0 text-ink-2">{cutWord(C(String(v)), 220)}</dd></div>
        ))}
      </dl>
    </div>
  );
}

function asksOf(w, qs, choices) {
  let list = Array.isArray(w.asks) && w.asks.length
    ? w.asks.map((x, i) => ({ id: String(x.id || "q" + (i + 1)), q: WH.clean(x.q || ""), detail: WH.clean(x.detail || ""), options: x.options || [] }))
    : (qs || []).map((n, i) => { const [q, d] = splitQ(WH.clean(n)); return { id: "q" + (i + 1), q, detail: d, options: [] as any[] }; });
  if (choices.length && !list.some((q) => (q.options || []).length)) {
    if (!list.length) list = [{ id: "q1", q: "Your decision", detail: "", options: [] }];
    const k = Math.max(0, list.findIndex((q) => /\?/.test(q.q)));
    list[k] = { ...list[k], options: choices };
  }
  return list;
}
// the subject is the point itself (one point, same words): show it once
const norm = (t) => String(t || "").toLowerCase().replace(/[^a-z0-9]+/g, " ").trim();
const samePt = (a) => { const pts = a.points || []; const t = norm(a.title); if (!t) return true; const p = norm(pts.length === 1 ? WH.ptText(pts[0]) : !pts.length ? a.text : ""); return !!p && (p.startsWith(t.replace(/ $/, "")) || t.startsWith(p)); };

export default function Inbox({ sub }: { sub: string }) {
  useWH(["inbox", "tickets", "approvals"]);
  const [, force] = useReducer((x) => x + 1, 0);
  useTick(60000, force);
  const parts = String(sub || "").split("/"), subTab = valid(parts[0]) ? parts[0] : "";
  const subItem = parts.length > 1 && parts[1] ? decodeURIComponent(parts.slice(1).join("/")) : "";
  const [tab, setTab] = useState(() => { if (subTab) return subTab; const p = WH.pref.get("wh_ibtab2"); return valid(p) ? p : "todo"; });
  const first = useRef(true);
  useEffect(() => { if (first.current) { first.current = false; return; } const t = subTab || "todo"; WH.pref.set("wh_ibtab2", t); setTab(t); }, [sub]);   // eslint-disable-line react-hooks/exhaustive-deps
  const edits = useRef(new Map<string, string>()).current;   // rid|aid -> edited reply text (kept while the view is open)
  const openPd = useRef(new Set<string>()).current;          // "How Claude checked" boxes the user opened, kept across refreshes
  const [busy, setBusy] = useState<string | null>(null);
  const [q, setQ] = useState("");
  const [sel, setSel] = useState<string>(subItem);
  const [wide, setWide] = useState(() => typeof window !== "undefined" && window.matchMedia(WIDE).matches);
  const wideRef = useRef(wide); wideRef.current = wide;
  const listRef = useRef<string[]>([]), curRef = useRef(""), tabRef = useRef(tab), lastIdx = useRef(0);
  tabRef.current = tab;
  // Jarvis sees the item on screen, its live draft (unsaved edits too) and the list, and sends through the same functions as the buttons.
  const shownRef = useRef(""), voiceRef = useRef<any>(null);
  useEffect(() => registerScreen({
    selected: () => shownRef.current,
    draft: (rid, aid) => voiceRef.current.replyText(rid + "|" + aid, WH.workFor(rid, aid)),
    list: () => (tabRef.current === "todo" ? listRef.current : []),
    sendSlack: (rid, aid) => voiceRef.current.sendSlack(rid, aid, true),
    postJira: (rid, aid) => voiceRef.current.postJira(rid, aid, true),
  }), []);
  useShell(() => TABS.map(([k, l]) => ({ l: "Show " + l, k: "inbox tab", ic: "inbox", run: () => WH.go("inbox/" + k) })));

  useEffect(() => { const m = window.matchMedia(WIDE), f = () => setWide(m.matches); m.addEventListener("change", f); return () => m.removeEventListener("change", f); }, []);
  // A deep link (or a row tap) selects the item; on phones the detail opens at the top.
  useEffect(() => { if (subItem) { setSel(subItem); if (!wideRef.current) window.scrollTo({ top: 0, behavior: "instant" as ScrollBehavior }); } }, [subItem]);
  const select = (k: string) => { setSel(k); WH.go("inbox/todo/" + encodeURIComponent(k)); };

  // j / k move through the To do list (not while typing, not with a dialog open).
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.metaKey || e.ctrlKey || e.altKey || (e.key !== "j" && e.key !== "k") || tabRef.current !== "todo") return;
      const t = e.target as HTMLElement | null;
      if ((t && t.closest && t.closest("input,textarea,select,[contenteditable=true]")) || document.querySelector("[role=dialog],[role=alertdialog]")) return;
      const L = listRef.current; if (!L.length) return;
      const i = L.indexOf(curRef.current), n = e.key === "j" ? Math.min(L.length - 1, i + 1) : Math.max(0, i - 1);
      e.preventDefault(); select(L[n]);
      setTimeout(() => document.querySelector(`[data-row-key="${CSS.escape(L[n])}"]`)?.scrollIntoView({ block: "nearest" }), 30);
    };
    document.addEventListener("keydown", onKey); return () => document.removeEventListener("keydown", onKey);
  }, []);   // eslint-disable-line react-hooks/exhaustive-deps

  // Today -> "Review and send": open the item, bring its row into view and pulse the card (after the page has settled).
  useEffect(() => {
    if (!WH.focusItem || tab !== "todo" || !WH.store.inboxLoaded) return;
    const key = WH.focusItem; WH.focusItem = null; setSel(key);
    setTimeout(() => {
      document.querySelector(`[data-row-key="${CSS.escape(key)}"]`)?.scrollIntoView({ block: "nearest" });
      const el = document.querySelector(`article[data-key="${CSS.escape(key)}"]`);
      if (!el) return;
      if (!wideRef.current) window.scrollTo({ top: el.getBoundingClientRect().top + window.scrollY - 72, behavior: "instant" as ScrollBehavior });
      el.classList.add("flash"); setTimeout(() => el.classList.remove("flash"), 1700);
    }, 140);
  });

  const pd = (k: string, summary: React.ReactNode, children: React.ReactNode, className = "mt-1.5") => (
    <Disclosure small className={className} summary={summary} defaultOpen={openPd.has(k)} onOpenChange={(o) => { if (o) openPd.add(k); else openPd.delete(k); }}>{children}</Disclosure>
  );

  /* ---------- status line ---------- */
  const sc = WH.store.inbox.schedule, last = WH.lastRoundup(), next = WH.nextRoundup();
  const hourly = sc && sc.work_times, times = sc && sc.times && !hourly ? sc.times.map((t) => t.replace(/^0/, "")).join(", ") : "", nw = WH.nextWork();
  const status = `${hourly ? `Checks every hour and works on items at half past, ${sc.days || ""}` : "Updates itself" + (times ? " " + (sc.days || "") + " at " + times : "")}${last ? " · last checked " + WH.fmtWhen(last) : ""}${WH.workRunning() ? " · working on items now" : hourly && nw ? " · next work run " + WH.fmtWhen(nw) : next ? " · next " + WH.fmtWhen(next) : ""}`;

  function pointInfo(r, a, k, w) {
    const wp = ((w && w.points) || []).find((x) => x.key === k) || {};
    const aps = WH.approvalsFor(r.id, a.id).filter((x) => x.kind !== "slack_send" && x.related && x.related.point === k);
    let s;
    if (WH.ticked(r.id, k)) s = wp.state === "done" || wp.state === "you" ? wp.state : /^answered by you/i.test(wp.answer || "") ? "you" : "manual";
    else if (aps.some((x) => x.status === "pending")) s = "ok";
    else if (aps.some((x) => x.status === "approved" || x.status === "running")) s = "running";
    else if (ST[wp.state]) s = wp.state;
    else s = wp.answer ? (w.reply && w.reply.text ? "drafted" : "checked") : "open";
    const ap = aps.find((x) => x.status === "pending" || x.status === "approved" || x.status === "running");
    const note = wp.note ? C(wp.note) : ap ? C(ap.title) : s === "manual" || s === "open" ? "" : firstLine(wp.answer || "");
    const detail = wp.answer && C(wp.answer) !== note ? C(wp.answer) : "";
    const srcs = (wp.sources || []).map((x) => [x.type, x.ref].filter(Boolean).join(": ")).join(" · ");
    return { s, note, detail, srcs: detail ? C(srcs) : "" };
  }

  function stLine(r, k, x, compact = false) {
    const [label, tone, I] = ST[x.s], dk = r.id + "|" + k;
    return (
      <>
        <div className="mt-2 flex flex-wrap items-baseline gap-x-2.5 gap-y-1">
          <AnimatePresence mode="wait" initial={false}>
            <motion.span key={x.s} className="inline-flex flex-none" initial={{ opacity: 0, scale: 0.96 }} animate={{ opacity: 1, scale: 1 }} exit={{ opacity: 0, scale: 0.96 }} transition={{ duration: 0.16 }}>
              <Tag tone={tone} data-state={x.s}>{I ? ic(I, 12) : null}{label}</Tag>
            </motion.span>
          </AnimatePresence>
          {x.note && !compact && <span className="min-w-0 text-[13.5px] text-ink-2">{x.note}</span>}
        </div>
        {x.detail && !compact && pd(dk, "How Claude checked",
          <div className="mt-1.5 whitespace-pre-line rounded-[10px] bg-card-2 px-3 py-2.5 text-[13.5px] text-ink-2">{x.detail}{x.srcs && <span className="mt-1 block text-[12.5px] text-mute">{x.srcs}</span>}</div>)}
      </>
    );
  }

  /* ---------- actions ---------- */
  const replyText = (key, w) => (edits.has(key) ? edits.get(key)! : (w && w.reply && w.reply.text) || "");
  async function markDone(rid, aid, keys) {
    const w = WH.workFor(rid, aid), rep = (w && w.reply) || {};
    const sent = rep.sent_at || (w && w.replied_by_owner) || WH.approvalsFor(rid, aid).some((x) => x.kind === "slack_send" && x.status === "done");
    if (rep.text && !sent && !(await WH.ask("Mark as done without sending?", `Your reply${rep.to ? " to " + rep.to : ""} has not been sent. "Mark done" only hides the item here.${rep.channel === "slack" ? " To send it, use Send to Slack instead." : ""}`, "Mark done anyway"))) return;
    // 1 Nov: with every point already ticked, only <aid>.reply kept it in To do, and "Mark done anyway" never ticked it
    WH.tickInbox(rid, rep.text && !sent ? [...keys, `${aid}.reply`] : keys, true); WH.toast('Marked done. Nothing was sent. Undo it from "Done" at the bottom.', "info", 3500);
  }
  async function makeTicket(rid, aid) {
    const r = WH.store.inbox.roundups.find((x) => x.id === rid), a = r && (r.actions || []).find((x) => x.id === aid);
    if (!a) return; setBusy("t|" + rid + "|" + aid); const ids = await WH.proposeTickets([{ r, a, i: null }]); setBusy(null);
    if (ids.length) WH.toast("Added to Jira tickets. Create it from the Tickets tab.", "ok", 3500);
  }
  // `voice`: Jarvis already read it back and gave a 3-second cancel, so the confirm dialog is skipped (same API call as the button).
  // Sending is code (app/slack_send.py): posted once, with the Slack draft deleted by the same call; the card follows reply.send.
  async function sendSlack(rid, aid, voice = false) {
    const key = rid + "|" + aid, w = WH.workFor(rid, aid), val = replyText(key, w);
    if (!w || !val.trim()) return false;
    const rep = w.reply, where = rep.where ? ` in ${rep.where}` : "";
    const att = rep.attachments || [], isFile = (f: any) => /\.(xlsx|csv)$/i.test(String(f.path || "")), canv = att.filter((f: any) => !f.inline && !isFile(f)), files = att.filter(isFile);
    const attLine = (canv.length ? "\n\nAttached as a Slack canvas: " + canv.map((f: any) => f.title).join(", ") : "") + (files.length ? "\n\nLinked from your OneDrive (Analysis): " + files.map((f: any) => f.title).join(", ") : "");
    const dest = WH.replyLink(w);
    if (!voice && !(await WH.ask(`Send this to ${rep.to || "Slack"}${where}?`, val + attLine + (dest ? `\n\nGoes to: ${dest.href}` : ""), "Send to Slack"))) return false;
    let r: any;
    try { r = await WH.api("/api/slack_send", { rid, aid, text: val, voice }); } catch { r = { ok: false, error: "Jarvis server not reachable" }; }
    if (!r || !r.ok) { WH.toast((r && r.error) || "That didn't go through.", r && r.state === "unclear" ? "info" : "err", 7000); WH.loadInbox(); return false; }
    WH.toast(r.already ? "Already sending." : "Sending now.", "ok", 3000);
    // follow it until Slack has answered (about half a minute)
    for (let i = 0; i < 60; i++) {
      await new Promise((res) => setTimeout(res, 3000)); await WH.loadInbox();
      const s = ((WH.workFor(rid, aid) || {}).reply || {}), st = s.sent_at ? "sent" : (s.send || {}).state;
      if (st === "sent") { WH.toast(`Sent to ${rep.to || "Slack"}.` + (s.slack_draft === "created" ? " An older copy may still be in your Slack Drafts." : ""), "ok", 5000); return true; }
      if (st === "failed") { WH.toast("Not sent: " + ((s.send || {}).error || "unknown error"), "err", 8000); return false; }
      if (st === "unclear") { WH.toast("Checking in Slack whether it went out…", "info", 5000); }
    }
    WH.toast("Still sending. The item shows Sent once Slack confirms.", "info", 6000);
    return false;
  }
  const postedMsg = (d: any, k: string) => (d && d.queued ? (d.already === "done" ? `That comment is already on ${k}.` : `Claude is posting it on ${k} now.`) : "Comment posted.");
  async function postJira(rid, aid, voice = false) {
    const key = rid + "|" + aid, w = WH.workFor(rid, aid); if (!w) return false;
    let res: any;
    if (voice) {
      try { res = await WH.api("/api/jira/comment", { key: w.reply.jira_key, text: replyText(key, w), dry: false, rid, aid }); } catch { res = { ok: false, error: "Jarvis server not reachable" }; }
      WH.log(`Post this comment on ${w.reply.jira_key}?`, JSON.stringify(res.result || res, null, 1).slice(0, 4000));
      if (!res.ok) { WH.actionError(res); res = null; } else WH.toast(postedMsg(res, w.reply.jira_key), "ok", 5000);
    } else res = await WH.confirmRun("/api/jira/comment", { key: w.reply.jira_key, text: replyText(key, w), rid, aid }, { title: `Post this comment on ${w.reply.jira_key}?`, yes: "Post comment", describe: (d) => (d.plan && d.plan[0] ? d.plan[0].text : ""), done: (d) => postedMsg(d, w.reply.jira_key) });
    // Posted through Claude (no Jira token): the worker ticks the points once the comment is really on Jira.
    if (res && !res.queued) WH.tickInbox(rid, keysFor((WH.store.inbox.roundups.find((x) => x.id === rid)?.actions || []).find((x) => x.id === aid) || { id: aid }), true);
    return res ? (res.queued && res.already !== "done" ? "queued" : true) : false;
  }
  const approve = async (id, choice?) => { if (await WH.setApproval(id, "approved", choice)) WH.toast("Approved. " + WH.whenApproved(), WH.store.approvals.executor ? "ok" : "info", 4500); };
  const decline = async (id) => { if (await WH.setApproval(id, "declined")) WH.toast("Declined. Nothing will happen.", "info", 2500); };
  const undoApproval = async (id) => { if (await WH.setApproval(id, "pending")) WH.toast("Approval taken back.", "info", 2500); };

  /* ---------- the Claude box ---------- */
  const spark = <span className="grid size-6 flex-none place-items-center rounded-full bg-ion text-white dark:text-chrome"><Sparkles size={13} strokeWidth={2} aria-hidden /></span>;
  /* ---------- what Claude needs from him: one card per question, one-tap options, one Send ---------- */
  // Each question: a bold one-line title, its details smaller below (nothing dropped), the routine's options as buttons,
  // plus "Already done" / "Skip" (always true answers), or his own words. Answers go to Claude together, labelled per question.
  function askList(r, a, key, qlist, ans) {
    const editing = edits.get("ans|" + key) === "edit" || !ans;
    const val = (q) => { const t = (edits.get(`askt|${key}|${q.id}`) || "").trim(); return t || edits.get(`ask|${key}|${q.id}`) || ""; };
    const done = qlist.filter((q) => val(q)).length;
    const pick = (q, label) => { const k = `ask|${key}|${q.id}`; if (edits.get(k) === label) edits.delete(k); else { edits.set(k, label); edits.delete(`askt|${key}|${q.id}`); } force(); };
    const send = async () => {
      const parts: Record<string, string> = {};
      qlist.forEach((q) => { const v = val(q); if (v) parts[q.id] = v; });
      if (!Object.keys(parts).length) return WH.toast("Pick or type an answer first.", "info");
      const text = qlist.filter((q) => parts[q.id]).map((q, i) => `${qlist.indexOf(q) + 1}. ${q.q} -> ${parts[q.id]}`).join("\n");
      if (await WH.saveAnswer(r.id, a.id, text, parts)) {
        qlist.forEach((q) => { edits.delete(`ask|${key}|${q.id}`); edits.delete(`askt|${key}|${q.id}`); });
        edits.delete("ans|" + key);
        WH.toast(`Sent to Claude (${Object.keys(parts).length} of ${qlist.length}). It starts on it now.`, "ok", 3500);
      }
      force();
    };
    const given = (q) => (ans && ans.parts && ans.parts[q.id]) || "";
    return (
      <section className="grid gap-2.5" data-needs>
        <div className="flex flex-wrap items-baseline justify-between gap-2">
          <span className="font-mono text-[11.5px] font-semibold uppercase tracking-[.12em] text-warn">Needs you · {qlist.length}</span>
          {editing ? <span className="text-[12.5px] text-mute">{done} of {qlist.length} answered</span>
            : <span className="text-[12.5px] text-mute">{ans.used ? "Claude used your answers" : "Claude is on it"} · <button type="button" className="font-medium text-ion hover:underline" onClick={() => { edits.set("ans|" + key, "edit"); force(); }}>Change</button></span>}
        </div>
        <ol className="m-0 grid list-none gap-2 p-0">
          {qlist.map((q, i) => {
            const cur = editing ? edits.get(`ask|${key}|${q.id}`) || "" : "", typed = edits.get(`askt|${key}|${q.id}`) || "";
            const sel = (q.options || []).find((o) => o.label === cur);
            const mine = !editing ? given(q) || (i === 0 && ans && !ans.parts ? ans.text : "") : "";
            return (
              <li key={q.id} className="grid grid-cols-[24px_minmax(0,1fr)] gap-3 rounded-[12px] border border-line bg-ink/[.015] px-3.5 py-3">
                <span className={cn("mt-px grid size-6 place-items-center rounded-full font-mono text-[11.5px] font-semibold", (editing ? val(q) : mine) ? "bg-ok-soft text-ok" : "bg-warn-soft text-warn")}>{(editing ? val(q) : mine) ? <Check size={12} strokeWidth={2.4} /> : i + 1}</span>
                <div className="grid min-w-0 gap-1.5">
                  <b className="font-display text-[14.5px] font-semibold leading-snug text-ink">{q.q}</b>
                  {q.detail && <p className="m-0 max-w-[92ch] text-[13px] leading-relaxed text-mute">{q.detail}</p>}
                  {editing ? (<>
                    <div className="mt-1 flex flex-wrap items-center gap-1.5">
                      {[...(q.options || []), ...GENERIC].map((o) => (
                        <button key={o.label} type="button" onClick={() => pick(q, o.label)} title={o.plan || undefined} aria-pressed={cur === o.label}
                          className={cn("inline-flex items-center gap-1.5 rounded-full border px-3 py-1 text-left text-[13px] font-medium transition-colors duration-150",
                            cur === o.label ? "border-ion bg-ion-soft text-ink" : o.generic ? "border-line-3 text-mute hover:border-ion hover:text-ink" : "border-line-3 bg-ink/[.03] text-ink hover:border-ion")}>
                          {cur === o.label && <Check size={12} strokeWidth={2.4} className="text-ion" />}{C(o.label)}{o.recommended && <span className="font-mono text-[10px] uppercase tracking-[.08em] text-ion">rec</span>}
                        </button>))}
                    </div>
                    {sel && sel.plan && <p className="m-0 text-[12.5px] text-mute">What happens: {C(sel.plan)}</p>}
                    <input value={typed} onChange={(e) => { edits.set(`askt|${key}|${q.id}`, e.target.value); force(); }} onKeyDown={(e) => { if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) send(); }}
                      placeholder="Or type your answer" aria-label={`Your answer to question ${i + 1}`}
                      className="mt-0.5 w-full rounded-[10px] border border-line-3 bg-transparent px-3 py-1.5 text-[13.5px] text-ink outline-none placeholder:text-mute focus:border-ion" />
                  </>) : mine ? <div className="text-[13.5px] text-ink-2"><span className="font-semibold text-ok">You:</span> {C(mine)}</div>
                    : <div className="text-[13px] text-mute">Not answered</div>}
                </div>
              </li>
            );
          })}
        </ol>
        {editing && (
          <div className="flex flex-wrap items-center gap-3">
            <Button variant="primary" disabled={!done} onClick={send}>{ic(Send)}Send {done || ""} answer{done === 1 ? "" : "s"} to Claude</Button>
            <span className="text-[12.5px] text-mute">Claude starts on it right away.</span>
          </div>
        )}
      </section>
    );
  }

  // he asked for it: an item waiting for the half-past run starts at once (5 Oct: "it should not wait till the next routine")
  async function doNow(rid, aid) {
    let res: any; try { res = await WH.api("/api/jarvis_task", { type: "start_task", rid, aid }); } catch { res = { ok: false }; }
    WH.toast(res && res.ok ? (res.queued_behind ? "Claude starts on this as soon as the current task ends." : "Claude is on it now.") : res && res.error ? "That didn't start: " + res.error + "." : "That didn't start. Is the Jarvis server running?", res && res.ok ? "ok" : "info", 4000);
  }
  function claudeBlock(r, a) {
    const w = WH.workFor(r.id, a.id), nextW = WH.nextWork();
    // a task Jarvis started runs at once on its own worker (not the weekday schedule): say so, never "Monday 06:30" (3 Oct)
    const jt = WH.store.inbox.jarvis_tasks || {}, mine = (x) => x && x.rid === r.id && x.aid === a.id;
    const jRunning = (jt.running_all || (jt.running ? [jt.running] : [])).find(mine);
    const jRun = !!jRunning, jQ = (jt.queue || []).some(mine);
    const since = jRunning && jRunning.started ? WH.fmtWhen(new Date(jRunning.started)) : "";
    if (!w) return <div data-claude><p className="m-0 flex items-center gap-2.5 text-[13.5px] text-ink-2">{spark}{jRun ? `Claude is working on this now${since ? " (started " + since + ")" : ""}. The result shows here when it finishes.` : jQ ? "Queued: Claude starts on this as soon as a worker is free." : WH.workRunning() ? "Claude is working on this now. The draft shows here when the run finishes." : <>Not started yet.<Button size="sm" className="ml-1" onClick={() => doNow(r.id, a.id)}>{ic(Play, 13)}Do it now</Button></>}</p></div>;
    const key = r.id + "|" + a.id, rep = w.reply || {}, text = replyText(key, w), att = rep.attachments || [];
    // The heading follows the open points: yours first, then Claude's own queue, then the draft.
    const states = (w.points || []).filter((p) => !WH.ticked(r.id, p.key) && !WH.sameAs(r.id, p.key)).map((p) => p.state);
    const head = states.some((s) => s === "decide" || s === "needs_you") ? "Claude needs your input"
      : states.length && states.every((s) => s === "queued" || s === "working") ? (states.includes("working") ? "Claude is running this now" : "Queued: Claude does this next")
      : text ? "Claude drafted a reply" : w.status === "done" ? "Claude finished this" : w.status === "needs_you" ? "Claude needs your input" : "Claude checked this";
    const link = WH.replyLink(w), aps = WH.approvalsFor(r.id, a.id), send = aps.filter((x) => x.kind === "slack_send").pop();
    const sst = rep.sent_at || (send && send.status === "done") ? "sent" : (rep.send || {}).state;
    let btn: React.ReactNode = null;
    if (sst === "sent") btn = <>
      <Tag tone="ok">{ic(Check, 13)}{rep.sent_by === "The owner (from Slack)" ? "You sent it from Slack" : "Sent"}</Tag>
      {(rep.sent_link || rep.message_link) && <Button variant="ghost" asChild><a href={rep.sent_link || rep.message_link} target="_blank" rel="noopener">Open in Slack</a></Button>}
    </>;
    else if (sst === "sending") btn = <Tag tone="ion">{ic(Clock, 13)}Sending to Slack now</Tag>;
    else if (sst === "unclear") btn = <Tag tone="ion">{ic(Clock, 13)}Checking in Slack whether it went out</Tag>;
    else if (text && rep.channel === "slack" && rep.channel_id) btn = (<>
      <Button variant="primary" onClick={() => sendSlack(r.id, a.id)} data-act="send-slack">{ic(Send, 15)}Send to Slack</Button>
      {link && <Button variant="ghost" asChild><a href={link.href} target="_blank" rel="noopener">Open in Slack</a></Button>}
      {sst === "failed" && <Tag>Last try did not go out: {C((rep.send || {}).error || "")}</Tag>}
    </>);
    else if (text && rep.channel === "jira" && rep.jira_key) btn = <Button variant="primary" onClick={() => postJira(r.id, a.id)}>{ic(Send, 15)}Post to {rep.jira_key}</Button>;
    else if (text && link) btn = <Button variant="primary" asChild><a href={link.href} {...(link.href.startsWith("http") ? { target: "_blank", rel: "noopener" } : {})}>{ic(Send, 15)}{link.label}</a></Button>;
    else if (text) btn = <Button onClick={() => WH.copy(text, "Reply copied.")}>{ic(Copy, 15)}Copy reply</Button>;
    const jobs = aps.filter((x) => x.kind !== "slack_send");
    const asks = jobs.filter((x) => x.status === "pending").map((x) => {
      const opts = x.options || [];
      return (
        <div key={x.id} className="grid grid-cols-[minmax(0,1fr)_auto] items-center gap-3 rounded-[12px] border bg-card px-3.5 py-3 shadow-xs max-[640px]:grid-cols-[minmax(0,1fr)]" style={{ borderColor: "color-mix(in srgb, var(--ion) 35%, var(--line))" }}>
          <div className="min-w-0">
            <b className="block font-display text-[14.5px] font-semibold">Needs your OK: {C(x.title)}</b>
            <small className="mt-0.5 block text-[13.5px] text-mute">{C(x.why)}</small>
            {x.plan && <Disclosure small className="mt-1.5" summary="What exactly happens"><div className="mt-1.5 whitespace-pre-line text-[13.5px] text-ink-2">{C(x.plan)}</div></Disclosure>}
            {opts.length > 0 && <div className="mt-2.5 grid gap-2">{opts.map((o) => (
              <div key={o.id} className="flex flex-wrap items-center gap-2.5">
                <Button size="sm" className="h-auto whitespace-normal py-1.5 text-left" variant={o.recommended ? "primary" : "default"} onClick={() => approve(x.id, o.id)}>{ic(Check)}{C(o.label)}</Button>
                {o.plan && <small className="text-[13.5px] text-mute">{C(o.plan)}</small>}
              </div>))}</div>}
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <Button size="sm" variant="ghost" onClick={() => decline(x.id)}>Not now</Button>
            {!opts.length && <Button size="sm" variant="primary" onClick={() => approve(x.id)}>{ic(Check)}Go ahead</Button>}
          </div>
        </div>
      );
    });
    // Finished, running and declined jobs: one short line each, full result behind "Details".
    const did = jobs.filter((x) => x.status !== "pending").map((x) => {
      const chosen = (x.options || []).find((o) => o.id === x.choice), dk = "ap|" + x.id;
      const stillOpen = x.status === "done" && x.related && x.related.point && !WH.ticked(r.id, x.related.point);   // the job ran but its point is not solved
      const [lab, tone, I]: [string, any, any] = stillOpen ? ["Ran · point still open", "warn", Info] : x.status === "done" ? ["Done", "ok", Check] : x.status === "failed" ? ["Did not work", "warn", Info] : x.status === "declined" ? ["Declined", "mute", null] : ["Approved", "ion", Clock];
      return (
        <div key={x.id} className="grid grid-cols-[auto_minmax(0,1fr)_auto] items-baseline gap-2.5 text-[14px]">
          <Tag tone={tone}>{I ? ic(I, 12) : null}{lab}</Tag>
          <div className="min-w-0">
            <b className="font-display text-[14px] font-semibold text-ink-2">{C(x.title)}{chosen ? " · " + C(chosen.label) : ""}</b>
            {(x.status === "approved" || x.status === "running") && <div className="text-[13.5px] text-mute">{WH.whenApproved()}</div>}
            {x.result && pd(dk, "Details", <div className="mt-1.5 whitespace-pre-line text-[13.5px] text-ink-2">{C(x.result)}</div>, "mt-1")}
          </div>
          {x.status === "approved" ? <Button size="sm" variant="ghost" onClick={() => undoApproval(x.id)}>Undo</Button> : <span />}
        </div>
      );
    });
    const ans = WH.answerFor(r.id, a.id), qs = w.needs_you || [];
    const outputs = w.done_work || [];
    // the choices for his decision (work entry `options`, or on a `decide` point): one button each
    const choices = [...(w.options || []), ...(w.points || []).filter((p) => p.state === "decide" && !WH.ticked(r.id, p.key)).flatMap((p) => p.options || [])]
      .filter((o, i, all) => o && o.label && all.findIndex((x) => x.label === o.label) === i);
    const qlist = asksOf(w, qs, choices);
    // Answers already shown under their own point are not repeated here.
    const shown = new Set((a.points || []).length ? a.points.map((_, i) => `${a.id}.${i}`) : [a.id]);
    const single = keysFor(a).length <= 1 && !!w.summary;      // its point shows only a tag, so its answer lives in Details
    const answers = (w.points || []).filter((p) => (single || !shown.has(p.key)) && p.answer);
    // Every place the context sweep searched, including the ones that found nothing.
    const cx = w.context || {}, chk = cx.checked || [], dkc = "cx|" + key, dkd = "det|" + key;
    const nUseful = chk.filter((c) => c.found > 0).length;
    const detBits = [outputs.length ? `${outputs.length} result${outputs.length === 1 ? "" : "s"}` : "", did.length ? `${did.length} job${did.length === 1 ? "" : "s"}` : "", chk.length ? `looked in ${chk.length} place${chk.length === 1 ? "" : "s"}` : ""].filter(Boolean);
    const hasDetails = outputs.length > 0 || did.length > 0 || answers.length > 0 || chk.length > 0;
    return (
      <div className="grid gap-3" data-claude>
        <div className="flex max-w-[92ch] items-start gap-2.5 text-[14px] text-ink-2">
          {spark}
          <div className="min-w-0 pt-0.5">{w.summary ? <ResultLine text={w.summary} verdict={a.noticed && a.noticed.verdict} /> : <b className="font-semibold text-ink">{head}</b>}
            {Array.isArray(w.numbers) && w.numbers.length > 0 && (
              <dl className="m-0 mt-1.5 grid grid-cols-[max-content_minmax(0,1fr)] gap-x-3 gap-y-0.5 text-[13.5px] max-[560px]:grid-cols-1">
                {w.numbers.slice(0, 5).map((x: any, i: number) => (
                  <div key={i} className="contents"><dt className="text-mute">{C(x.what)}</dt>
                    <dd className="m-0 font-mono text-[13px] text-ink">{C(x.now)}{x.was ? <span className="text-mute"> (was {C(x.was)})</span> : null}</dd></div>
                ))}
              </dl>
            )}
            {w.review && (
              <div className="mt-1.5 grid gap-1">
                <span className="flex flex-wrap items-center gap-2 text-[13px]">
                  <Tag tone={w.review.verdict === "ok" ? "ok" : "warn"}>{w.review.verdict === "ok" ? "Checked by a second reviewer" : "Reviewer found problems"}</Tag>
                  {w.review.verdict === "ok" && Array.isArray(w.review.checked) && w.review.checked.length > 0 && <span className="text-mute">{w.review.checked.length} numbers traced to their source</span>}
                </span>
                {w.review.verdict !== "ok" && (w.review.issues || []).filter((i: any) => i.must_fix).slice(0, 4).map((i: any, k: number) => (
                  <span key={k} className="text-[13px] text-ink-2">· {C(i.problem)}{i.fix ? <span className="text-mute"> Fix: {C(i.fix)}</span> : null}</span>
                ))}
              </div>
            )}
            {Array.isArray(w.notes) && w.notes.length > 0 && (
              <ul className="m-0 mt-1.5 grid gap-0.5 pl-[18px] text-[13px] text-mute marker:text-mute">{w.notes.slice(0, 3).map((x: any, i: number) => <li key={i}>{C(x)}</li>)}</ul>
            )}
            {rep.slack_draft === "created" && !rep.sent_at && <Tag tone="ok" className="ml-2 align-middle">Draft waiting in Slack</Tag>}</div>
        </div>
        {(rep.sent_old_draft || rep.owner_wrote_after) && !rep.sent_at && (
          <div className="flex max-w-[92ch] items-start gap-2 rounded-[10px] bg-warn-soft px-3 py-2.5 text-[13.5px] text-ink-2">
            <Info size={13} strokeWidth={1.8} className="mt-0.5 flex-none text-warn" />
            <span>{rep.sent_old_draft ? `You sent an older version of this draft from Slack ("${C(rep.sent_old_draft.text).slice(0, 80)}…"). The newer text below is not sent yet.`
              : `You wrote in that conversation after this draft ("${C(rep.owner_wrote_after.text).slice(0, 80)}…"). Check before sending this too.`}</span>
          </div>
        )}
        {w.replied_by_owner && (
          <div className="flex max-w-[92ch] items-start gap-2 rounded-[10px] bg-info-soft px-3 py-2.5 text-[13.5px] text-ink-2">
            <Check size={13} strokeWidth={1.8} className="mt-0.5 flex-none text-info" />
            <span title={C(w.replied_by_owner.text)}>You already replied{w.replied_by_owner.at ? " at " + WH.fmtWhen(new Date(w.replied_by_owner.at)) : ""}: “{C(w.replied_by_owner.text).slice(0, 90)}{(w.replied_by_owner.text || "").length > 90 ? "…" : ""}”. The draft covers only what is left.</span>
          </div>
        )}
        {asks}
        {qlist.length > 0 && askList(r, a, key, qlist, ans)}
        {(text || edits.has(key)) && rep.text != null && (
          <Textarea className="min-h-24 rounded-[10px] border-line bg-card px-3.5 py-3 shadow-xs" value={text} onChange={(e) => { edits.set(key, e.target.value); force(); }} aria-label={`Reply to ${a.who || ""}`} data-reply={key} />
        )}
        {text && att.length > 0 && (
          <div className="flex flex-wrap items-center gap-2 text-[13.5px]">
            {att.map((f, i) => <span key={i} className="inline-flex min-w-0 items-center gap-2 rounded-[10px] border border-line bg-card py-1 pl-2.5 pr-1 shadow-xs">{ic(FileText, 14)}<b className="min-w-0 truncate font-semibold">{C(f.title || "Attachment")}</b><PillButton onClick={() => WH.openOutput(f.path)}>Open</PillButton></span>)}
            <small className="basis-full text-mute">{rep.channel !== "slack" ? "Add this file to your reply." : att.every((f: any) => /\.(xlsx|csv)$/i.test(String(f.path || ""))) ? "In your shared OneDrive Analysis folder; the link is at the end of the draft, so it goes even if you send from Slack." : att.some((f: any) => /\.(xlsx|csv)$/i.test(String(f.path || ""))) ? "Tables: linked from your OneDrive Analysis folder (in the draft). Write-ups: a Slack canvas, only when you send from Jarvis." : "Goes as a Slack canvas when you send from Jarvis (not from the Slack draft)."}</small>
          </div>
        )}
        {(btn || WH.resultUnseen(r, a)) && <div className="flex flex-wrap items-center gap-2">{btn}{WH.resultUnseen(r, a) && <Button variant={btn ? "ghost" : "primary"} onClick={() => WH.tickInbox(r.id, [`${a.id}.seen`], true)} data-act="seen">{ic(Check, 15)}Got it</Button>}</div>}
        {outputs.filter((d) => d.path).length > 0 && (
          <div className="flex flex-wrap items-center gap-2 text-[13.5px]">
            {outputs.filter((d) => d.path).map((d, i) => <span key={i} className="inline-flex min-w-0 max-w-full items-center gap-2 rounded-[10px] border border-line bg-card py-1 pl-2.5 pr-1 shadow-xs" title={C(d.summary || "")}>{ic(FileText, 14)}<b className="min-w-0 truncate font-semibold">{C(d.title || "Result")}</b><PillButton onClick={() => WH.openOutput(d.path)}>Open</PillButton></span>)}
          </div>
        )}
        {hasDetails ? pd(dkd, `Details${detBits.length ? " · " + detBits.join(" · ") : ""}`, (
          <div className="mt-2 grid gap-3 border-l border-line-2 pl-3.5">
            {outputs.length > 0 && <ul className="m-0 grid gap-1.5 pl-[18px] text-[13.5px] text-ink-2">{outputs.map((d, i) => <li key={i}><b>{C(d.title || "Result")}</b>{d.summary ? " — " + C(d.summary) : ""}</li>)}</ul>}
            {did.length > 0 && <div className="grid gap-2">{did}</div>}
            {answers.length > 0 && (
              <ol className="m-0 grid gap-2 pl-[18px] text-[13.5px] text-ink-2">{answers.map((p, i) => <li key={i}>{C(p.answer)}{(p.sources || []).length > 0 && <span className="mt-0.5 block text-[12.5px] text-mute">{(p.sources || []).map((s) => C([s.type, s.ref].filter(Boolean).join(": "))).join(" · ")}</span>}</li>)}</ol>
            )}
            {chk.length > 0 && pd(dkc, `Where Claude looked · ${chk.length} place${chk.length === 1 ? "" : "s"}, ${nUseful} with something useful`, (<>
              <ul className="mb-1.5 mt-2 grid gap-1 pl-[18px] text-[13.5px] text-ink-2">{chk.map((c, i) => <li key={i} className={c.found > 0 ? "" : "text-mute"}><b>{WHERE[c.where] || c.where || ""}</b> <span className="[overflow-wrap:anywhere]">{C(c.what || "")}</span>{c.found > 0 ? (c.used ? ` — ${C(c.used)}` : "") : " — nothing new"}</li>)}</ul>
              {cx.file && <PillButton onClick={() => WH.openOutput(cx.file)}>Open everything Claude found</PillButton>}
            </>), "mt-0")}
          </div>
        ), "mt-0") : null}
      </div>
    );
  }

  /** Scrolls to another item (the lead of a grouped ask) and flashes it. */
  function jumpTo(rid, aid) {
    // One item shows at a time (#/inbox/<tab>/<rid>|<aid>): open the other one in the same tab.
    const tab = (WH.parse().sub || "todo").split("/")[0] || "todo";
    location.hash = `#/inbox/${tab}/${encodeURIComponent(rid + "|" + aid)}`;
  }

  function itemInfo(r, a) {
    const pts = a.points || [], wk = WH.workFor(r.id, a.id);
    // Each point carries its own status, so it is clear what Claude did, what you did and what is still open.
    const infos = pts.length ? pts.map((p, i) => (WH.isWait(p) || !wk || WH.sameAs(r.id, `${a.id}.${i}`) ? null : pointInfo(r, a, `${a.id}.${i}`, wk))) : [wk ? pointInfo(r, a, a.id, wk) : null];
    const mine = infos.filter(Boolean) as any[], nDone = mine.filter((x) => GROUP[x.s] === "done").length, nNeed = mine.filter((x) => GROUP[x.s] === "need" || GROUP[x.s] === "ok").length;
    return { pts, wk, infos, mine, nDone, nNeed };
  }

  function itemCard(r, a, s) {
    const { pts, wk, infos, mine, nDone, nNeed } = itemInfo(r, a);
    const tk = WH.ticketFor(r.id, a.id) || (a.points || []).map((p, i) => WH.ticketFor(r.id, `${a.id}.${i}`)).find(Boolean);
    const isTask = r.kind === "meeting" || (wk && wk.kind === "task");   // tickets are for analysis/development work, not questions
    const key = r.id + "|" + a.id;
    return (
      <motion.article key={key} data-key={key} initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.22, ease: [0.2, 0.8, 0.2, 1] }}
        className="relative overflow-hidden rounded-[14px] border border-line bg-card shadow-card [overflow-wrap:anywhere]">
        {mine.length > 1 && <div aria-hidden className="flex h-[3px] gap-[2px]" data-prog-bar>{mine.map((x, i) => <i key={i} className={cn("flex-1 transition-colors duration-300", DOT[GROUP[x.s]])} />)}</div>}
        <div className="grid gap-4 px-6 py-5 max-[560px]:px-4">
          <div className="flex items-start gap-3">
            <Avatar name={avatarOf(a)} size={40} />
            <div className="min-w-0 flex-1">
              <div className="flex flex-wrap items-baseline gap-x-2 gap-y-0.5">
                <b className="font-display text-[17px] font-semibold tracking-[-.01em]">{C(nameOf(a))}</b>
                {placeOf(a) && <span className="text-[13px] text-mute">{C(placeOf(a))}</span>}
              </div>
              <div className="mt-1 flex flex-wrap items-center gap-2">
                <SrcBadge s={s} />
                {a.title && !samePt(a) && <span className="min-w-0 text-[13.5px] text-mute">{C(a.title)}</span>}
                {s === "bitbucket" && /^https:\/\/bitbucket\.org\//.test(String(a.link || "")) && <a href={a.link} target="_blank" rel="noopener" className="text-[13px] text-ink-2 underline decoration-dotted underline-offset-2 hover:text-ink">Open in Bitbucket</a>}
              </div>
            </div>
            {a.when && <span className="max-w-[34%] flex-none truncate font-mono text-[12px] text-mute" title={C(a.when)}>{C(a.when)}</span>}
          </div>
          {mine.length > 1 && (
            <div className="-mt-1 text-[13px] font-semibold text-mute" title="Green: done · blue: answered in the draft or waiting for your OK · orange: needs you" data-prog>
              {nDone} of {mine.length} done{nNeed ? ` · ${nNeed} need${nNeed === 1 ? "s" : ""} you` : ""}
            </div>
          )}
          {pts.length ? (
            <ul className="m-0 grid list-none gap-3 p-0">
              {pts.map((p, i) => {
                const k = `${a.id}.${i}`, w = WH.isWait(p), t = WH.ticked(r.id, k), same = WH.sameAs(r.id, k), also = WH.alsoAsked(r.id, k);
                return (
                  <li key={i} className="grid grid-cols-[20px_minmax(0,1fr)] gap-3 text-[14.5px] text-ink-2">
                    {w ? <span className="mt-px grid size-5 place-items-center rounded-full border-[1.5px] border-dashed border-line-3 text-mute" title="Waiting on them">{ic(Hourglass, 10)}</span>
                      : <RoundCheck checked={t} onChange={(v) => WH.tickInbox(r.id, [k], v)} label="Mark this point done" />}
                    <div className="min-w-0">
                      {a.noticed && i === 0 ? <NoticedBrief n={a.noticed} />
                        : <span className={cn("transition-colors duration-200", (t || w || same) && "text-mute")}>{longBrief(a, pts) ? firstSentence(WH.ptText(p)) : WH.ptText(p)}</span>}
                      {i === pts.length - 1 && briefFold(a, pts)}
                      {same && (
                        <button type="button" onClick={() => jumpTo(same.rid, same.aid)} className="mt-1 flex w-fit items-center gap-1.5 text-left text-[12.5px] font-medium text-info hover:underline" title={same.g.why}>
                          {ic(Link2, 12)}Same ask as {C(sameRef(same.a))}{same.a.when ? ` (${C(same.a.when)})` : ""}: tracked there{t ? ", done" : ""}
                        </button>
                      )}
                      {also.length > 0 && (
                        <div className="mt-1 flex flex-wrap items-center gap-1.5 text-[12.5px] text-mute" title={WH.groupOf(r.id, k)?.why || ""}>
                          {ic(Link2, 12)}Also asked: {also.map((x: any, j: number) => (
                            <button key={j} type="button" onClick={() => jumpTo(x.rid, x.a.id)} className="underline decoration-dotted underline-offset-2 hover:text-ink-2">{C(x.a.where || "elsewhere")}{x.a.when ? `, ${C(x.a.when)}` : ""}{x.a.title ? ` (${C(x.a.title)})` : ""}</button>
                          ))}
                        </div>
                      )}
                      {w && <span className="ml-2 whitespace-nowrap font-mono text-[11px] font-medium uppercase tracking-[.06em] text-mute">waiting on them</span>}
                      {p.points && <ul className="mt-1.5 grid gap-1 pl-[18px] text-[14px] text-ink-2 marker:text-mute">{p.points.map((x, j) => <li key={j}>{WH.ptText(x)}</li>)}</ul>}
                      {infos[i] && stLine(r, k, infos[i], mine.length <= 1 && !!(wk && wk.summary))}
                    </div>
                  </li>
                );
              })}
            </ul>
          ) : (<>
            {a.text && <p className="m-0 max-w-[92ch] text-[14.5px] text-ink-2">{C(a.text)}</p>}
            {infos[0] && <div>{stLine(r, a.id, infos[0], !!(wk && wk.summary))}</div>}
          </>)}
          {claudeBlock(r, a)}
          <div className="flex flex-wrap items-center gap-2 border-t border-line-2 pt-3">
            <Button size="sm" variant="ghost" onClick={() => markDone(r.id, a.id, keysFor(a))} title="Marks this handled in Jarvis. It does not send anything.">{ic(Check)}Mark done</Button>
            {tk ? <PillButton on asChild><a href="#/tickets">{tk.key ? tk.key : "Ticket proposed"}</a></PillButton>
              : isTask ? <Button size="sm" variant="ghost" busy={busy === "t|" + key} onClick={() => makeTicket(r.id, a.id)}>{ic(Ticket)}Make Jira ticket</Button> : null}
          </div>
        </div>
      </motion.article>
    );
  }

  /* ---------- list row (left pane) ---------- */
  function row(x, on) {
    const k = keyOf(x), { mine, nNeed } = itemInfo(x.r, x.a);
    const summary = C(x.a.title || WH.ptText((x.a.points || [])[0]) || x.a.text || "");
    return (
      <motion.button key={k} {...itemMotion} type="button" data-row-key={k} aria-current={on ? "true" : undefined} onClick={() => select(k)}
        className={cn("group relative grid w-full grid-cols-[36px_minmax(0,1fr)] gap-3 rounded-[12px] border-0 bg-transparent px-3 py-3 text-left transition-colors duration-150 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ion", !on && "hover:bg-card/70")}>
        {on && (
          <motion.span layoutId="ib-sel" className="absolute inset-0 rounded-[12px] border border-line bg-card shadow-card" transition={{ type: "spring", stiffness: 500, damping: 42 }}>
            <span className="absolute bottom-3 left-0 top-3 w-[3px] rounded-full bg-ion" />
          </motion.span>
        )}
        <Avatar name={avatarOf(x.a)} className="relative" />
        <span className="relative min-w-0">
          <span className="flex items-baseline gap-2">
            <b className="min-w-0 flex-1 truncate font-display text-[14.5px] font-semibold text-ink">{C(nameOf(x.a))}</b>
            {x.a.when && <span className="max-w-[45%] flex-none truncate font-mono text-[11px] text-mute">{C(x.a.when)}</span>}
          </span>
          <span className="mt-0.5 block truncate text-[13px] text-ink-2">{summary}</span>
          <span className="mt-1.5 flex items-center gap-2">
            <SrcIcon s={x.s} size={12} />
            {mine.length > 0 && <span className="inline-flex gap-[3px]" aria-hidden>{mine.map((m2, i) => <i key={i} className={cn("size-[7px] rounded-full", DOT[GROUP[m2.s]])} />)}</span>}
            {nNeed ? <span className="text-[11.5px] font-semibold text-warn">{nNeed} need{nNeed === 1 ? "s" : ""} you</span> : null}
          </span>
        </span>
      </motion.button>
    );
  }

  /* ---------- page ---------- */
  const m = WH.inboxModel();
  const open = m.actions.filter((x) => !WH.itemDone(x.r, x.a) && WH.openKeys(x.r, x.a).length);   // items whose own points are all ticked or all waiting drop out
  const done = m.actions.filter((x) => WH.itemDone(x.r, x.a));
  const fyi = [...m.answers.map((x) => ({ s: x.s, who: nameOf(x.g), when: x.g.when, title: x.g.title, pts: x.g.points || [] })), ...m.fyi.map((x) => ({ s: x.s, text: WH.ptText(x.f), pts: x.f.points || [], link: (x.f && typeof x.f === "object" && x.f.link) || "" }))] as any[];
  const counts = { todo: open.length, waiting: m.followups.length, fyi: fyi.length };
  const loaded = WH.store.inboxLoaded;
  const toks = q.toLowerCase().trim().split(/\s+/).filter(Boolean);
  const list = toks.length ? open.filter((x) => { const h = [x.a.who, x.a.where, x.a.title, x.a.text, ...(x.a.points || []).map((p) => WH.ptText(p))].join(" ").toLowerCase(); return toks.every((t) => h.includes(t)); }) : open;
  // The selected item; when it leaves the list (marked done), the one now in its place is selected.
  let idx = list.findIndex((x) => keyOf(x) === sel);
  if (idx < 0 && list.length) idx = Math.min(lastIdx.current, list.length - 1);
  if (idx >= 0) lastIdx.current = idx;
  const cur = idx >= 0 ? list[idx] : null, curKey = cur ? keyOf(cur) : "";
  listRef.current = list.map(keyOf); curRef.current = curKey;
  const showDetail = !wide && !!subItem && !!cur;
  shownRef.current = tab === "todo" && cur && (wide || showDetail) ? curKey : "";
  voiceRef.current = { sendSlack, postJira, replyText };

  const doneBlock = done.length > 0 && (
    <div className="mt-3" data-list="done">
      <Disclosure className="px-1" summary={`Done (${done.length})`}>
        <div className="mt-2 grid">
          <AnimatePresence initial={false}>
            {done.map((x) => {
              const rb = (WH.workFor(x.r.id, x.a.id) || {}).replied_by_owner;
              return (
                <motion.div key={x.r.id + "|" + x.a.id} {...itemMotion} className="flex items-baseline gap-2.5 border-t border-line-2 px-1 py-2.5 text-[13.5px] text-mute">
                  <Check size={14} strokeWidth={1.8} className="flex-none translate-y-0.5 text-ok" aria-hidden />
                  <b className="font-semibold text-ink-2">{C(nameOf(x.a))}</b>
                  <span className="min-w-0 flex-1 truncate">{rb ? `You replied in ${rb.where === "email" ? "email" : rb.where === "jira" ? "Jira" : "Slack"}${rb.at ? " at " + WH.fmtWhen(new Date(rb.at)) : ""} · ` : ""}{C(x.a.title || WH.ptText((x.a.points || [])[0]) || x.a.text || "")}</span>
                  <PillButton onClick={() => WH.tickInbox(x.r.id, [...keysFor(x.a), `${x.a.id}.reply`], false)}>Undo</PillButton>
                </motion.div>
              );
            })}
          </AnimatePresence>
        </div>
      </Disclosure>
    </div>
  );
  const listCol = (
    <div className="min-w-0">
      <label className="relative mb-2 block">
        <Search size={15} strokeWidth={1.8} className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-mute" aria-hidden />
        <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Filter by name or text" aria-label="Filter items" autoComplete="off" spellCheck={false}
          className="h-10 w-full rounded-[10px] border border-line bg-card pl-9 pr-3 text-[14px] text-ink shadow-xs outline-none transition-[border-color,box-shadow] duration-150 placeholder:text-mute focus:border-ion focus:shadow-[0_0_0_3px_var(--ion-soft)]" />
      </label>
      <div className="grid gap-0.5" data-list="todo">
        <AnimatePresence initial={false} mode="popLayout">{list.map((x) => row(x, wide && keyOf(x) === curKey))}</AnimatePresence>
      </div>
      {!list.length && <Empty bare icon={Search} tone="mute" className="py-8" title="No item matches">Try another word, or clear the filter.</Empty>}
      {wide && <p className="mt-2 px-3 font-mono text-[11px] text-mute">j / k to move</p>}
      {doneBlock}
    </div>
  );
  const feedCard = (key, i, name, s, when, body, srcName?) => (
    <motion.div key={key} initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0, transition: { delay: Math.min(i, 8) * 0.03, duration: 0.22 } }}>
      <Card lift className="grid grid-cols-[36px_minmax(0,1fr)] gap-3.5 px-5 py-4 [overflow-wrap:anywhere] max-[560px]:px-4">
        <Avatar name={name || srcName} />
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-x-2.5 gap-y-1">
            {name && <b className="font-display text-[15.5px] font-semibold">{name}</b>}
            <SrcBadge s={s} />
            {when && <span className="ml-auto font-mono text-[12px] text-mute">{when}</span>}
          </div>
          {body}
        </div>
      </Card>
    </motion.div>
  );

  return (
    <div className="grid w-full grid-cols-[minmax(0,1fr)] gap-6">
      <PageHeader kicker="Inbox" title="Waiting on you" art="/media/screens/inbox.jpg" film="/media/screens/inbox.mp4" sub={<span className="flex items-start gap-2 text-[14px] text-mute"><i className={cn("mt-[7px] size-2 flex-none rounded-full bg-ok", WH.workRunning() && "live-dot text-ok")} /><span>{status}</span></span>} />
      {(WH.store.inbox.slack_leftovers || []).length > 0 && (
        <div className="grid gap-1.5 rounded-[12px] border border-line bg-card px-3.5 py-3 text-[13.5px] text-ink-2" data-leftovers>
          <b className="font-display text-[14px] font-semibold text-ink">Old drafts to delete in Slack</b>
          <span className="text-mute">These replies are sent, merged or already answered by you, but their Slack draft is still in your Drafts (Slack cannot delete a draft without sending it). Delete each one in Slack, then press Deleted.</span>
          {(WH.store.inbox.slack_leftovers || []).map((x: any) => (
            <div key={x.rid + x.aid} className="flex flex-wrap items-center gap-2">
              <span className="min-w-0">{C(x.to)}{x.where ? ` · ${C(x.where)}` : ""} <span className="text-mute">({x.why})</span>{x.start && <span className="block text-[12.5px] text-mute">"{C(x.start)}…"</span>}</span>
              <Button size="sm" variant="ghost" asChild><a href={x.link} target="_blank" rel="noopener">Open in Slack</a></Button>
              <Button size="sm" onClick={async () => { await WH.api("/api/slack_leftover_done", { rid: x.rid, aid: x.aid }); WH.loadInbox(); }}>{ic(Check, 13)}Deleted</Button>
            </div>
          ))}
        </div>
      )}
      <Tabs value={tab} onValueChange={(v) => WH.go("inbox/" + v)} className="grid gap-5">
        <TabsList aria-label="Inbox sections" className="w-fit">
          {TABS.map(([k, l]) => (
            <TabsTrigger key={k} value={k} data-tab={k}>
              {k === tab && <motion.span layoutId="ib-tab" className="absolute inset-0 -z-10 rounded-[9px] bg-card shadow-[0_1px_2px_rgba(1,20,26,.1)] ring-1 ring-line" transition={{ type: "spring", stiffness: 500, damping: 40 }} />}
              {l} <span className={cn("rounded-full px-[7px] py-px font-mono text-[11.5px] font-medium leading-[1.4]", k === tab ? "bg-ion-soft text-ion" : "bg-card/70 text-mute")} data-count>{counts[k]}</span>
            </TabsTrigger>
          ))}
        </TabsList>
        <TabsContent value="todo">
          {!loaded ? <SkeletonList rows={4} /> : !open.length ? (<>
            <Empty art="/media/screens/rest.jpg" icon={Coffee} tone="ok" title="Nothing waiting on you">New items appear here after each automatic update.</Empty>
            {doneBlock}
          </>) : wide ? (
            <div className="grid grid-cols-[minmax(300px,370px)_minmax(0,1fr)] items-start gap-5">
              {listCol}
              <div className="pane sticky top-[64px] -m-2 max-h-[calc(100dvh-72px)] overflow-y-auto p-2">
                {cur ? itemCard(cur.r, cur.a, cur.s) : <Empty icon={MousePointerClick} title="Pick an item">Select an item on the left to see Claude's work and the reply.</Empty>}
              </div>
            </div>
          ) : showDetail ? (
            <div className="grid gap-3">
              <Button variant="ghost" size="sm" className="w-fit" onClick={() => WH.go("inbox/todo")}><ArrowLeft size={15} strokeWidth={1.8} />All items</Button>
              {itemCard(cur!.r, cur!.a, cur!.s)}
            </div>
          ) : listCol}
        </TabsContent>
        <TabsContent value="waiting">
          {!loaded ? <SkeletonList rows={3} /> : m.followups.length ? (
            <div className="grid gap-2.5">{m.followups.map((x, i) => feedCard("w" + i, i, C(nameOf(x.a)), x.s, x.a.when && C(x.a.when),
              <p className="mb-0 mt-1.5 max-w-[92ch] text-[14.5px] text-ink-2">{WH.ptText(x.p)}</p>))}</div>
          ) : <Empty icon={Users} tone="ok" title="Nobody owes you anything right now">Things others promised you show up here.</Empty>}
        </TabsContent>
        <TabsContent value="fyi">
          {!loaded ? <SkeletonList rows={3} /> : fyi.length ? (
            <div className="grid gap-2.5">{fyi.map((f, i) => feedCard("f" + i, i, f.who ? C(f.title || f.who) : "", f.s, f.when && C(f.when), (<>
              {f.text && <p className="mb-0 mt-1.5 max-w-[92ch] text-[14.5px] text-ink-2">{f.text}</p>}
              {f.s === "bitbucket" && /^https:\/\/bitbucket\.org\//.test(String(f.link || "")) && <a href={f.link} target="_blank" rel="noopener" className="mt-1 inline-block text-[13px] text-ink-2 underline decoration-dotted underline-offset-2 hover:text-ink">Open in Bitbucket</a>}
              {f.pts.length > 0 && <ul className="mb-0 mt-1.5 grid gap-1 pl-[18px] text-[14px] text-ink-2 marker:text-mute">{f.pts.map((p, j) => <li key={j}>{WH.ptText(p)}</li>)}</ul>}
            </>), WH.SRC[f.s]))}</div>
          ) : <Empty icon={InboxIcon} tone="mute" title="Nothing to note" />}
        </TabsContent>
      </Tabs>
    </div>
  );
}
