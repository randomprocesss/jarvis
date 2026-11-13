"use client";
/* Minimal markdown for Jarvis's answers: paragraphs, bullet and numbered lists, short labels, **bold**, `inline code`,
   ``` code blocks ```, [links](https://…), bare links, and paths starting with ~/ or / that show the file in Finder when
   clicked. Pipe tables stay monospace. */
import { Fragment, type ReactNode } from "react";
import { reveal } from "@/lib/jarvis";

const PATH = /^(?:~\/|\/)[\w.@%+~=,\-/]*[\w/]$/;
const isPath = (s: string) => PATH.test(s) && s.length > 2 && !s.startsWith("//");
const pathBtn = (p: string, k: number, code = false) => (
  <button key={k} type="button" onClick={() => reveal(p)} title="Show in Finder"
    className={code
      ? "cursor-pointer rounded-[5px] border-0 bg-ion/[.07] px-1 py-px font-mono text-[12.5px] text-arc-hi [overflow-wrap:anywhere] hover:underline"
      : "cursor-pointer border-0 bg-transparent p-0 font-mono text-[12.5px] text-arc-hi [overflow-wrap:anywhere] hover:underline"}>{p}</button>
);

// one pass over a line: `code` | **bold** | [text](url) | bare url | path
const TOK = /(`[^`\n]+`)|(\*\*[^*\n]+\*\*)|(\[[^\]\n]+\]\((?:https?:|mailto:)[^)\s]+\))|(https?:\/\/[^\s<>()]+[^\s<>().,;:!?'"])|((?:^|(?<=[\s(]))(?:~\/|\/)[\w.@%+~=,\-/]*[\w/])/g;

function inline(text: string): ReactNode[] {
  const out: ReactNode[] = [];
  let last = 0, k = 0, m: RegExpExecArray | null;
  TOK.lastIndex = 0;
  while ((m = TOK.exec(text))) {
    if (m.index > last) out.push(text.slice(last, m.index));
    const [all, code, bold, link, url, path] = m;
    if (code) { const c = code.slice(1, -1); out.push(isPath(c) ? pathBtn(c, k++, true) : <code key={k++} className="rounded-[5px] bg-ion/[.07] px-1 py-px font-mono text-[12.5px] text-glass-ink [overflow-wrap:anywhere]">{c}</code>); }
    else if (bold) out.push(<b key={k++} className="font-semibold text-glass-ink">{bold.slice(2, -2)}</b>);
    else if (link) { const i = link.indexOf("]("); out.push(<a key={k++} href={link.slice(i + 2, -1)} target="_blank" rel="noopener" className="text-arc-hi underline decoration-ion/[.35] underline-offset-2 hover:decoration-arc-hi">{link.slice(1, i)}</a>); }
    else if (url) out.push(<a key={k++} href={url} target="_blank" rel="noopener" className="text-arc-hi underline decoration-ion/[.35] underline-offset-2 [overflow-wrap:anywhere]">{url}</a>);
    else if (path && isPath(path)) out.push(pathBtn(path, k++));
    else out.push(all);
    last = m.index + all.length;
    if (!all) TOK.lastIndex++;
  }
  if (last < text.length) out.push(text.slice(last));
  return out;
}

/* Lines, grouped: "- " / "• " / "* " bullets, "1." / "1)" / "(1)" numbered items, a short "**Label:**" or "Label:" line as a
   small label, pipe tables, and paragraphs (5 Oct: lists showed as plain dashed lines inside one paragraph). */
const BUL = /^\s*(?:[-•*]|\u2013)\s+(.*)$/, NUM = /^\s*(?:\(?\d{1,2}[.)])\s+(.*)$/;
const LABEL = /^\s*(?:\*\*([^*\n]{2,40}?):?\*\*:?|([A-Z][\w ,'/&()-]{1,38}):)\s*$/;
type Block = { kind: "p" | "ul" | "ol" | "label" | "table"; lines: string[] };
function blocks(src: string): Block[] {
  const out: Block[] = [];
  const push = (kind: Block["kind"], line: string) => {
    const last = out[out.length - 1];
    if (last && last.kind === kind && kind !== "label") last.lines.push(line); else out.push({ kind, lines: [line] });
  };
  for (const raw of String(src || "").split("\n")) {
    const ln = raw.replace(/\s+$/, "");
    if (!ln.trim()) { out.push({ kind: "p", lines: [] }); continue; }          // a blank line ends the paragraph or list
    let m: RegExpMatchArray | null;
    if (/^\s*\|/.test(ln)) push("table", ln.trim());
    else if ((m = ln.match(BUL))) push("ul", m[1]);
    else if ((m = ln.match(NUM))) push("ol", m[1]);
    else if ((m = ln.match(LABEL))) push("label", (m[1] || m[2]).trim());
    else push("p", ln.trim());
  }
  return out.filter((b) => b.lines.length);
}
function List({ kind, items }: { kind: "ul" | "ol"; items: string[] }) {
  const Tag = kind === "ol" ? "ol" : "ul";
  return (
    <Tag className={(kind === "ol" ? "list-decimal" : "list-disc") + " m-0 grid gap-1 pl-[18px] marker:text-ion/[.55]"}>
      {items.map((x, n) => <li key={n} className="pl-0.5">{inline(x)}</li>)}
    </Tag>
  );
}

export function Md({ text }: { text: string }) {
  const parts = String(text || "").split(/```[\w-]*\n?/);
  return (
    <div className="grid gap-2.5">
      {parts.map((p, i) => i % 2 ? (
        <pre key={i} className="m-0 overflow-x-auto rounded-[10px] bg-line-2 px-3 py-2.5 font-mono text-[12.5px] leading-relaxed text-glass-ink ring-1 ring-inset ring-ion/[.07]">{p.replace(/\n$/, "")}</pre>
      ) : blocks(p).map((b, j) => b.kind === "table" ? (
        <pre key={i + "." + j} className="m-0 overflow-x-auto font-mono text-[12px] leading-relaxed text-glass-ink-2">{b.lines.join("\n")}</pre>
      ) : b.kind === "label" ? (
        <div key={i + "." + j} className="-mb-1 font-mono text-[11px] font-medium uppercase tracking-[.08em] text-glass-ink-2">{b.lines[0]}</div>
      ) : b.kind === "ul" || b.kind === "ol" ? (
        <List key={i + "." + j} kind={b.kind} items={b.lines} />
      ) : (
        <p key={i + "." + j} className="m-0 whitespace-pre-line">{b.lines.map((ln, n) => <Fragment key={n}>{n ? "\n" : ""}{inline(ln)}</Fragment>)}</p>
      )))}
    </div>
  );
}
