import { useCallback, useEffect, useState } from "react";
import { go, useRoute, type Tab } from "./router";
import Home from "./screens/Home";
import Review from "./screens/Review";
import Tracker from "./screens/Tracker";
import Shortlist from "./screens/Shortlist";
import Board from "./screens/Board";
import Settings from "./screens/Settings";

const NAV: { tab: Tab; label: string; icon: string }[] = [
  { tab: "home", label: "Home", icon: "M3 11l9-7 9 7v9a1 1 0 0 1-1 1h-5v-6h-6v6H4a1 1 0 0 1-1-1z" },
  { tab: "review", label: "Review", icon: "M5 4h10l4 4v12a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V5a1 1 0 0 1 1-1zm3 9l2.5 2.5L16 10" },
  { tab: "tracker", label: "Tracker", icon: "M4 20V10m6 10V4m6 16v-7m4 7H2" },
  { tab: "shortlist", label: "Shortlist", icon: "M12 3l2.6 5.6 6.1.7-4.5 4.2 1.2 6L12 16.6 6.6 19.5l1.2-6L3.3 9.3l6.1-.7z" },
  { tab: "board", label: "Board", icon: "M4 5h16M4 12h16M4 19h10" },
];

export default function App() {
  const route = useRoute();
  const [toast, setToast] = useState<string | null>(null);
  const [online, setOnline] = useState(true);
  const show = useCallback((msg: string) => setToast(msg), []);


  useEffect(() => {
    if (!toast) return;
    const t = window.setTimeout(() => setToast(null), 4000);
    return () => window.clearTimeout(t);
  }, [toast]);

  useEffect(() => {
    const ping = () => fetch("/health").then((r) => setOnline(r.ok)).catch(() => setOnline(false));
    ping();
    const t = window.setInterval(ping, 30_000);
    return () => window.clearInterval(t);
  }, []);

  return (
    <div className="mx-auto min-h-screen max-w-xl px-4 pb-[calc(88px+env(safe-area-inset-bottom))] pt-[env(safe-area-inset-top)]">
      {!online && (
        <div className="sticky top-0 z-10 -mx-4 mb-2 bg-warn/15 px-4 py-2 text-center text-[13px] text-warn">
          Laptop offline: showing what was loaded before
        </div>
      )}

      {route.tab === "home" && <Home />}
      {route.tab === "review" && <Review app={route.app} stem={route.stem} toast={show} />}
      {route.tab === "tracker" && <Tracker />}
      {route.tab === "shortlist" && <Shortlist toast={show} />}
      {route.tab === "board" && <Board />}
      {route.tab === "settings" && <Settings />}

      {toast && (
        <div role="status" className="fixed inset-x-4 bottom-[calc(140px+env(safe-area-inset-bottom))] z-40 mx-auto max-w-md rounded-xl border border-line bg-surface-2 px-4 py-3 text-[14px] shadow-lg">
          {toast}
        </div>
      )}

      <nav className="fixed inset-x-0 bottom-0 z-20 border-t border-line bg-surface/95 backdrop-blur" aria-label="Main">
        <div className="mx-auto flex max-w-xl px-2 pb-[calc(6px+env(safe-area-inset-bottom))] pt-1.5">
          {NAV.map((n) => {
            const active = n.tab === route.tab;
            return (
              <button key={n.tab} onClick={() => go(n.tab === "home" ? "/" : `/${n.tab}`)} aria-current={active ? "page" : undefined}
                      className={`flex min-h-[52px] flex-1 flex-col items-center justify-center gap-1 rounded-xl text-[11px] ${active ? "text-accent" : "text-muted"}`}>
                <svg viewBox="0 0 24 24" className="h-6 w-6" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                  <path d={n.icon} />
                </svg>
                {n.label}
              </button>
            );
          })}
        </div>
      </nav>
    </div>
  );
}
