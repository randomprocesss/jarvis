/* Jarvis runtime: a port of static/core.js for the React app. Same store, API calls, inbox model and text rules.
   React views subscribe to store events with useWH(['inbox', ...]). Nothing here touches the DOM except theme. */
"use client";
import { useEffect, useState } from "react";
import { toast as sonner } from "sonner";

export const WH: any = {};
const isBrowser = typeof window !== "undefined";

/* ---------- small helpers ---------- */
WH.clone = (o) => JSON.parse(JSON.stringify(o));
WH.iso = (d: Date) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
WH.D = (s: string) => new Date(s + "T00:00");
WH.mondayOf = (d) => { const x = new Date(d); x.setDate(x.getDate() - ((x.getDay() + 6) % 7)); return x; };
WH.MON = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
WH.days = (mon = WH.store.weekMon) => { const m = WH.D(mon); return [0, 1, 2, 3, 4].map((i) => { const d = new Date(m); d.setDate(m.getDate() + i); return WH.iso(d); }); };
WH.dname = (d) => WH.D(d).toLocaleDateString("en-GB", { weekday: "short" });
WH.dshort = (d) => `${WH.D(d).getDate()} ${WH.MON[WH.D(d).getMonth()]}`;
WH.dlong = (d) => WH.D(d).toLocaleDateString("en-GB", { weekday: "long", day: "numeric", month: "long" });
WH.isoWeek = (s) => { const d = WH.D(s), t: any = new Date(Date.UTC(d.getFullYear(), d.getMonth(), d.getDate())), n = t.getUTCDay() || 7; t.setUTCDate(t.getUTCDate() + 4 - n); const y: any = new Date(Date.UTC(t.getUTCFullYear(), 0, 1)); return Math.ceil(((t - y) / 864e5 + 1) / 7); };
WH.weekText = (mon = WH.store.weekMon) => { const ds = WH.days(mon), a = WH.D(ds[0]), b = WH.D(ds[4]); return `${a.getDate()}${a.getMonth() !== b.getMonth() ? " " + WH.MON[a.getMonth()] : ""} – ${b.getDate()} ${WH.MON[b.getMonth()]}`; };
WH.h1 = (x) => { const v = Math.round((+x || 0) * 10) / 10; return Number.isInteger(v) ? String(v) : v.toFixed(1); };
WH.plural = (n, one, many?) => `${n} ${n === 1 ? one : many || one + "s"}`;
const store = (k: string) => { try { return localStorage.getItem(k); } catch { return null; } };
const persist = (k: string, v: any) => { try { if (v == null) localStorage.removeItem(k); else localStorage.setItem(k, v); } catch {} };
WH.pref = { get: store, set: persist };

/* ---------- api ---------- */
WH.api = async (url: string, body?: any) => {
  const key = (window as any).jarvisApp?.key;          // the page key: only the Jarvis app window has it (sends, approvals, writes)
  const r = await fetch(url, body === undefined ? {} : { method: "POST", headers: { "Content-Type": "application/json", ...(key ? { "X-Jarvis-Key": key } : {}) }, body: JSON.stringify(body) });
  let j: any;
  try { j = await r.json(); } catch { j = { error: "bad response" }; }
  if (!r.ok && !j.error) j.error = "HTTP " + r.status;
  return j;
};

/* ---------- event bus ---------- */
const subs: Record<string, Set<(d?: any) => void>> = {};
WH.on = (e: string, f) => { (subs[e] = subs[e] || new Set()).add(f); return () => { subs[e].delete(f); }; };
WH.emit = (e: string, d?: any) => { (subs[e] || []).forEach((f) => { try { f(d); } catch (err) { console.error(err); } }); };

/** Re-render the calling component whenever any of these store events fire. */
export function useWH(events: string[]) {
  const [, bump] = useState(0);
  useEffect(() => {
    const offs = events.map((e) => WH.on(e, () => bump((x) => x + 1)));
    return () => offs.forEach((f) => f());
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [events.join(",")]);
}

/* ---------- toasts, dialogs, clipboard, log ---------- */
WH.toast = (msg: string, type = "info", ms = 3800) => {
  const o = { duration: ms };
  if (type === "ok") sonner.success(msg, o); else if (type === "err") sonner.error(msg, o); else sonner.info(msg, o);
};
let askHandler: ((req: any) => void) | null = null;
WH.setAskHandler = (f) => { askHandler = f; };
WH.ask = (title: string, body?: string, yes = "Continue", danger = false) => new Promise<boolean>((res) => {
  if (askHandler) askHandler({ title, body: body || "", yes, danger, res });
  else res(window.confirm(title + "\n\n" + (body || "")));
});
WH.copyText = async (t: string) => { try { await navigator.clipboard.writeText(t); return true; } catch { return false; } };
WH.copy = async (t: string, what = "Copied.") => { const ok = await WH.copyText(t); WH.toast(ok ? what : "Could not copy. Check clipboard permissions.", ok ? "ok" : "err", 2600); return ok; };
WH.logs = [] as any[];
WH.log = (title: string, text: any) => {
  const ts = new Date().toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit", second: "2-digit" });
  WH.logs.unshift({ ts, title, text: String(text || "").trim() }); WH.logs = WH.logs.slice(0, 60);
};

/* ---------- who Jarvis works for (GET /api/me): their name, where their tickets and chats open, what is switched on ---------- */
WH.me = { set_up: false, name: "", first: "", called: "", ticket_base: "", slack_workspace: "", projects: [], kinds: {}, apps: {}, share: false, modules: [], media: false };
WH.loadMe = async () => { try { const d = await WH.api("/api/me"); if (d && !d.error) { WH.me = { ...WH.me, ...d, loaded: true }; WH.emit("me"); } } catch {} };
/** Is this name the owner's own (their records say "You", their name or their first name)? */
WH.isMe = (who) => { const w = String(who || "").trim().toLowerCase(); return !!w && (w === "you" || w === "owner" || w === "the owner" || (!!WH.me.name && (w === WH.me.name.toLowerCase() || w === WH.me.first.toLowerCase()))); };
/** "Good morning, Sam" once the name is known; "Good morning" before. */
WH.hello = (d: Date = new Date()) => WH.greeting(d) + (WH.me.first ? ", " + WH.me.first : "");
/** Where a ticket opens, or "" when no tracker address is set. */
WH.ticketUrl = (key) => (WH.me.ticket_base && key ? WH.me.ticket_base.replace(/\/?$/, "/") + encodeURIComponent(String(key)) : "");

/* ---------- stores ---------- */
const defaultMon = () => { const t = new Date(), m = WH.mondayOf(t); if ((t.getDay() + 6) % 7 < 3) m.setDate(m.getDate() - 7); return WH.iso(m); };
WH.store = {
  inbox: { roundups: [], state: {} }, inboxLoaded: false,
  tickets: { items: [], mtime: 0 }, ticketsLoaded: false,
  week: null,
  weekMon: (() => { const v = isBrowser ? store("wh_mon") : null; return v && /^\d{4}-\d{2}-\d{2}$/.test(v) ? WH.iso(WH.mondayOf(WH.D(v))) : defaultMon(); })(),
  approvals: { items: [], mtime: 0, executor: false },
};
WH.setWeekMon = (m) => { WH.store.weekMon = m; persist("wh_mon", m); WH.emit("weekmon", m); };
WH.thisMonday = () => WH.iso(WH.mondayOf(new Date()));
// Fix over core.js: keep work_last so "Claude is working on items now" can show.
WH.loadInbox = async () => {
  try {
    const d = await WH.api("/api/inbox");
    if (d && Array.isArray(d.roundups)) {
      // 19 Oct ("I dont like the way it writes You(Jarvis)"): older items stored "You (Jarvis)" / "Jarvis (noticed)"; every view
      // shows them as "You" / "Jarvis" (the source chip says Jarvis already)
      for (const r of d.roundups) for (const a of (r && r.actions) || []) {
        if (a && /^you \(jarvis\)$/i.test(String(a.who || ""))) a.who = "You";
        else if (a && /^jarvis \(noticed\)$/i.test(String(a.who || ""))) a.who = "Jarvis";
      }
      WH.store.inbox = { roundups: d.roundups, state: d.state || {}, schedule: d.schedule || null, last: d.last || null, work_last: d.work_last || null, work: d.work || {}, style_at: d.style_at || null, answers: d.answers || {}, groups: d.groups || [], slack_leftovers: d.slack_leftovers || [], task_alerts: d.task_alerts || [] , jarvis_tasks: d.jarvis_tasks || {} };
      WH.store.inboxLoaded = true; WH.emit("inbox");
    }
  } catch {}
};
WH.tickInbox = async (rid, keys, done) => {
  keys = (keys || []).filter(Boolean); if (!rid || !keys.length) return;
  // On something Jarvis did for him, marking a point done (Mark done, the point's tick) also acknowledges the result, and Undo
  // brings it back (6 Oct: "things I have marked done still show up": the worker ticks its own points when it finishes, so
  // his Mark done re-sent the same tick and only "Got it" (<aid>.seen) cleared the item)
  const rr = (WH.store.inbox.roundups || []).find((x: any) => x.id === rid);
  if (rr && rr.kind === "jarvis") {
    const acts = new Set(keys.filter((k: string) => !/\.(seen|reply)$/.test(k)).map((k: string) => k.split(".")[0]));
    acts.forEach((aid) => { if (!keys.includes(`${aid}.seen`)) keys = [...keys, `${aid}.seen`]; });
  }
  const st = WH.store.inbox.state; st[rid] = st[rid] || {}; keys.forEach((k) => { if (done) st[rid][k] = true; else delete st[rid][k]; }); WH.emit("inbox");
  try { const r = await WH.api("/api/inbox_tick", { rid, keys, done }); if (r.state) { WH.store.inbox.state = r.state; WH.emit("inbox"); return true; } else throw new Error(r.error); }
  catch { WH.toast("Could not save that tick. Is Jarvis running?", "err"); return false; }
};
WH.loadTickets = async () => {
  try {
    const d = await WH.api("/api/tickets");
    if (d && Array.isArray(d.items)) { const changed = d.mtime !== WH.store.tickets.mtime || !WH.store.ticketsLoaded; WH.store.tickets = { items: d.items, mtime: d.mtime }; WH.store.ticketsLoaded = true; if (changed) WH.emit("tickets"); }
  } catch {}
};
WH.saveTickets = async (items) => {
  const r = await WH.api("/api/tickets_save", { items });
  if (r.error) { WH.toast("Could not save tickets: " + r.error, "err", 5000); return false; }
  WH.store.tickets = { items: r.items, mtime: r.mtime }; WH.store.ticketsLoaded = true; WH.emit("tickets"); return true;
};
WH.loadApprovals = async () => {
  try {
    const d = await WH.api("/api/approvals");
    if (d && Array.isArray(d.items) && (d.mtime !== WH.store.approvals.mtime || d.executor !== WH.store.approvals.executor)) { WH.store.approvals = d; WH.emit("approvals"); }
  } catch {}
};
WH.setApproval = async (id, status, choice?) => { const r = await WH.api("/api/approvals_set", { id, status, choice }); if (r.error) { WH.toast(r.error, "err"); return false; } WH.store.approvals = r; WH.emit("approvals"); return true; };
WH.addApproval = async (item) => { const r = await WH.api("/api/approvals_add", { item }); if (r.error) { WH.toast(r.error, "err"); return false; } WH.store.approvals = r; WH.emit("approvals"); return true; };
WH.saveAnswer = async (rid, aid, text, parts?) => { const r = await WH.api("/api/answer", { rid, aid, text, ...(parts ? { parts } : {}) }); if (r.error) { WH.toast(r.error, "err"); return false; } WH.store.inbox.answers = r.answers; WH.emit("inbox"); return r; };
WH.answerFor = (rid, aid) => (WH.store.inbox.answers || {})[rid + "|" + aid] || null;
WH.pendingApprovals = () => WH.store.approvals.items.filter((a) => a.status === "pending");
/* Honest timing text for approved work. */
WH.whenApproved = () => WH.store.approvals.executor ? "Claude is doing it now and updates the item when it's done." : 'Waiting: the "do what I approved" task is not set up yet, so nothing runs on its own.';
WH.approvalsFor = (rid, aid) => WH.store.approvals.items.filter((a) => a.related && a.related.rid === rid && a.related.aid === aid);

/* ---------- inbox model (shared by home, inbox, tickets) ---------- */
WH.SRC = { slack: "Slack", email: "Email", teams: "Teams", calendar: "Calendar", meeting: "Meeting", jira: "Jira", confluence: "Confluence", bitbucket: "Bitbucket", jarvis: "Jarvis", other: "Other" };
WH.srcOf = (x, r) => { const s = (x && typeof x === "object" && x.src) || (r.kind && r.kind !== "mixed" ? r.kind : "") || "other"; return WH.SRC[s] ? s : "other"; };
// The org's own name never shows in the app: "the <org> forecast" reads as "our forecast". Email domains are left alone.
/* The presenter (HARNESS.md §6.7, Phase 3): what is SHOWN never carries the org's name, its email domains, invisible
   (zero-width / direction) characters or a mail disclaimer tail. Display only: sent text is never passed through this. */
// The patterns come from the server (/presenter.js, state/presenter.json), so this source names no employer. Built on first
// use (Next's own scripts may run before presenter.js); until they arrive nothing is cached, so the next call tries again.
const esc = (x: string) => x.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
let PAT: { mail: RegExp; addr: RegExp; name: RegExp } | null = null;
function pats() {
  if (PAT) return PAT;
  const P = typeof window !== "undefined" ? (window as any).__WH_PRESENTER : null;
  const DOM = ((P && P.domains) || []).map(esc).join("|") || "(?!x)x", WORDS = ((P && P.words) || []).map(esc).join("|") || "(?!x)x";
  const p = { mail: new RegExp(`\\s*<[^<>\\s@]+@(?:${DOM})>`, "gi"), addr: new RegExp(`\\b([\\w.+-]+)@(?:${DOM})\\b`, "gi"),
              name: new RegExp(`(?<![@A-Za-z0-9.])(the\\s+)?(?:${WORDS})(['’]s)?(?![A-Za-z0-9])(?!\\.\\w)`, "gi") };   // not \\b: "X_Notes" (6 Oct)
  if (P) PAT = p;
  return p;
}
const INVISIBLE = /[\u200B-\u200F\u202A-\u202E\u2060-\u2064\uFEFF]/g;
const DISCLAIMER = /\n\s*(?:CONFIDENTIALITY NOTICE|DISCLAIMER|This (?:e-?mail|message)(?: and any attachments)? (?:is|are|may (?:be|contain))(?: strictly)? (?:confidential|privileged))[\s\S]*$/i;
WH.clean = (s) => {
  const P = pats();
  let t = String(s ?? "").replace(INVISIBLE, "").replace(P.mail, "").replace(P.addr, "$1");
  if (t.length > 200) t = t.replace(DISCLAIMER, "");
  return t.replace(P.name, (m, the) => (the && the[0] === "T" ? "Our" : "our"));
};
WH.ptText = (p) => WH.clean(typeof p === "string" ? p : (p && p.text) || "");
WH.isWait = (p) => typeof p === "object" && !!p && !!p.waiting;
WH.ticked = (rid, k) => !!WH.store.inbox.state?.[rid]?.[k];
/* The same ask from different places is ONE task (groups.json, 2 Oct): a member point shows "Same as …" and is worked under its lead. */
WH.groupOf = (rid, k) => (WH.store.inbox.groups || []).find((g: any) => g.lead === rid + "|" + k || (g.members || []).includes(rid + "|" + k)) || null;
/** The lead's place when this point is a copy of an ask tracked elsewhere (only if that lead is loaded), else null. */
WH.sameAs = (rid, k) => {
  const g = WH.groupOf(rid, k); if (!g || g.lead === rid + "|" + k) return null;
  const [lr, lk] = g.lead.split("|"), r = (WH.store.inbox.roundups || []).find((x: any) => x.id === lr), a = r && (r.actions || []).find((x: any) => x.id === lk.split(".")[0]);
  return a ? { g, rid: lr, key: lk, aid: a.id, a, r } : null;
};
/** For a lead point: where else the same ask came in (who, where, when, title). */
WH.alsoAsked = (rid, k) => {
  const g = WH.groupOf(rid, k); if (!g || g.lead !== rid + "|" + k) return [];
  return (g.members || []).map((m: string) => { const [mr, mk] = m.split("|"), r = (WH.store.inbox.roundups || []).find((x: any) => x.id === mr), a = r && (r.actions || []).find((x: any) => x.id === mk.split(".")[0]); return a ? { rid: mr, key: mk, a, r } : null; }).filter(Boolean);
};
/* What he asked Jarvis for stays in To do with its result until he has seen it (5 Oct: "it showed for a moment in To do and
   vanished": the worker ticks its own points when it finishes). "Got it" ticks the extra key <aid>.seen. */
WH.resultUnseen = (r, a) => {
  const w = WH.workFor(r.id, a.id);
  if (r.kind !== "jarvis" || !w || WH.ticked(r.id, `${a.id}.seen`)) return false;
  const rp = w.reply || {};
  if (rp.sent_at || rp.posted_at || w.replied_by_owner || w.status === "waiting") return false;   // 2 Nov: he sent it (so he saw it), or it waits on someone
  const at = Date.parse(w.updated || r.window_end || "");
  return !at || Date.now() - at < 48 * 3600e3;          // results from the last two days; older ones are not brought back
};
WH.openKeys = (r, a) => {
  const pts = a.points || [];
  const keys = !pts.length ? (a.waiting || WH.ticked(r.id, a.id) || WH.sameAs(r.id, a.id) ? [] : [a.id])
    : pts.map((p, i) => (WH.isWait(p) || WH.ticked(r.id, `${a.id}.${i}`) || WH.sameAs(r.id, `${a.id}.${i}`) ? null : `${a.id}.${i}`)).filter(Boolean);
  const w = WH.workFor(r.id, a.id), rp = (w && w.reply) || {};
  const unsent = rp.channel === "slack" && String(rp.text || "").trim() && !rp.sent_at && !w.merged_into && !rp.dropped && !w.replied_by_owner && !WH.ticked(r.id, `${a.id}.reply`);
  const k2 = unsent && keys.length === 0 ? [`${a.id}.reply`] : keys;           // an unsent reply keeps the item in To do
  return WH.resultUnseen(r, a) ? [...k2, `${a.id}.seen`] : k2;
};
WH.itemDone = (r, a) => { const pts = a.points || []; if (!pts.length) return WH.ticked(r.id, a.id); const own = pts.filter((p) => !WH.isWait(p)); return own.length > 0 && WH.openKeys(r, a).length === 0; };
/* Next automatic inbox update from the schedule {times:['09:00',...], days:'Mon–Fri'} -> Date or null. */
WH.nextRoundup = () => {
  const sc = WH.store.inbox.schedule; if (!sc || !Array.isArray(sc.times)) return null;
  const now = new Date();
  for (let d = 0; d < 8; d++) {
    const day = new Date(now); day.setDate(now.getDate() + d); const wd = day.getDay(); if (wd === 0 || wd === 6) continue;
    for (const t of sc.times) { const [h, m] = t.split(":").map(Number); const x = new Date(day); x.setHours(h, m, 0, 0); if (x > now) return x; }
  }
  return null;
};
/* Next work run (Claude works on the items at half past) and whether one is running now (work_last.json). */
/* 5 Oct: the clock routines are retired (the spine starts work when items arrive), so there is no "next run" to show. */
WH.nextWork = () => null as Date | null;
WH._nextWorkClock = () => { const sc = WH.store.inbox.schedule; if (!sc || !Array.isArray(sc.work_times)) return WH.nextRoundup(); const keep = sc.times; sc.times = sc.work_times; try { return WH.nextRoundup(); } finally { sc.times = keep; } };
WH.workRunning = () => { const w = WH.store.inbox.work_last; if (!w || !w.started_at) return false; const s: any = new Date(w.started_at), f = w.finished_at ? new Date(w.finished_at) : null; return (!f || f < s) && Date.now() - s < 120 * 60e3; };
/* Newest check time: roundup_last.checked_at or the newest file's window_end. */
WH.lastRoundup = () => {
  const ts = [WH.store.inbox.last && WH.store.inbox.last.checked_at, ...(WH.store.inbox.roundups || []).map((r) => r.window_end)].filter(Boolean).map((t) => new Date(t)).filter((d: any) => !isNaN(d));
  return ts.length ? new Date(Math.max(...ts.map(Number))) : null;
};
WH.fmtWhen = (d) => { if (!d) return ""; const now = new Date(), t = d.toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit" }); const sameDay = d.toDateString() === now.toDateString(); const tmr = new Date(now); tmr.setDate(now.getDate() + 1); if (sameDay) return t; if (d.toDateString() === tmr.toDateString()) return "tomorrow " + t; /* 29 Oct: a weekday alone is only clear within the week ("Tue 18:45" was really 28 Jul) */ if (Math.abs(now.getTime() - d.getTime()) < 6 * 864e5) return d.toLocaleDateString("en-GB", { weekday: "short" }) + " " + t; return d.toLocaleDateString("en-GB", { day: "numeric", month: "short", ...(d.getFullYear() !== now.getFullYear() ? { year: "numeric" } : {}) }); };
/* What Claude prepared for an action (work/<rid>.json[aid]) or null. */
WH.workFor = (rid, aid) => (WH.store.inbox.work?.[rid] || {})[aid] || null;
/* One-click ways to send a prepared reply yourself: returns {label, href} or null. Nothing is sent by the app. */
WH.replyLink = (w) => {
  const r = w && w.reply; if (!r || !r.text) return null;
  if (r.channel === "slack") {
    const host = ((r.slack_link || "").match(/^https:\/\/[a-z0-9-]+\.slack\.com/) || [WH.me.slack_workspace || ""])[0];
    const href = r.channel_id && host ? `${host}/archives/${r.channel_id}` + (r.thread_ts ? `/p${String(r.thread_ts).replace(".", "")}?thread_ts=${r.thread_ts}&cid=${r.channel_id}` : "") : r.slack_link || "slack://open";
    return { label: r.slack_draft === "created" ? "Open the draft in Slack" : "Open Slack", href };
  }
  if (r.channel === "email" && r.email_to) return { label: "Open reply in Outlook", href: `mailto:${encodeURIComponent(r.email_to)}?subject=${encodeURIComponent(r.subject || "")}&body=${encodeURIComponent(r.text)}` };
  if (r.channel === "teams" && r.teams_to) return { label: "Open in Teams", href: `https://teams.microsoft.com/l/chat/0/0?users=${encodeURIComponent(r.teams_to)}&message=${encodeURIComponent(r.text)}` };
  return null;
};
WH.inboxModel = () => {
  const cut = new Date(); cut.setDate(cut.getDate() - 7); const c = WH.iso(cut);
  const all = WH.store.inbox.roundups || [], files = all.filter((r) => !r.error && String(r.created || r.id).slice(0, 10) >= c), bad = all.filter((r) => r.error);
  const actions: any[] = [], followups: any[] = [], answers: any[] = [], fyi: any[] = [], people: any = {};
  // Calendar events never show in Jarvis, even if a round-up still carries one.
  const noCal = (x, r) => WH.srcOf(x, r) !== "calendar";
  files.forEach((r) => {
    (r.actions || []).filter((a) => noCal(a, r)).forEach((a) => {
      const s = WH.srcOf(a, r); actions.push({ r, a, s });
      const open = WH.openKeys(r, a).length; if (open) people[a.who || "—"] = (people[a.who || "—"] || 0) + open;
      (a.points || []).forEach((p, i) => { if (WH.isWait(p)) followups.push({ r, a, p, i, s, key: `${a.id}.${i}` }); });
      if (!(a.points || []).length && a.waiting) followups.push({ r, a, p: { text: a.text || "" }, i: null, s, key: a.id });
    });
    (r.answers || []).filter((g) => noCal(g, r)).forEach((g) => answers.push({ r, g, s: WH.srcOf(g, r) }));
    (r.fyi || []).filter((f) => noCal(f, r)).forEach((f) => fyi.push({ r, f, s: WH.srcOf(f, r) }));
  });
  const open = actions.reduce((n, x) => n + WH.openKeys(x.r, x.a).length, 0);
  const bySource: any = {}; actions.forEach((x) => { const n = WH.openKeys(x.r, x.a).length; if (n) bySource[x.s] = (bySource[x.s] || 0) + n; });
  const skipped: any[] = []; files.forEach((r) => Object.entries(r.sources_checked || {}).forEach(([k, v]) => { if (!String(v).startsWith("ok")) skipped.push({ src: k, why: String(v).replace(/^skipped:\s*/, "") }); }));
  return { files, bad, actions, followups, answers, fyi, open, people, bySource, skipped };
};
/* Open items (not done, with at least one open point) — the Inbox "To do" list and its count. */
WH.openItems = () => WH.inboxModel().actions.filter((x) => !WH.itemDone(x.r, x.a) && WH.openKeys(x.r, x.a).length);

/* ---------- ticket proposals ---------- */
WH.ticketIdFor = (rid, key) => ("t-" + rid + "-" + key).replace(/[^A-Za-z0-9._-]/g, "-").slice(0, 80);
WH.ticketFor = (rid, key) => WH.store.tickets.items.find((t) => t.id === WH.ticketIdFor(rid, key));
const firstSentence = (t) => { const s = String(t || "").replace(/\s+/g, " ").trim(); const m = s.match(/^(.{20,}?[.?!])(\s|$)/); let o = m ? m[1] : s; if (o.length > 90) o = o.slice(0, 88).replace(/\s+\S*$/, "") + "…"; return o.replace(/[.]$/, ""); };
/* picks: [{r, a, i}] where i = point index, or null for the whole item. Returns created-or-existing ids. */
WH.proposeTickets = async (picks) => {
  const items = WH.clone(WH.store.tickets.items), ids: string[] = [], now = new Date().toISOString(); let added = 0;
  picks.forEach(({ r, a, i }) => {
    const key = i == null ? a.id : `${a.id}.${i}`, id = WH.ticketIdFor(r.id, key);
    ids.push(id); if (items.some((t) => t.id === id)) return;
    const p = i == null ? null : a.points[i];
    const text = i == null ? a.text || (a.points || []).filter((q) => !WH.isWait(q)).map(WH.ptText).join(" ") : WH.ptText(p);
    const note = i == null ? a.note : p && p.note;
    const s = WH.srcOf(a, r);
    items.push({
      id, status: "draft", key: null, created_at: null, updated: now,
      summary: firstSentence(text), type: "Task", parent: "", link: "", estimate_h: null,
      description: text + (note ? "\n\nContext: " + note : "") + `\n\nRaised by ${a.who || "someone"}${a.where ? " in " + a.where : ""}${a.when ? " (" + a.when + ")" : ""} via ${WH.SRC[s]}.`,
      source: { rid: r.id, key, src: s, who: a.who || "", where: a.where || "", when: a.when || "", text },
    }); added++;
  });
  if (added && !(await WH.saveTickets(items))) return [];
  return ids;
};

/* ---------- theme: a saved "dark" choice is applied on load ---------- */
// 12 Nov (redesign): the Mac's appearance decides (layout.tsx sets it before the first paint and follows changes).
WH.applyTheme = () => { try { const dark = matchMedia("(prefers-color-scheme: dark)").matches; if (dark) document.documentElement.dataset.theme = "dark"; else delete document.documentElement.dataset.theme; } catch {} WH.emit("theme"); };
if (isBrowser) { try { matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => WH.emit("theme")); } catch {} }

/* ---------- direct actions (the tracker) ---------- */
WH.jira = { status: null };
WH.loadJiraStatus = async () => { try { WH.jira.status = await WH.api("/api/jira/status"); } catch { WH.jira.status = { ok: false, configured: false, error: "Jarvis server not reachable" }; } WH.emit("jira", WH.jira.status); return WH.jira.status; };
WH.actionError = (r) => {
  if (r && r.needs_token) { WH.toast('Jira did not accept the request, so Claude will do this step through the Atlassian connector instead.', "info", 6000); WH.loadJiraStatus(); return; }
  WH.toast((r && (r.error || r.out)) || "Something went wrong. Details are in the activity log (⌘K).", "err", 6500);
};
/* Dry run -> show exactly what will happen -> confirm -> run. opts: {title, yes, describe(dryResult) -> text or '' (nothing to do), empty, done(result) -> toast text} */
WH.confirmRun = async (url, body, opts: any = {}) => {
  let dry: any;
  try { dry = await WH.api(url, { ...body, dry: true }); } catch { dry = { ok: false, error: "Jarvis server not reachable" }; }
  if (!dry.ok) { WH.actionError(dry); return null; }
  const text = opts.describe ? opts.describe(dry) : "";
  if (!text) { WH.toast(opts.empty || "Nothing to do.", "info"); return null; }
  if (!(await WH.ask(opts.title || "Go ahead?", text, opts.yes || "Go ahead"))) return null;
  let res: any;
  try { res = await WH.api(url, { ...body, dry: false }); } catch { res = { ok: false, error: "Jarvis server not reachable" }; }
  WH.log(opts.title || url, JSON.stringify(res.result || res, null, 1).slice(0, 4000));
  if (!res.ok) { WH.actionError(res); return null; }
  if (opts.done) WH.toast(opts.done(res), "ok", 5000);
  return res;
};
WH.openOutput = async (path) => { const r = await WH.api("/api/open_output", { path }); if (!r.ok) WH.toast(r.out || "Could not open that file.", "err"); };

/* ---------- router + the current view's hooks into the shell (palette actions, ⌘S, dirty guard, status pill) ---------- */
export const VIEWS: Record<string, { title: string }> = { home: { title: "Home" }, today: { title: "Today" }, inbox: { title: "Inbox" }, activity: { title: "Activity" }, brain: { title: "Brain" }, trust: { title: "Trust" },
  tickets: { title: "Jira tickets" }, office: { title: "Office" }, setup: { title: "Setup" },
  deploy: { title: "Deploy gate" } };   // an optional part: its screen says so when settings have it switched off
// 12 Nov (redesign): the screens the old app had and the new one folded in, so an old link or a spoken "open watching" still lands.
const MOVED: Record<string, string> = { "": "home", watching: "brain/watching", learned: "trust/learned", research: "brain/research" };
WH.parse = () => {
  let h = ((isBrowser && location.hash) || "#/").replace(/^#\/?/, ""); let [name, ...rest] = h.split("/");
  if (!VIEWS[name]) { const to = (MOVED[name] ?? "home").split("/"); name = to[0]; rest = to.slice(1).concat(rest); }
  return { name, sub: rest.join("/") || "" };
};
WH.go = (path) => { location.hash = "#/" + String(path || "").replace(/^#?\/?/, ""); };
WH.shell = { dirty: false, save: null, actions: null, status: null };
WH.setStatus = (text, kind?) => { WH.shell.status = text ? { text, kind: kind || "" } : null; WH.emit("shell"); };
WH.resetShell = () => { WH.shell = { dirty: false, save: null, actions: null, status: null }; WH.emit("shell"); };
WH.focusItem = null as string | null;

/* Greeting by the time of day: morning 5–11, afternoon 12–16, evening 17–21, late night 22–4. */
WH.greeting = (d: Date = new Date()) => { const h = d.getHours(); return h >= 5 && h < 12 ? "Good morning" : h >= 12 && h < 17 ? "Good afternoon" : h >= 17 && h < 22 ? "Good evening" : "Working late"; };
