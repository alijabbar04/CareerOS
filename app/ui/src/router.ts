import { useEffect, useState } from "react";

// Hash routes: #/ (home), #/review, #/review/55, #/review/55/answer-1-r2
export type Tab = "home" | "review" | "tracker" | "shortlist" | "board" | "settings";
export type Route = { tab: Tab; app?: number; stem?: string };

const TABS: Tab[] = ["review", "tracker", "shortlist", "board", "settings"];

export function parse(hash: string): Route {
  const parts = hash.replace(/^#\/?/, "").split("/").filter(Boolean);
  const tab = (TABS as string[]).includes(parts[0]) ? (parts[0] as Tab) : "home";
  const app = parts[1] ? Number(parts[1]) : NaN;
  return { tab, app: Number.isFinite(app) ? app : undefined, stem: parts[2] };
}

export const go = (path: string) => {
  window.location.hash = path;
};

export function useRoute(): Route {
  const [route, setRoute] = useState(() => parse(window.location.hash));
  useEffect(() => {
    const onChange = () => {
      setRoute(parse(window.location.hash));
      window.scrollTo(0, 0);
    };
    window.addEventListener("hashchange", onChange);
    return () => window.removeEventListener("hashchange", onChange);
  }, []);
  return route;
}
