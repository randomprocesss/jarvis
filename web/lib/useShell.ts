"use client";
import { useEffect, useRef } from "react";
import { WH } from "@/lib/wh";

/** Register the current view's ⌘K actions and ⌘S save with the shell (cleared when the view unmounts). */
export function useShell(actions?: () => any[], save?: () => any) {
  const a = useRef(actions), s = useRef(save);
  a.current = actions; s.current = save;
  useEffect(() => {
    WH.shell.actions = () => (a.current ? a.current() : []);
    WH.shell.save = s.current ? () => s.current && s.current() : null;
    return () => { WH.shell.actions = null; WH.shell.save = null; };
  }, []);
}

/** Re-render every `ms` while the window is visible. */
export function useTick(ms: number, f: () => void) {
  const r = useRef(f); r.current = f;
  useEffect(() => { const id = setInterval(() => { if (!document.hidden) r.current(); }, ms); return () => clearInterval(id); }, [ms]);
}
