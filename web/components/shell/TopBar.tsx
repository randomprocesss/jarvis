"use client";
/* Sticky header over the content (redesign, 12 Nov 2026): the page's name, the save status, search. Palette colours only. */
import { Menu, Search } from "lucide-react";
import { WH, VIEWS, useWH } from "@/lib/wh";
import { cn } from "@/lib/utils";
import { BrandMark, navCounts } from "@/components/shell/Sidebar";

export function TopBar({ name, onMenu }: { name: string; onMenu: () => void }) {
  useWH(["inbox", "tickets", "approvals", "shell", "theme"]);
  const st = WH.shell.status, counts = navCounts();
  const total = (counts.today || 0) + (counts.inbox || 0);
  return (
    <header className="sticky top-0 z-30 flex h-14 items-center gap-3 border-b border-line bg-canvas/85 px-[clamp(16px,3vw,32px)] backdrop-blur-md">
      <button type="button" onClick={onMenu} aria-label="Open menu" className="relative -ml-1.5 hidden size-9 place-items-center rounded-[9px] border-0 bg-transparent text-ink hover:bg-line-2 max-[899px]:grid">
        <Menu size={19} strokeWidth={1.8} />
        {total ? <span className="absolute right-1 top-1 size-2 rounded-full bg-ion" /> : null}
      </button>
      <a href="#/today" aria-label="Jarvis" className="hidden items-center gap-2 no-underline max-[899px]:flex"><BrandMark size={18} /></a>
      <div className="min-w-0 truncate text-[14.5px] font-semibold tracking-[-.005em] text-ink" data-crumb>{VIEWS[name]?.title}</div>
      <div className="ml-auto flex items-center gap-1.5">
        {st && (
          <span className={cn("inline-flex items-center gap-2 whitespace-nowrap rounded-full border px-[11px] py-[6px] text-xs font-medium leading-none",
            st.kind === "dirty" ? "border-warn/35 bg-warn-soft text-warn" : "border-line bg-card text-ink-2")} data-status={st.kind}>
            <i className={cn("size-[7px] rounded-full", st.kind === "dirty" ? "bg-warn" : st.kind === "saved" ? "bg-ok" : "bg-mute")} />
            <span className="max-[560px]:hidden">{st.text}</span>
          </span>
        )}
        <button type="button" title="Search and commands (⌘K)" onClick={() => WH.emit("palette", "toggle")} aria-label="Search and commands"
          className="inline-flex h-9 items-center gap-2 rounded-[9px] border border-line bg-card px-3 text-[13px] text-mute hover:border-line-3 hover:text-ink max-[899px]:size-9 max-[899px]:justify-center max-[899px]:px-0">
          <Search size={15} strokeWidth={1.8} /><span className="max-[899px]:hidden">Search</span><kbd className="font-mono text-[10.5px] max-[899px]:hidden">⌘K</kbd>
        </button>
      </div>
    </header>
  );
}
