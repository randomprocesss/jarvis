import type { Metadata, Viewport } from "next";
import "./globals.css";

export const metadata: Metadata = { title: "Jarvis", description: "What needs you, what Jarvis did, how far it may act, and what it knows." };
export const viewport: Viewport = { width: "device-width", initialScale: 1, viewportFit: "cover" };

// 12 Nov 2026 (redesign): the look follows the Mac's appearance, light or dark, and switches with it. Set before the
// first paint so nothing flashes. System fonts: no web font is fetched.
const THEME = `(function(){try{var m=matchMedia('(prefers-color-scheme: dark)');var f=function(){if(m.matches)document.documentElement.dataset.theme='dark';else delete document.documentElement.dataset.theme};f();m.addEventListener('change',f);var h=(location.hash||'').replace(/^#\\/?/,'').split('/')[0];if(h===''||h==='home')document.documentElement.dataset.scene='core'}catch(e){}})()`;

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" suppressHydrationWarning>
      <head>
        <script dangerouslySetInnerHTML={{ __html: THEME }} />
        {/* the presenter's patterns (state/presenter.json via the server), before the app's own code */}
        <script src="/presenter.js" />
      </head>
      <body>{children}</body>
    </html>
  );
}
