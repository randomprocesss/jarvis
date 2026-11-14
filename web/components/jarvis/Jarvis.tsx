"use client";
/* Jarvis on every page (mounted once from App.tsx): a slim caption pill at the bottom centre (live caption, "Thinking…",
   or an action's read-back with a draining 3-second line) and a slim conversation panel from the right while a conversation
   is open (his lines right, Jarvis's left), which also shows recent conversations read-only. Esc cancels a waiting action
   and stops the voice. State and logic: lib/jarvis.ts.
   Type or talk, in normal and office mode (the owner, 6 Oct): a click on the caption pill, or on the faint "Ask Jarvis" pill
   that stands in when nothing is showing, or "/" anywhere, opens the chat ready to type; the mic beside Send talks (app
   only). Typed questions are answered like spoken ones (spoken, or captions while office mode is quiet). */
import { useEffect, useRef, useState } from "react";
import { AnimatePresence, motion } from "framer-motion";
import { ArrowLeft, ArrowUp, Mic, X } from "lucide-react";
import { useJarvis, initJarvis, ask, cancelPending, closePanel, openChat, stopSpeaking, talk, jarvis, type JState, untag, answerText } from "@/lib/jarvis";
import { cn } from "@/lib/utils";
import { Md } from "@/components/jarvis/Md";
import { EnrolDialog, VoicesDialog, StatsDialog } from "@/components/jarvis/Dialogs";

const EASE = [0.16, 1, 0.3, 1] as any;

function Caption({ s }: { s: JState }) {
  const p = s.pending;
  const text = p ? p.label : s.thinking ? "Thinking…" : s.caption || (s.awake ? "Listening…" : "");
  const kind = p ? "pending" : s.thinking ? "think" : s.caption ? s.capKind : s.awake ? "listen" : "";
  // a click on the pill (not on its Cancel button) opens the chat; with nothing to show, a faint "Ask Jarvis" pill stands in
  // (the owner, 6 Oct: "on click chat panel", in office and normal mode)
  const onPill = (e: React.MouseEvent) => { if (!(e.target as HTMLElement).closest("button")) openChat(); };
  const idle = !text && s.panel !== "conv";
  return (
    <div className="pointer-events-none fixed inset-x-0 bottom-5 z-[58] flex justify-center px-4 max-[560px]:bottom-4" aria-live="polite">
      <AnimatePresence>
        {idle && (
          <motion.button key="idle" type="button" onClick={() => openChat()} aria-label="Ask Jarvis: type or talk" title="Ask Jarvis (press /)"
            initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0, transition: { duration: 0.12 } }} transition={{ duration: 0.3, ease: EASE }}
            data-jarvis-idle
            className="pointer-events-auto flex cursor-pointer items-center gap-2 rounded-full border border-line-3 bg-card px-3.5 py-1.5 text-[12.5px] font-medium text-ink-2 shadow-md transition-colors hover:border-ion hover:text-ion focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ion/60">
            <i aria-hidden className="size-[7px] rounded-full bg-ion" />Ask Jarvis<kbd className="rounded border border-line-3 px-1 font-mono text-[10.5px] text-mute">/</kbd>
          </motion.button>
        )}
        {text && (
          <motion.div key="pill" initial={{ opacity: 0, y: 8, filter: "blur(4px)" }} animate={{ opacity: 1, y: 0, filter: "blur(0px)" }} exit={{ opacity: 0, y: 6, transition: { duration: 0.18 } }}
            transition={{ duration: 0.32, ease: EASE }} data-jarvis-caption={kind} onClick={onPill} title="Open the chat"
            className="pointer-events-auto relative flex cursor-pointer max-w-[min(620px,100%)] items-center gap-3 overflow-hidden rounded-full border border-line bg-card shadow-md py-2.5 pl-4 pr-4 text-[13.5px] leading-snug text-glass-ink-2-xl">
            <i aria-hidden className={cn("arc-dot", kind === "think" && "jv-think", kind === "err" && "!bg-accent-red !shadow-none", kind === "pending" && "!bg-accent-gold !shadow-[0_0_8px_1px_rgba(232,176,75,.45)]")} />
            <span className={cn("min-w-0 line-clamp-2", (kind === "heard" || kind === "say" || kind === "pending") && "text-glass-ink")} data-jarvis-text>{text}</span>
            {p && <>
              <button type="button" onClick={() => cancelPending()} className="flex-none cursor-pointer rounded-full border-0 bg-ink/[.06] px-2.5 py-1 text-[12px] font-medium text-glass-ink-2 hover:bg-ink/[.1] hover:text-glass-ink" data-jarvis-cancel>
                Cancel <kbd className="ml-1 font-mono text-[10.5px] text-glass-mute">esc</kbd>
              </button>
              <span aria-hidden className="absolute inset-x-5 bottom-0 h-px bg-ion/[.08]">
                {p.until && !p.held
                  ? <span key={p.until} className="jv-drain absolute inset-0 bg-arc-hi" style={{ ["--jv-ms" as any]: p.ms + "ms", ["--jv-from" as any]: String(p.frac) }} data-jarvis-drain />
                  : <span className="absolute inset-0 origin-left bg-arc-hi/70" style={{ transform: `scaleX(${p.frac})` }} />}
              </span>
            </>}
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}

const when = (iso: string) => { const d = new Date(iso); return isNaN(+d) ? "" : d.toLocaleString("en-GB", { weekday: "short", day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" }); };

function Turn({ t }: { t: { me?: boolean; text: string; err?: boolean; note?: boolean } }) {
  if (t.me) return <div className="ml-10 w-fit max-w-full self-end whitespace-pre-line rounded-[14px] rounded-br-[5px] bg-ion/[.075] px-3.5 py-2 text-[14px] leading-relaxed text-glass-ink ring-1 ring-inset ring-ion/[.1] [overflow-wrap:anywhere]">{t.text}</div>;
  if (t.note || t.err) return (
    <div className="flex items-start gap-2 text-[12.5px] leading-snug text-glass-mute">
      <i aria-hidden className={cn("mt-[6px] size-[5px] flex-none rounded-full", t.err ? "bg-accent-red" : "bg-arc-hi/60")} /><span className="min-w-0 [overflow-wrap:anywhere]">{t.text}</span>
    </div>
  );
  return (
    <div className="mr-4 text-[14px] leading-relaxed text-glass-ink-2 [overflow-wrap:anywhere]">
      <Md text={t.text} />
      {(t as any).details && (
        <details className="mt-1.5 rounded-[10px] bg-ion/[.04] px-3 py-1.5 text-[13px] text-glass-mute ring-1 ring-inset ring-ion/[.07]">
          <summary className="cursor-pointer select-none font-mono text-[10.5px] tracking-[.14em] text-glass-mute">DETAILS</summary>
          <div className="mt-1.5 text-glass-ink-2"><Md text={(t as any).details} /></div>
        </details>
      )}
    </div>
  );
}

function Panel({ s }: { s: JState }) {
  const [hover, setHover] = useState(false), [draft, setDraft] = useState("");
  const body = useRef<HTMLDivElement>(null);
  const show = !!s.panel || (hover && s.turns.length > 0);       // a conversation that ends under the pointer waits until it leaves
  const hist = s.panel === "history";
  useEffect(() => { if (!hover && body.current) body.current.scrollTo({ top: body.current.scrollHeight, behavior: "smooth" }); }, [s.turns.length, s.thinking, hover]);
  const send = () => { const q = draft.trim(); if (!q) return; setDraft(""); ask(q); };
  const pick = s.histPick;
  return (
    <AnimatePresence>
      {show && (
        <motion.aside key="jv-panel" aria-label={hist ? "Recent conversations" : "Conversation with Jarvis"} data-jarvis-panel={hist ? "history" : "conv"}
          initial={{ opacity: 0, x: 24 }} animate={{ opacity: 1, x: 0 }} exit={{ opacity: 0, x: 12, transition: { duration: 0.35, ease: "easeIn" } }} transition={{ duration: 0.4, ease: EASE }}
          onPointerEnter={() => setHover(true)} onPointerLeave={() => setHover(false)}
          className="glass-panel fixed bottom-[76px] right-4 top-[72px] z-[57] flex w-[min(380px,calc(100vw-32px))] flex-col !bg-card max-[560px]:top-16">
          <div className="flex h-12 flex-none items-center gap-2 border-b border-ion/[.07] pl-4 pr-2">
            {hist && pick && <button type="button" aria-label="Back to the list" onClick={() => jarvis.set({ histPick: null })} className="grid size-7 cursor-pointer place-items-center rounded-[8px] border-0 bg-transparent text-glass-mute hover:bg-ink/[.06] hover:text-glass-ink"><ArrowLeft size={15} strokeWidth={1.8} /></button>}
            <span className="flex-1 truncate font-mono text-[10.5px] font-medium uppercase tracking-[.2em] text-glass-mute">{hist ? (pick ? when(pick.started) : "Recent conversations") : "Jarvis"}</span>
            <button type="button" aria-label="Close" title={hist ? "Close" : "End the conversation"} onClick={() => { setHover(false); closePanel(); }}
              className="grid size-8 cursor-pointer place-items-center rounded-[10px] border-0 bg-transparent text-glass-mute hover:bg-ink/[.06] hover:text-glass-ink"><X size={15} strokeWidth={1.8} /></button>
          </div>
          <div ref={body} className="pane flex min-h-0 flex-1 flex-col gap-4 overflow-y-auto px-4 py-4">
            {!hist && !s.turns.length && !s.thinking && <p className="m-0 text-[13px] leading-relaxed text-glass-mute">Type below, or press the mic and talk. Answers come the same way either way: spoken, or as captions in office mode.</p>}
            {!hist && s.turns.map((t, i) => <Turn key={i} t={t} />)}
            {!hist && s.thinking && <div className="flex items-center gap-2 text-[12.5px] text-glass-mute"><i aria-hidden className="arc-dot jv-think" />Thinking…</div>}
            {hist && !pick && (s.history == null ? <p className="m-0 text-[13px] text-glass-mute">Loading…</p>
              : !s.history.length ? <p className="m-0 text-[13px] text-glass-mute">No conversations in the last 7 days.</p>
              : s.history.map((c) => (
                <button key={c.id} type="button" onClick={() => jarvis.set({ histPick: c })}
                  className="grid cursor-pointer gap-1 rounded-[12px] border-0 bg-transparent px-3 py-2.5 text-left transition-colors hover:bg-ink/[.04]">
                  <span className="font-mono text-[11px] text-glass-mute">{when(c.started)} · {c.turns.length} {c.turns.length === 1 ? "turn" : "turns"}</span>
                  <span className="truncate text-[14px] text-glass-ink-2">{c.turns[0]?.q || ""}</span>
                </button>
              )))}
            {hist && pick && (pick.turns || []).map((t: any, i: number) => (
              <div key={i} className="grid gap-3">
                <Turn t={{ me: true, text: t.q || "" }} />
                <Turn t={{ text: /^Asked Claude:/.test(String(t.text || "")) ? (untag(t.speak || "") || "Asked Claude to look into it.") : answerText(t.speak || "", t.text || "") }} />
              </div>
            ))}
          </div>
          {!hist && (
            <form onSubmit={(e) => { e.preventDefault(); send(); }} className="flex flex-none items-center gap-2 border-t border-ion/[.07] px-3 py-2.5">
              <input data-jarvis-input value={draft} onChange={(e) => setDraft(e.target.value)} placeholder="Type to Jarvis" aria-label="Type to Jarvis" maxLength={2000} autoComplete="off"
                className="h-9 min-w-0 flex-1 rounded-[10px] border-0 bg-ink/[.04] px-3 text-[14px] text-glass-ink outline-none ring-1 ring-inset ring-ion/[.08] placeholder:text-glass-mute focus:ring-ion/[.3]" />
              {s.native && <button type="button" onClick={() => talk()} aria-label="Talk to Jarvis" title="Talk"
                className="grid size-9 flex-none cursor-pointer place-items-center rounded-[10px] border-0 bg-transparent text-glass-mute ring-1 ring-inset ring-ion/[.08] hover:bg-ink/[.06] hover:text-glass-ink"><Mic size={15} strokeWidth={1.8} /></button>}
              <button type="submit" aria-label="Send to Jarvis" disabled={!draft.trim()}
                className="grid size-9 flex-none cursor-pointer place-items-center rounded-[10px] border-0 bg-ion/[.14] text-glass-ink ring-1 ring-inset ring-ion/[.22] hover:bg-ion/[.22] disabled:cursor-default disabled:opacity-40"><ArrowUp size={15} strokeWidth={2} /></button>
            </form>
          )}
        </motion.aside>
      )}
    </AnimatePresence>
  );
}

export default function Jarvis() {
  const s = useJarvis();
  useEffect(() => { initJarvis(); }, []);
  // Esc: cancel a waiting action (before anything else sees the key) and stop the voice.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const el = e.target as HTMLElement | null;
      if (e.key === "/" && !e.metaKey && !e.ctrlKey && !e.altKey && !(el && (el.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(el.tagName)))) {
        e.preventDefault(); openChat(); return;
      }
      if (e.key !== "Escape") return;
      if (cancelPending()) { e.preventDefault(); e.stopPropagation(); return; }
      if (jarvis.get().speaking) stopSpeaking();
    };
    window.addEventListener("keydown", onKey, true);
    return () => window.removeEventListener("keydown", onKey, true);
  }, []);
  return (
    <>
      <Panel s={s} />
      <Caption s={s} />
      <EnrolDialog open={s.dialog === "enrol"} native={s.native} extras={s.extras} />
      <VoicesDialog open={s.dialog === "voices"} native={s.native} voices={s.voices} current={s.voice} />
      <StatsDialog open={s.dialog === "stats"} />
    </>
  );
}
