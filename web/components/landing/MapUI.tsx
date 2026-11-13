"use client";
/* The map as a tool (13 Nov 2026, the owner: "make explore the maps view also really really good"). Everything around the
   canvas while Explore is on: search with a list of what matches (Enter shows it on the map, Shift+Enter asks Jarvis),
   lenses and the legend (each kind with its shape and count; a click hides or shows it), an overview of what Jarvis
   knows (counts read from the knowledge base ledger by /api/brain, never a record's text), the inspector of what is
   picked (a workstream's make-up, every connection as a step to the next node), and zoom / fit. Esc clears. Names and counts only: this screen can be on the office monitor. */
import { useEffect, useMemo, useRef, useState, type RefObject } from "react";
import { ArrowUpRight, Maximize2, MessageCircleQuestion, Minus, Plus, Search, X } from "lucide-react";
import { WH } from "@/lib/wh";
import { cn } from "@/lib/utils";
import { KINDS, RING, kindColor, nodeLine, type BNode, type Brain, type MapHandle } from "@/components/landing/BrainMap";

const C = (s: any) => WH.clean(s);
const fmt = (n?: number) => (n || 0).toLocaleString("en-GB");
const panel = "rounded-[14px] border border-[rgba(159,231,255,.16)] bg-[rgba(6,16,23,.8)] shadow-[0_18px_50px_-24px_rgba(0,0,0,.85)] backdrop-blur-md";
const kicker = "font-mono text-[10px] font-semibold uppercase tracking-[.18em] text-glass-mute";
const ALL = Object.keys(KINDS);
const LENSES: [string, string, string[]][] = [
  ["Everything", "all of it", ALL],
  ["Knowledge", "workstreams, the things and tickets they share, memory", ["stream", "table", "ticket", "topic", "note"]],
  ["Asks & people", "inbox items, who asked, where it came from", ["item", "person", "source", "ticket"]],
  ["Code", "the repositories", ["repo", "source"]],
];
const RANK: Record<string, number> = { stream: 0, topic: 1, person: 2, table: 3, ticket: 4, repo: 5, source: 6, item: 7, note: 8 };

/** The legend's mark for a kind: the same shape the map draws. */
function Mark({ kind, size = 10 }: { kind: string; size?: number }) {
  const c = kindColor(kind), sh = KINDS[kind]?.shape, s = { width: size, height: size } as any;
  if (sh === "diamond") return <i aria-hidden className="inline-block flex-none rotate-45 rounded-[1.5px]" style={{ ...s, width: size * 0.8, height: size * 0.8, background: c }} />;
  if (sh === "square") return <i aria-hidden className="inline-block flex-none rounded-[2.5px]" style={{ ...s, background: c }} />;
  if (sh === "frame") return <i aria-hidden className="inline-block flex-none rounded-[2.5px]" style={{ ...s, border: `1.5px solid ${c}`, background: c + "33" }} />;
  if (sh === "ring") return <i aria-hidden className="inline-block flex-none rounded-full" style={{ ...s, border: `2px solid ${c}` }} />;
  if (sh === "hub") return <i aria-hidden className="inline-block flex-none rounded-full" style={{ ...s, background: c, boxShadow: `0 0 0 2px rgba(6,16,23,1), 0 0 0 3.5px ${c}` }} />;
  return <i aria-hidden className="inline-block flex-none rounded-full" style={{ ...s, background: c }} />;
}

export default function MapUI({ brain, sel, onPick, q, setQ, show, setShow, openOnly, setOpenOnly, map }: {
  brain: Brain | null; sel: BNode | null; onPick: (n: BNode | null) => void; q: string; setQ: (s: string) => void;
  show: Set<string>; setShow: (s: Set<string>) => void; openOnly: boolean; setOpenOnly: (b: boolean) => void; map: RefObject<MapHandle | null>;
}) {
  const input = useRef<HTMLInputElement>(null);
  const [act, setAct] = useState(0);
  const [more, setMore] = useState<string>("");            // the connection group shown in full

  const G = useMemo(() => {
    const by = new Map<string, BNode>(), adj = new Map<string, string[]>();
    (brain?.nodes || []).forEach((n) => by.set(n.id, n));
    (brain?.links || []).forEach(([a, b]) => { if (!by.has(a) || !by.has(b)) return; (adj.get(a) || adj.set(a, []).get(a)!).push(b); (adj.get(b) || adj.set(b, []).get(b)!).push(a); });
    return { by, adj };
  }, [brain]);
  const deg = (n: BNode) => (G.adj.get(n.id) || []).length;
  const pick = (n: BNode | null) => { setMore(""); onPick(n); if (n) map.current?.focus(n.id); };
  const ask = (t: string) => { const s = t.trim(); if (s) { WH.pref.set("wh_ask", s); WH.go("brain/ask"); } };

  const term = q.trim().toLowerCase();
  const results = useMemo(() => {
    if (!term) return [];
    return (brain?.nodes || []).filter((n) => n.label.toLowerCase().includes(term) || (n.who || "").toLowerCase().includes(term))
      .sort((a, b) => (RANK[a.kind] ?? 9) - (RANK[b.kind] ?? 9) || Number(b.label.toLowerCase().startsWith(term)) - Number(a.label.toLowerCase().startsWith(term)) || a.label.localeCompare(b.label));
  }, [brain, term]);
  useEffect(() => { setAct(0); }, [term]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      // ("/" is the app's own key for Ask Jarvis, so the map takes no key of its own for search: the box has the focus on the way in)
      if (e.key === "Escape") { if (sel) pick(null); else if (q) setQ(""); else input.current?.blur(); }
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  });

  const lensOn = (kinds: string[]) => kinds.length === show.size && kinds.every((k) => show.has(k));
  const toggle = (k: string) => { const s = new Set(show); if (s.has(k)) s.delete(k); else s.add(k); setShow(s); };
  const counts = brain?.counts || {}, kb = brain?.kb || null;
  const streams = useMemo(() => (brain?.nodes || []).filter((n) => n.kind === "stream").sort((a, b) => (b.n || 0) - (a.n || 0)), [brain]);
  const openItems = (brain?.nodes || []).filter((n) => n.kind === "item" && !n.done).length;

  // the picked node's connections, by kind, in the legend's order
  const groups = useMemo(() => {
    if (!sel) return [];
    const ns = (G.adj.get(sel.id) || []).map((id) => G.by.get(id)!).filter(Boolean);
    return ALL.map((k) => [k, ns.filter((n) => n.kind === k).sort((a, b) => (b.n || 0) - (a.n || 0) || a.label.localeCompare(b.label))] as [string, BNode[]]).filter(([, l]) => l.length);
  }, [sel, G]);
  const live = sel ? G.by.get(sel.id) || sel : null;       // the newest copy of what is picked (the map's data refreshes)

  return (
    <>
      {/* left: search, then what is on the map */}
      <div className="absolute left-6 top-16 z-30 grid w-[min(300px,calc(100%-48px))] grid-cols-[minmax(0,1fr)] gap-3" data-no-apart>
        <form onSubmit={(e) => { e.preventDefault(); }} className="relative" role="search">
          <Search size={15} className="pointer-events-none absolute left-3 top-[13px] text-glass-mute" />
          <input ref={input} autoFocus value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search the map" aria-label="Search the map"
            onKeyDown={(e) => {
              if (e.key === "ArrowDown") { e.preventDefault(); setAct((a) => Math.min(results.length - 1, a + 1)); }
              else if (e.key === "ArrowUp") { e.preventDefault(); setAct((a) => Math.max(0, a - 1)); }
              else if (e.key === "Enter") { e.preventDefault(); if (e.shiftKey || !results.length) ask(q); else pick(results[Math.min(act, results.length - 1)]); }
            }}
            className="h-10 w-full rounded-full border border-[rgba(159,231,255,.2)] bg-[rgba(6,18,26,.78)] pl-9 pr-10 text-[13.5px] text-glass-ink outline-none backdrop-blur placeholder:text-glass-mute focus:border-[rgba(159,231,255,.5)]" />
          {q ? <button type="button" onClick={() => { setQ(""); input.current?.focus(); }} aria-label="Clear" className="absolute right-2.5 top-2 grid size-6 place-items-center rounded-full border-0 bg-transparent text-glass-mute hover:text-glass-ink"><X size={14} /></button>
            : null}
          {term && (
            <div className={cn(panel, "mt-2 overflow-hidden")} data-testid="map-results">
              {results.length ? (
                <ul className="m-0 max-h-[300px] list-none overflow-y-auto p-1.5">{results.slice(0, 40).map((n, i) => (
                  <li key={n.id}>
                    <button type="button" onClick={() => pick(n)} onMouseEnter={() => setAct(i)}
                      className={cn("flex w-full items-center gap-2.5 rounded-[9px] px-2.5 py-1.5 text-left", i === act ? "bg-[rgba(159,231,255,.1)]" : "hover:bg-[rgba(159,231,255,.06)]")}>
                      <Mark kind={n.kind} />
                      <span className="min-w-0 flex-1 truncate text-[13px] text-glass-ink">{C(n.label)}</span>
                      <span className="flex-none text-[11px] text-glass-mute">{KINDS[n.kind]?.one || n.kind}</span>
                    </button>
                  </li>))}</ul>
              ) : <p className="m-0 px-3.5 py-3 text-[13px] text-glass-ink-2">Nothing on the map is called that.</p>}
              <div className="flex flex-wrap items-center gap-x-3 border-t border-[rgba(159,231,255,.12)] px-3.5 py-2 text-[11px] text-glass-mute">
                {results.length > 0 && <span>{results.length} match{results.length === 1 ? "" : "es"} · <b className="font-medium text-glass-ink-2">↵</b> show on the map</span>}
                <button type="button" onClick={() => ask(q)} className="ml-auto inline-flex items-center gap-1 rounded-full border-0 bg-transparent p-0 text-[11.5px] font-medium text-[#8fe9f7] hover:underline"><MessageCircleQuestion size={12} />Ask Jarvis <span className="text-glass-mute">⇧↵</span></button>
              </div>
            </div>)}
        </form>

        {!term && (
          <div className={cn(panel, "p-3 max-[900px]:hidden")} data-testid="map-legend">
            <div className="flex flex-wrap gap-1">{LENSES.map(([name, hint, kinds]) => (
              <button key={name} type="button" title={hint} aria-pressed={lensOn(kinds)} onClick={() => setShow(new Set(kinds))}
                className={cn("rounded-full px-2.5 py-1 text-[11.5px] font-medium transition-colors", lensOn(kinds) ? "bg-[rgba(143,233,247,.16)] text-[#bdf3fb]" : "text-glass-mute hover:bg-[rgba(159,231,255,.07)] hover:text-glass-ink")}>{name}</button>))}</div>
            <ul className="m-0 mt-2 list-none border-t border-[rgba(159,231,255,.1)] p-0 pt-1.5">{ALL.filter((k) => counts[k]).map((k) => (
              <li key={k}>
                <button type="button" onClick={() => toggle(k)} aria-pressed={show.has(k)} title={show.has(k) ? "Hide these" : "Show these"}
                  className={cn("flex w-full items-center gap-2.5 rounded-[8px] px-1.5 py-[5px] text-left transition-opacity hover:bg-[rgba(159,231,255,.06)]", !show.has(k) && "opacity-35")}>
                  <span className="grid w-3.5 place-items-center"><Mark kind={k} /></span>
                  <span className="flex-1 text-[12.5px] text-glass-ink-2">{KINDS[k].label}</span>
                  <span className="font-mono text-[11.5px] tabular-nums text-glass-mute">{counts[k]}</span>
                </button>
              </li>))}</ul>
            {(counts.item || 0) > 0 && (
              <label className="mt-1.5 flex cursor-pointer items-center gap-2.5 border-t border-[rgba(159,231,255,.1)] px-1.5 pt-2 text-[12px] text-glass-ink-2">
                <input type="checkbox" checked={openOnly} onChange={(e) => setOpenOnly(e.target.checked)} className="size-3.5 accent-[#36e2c3]" />
                Only open inbox items <span className="ml-auto font-mono text-[11.5px] tabular-nums text-glass-mute">{openItems}</span>
              </label>)}
          </div>)}
      </div>

      {/* right: what is picked, or what Jarvis knows */}
      <div className="absolute right-6 top-16 z-30 w-[min(320px,calc(100%-48px))] max-[900px]:bottom-16 max-[900px]:top-auto" data-no-apart>
        {live ? (
          <div className={cn(panel, "max-h-[calc(100dvh-150px)] overflow-y-auto p-4 text-left max-[900px]:max-h-[46dvh]")} data-testid="map-inspector">
            <div className={cn(kicker, "flex items-center gap-2")}>
              <Mark kind={live.kind} />{KINDS[live.kind]?.one || live.kind}
              <button type="button" onClick={() => pick(null)} aria-label="Close" className="ml-auto grid size-6 place-items-center rounded-md border-0 bg-transparent text-glass-mute hover:text-glass-ink"><X size={14} /></button>
            </div>
            <b className="mt-2 block text-[15.5px] font-semibold leading-snug text-glass-ink [overflow-wrap:anywhere]">{C(live.label)}</b>
            <div className="mt-1 text-[12.5px] leading-snug text-glass-ink-2">{C(nodeLine(live, deg(live)))}</div>

            {live.kind === "stream" && (() => {
              const tot = RING.reduce((s, [k]) => s + (Number(live[k]) || 0), 0) || 1;
              return (
                <div className="mt-3">
                  <div className="flex h-2 overflow-hidden rounded-full bg-white/5">{RING.map(([k, , c]) => <span key={String(k)} style={{ width: `${((Number(live[k]) || 0) / tot) * 100}%`, background: c }} />)}</div>
                  <dl className="m-0 mt-2.5 grid grid-cols-2 gap-x-4 gap-y-1.5">{RING.map(([k, name, c]) => (
                    <div key={String(k)} className="flex items-baseline gap-2"><i aria-hidden className="size-2 flex-none translate-y-[-1px] rounded-full" style={{ background: c }} />
                      <dt className="flex-1 text-[12px] text-glass-mute">{name}</dt><dd className="m-0 font-mono text-[12.5px] tabular-nums text-glass-ink">{fmt(Number(live[k]))}</dd></div>))}</dl>
                  {(live.old || 0) > 0 && <p className="m-0 mt-2 text-[11.5px] text-glass-mute">{fmt(live.old)} replaced records are kept as history and never given as current.</p>}
                </div>);
            })()}

            {groups.map(([k, list]) => (
              <div key={k} className="mt-3.5">
                <div className={cn(kicker, "flex items-center gap-2")}>{KINDS[k].label}<span className="text-glass-ink-2">{list.length}</span></div>
                <div className="mt-1.5 flex flex-wrap gap-1.5">
                  {(more === k ? list : list.slice(0, 8)).map((n) => (
                    <button key={n.id} type="button" onClick={() => pick(n)} title={C(nodeLine(n, deg(n)))}
                      className="inline-flex max-w-full items-center gap-1.5 rounded-full border border-[rgba(159,231,255,.14)] bg-[rgba(159,231,255,.04)] px-2.5 py-1 text-left text-[12px] text-glass-ink-2 transition-colors hover:border-[rgba(159,231,255,.4)] hover:text-glass-ink">
                      <Mark kind={n.kind} size={8} /><span className="min-w-0 truncate">{C(n.label)}</span>
                    </button>))}
                  {more !== k && list.length > 8 && <button type="button" onClick={() => setMore(k)} className="rounded-full border-0 bg-transparent px-1.5 py-1 text-[12px] font-medium text-[#8fe9f7] hover:underline">+{list.length - 8} more</button>}
                </div>
              </div>))}
            {!groups.length && <p className="m-0 mt-3 text-[12.5px] text-glass-mute">Nothing on the map is linked to it yet.</p>}

            <div className="mt-4 flex flex-wrap gap-2 border-t border-[rgba(159,231,255,.1)] pt-3">
              {live.kind === "item" && live.key && <button type="button" className="btn-arc !min-h-0 !px-3.5 !py-2 !text-[12.5px]" onClick={() => { WH.focusItem = live.key!; WH.go("inbox/todo/" + encodeURIComponent(live.key!)); }}>Open in the inbox</button>}
              {live.kind === "ticket" && !!WH.me.ticket_base && <a className="btn-arc !min-h-0 !px-3.5 !py-2 !text-[12.5px] no-underline" href={WH.ticketUrl(live.label)} target="_blank" rel="noopener">Open in Jira <ArrowUpRight size={13} /></a>}
              <button type="button" onClick={() => ask(live.kind === "table" ? `${live.detail || "thing"} ${live.label}` : live.label)}
                className={cn("inline-flex items-center gap-1.5 rounded-full px-3.5 py-2 text-[12.5px] font-medium transition-colors", live.kind === "item" || live.kind === "ticket" ? "border border-[rgba(159,231,255,.2)] bg-transparent text-glass-ink-2 hover:text-glass-ink" : "btn-arc !min-h-0")}>
                <MessageCircleQuestion size={13} />Ask Jarvis about it
              </button>
            </div>
          </div>
        ) : (
          <div className={cn(panel, "max-h-[calc(100dvh-150px)] overflow-y-auto p-4 text-left max-[900px]:hidden")} data-testid="map-overview">
            <div className={kicker}>What Jarvis knows</div>
            {kb ? (<>
              <div className="mt-2 font-display text-[34px] font-semibold leading-none tracking-[-.02em] tabular-nums text-glass-ink">{fmt(kb.records)}</div>
              <div className="mt-1 text-[12.5px] text-glass-ink-2">current records in the knowledge base's {kb.streams} workstreams</div>
              <dl className="m-0 mt-3 grid grid-cols-3 gap-2">{([["Workstreams", kb.streams], ["Open points", kb.open], ["Decisions", kb.decisions]] as [string, number][]).map(([l, n]) => (
                <div key={l} className="rounded-[10px] border border-[rgba(159,231,255,.1)] bg-[rgba(159,231,255,.03)] px-2.5 py-2">
                  <dd className="m-0 font-mono text-[14px] font-semibold tabular-nums text-glass-ink">{fmt(n)}</dd><dt className="mt-0.5 text-[10.5px] text-glass-mute">{l}</dt></div>))}</dl>
              <p className="m-0 mt-2 text-[11.5px] leading-snug text-glass-mute">{fmt(kb.old)} replaced records kept as history · ledger of {new Date(kb.at).toLocaleDateString("en-GB", { day: "numeric", month: "short" })} {new Date(kb.at).toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit" })}</p>
              <div className={cn(kicker, "mt-4")}>Largest workstreams</div>
              <ul className="m-0 mt-1.5 list-none p-0">{streams.slice(0, 7).map((n) => (
                <li key={n.id}>
                  <button type="button" onClick={() => pick(n)} className="group grid w-full grid-cols-[minmax(0,1fr)_auto] items-center gap-x-3 rounded-[8px] px-1.5 py-[5px] text-left hover:bg-[rgba(159,231,255,.06)]">
                    <span className="min-w-0">
                      <span className="block truncate text-[12.5px] text-glass-ink-2 group-hover:text-glass-ink">{C(n.label)}</span>
                      <span className="mt-1 block h-[3px] overflow-hidden rounded-full bg-white/5"><span className="block h-full rounded-full bg-[#8fe9f7]/70" style={{ width: `${((n.n || 0) / (streams[0]?.n || 1)) * 100}%` }} /></span>
                    </span>
                    <span className="font-mono text-[11.5px] tabular-nums text-glass-mute">{fmt(n.n)}</span>
                  </button>
                </li>))}</ul>
            </>) : <p className="m-0 mt-2 text-[12.5px] text-glass-ink-2">The knowledge base ledger could not be read; the map shows the inbox, tickets, repos and memory notes.</p>}
            <div className={cn(kicker, "mt-4")}>Also on the map</div>
            <p className="m-0 mt-1.5 text-[12px] leading-relaxed text-glass-ink-2">
              {[counts.item && `${counts.item} inbox items (${openItems} open)`, counts.ticket && `${counts.ticket} tickets`, counts.table && `${counts.table} things`, counts.person && `${counts.person} people`, counts.note && `${counts.note} memory notes`, counts.repo && `${counts.repo} repos`].filter(Boolean).join(" · ")}
            </p>
            <p className="m-0 mt-3 border-t border-[rgba(159,231,255,.1)] pt-2.5 text-[11.5px] leading-snug text-glass-mute">Click a dot to see what it connects. Names and counts only: nothing here shows a record's text.</p>
          </div>
        )}
      </div>

      {/* bottom right: the camera */}
      <div className="absolute bottom-5 right-6 z-30 flex items-center gap-3" data-no-apart>
        <span className="font-mono text-[10px] uppercase tracking-[.16em] text-glass-mute max-[1100px]:hidden">Scroll to zoom · drag to move · double-click to zoom in</span>
        <div className={cn(panel, "flex items-center gap-0.5 !rounded-full p-1")}>
          {([[Minus, "Zoom out", () => map.current?.zoom(1 / 1.35)], [Plus, "Zoom in", () => map.current?.zoom(1.35)], [Maximize2, "Fit everything", () => map.current?.fit()]] as [any, string, () => void][]).map(([Icon, label, run]) => (
            <button key={label} type="button" onClick={run} aria-label={label} title={label} className="grid size-8 place-items-center rounded-full border-0 bg-transparent text-glass-ink-2 transition-colors hover:bg-[rgba(159,231,255,.1)] hover:text-glass-ink"><Icon size={15} /></button>))}
        </div>
      </div>
    </>
  );
}
