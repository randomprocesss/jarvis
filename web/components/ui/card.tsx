import * as React from "react";
import { Inbox as InboxIcon } from "lucide-react";
import { cn } from "@/lib/utils";
import { WH, useWH } from "@/lib/wh";

/** Card: 14px radius, 1px hairline, very soft shadow. `lift` adds the hover lift (1px) and the stronger shadow. */
function Card({ className, lift, ...props }: React.ComponentProps<"div"> & { lift?: boolean }) {
  return (
    <div
      className={cn("glass-card rounded-[16px] border border-line text-card-foreground shadow-card",
        lift && "transition-[box-shadow,translate,border-color] duration-200 ease-out hover:-translate-y-px hover:border-line-3 hover:shadow-card-hover motion-reduce:hover:translate-y-0",
        className)}
      {...props}
    />
  );
}

/** Friendly empty state: a large soft icon (or, with `art`, a picture: 13 Nov, the finished machine at rest), a bold line
    and a muted line. The picture is shown whole, so the provider's mark in its corner stays visible. */
function Empty({ title, children, className, icon: Icon = InboxIcon, tone = "ion", bare, art }: { title?: React.ReactNode; children?: React.ReactNode; className?: string; icon?: any; tone?: "ion" | "ok" | "warn" | "mute"; bare?: boolean; art?: string }) {
  useWH(["me"]);
  if (!WH.me.media) art = undefined;                    // the pictures are an optional pack
  const c = { ion: "var(--ion)", ok: "var(--ok)", warn: "var(--warn)", mute: "var(--mute)" }[tone];
  const body = (
    <div className={cn("flex flex-col items-center px-6 py-10 text-center", className)}>
      {art ? (
        <span className="jv-empty-art mb-5 block aspect-video w-full max-w-[360px] overflow-hidden rounded-[14px]">
          <img src={art} alt="" aria-hidden decoding="async" loading="lazy" className="block h-full w-full object-cover" />
        </span>
      ) : (
        <span className="mb-4 grid size-14 place-items-center rounded-2xl" style={{ color: c, background: `color-mix(in srgb, ${c} 10%, var(--card))` }}>
          <Icon size={26} strokeWidth={1.6} aria-hidden />
        </span>
      )}
      {title && <b className="block font-display text-[16px] font-semibold text-ink">{title}</b>}
      {children && <div className="mt-1 max-w-[46ch] text-[14px] text-mute">{children}</div>}
    </div>
  );
  return bare ? body : <Card>{body}</Card>;
}

/** Shimmer placeholder line/box. */
function Skeleton({ className, ...props }: React.ComponentProps<"div">) {
  return <div aria-hidden className={cn("skel h-3.5", className)} {...props} />;
}

/** A few shimmering rows shaped like cards, while data loads. */
function SkeletonList({ rows = 3, className }: { rows?: number; className?: string }) {
  return (
    <div className={cn("grid gap-3", className)} role="status" aria-label="Loading">
      {Array.from({ length: rows }).map((_, i) => (
        <Card key={i} className="flex items-start gap-3.5 px-5 py-4">
          <Skeleton className="size-9 flex-none rounded-full" />
          <div className="grid flex-1 gap-2 pt-1">
            <Skeleton className="w-2/5" />
            <Skeleton className="h-3 w-4/5" />
          </div>
        </Card>
      ))}
    </div>
  );
}

export { Card, Empty, Skeleton, SkeletonList };
