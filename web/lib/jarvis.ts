/* Jarvis voice, page side. One small store, the bridge to the Mac app (window.jarvisApp -> app, window.jarvisVoice <- app),
   the screen object the brain sees, ask() (instant fast path for navigation, else POST /api/jarvis and read the NDJSON
   stream), and the brain's actions: each change gets a spoken read-back and a 3-second cancel window, then runs through the
   same API calls as its button. In a normal browser there is no mic or speech: typing still works, answers show on screen. */
"use client";
import { createElement, useSyncExternalStore } from "react";
import { toast } from "sonner";
import { WH, VIEWS } from "@/lib/wh";

export type JTurn = { me?: boolean; text: string; err?: boolean; note?: boolean };
export type JPending = { label: string; ms: number; until: number | null; held: boolean; frac: number; n: number };
export type JState = {
  native: boolean; listening: boolean; awake: boolean; thinking: boolean; speaking: boolean;
  model: string; mic: string; speech: string; voice: string;
  caption: string; capKind: "" | "heard" | "say" | "info" | "err";
  conv: string | null; turns: JTurn[];
  panel: "" | "conv" | "history"; history: any[] | null; histPick: any | null;
  pending: JPending | null;
  voices: { id: string; label: string; detail?: string }[]; dialog: "" | "enrol" | "voices" | "stats";
  flags: Record<string, any>;
  /** office mode from the app (Office.swift): external display, quiet captions, headphones, lid closed, mic in use */
  office: { active?: boolean; quiet?: boolean; quiet_setting?: boolean; clamshell?: boolean; headphones?: boolean; input?: string; output?: string; no_mic?: boolean };
};

const W = (): any => (typeof window === "undefined" ? {} : window);
const CONV_MS = 10 * 60e3, WINDOW_MS = 3000;

let S: JState = {
  native: false, listening: false, awake: false, thinking: false, speaking: false,
  model: "hey_jarvis", mic: "undetermined", speech: "undetermined", voice: "",
  caption: "", capKind: "", conv: null, turns: [], panel: "", history: null, histPick: null,
  pending: null, voices: [], dialog: "", flags: {}, office: {},
};
const subs = new Set<() => void>();
let busyOn = false, pendOn = false;
function set(p: Partial<JState>) {
  S = { ...S, ...p };
  const b = S.thinking || !!S.pending;               // the app must not go to sleep while the brain thinks or an action waits
  // `pending`: an action waits for yes/cancel, so a "yeah" is an answer then, never a backchannel
  if (b !== busyOn || !!S.pending !== pendOn) { busyOn = b; pendOn = !!S.pending; post({ type: "busy", on: b, pending: pendOn }); }
  subs.forEach((f) => { try { f(); } catch (e) { console.error(e); } });
}
const sub = (f: () => void) => { subs.add(f); return () => { subs.delete(f); }; };
const get = () => S;
/** The whole state (re-renders on every change). */
export function useJarvis() { return useSyncExternalStore(sub, get, get); }
/** One primitive field (re-renders only when it changes). */
export function useJ<T extends string | number | boolean | null>(sel: (s: JState) => T): T { return useSyncExternalStore(sub, () => sel(S), () => sel(S)); }
export const jarvis = { get, set, sub };

/* ---------- bridge ---------- */
export function post(m: any) {
  const a = W().jarvisApp;
  if (!a || !a.native || typeof a.post !== "function") return;
  try { a.post(m); } catch (e) { console.warn("jarvis post failed", e); }
}
const evSubs = new Set<(ev: any) => void>();
/** Raw app events (enrolment, wake for "Test it"). */
export const onVoice = (f: (ev: any) => void) => { evSubs.add(f); return () => { evSubs.delete(f); }; };

/* ---------- speech + caption ---------- */
let sid = 0, lastId = "", readbackId = "", capT: any = 0, lastActive = 0, convT0 = 0;
const texts = new Map<string, string>();
const forEar = (t: string) => String(t || "").replace(/[*`#_]/g, "").replace(/…/g, ".").replace(/\s+/g, " ").trim();
/** The brain may add one sound tag (<chuckle>, <sigh>, <laugh>) for the expressive voice; never shown, and never sent to the
    system voices (they would read it out). The local helpers strip or use it themselves. */
const TAGS = /\s*<(chuckle|laugh|sigh|gasp|groan|happy|surprised|sarcastic|whispering|neutral|warm|amused|serious|calm|excited|sympathetic)>\s*/gi;
const MOODS = new Set(["neutral", "warm", "amused", "serious", "calm", "excited", "sympathetic"]);
/** The reply's mood (the brain's "mood"), as a leading tag the local voice uses to pick a matching delivery. */
const withMood = (t: string, mood?: string) => (mood && MOODS.has(mood) && mood !== "neutral" ? `<${mood}> ` + t : t);
// Screen text keeps its line breaks (5 Oct: the chat showed every list as one paragraph); speech flattens in forEar.
export const untag = (t: string) => String(t || "").replace(TAGS, " ").replace(/[ \t]+/g, " ").replace(/ *\n */g, "\n").replace(/\n{3,}/g, "\n\n").trim();
/** One answer shape: the spoken sentence leads, the screen text adds to it and never repeats it. */
export const answerText = (sp: string, text: string) => {
  const a = untag(sp), b = untag(text);
  if (!b) return a;
  if (!a) return b;
  const sig = (x: string) => x.toLowerCase().replace(/[^a-z0-9]/g, "").slice(0, 60);
  return sig(b).startsWith(sig(a).slice(0, 40)) ? b : a + "\n\n" + b;
};
/** What a spoken line is for (the app times openers/fillers and measures each turn by it). */
export type SayKind = "opener" | "filler" | "answer" | "readback" | "other";
/** Queue one sentence in the app's voice; returns its id ("" in a browser). `turn` ties it to the voice turn it answers. */
export function say(text: string, kind: SayKind = "other", turn = "") {
  const t0 = forEar(text), t = S.voice.startsWith("apple:") ? untag(t0) : t0;
  if (!untag(t) || !S.native) return "";
  const id = "j" + ++sid; texts.set(id, untag(t)); lastId = id;
  post({ type: "speak", id, text: t, kind, ...(turn ? { turn } : {}) });
  return id;
}
const sentences = (t: string) => (forEar(t).match(/[^.!?]+[.!?]*(\s+|$)/g) || []).map((x) => x.trim()).filter(Boolean);
function sayAll(t: string, kind: SayKind = "other", turn = "", mood?: string) { let id = ""; sentences(t).forEach((s) => { id = say(withMood(s, mood), kind, turn) || id; }); return id; }
/** The voice turn being answered (from the app's "final"); "" for typed questions. Timings go back to the app's metrics. */
let curTurn = "", nextVoice: any = null, lastBarge = 0;
const mark = (turn: string, m: any) => { if (turn) post({ type: "turn", turn, ...m }); };
function caption(text: string, kind: JState["capKind"], ms = 0) {
  clearTimeout(capT);
  set({ caption: text, capKind: text ? kind : "" });
  if (ms) capT = setTimeout(() => { if (!S.speaking) set({ caption: "", capKind: "" }); }, ms);
}
const addTurn = (t: JTurn) => { lastActive = Date.now(); set({ turns: [...S.turns, t] }); };
const note = (text: string) => { if (S.panel === "conv") addTurn({ text, note: true }); };

/* ---------- events from the app ---------- */
let endAfter = false;
function onEvent(ev: any) {
  if (!ev || typeof ev !== "object") return;
  evSubs.forEach((f) => { try { f(ev); } catch (e) { console.error(e); } });
  switch (ev.type) {
    case "state":
      set({ listening: !!ev.listening, awake: !!ev.awake, speaking: !!ev.speaking, model: ev.model || S.model, mic: ev.mic || S.mic, speech: ev.speech || S.speech, voice: ev.voice || S.voice, flags: ev.flags && typeof ev.flags === "object" ? ev.flags : S.flags, office: ev.office && typeof ev.office === "object" ? ev.office : S.office });
      break;
    case "wake":
      set({ awake: true }); if (S.capKind !== "say") caption("", "");
      // he is about to talk: the brain process that will answer warms its prompt cache meanwhile
      fetch("/api/jarvis_prewarm", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ conv: S.conv || "" }) }).catch(() => {});
      break;
    case "partial": partialAt = Date.now(); hold(); clearTimeout(capT); set({ caption: String(ev.text || ""), capKind: "heard" }); break;
    case "final":
      // a test turn marked quiet (speech-to-text accuracy runs): shown, never sent to the brain
      if (ev.quiet) { mark(String(ev.turn || ""), { fast_path: true, done: true }); caption(String(ev.text || ""), "heard", 1500); break; }
      nextVoice = ev.voice && typeof ev.voice === "object" ? ev.voice : null;   // how long and how loud he spoke (mood hint)
      heard(ev.text, ev.turn); break;
    case "turn_abort": dropTurn(String(ev.turn || "")); break;          // he went on right after the turn end: merge with what follows
    case "final_update": fixTurn(String(ev.turn || ""), String(ev.text || "")); break;
    case "caption": if (ev.text) caption(String(ev.text), "info", 9000); break;   // a say_soon line that never found a quiet moment
    // a one-shot push-to-talk keeps the conversation; so does a typed chat (6 Oct: the app's listening window ended a few
    // seconds after each answer and closed the panel while he was still typing)
    case "sleep": set({ awake: false }); if (ev.reason !== "ptt_done" && !chatInUse()) endConversation(false); break;
    case "barge": set({ speaking: false }); hold(); lastBarge = Date.now(); if (typeof ev.heard === "string") heardCut = ev.heard; break;
    case "speak_start": {
      set({ speaking: true }); const t = texts.get(ev.id); if (t) caption(t, "say");
      // the read-back has started: if speak_end never comes, start the window a little after it should have finished
      if (ev.id && ev.id === readbackId) { clearTimeout(fallbackT); fallbackT = setTimeout(readbackDone, 4000 + (t || "").length * 75); }
      break;
    }
    case "speak_end":
      texts.delete(ev.id);
      // office quiet mode: the line was shown, not spoken, so it stays up long enough to read
      if (ev.id === lastId) { set({ speaking: false }); caption(S.caption, S.capKind, ev.quiet ? Math.min(12000, Math.max(4000, (S.caption || "").length * 70)) : 1800); }
      if (ev.id && ev.id === readbackId) readbackDone();
      break;
    case "voices": set({ voices: Array.isArray(ev.list) ? ev.list : [], voice: ev.current || S.voice }); break;
    case "open": if (ev.what === "enrol" || ev.what === "voices") set({ dialog: ev.what }); break;
    case "error": caption(String(ev.text || "Something went wrong with the voice."), "err", 6000); break;
    case "speaker": spk.set(String(ev.turn || ""), { ok: !!ev.ok, score: ev.score, why: ev.why }); spkWake(); break;
    case "call": caption(ev.active ? "On a call: Jarvis stays quiet and shows answers here." : "Call over: Jarvis speaks again.", "info", 5000); break;
  }
}

const norm = (t: string) => String(t || "").toLowerCase().replace(/[’‘]/g, "'").replace(/[.,!?;:"“”]/g, " ").replace(/\s+/g, " ").trim();
const CANCEL = /^(stop|cancel|no|nope|don'?t|wait|hold on|never ?mind)\b/;
const AFFIRM = /^(yes|yeah|yep|yup|sure|ok|okay|go ahead|do it|send it|post it|please do|go on)\b/;
const BYE = /^(that'?s all|that is all|that'?s it|thanks jarvis|thank you jarvis|thanks that'?s all|ok thanks jarvis|okay thanks jarvis)\b/;
/** What he said (a 'final' from the app): a few phrases are handled here; anything else goes to ask(). */
function heard(raw: any, turn?: any) {
  let text = String(raw || "").trim();
  // the words of a turn that was dropped because he went on: they belong in front of these
  if (mergeText && Date.now() - mergeAt < 8000) text = (mergeText + " " + text).trim();
  mergeText = "";
  const n = norm(text);
  if (!n) return;
  curTurn = typeof turn === "string" ? turn : "";
  if (cur || queue.length) {
    // While an action waits, only a clear "yes" lets it go (at once); anything else cancels it. A yes is never sent to the brain,
    // so the same action can't come back a second time.
    if (AFFIRM.test(n) && !CANCEL.test(n) && n.split(" ").length <= 5) {
      if (cur && cur.needsYes && S.native) { confirmByVoice(curTurn); return; }   // an outward action: only HIS yes counts
      goNow(); return;
    }
    cancelPending();
    const rest = n.replace(/^(stop|cancel|no|nope|don'?t|wait|hold on|never ?mind)\b[\s,]*/, "").trim();
    if (CANCEL.test(n) && rest.split(" ").filter(Boolean).length < 3) return;   // "no, not yet": just the cancel
  }
  const local = () => { mark(curTurn, { fast_path: true, done: true }); curTurn = ""; };
  if (n === "stop listening" || n === "jarvis stop listening") { local(); setListening(false); caption("Listening off.", "info", 2500); return; }
  if (BYE.test(n)) { local(); post({ type: "sleep" }); endConversation(true); caption("", ""); return; }
  if (/^(stop|cancel|never ?mind|be quiet|quiet)$/.test(n)) { local(); post({ type: "stop_speaking" }); set({ speaking: false }); caption("", ""); return; }
  ask(text);
}

/* ---------- screen awareness ---------- */
type Reg = { selected?: () => string; draft?: (rid: string, aid: string) => string; list?: () => string[]; sendSlack?: (rid: string, aid: string) => Promise<boolean>; postJira?: (rid: string, aid: string) => Promise<boolean | "queued"> };
const reg: Reg = {};
/** A view (Inbox) lends its selected item, live draft text, visible list and send/post functions; the returned function takes them back. */
export function registerScreen(h: Reg) {
  Object.assign(reg, h);
  return () => { (Object.keys(h) as (keyof Reg)[]).forEach((k) => { if (reg[k] === h[k]) delete reg[k]; }); };
}
const cut = (s: any, n: number) => { const t = String(s ?? "").replace(/\s+/g, " ").trim(); return t.length > n ? t.slice(0, n - 1) + "…" : t; };
const cutKeep = (s: any, n: number) => { const t = String(s ?? "").trim(); return t.length > n ? t.slice(0, n - 1) + "…" : t; };
const splitKey = (k: string) => { const i = k.indexOf("|"); return i < 0 ? ["", ""] : [k.slice(0, i), k.slice(i + 1)]; };
function findItem(rid: string, aid: string) {
  const r = (WH.store.inbox.roundups || []).find((x: any) => x.id === rid), a = r && (r.actions || []).find((x: any) => x.id === aid);
  return { r: r || null, a: a || null };
}
/** The reply as it is now: the Inbox's live text (unsaved edits included) when it is open, else the saved draft. */
const draftOf = (rid: string, aid: string): string => {
  if (reg.draft) { const v = reg.draft(rid, aid); if (typeof v === "string") return v; }
  return WH.workFor(rid, aid)?.reply?.text || "";
};
const keysFor = (a: any) => { const pts = a.points || []; return pts.length ? pts.map((p: any, i: number) => (WH.isWait(p) ? null : `${a.id}.${i}`)).filter(Boolean) : [a.id]; };
const bytes = (o: any) => new TextEncoder().encode(JSON.stringify(o)).length;
const LIMITS = [
  { pt: 240, draft: 2400, sel: 1500, list: 30, ap: 8 }, { pt: 160, draft: 1600, sel: 900, list: 20, ap: 6 },
  { pt: 100, draft: 1000, sel: 500, list: 12, ap: 4 }, { pt: 60, draft: 500, sel: 250, list: 6, ap: 2 }, { pt: 40, draft: 300, sel: 120, list: 0, ap: 0 },
];
function screenAt(L: (typeof LIMITS)[number]) {
  const route = (location.hash || "#/").replace(/^#\/?/, "") || "home";
  const o: any = { route, page: VIEWS[WH.parse().name]?.title || "Home", now: new Date().toLocaleString("en-GB", { weekday: "short", hour: "2-digit", minute: "2-digit" }) };
  const key = reg.selected ? reg.selected() : "";
  if (key) {
    const [rid, aid] = splitKey(key), { r, a } = findItem(rid, aid);
    if (r && a) {
      const w = WH.workFor(rid, aid) || {}, rep = w.reply || {}, draft = draftOf(rid, aid), wps = w.points || [];
      o.selected = {
        rid, aid, who: cut(a.who, 60), where: cut(a.where, 80), when: cut(a.when, 40), src: WH.srcOf(a, r), title: cut(a.title, 120),
        points: (a.points || []).map((p: any, i: number) => {
          const k = `${aid}.${i}`, wp = wps.find((x: any) => x.key === k) || {};
          return { key: k, text: cut(WH.ptText(p), L.pt), ...(WH.ticked(rid, k) ? { done: true } : {}), ...(WH.isWait(p) ? { waiting: true } : {}), ...(wp.state ? { state: wp.state } : {}) };
        }),
        ...((a.points || []).length ? {} : { text: cut(WH.clean(a.text), L.pt * 2) }),
        ...(w.status ? { status: w.status } : {}),
        ...(w.summary ? { summary: cut(WH.clean(w.summary), L.pt) } : {}),
        ...((w.needs_you || []).length ? { needs_you: w.needs_you.slice(0, 4).map((x: any) => cut(WH.clean(x), L.pt)) } : {}),
        draft: draft ? { text: cutKeep(draft, L.draft), to: rep.to || "", where: rep.where || "", channel: rep.channel || "", ...(rep.jira_key ? { jira_key: rep.jira_key } : {}), edited: draft !== (rep.text || "") } : null,
        attachments: (rep.attachments || []).map((f: any) => cut(f.title || "Attachment", 80)),
        approvals: WH.approvalsFor(rid, aid).filter((x: any) => x.status === "pending" || x.kind === "slack_send").slice(-6).map((x: any) => ({
          id: x.id, kind: x.kind, status: x.status, label: cut(x.title, 120), ...((x.options || []).length ? { options: x.options.map((p: any) => ({ id: p.id, label: cut(p.label, 80) })) } : {}),
        })),
      };
    }
  }
  try { const t = String(window.getSelection?.()?.toString() || "").trim(); if (t) o.selection = cutKeep(t, L.sel); } catch {}
  try { const op = openItems(6); if (op.length) o.open = op; } catch {}
  const list = reg.list ? reg.list() : [];
  if (list.length && L.list) o.list = list.slice(0, L.list).map((k) => { const [rid, aid] = splitKey(k), { a } = findItem(rid, aid); return { key: k, who: cut(a?.who, 40), title: cut(a?.title || WH.ptText((a?.points || [])[0]) || a?.text, 90) }; });
  const pend = WH.pendingApprovals();
  if (pend.length && L.ap) o.pending_approvals = pend.slice(0, L.ap).map((x: any) => ({ id: x.id, label: cut(x.title, 100), ...(x.related?.rid ? { rid: x.related.rid, aid: x.related.aid } : {}) }));
  return o;
}
/** Questions that point at what he is looking at ("summarise this", "what's on my screen", "reply to that"). */
const LOOK = /\b(this|that|these|those|here|screen|window|page|tab|look at|looking at|on my (screen|monitor|display))\b/i;
let askedScreen = false, capSeq = 0;
/** The app's OCR of the window he was last in (any display); null after 2.5 s or when unavailable. */
function captureScreen(): Promise<any> {
  return new Promise((resolve) => {
    const id = "cap" + ++capSeq;
    const off = (f: any) => evSubs.delete(f);
    const f = (ev: any) => { if (ev && ev.type === "screen_capture" && ev.id === id) { off(f); clearTimeout(t); resolve(ev); } };
    const t = setTimeout(() => { off(f); resolve(null); }, 2500);
    evSubs.add(f);
    post({ type: "capture_screen", id });
  });
}

/** What the brain sees: route, the selected Inbox item (with the live draft), the selected text, the visible list, what is
    open. Kept <= ~6 KB by trimming in steps. */
export function buildScreen() {
  let o: any = {};
  for (const L of LIMITS) { o = screenAt(L); if (bytes(o) <= 6000) return o; }
  return { route: o.route, page: o.page, ...(o.selected ? { selected: { rid: o.selected.rid, aid: o.selected.aid, who: o.selected.who, title: o.selected.title } } : {}) };
}

/* ---------- conversation ---------- */
let ctl: AbortController | null = null, lastAck = -1, freshNext = false;
let bgFrom: { conv: string; q: string; at: number } | null = null;   // the question that went to the background (context carries over)
// the question on its way (for a turn the app drops or corrects), the words to put in front of the next one, what he heard
let askTurn = "", askQ = "", askSpoke = false, mergeText = "", mergeAt = 0, heardCut = "", dropPrev = false;
let partialAt = 0, rescueT: ReturnType<typeof setTimeout> | undefined;   // the last words heard live; the dropped-turn rescue
/** The server forgets a question the page took back (its turn is stopped and never recorded in the conversation). */
const unask = (q: string) => { if (S.conv && q) fetch("/api/jarvis_drop", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ conv: S.conv, q }) }).catch(() => {}); };
/** The app dropped this turn (he went on talking right after it ended): stop it and keep its words for the next one. */
function dropTurn(turn: string) {
  if (!turn || turn !== askTurn || askSpoke) return;
  const at = (mergeAt = Date.now());
  mergeText = askQ; dropPrev = true;
  if (ctl) { ctl.abort(); ctl = null; unask(askQ); }
  askTurn = ""; set({ thinking: false, turns: S.turns.filter((t, i) => !(i === S.turns.length - 1 && t.me && t.text === askQ)) });
  // A dropped question is never lost: if no words follow (a cough, a breath), or they stop and no final comes, it is asked as it was
  clearTimeout(rescueT);
  const chk = () => {
    if (!mergeText || mergeAt !== at) return;                              // the rest came: heard() merged it
    const more = partialAt > at, talking = Date.now() - partialAt < 1500;
    if ((more && talking && Date.now() - at < 8000) || (!more && Date.now() - at < 2000)) { rescueT = setTimeout(chk, 500); return; }
    const q = mergeText; mergeText = ""; ask(q);
  };
  rescueT = setTimeout(chk, 2000);
}
/** The recognizer's final changed words that were sent early: ask again with the right ones (same turn). */
function fixTurn(turn: string, text: string) {
  if (!turn || turn !== askTurn || askSpoke || !text.trim()) return;
  const q = askQ;
  if (ctl) { ctl.abort(); ctl = null; unask(q); }
  set({ turns: S.turns.filter((t, i) => !(i === S.turns.length - 1 && t.me && t.text === q)) });
  curTurn = turn; ask(text);
}
const ACKS = ["Hmm, one second.", "Sure.", "Let me look.", "One moment.", "Let me check."];
const HANDOFF_MS = 20000;
const HANDOFF = ["This one's taking a while. I'll keep at it and tell you when I have it.", "It's a bigger one. I'll carry on in the background and come back to you.", "Still digging. I'll let you know as soon as I have it."];
/** Say this when it suits him (never over him or Jarvis). prio: "urgent" = work blocked or due now, the first quiet moment;
 *  "asked" = what he asked for, at a natural break (45 s at most); "fyi" = news he did not ask for, only at a break while he
 *  is at the Mac, bundled, at most every 10 minutes (Conversation.swift soonTick). Browser: on screen. */
export function sayWhenFree(text: string, prio: "urgent" | "asked" | "fyi" = "asked") { if (S.native) post({ type: "say_soon", text, prio }); else caption(text, "info", 8000); }
/** News he did not ask for that is about what he is talking about right now counts as asked for (relevance). */
const STOPW = new Set("the a an to of in on for and or is are was it this that what with your you my sir needs need new waiting item items".split(" "));
function relatedToTalk(title: string): boolean {
  const w = (t: string) => new Set(norm(t).split(" ").filter((x) => x.length > 2 && !STOPW.has(x)));
  const a = w(title), b = w(S.turns.slice(-4).map((t) => t.text).join(" "));
  let n = 0; a.forEach((x) => { if (b.has(x)) n++; });
  return n >= 2;
}
/** A new decision/approval: said as news (fyi), or as asked for when it is about the conversation going on. */
function sayNews(text: string, title: string) {
  if (!S.flags.proactive) return;
  if (relatedToTalk(title)) sayWhenFree(text, "asked"); else post({ type: "proactive", text });
}
const firstSentence = (t: string) => { const m = String(t || "").replace(/\s+/g, " ").trim().match(/^.{20,220}?[.!?](?=\s|$)/); return m ? m[0] : cut(t, 160); };
/** A slow question's answer, after it went to the background. */
function deliverBg(ev: any, q: string) {
  const sp = untag(String(ev.speak || "")).trim(), text = String(ev.text || "").trim();
  addTurn({ text: `Re: ${cut(q, 70)}\n\n${answerText(sp, text) || "Done."}` });
  if (sp) sayWhenFree(`Sir, about ${cut(q.replace(/[?.!]+$/, ""), 60)}: ${sp}`);
  toast(`Answer ready: ${cut(q, 60)}`, { duration: 12000, description: cut(untag(sp || text), 160) });
  runActions(Array.isArray(ev.actions) ? ev.actions : []);
}
/** It did not finish even in the background: hand it over as a proper task (read back, 3 s to cancel). */
function bgFailed(q: string) {
  sayWhenFree("That one's bigger than I thought. I'll run it as a proper task.");
  runActions([{ type: "new_task", text: q }]);
}
/** What is open right now (needs him, or running), for the brain's "by the way". */
function openItems(n: number) {
  const out: any[] = [], st = WH.store.inbox;
  for (const r of st?.roundups || []) for (const a of r.actions || []) {
    const w = WH.workFor(r.id, a.id) || {}, pts = (w.points || []).filter((p: any) => !WH.ticked(r.id, p.key));
    const need = pts.some((p: any) => p.state === "decide" || p.state === "needs_you"), run = w.status === "working" || pts.some((p: any) => p.state === "working");
    if (!need && !run) continue;
    out.push({ who: cut(a.who, 30), title: cut(a.title || WH.ptText((a.points || [])[0]) || a.text, 70), state: need ? "needs you" : "running" });
    if (out.length >= n) return out;
  }
  return out;
}
/** Ends the conversation: the next question starts fresh. `force` also drops an answer still on its way. */
export function endConversation(force = true) {
  if (S.thinking && !force) { endAfter = true; return; }
  endAfter = false;
  if (force && ctl) { ctl.abort(); ctl = null; }
  convT0 = 0; heardCut = "";             // what he heard of an old answer means nothing to a new conversation
  set({ conv: null, thinking: false, panel: S.panel === "conv" ? "" : S.panel });
}
const ROUTES: Record<string, string> = { inbox: "inbox", today: "today", tickets: "tickets", "jira tickets": "tickets", home: "" };
const NAMES: Record<string, string> = { inbox: "Inbox", today: "Today", tickets: "Jira tickets", "": "Home" };
/** Obvious navigation, instantly and without the server. Returns true when it handled the words. */
const INTERRUPT = /^(wait|wait wait|hold on|hang on|one (sec|second|moment|minute)|a (sec|second)|just a (sec|second|moment)|sorry|excuse me|hmm,? wait)[.!,]*$/i;
function fast(q: string, turn = "") {
  if (INTERRUPT.test(q.trim())) {
    // he cut in, or asked for a moment: yield and keep listening (follow-ups need no wake word)
    const line = /^(sorry|excuse me)/i.test(q.trim()) ? "No worries, go ahead." : Date.now() - lastBarge < 6000 ? "Sorry, go on." : "Sure, take your time.";
    if (S.native) say(line, "answer", turn); else caption(line, "info", 2500);
    return true;
  }
  const n = norm(q).replace(/^(hey )?jarvis /, "").replace(/^(please|can you|could you) /, "").replace(/ please$/, "");
  const m = n.match(/^(?:(?:open|go to|show(?: me)?|take me to|switch to|back to)\s+)?(?:the |my )?(inbox|today|tickets|jira tickets|weekly close|close|home)(?: (?:page|view|tab|screen))?$/);
  if (m && (m[0] !== m[1] || m[1] !== "close")) {         // a bare "close" is not navigation
    const r = ROUTES[m[1]]; fastNote(q, NAMES[r]);
    if (WH.parse().name !== (r || "home")) WH.go(r);
    return true;
  }
  if (/^(next|next item|next one|the next one|go to the next one)$/.test(n)) return step(q, 1);
  if (/^(previous|previous item|previous one|prev|the previous one|back one|the one before)$/.test(n)) return step(q, -1);
  if (/^((are|is) (all )?(the |my )?(tasks|items|things|work|jobs)( all)? (done|finished|complete|completed)|(is )?everything done|what('s| is) (left|pending|still open|open)|(is there )?anything (left|pending|open)|what (needs|need) me|(how many|what) (things|items) (need|needs) me|what('s| is) the status|status( update)?)$/.test(n)) {
    status(q, turn); return true;
  }
  if (/^(new conversation|start a new conversation|start over|new chat|fresh start)$/.test(n)) {
    endConversation(true); set({ turns: [] }); caption("New conversation.", "info", 2200); return true;
  }
  return false;
}
/** "Are all the tasks done?" — answered at once from the same data the app shows, so there is no wait. */
function status(q: string, turn = "") {
  const m = WH.inboxModel(), open = m.actions.filter((x: any) => !WH.itemDone(x.r, x.a) && WH.openKeys(x.r, x.a).length);
  const asks = WH.pendingApprovals().length;
  let needs = 0, mine = 0; const firsts: string[] = [];
  open.forEach(({ r, a }: any) => {
    const w = WH.workFor(r.id, a.id), pts = ((w && w.points) || []).filter((p: any) => !WH.ticked(r.id, p.key));
    const you = pts.some((p: any) => p.state === "decide" || p.state === "needs_you") || !!(w && w.reply && w.reply.text && !w.reply.sent_at && !w.replied_by_owner);
    if (you) { needs++; const who = String(a.who || "someone").replace(/\s*\(.*$/, "").trim(); if (firsts.length < 2 && !firsts.includes(who)) firsts.push(who); }
    else if (!w || w.running || pts.some((p: any) => p.state === "queued")) mine++;
  });
  const youN = needs + asks;
  const speak = !open.length && !asks ? "Yes, all done. Nothing is open."
    : `Not yet. ${youN ? `${youN === 1 ? "One thing needs" : youN + " things need"} you` : "Nothing needs you"}${mine ? `, and I'm still working on ${mine === 1 ? "one" : mine}` : ""}.${firsts.length ? ` First up, ${firsts.join(", then ")}.` : ""}`;
  const text = `${open.length} open item${open.length === 1 ? "" : "s"} · ${youN} need${youN === 1 ? "s" : ""} you (${asks} approval${asks === 1 ? "" : "s"}) · ${mine} with Claude.`;
  if (S.panel === "conv") { addTurn({ me: true, text: q }); addTurn({ text: speak + "\n\n" + text }); }
  caption(speak, "say", 5000);
  if (S.native) sayAll(speak, "answer", turn);      // sentence by sentence: the first one is heard sooner
  return true;
}
function fastNote(q: string, what: string) {
  if (S.panel === "conv") { addTurn({ me: true, text: q }); addTurn({ text: what, note: true }); }
  caption(what, "info", 1800);
}
function step(q: string, d: number) {
  const L = reg.list ? reg.list() : null;
  if (!L || !L.length) { fastNote(q, "Inbox"); WH.go("inbox/todo"); return true; }
  const curKey = reg.selected ? reg.selected() : "", i = L.indexOf(curKey), j = i < 0 ? 0 : Math.max(0, Math.min(L.length - 1, i + d));
  const [rid, aid] = splitKey(L[j]), { a } = findItem(rid, aid);
  fastNote(q, i === j ? (d > 0 ? "That's the last one." : "That's the first one.") : cut(a?.who || "Next item", 40));
  WH.go("inbox/todo/" + encodeURIComponent(L[j]));
  setTimeout(() => document.querySelector(`[data-row-key="${CSS.escape(L[j])}"]`)?.scrollIntoView({ block: "nearest" }), 40);
  return true;
}

/** Ask Jarvis (typed or spoken). */
export async function ask(q0: string) {
  const q = String(q0 || "").trim().slice(0, 2000);
  const turn = curTurn; curTurn = "";
  if (!q) return;
  lastTyped = !turn;                                   // no voice turn behind it: he typed it
  if (fast(q, turn)) { mark(turn, { fast_path: true, done: true }); return; }
  const stale = S.panel === "conv" && lastActive && Date.now() - lastActive > CONV_MS;
  const fresh = S.panel !== "conv" || stale || freshNext;
  freshNext = false;
  if (fresh) { convT0 = 0; set({ conv: null, turns: [] }); }
  else if (S.conv && convT0 && Date.now() - convT0 > CONV_MS) { convT0 = 0; set({ conv: null }); }   // the server would start a new one anyway
  if (ctl) ctl.abort();
  const my = (ctl = new AbortController());
  endAfter = false;
  askTurn = turn; askQ = q; askSpoke = false;
  addTurn({ me: true, text: q });
  set({ thinking: true, panel: "conv", histPick: null });
  caption("", "");
  // With cached_openers on, the Mac app times every opener and filler itself (one place: Conversation.swift fillerTick); this page
  // only tells it what the brain is doing. Otherwise (or in a browser) the page does it, as before.
  const appTimes = S.native && !!S.flags.cached_openers;
  if (S.native && !appTimes) { let i = 0; do { i = Math.floor(Math.random() * ACKS.length); } while (i === lastAck); lastAck = i; say(ACKS[i], "opener", turn); }
  let spoke = false, got = false, progress = "", bg = false;
  // Like a person: a question that turns out slow is not held over him. After 20 s with no answer it moves to the background
  // ("I'll keep at it and tell you"), the conversation is free again, and the answer comes back later on its own.
  const handT = setTimeout(() => {
    if (my !== ctl || spoke || got) return;
    bg = true; ctl = null; freshNext = true;            // a new question no longer stops this one; it starts its own conversation
    bgFrom = { conv: S.conv || "", q, at: Date.now() };  // ...but carries this one's context over (5 Oct: "it has dementia")
    mark(turn, { done: true });                         // the app stops its own wait lines for this turn
    set({ thinking: false });
    const line = HANDOFF[Math.floor(Math.random() * HANDOFF.length)];
    say(line, "filler", turn); caption("Working on it in the background…", "info", 5000);
  }, HANDOFF_MS);
  // Not timed by the app: at most one line while it waits, and only a real one ("I'm reading your notes now."), after 6 s
  const fill = appTimes ? 0 : setTimeout(() => {
    if (my !== ctl || spoke || got || !progress || /^(working on it|digging into it)$/i.test(progress)) return;
    const line = `I'm ${progress.charAt(0).toLowerCase() + progress.slice(1)} now.`;
    if (S.native) say(line, "filler", turn); else caption(line, "info", 3000);
  }, 6000);
  try {
    const body: any = { q, screen: buildScreen() };
    // "this / that / my screen": the app reads the window he is looking at on any display (on-device OCR, not saved)
    if (S.native && eyesOn() && LOOK.test(q)) {
      const w = await captureScreen();
      if (w && w.text) body.screen = { other_window: { app: w.app, title: w.title, text: w.text }, ...body.screen };
      else if (w && w.error === "permission" && !askedScreen) { askedScreen = true; caption("To read other windows, allow Screen Recording for Jarvis in System Settings → Privacy & Security.", "info", 9000); }
    }
    if (S.conv) body.conv = S.conv;
    else if (bgFrom && bgFrom.conv && Date.now() - bgFrom.at < CONV_MS) { body.prev_conv = bgFrom.conv; body.pending_q = bgFrom.q; }
    if (heardCut) { body.heard = heardCut; heardCut = ""; }        // he cut the last answer off: the brain learns what he heard
    if (nextVoice) { body.voice = nextVoice; nextVoice = null; }   // how long and how loud he spoke (mood hint)
    if (dropPrev) { body.dropped = true; dropPrev = false; }       // the last question was only the start of this one
    mark(turn, { req_sent: Date.now() });
    let tok = false;
    const r = await fetch("/api/jarvis", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body), signal: my.signal });
    if (!r.ok || !r.body) { let e = "HTTP " + r.status; try { e = (await r.json()).error || e; } catch {} throw new Error(e); }
    const rd = r.body.getReader(), dec = new TextDecoder();
    let buf = "";
    const line = (s: string) => {
      if (my !== ctl && !bg) return;
      let ev: any; try { ev = JSON.parse(s); } catch { return; }
      if (!tok && (ev.type === "progress" || ev.type === "speak" || ev.type === "final")) { tok = true; mark(turn, { first_token: Date.now() }); }
      if (ev.type === "ack") { if (ev.conv && ev.conv !== S.conv) { convT0 = Date.now(); set({ conv: ev.conv }); } }
      else if (bg) { if (ev.type === "final") { got = true; deliverBg(ev, q); } else if (ev.type === "error") { got = true; bgFailed(q); } }
      else if (ev.type === "progress" && ev.text) { progress = String(ev.text); if (!spoke) caption(progress + "…", "info"); if (appTimes) post({ type: "progress", turn, text: progress }); }
      else if (ev.type === "speak" && ev.text && ev.filler) say(ev.text, "filler", turn);   // the hand-over line keeps the wait, not the answer
      else if (ev.type === "speak" && ev.text) { spoke = true; askSpoke = askSpoke || my === ctl; say(withMood(ev.text, ev.mood), "answer", turn); }
      else if (ev.type === "final") { got = true; finalize(ev, spoke, turn); }
      else if (ev.type === "error") { got = true; fail(ev.error, turn); }
    };
    for (;;) {
      const { value, done } = await rd.read();
      if (done) break;
      buf += dec.decode(value, { stream: true });
      let i;
      while ((i = buf.indexOf("\n")) >= 0) { const s = buf.slice(0, i).trim(); buf = buf.slice(i + 1); if (s) line(s); }
    }
    if (buf.trim()) line(buf.trim());
    if (!got && bg) bgFailed(q);
    else if (!got && my === ctl) fail("No answer came back.", turn);
  } catch (e: any) {
    if (bg && !my.signal.aborted) bgFailed(q);
    else if (!my.signal.aborted && my === ctl) fail(e && e.message ? e.message : String(e), turn);
  } finally {
    clearTimeout(fill); clearTimeout(handT);
    // The app said "sleep" while this was on its way: keep the answer on screen; the next question starts fresh.
    if (my === ctl) { ctl = null; set({ thinking: false }); if (endAfter) { endAfter = false; freshNext = true; convT0 = 0; set({ conv: null }); } }
  }
}
function finalize(ev: any, spoke: boolean, turn = "") {
  const text = String(ev.text || "").trim(), sp = String(ev.speak || "").trim();
  if (ev.conv && ev.conv !== S.conv) { convT0 = convT0 || Date.now(); set({ conv: ev.conv }); }
  addTurn({ text: answerText(sp, text) || "Done." });
  const acts = Array.isArray(ev.actions) ? ev.actions : [];
  const task = acts.some((a: any) => a && (a.type === "new_task" || a.type === "start_task"));   // the read-back says it
  if (S.native) { if (!spoke && sp && !task) sayAll(sp, "answer", turn, ev.mood); }
  else if (sp) caption(untag(sp), "say", 6000);
  mark(turn, { done: true });
  runActions(Array.isArray(ev.actions) ? ev.actions : []);
}
function fail(err: any, turn = "") {
  const e = cut(err || "Something went wrong.", 160);
  addTurn({ text: e, err: true });
  say("Sorry, that didn't work.", "answer", turn);
  mark(turn, { done: true });
  caption("Sorry — " + e, "err", 6000);
}

/* ---------- actions ---------- */
// needsYes: an outward action (send, Jira post, approval) runs only on his clear "yes"; silence or anything else cancels it
// (Phase 3, 5 Oct: before, a voice send went out after 3 s of silence). YES_MS is how long the yes is waited for.
type Prep = { label: string; spoken: string; run: () => Promise<string>; key: string; ms?: number; full?: string; needsYes?: boolean };
const YES_MS = 8000;
let queue: Prep[] = [], cur: Prep | null = null, timer: any = 0, fallbackT: any = 0, holdT: any = 0, left = WINDOW_MS, nPend = 0;
let rbWaiting = false;          // the read-back is queued or playing: the countdown must not start yet
const done = new Set<string>(); // actions already run this page load (rid|aid + type): never twice
// 19 Oct: "restart it" twice in a day was dropped as already done, silently, while Jarvis said "Restarting now". Starting,
// stopping and telling Claude are his to repeat: they are kept once per reply, not once per page load. Sends, Jira posts and
// approvals stay once per page load. (The server keeps one worker per item, so a repeat never doubles the work.)
const PER_REPLY = new Set(["start_task", "stop_task", "new_task", "tell_claude"]);
let replyN = 0;
const CHANGING = new Set(["send_reply", "post_jira", "approve", "decline", "tick", "tell_claude", "start_task", "stop_task", "new_task", "set_model"]);
const refuse = (msg: string) => { say(msg); caption(msg, "info", 6000); note(msg); return null; };
const poss = (who: string) => (who ? (/s$/i.test(who) ? `${who}'` : `${who}'s`) + " item" : "that item");
/** How an item is named out loud: someone's item by their name; his own asks by their title (19 Oct: not "You (Jarvis)'s item"). */
const itemRef = (rid: string, aid: string) => {
  const { a } = findItem(rid, aid);
  if (!a) return "that item";
  const who = WH.clean(a.who || "");
  if (a.src === "jarvis" || /^you\b/i.test(who)) return a.title ? `"${cut(WH.clean(a.title), 60)}"` : "your task";
  return poss(cut(who, 40));
};

export function openItem(rid: string, aid: string) {
  const key = rid + "|" + aid;
  WH.focusItem = key; WH.go("inbox/todo/" + encodeURIComponent(key));
}
/** Show a file in Finder: the Mac app does it; a browser cannot, so the path is copied instead. */
export function reveal(path: string) {
  if (!path) return;
  if (S.native) post({ type: "reveal", path });
  else WH.copy(path, "Path copied. Showing it in Finder works in the Jarvis app.");
}

/** Text longer than the label shows in full in the panel, with a longer window to read it (3 s + ~1 s per 40 characters, max 12 s). */
const longText = (t: string) => (t.length > 70 ? { full: t, ms: Math.min(12000, WINDOW_MS + Math.round(t.length / 40) * 1000) } : {});

function prepare(a: any): Prep | null {
  const rid = String(a.rid || ""), aid = String(a.aid || "");
  if (a.type === "send_reply" || a.type === "post_jira") {
    const w = WH.workFor(rid, aid), rep = (w && w.reply) || {}, text = draftOf(rid, aid), { r, a: item } = findItem(rid, aid);
    if (!r || !item) return refuse("I can't find that item any more.");
    if (!w || !String(text).trim()) return refuse("There's no drafted reply on that item yet.");
    if (a.type === "post_jira") {
      if (rep.channel !== "jira" || !rep.jira_key) return refuse("That draft isn't a Jira comment.");
      const posted = () => { const ks = keysFor(item); return ks.length > 0 && ks.every((k: string) => WH.ticked(rid, k)); };
      if (posted()) return refuse(`That comment is already on ${rep.jira_key}.`);
      return {
        key: "post_jira|" + rid + "|" + aid, needsYes: true,
        label: `Say yes to post your comment on ${rep.jira_key}`,
        spoken: `Your comment on ${rep.jira_key}, starting: ${cut(WH.clean(String(text || "")).replace(/[*_`#>]/g, ""), 70)}. Shall I post it?`,
        run: async () => {
          if (posted()) return `That comment is already on ${rep.jira_key}.`;
          let ok: boolean | "queued" = false;
          if (reg.postJira) ok = await reg.postJira(rid, aid);
          else {
            let res: any;
            try { res = await WH.api("/api/jira/comment", { key: rep.jira_key, text: draftOf(rid, aid), dry: false, rid, aid }); } catch { res = { ok: false, error: "Jarvis server not reachable" }; }
            WH.log(`Post this comment on ${rep.jira_key}?`, JSON.stringify(res.result || res, null, 1).slice(0, 4000));
            if (!res.ok) WH.actionError(res);
            else if (res.queued && res.already !== "done") ok = "queued";
            else { ok = true; if (!res.queued) WH.tickInbox(rid, keysFor(item), true); }
          }
          // Without a Jira token a Claude worker posts it; say so honestly instead of "Posted".
          if (ok === "queued") return `Claude's posting it on ${rep.jira_key} now. It gets ticked once it's really there.`;
          return ok ? `Posted on ${rep.jira_key}.` : "That didn't go through. The error is on screen.";
        },
      };
    }
    const send = WH.approvalsFor(rid, aid).filter((x: any) => x.kind === "slack_send").pop(), sst = (rep.send || {}).state;
    if (rep.sent_at || (send && send.status === "done")) return refuse("That reply was already sent.");
    if (sst === "sending" || sst === "unclear") return refuse(sst === "sending" ? "That reply is being sent right now." : "I'm still checking whether the last try went out.");
    if (rep.channel === "jira" && rep.jira_key) return refuse(`That draft is a comment for ${rep.jira_key}. Say "post it" to put it on Jira.`);
    if (rep.channel !== "slack" || !rep.channel_id) return refuse("That reply isn't a Slack message, so I can't send it from here. The button on the item opens it for you.");
    const to = WH.clean(rep.to || item.who || "them"), where = rep.where ? " in " + WH.clean(rep.where) : "";
    const opening = cut(WH.clean(String(draftOf(rid, aid) || "")).replace(/<@[A-Z0-9]+(\|[^>]*)?>/g, "").replace(/[*_`#>]/g, "").trim(), 70);
    return {
      key: "send_reply|" + rid + "|" + aid, needsYes: true,
      label: `Say yes to send your reply to ${to}${where}`, spoken: `Your reply to ${to}${where}, starting: ${opening}. Shall I send it?`,
      run: async () => {
        // the server refuses a second send of the same reply, so a "yes" heard twice cannot post twice
        let ok: boolean;
        if (reg.sendSlack) ok = await reg.sendSlack(rid, aid);
        else {
          let r: any;
          try { r = await WH.api("/api/slack_send", { rid, aid, text: draftOf(rid, aid), voice: true }); } catch { r = { ok: false, error: "Jarvis server not reachable" }; }
          if (!r || !r.ok) { WH.actionError(r || {}); return "That didn't go through. The error is on screen."; }
          ok = false;
          for (let i = 0; i < 60 && !ok; i++) {
            await new Promise((res) => setTimeout(res, 3000)); await WH.loadInbox();
            const s2 = ((WH.workFor(rid, aid) || {}).reply || {});
            if (s2.sent_at) ok = true;
            else if ((s2.send || {}).state === "failed") return "It didn't go out: " + WH.clean((s2.send || {}).error || "unknown error");
          }
          if (!ok) return "Still sending. I'll show it as sent once Slack confirms.";
        }
        if (ok) return "Sent.";
        const s3 = ((WH.workFor(rid, aid) || {}).reply || {}), st3 = s3.sent_at ? "sent" : (s3.send || {}).state;
        return st3 === "sent" ? "Sent." : st3 === "failed" ? "It didn't go out: " + WH.clean((s3.send || {}).error || "the error is on screen")
          : st3 === "sending" || st3 === "unclear" ? "Still sending. The item shows Sent once Slack confirms." : "That didn't go through. The error is on screen.";
      },
    };
  }
  if (a.type === "approve" || a.type === "decline") {
    const x = WH.store.approvals.items.find((i: any) => i.id === a.id);
    if (!x) return refuse("I can't find that request.");
    if (x.status !== "pending") return refuse("That request isn't waiting for an answer any more.");
    const title = cut(WH.clean(x.title), 90);
    if (a.type === "decline") return { key: "decline|" + x.id, label: `Declining: ${title}…`, spoken: `Declining ${title}.`, run: async () => ((await WH.setApproval(x.id, "declined")) ? "Declined. Nothing will happen." : "That didn't go through. The error is on screen.") };
    const opts = x.options || [], opt = a.choice ? opts.find((o: any) => o.id === a.choice) : null;
    if (opts.length && !opt) return refuse("That one has options. Which one should I pick?");
    const what = title + (opt ? " — " + cut(WH.clean(opt.label), 60) : "");
    return {
      key: "approve|" + x.id, needsYes: true,
      label: `Say yes to approve: ${what}`, spoken: `Approve ${what}? Say yes to go ahead.`,
      run: async () => (WH.store.approvals.items.find((i: any) => i.id === x.id)?.status !== "pending" ? "That request was already answered." : (await WH.setApproval(x.id, "approved", opt ? opt.id : undefined)) ? "Approved. " + WH.whenApproved() : "That didn't go through. The error is on screen."),
    };
  }
  if (a.type === "tick") {
    const keys: string[] = (a.keys || []).map(String), r = (WH.store.inbox.roundups || []).find((x: any) => x.id === rid);
    const ok = keys.filter((k) => { const aid0 = k.split(".")[0], it = r && (r.actions || []).find((x: any) => x.id === aid0); return it && (k === aid0 || /^\d+$/.test(k.slice(aid0.length + 1))); });
    if (!r || !ok.length) return refuse("I can't find those points.");
    const n = ok.length;
    return { key: "tick|" + rid + "|" + ok.join(","), label: `Marking ${n === 1 ? "that point" : n + " points"} done on ${itemRef(rid, ok[0].split(".")[0])}…`, spoken: `Marking ${n === 1 ? "that point" : n + " points"} done.`,
      run: async () => ((await WH.tickInbox(rid, ok, true)) === false ? "That didn't save. The error is on screen." : "Marked done.") };
  }
  if (a.type === "tell_claude") {
    const { a: item } = findItem(rid, aid), text = String(a.text || "").trim();
    if (!item || !text) return refuse("I can't find that item.");
    const ref = itemRef(rid, aid);
    return {
      key: "tell_claude|" + rid + "|" + aid + "|" + text, ...longText(text),
      label: `Telling Claude on ${ref}: “${cut(text, 70)}”`, spoken: `Telling Claude on ${ref}.`,
      run: async () => {
        const r: any = await WH.saveAnswer(rid, aid, text);
        if (!r) return "That didn't go through. The error is on screen.";
        return r.steered ? "Passed on. Claude's working on it right now and picks it up at its next step." : "Sent. Claude starts on it now.";
      },
    };
  }
  if (a.type === "set_model") {
    // the model switcher: read back, 3 s to cancel, then the server checks the model answers and saves it
    const N: Record<string, string> = { haiku: "Haiku 4.5", sonnet: "Sonnet 5.5", opus: "Opus 5.5", fable: "Fable 5.1" };
    const m = String(a.model || ""), tier = String(a.tier || "thinking");
    const part: Record<string, string> = { thinking: "deep questions and tasks", deep: "deep questions", fast: "quick answers", work: "tasks", all: "everything" };
    const dflt = m === "default";
    if (!dflt && !N[m]) return refuse("I only know Haiku, Sonnet, Opus and Fable.");
    const label = dflt ? "Going back to the default models…" : `Switching ${part[tier] || part.thinking} to ${N[m]}…`;
    return {
      key: "set_model|" + tier + "|" + m, label, spoken: label.replace(/…$/, "."),
      run: async () => {
        let r: any;
        try { r = await WH.api("/api/jarvis_model", { tier, model: m }); } catch { r = { ok: false, error: "Jarvis server not reachable" }; }
        if (!r || !r.ok) return (r && r.error) || "That didn't work.";
        WH.emit("models", r);
        return dflt ? "Back to default: Haiku for quick answers, Sonnet for deep questions, Opus for tasks." : `Done: ${part[tier] || part.thinking} now use ${N[m]}.`;
      },
    };
  }
  if (a.type === "stop_task") {
    const who = itemRef(rid, aid);
    return {
      key: "stop_task|" + rid + "|" + aid, label: `Stopping the work on ${who}…`, spoken: `Stopping the work on ${who}.`,
      run: async () => {
        let res: any;
        try { res = await WH.api("/api/jarvis_task", { type: "stop_task", rid, aid }); } catch { res = { ok: false, error: "Jarvis server not reachable" }; }
        WH.loadInbox();
        return res && res.ok ? (res.stopped ? "Stopped. Nothing more will happen on it until you say." : "Taken off the queue.") : "Nothing was running on it.";
      },
    };
  }
  if (a.type === "start_task" || a.type === "new_task") {
    let body: any, label: string, spoken: string;
    if (a.type === "start_task") {
      const { a: item } = findItem(rid, aid);
      if (!item) return refuse("I can't find that item.");
      body = { type: "start_task", rid, aid }; label = `Working on ${itemRef(rid, aid)} in the background…`; spoken = `I'll work on ${itemRef(rid, aid)} in the background.`;
    } else {
      const text = String(a.text || "").trim();
      if (!text) return refuse("What should the new task be?");
      const title = String(a.title || "").trim();       // the brain's short spoken name for it ("the March accuracy check")
      const kind = String(a.kind || "").trim();          // the brain's label: which specialist does it (Phase 4 router)
      const points = Array.isArray(a.points) ? a.points.filter((p: any) => typeof p === "string" && p.trim()) : [];   // his asks, one line each
      body = { type: "new_task", text, ...(title ? { title } : {}), ...(kind ? { kind } : {}), ...(points.length ? { points } : {}) };
      label = `In the background: ${cut(title || text, 70)}…`;
      // "run the scenario for March" (their words) or "the accuracy check" (a name): either way one natural line
      const verb = title && !/^(the|a|an|your|our|my|this|that|some|all)\b/i.test(title);
      spoken = title ? `I'll ${verb ? "" : "do "}${cut(title, 90)} in the background.` : `I'll do that in the background: ${cut(text, 70)}.`;
    }
    return {
      key: a.type + "|" + (a.type === "start_task" ? rid + "|" + aid : String(a.text || "").trim()),
      ...(a.type === "new_task" ? longText(String(a.text || "").trim()) : {}),
      label, spoken,
      run: async () => {
        let res: any;
        try { res = await WH.api("/api/jarvis_task", body); } catch { res = { ok: false, error: "Jarvis server not reachable" }; }
        if (!res.ok || !res.item) return "That didn't start: " + cut(res.error || "no answer", 80) + ".";
        if (res.held) {
          // his 10% rail (spine/governor.py): a big task waits for his clear yes, like a send
          const id = String(res.task_id || "");
          queue.unshift({
            key: "go|" + id, needsYes: true, label: "Say yes to start it (big task)", spoken: "Say yes to start it.",
            run: async () => {
              let r: any;
              try { r = await WH.api("/api/jarvis_task", { type: "go", id }); } catch { r = { ok: false, error: "Jarvis server not reachable" }; }
              if (!r.ok || !r.item) return "That didn't start: " + cut(r.error || "no answer", 80) + ".";
              watch(r.item.rid, r.item.aid);
              return "Started.";
            },
          });
          return String(res.rail || "This one is big, so it waits for your yes.");
        }
        watch(res.item.rid, res.item.aid);
        const n = +res.queued_behind || 0;
        return n ? `It's queued behind ${n === 1 ? "one other task" : n + " others"}.` : "";   // started: nothing more to say
      },
    };
  }
  return null;
}

function runActions(list: any[]) {
  const n = ++replyN;
  for (const a of list) {
    if (!a || typeof a !== "object") continue;
    if (a.type === "open") {
      if (a.route != null && ROUTES[a.route] !== undefined) WH.go(ROUTES[a.route]);
      else if (a.rid && a.aid) openItem(String(a.rid), String(a.aid));
    } else if (a.type === "reveal") reveal(String(a.path || ""));
    else if (a.type === "pronounce" && a.word && a.say) {
      // "that's pronounced ...": the lexicon learns it (spoken words only; nothing else changes)
      post({ type: "lexicon_add", word: String(a.word), say: String(a.say) }); note(`Pronunciation saved: ${cut(a.word, 40)} → ${cut(a.say, 60)}`);
    }
    else if (CHANGING.has(a.type)) {
      const p = prepare(a);
      if (p && PER_REPLY.has(a.type)) p.key += "|r" + n;
      if (p && !done.has(p.key) && p.key !== cur?.key && !queue.some((q) => q.key === p.key)) queue.push(p);
      else if (p && done.has(p.key)) note("Already done earlier: " + p.label);   // never silently
    }
  }
  if (!cur) nextPending();
}
function nextPending() {
  cur = queue.shift() || null;
  clearTimeout(timer); clearTimeout(fallbackT); clearTimeout(holdT);
  if (!cur) { readbackId = ""; set({ pending: null }); return; }
  left = cur.ms || (cur.needsYes ? YES_MS : WINDOW_MS);
  set({ pending: { label: cur.label, ms: left, until: null, held: false, frac: 1, n: ++nPend } });
  note(cur.label);
  if (cur.full) addTurn({ text: cut(cur.full, 140), details: cur.full } as any);   // the whole text is one tap away, not a wall
  if (S.native) {
    // The read-back goes behind whatever Jarvis is still saying; the window starts only once it has been heard
    // (speak_end of the read-back, or a little after its speak_start). The 30 s guard only covers a silent app.
    rbWaiting = true;
    readbackId = say(cur.spoken, "readback");
    fallbackT = setTimeout(readbackDone, 30000);
  } else startCount();
}
function readbackDone() {
  clearTimeout(fallbackT); readbackId = "";
  if (!rbWaiting) return;
  rbWaiting = false;
  if (cur && S.pending && !S.pending.held) startCount();
}
function startCount() {
  clearTimeout(fallbackT); clearTimeout(holdT);
  if (!cur || !S.pending || rbWaiting) return;
  clearTimeout(timer);
  const full = cur.ms || WINDOW_MS;
  set({ pending: { ...S.pending, until: Date.now() + left, ms: left, held: false, frac: left / full } });
  timer = setTimeout(cur.needsYes ? noYes : fire, left);
}
/** The window ran out on an outward action without his "yes": nothing is sent. */
function noYes() {
  const c = cur;
  if (!c) return;
  cur = null; clearTimeout(timer); clearTimeout(holdT);
  set({ pending: null });
  const msg = "I didn't hear a yes, so nothing was sent.";
  say(msg); caption(msg + " Say it again, or press the button.", "info", 6000); note(msg);
  nextPending();
}
/* The speaker check (Phase 3): the app scores a short reply against his voiceprint (Speaker.swift, speaker.py). */
const spk = new Map<string, { ok: boolean; score?: number; why?: string }>();
let spkWaiters: (() => void)[] = [];
const spkWake = () => { const w = spkWaiters; spkWaiters = []; w.forEach((f) => f()); };
async function confirmByVoice(turn: string) {
  const t0 = Date.now();
  while (!spk.has(turn) && Date.now() - t0 < 2000) await new Promise<void>((res) => { spkWaiters.push(res); setTimeout(res, 250); });
  const r = spk.get(turn);
  spk.delete(turn);
  if (r && r.ok) { goNow(); return; }
  const c = cur;
  cur = null; clearTimeout(timer); clearTimeout(holdT); clearTimeout(fallbackT); rbWaiting = false;
  set({ pending: null });
  const msg = r && r.why === "noisy" ? "It's too noisy here for me to check your voice, so I didn't do it. Press the button to go ahead."
    : r ? "That didn't sound like your voice, so I didn't do it. Press the button to go ahead." : "I couldn't check your voice, so I didn't do it. Press the button to go ahead.";
  say(msg); caption(msg, "info", 7000); note(msg + (c ? " (" + c.label.replace(/^Say yes to /, "") + ")" : ""));
  nextPending();
}
/** He said yes: run it now (once the read-back has been heard). */
function goNow() {
  if (!cur || !S.pending) return;
  if (rbWaiting) { rbWaiting = false; readbackId = ""; post({ type: "stop_speaking" }); }
  clearTimeout(timer); clearTimeout(holdT); clearTimeout(fallbackT);
  fire();
}
/** He started talking: the countdown waits for his words (resumes by itself after 8 s). */
function hold() {
  if (!cur || !S.pending || S.pending.held) return;
  if (S.pending.until) { left = Math.max(1200, S.pending.until - Date.now()); clearTimeout(timer); }
  set({ pending: { ...S.pending, until: null, held: true, frac: left / (cur.ms || WINDOW_MS) } });
  clearTimeout(holdT); holdT = setTimeout(resume, 8000);
}
function resume() {
  if (!cur || !S.pending || !S.pending.held) return;
  if (rbWaiting) set({ pending: { ...S.pending, held: false } });   // still waiting for the read-back to be heard
  else startCount();
}
async function fire() {
  const c = cur;
  if (!c) return;
  cur = null; clearTimeout(timer); clearTimeout(holdT);
  set({ pending: null });
  let msg: string;
  if (done.has(c.key)) msg = "I've already done that one.";
  else try { done.add(c.key); msg = await c.run(); } catch (e: any) { msg = "That didn't work: " + cut(e && e.message, 80); }
  if (msg) { say(msg); caption(msg, "info", 6000); note(msg); }
  else { caption("Working on it in the background.", "info", 4000); note("Started in the background."); }
  nextPending();
}
/** Esc or a spoken stop / cancel / no inside the window: nothing runs (the rest of the queue is dropped too). */
export function cancelPending() {
  if (!cur && !queue.length) return false;
  // Phase 5: a cancel during the read-back is a learning signal (what he did not want done)
  if (cur) WH.api("/api/learn_signal", { kind: "cancel_readback", label: cur.label, ref: cur.key }).catch(() => {});
  cur = null; queue = []; readbackId = ""; rbWaiting = false;
  clearTimeout(timer); clearTimeout(fallbackT); clearTimeout(holdT);
  post({ type: "stop_speaking" });
  set({ pending: null, speaking: false });
  say("Cancelled.");
  caption("Cancelled. Nothing was done.", "info", 3500); note("Cancelled. Nothing was done.");
  return true;
}

/** The toast button that opens an item. */
const openButton = (rid: string, aid: string) => createElement("button", { type: "button", onClick: () => openItem(rid, aid),
  className: "ml-auto flex-none rounded-full border border-[rgba(159,231,255,.28)] bg-[rgba(63,200,255,.12)] px-3 py-1 text-[12.5px] font-medium text-[#f2fbff] hover:bg-[rgba(63,200,255,.2)]" }, "Open");

/* ---------- started work: tell him when it lands ---------- */
const watching = new Map<string, { rid: string; aid: string; from: string | null; at: number }>();
let watchOff: (() => void) | null = null;
function watch(rid: string, aid: string) {
  watching.set(rid + "|" + aid, { rid, aid, from: WH.workFor(rid, aid)?.updated || null, at: Date.now() });
  if (!watchOff) watchOff = WH.on("inbox", checkWatch);       // App.tsx re-reads the inbox within 2 s of any change
}
function checkWatch() {
  for (const [k, t] of watching) {
    const w = WH.workFor(t.rid, t.aid);
    if (w && ["done", "drafted", "needs_you"].includes(w.status) && (w.updated || null) !== t.from) {
      watching.delete(k);
      const need = needsOf(t.rid, w);
      if (need.length) needSeen.add(need[0] + "|" + t.aid);           // said here, so the general watcher below does not say it again
      const msg = need.length ? "It needs your decision. It's in your inbox." : "Done. It's in your inbox.";
      // he asked for it, so he hears how it went (at the next quiet moment, never over him): the result, or the question
      const { a: it } = findItem(t.rid, t.aid), what = cut(WH.clean(it?.title || "That task"), 60);
      const ask0 = (w.asks && w.asks[0] && w.asks[0].q) || (w.needs_you || [])[0] || "";
      sayWhenFree(need.length ? `Sir, ${what} needs your decision${ask0 ? ": " + firstSentence(WH.clean(ask0)) : "."}` : `Sir, ${what} is done${w.summary ? ". " + firstSentence(WH.clean(w.summary)) : "."}`);
      caption(msg, "info", 6000);
      toast.success(msg, { duration: 12000, action: openButton(t.rid, t.aid) });
    } else if (Date.now() - t.at > 4 * 3600e3) watching.delete(k);
  }
  if (!watching.size) { watchOff?.(); watchOff = null; }
}

/* ---------- a decision or question a routine left for him (2 Oct: it sat in the inbox unannounced) ---------- */
/** Open questions/decisions in one work entry, as stable keys (a new question = a new key). */
function needsOf(rid: string, w: any): string[] {
  if (!w) return [];
  const pts = (w.points || []).filter((p: any) => (p.state === "decide" || p.state === "needs_you") && !WH.ticked(rid, p.key));
  if (!pts.length && !(w.needs_you || []).length) return [];
  return [rid + "|" + (pts.map((p: any) => p.key + ":" + cut(p.note, 60)).join(";") || cut((w.needs_you || [])[0], 80))];
}
const needSeen = new Set<string>();
let needInit = false;
function checkNeeds() {
  const work = WH.store.inbox?.work || {}, fresh: { rid: string; aid: string; title: string }[] = [];
  for (const r of WH.store.inbox?.roundups || []) for (const a of r.actions || []) {
    const w = (work[r.id] || {})[a.id];
    // only work that changed in the last 15 minutes is news (9 Oct: when the Inbox window widened to 7 days, an open page
    // announced a week-old item he had already handled as "needs your decision", under "Nothing needs you")
    if (!w || !w.updated || Date.now() - Date.parse(w.updated) > 15 * 60e3) continue;
    const ks = needsOf(r.id, w).map((x) => x + "|" + a.id);
    if (watching.has(r.id + "|" + a.id)) continue;            // a task he started by voice: checkWatch says it
    for (const k of ks) if (!needSeen.has(k)) { needSeen.add(k); fresh.push({ rid: r.id, aid: a.id, title: WH.clean(a.title || a.text || "") }); }
  }
  if (!needInit) { needInit = true; return; }                 // what was already waiting when the page loaded is not news
  if (!fresh.length) return;
  const one = fresh[0], msg = fresh.length === 1 ? `Needs your decision: ${cut(one.title, 60)}` : `${fresh.length} items need your decision`;
  sayNews(fresh.length === 1 ? `Sir, ${cut(one.title, 70)} needs your decision.` : `Sir, ${fresh.length} items need your decision.`, fresh.map((f) => f.title).join(" "));
  caption(msg, "info", 8000);
  toast(msg, { duration: 15000, action: openButton(one.rid, one.aid) });
}

/* ---------- task alerts (Phase 4): a task that failed, got stuck, runs long, was stopped, or waits at his 10% rail.
   No result card shows these, so each is said once (what was there when the page loaded is not news). ---------- */
const alertSeen = new Set<number>();
let alertInit = false;
function checkAlerts() {
  const list: any[] = (WH.store.inbox as any)?.task_alerts || [];
  const fresh = list.filter((a) => !alertSeen.has(a.id));
  fresh.forEach((a) => alertSeen.add(a.id));
  if (!alertInit) { alertInit = true; return; }
  for (const a of fresh) {
    const what = cut(WH.clean(a.title || "a task"), 60);
    const line =
      a.kind === "failed" ? `Sir, ${what} didn't finish${a.why ? ": " + cut(WH.clean(a.why), 80) : "."} I've left it in the inbox.` :
      a.kind === "stuck" ? `${what} had stalled for ${a.minutes || 25} minutes, so I restarted it where it stopped.` :
      a.kind === "long" ? `${what} has been running ${a.minutes || 45} minutes. It's still going.` :
      a.kind === "stopped" ? `I stopped ${what} after two hours. It's in the inbox; say continue if you want it to carry on.` :
      a.kind === "refused" ? `I stopped ${what}: it kept trying things it isn't allowed to do.` :
      a.kind === "health" ? WH.clean(a.why || "") :
      a.kind === "storm" ? "My server keeps restarting. I've started looking into why; the fix will be in your inbox." :
      a.kind === "improved" ? `I've improved myself: ${cut(WH.clean(a.why || a.title || ""), 120)}. It's in Learned, and I'll undo it if it makes things worse.` :
      a.kind === "rolled_back" ? `I undid a change I made to myself: ${cut(WH.clean(a.why || ""), 120)}.` : "";
    if (!line) continue;
    sayWhenFree(line);
    caption(line, "info", 8000);
    if (a.rid && a.aid) toast(line, { duration: 15000, action: openButton(a.rid, a.aid) });
    else toast(line, { duration: 15000 });
  }
}

/* ---------- nudges (GET /api/nudges every 3 s): what Claude found (ask_claude) and reminders that are due ---------- */
let vpnKey = "", vpnSaid = 0;
const nudged = new Set<string>();
async function checkNudges() {
  let d: any;
  try { d = await fetch("/api/nudges", { cache: "no-store" }).then((r) => r.json()); } catch { return; }
  // what Claude found when Jarvis asked it (ask_claude): said at the next quiet moment, kept in the conversation
  for (const c of (d && d.answers) || []) {
    if (nudged.has("c" + c.id)) continue;
    nudged.add("c" + c.id);
    const sp = untag(String(c.speak || "")).trim(), q = cut(String(c.q || "").replace(/[?.!]+$/, ""), 60);
    if (c.state === "done" && sp) {
      sayWhenFree(sp);
      addTurn({ text: sp, details: untag(String(c.text || "")).trim() || undefined } as any);   // the answer, details folded (5 Oct)
      toast(`Claude came back: ${q}`, { duration: 15000, description: cut(sp, 160) });
    } else {
      sayWhenFree(`Sorry, Claude couldn't finish ${q}.`);
      toast(`Claude couldn't finish: ${q}`, { duration: 12000 });
    }
    fetch("/api/consult_done", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ id: c.id }) }).catch(() => {});
  }
  for (const r of (d && d.due) || []) {
    if (nudged.has(r.id)) continue;
    nudged.add(r.id);
    sayWhenFree(`Sir, a reminder: ${WH.clean(r.text)}.`, "urgent");
    toast(`Reminder: ${cut(WH.clean(r.text), 80)}`, { duration: 20000 });
    fetch("/api/reminder_done", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ id: r.id }) }).catch(() => {});
  }
  const w: any[] = (d && d.waiting) || [], key = w.map((x) => x.pid + "@" + x.since).join(",");
  if (w.length && key !== vpnKey) {
    const who = cut(WH.clean(w[0].who || "A database step"), 70), msg = `Connect the VPN: ${who} is waiting for the database.`;
    toast(msg, { id: "vpn", duration: Infinity, description: "Jarvis carries on by itself once it is connected." });
    caption(msg, "info", 10000);
    // he asked to be asked: said directly (not the once-per-10-minutes proactive line), but never over him
    if (Date.now() - vpnSaid > 120000) { sayWhenFree(`Sir, please connect the VPN. ${who} needs the database.`, "urgent"); vpnSaid = Date.now(); }
  } else if (!w.length && vpnKey) {
    toast.dismiss("vpn");
    toast.success("VPN connected. Carrying on.", { duration: 5000 });
    caption("VPN connected. Carrying on.", "info", 5000);
  }
  vpnKey = key;
}

/* ---------- a new approval while he is away from the screen (proactive) ---------- */
let seenAp: Set<string> | null = null;
function checkApprovals() {
  const ids = WH.pendingApprovals().map((x: any) => x.id);
  if (!seenAp) { seenAp = new Set(ids); return; }                 // the ones already there when the page loaded are not news
  const fresh = WH.pendingApprovals().filter((x: any) => !seenAp!.has(x.id));
  ids.forEach((i: string) => seenAp!.add(i));
  if (fresh.length) sayNews(fresh.length === 1 ? `A new approval is waiting: ${cut(WH.clean(fresh[0].title), 70)}.` : `${fresh.length} new approvals are waiting.`, fresh.map((x: any) => WH.clean(x.title)).join(" "));
}
/** EYES (landing page switch): may Jarvis read the window he is looking at when he says "this"? On unless switched off. */
export const eyesOn = () => { try { return localStorage.getItem("jv_eyes") !== "0"; } catch { return true; } };
export function setEyes(on: boolean) { try { localStorage.setItem("jv_eyes", on ? "1" : "0"); } catch {} set({}); }
/** Office quiet mode (captions instead of speech while at the office without headphones); the app recomputes office.quiet. */
export function setOfficeQuiet(on: boolean) { post({ type: "set_flag", name: "office_quiet", on }); }
/** "Speak up on its own" (voice settings). */
export function setProactive(on: boolean) { post({ type: "set_flag", name: "proactive", on }); set({ flags: { ...S.flags, proactive: on } }); }

/* ---------- switches, dialogs, history ---------- */
export function setListening(on: boolean) { post({ type: "listen", on }); set({ listening: on }); }
/** Push-to-talk (the reactor / brand mark): the app listens until he stops speaking. */
export function talk() { if (S.native) post({ type: "ptt_start" }); }
export const openDialog = (what: JState["dialog"]) => set({ dialog: what });
export async function loadHistory() {
  set({ panel: "history", histPick: null, history: null });
  try {
    const d = await WH.api("/api/jarvis_conversations"), cut7 = Date.now() - 7 * 864e5;
    set({ history: (d.conversations || []).filter((c: any) => { const t = Date.parse(c.started || ""); return !isNaN(t) && t >= cut7 && (c.turns || []).length; }) });
  } catch { set({ history: [] }); }
}
/** The chat is being typed in: its box has focus or text, or the last question was typed (not spoken). Voice "sleep" and the
 *  idle close leave such a conversation open; he closes it with the x. */
let lastTyped = false;
export function chatInUse() {
  const el = typeof document !== "undefined" ? (document.querySelector("[data-jarvis-input]") as HTMLInputElement | null) : null;
  return S.panel === "conv" && (lastTyped || !!(el && (document.activeElement === el || el.value.trim())));
}
/** Opens the chat on a click (the caption pill, the idle "Ask Jarvis" pill, or "/"), ready to type. A conversation still
 *  going is shown as it is; otherwise it opens empty and the first question starts a fresh one. */
export function openChat() {
  if (S.panel !== "conv") set({ panel: "conv", histPick: null, ...(S.conv && S.turns.length ? {} : { turns: [] }) });
  setTimeout(() => (document.querySelector("[data-jarvis-input]") as HTMLInputElement | null)?.focus(), 60);
}
export const closePanel = () => { if (S.panel === "conv") { if (S.awake) post({ type: "sleep" }); endConversation(true); } else set({ panel: "", histPick: null }); };
export const stopSpeaking = () => { post({ type: "stop_speaking" }); set({ speaking: false }); };

/* ---------- start ---------- */
let inited = false;
export function initJarvis() {
  if (inited || typeof window === "undefined") return;
  inited = true;
  const w = W();
  w.jarvisVoice = { onEvent, state: () => ({ panel: S.panel, turns: S.turns.length, thinking: S.thinking, typed: lastTyped, inUse: chatInUse() }) };   // state: read-only, for checks
  // A small handle for checking things from the developer console (and the read-only tests).
  w.jarvisPage = { ask, screen: buildScreen, state: () => S, set, cancel: cancelPending, WH };
  set({ native: !!(w.jarvisApp && w.jarvisApp.native) });
  if (S.native) { post({ type: "hello" }); post({ type: "busy", on: false }); post({ type: "voices" }); }
  WH.on("approvals", checkApprovals); checkApprovals();
  WH.on("inbox", checkNeeds);
  WH.on("inbox", checkAlerts);
  setInterval(checkNudges, 3000); checkNudges();
  // Ten quiet minutes end a conversation (the server does the same).
  setInterval(() => { if (S.panel === "conv" && !S.thinking && !S.pending && lastActive && Date.now() - lastActive > CONV_MS && !chatInUse()) endConversation(true); }, 30000);
}
