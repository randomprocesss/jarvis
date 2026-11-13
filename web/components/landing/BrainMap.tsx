"use client";
/* Brain map (landing page, 2 Oct): what Jarvis knows about the owner's work as a live force-directed graph on one canvas.
   People, inbox items, Jira tickets, repos, memory notes (by topic) and sources, from /api/brain. Hover lights a node's
   links, click selects it (the page shows it in the inspector), the search and the legend filter it. HOLO adds a slow
   holographic depth turn. A few hundred nodes, plain 2D canvas, no libraries; it settles, then only breathes.

   13 Nov (the owner: "make explore the maps view also really really good"): the knowledge base is on the map. Each workstream
   is a hub whose ring shows its facts, methods, decisions and open points; the things, tickets and people its records
   name hang between the hubs that share them. Every kind has its own shape, so the legend reads without colour alone.
   The camera flies to what is picked (focus), zooms at the cursor, and fits on request (the MapHandle); a hovered dot
   says what it is; an open inbox item pulses. */
import { forwardRef, useEffect, useImperativeHandle, useRef } from "react";

export type BNode = { id: string; kind: string; label: string; who?: string; src?: string; state?: string; done?: boolean; key?: string; detail?: string; when?: string;
  n?: number; facts?: number; methods?: number; decisions?: number; open?: number; old?: number };
export type Brain = { nodes: BNode[]; links: [string, string][]; counts: Record<string, number>;
  kb?: { records: number; open: number; decisions: number; old: number; streams: number; at: string } | null };
export type MapHandle = { focus: (id: string) => void; zoom: (f: number) => void; fit: () => void };

/* 12 Nov (redesign): two sets of colours, so the map reads on a light page as well as a dark one. The order is the legend's. */
export const KINDS: Record<string, { label: string; one: string; color: string; light: string; shape: "hub" | "dot" | "ring" | "diamond" | "square" | "frame" }> = {
  stream: { label: "Workstreams", one: "Workstream", color: "#8fe9f7", light: "#0b7285", shape: "hub" },
  table: { label: "Things", one: "Thing", color: "#9ad17b", light: "#3d7a28", shape: "frame" },
  ticket: { label: "Tickets", one: "Ticket", color: "#ff8a5c", light: "#c2531c", shape: "diamond" },
  person: { label: "People", one: "Person", color: "#ffcf5c", light: "#b26f12", shape: "ring" },
  item: { label: "Inbox items", one: "Inbox item", color: "#36e2c3", light: "#0f8f7c", shape: "dot" },
  topic: { label: "Memory topics", one: "Memory topic", color: "#eaf6ff", light: "#17212b", shape: "dot" },
  note: { label: "Memory notes", one: "Memory note", color: "#4fa8ff", light: "#2563c9", shape: "dot" },
  repo: { label: "Repos", one: "Repository", color: "#a78bfa", light: "#6e4bd8", shape: "square" },
  source: { label: "Sources", one: "Source", color: "#ff5c8a", light: "#b8322a", shape: "dot" },
};
/** A workstream's ring: what its current records are. */
export const RING: [keyof BNode, string, string][] = [["facts", "Facts", "#5fe3f5"], ["methods", "Methods", "#a78bfa"], ["decisions", "Decisions", "#ffcf5c"], ["open", "Open points", "#ff8a5c"]];
const isDark = () => typeof document !== "undefined" && (document.documentElement.dataset.theme === "dark" || document.documentElement.dataset.scene === "core");
export const kindColor = (k: string) => { const c = KINDS[k]; return c ? (isDark() ? c.color : c.light) : isDark() ? "#9fe7ff" : "#2563c9"; };
const fmt = (n?: number) => (n || 0).toLocaleString("en-GB");
/** One quiet line about a node (the hover card and the inspector use it). */
export const nodeLine = (p: BNode, deg: number) =>
  p.kind === "stream" ? `${fmt(p.n)} records · ${fmt(p.open)} open points`
    : p.kind === "table" ? `${p.detail || "Thing"} · used by ${deg} workstream${deg === 1 ? "" : "s"}`
    : p.kind === "ticket" ? (p.detail || `Ticket · ${deg} link${deg === 1 ? "" : "s"}`)
    : p.kind === "person" ? `${deg} link${deg === 1 ? "" : "s"} to items and workstreams`
    : p.kind === "item" ? [p.who, p.when, p.done ? "done" : p.state].filter(Boolean).join(" · ")
    : p.kind === "topic" ? `${deg} memory note${deg === 1 ? "" : "s"}`
    : p.kind === "source" ? `${deg} link${deg === 1 ? "" : "s"}`
    : KINDS[p.kind]?.one || p.kind;

type P = BNode & { x: number; y: number; z: number; vx: number; vy: number; deg: number; r: number; c: number; hx?: number; hy?: number };
type Sim = { ps: P[]; links: [P, P][]; alpha: number };
const HUB = (k: string) => k === "stream" || k === "topic";
const springLen = (a: string, b: string) =>
  a === "topic" || b === "topic" ? 32
    : a === "stream" || b === "stream" ? (a === "person" || b === "person" ? 150 : a === "table" || b === "table" ? 118 : 100)
    : a === "source" || b === "source" ? 70 : 36;

/** One tick of the layout: repulsion between nodes (hubs push harder), springs along links, a pull to the middle. */
function physics(S: Sim) {
  const ps = S.ps, n = ps.length, a = S.alpha;
  if (a < 0.004) return;
  for (let i = 0; i < n; i++) {                       // repulsion (n^2 is fine for a few hundred)
    const p = ps[i];
    for (let j = i + 1; j < n; j++) {
      const q = ps[j]; let dx = p.x - q.x, dy = p.y - q.y; let d2 = dx * dx + dy * dy;
      const cc = p.c * q.c;
      if (d2 > (cc > 2 ? 300000 : 160000)) continue;
      if (d2 < 1) { dx = Math.random() - 0.5; dy = Math.random() - 0.5; d2 = 1; }
      const f = (420 * a * cc) / d2; p.vx += dx * f; p.vy += dy * f; q.vx -= dx * f; q.vy -= dy * f;
    }
  }
  for (const [p, q] of S.links) {                     // springs: notes sit close to their topic, tables between the hubs that share them
    const L = springLen(p.kind, q.kind);
    // a table, ticket or person shared by many workstreams pulls each of them only a little, or the hubs would pile up
    const shared = p.kind === "stream" ? q.deg : q.kind === "stream" ? p.deg : 0, w = shared ? 0.22 / Math.sqrt(Math.max(1, shared / 2)) : 1;
    const dx = q.x - p.x, dy = q.y - p.y, d = Math.sqrt(dx * dx + dy * dy) || 1, f = ((d - L) / d) * 0.06 * a * w;
    p.vx += dx * f; p.vy += dy * f; q.vx -= dx * f; q.vy -= dy * f;
  }
  for (const p of ps) {
    // a hub is held near its own place on the plan (workstreams on the inner wheel, memory topics around it); the rest
    // is pulled to the middle and finds its place between the hubs it belongs to
    if (p.hx !== undefined) { p.vx += (p.hx - p.x) * 0.5 * a; p.vy += (p.hy! - p.y) * 0.5 * a; }
    else { const g = p.deg ? 0.02 : 0.07; p.vx -= p.x * g * a; p.vy -= p.y * g * a; }   // a dot linked to nothing stays near the middle, not out at the rim
    p.vx *= 0.82; p.vy *= 0.82; p.x += p.vx; p.y += p.vy;
  }
  S.alpha *= 0.985;
}

type Props = {
  data: Brain | null; show: Set<string>; query: string; holo: boolean; selected: string | null;
  onSelect: (n: BNode | null) => void; reduce: boolean;
  /** the open part of the screen (insets in px) the map fits itself into once it settles */
  fit?: { l: number; t: number; r: number; b: number };
  /** no resting labels (the slim side panel: they would clip at the edges) */
  quiet?: boolean;
  /** leave finished inbox items off the map */
  openOnly?: boolean;
};

const BrainMap = forwardRef<MapHandle, Props>(function BrainMap({ data, show, query, holo, selected, onSelect, reduce, fit, quiet, openOnly }, ref) {
  const cv = useRef<HTMLCanvasElement>(null);
  const st = useRef<{ ps: P[]; by: Map<string, P>; adj: Map<string, Set<string>>; links: [P, P][]; alpha: number; hover: P | null;
    view: { x: number; y: number; k: number }; cam: { x: number; y: number; k: number } | null;
    drag: { x: number; y: number; vx: number; vy: number } | null; moved: boolean; fitted: boolean; touched: boolean; seen: Set<string> } | null>(null);
  const props = useRef({ show, query, holo, selected, onSelect, reduce, fit, quiet, openOnly });
  props.current = { show, query, holo, selected, onSelect, reduce, fit, quiet, openOnly };
  const geom = useRef({ W: 0, H: 0 });

  // where the map's own middle is on screen: the centre of the part no panel covers
  const centre = () => { const { W, H } = geom.current, F = props.current.fit || { l: 0, t: 0, r: 0, b: 0 };
    return { ox: F.l + Math.max(200, W - F.l - F.r) / 2 - W / 2, oy: F.t + Math.max(200, H - F.t - F.b) / 2 - H / 2 }; };
  const fitTarget = () => {
    const S = st.current, { W, H } = geom.current, F = props.current.fit || { l: 40, t: 40, r: 40, b: 40 };
    if (!S) return null;
    let x0 = 1e9, x1 = -1e9, y0 = 1e9, y1 = -1e9, any = false;
    // the frame is the plan's own hubs (what hangs off them gets a margin); a stray dot never shrinks the whole map
    const planned = !props.current.holo && S.ps.some((p) => p.hx !== undefined && props.current.show.has(p.kind));
    for (const p of S.ps) { if (!props.current.show.has(p.kind) || (planned ? p.hx === undefined : p.kind === "note" && !props.current.holo)) continue; any = true; if (p.x < x0) x0 = p.x; if (p.x > x1) x1 = p.x; if (p.y < y0) y0 = p.y; if (p.y > y1) y1 = p.y; }
    if (!any) return null;
    const aw = Math.max(200, W - F.l - F.r), ah = Math.max(200, H - F.t - F.b), pad = planned ? 190 : 90;
    const k = Math.max(0.3, Math.min(1.8, Math.min(aw / (x1 - x0 + pad), ah / (y1 - y0 + pad))));
    return { k, x: F.l + aw / 2 - W / 2 - ((x0 + x1) / 2) * k, y: F.t + ah / 2 - H / 2 - ((y0 + y1) / 2) * k };
  };

  useImperativeHandle(ref, () => ({
    focus: (id: string) => {
      const S = st.current, p = S?.by.get(id); if (!S || !p) return;
      // close enough to read it, far enough to keep what it connects to on screen
      const { W, H } = geom.current, F = props.current.fit || { l: 0, t: 0, r: 0, b: 0 }, { ox, oy } = centre();
      let mx = 60, my = 50;
      (S.adj.get(id) || new Set<string>()).forEach((nid) => { const q = S.by.get(nid); if (!q || !props.current.show.has(q.kind)) return; mx = Math.max(mx, Math.abs(q.x - p.x)); my = Math.max(my, Math.abs(q.y - p.y)); });
      const k = Math.max(0.55, Math.min(1.9, Math.min(Math.max(200, W - F.l - F.r) / (2 * mx + 270), Math.max(200, H - F.t - F.b) / (2 * my + 210))));
      S.cam = { k, x: ox - p.x * k, y: oy - p.y * k }; S.touched = true;
    },
    zoom: (f: number) => {
      const S = st.current; if (!S) return;
      const v = S.cam || S.view, k = Math.max(0.35, Math.min(3.4, v.k * f)), { ox, oy } = centre();
      S.cam = { k, x: ox - ((ox - v.x) / v.k) * k, y: oy - ((oy - v.y) / v.k) * k }; S.touched = true;
    },
    fit: () => { const S = st.current, t = fitTarget(); if (S && t) { S.cam = t; S.touched = false; S.fitted = true; } },
  }), []);

  // build the simulation when the data changes (positions of nodes that stay are kept)
  useEffect(() => {
    if (!data) return;
    const old = st.current?.by || new Map<string, P>();
    const deg = new Map<string, number>();
    data.links.forEach(([a, b]) => { deg.set(a, (deg.get(a) || 0) + 1); deg.set(b, (deg.get(b) || 0) + 1); });
    const ps: P[] = data.nodes.map((n, i) => {
      const o = old.get(n.id), d = deg.get(n.id) || 0, a = i * 2.39996, rr = 120 + Math.sqrt(i) * 22;
      const r = n.kind === "stream" ? 7 + Math.sqrt(n.n || 0) / 9 : n.kind === "topic" || n.kind === "source" ? 5 + Math.sqrt(d) * 0.9
        : n.kind === "table" ? 3.2 + Math.sqrt(d) * 0.8 : n.kind === "person" ? 3.6 + Math.sqrt(d) * 0.7 : 2.2 + Math.sqrt(d) * 1.25;
      const c = n.kind === "stream" ? 2.4 : n.kind === "topic" ? 2.2 : n.kind === "note" ? 1 : 1.35;
      return { ...n, x: o ? o.x : Math.cos(a) * rr, y: o ? o.y : Math.sin(a) * rr, z: o ? o.z : Math.random() * 2 - 1, vx: 0, vy: 0, deg: d, r, c };
    });
    const by = new Map(ps.map((p) => [p.id, p]));
    const adj = new Map<string, Set<string>>();
    const links: [P, P][] = [];
    data.links.forEach(([a, b]) => {
      const pa = by.get(a), pb = by.get(b); if (!pa || !pb) return;
      links.push([pa, pb]);
      (adj.get(a) || adj.set(a, new Set()).get(a)!).add(b);
      (adj.get(b) || adj.set(b, new Set()).get(b)!).add(a);
    });
    // 13 Nov: the plan of the map. Workstreams sit on an inner wheel, ordered so that those sharing the most tables,
    // tickets and people are neighbours; what they share falls between them. Memory topics sit on an outer ring with
    // their notes around them. Forces alone piled twenty hubs into one knot.
    const streams = ps.filter((p) => p.kind === "stream").sort((a, b) => (b.n || 0) - (a.n || 0));
    // the plan is as wide as the open part of the screen is (between the panels), so the fit wastes nothing
    const F0 = props.current.fit || { l: 0, t: 0, r: 0, b: 0 }, g0 = geom.current;
    const asp = Math.max(1.05, Math.min(1.9, g0.W && g0.H ? Math.max(200, g0.W - F0.l - F0.r) / Math.max(200, g0.H - F0.t - F0.b) : 1.4));
    const RX = 600 * Math.sqrt(asp), RY = 600 / Math.sqrt(asp);
    if (streams.length) {
      const shared = (a: P, b: P) => { const A = adj.get(a.id) || new Set<string>(), B = adj.get(b.id) || new Set<string>(); let k = 0; A.forEach((x) => { if (B.has(x)) k++; }); return k; };
      const ring: P[] = [streams[0]], left = streams.slice(1);
      while (left.length) { const last = ring[ring.length - 1]; let bi = 0, bv = -1; left.forEach((x, i) => { const v2 = shared(last, x); if (v2 > bv) { bv = v2; bi = i; } }); ring.push(left.splice(bi, 1)[0]); }
      // two rings, alternating, so twenty names have room
      ring.forEach((p, i) => { const a = -Math.PI / 2 + (i / ring.length) * 6.2832, f = i % 2 === 1 ? 0.43 : 0.74; p.hx = Math.cos(a) * RX * f; p.hy = Math.sin(a) * RY * f; });
    }
    // the outer ring: memory topics, and between them the sources (Slack, Email, Jira, Bitbucket) with what came in through each
    const topics = ps.filter((p) => p.kind === "topic"), sources = ps.filter((p) => p.kind === "source");
    const outer: P[] = [...topics];
    sources.forEach((p, i) => outer.splice(Math.min(outer.length, Math.round(((i + 0.5) * (topics.length + sources.length)) / Math.max(1, sources.length))), 0, p));
    outer.forEach((p, i) => { const a = -Math.PI / 2 + ((i + 0.5) / outer.length) * 6.2832, f = streams.length ? 1 : 0.5; p.hx = Math.cos(a) * RX * f; p.hy = Math.sin(a) * RY * f; });
    for (const p of ps) {                                   // a new node starts where it belongs, not on a spiral
      if (old.has(p.id)) continue;
      if (p.hx !== undefined) { p.x = p.hx; p.y = p.hy!; continue; }
      const hubs = [...(adj.get(p.id) || [])].map((id) => by.get(id)!).filter((q) => q && q.hx !== undefined);
      if (hubs.length) {
        const j = (p.z + 1) * 3.1416, sp = hubs.length === 1 ? (hubs[0].kind === "topic" ? 26 : 70) : 20;
        p.x = hubs.reduce((s2, q) => s2 + q.hx!, 0) / hubs.length * (hubs.length === 1 ? 1 : 0.8) + Math.cos(j) * sp;
        p.y = hubs.reduce((s2, q) => s2 + q.hy!, 0) / hubs.length * (hubs.length === 1 ? 1 : 0.8) + Math.sin(j) * sp;
      }
    }
    const fresh = !st.current, grown = !fresh && ps.some((p) => !old.has(p.id));
    if (fresh) { const sim = { ps, links, alpha: 1 }; for (let i = 0; i < 260; i++) physics(sim); }
    st.current = { ps, by, adj, links, alpha: fresh ? 0.11 : grown ? 0.35 : 0.06, hover: null, view: st.current?.view || { x: 0, y: 0, k: 1 }, cam: st.current?.cam || null,
      drag: null, moved: false, fitted: !!st.current?.fitted, touched: !!st.current?.touched, seen: new Set(ps.map((p) => p.id)) };
  }, [data]);

  useEffect(() => {
    const c = cv.current; if (!c) return;
    const ctx = c.getContext("2d")!;
    let raf = 0, t0 = performance.now(), W = 0, H = 0, dpr = 1;
    const size = () => { dpr = Math.min(2, window.devicePixelRatio || 1); W = c.clientWidth; H = c.clientHeight; c.width = W * dpr; c.height = H * dpr; geom.current = { W, H }; };
    size();
    const ro = new ResizeObserver(size); ro.observe(c);

    const step = (S: NonNullable<typeof st.current>) => {
      physics(S);
      const v = S.view;
      if (S.cam) {                                         // the camera glides to what was asked for
        const m = S.cam, e = props.current.reduce ? 1 : 0.14;
        v.k += (m.k - v.k) * e; v.x += (m.x - v.x) * e; v.y += (m.y - v.y) * e;
        if (Math.abs(m.k - v.k) < 0.002 && Math.abs(m.x - v.x) < 0.5 && Math.abs(m.y - v.y) < 0.5) S.cam = null;
        return;
      }
      // once it has mostly settled, fit it into the open part of the screen (until he zooms or drags himself)
      // (13 Nov: it keeps fitting until the layout has stopped, or the last of the settling pushed dots under the panels)
      if (props.current.fit && !S.touched && (!S.fitted || S.alpha > 0.0045) && S.alpha < 0.12) {
        const t = fitTarget(); if (!t) return;
        const e = S.fitted ? 0.08 : 1;
        v.k += (t.k - v.k) * e; v.x += (t.x - v.x) * e; v.y += (t.y - v.y) * e;
        S.fitted = true;
      }
    };

    const path = (kind: string, x: number, y: number, r: number) => {
      ctx.beginPath();
      const sh = KINDS[kind]?.shape;
      if (sh === "diamond") { const d = r * 1.3; ctx.moveTo(x, y - d); ctx.lineTo(x + d, y); ctx.lineTo(x, y + d); ctx.lineTo(x - d, y); ctx.closePath(); }
      else if (sh === "square" || sh === "frame") { const s = r * 1.75; (ctx as any).roundRect ? (ctx as any).roundRect(x - s / 2, y - s / 2, s, s, s * 0.26) : ctx.rect(x - s / 2, y - s / 2, s, s); }
      else ctx.arc(x, y, Math.max(0, r), 0, 6.2832);
    };

    const draw = (now: number) => {
      const S = st.current;
      if (!S) { raf = requestAnimationFrame(draw); return; }
      const P = props.current, t = (now - t0) / 1000, dark = isDark();
      step(S);
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      ctx.clearRect(0, 0, W, H);
      const v = S.view, cx = W * 0.5 + v.x, cy = H * 0.5 + v.y;
      const ang = P.holo && !P.reduce ? t * 0.06 : 0, ca = Math.cos(ang), sa = Math.sin(ang);
      const breathe = P.reduce ? 0 : 1;
      const q = P.query.trim().toLowerCase();
      const focus = S.hover || (P.selected ? S.by.get(P.selected) || null : null);
      const picked = P.selected ? S.by.get(P.selected) || null : null;
      const near = focus ? S.adj.get(focus.id) || new Set<string>() : null;
      const proj = (p: P) => {
        const bx = p.x + Math.sin(t * 0.35 + p.z * 6) * 1.4 * breathe, by2 = p.y + Math.cos(t * 0.31 + p.z * 5) * 1.4 * breathe;
        if (!P.holo) return { X: cx + bx * v.k, Y: cy + by2 * v.k, s: 1 };
        const zz = p.z * 160, x3 = bx * ca - zz * sa, z3 = bx * sa + zz * ca, s = 700 / Math.max(180, 700 + z3);   // never negative, never huge (12 Nov: a negative radius threw and froze the map)
        return { X: cx + x3 * s * v.k, Y: cy + by2 * s * v.k, s };
      };
      const hit = (p: P) => !!q && (p.label.toLowerCase().includes(q) || (p.who || "").toLowerCase().includes(q));
      // 13 Nov: as a tool, the map keeps its 180 memory notes folded into their topics (the topic says how many) until he
      // zooms in, points at the topic, or searches; drawn all the time they hid the workstreams behind star dust
      const notesOut = P.holo || v.k >= 0.9;
      const vis = (p: P) => P.show.has(p.kind) && !(P.openOnly && p.kind === "item" && p.done)
        && (p.kind !== "note" || notesOut || p === focus || (!!near && near.has(p.id)) || hit(p));
      S.seen.clear(); for (const p of S.ps) if (vis(p)) S.seen.add(p.id);
      const zk = Math.max(0.75, Math.min(1.6, v.k));
      const pos = new Map<string, { X: number; Y: number; s: number }>();
      for (const p of S.ps) pos.set(p.id, proj(p));

      // links: quiet threads at rest; the focused node's own links carry a slow signal
      for (const [a, b] of S.links) {
        if (!vis(a) || !vis(b)) continue;
        const A = pos.get(a.id)!, B = pos.get(b.id)!;
        const hot = focus && (a === focus || b === focus);
        const knowledge = a.kind === "stream" || b.kind === "stream";
        ctx.lineWidth = hot ? 1.1 : knowledge ? 0.8 : 0.6;
        ctx.strokeStyle = dark ? (hot ? "rgba(159,231,255,0.62)" : focus || q ? "rgba(159,231,255,0.03)" : knowledge ? "rgba(143,233,247,0.085)" : "rgba(120,200,255,0.07)")
          : (hot ? "rgba(37,99,201,0.6)" : focus || q ? "rgba(23,33,43,0.03)" : knowledge ? "rgba(11,114,133,0.2)" : "rgba(23,33,43,0.09)");
        if (hot && !P.reduce) { ctx.setLineDash([5, 7]); ctx.lineDashOffset = -t * 22; }
        ctx.beginPath(); ctx.moveTo(A.X, A.Y); ctx.lineTo(B.X, B.Y); ctx.stroke();
        if (hot) ctx.setLineDash([]);
      }

      // nodes
      for (const p of S.ps) {
        if (!vis(p)) continue;
        const A = pos.get(p.id)!, col = kindColor(p.kind), sh = KINDS[p.kind]?.shape;
        const match = hit(p);
        // what is pointed at or picked decides what is lit; a search lights its matches only while nothing is picked
        const dim = (focus ? p !== focus && !near!.has(p.id) : !!q && !match) || p.done;
        const note = p.kind === "note" && p !== focus && !match;
        const rr = (note ? 1.25 : p.r) * A.s * zk * (p === focus ? 1.22 : 1);
        ctx.globalCompositeOperation = dark ? "lighter" : "source-over";
        ctx.globalAlpha = dim ? 0.12 : note ? (near && near.has(p.id) ? 0.9 : 0.38) : 0.95;
        if (!dim && (HUB(p.kind) || p.kind === "source" || p === focus || match)) {
          const g = ctx.createRadialGradient(A.X, A.Y, 0, A.X, A.Y, rr * (p.kind === "stream" ? 3.1 : 4));
          g.addColorStop(0, col + (dark ? "55" : "22")); g.addColorStop(1, col + "00");
          ctx.fillStyle = g; ctx.beginPath(); ctx.arc(A.X, A.Y, Math.max(0, rr * (p.kind === "stream" ? 3.1 : 4)), 0, 6.2832); ctx.fill();
        }
        if (p.kind === "stream") {
          // a workstream: a bright core and a ring cut by what its records are (facts, methods, decisions, open points)
          ctx.fillStyle = col; ctx.beginPath(); ctx.arc(A.X, A.Y, Math.max(0, rr * 0.46), 0, 6.2832); ctx.fill();
          const tot = RING.reduce((s2, [k]) => s2 + (Number(p[k]) || 0), 0) || 1, gap = 0.09;
          let a0 = -Math.PI / 2;
          ctx.lineWidth = Math.max(1.8, rr * 0.2); ctx.lineCap = "butt";
          for (const [k, , rc] of RING) {
            const frac = (Number(p[k]) || 0) / tot; if (frac <= 0) continue;
            const a1 = a0 + frac * 6.2832;
            if (a1 - a0 > gap) { ctx.strokeStyle = rc; ctx.beginPath(); ctx.arc(A.X, A.Y, Math.max(0, rr), a0 + gap / 2, a1 - gap / 2); ctx.stroke(); }
            a0 = a1;
          }
        } else if (sh === "ring") {
          ctx.lineWidth = Math.max(1.3, rr * 0.34); ctx.strokeStyle = col; ctx.beginPath(); ctx.arc(A.X, A.Y, Math.max(0, rr), 0, 6.2832); ctx.stroke();
          ctx.fillStyle = col; ctx.beginPath(); ctx.arc(A.X, A.Y, Math.max(0, rr * 0.34), 0, 6.2832); ctx.fill();
        } else if (sh === "frame") {
          path(p.kind, A.X, A.Y, rr); ctx.globalAlpha *= 0.22; ctx.fillStyle = col; ctx.fill();
          ctx.globalAlpha = dim ? 0.12 : 0.95; ctx.lineWidth = 1.3; ctx.strokeStyle = col; path(p.kind, A.X, A.Y, rr); ctx.stroke();
        } else { ctx.fillStyle = col; path(p.kind, A.X, A.Y, rr); ctx.fill(); }
        // an inbox item still open: a slow pulse, so what is live stands out from what is history
        if (p.kind === "item" && !p.done && !dim && !P.holo && !P.reduce) {
          const ph = (t * 0.55 + (p.z + 1) * 0.5) % 1;
          ctx.globalAlpha = (1 - ph) * 0.5; ctx.lineWidth = 1; ctx.strokeStyle = col;
          ctx.beginPath(); ctx.arc(A.X, A.Y, rr + 2 + ph * 9, 0, 6.2832); ctx.stroke();
        }
      }
      ctx.globalCompositeOperation = "source-over"; ctx.globalAlpha = 1;
      // the picked node keeps a marker while the pointer is elsewhere
      if (picked && vis(picked)) {
        const A = pos.get(picked.id)!, rr = picked.r * A.s * zk * 1.22 + 6 + (P.reduce ? 0 : Math.sin(t * 2.4) * 1.2);
        ctx.strokeStyle = dark ? "rgba(255,255,255,0.75)" : "rgba(23,33,43,0.7)"; ctx.lineWidth = 1.2;
        ctx.beginPath(); ctx.arc(A.X, A.Y, rr, 0, 6.2832); ctx.stroke();
      }

      // labels: at rest the hubs (workstreams, memory topics) and, as there is room, people, sources, repos and tables;
      // everything near what is hovered, picked or searched. Quiet sans with a halo; a label never overlaps one already drawn.
      ctx.textAlign = "center"; ctx.lineJoin = "round";
      const boxes: [number, number, number, number][] = [];
      const rank = (p: P) => (p === focus ? 9 : p.kind === "stream" ? 6 : p.kind === "topic" ? 5 : p.kind === "person" ? 4 : p.kind === "table" || p.kind === "repo" || p.kind === "source" ? 3 : 1);
      const order = [...S.ps].sort((a, b) => rank(b) - rank(a) || b.deg - a.deg);
      const rest = !focus && !q && !P.quiet;
      for (const p of order) {
        if (!vis(p)) continue;
        const A = pos.get(p.id)!;
        const match = hit(p);
        const show = p === focus || match || (near && near.has(p.id) && p.kind !== "note")
          || (rest && (HUB(p.kind) || p.kind === "source" || (p.kind === "person" && v.k > 0.72) || (p.kind === "repo" && v.k > 0.85) || (p.kind === "table" && v.k > 0.95)))
          || (v.k > 1.75 && p.kind !== "note") || v.k > 2.7;
        if (!show) continue;
        const big = p.kind === "stream";
        ctx.font = `${big ? 600 : 500} ${big ? 12 : 11}px -apple-system, BlinkMacSystemFont, system-ui, sans-serif`;
        const max = big ? 36 : 30, base = p.label.length > max ? p.label.slice(0, max - 1) + "…" : p.label;
        const txt = p.kind === "topic" && !notesOut && p !== focus ? `${base} · ${p.deg}` : base;
        const w = ctx.measureText(txt).width, x = A.X, rad = p.r * A.s * zk;
        let y = A.Y + rad + (big ? 17 : 14);
        let b: [number, number, number, number] = [x - w / 2 - 4, y - 11, x + w / 2 + 4, y + 4];
        const clash = (bb: number[]) => boxes.some((o) => bb[0] < o[2] && bb[2] > o[0] && bb[1] < o[3] && bb[3] > o[1]);
        if (p !== focus && clash(b)) {                      // no room under the dot: try above it before giving up
          y = A.Y - rad - (big ? 9 : 7); b = [x - w / 2 - 4, y - 11, x + w / 2 + 4, y + 4];
          if (clash(b)) continue;
        }
        boxes.push(b);
        ctx.strokeStyle = dark ? "rgba(6,12,17,0.92)" : "rgba(255,255,255,0.94)"; ctx.lineWidth = 3.5; ctx.strokeText(txt, x, y);
        ctx.fillStyle = dark ? (p === focus || big ? "rgba(255,255,255,0.96)" : p.kind === "topic" ? "rgba(214,236,250,0.66)" : "rgba(214,236,250,0.82)")
          : (p === focus || big ? "rgba(23,33,43,1)" : p.kind === "topic" ? "rgba(52,66,79,0.82)" : "rgba(52,66,79,0.92)");
        ctx.fillText(txt, x, y);
      }

      // the hovered dot says what it is
      const h = S.hover;
      if (h && vis(h) && !S.drag && !P.holo) {
        const A = pos.get(h.id)!, line = nodeLine(h, h.deg), kind = (KINDS[h.kind]?.one || h.kind).toUpperCase();
        ctx.font = "600 9.5px ui-monospace, SFMono-Regular, Menlo, monospace"; const w0 = ctx.measureText(kind).width + 14;
        ctx.font = "500 11.5px -apple-system, BlinkMacSystemFont, system-ui, sans-serif";
        const sub = line.length > 58 ? line.slice(0, 57) + "…" : line, w1 = ctx.measureText(sub).width;
        const bw = Math.max(w0, w1) + 22, bh = 42, rr = h.r * A.s * zk * 1.22;
        let bx = A.X - bw / 2, by2 = A.Y - rr - bh - 12;
        bx = Math.max(8, Math.min(W - bw - 8, bx)); if (by2 < 8) by2 = A.Y + rr + 26;
        ctx.fillStyle = dark ? "rgba(6,16,23,0.94)" : "rgba(255,255,255,0.97)"; ctx.strokeStyle = dark ? "rgba(159,231,255,0.28)" : "rgba(23,33,43,0.16)"; ctx.lineWidth = 1;
        ctx.beginPath(); (ctx as any).roundRect ? (ctx as any).roundRect(bx, by2, bw, bh, 9) : ctx.rect(bx, by2, bw, bh); ctx.fill(); ctx.stroke();
        ctx.textAlign = "left";
        ctx.fillStyle = kindColor(h.kind); ctx.beginPath(); ctx.arc(bx + 12, by2 + 13, 3, 0, 6.2832); ctx.fill();
        ctx.font = "600 9.5px ui-monospace, SFMono-Regular, Menlo, monospace";
        ctx.fillStyle = dark ? "rgba(159,231,255,0.7)" : "rgba(52,66,79,0.75)"; ctx.fillText(kind, bx + 20, by2 + 16.5);
        ctx.font = "500 11.5px -apple-system, BlinkMacSystemFont, system-ui, sans-serif";
        ctx.fillStyle = dark ? "rgba(234,246,255,0.95)" : "rgba(23,33,43,0.95)"; ctx.fillText(sub, bx + 11, by2 + 32);
      }
      raf = requestAnimationFrame(draw);
    };
    raf = requestAnimationFrame(draw);

    // hit testing, hover, click, pan, zoom
    const at = (ev: PointerEvent | MouseEvent) => {
      const S = st.current; if (!S) return null;
      const r = c.getBoundingClientRect(), mx = ev.clientX - r.left, my = ev.clientY - r.top, v = S.view, P = props.current;
      let best: P | null = null, bs = 1e9;
      for (const p of S.ps) {
        if (!S.seen.has(p.id)) continue;
        const X = W * 0.5 + v.x + p.x * v.k, Y = H * 0.5 + v.y + p.y * v.k, d = Math.sqrt((X - mx) ** 2 + (Y - my) ** 2);
        const reach = Math.max(13, p.r * Math.max(0.75, Math.min(1.6, v.k)) + 7);
        if (d < reach && d - p.r < bs) { bs = d - p.r; best = p; }   // inside two dots: the smaller, nearer one wins
      }
      return best;
    };
    const down = (e: PointerEvent) => { const S = st.current; if (!S) return; S.drag = { x: e.clientX, y: e.clientY, vx: S.view.x, vy: S.view.y }; S.moved = false; };
    const move = (e: PointerEvent) => {
      const S = st.current; if (!S) return;
      if (S.drag) {
        const dx = e.clientX - S.drag.x, dy = e.clientY - S.drag.y;
        if (Math.abs(dx) + Math.abs(dy) > 3) { S.moved = true; S.cam = null; S.touched = true; S.view.x = S.drag.vx + dx; S.view.y = S.drag.vy + dy; c.style.cursor = "grabbing"; }
        return;
      }
      const h = props.current.holo || e.target !== c ? null : at(e);
      if (h !== S.hover) { S.hover = h; c.style.cursor = h ? "pointer" : "grab"; }
    };
    const up = (e: PointerEvent) => {
      const S = st.current; if (!S) return;
      const was = S.drag; S.drag = null; c.style.cursor = S.hover ? "pointer" : "grab";
      if (was && !S.moved && !props.current.holo && e.target === c) { const h = at(e); props.current.onSelect(h); }
    };
    const leave = () => { const S = st.current; if (S) { S.hover = null; } };
    const zoomAt = (e: MouseEvent, k: number) => {
      const S = st.current; if (!S) return;
      const r = c.getBoundingClientRect(), ox = e.clientX - r.left - W / 2, oy = e.clientY - r.top - H / 2, v = S.view;
      const wx = (ox - v.x) / v.k, wy = (oy - v.y) / v.k;
      return { k, x: ox - wx * k, y: oy - wy * k };
    };
    const wheel = (e: WheelEvent) => {
      const S = st.current; if (!S) return;
      e.preventDefault();
      // zoom where the pointer is (a pinch arrives as a wheel with ctrl held and small steps)
      const k = Math.max(0.35, Math.min(3.4, S.view.k * Math.exp(-e.deltaY * (e.ctrlKey ? 0.012 : 0.0015))));
      const t = zoomAt(e, k); if (!t) return;
      S.view.k = t.k; S.view.x = t.x; S.view.y = t.y; S.cam = null; S.touched = true;
    };
    const dbl = (e: MouseEvent) => {
      const S = st.current; if (!S || props.current.holo) return;
      const t = zoomAt(e, Math.min(3.4, S.view.k * 1.6)); if (t) { S.cam = t; S.touched = true; }
    };
    c.addEventListener("pointerdown", down); window.addEventListener("pointermove", move); window.addEventListener("pointerup", up);
    c.addEventListener("pointerleave", leave); c.addEventListener("wheel", wheel, { passive: false }); c.addEventListener("dblclick", dbl);
    return () => {
      cancelAnimationFrame(raf); ro.disconnect();
      c.removeEventListener("pointerdown", down); window.removeEventListener("pointermove", move); window.removeEventListener("pointerup", up);
      c.removeEventListener("pointerleave", leave); c.removeEventListener("wheel", wheel); c.removeEventListener("dblclick", dbl);
    };
  }, []);

  return <canvas ref={cv} className="absolute inset-0 h-full w-full cursor-grab touch-none" aria-label="Brain map: workstreams, things, tickets, people, items, repos and notes" role="img" />;
});

export default BrainMap;
