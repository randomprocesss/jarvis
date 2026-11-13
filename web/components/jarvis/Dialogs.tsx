"use client";
/* "Teach Jarvis your voice" (one step at a time: 30 x "Jarvis", a minute of reading aloud, training, a test) and
   "Choose Jarvis's voice" (play a sample, pick one). Both talk to the Mac app through lib/jarvis.ts post(). */
import { useEffect, useRef, useState } from "react";
import { Check, Play } from "lucide-react";
import { post, onVoice, jarvis, useJ, setProactive } from "@/lib/jarvis";
import { Dialog, DialogContent, DialogDescription, DialogTitle } from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

const CLIPS = 30, AWAY_FROM = 25;
const hint = (n: number) => (n <= 10 ? "In your normal voice" : n <= 17 ? "Quietly" : n <= 24 ? "Quickly" : "From across the room");
const READ = `Good morning. This is a short note to read at your normal pace, the way you would talk in a meeting. The weekly numbers came in a little late, so the review moved to Thursday afternoon. Most of the open items are small: two replies to send, one ticket to update, and a question about lead times that needs a closer look. When you get a moment, check the draft in the inbox and say whether the tone is right. If something looks off, say so and it will be changed before anything goes out. The forecast run finished overnight and the results are in the usual folder. Nothing needs a decision today, but the inventory list should be checked before Friday. Remember to log the hours for the week, and leave a note for the team about the new schedule. That is everything for now. Thank you for reading this; it helps the assistant learn how you sound when you are simply talking.`;

type Step = "intro" | "clips" | "away" | "talk" | "train" | "done" | "fail";
const close = () => jarvis.set({ dialog: "" });

export function EnrolDialog({ open, native }: { open: boolean; native: boolean }) {
  const [step, setStep] = useState<Step>("intro"), [n, setN] = useState(1), [miss, setMiss] = useState(""), [talking, setTalking] = useState(false);
  const [secs, setSecs] = useState(0), [res, setRes] = useState<any>(null), [test, setTest] = useState<"" | "wait" | "heard">("");
  const nRef = useRef(1), stepRef = useRef<Step>("intro"), timers = useRef<any[]>([]), started = useRef(0);
  useEffect(() => { stepRef.current = step; }, [step]);
  const later = (f: () => void, ms: number) => { timers.current.push(setTimeout(f, ms)); };
  const clear = () => { timers.current.forEach(clearTimeout); timers.current = []; };
  const record = (k: number) => {
    nRef.current = k; setN(k);
    post({ type: "enrol", step: "clip", n: k });
    clear(); later(() => setMiss("No answer from the microphone. Check that Jarvis may use it, then press Retry."), 9000);
  };
  const startTalk = () => { setMiss(""); setSecs(0); started.current = Date.now(); setTalking(true); post({ type: "enrol", step: "talk_start" }); };
  const stopTalk = () => { setTalking(false); post({ type: "enrol", step: "talk_stop" }); later(() => setMiss("No answer from the app yet."), 15000); };
  // reset each time it opens
  useEffect(() => { if (open) { clear(); setStep("intro"); setN(1); setMiss(""); setTalking(false); setSecs(0); setRes(null); setTest(""); } }, [open]);
  useEffect(() => onVoice((ev) => {
    const st = stepRef.current;
    if (ev.type === "enrol_clip" && st === "clips" && +ev.n === nRef.current) {
      clear();
      if (!ev.ok) { setMiss("Didn't catch that. Once more."); later(() => { setMiss(""); record(nRef.current); }, 1100); return; }
      setMiss("");
      const k = nRef.current + 1;
      if (k > CLIPS) { setStep("talk"); return; }
      if (k === AWAY_FROM) { nRef.current = k; setN(k); setStep("away"); return; }
      later(() => record(k), 700);
    } else if (ev.type === "enrol_talk" && st === "talk") {
      clear(); setTalking(false);
      if (ev.ok) { setStep("train"); post({ type: "enrol", step: "train" }); }
      else setMiss("That didn't record. Press Start reading to try again.");
    } else if (ev.type === "enrol_result" && st === "train") {
      setRes(ev); setStep(ev.ok ? "done" : "fail");
    } else if (ev.type === "wake" && test === "wait") setTest("heard");
  }), [test]);   // eslint-disable-line react-hooks/exhaustive-deps
  // the reading timer (stops by itself at 95 s)
  useEffect(() => {
    if (!talking) return;
    const id = setInterval(() => { const s = Math.round((Date.now() - started.current) / 1000); setSecs(s); if (s >= 95) stopTalk(); }, 500);
    return () => clearInterval(id);
  }, [talking]);   // eslint-disable-line react-hooks/exhaustive-deps
  const onOpenChange = (o: boolean) => {
    if (o) return;
    clear();
    if (step !== "intro" && step !== "done" && step !== "fail") post({ type: "enrol", step: "cancel" });
    close();
  };
  const pct = Math.round((Math.min(n - 1, CLIPS) / CLIPS) * 100);
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="p-6" data-jarvis-dialog="enrol" onOpenAutoFocus={(e) => e.preventDefault()}>
        <DialogTitle>Teach Jarvis your voice</DialogTitle>
        {!native ? (
          <DialogDescription>This works in the Jarvis app, which can use the microphone. Here you can type to Jarvis from ⌘K.</DialogDescription>
        ) : step === "intro" ? (<>
          <DialogDescription>
            Jarvis learns how you say its name, so it wakes for you and not for a video or someone nearby. You say “Jarvis” 30 times, then read a short paragraph aloud, about five minutes in all. The recordings are deleted after training.
          </DialogDescription>
          <p className="mb-0 mt-3 text-[13.5px] leading-relaxed text-mute">It cuts false wake-ups a lot, but it is not a security lock: someone close enough can still talk to it. Every action still has a spoken read-back and a 3-second cancel.</p>
        </>) : step === "clips" ? (
          <div className="mt-6 grid place-items-center gap-2 py-4 text-center" data-enrol-step="clips">
            <span className="font-mono text-[11px] uppercase tracking-[.2em] text-mute">{hint(n)}</span>
            <span className="font-display text-[30px] font-light tracking-[-.02em] text-ink">Say “Jarvis”</span>
            <span className={cn("min-h-5 text-[13px]", miss ? "text-ink-2" : "text-transparent")}>{miss || "."}</span>
          </div>
        ) : step === "away" ? (
          <DialogDescription>Good. Now the last six from across the room: press Continue, step back a few metres, and say “Jarvis” each time the screen asks.</DialogDescription>
        ) : step === "talk" ? (
          <div className="mt-3 grid gap-3" data-enrol-step="talk">
            <DialogDescription>{talking ? "Read this aloud at your normal pace. Press Done when you reach the end." : "Last step: read a short paragraph aloud, about a minute, so Jarvis learns what your normal talking sounds like (and does not wake on it)."}</DialogDescription>
            {talking && <p className="m-0 max-h-[36vh] overflow-auto rounded-[12px] bg-ink/[.03] px-4 py-3 text-[15px] leading-[1.7] text-ink ring-1 ring-inset ring-ion/[.08]">{READ}</p>}
            {miss && <p className="m-0 text-[13px] text-ink-2">{miss}</p>}
          </div>
        ) : step === "train" ? (
          <DialogDescription><span className="mr-2 inline-block align-[-2px] text-arc-hi"><span className="spin" /></span>Learning your voice. This takes about a minute.</DialogDescription>
        ) : step === "done" ? (<>
          <DialogDescription className="text-ink"><Check size={15} strokeWidth={2} className="mr-1.5 inline align-[-2px] text-arc-hi" />Ready — Jarvis now answers to your voice.</DialogDescription>
          {res && res.held_out_recall != null && <p className="mb-0 mt-2 text-[13px] text-mute">It recognised {Math.round(res.held_out_recall * 100)}% of the clips it kept back for checking.</p>}
          {test && <p className="mb-0 mt-3 text-[14px] text-ink-2" data-enrol-test={test}>{test === "wait" ? "Say “Jarvis” now…" : "Heard you."}</p>}
        </>) : (
          <DialogDescription>Training did not work{res && res.error ? ": " + res.error : "."} Nothing changed; Jarvis still answers to “Hey Jarvis”.</DialogDescription>
        )}
        {native && step === "clips" && (
          <div className="mt-2 grid gap-1.5" aria-label={`${n - 1} of ${CLIPS} recorded`}>
            <div className="h-px w-full bg-ion/[.1]"><div className="h-px bg-arc-hi transition-[width] duration-300" style={{ width: pct + "%" }} /></div>
            <span className="text-right font-mono text-[11px] text-mute">{Math.min(n - 1, CLIPS)} / {CLIPS}</span>
          </div>
        )}
        <div className="mt-[22px] flex flex-wrap items-center justify-end gap-2">
          {native && step === "talk" && talking && <span className="mr-auto font-mono text-[12px] text-mute">{secs} s</span>}
          {(step === "done" || step === "fail" || !native) ? (<>
            {native && step === "done" && <Button variant="ghost" onClick={() => setTest("wait")} disabled={test === "wait"}>Test it</Button>}
            {native && step === "fail" && <Button variant="ghost" onClick={() => { setStep("intro"); setRes(null); }}>Try again</Button>}
            <Button variant="primary" onClick={() => onOpenChange(false)}>Done</Button>
          </>) : (<>
            <Button variant="ghost" onClick={() => onOpenChange(false)}>Cancel</Button>
            {step === "intro" && <Button variant="primary" onClick={() => { post({ type: "enrol", step: "begin" }); setStep("clips"); later(() => record(1), 600); }}>Start</Button>}
            {step === "clips" && miss.startsWith("No answer") && <Button variant="primary" onClick={() => { setMiss(""); record(nRef.current); }}>Retry</Button>}
            {step === "away" && <Button variant="primary" onClick={() => { setStep("clips"); later(() => record(AWAY_FROM), 3000); }}>Continue</Button>}
            {step === "talk" && (talking ? <Button variant="primary" onClick={stopTalk}>Done</Button> : <Button variant="primary" onClick={startTalk}>Start reading</Button>)}
          </>)}
        </div>
      </DialogContent>
    </Dialog>
  );
}

export function VoicesDialog({ open, native, voices, current }: { open: boolean; native: boolean; voices: { id: string; label: string; detail?: string }[]; current: string }) {
  useEffect(() => { if (open) post({ type: "voices" }); }, [open]);
  const choose = (id: string) => { post({ type: "set_voice", voice: id }); jarvis.set({ voice: id }); };
  return (
    <Dialog open={open} onOpenChange={(o) => { if (!o) close(); }}>
      <DialogContent className="p-6" data-jarvis-dialog="voices" onOpenAutoFocus={(e) => e.preventDefault()}>
        <DialogTitle>Choose Jarvis’s voice</DialogTitle>
        {!native ? <DialogDescription>Voices work in the Jarvis app. In a browser, answers show on screen.</DialogDescription>
          : !voices.length ? <DialogDescription>Loading voices…</DialogDescription> : (
            <div className="mt-4 grid gap-1">
              {voices.map((v) => {
                const on = v.id === current;
                return (
                  <div key={v.id} className={cn("flex items-center gap-3 rounded-[12px] px-3 py-2.5", on ? "bg-ion/[.06] ring-1 ring-inset ring-ion/[.12]" : "hover:bg-ink/[.03]")} data-voice={v.id}>
                    <button type="button" aria-label={`Play ${v.label}`} title="Play a sample" onClick={() => post({ type: "preview", voice: v.id })}
                      className="grid size-8 flex-none cursor-pointer place-items-center rounded-full border-0 bg-ink/[.05] text-arc-hi ring-1 ring-inset ring-ion/[.16] hover:bg-ink/[.09]"><Play size={13} strokeWidth={2} /></button>
                    <div className="min-w-0 flex-1">
                      <div className="truncate text-[14.5px] text-ink">{v.label}</div>
                      {v.detail && <div className="truncate text-[12.5px] text-mute">{v.detail}</div>}
                    </div>
                    {on ? <span className="inline-flex flex-none items-center gap-2 px-2 text-[12.5px] text-ink-2"><i aria-hidden className="arc-dot" />In use</span>
                      : <Button size="sm" variant="ghost" onClick={() => choose(v.id)}>Use</Button>}
                  </div>
                );
              })}
            </div>
          )}
        {native && <ProactiveRow />}
        <div className="mt-[22px] flex justify-end"><Button variant="primary" onClick={close}>Done</Button></div>
      </DialogContent>
    </Dialog>
  );
}

/** One switch under the voices: may Jarvis speak first? (off by default) */
function ProactiveRow() {
  const on = useJ((s) => !!s.flags.proactive);
  return (
    <div className="mt-4 flex items-center gap-3 border-t border-ion/[.07] pt-4" data-proactive={on ? "on" : "off"}>
      <div className="min-w-0 flex-1">
        <div className="text-[14.5px] text-ink">Speak up on its own</div>
        <div className="text-[12.5px] text-mute">When a task you started by voice is done, or a new approval is waiting. At most once every 10 minutes.</div>
      </div>
      <Button size="sm" variant={on ? "primary" : "ghost"} onClick={() => setProactive(!on)} aria-pressed={on}>{on ? "On" : "Off"}</Button>
    </div>
  );
}

/* "Voice stats" (⌘K): today's spoken turns, from voice/metrics.ndjson (timings and flags only). */
const sec = (v: number | null | undefined) => (v == null ? "—" : v < 1000 ? `${Math.round(v)} ms` : `${(v / 1000).toFixed(1)} s`);
export function StatsDialog({ open }: { open: boolean }) {
  const [d, setD] = useState<any>(null), [err, setErr] = useState("");
  useEffect(() => {
    if (!open) return;
    setD(null); setErr("");
    fetch("/api/jarvis_metrics", { cache: "no-store" }).then((r) => r.json()).then(setD).catch(() => setErr("Could not read the stats."));
  }, [open]);
  const L = d && d.live, T = d && d.test;
  const Row = ({ k, v, sub }: { k: string; v: string; sub?: string }) => (
    <div className="flex items-baseline gap-3 border-b border-ion/[.06] py-2.5 last:border-0">
      <span className="flex-1 text-[14px] text-ink-2">{k}</span>
      <span className="font-mono text-[14px] text-ink" data-num>{v}</span>
      {sub && <span className="w-[92px] text-right font-mono text-[12px] text-mute">{sub}</span>}
    </div>
  );
  return (
    <Dialog open={open} onOpenChange={(o) => { if (!o) close(); }}>
      <DialogContent className="p-6" data-jarvis-dialog="stats" onOpenAutoFocus={(e) => e.preventDefault()}>
        <DialogTitle>Voice stats</DialogTitle>
        <DialogDescription>Today’s spoken turns, timed from your last word. Timings only; nothing you said is kept.</DialogDescription>
        {err ? <p className="mb-0 mt-4 text-[13.5px] text-ink-2">{err}</p> : !d ? <p className="mb-0 mt-4 text-[13.5px] text-mute">Loading…</p> : (
          <div className="mt-4" data-stats>
            {!L.turns ? <p className="m-0 text-[13.5px] text-mute">No spoken turns yet today.</p> : (<>
              <Row k="First sound" v={sec(L.first_audio.median)} sub={"p90 " + sec(L.first_audio.p90)} />
              <Row k="The answer itself" v={sec(L.answer_audio.median)} sub={"p90 " + sec(L.answer_audio.p90)} />
              <Row k="Barge-ins" v={String(L.barge)} sub={L.false_barge ? `${L.false_barge} false` : "none false"} />
              <Row k="Cut you off" v={String(L.cutoff)} sub={`of ${L.turns} turns`} />
            </>)}
            {T && T.turns > 0 && <p className="mb-0 mt-3 font-mono text-[11.5px] text-mute">Tests today: {T.turns} turns · first sound {sec(T.first_audio.median)} · answer {sec(T.answer_audio.median)}</p>}
          </div>
        )}
        <div className="mt-[22px] flex justify-end"><Button variant="primary" onClick={close}>Done</Button></div>
      </DialogContent>
    </Dialog>
  );
}
