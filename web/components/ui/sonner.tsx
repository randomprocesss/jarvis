"use client";
import { Toaster as Sonner } from "sonner";
import { Check, CircleAlert, Info } from "lucide-react";

const dot = (bg: string, fg: string, I: any) => (
  <span className="grid size-6 place-items-center rounded-full" style={{ background: bg, color: fg }}><I size={14} strokeWidth={2.2} /></span>
);

/** Toasts: bottom-right cards with an icon and a colour per type. */
function Toaster() {
  return (
    <Sonner
      position="bottom-right"
      gap={10}
      offset={20}
      mobileOffset={16}
      icons={{
        success: dot("var(--ok)", "var(--card)", Check),
        error: dot("var(--bad)", "var(--card)", CircleAlert),
        info: dot("var(--ion)", "var(--card)", Info),
      }}
      toastOptions={{
        unstyled: true,
        classNames: {
          toast: "flex w-[min(380px,calc(100vw-32px))] items-start gap-3 rounded-[14px] glass-card border border-line border-l-[2px] px-4 py-3 font-sans text-[14px] leading-[1.45] text-ink shadow-lg backdrop-blur data-[type=success]:border-l-ok data-[type=error]:border-l-bad data-[type=info]:border-l-ion data-[type=success]:bg-[color-mix(in_srgb,var(--ok)_7%,var(--card))] data-[type=error]:bg-[color-mix(in_srgb,var(--bad)_7%,var(--card))] data-[type=info]:bg-[color-mix(in_srgb,var(--ion)_6%,var(--card))]",
          icon: "mt-px flex-none",
        },
      }}
    />
  );
}

export { Toaster };
