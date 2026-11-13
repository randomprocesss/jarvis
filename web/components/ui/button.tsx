"use client";
import * as React from "react";
import { Slot } from "@radix-ui/react-slot";
import { cva, type VariantProps } from "class-variance-authority";
import { cn } from "@/lib/utils";

const buttonVariants = cva(
  "inline-flex items-center justify-center gap-2 whitespace-nowrap border font-sans font-semibold no-underline transition-[background-color,border-color,color,box-shadow,filter,transform] duration-150 ease-out active:translate-y-px focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ion focus-visible:ring-offset-2 focus-visible:ring-offset-[var(--card)] disabled:pointer-events-none disabled:opacity-50 [&_svg]:shrink-0 cursor-pointer",
  {
    variants: {
      variant: {
        default: "border-line-3 bg-card text-ink hover:bg-card-2 hover:border-ion/50",
        primary: "border-ion bg-ion text-primary-foreground hover:brightness-110",
        ghost: "border-transparent bg-transparent text-ink-2 hover:bg-line-2 hover:text-ink",
        danger: "border-bad bg-bad text-white hover:brightness-95",
        // the small rounded "linkbtn" of the old UI
        pill: "rounded-full border-line-3 bg-card text-mute font-medium hover:border-ion hover:text-ion",
        pillOk: "rounded-full border-ok/45 bg-ok-soft text-ok font-medium",
        icon: "border-transparent bg-transparent text-mute hover:bg-line-2 hover:border-line hover:text-ink",
      },
      size: {
        default: "min-h-[38px] rounded-[10px] px-4 py-2 text-[14px]",
        sm: "min-h-8 rounded-[10px] px-3 py-1 text-[13px]",
        lg: "min-h-[46px] rounded-[10px] px-[22px] py-2.5 text-[15.5px]",
        pill: "px-[9px] py-0.5 text-xs gap-[5px]",
        icon: "size-[34px] rounded-[10px] p-0",
        iconSm: "size-7 rounded-[8px] p-0",
      },
    },
    defaultVariants: { variant: "default", size: "default" },
  }
);

type ButtonProps = React.ComponentProps<"button"> & VariantProps<typeof buttonVariants> & { asChild?: boolean; busy?: boolean };

function Button({ className, variant, size, asChild = false, busy = false, disabled, children, type, ...props }: ButtonProps) {
  const cls = cn(buttonVariants({ variant, size }), busy && "cursor-progress [&_svg]:hidden", className);
  if (asChild) return <Slot className={cls} {...(props as any)}>{children}</Slot>;
  return (
    <button type={type || "button"} className={cls} disabled={disabled || busy} aria-busy={busy || undefined} {...props}>
      {busy && <span className="spin" aria-hidden />}
      {children}
    </button>
  );
}

/** Small rounded link-style button ("Open", "Undo", "Change"). */
function PillButton({ className, on, ...props }: ButtonProps & { on?: boolean }) {
  return <Button variant={on ? "pillOk" : "pill"} size="pill" className={className} {...props} />;
}

export { Button, PillButton, buttonVariants };
