"use client";
import dynamic from "next/dynamic";

// The whole app is one client page with hash routes (#/, #/inbox, #/tickets).
const App = dynamic(() => import("@/components/App"), { ssr: false });

export default function Page() {
  return <App />;
}
