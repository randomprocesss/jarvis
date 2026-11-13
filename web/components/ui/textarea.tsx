"use client";
import * as React from "react";
import { cn } from "@/lib/utils";

const grow = (t: HTMLTextAreaElement | null) => { if (!t) return; t.style.height = "auto"; t.style.height = t.scrollHeight + 2 + "px"; };

/** Textarea that grows with its content (controlled or not). */
function Textarea({ className, autoGrow = true, onChange, ...props }: React.ComponentProps<"textarea"> & { autoGrow?: boolean }) {
  const ref = React.useRef<HTMLTextAreaElement>(null);
  React.useLayoutEffect(() => { if (autoGrow) grow(ref.current); });
  return (
    <textarea
      ref={ref}
      className={cn("w-full rounded-lg border border-line-3 bg-card px-3 py-2.5 text-[14.5px] leading-normal text-ink placeholder:text-mute focus:border-ion focus:shadow-[0_0_0_3px_var(--ion-soft)] focus:outline-none", autoGrow ? "resize-none overflow-hidden" : "resize-y", className)}
      onChange={(e) => { if (autoGrow) grow(e.currentTarget); onChange?.(e); }}
      {...props}
    />
  );
}

function Input({ className, ...props }: React.ComponentProps<"input">) {
  return <input className={cn("w-full rounded-lg border border-line-3 bg-card px-2.5 py-2 text-ink placeholder:text-mute focus:border-ion focus:shadow-[0_0_0_3px_var(--ion-soft)] focus:outline-none", className)} {...props} />;
}

export { Textarea, Input, grow };
