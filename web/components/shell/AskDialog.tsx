"use client";
import { useEffect, useRef, useState } from "react";
import { WH } from "@/lib/wh";
import { Dialog, DialogContent, DialogDescription, DialogTitle } from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";

/** WH.ask(title, body, yes, danger) -> Promise<boolean>, as a calm confirm dialog. Esc / outside click = Cancel. */
export function AskDialog() {
  const [req, setReq] = useState<any>(null), [open, setOpen] = useState(false);
  const res = useRef<((v: boolean) => void) | null>(null), yes = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    WH.setAskHandler((r) => { if (res.current) res.current(false); res.current = r.res; setReq(r); setOpen(true); });
    return () => WH.setAskHandler(null);
  }, []);
  const done = (v: boolean) => { const f = res.current; res.current = null; setOpen(false); if (f) f(v); };
  return (
    <Dialog open={open} onOpenChange={(o) => { if (!o) done(false); }}>
      <DialogContent role="alertdialog" aria-modal="true" className="p-6" onOpenAutoFocus={(e) => { e.preventDefault(); setTimeout(() => yes.current?.focus(), 30); }}>
        <DialogTitle>{req?.title}</DialogTitle>
        <DialogDescription className="max-h-[46vh] overflow-auto whitespace-pre-line">{req?.body}</DialogDescription>
        <div className="mt-[22px] flex flex-wrap justify-end gap-2">
          <Button variant="ghost" onClick={() => done(false)}>Cancel</Button>
          <Button ref={yes} variant={req?.danger ? "danger" : "primary"} onClick={() => done(true)}>{req?.yes}</Button>
        </div>
      </DialogContent>
    </Dialog>
  );
}
