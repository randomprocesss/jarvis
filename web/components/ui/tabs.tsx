"use client";
import * as React from "react";
import * as TabsPrimitive from "@radix-ui/react-tabs";
import { cn } from "@/lib/utils";

const Tabs = TabsPrimitive.Root;

function TabsList({ className, ...props }: React.ComponentProps<typeof TabsPrimitive.List>) {
  return <TabsPrimitive.List className={cn("inline-flex max-w-full flex-wrap items-center gap-1 rounded-[12px] border border-line bg-line-2 p-1", className)} {...props} />;
}

function TabsTrigger({ className, ...props }: React.ComponentProps<typeof TabsPrimitive.Trigger>) {
  return (
    <TabsPrimitive.Trigger
      className={cn("relative z-0 inline-flex items-center gap-2 rounded-[9px] border-0 bg-transparent px-3 py-1.5 text-[13.5px] font-semibold text-mute transition-colors duration-150 hover:text-ink focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ion data-[state=active]:text-ink", className)}
      {...props}
    />
  );
}

function TabsContent({ className, ...props }: React.ComponentProps<typeof TabsPrimitive.Content>) {
  return <TabsPrimitive.Content className={cn("outline-none", className)} {...props} />;
}

export { Tabs, TabsList, TabsTrigger, TabsContent };
