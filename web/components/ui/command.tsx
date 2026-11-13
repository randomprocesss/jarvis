"use client";
import * as React from "react";
import { Command as CommandPrimitive } from "cmdk";
import { cn } from "@/lib/utils";

function Command({ className, ...props }: React.ComponentProps<typeof CommandPrimitive>) {
  return <CommandPrimitive className={cn("flex flex-col", className)} {...props} />;
}

function CommandInput({ className, ...props }: React.ComponentProps<typeof CommandPrimitive.Input>) {
  return <CommandPrimitive.Input className={cn("min-w-0 flex-1 border-0 bg-transparent text-[17px] text-ink outline-none placeholder:text-mute", className)} {...props} />;
}

function CommandList({ className, ...props }: React.ComponentProps<typeof CommandPrimitive.List>) {
  return <CommandPrimitive.List className={cn("max-h-[52vh] overflow-auto p-2", className)} {...props} />;
}

function CommandItem({ className, ...props }: React.ComponentProps<typeof CommandPrimitive.Item>) {
  return (
    <CommandPrimitive.Item
      className={cn("flex cursor-pointer items-center gap-3 rounded-[10px] px-3 py-2.5 text-[14.5px] text-ink-2 transition-colors duration-100 [&_svg]:text-mute data-[selected=true]:bg-ion/[.07] data-[selected=true]:text-ink data-[selected=true]:[&_svg]:text-arc-hi", className)}
      {...props}
    />
  );
}

export { Command, CommandInput, CommandList, CommandItem };
