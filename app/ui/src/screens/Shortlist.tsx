import { useState } from "react";
import { ApiError, get, post, type ShortlistRow } from "../api";
import { go } from "../router";
import { Card, Empty, Failure, Loading, Pill } from "../components/ui";
import { useData } from "../components/useData";

export default function Shortlist({ toast }: { toast: (msg: string) => void }) {
  const { data, error } = useData(() => get<ShortlistRow[]>("/shortlist/latest"), "shortlist");
  const [done, setDone] = useState<Record<number, string>>({});
  const [busy, setBusy] = useState<number | null>(null);

  if (error) return <Failure error={error} />;
  if (!data) return <Loading />;

  const act = async (row: ShortlistRow, action: "approve" | "skip") => {
    if (!row.posting_id) return;
    setBusy(row.posting_id);
    try {
      if (action === "approve") {
        const r = await post<{ application_id: number }>(`/shortlist/${row.posting_id}/approve`);
        setDone((d) => ({ ...d, [row.posting_id!]: "approved" }));
        toast(`Application created for ${row.company}. Drafting starts from its brief.`);
        go(`/review/${r.application_id}`);
      } else {
        await post(`/shortlist/${row.posting_id}/skip`);
        setDone((d) => ({ ...d, [row.posting_id!]: "skipped" }));
      }
    } catch (e) {
      toast(e instanceof ApiError ? e.detail : "Couldn't reach the laptop. Nothing was changed.");
    } finally {
      setBusy(null);
    }
  };

  const open = data.filter((r) => !r.posting_id || done[r.posting_id] !== "skipped");
  return (
    <div className="space-y-4">
      <header className="pt-2">
        <h1 className="text-[26px] tracking-tight">Shortlist</h1>
        <p className="text-[13px] text-muted">Today's best matches, ranked by fit. Approving creates the application and its brief.</p>
      </header>
      {open.length === 0 ? <Card><Empty>Nothing new on today's shortlist.</Empty></Card> : open.map((r) => (
        <Card key={`${r.rank}-${r.url}`}>
          <div className="flex items-start gap-3 py-1">
            <span className="mt-0.5 w-6 shrink-0 text-[20px] font-semibold text-muted" style={{ fontFamily: "var(--font-head)" }}>{r.rank}</span>
            <div className="min-w-0 flex-1">
              <h2 className="text-[17px] leading-snug">{r.title}</h2>
              <p className="mt-0.5 text-[14px] text-muted">{r.company}{r.location && !/not published/i.test(r.location) ? ` · ${r.location}` : ""}</p>
              <div className="mt-2 flex flex-wrap gap-1.5">
                <Pill tone={r.score >= 85 ? "ok" : "neutral"}>fit {r.score}/100</Pill>
                <Pill>{r.closes_at ? `closes ${r.closes_at}` : "no closing date"}</Pill>
                {r.application_id && <Pill tone="info">in the pipeline</Pill>}
              </div>
            </div>
          </div>
          <div className="mt-2 flex gap-2 border-t border-line pt-3">
            {r.url && <a href={r.url} target="_blank" rel="noreferrer" className="btn flex min-h-[44px] flex-1 items-center justify-center bg-surface-2 text-[14px]">Posting</a>}
            {r.application_id ? (
              <button onClick={() => go(`/review/${r.application_id}`)} className="btn min-h-[44px] flex-1 bg-surface-2 text-[14px]">Open</button>
            ) : r.posting_id ? (
              <>
                <button disabled={busy === r.posting_id} onClick={() => act(r, "skip")} className="btn min-h-[44px] flex-1 border border-line text-[14px] text-muted disabled:opacity-50">Skip</button>
                <button disabled={busy === r.posting_id} onClick={() => act(r, "approve")} className="btn min-h-[44px] flex-[1.3] bg-accent text-[14px] font-semibold text-bg disabled:opacity-50">Apply</button>
              </>
            ) : <span className="self-center text-[13px] text-muted">Posting not in the tracker yet</span>}
          </div>
        </Card>
      ))}
    </div>
  );
}
