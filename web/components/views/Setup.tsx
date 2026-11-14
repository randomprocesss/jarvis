"use client";
/* Setup: where a new owner starts, and where anyone later sees what is connected and what is not. Every step shows what
   is in place and has the action beside it; nothing is edited by hand in a file. The checks and the suggestions come
   from one place on the server (jarvis/setup.py). Nothing here acts by itself: a check runs on a press, a setting
   changes on Save, and a suggestion only offers. */
import { useEffect, useRef, useState } from "react";
import { WH } from "@/lib/wh";
import { Button } from "@/components/ui/button";
import { Panel, Chip, PageTitle, Loading, Note, when } from "@/components/ui/kit";

const field = "w-full rounded-md border border-line bg-transparent px-3 py-1.5 text-[14px] text-ink outline-none focus:border-ion";
const label = "mb-1 block text-[12.5px] font-medium text-mute";

function Field({ name, value, onChange, hint, placeholder, wide }: { name: string; value: string; onChange: (v: string) => void; hint?: string; placeholder?: string; wide?: boolean }) {
  return (
    <label className={wide ? "col-span-full block" : "block"}>
      <span className={label}>{name}</span>
      <input className={field} value={value} placeholder={placeholder} onChange={(e) => onChange(e.target.value)} />
      {hint && <span className="mt-1 block text-[12px] text-mute">{hint}</span>}
    </label>
  );
}

function Step({ s, n, children, open }: { s: any; n: number; children: React.ReactNode; open?: boolean }) {
  const info = s.id === "budget" || s.id === "optional" || s.id === "trust";
  return (
    <section id={"setup-" + s.id} data-setup-step={s.id} data-done={s.done ? "1" : "0"}>
      <Panel title={<span><span className="mr-2 font-mono text-[12.5px] text-mute">{n}</span>{s.title}</span>}
        right={info ? <Chip tone="mute">as you like</Chip> : s.done ? <Chip tone="ok">in place</Chip> : <Chip tone={open ? "ion" : "warn"}>{open ? "next" : "to do"}</Chip>}>
        <p className="m-0 mb-3 text-[14px] text-ink-2" data-setup-status>{s.status}</p>
        {children}
      </Panel>
    </section>
  );
}

export default function SetupView() {
  const [st, setSt] = useState<any>(null), [err, setErr] = useState(""), [busy, setBusy] = useState("");
  const [edit, setEdit] = useState<Record<string, any>>({}), [folder, setFolder] = useState("");
  const alive = useRef(true);
  const load = () => WH.api("/api/setup").then((d: any) => { if (!alive.current) return; if (d && d.steps) { setSt(d); setErr(""); } else setErr((d && d.error) || "Jarvis is not answering"); }).catch(() => alive.current && setErr("Jarvis is not answering"));
  const running = !!(st && st.steps.find((x: any) => x.id === "learn")?.learning?.running);
  useEffect(() => { alive.current = true; load(); return () => { alive.current = false; }; }, []);
  useEffect(() => { const t = setInterval(() => { if (!document.hidden && !busy) load(); }, running ? 4000 : 15000); return () => clearInterval(t); }, [running, busy]);

  const step = (id: string) => st.steps.find((x: any) => x.id === id);
  const val = (k: string, from: any) => (k in edit ? edit[k] : from ?? "");
  const set = (k: string) => (v: any) => setEdit((e) => ({ ...e, [k]: v }));
  const changed = (keys: string[]) => keys.filter((k) => k in edit);
  const save = async (values: Record<string, any>, done = "Saved.") => {
    if (!Object.keys(values).length) return true;
    setBusy("save");
    const r = await WH.api("/api/setup_save", { set: values }).catch(() => ({ error: "Jarvis is not answering" }));
    setBusy("");
    if (r && r.ok) {
      setSt(r.state); setEdit((e) => { const n = { ...e }; Object.keys(values).forEach((k) => delete n[k]); return n; });
      WH.toast(done, "ok"); WH.loadMe(); return true;
    }
    WH.toast((r && r.error) || "Could not save.", "err", 7000); return false;
  };
  const saveKeys = (keys: string[], done?: string) => save(Object.fromEntries(changed(keys).map((k) => [k, edit[k]])), done);
  const check = async () => {
    setBusy("check");
    const r = await WH.api("/api/setup_check", { what: "claude" }).catch(() => ({ error: "Jarvis is not answering" }));
    setBusy("");
    if (r && r.state) setSt(r.state);
    WH.loadMe();
    if (r && r.result && r.result.ok) WH.toast(`Claude answered in ${r.result.secs} s.` + (r.result.expected_set?.length ? " Found connected: " + r.result.expected_set.join(", ").replace(/claude\.ai /g, "") + "." : ""), "ok", 6000);
    else WH.toast((r && r.result && r.result.error) || (r && r.error) || "The check did not finish.", "err", 8000);
  };
  const learn = async () => {
    setBusy("learn");
    const r = await WH.api("/api/setup_learn", {}).catch(() => ({ error: "Jarvis is not answering" }));
    setBusy("");
    if (r && r.ok) WH.toast("Learning has started. You can leave this page; it carries on.", "ok", 6000); else WH.toast((r && r.error) || "Could not start.", "err", 8000);
    load();
  };
  const dismiss = async (id: string) => { await WH.api("/api/setup_dismiss", { id }); load(); };
  const demo = async () => {
    setBusy("demo");
    const r = await WH.api("/api/demo_start", {}).catch(() => ({ error: "Jarvis is not answering" }));
    setBusy("");
    if (r && r.ok && r.url) window.location.href = r.url; else WH.toast((r && r.error) || "The demo did not start.", "err", 8000);
  };
  const act = (s: any) => { if (s.save) save(s.save, "Done."); else if (s.run === "learn") learn(); else if (s.run === "check") check(); else if (s.href) window.open(s.href, "_blank", "noopener"); };

  if (err && !st) return <><PageTitle title="Setup" /><Note tone="warn">{err}. Check that Jarvis is running, then reload this page.</Note></>;
  if (!st) return <><PageTitle title="Setup" /><Panel><Loading rows={5} /></Panel></>;

  const you = step("you"), cl = step("claude"), src = step("sources"), fo = step("folders"), bu = step("budget"), le = step("learn"), op = step("optional"), tr = step("trust");
  const c = cl.claude || {}, lr = le.learning || {};
  const folders: string[] = fo.folders.rows.map((r: any) => r.path);
  const mode = val("engine.mode", bu.values["engine.mode"]);
  const youKeys = ["owner.name", "owner.address_as", "work.about", "work.days", "work.hours", "work.quiet"];
  const firstTime = !st.set_up;

  return (
    <>
      <PageTitle title={firstTime ? "Welcome. Let's set Jarvis up." : "Setup"}
        sub={firstTime ? "A few steps, all on this page. Everything stays on this computer, in " + st.home + ". Nothing is sent, posted or changed anywhere without your press."
          : st.left ? `${st.left} step${st.left === 1 ? "" : "s"} still to do. Everything Jarvis knows about you is kept in ${st.home}.` : `Everything is in place. Your data is kept in ${st.home}.`}
        right={<div className="flex items-center gap-2">{!firstTime && <Button size="sm" variant="ghost" busy={busy === "demo"} disabled={!!busy} onClick={demo}>Look around the demo</Button>}{st.version ? <Chip tone="mute">version {st.version}</Chip> : null}</div>} />

      {firstTime && (
        <Note tone="ion">
          <div className="flex flex-wrap items-center gap-3">
            <span className="min-w-0 flex-1">Want to see it working before you set anything up? Look around a made-up person's week: an inbox, drafts waiting, a knowledge base. Nothing in it is real and nothing is sent.</span>
            <Button busy={busy === "demo"} disabled={!!busy} onClick={demo}>Look around first</Button>
          </div>
        </Note>
      )}

      {st.suggestions.length > 0 && st.set_up && (
        <Panel title="Suggested" n={st.suggestions.length}>
          <div className="grid gap-2.5" data-setup-suggestions>
            {st.suggestions.map((s: any) => (
              <div key={s.id} className="flex flex-wrap items-center gap-3 rounded-[10px] border border-line px-3.5 py-2.5" data-suggestion={s.id}>
                <div className="min-w-0 flex-1">
                  <b className="block text-[14px] font-semibold text-ink">{s.title}</b>
                  <span className="text-[13px] text-ink-2">{s.why}{s.note ? " " + s.note : ""}</span>
                </div>
                {(s.save || s.run || s.href) && <Button size="sm" variant="primary" disabled={!!busy} onClick={() => act(s)}>{s.label}</Button>}
                <Button size="sm" variant="ghost" onClick={() => dismiss(s.id)}>Not now</Button>
              </div>
            ))}
          </div>
        </Panel>
      )}

      <Step s={you} n={1} open={st.next === "you"}>
        <div className="grid grid-cols-2 gap-x-4 gap-y-3 max-[640px]:grid-cols-1">
          <Field name="Your name" value={val("owner.name", you.values["owner.name"])} onChange={set("owner.name")} placeholder="Sam Lee" />
          <Field name="What Jarvis calls you" value={val("owner.address_as", you.values["owner.address_as"])} onChange={set("owner.address_as")} placeholder="your first name, if left empty" />
          <Field wide name="What you do, in one line" value={val("work.about", you.values["work.about"])} onChange={set("work.about")} placeholder="product design for a delivery service" hint="Jarvis uses this to tell your work from everything else. You can change it any time." />
          <Field name="Working days" value={val("work.days", you.values["work.days"])} onChange={set("work.days")} hint="like mon-fri" />
          <Field name="Working hours" value={val("work.hours", you.values["work.hours"])} onChange={set("work.hours")} hint="like 09:00-19:00" />
          <Field name="Quiet hours" value={val("work.quiet", you.values["work.quiet"])} onChange={set("work.quiet")} hint="no pings in these hours, like 22:30-07:00" />
        </div>
        <div className="mt-4 flex items-center gap-3">
          <Button variant="primary" busy={busy === "save"} disabled={!!busy || !changed(youKeys).length || !String(val("owner.name", you.values["owner.name"])).trim()} onClick={() => saveKeys(youKeys, firstTime ? "Saved. Next: Claude." : "Saved.")}>Save</Button>
          {firstTime && <span className="text-[13px] text-mute">Only your name is needed to begin.</span>}
        </div>
      </Step>

      <Step s={cl} n={2} open={st.next === "claude"}>
        <div className="flex flex-wrap items-center gap-2">
          <Chip tone={c.found ? "ok" : "bad"}>{c.found ? "Claude Code " + (c.version || "found") : "Claude Code not found"}</Chip>
          {c.found && !c.key && <Chip tone={c.signed_in ? "ok" : c.unknown ? "mute" : "bad"}>{c.signed_in ? "signed in" + (c.plan ? " · " + c.plan + " plan" : "") : c.unknown ? "sign-in not known" : "not signed in"}</Chip>}
          {c.found && c.key && <Chip tone={c.signed_in ? "ok" : "bad"}>{c.signed_in ? "API key kept" : "no API key yet"}</Chip>}
          {c.test && <Chip tone={c.test.ok ? "ok" : "bad"}>{c.test.ok ? "answered " + when(c.test.at) : "last check failed"}</Chip>}
        </div>
        {c.test && !c.test.ok && c.test.error && <div className="mt-3"><Note tone="warn">{c.test.error}</Note></div>}
        {c.key && <ApiKey has={!!c.signed_in} where={c.key_where} canStore={!!c.key_store} onDone={(s: any) => s && setSt(s)} />}
        <div className="mt-4 flex flex-wrap items-center gap-3">
          <Button variant={cl.done ? "default" : "primary"} busy={busy === "check"} disabled={!!busy || !c.found} onClick={check}>{cl.done ? "Check again" : "Check Claude and my apps"}</Button>
          <span className="text-[13px] text-mute">One short Claude run: it shows that Claude answers and which of your apps are connected. It can take up to a minute.</span>
        </div>
      </Step>

      <Step s={src} n={3} open={st.next === "sources"}>
        <div className="grid gap-2" data-setup-sources>
          <div className="flex flex-wrap items-center gap-3 rounded-[10px] border border-line px-3.5 py-2.5">
            <div className="min-w-0 flex-1"><b className="block text-[14px] font-semibold text-ink">Your Claude Code chats</b>
              <span className="text-[13px] text-ink-2">{src.chats.found} chats in {src.chats.projects} projects on this computer; {src.chats.in_scope} of them were held in your work folders.</span></div>
            <Chip tone={src.chats.on ? "ok" : "mute"}>{src.chats.on ? "read" : "not read"}</Chip>
            <Button size="sm" variant="ghost" disabled={!!busy} onClick={() => save({ "sources.chats": !src.chats.on }, src.chats.on ? "Your chats are no longer read." : "Your chats are read again.")}>{src.chats.on ? "Do not read" : "Read them"}</Button>
          </div>
          {src.apps.rows.map((r: any) => (
            <div key={r.id} className="flex flex-wrap items-center gap-3 rounded-[10px] border border-line px-3.5 py-2.5" data-app={r.id}>
              <div className="min-w-0 flex-1"><b className="block text-[14px] font-semibold text-ink">{r.label}</b>
                <span className="text-[13px] text-ink-2">{r.what}{r.fix ? ". " + r.fix : ""}</span></div>
              <Chip tone={r.state === "connected" ? "ok" : r.state === "unknown" || r.state === "off" ? "mute" : "warn"}>{r.state}</Chip>
            </div>
          ))}
        </div>
        {src.apps.other.length > 0 && <p className="mb-0 mt-3 text-[13px] text-mute">Also connected to your Claude account, and not read by Jarvis: {src.apps.other.join(", ")}.</p>}
        {src.apps.on && !src.apps.api_key && <Reads r={src.reads} busy={busy} save={save} setSt={setSt} />}
        <div className="mt-4 flex flex-wrap items-center gap-3">
          <Button size="sm" disabled={!!busy} onClick={() => save({ "sources.claude_connectors": !src.apps.on }, src.apps.on ? "Your connected apps are no longer read." : "Your connected apps are read.")}>{src.apps.on ? "Stop reading my connected apps" : "Read my connected apps"}</Button>
          <span className="text-[13px] text-mute">Apps are connected in Claude itself, never here. Jarvis only reads through them; anything it would send waits for your press.{src.apps.checked ? " Last checked " + when(src.apps.checked) + "." : ""}</span>
        </div>
      </Step>

      <Step s={fo} n={4} open={st.next === "folders"}>
        <div className="grid gap-2" data-setup-folders>
          {fo.folders.rows.map((r: any) => (
            <div key={r.path} className="flex flex-wrap items-center gap-3 rounded-[10px] border border-line px-3.5 py-2">
              <span className="min-w-0 flex-1 font-mono text-[13px] text-ink">{r.path}</span>
              {!r.ok && <Chip tone="warn">{r.why || "cannot be read"}</Chip>}
              <Button size="sm" variant="ghost" disabled={!!busy} onClick={() => save({ "work.folders": folders.filter((f) => f !== r.path) }, "Removed.")}>Remove</Button>
            </div>
          ))}
        </div>
        <form className="mt-3 flex flex-wrap items-center gap-3" onSubmit={(e) => { e.preventDefault(); const f = folder.trim(); if (f && !folders.includes(f)) save({ "work.folders": [...folders, f] }, "Added.").then((ok) => ok && setFolder("")); }}>
          <input className={field + " max-w-[420px] font-mono text-[13px]"} value={folder} onChange={(e) => setFolder(e.target.value)} placeholder="~/Work/Atlas" />
          <Button type="submit" disabled={!!busy || !folder.trim()}>Add folder</Button>
        </form>
        <p className="mb-0 mt-2 text-[13px] text-mute">Jarvis learns from the Claude Code chats held in these folders and puts its own work files in the first one ({fo.folders.workdir}). Private folders stay out simply by not being listed.</p>
      </Step>

      <Step s={bu} n={5}>
        <div className="grid grid-cols-2 gap-x-4 gap-y-3 max-[640px]:grid-cols-1">
          <label className="block"><span className={label}>How Claude is paid for</span>
            <select className={field} value={mode} onChange={(e) => set("engine.mode")(e.target.value)}>
              <option value="claude-plan">A Claude plan (Pro or Max)</option>
              <option value="api-key">An API key (paid per use)</option>
            </select></label>
          {mode === "claude-plan"
            ? <Field name="Share of the plan's window for background work (%)" value={String(val("bg_pct", Math.round(100 * Number(bu.values["engine.background_share"] || 0))))} onChange={set("bg_pct")} hint="Work you ask for is never held back by this." />
            : <Field name="Background work stops at (US dollars a day)" value={String(val("cap_usd", bu.values["engine.daily_cap_usd"]))} onChange={set("cap_usd")} hint="Work you ask for is never held back by this." />}
        </div>
        <div className="mt-4"><Button busy={busy === "save"} disabled={!!busy || !changed(["engine.mode", "bg_pct", "cap_usd"]).length} onClick={async () => {
          const v: Record<string, any> = {};
          if ("engine.mode" in edit) v["engine.mode"] = edit["engine.mode"];
          if ("bg_pct" in edit) { const p = Number(edit["bg_pct"]); if (!(p >= 0 && p <= 100)) { WH.toast("The share is a number from 0 to 100.", "err"); return; } v["engine.background_share"] = Math.round(p) / 100; }
          if ("cap_usd" in edit) { const d = Number(edit["cap_usd"]); if (!(d >= 0)) { WH.toast("The daily limit is a number of dollars.", "err"); return; } v["engine.daily_cap_usd"] = d; }
          if (await save(v)) setEdit((e) => { const n = { ...e }; delete n["bg_pct"]; delete n["cap_usd"]; return n; });
        }}>Save</Button></div>
      </Step>

      <Step s={le} n={6} open={st.next === "learn"}>
        <div className="flex flex-wrap items-center gap-2">
          <Chip tone={lr.running ? "ion" : lr.built ? "ok" : "mute"}>{lr.running ? "learning now" : lr.built ? "knowledge base built" : "not started"}</Chip>
          <Chip tone="mute">{lr.chat_notes} chats read</Chip><Chip tone="mute">{lr.kb_notes} notes</Chip>
          {lr.night && <Chip tone="mute">last night's update {when(lr.night)}</Chip>}
        </div>
        {lr.error && !lr.running && <div className="mt-3"><Note tone="warn">{lr.error}</Note></div>}
        <div className="mt-4 flex flex-wrap items-center gap-3">
          <Button variant={lr.built ? "default" : "primary"} busy={busy === "learn"} disabled={!!busy || lr.running} onClick={learn}>{lr.running ? "Learning…" : lr.built ? "Learn again from new chats" : "Start learning"}</Button>
          <span className="max-w-[70ch] text-[13px] text-mute">Jarvis reads the chats in your work folders and builds a knowledge base of your own, on this computer. The first pass can take hours and uses your Claude allowance; it waits when the allowance is nearly used and carries on later. After that it updates itself every night.</span>
        </div>
      </Step>

      <Step s={op} n={7}>
        <div className="grid gap-2" data-setup-parts>
          {op.parts.map((p: any) => (
            <div key={p.id} className="flex flex-wrap items-center gap-3 rounded-[10px] border border-line px-3.5 py-2.5" data-part={p.id}>
              <div className="min-w-0 flex-1"><b className="block text-[14px] font-semibold text-ink">{p.label}</b>
                <span className="text-[13px] text-ink-2">{p.status}{p.how ? ". " + p.how : ""}</span>
                {p.id === "vault" && <input className={field + " mt-2 max-w-[420px] font-mono text-[13px]"} placeholder="the folder, e.g. ~/Notes/Work" value={val("vault.path", p.value)} onChange={(e) => set("vault.path")(e.target.value)} />}
                {p.id === "prwatch" && <input className={field + " mt-2 max-w-[420px] font-mono text-[13px]"} placeholder="your workspace on the code host" value={val("sources.code_workspace", p.value)} onChange={(e) => set("sources.code_workspace")(e.target.value)} />}
              </div>
              <Chip tone={p.on ? "ok" : "mute"}>{p.on ? "on" : "off"}</Chip>
              {p.keys && <Button size="sm" disabled={!!busy} onClick={() => {
                const sw = p.keys[0], v: Record<string, any> = { [sw]: !p.on };
                if (!p.on && p.keys[1] && String(edit[p.keys[1]] || "").trim()) v[p.keys[1]] = String(edit[p.keys[1]]).trim();
                save(v, p.on ? "Switched off." : "Switched on. It starts with Jarvis the next time Jarvis starts.");
              }}>{p.on ? "Switch off" : "Switch on"}</Button>}
            </div>
          ))}
        </div>
      </Step>

      <Step s={tr} n={8}>
        <div className="flex flex-wrap items-center gap-3">
          <a className="text-[14px] font-medium text-ion no-underline hover:underline" href="#/trust">See how far Jarvis may act, kind by kind</a>
          <span className="text-[13px] text-mute">A level only ever rises by your own press there, and only after your own record has earned it.</span>
        </div>
      </Step>

      <Updates />
      <Privacy busy={busy} />
      {!firstTime && <p className="m-0 text-[13px] text-mute">Want to see a made-up person's Jarvis? <button type="button" className="text-ion underline-offset-2 hover:underline" disabled={!!busy} onClick={demo}>Open the demo</button></p>}
    </>
  );
}

/** This version, and whether a newer release is out. Updating is always the owner's press. */
function Updates() {
  const [u, setU] = useState<any>(null), [busy, setBusy] = useState("");
  const look = (now = false) => WH.api("/api/update" + (now ? "?now=1" : "")).then((d: any) => d ? setU(d) : null).catch(() => null);   // an error is shown, not hidden
  useEffect(() => { look(); }, []);
  if (!u) return null;
  const go = async () => {
    setBusy("update");
    const r = await WH.api("/api/update", {}).catch(() => ({ error: "Jarvis is not answering" }));
    if (r && r.ok) { WH.toast(`Updated to ${r.to}. Jarvis is restarting; this page reloads by itself.`, "ok", 8000); setTimeout(() => location.reload(), 9000); }
    else { setBusy(""); WH.toast((r && r.error) || "The update did not run.", "err", 9000); }
  };
  return (
    <section id="setup-updates" data-setup-step="updates">
      <Panel title="Updates" right={u.available ? <Chip tone="ion">{u.latest} available</Chip> : <Chip tone="mute">version {u.current || "?"}</Chip>}>
        <p className="m-0 text-[14px] text-ink-2">
          {u.available ? `Version ${u.latest.replace(/^v/, "")} is out; this is ${u.current}.` : u.why_not || u.error || `This is the newest version, ${u.current}.`}
          {u.checked ? ` Looked ${when(u.checked)}.` : ""}
        </p>
        {u.available && u.why_not && <div className="mt-3"><Note tone="warn">{u.why_not}</Note></div>}
        <div className="mt-3 flex flex-wrap items-center gap-3">
          {u.available && u.can_update && <Button variant="primary" busy={busy === "update"} disabled={!!busy} onClick={go}>Update to {u.latest.replace(/^v/, "")}</Button>}
          <Button size="sm" variant="ghost" disabled={!!busy} onClick={() => look(true)}>Check now</Button>
          <span className="text-[13px] text-mute">Jarvis looks for a new release once a day, with your own git sign-in, and never updates without your press. Your settings are copied first.</span>
        </div>
      </Panel>
    </section>
  );
}

/** A copy of everything, and erasing everything. Erasing moves it to the Trash unless the owner chooses otherwise. */
function Privacy({ busy: pageBusy }: { busy: string }) {
  const [open, setOpen] = useState(false), [p, setP] = useState<any>(null), [typed, setTyped] = useState(""), [mode, setMode] = useState("trash");
  const [busy, setBusy] = useState(""), [done, setDone] = useState<any>(null);
  const size = (b: number) => b >= 1e9 ? (b / 1e9).toFixed(1) + " GB" : b >= 1e6 ? (b / 1e6).toFixed(1) + " MB" : b >= 1e3 ? Math.round(b / 1e3) + " KB" : b + " bytes";
  const preview = async () => { setOpen(true); setP(null); const d = await WH.api("/api/erase_preview").catch(() => null); setP(d && !d.error ? d : { error: (d && d.error) || "Jarvis is not answering" }); };
  const copy = async () => {
    setBusy("copy");
    const r = await WH.api("/api/export", {}).catch(() => ({ error: "Jarvis is not answering" }));
    setBusy("");
    if (r && r.ok) WH.toast(`A copy is saved: ${r.path} (${size(r.bytes)}).`, "ok", 10000); else WH.toast((r && r.error) || "The copy was not made.", "err", 8000);
  };
  const erase = async () => {
    setBusy("erase");
    const r = await WH.api("/api/erase", { confirm: typed, mode }).catch(() => ({ error: "Jarvis is not answering" }));
    setBusy("");
    if (r && r.ok) setDone(r); else WH.toast((r && r.error) || "Nothing was removed.", "err", 9000);
  };
  if (done) return (
    <Panel title="Privacy"><Note tone="ion">Everything Jarvis knew about you was {done.where}. Jarvis has stopped. Starting it again begins a new setup.</Note></Panel>
  );
  return (
    <section id="setup-privacy" data-setup-step="privacy">
      <Panel title="Privacy">
        <p className="m-0 text-[14px] text-ink-2">Everything Jarvis knows about you is on this computer, in one folder. No usage data, crash report or copy of it is ever sent anywhere.</p>
        <div className="mt-3 flex flex-wrap items-center gap-3">
          <Button size="sm" busy={busy === "copy"} disabled={!!busy || !!pageBusy} onClick={copy}>Download a copy</Button>
          {!open && <Button size="sm" variant="ghost" disabled={!!busy || !!pageBusy} onClick={preview}>Erase everything Jarvis knows about me…</Button>}
        </div>
        {open && (
          <div className="mt-4 grid gap-3 rounded-[10px] border border-bad/30 bg-bad-soft/40 px-4 py-3" data-erase>
            {!p ? <Loading rows={3} /> : p.error ? <Note tone="warn">{p.error}</Note> : (<>
              <b className="text-[14px] text-ink">This removes, from {p.home}:</b>
              <ul className="m-0 grid gap-1 pl-5 text-[13.5px] text-ink-2">
                {[...p.rows, ...p.also].map((r: any) => <li key={r.name}><span className="text-ink">{r.what}</span> <span className="text-mute">({size(r.bytes)})</span></li>)}
              </ul>
              <p className="m-0 text-[13.5px] text-ink-2">{p.stays}</p>
              {!p.safe && <Note tone="warn">This data folder does not look like Jarvis's own folder, so erasing is not offered. Move it to the Trash by hand if you are sure.</Note>}
              {p.safe && (<>
                <div className="grid gap-1.5 text-[13.5px] text-ink-2">
                  <label className="flex items-center gap-2"><input type="radio" checked={mode === "trash"} onChange={() => setMode("trash")} /> Move it to {p.trash}, where it can be taken back (recommended)</label>
                  <label className="flex items-center gap-2"><input type="radio" checked={mode === "forever"} onChange={() => setMode("forever")} /> Erase it for good. This cannot be undone.</label>
                </div>
                <div className="flex flex-wrap items-center gap-3">
                  <input className="w-[160px] rounded-md border border-line bg-transparent px-3 py-1.5 font-mono text-[14px] text-ink outline-none focus:border-bad" value={typed} onChange={(e) => setTyped(e.target.value)} placeholder="type ERASE" aria-label="Type ERASE to confirm" />
                  <Button variant="danger" busy={busy === "erase"} disabled={!!busy || typed !== "ERASE"} onClick={erase}>{mode === "forever" ? "Erase for good" : "Move to the Trash"}</Button>
                  <Button variant="ghost" disabled={!!busy} onClick={() => { setOpen(false); setTyped(""); }}>Cancel</Button>
                </div>
              </>)}
            </>)}
          </div>
        )}
      </Panel>
    </section>
  );
}

/** The owner's API key, when Claude is paid for per use. The key goes to the system's password store; it is never shown
    again, never kept in a file of Jarvis's and never logged. */
function ApiKey({ has, where, canStore, onDone }: { has: boolean; where: string; canStore: boolean; onDone: (s: any) => void }) {
  const [k, setK] = useState(""), [busy, setBusy] = useState(false);
  const save = async (body: any, done: string) => {
    setBusy(true);
    const r = await WH.api("/api/setup_key", body).catch(() => ({ error: "Jarvis is not answering" }));
    setBusy(false); setK("");
    if (r && r.ok) { WH.toast(done, "ok", 6000); onDone(r.state); } else WH.toast((r && r.error) || "Nothing was saved.", "err", 8000);
  };
  return (
    <div className="mt-3 grid gap-2" data-api-key>
      <p className="m-0 text-[13px] text-mute">{has ? `Your key is kept in ${where}.` : canStore ? `Your key will be kept in ${where.split(":")[0]}. It is never shown again and never written to a file.` : where}</p>
      {canStore && (
        <form className="flex flex-wrap items-center gap-3" onSubmit={(e) => { e.preventDefault(); if (k.trim()) save({ key: k.trim() }, "The key is kept. Press Check to see Claude answer."); }}>
          <input type="password" autoComplete="off" spellCheck={false} className="w-[340px] max-w-full rounded-md border border-line bg-transparent px-3 py-1.5 font-mono text-[13px] text-ink outline-none focus:border-ion"
            value={k} onChange={(e) => setK(e.target.value)} placeholder={has ? "paste a new key to replace it" : "sk-ant-..."} aria-label="Anthropic API key" />
          <Button type="submit" disabled={busy || !k.trim()}>{has ? "Replace the key" : "Keep the key"}</Button>
          {has && <Button type="button" variant="ghost" disabled={busy} onClick={() => save({ remove: true }, "The key was removed.")}>Remove it</Button>}
        </form>
      )}
    </div>
  );
}

/** What learning reads from the apps. Nothing is read until something is named here. */
function Reads({ r, busy, save, setSt }: { r: any; busy: string; save: (v: Record<string, any>, done?: string) => Promise<boolean>; setSt: (s: any) => void }) {
  const list = (v: any) => (Array.isArray(v) ? v.join(", ") : String(v || ""));
  const lines = (v: any) => (Array.isArray(v) ? v.join("\n") : String(v || ""));
  const [f, setF] = useState(() => ({ t: list(r.values["sources.tracker_projects"]), w: list(r.values["sources.wiki_spaces"]),
    c: lines(r.values["sources.channels"]), m: list(r.values["sources.mail_terms"]), s: r.values["sources.since"] || "" }));
  const [sites, setSites] = useState<any[]>([]), [finding, setFinding] = useState(false);
  const split = (s: string) => s.split(",").map((x) => x.trim()).filter(Boolean);
  const find = async (id?: string) => {
    setFinding(true);
    const x = await WH.api("/api/setup_find_site", id ? { id } : {}).catch(() => ({ error: "Jarvis is not answering" }));
    setFinding(false);
    if (x && x.ok) { setSites(x.sites || []); if (x.state) setSt(x.state); WH.toast(x.chosen ? "Your Atlassian site is set." : "Pick your site below.", "ok"); }
    else WH.toast((x && x.error) || "No site was found.", "err", 8000);
  };
  const field = "w-full rounded-md border border-line bg-transparent px-3 py-1.5 text-[13.5px] text-ink outline-none focus:border-ion";
  const lab = "mb-1 block text-[12.5px] font-medium text-mute";
  return (
    <div className="mt-4 grid gap-3 rounded-[10px] border border-line px-4 py-3" data-setup-reads>
      <b className="text-[14px] text-ink">What learning reads from your apps</b>
      <p className="m-0 text-[13px] text-mute">Nothing is read from the apps until it is named here. Every night Jarvis reads what changed and adds it to your knowledge base.</p>
      <div className="grid grid-cols-2 gap-x-4 gap-y-3 max-[640px]:grid-cols-1">
        <label className="block"><span className={lab}>Ticket projects (their keys)</span><input className={field} value={f.t} onChange={(e) => setF({ ...f, t: e.target.value })} placeholder="ABC, OPS" /></label>
        <label className="block"><span className={lab}>Wiki spaces (their keys)</span><input className={field} value={f.w} onChange={(e) => setF({ ...f, w: e.target.value })} placeholder="DOCS" /></label>
        <label className="col-span-full block"><span className={lab}>Chat channels: one per line, the channel id then its name</span>
          <textarea className={field + " min-h-[64px] font-mono"} value={f.c} onChange={(e) => setF({ ...f, c: e.target.value })} placeholder="C0123ABCD orders-team" /></label>
        <label className="block"><span className={lab}>Words that mark work mail and meetings</span><input className={field} value={f.m} onChange={(e) => setF({ ...f, m: e.target.value })} placeholder="orders, deliveries" /></label>
        <label className="block"><span className={lab}>Read back to (a date)</span><input className={field} value={f.s} onChange={(e) => setF({ ...f, s: e.target.value })} placeholder="twelve months back if empty" /></label>
      </div>
      <div className="flex flex-wrap items-center gap-3">
        <Button size="sm" disabled={!!busy} onClick={() => save({ "sources.tracker_projects": split(f.t).map((x) => x.toUpperCase()), "sources.wiki_spaces": split(f.w),
          "sources.channels": f.c.split("\n").map((x) => x.trim()).filter(Boolean), "sources.mail_terms": split(f.m), "sources.since": f.s.trim() }, "Saved. Tonight's update reads them.")}>Save</Button>
        <span className="text-[13px] text-mute">Tickets and wiki pages need your Atlassian site: {r.site ? <span className="text-ok">set</span> : "not set yet"}.</span>
        <Button size="sm" variant="ghost" disabled={finding || !!busy} onClick={() => find()}>{finding ? "Looking…" : r.site ? "Find it again" : "Find my site"}</Button>
      </div>
      {sites.length > 1 && (
        <div className="flex flex-wrap items-center gap-2">
          <span className="text-[13px] text-mute">Which site is your work?</span>
          {sites.map((s) => <Button key={s.id} size="sm" variant={s.id === r.site ? "primary" : "default"} disabled={finding} onClick={() => find(s.id)}>{s.name || s.url}</Button>)}
        </div>
      )}
    </div>
  );
}
