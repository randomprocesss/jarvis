import * as React from "react";
import { cva, type VariantProps } from "class-variance-authority";
import { cn } from "@/lib/utils";
import { WH } from "@/lib/wh";

const tagVariants = cva("inline-flex items-center gap-1.5 rounded-full px-[9px] py-[3px] text-xs font-semibold whitespace-nowrap leading-[1.35]", {
  variants: { tone: { warn: "bg-warn-soft text-warn", ion: "bg-ion-soft text-ion", ok: "bg-ok-soft text-ok", mute: "bg-line-2 text-mute" } },
  defaultVariants: { tone: "warn" },
});

function Tag({ className, tone, ...props }: React.ComponentProps<"span"> & VariantProps<typeof tagVariants>) {
  return <span className={cn(tagVariants({ tone }), className)} {...props} />;
}

/** Source label (SLACK, EMAIL, ...) in the source's own colour. */
function SrcTag({ s }: { s: string }) {
  return <span className="src" style={{ ["--c" as any]: `var(--s-${s})` }}>{WH.SRC[s]}</span>;
}

/** Count bubble used in section headers and tabs. */
function Count({ n, tone = "mute", className }: { n: number; tone?: "mute" | "warn" | "ok" | "ion"; className?: string }) {
  const t = { mute: "bg-line-2 text-mute", warn: "bg-warn-soft text-warn", ok: "bg-ok-soft text-ok", ion: "bg-ion-soft text-ion" }[tone];
  return <span className={cn("rounded-full px-2 py-px font-mono text-xs font-medium tabular-nums", t, className)}>{n}</span>;
}

export { Tag, SrcTag, Count, tagVariants };
