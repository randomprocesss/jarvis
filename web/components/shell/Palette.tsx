"use client";
import { useEffect, useState } from "react";
import { House, Inbox, Ticket, Clock, List, Sun, Moon, Search, Send, Pencil, RefreshCw, Grid3x3, ChevronLeft, ChevronRight, Sparkles, AudioLines, Mic, MicOff, MessagesSquare } from "lucide-react";
import { WH, VIEWS, useWH } from "@/lib/wh";
import { Dialog, DialogContent, DialogTitle } from "@/components/ui/dialog";
import { Command, CommandInput, CommandItem, CommandList } from "@/components/ui/command";
import { ask, openDialog, loadHistory, setListening, useJ } from "@/lib/jarvis";

const ICONS: Record<string, any> = { home: House, inbox: Inbox, ticket: Ticket, clock: Clock, list: List, sun: Sun, moon: Moon, send: Send, edit: Pencil, refresh: RefreshCw, grid: Grid3x3, left: ChevronLeft, right: ChevronRight, spark: Sparkles, voice: AudioLines, mic: Mic, micOff: MicOff, chat: MessagesSquare };

export function Palette({ name }: { name: string }) {
  useWH(["theme"]);
  const native = useJ((s) => s.native), listening = useJ((s) => s.listening);
  const [open, setOpen] = useState(false), [q, setQ] = useState(""), [sel, setSel] = useState("0");
  useEffect(() => WH.on("palette", (v) => { setOpen((o) => (v === "toggle" ? !o : !!v)); setQ(""); setSel("0"); }), []);
  const core = [
    { l: "Go to Today", k: "home start dashboard needs you", ic: "home", g: "Go", run: () => WH.go("today") },
    { l: "Go to Inbox", k: "to do follow ups action items waiting", ic: "inbox", g: "Go", run: () => WH.go("inbox") },
    { l: "Go to Activity", k: "what jarvis did running finished actions sent", ic: "clock", g: "Go", run: () => WH.go("activity") },
    { l: "Ask the knowledge base", k: "brain ledger facts lookup search question", ic: "inbox", g: "Go", run: () => WH.go("brain/ask") },
    { l: "Go to Brain: open questions", k: "open questions waiting unanswered", ic: "inbox", g: "Go", run: () => WH.go("brain/questions") },
    { l: "Go to Brain: promises", k: "promises owe owed commitments", ic: "inbox", g: "Go", run: () => WH.go("brain/promises") },
    { l: "Go to Brain: outside research", k: "research papers trials findings", ic: "inbox", g: "Go", run: () => WH.go("brain/research") },
    { l: "Go to Trust: levels", k: "autonomy ladder levels ceilings scorecard plan", ic: "home", g: "Go", run: () => WH.go("trust") },
    { l: "Go to Jira tickets", k: "proposals create", ic: "ticket", g: "Go", run: () => WH.go("tickets") },
    { l: "Show activity log", k: "log history errors", ic: "list", g: "App", run: () => WH.ask("Activity log", WH.logs.length ? WH.logs.map((l) => `[${l.ts}] ${l.title}\n${l.text}`).join("\n\n") : "Nothing yet.", "Close") },
  ];
  const jv = [
    { l: "Office panel", k: "office monitor side panel dashboard external screen", ic: "voice", g: "Jarvis", run: () => WH.go("office") },
    { l: "Teach Jarvis your voice", k: "voice wake word enrol train microphone", ic: "voice", g: "Jarvis", run: () => openDialog("enrol") },
    { l: "Choose Jarvis's voice", k: "voice speech speaker sound", ic: "voice", g: "Jarvis", run: () => openDialog("voices") },
    { l: "Recent conversations", k: "history chat jarvis talk", ic: "chat", g: "Jarvis", run: () => loadHistory() },
    { l: "Voice stats", k: "voice latency timing speed barge metrics", ic: "voice", g: "Jarvis", run: () => openDialog("stats") },
    ...(native ? [{ l: listening ? "Turn listening off" : "Turn listening on", k: "listening microphone mic on off", ic: listening ? "micOff" : "mic", g: "Jarvis", run: () => setListening(!listening) }] : []),
  ];
  let view: any[] = [];
  if (open) { try { view = (WH.shell.actions && WH.shell.actions()) || []; } catch {} }
  const match = (v: string) => { const toks = v.toLowerCase().trim().split(/\s+/).filter(Boolean); return [...view.map((a) => ({ g: VIEWS[name]?.title, ...a })), ...core, ...jv].filter((a) => toks.every((t) => (a.l + " " + (a.k || "") + " " + (a.g || "")).toLowerCase().includes(t))); };
  // Typed text: "Ask Jarvis: …" comes first; Enter still runs the first matching command when one matches (as before).
  const typed = q.trim(), found = match(q);
  const items = [...(typed ? [{ l: "Ask Jarvis: " + typed, ic: "spark", g: "Jarvis", ask: true, run: () => ask(typed) }] : []), ...found];
  // cmdk selects the first row after each keystroke; this runs after it, so Enter keeps running the first matching command.
  const pick = typed && found.length ? "1" : "0";
  useEffect(() => { setSel(pick); }, [q]);   // eslint-disable-line react-hooks/exhaustive-deps
  const run = (a) => { setOpen(false); setTimeout(() => a.run(), 80); };
  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogContent aria-describedby={undefined} className="glass-panel overflow-hidden border-0 !bg-card shadow-lg">
        <DialogTitle className="sr-only">Commands</DialogTitle>
        <Command shouldFilter={false} value={sel} onValueChange={setSel} loop>
          <div className="flex items-center gap-3 border-b border-line px-[18px] py-[18px]">
            <Search size={16} strokeWidth={1.8} className="text-arc-hi" aria-hidden />
            <CommandInput value={q} onValueChange={(v) => { setQ(v); setSel("0"); }} placeholder="Type a command, or ask Jarvis" autoComplete="off" spellCheck={false} aria-label="Search commands" />
          </div>
          <CommandList>
            {items.length ? items.map((a, i) => {
              const Icon = ICONS[a.ic] || Sparkles;
              return (
                <CommandItem key={i + a.l} value={String(i)} onSelect={() => run(a)}>
                  <Icon size={15} strokeWidth={1.8} aria-hidden />
                  <span className={a.ask ? "min-w-0 truncate" : undefined}>{a.l}</span>
                  {a.h ? <span className="ml-auto rounded-[5px] border border-line-3 bg-card px-1.5 py-0.5 font-mono text-[11.5px] text-ink-2">{a.h}</span>
                    : <span className="ml-auto font-mono text-[11px] uppercase tracking-[.06em] text-mute">{a.g || ""}</span>}
                </CommandItem>
              );
            }) : <div className="px-3 py-2.5 text-[14.5px] text-mute">No matching command</div>}
          </CommandList>
          <div className="flex gap-3.5 border-t border-line px-[18px] py-2.5 font-mono text-xs text-mute"><span>↑↓ move</span><span>↵ run</span><span>esc close</span></div>
        </Command>
      </DialogContent>
    </Dialog>
  );
}
