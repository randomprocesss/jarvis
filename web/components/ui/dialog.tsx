"use client";
import * as React from "react";
import * as DialogPrimitive from "@radix-ui/react-dialog";
import { cn } from "@/lib/utils";

const Dialog = DialogPrimitive.Root;
const DialogClose = DialogPrimitive.Close;

function DialogContent({ className, children, ...props }: React.ComponentProps<typeof DialogPrimitive.Content>) {
  return (
    <DialogPrimitive.Portal>
      <DialogPrimitive.Overlay className="wh-overlay fixed inset-0 z-[60] bg-black/40 backdrop-blur-[4px]" />
      <DialogPrimitive.Content
        className={cn("wh-sheet fixed left-1/2 top-[16vh] z-[61] w-[min(580px,calc(100vw-32px))] -translate-x-1/2 glass-panel rounded-[18px] border-0 bg-card text-ink shadow-lg outline-none", className)}
        {...props}
      >
        {children}
      </DialogPrimitive.Content>
    </DialogPrimitive.Portal>
  );
}

function DialogTitle({ className, ...props }: React.ComponentProps<typeof DialogPrimitive.Title>) {
  return <DialogPrimitive.Title className={cn("m-0 font-display text-xl font-normal leading-[1.3] tracking-[-.02em]", className)} {...props} />;
}

function DialogDescription({ className, ...props }: React.ComponentProps<typeof DialogPrimitive.Description>) {
  return <DialogPrimitive.Description className={cn("mt-2.5 text-[14.5px] text-ink-2", className)} {...props} />;
}

export { Dialog, DialogClose, DialogContent, DialogTitle, DialogDescription };
