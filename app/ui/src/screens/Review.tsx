import { useEffect, useRef, useState, type ReactNode } from "react";
import { ApiError, get, post, type Application, type ApplicationDetail, type Draft } from "../api";
import { go } from "../router";
import { Card, Empty, Failure, Loading, Pill, Row, STATUS_LABEL, STATUS_TONE, daysLeft, type Tone } from "../components/ui";
import { useData } from "../components/useData";
import Prose from "../components/Prose";

const SENT = ["submitted", "acknowledged", "assessment", "interview", "offer", "accepted", "rejected", "ghosted"];

function kindLabel(d: Draft): string {
  const kind = d.kind ?? d.stem.replace(/-r\d+$/, "");
  const [base, n] = kind.split(/-(?=\d+$)/);
  const name = base === "answer" ? "Answer" : base === "cover-letter" ? "Cover letter" : base === "employment" ? "Employment entry" : base;
  return n ? `${name} ${n}` : name;
}

function verdictTone(v: string | null | undefined): Tone {
  return v === "PASS" ? "ok" : v === "FAIL" ? "bad" : "neutral";
}

function Checks({ d }: { d: Draft }) {
  const hard = d.verify.hard_fails?.length ?? 0;
  return (
    <div className="flex flex-wrap gap-1.5">
      <Pill tone={hard ? "bad" : d.verify.hard_fails ? "ok" : "neutral"}>{hard ? `${hard} check${hard > 1 ? "s" : ""} failed` : d.verify.hard_fails ? "Checks pass" : "Not checked"}</Pill>
      {d.verdicts.verifier && <Pill tone={verdictTone(d.verdicts.verifier)}>Verifier {d.verdicts.verifier === "PASS" ? "✓" : "✗"}</Pill>}
      {d.verdicts.red_team && <Pill tone={verdictTone(d.verdicts.red_team)}>Red team {d.verdicts.red_team === "PASS" ? "✓" : "✗"}</Pill>}
    </div>
  );
}

// ---------------------------------------------------------------------------
// List
// ---------------------------------------------------------------------------

function ApplicationList() {
  const { data, error } = useData(() => get<Application[]>("/applications"), "apps");
  if (error) return <Failure error={error} />;
  if (!data) return <Loading />;
  const recent = data.filter((a) => a.folder);   // applications with a CareerOS folder (not the imported history)
  const groups: [string, Application[]][] = [
    ["Needs you", recent.filter((a) => a.status === "ready-for-review")],
    ["Approved, not sent", recent.filter((a) => a.status === "approved")],
    ["Drafting", recent.filter((a) => a.status === "draft")],
    ["Sent", recent.filter((a) => SENT.includes(a.status))],
  ];
  return (
    <div className="space-y-4">
      <h1 className="pt-2 text-[26px] font-semibold tracking-tight">Review</h1>
      {groups.filter(([, apps]) => apps.length).map(([title, apps]) => (
        <Card key={title} title={title}>
          {apps.map((a) => (
            <Row key={a.id} title={a.company} sub={a.deadline ? `${a.role} · closes ${a.deadline.slice(0, 10)}` : a.role}
                 right={<Pill tone={STATUS_TONE[a.status] ?? "neutral"}>{STATUS_LABEL[a.status] ?? a.status}</Pill>}
                 onClick={() => go(`/review/${a.id}`)} />
          ))}
        </Card>
      ))}
      {recent.length === 0 && <Empty>No applications yet.</Empty>}
    </div>
  );
}

// ---------------------------------------------------------------------------
// One application
// ---------------------------------------------------------------------------

function ApplicationView({ id }: { id: number }) {
  const { data, error } = useData(() => get<ApplicationDetail>(`/applications/${id}`), `app-${id}`);
  if (error) return <Failure error={error} />;
  if (!data) return <Loading />;
  const left = data.deadline ? daysLeft(data.deadline) : null;
  return (
    <div className="space-y-4">
      <button onClick={() => go("/review")} className="min-h-[44px] pt-2 text-[15px] text-info">‹ Review</button>
      <header>
        <h1 className="text-[24px] font-semibold leading-tight tracking-tight">{data.company}</h1>
        <p className="mt-1 text-[15px] text-muted">{data.role}</p>
        <div className="mt-3 flex flex-wrap gap-1.5">
          <Pill tone={STATUS_TONE[data.status] ?? "neutral"}>{STATUS_LABEL[data.status] ?? data.status}</Pill>
          {left && <Pill tone={left.tone}>closes {data.deadline!.slice(0, 10)} · {left.label}</Pill>}
        </div>
      </header>

      {data.fact_requests.length > 0 && (
        <Card title="Questions for you">
          {data.fact_requests.map((q) => <p key={q} className="border-t border-line py-2.5 text-[15px] first:border-t-0">{q.replace(/^FACT REQUEST:\s*/i, "")}</p>)}
        </Card>
      )}

      <Card title="To review">
        {data.drafts.length === 0 ? <Empty>Nothing waiting. Everything drafted is approved.</Empty> :
          data.drafts.map((d) => (
            <Row key={d.stem} title={d.question ?? kindLabel(d)} sub={`${kindLabel(d)} · round ${d.round ?? "?"} · ${d.words} words`}
                 right={<Pill tone={d.verify.hard_fails?.length ? "bad" : "warn"}>review</Pill>} onClick={() => go(`/review/${id}/${d.stem}`)} />
          ))}
      </Card>

      <Card title={`Approved (${data.finals.length})`}>
        {data.finals.length === 0 ? <Empty>Nothing approved yet.</Empty> :
          data.finals.map((d) => (
            <Row key={d.stem} title={d.question ?? kindLabel(d)} sub={`${kindLabel(d)} · ${d.words} words`}
                 right={<Pill tone="ok">approved</Pill>} onClick={() => go(`/review/${id}/${d.stem}`)} />
          ))}
      </Card>
    </div>
  );
}

// ---------------------------------------------------------------------------
// One draft: read, approve, edit, reject
// ---------------------------------------------------------------------------

function Sheet({ title, children, onClose }: { title: string; children: ReactNode; onClose: () => void }) {
  return (
    <div className="fixed inset-0 z-30 flex flex-col bg-bg" role="dialog" aria-label={title}>
      <div className="flex items-center justify-between border-b border-line px-4 pb-3 pt-[calc(12px+env(safe-area-inset-top))]">
        <button onClick={onClose} className="min-h-[44px] text-[15px] text-muted">Cancel</button>
        <h2 className="text-[16px] font-semibold">{title}</h2>
        <span className="w-12" />
      </div>
      <div className="flex min-h-0 flex-1 flex-col p-4">{children}</div>
    </div>
  );
}

function DraftView({ id, stem, toast }: { id: number; stem: string; toast: (msg: string) => void }) {
  const { data, error } = useData(() => get<Draft>(`/applications/${id}/drafts/${stem}`), `draft-${id}-${stem}`);
  const [armed, setArmed] = useState(false);
  const [busy, setBusy] = useState(false);
  const [sheet, setSheet] = useState<"edit" | "reject" | null>(null);
  const [text, setText] = useState("");
  const timer = useRef<number | undefined>(undefined);
  useEffect(() => () => window.clearTimeout(timer.current), []);

  if (error) return <Failure error={error} />;
  if (!data) return <Loading />;

  const act = async (fn: () => Promise<void>) => {
    setBusy(true);
    try {
      await fn();
    } catch (e) {
      toast(e instanceof ApiError ? e.detail : "Couldn't reach the laptop. Nothing was changed.");
    } finally {
      setBusy(false);
    }
  };

  const approve = () => {
    if (!armed) {
      setArmed(true);
      timer.current = window.setTimeout(() => setArmed(false), 4000);
      return;
    }
    window.clearTimeout(timer.current);
    setArmed(false);
    act(async () => {
      await post(`/applications/${id}/drafts/${stem}/approve`);
      toast("Approved. It's in final and on OneDrive.");
      go(`/review/${id}`);
    });
  };

  const saveEdit = () => act(async () => {
    const r = await post<{ stem: string; words_changed: number; verify: Draft["verify"] }>(`/applications/${id}/drafts/${stem}/edit`, { text });
    setSheet(null);
    const hard = r.verify.hard_fails?.length ?? 0;
    toast(`Saved as a new round (${r.words_changed} words changed). ${hard ? `${hard} check${hard > 1 ? "s" : ""} to look at.` : "Checks pass."}`);
    go(`/review/${id}/${r.stem}`);
  });

  const reject = () => act(async () => {
    await post(`/applications/${id}/drafts/${stem}/reject`, { reason: text || undefined });
    setSheet(null);
    toast("Rejected. It goes back for a redraft.");
    go(`/review/${id}`);
  });

  return (
    <div className={data.approved ? "space-y-4" : "space-y-4 pb-24"}>
      <button onClick={() => go(`/review/${id}`)} className="min-h-[44px] pt-2 text-[15px] text-info">‹ Back</button>
      <header className="space-y-2">
        <p className="text-[13px] text-muted">{kindLabel(data)} · round {data.round ?? "?"} · {data.words} words</p>
        <h1 className="text-[20px] font-semibold leading-snug">{data.question ?? kindLabel(data)}</h1>
        {data.approved ? <Pill tone="ok">Approved · in final</Pill> : <Checks d={data} />}
      </header>

      {!!data.verify.hard_fails?.length && (
        <Card title="Failed checks">
          {data.verify.hard_fails.map((f) => <p key={f} className="border-t border-line py-2 text-[14px] text-bad first:border-t-0">{f}</p>)}
        </Card>
      )}

      <article className="card px-4 py-4 text-[17px] leading-[1.65]"><Prose text={data.text} /></article>

      {!data.approved && (
        <div className="fixed inset-x-0 bottom-[calc(64px+env(safe-area-inset-bottom))] z-20 border-t border-line bg-bg/95 px-4 py-3 backdrop-blur">
          <div className="mx-auto flex max-w-xl gap-2">
            <button disabled={busy} onClick={() => { setText(""); setSheet("reject"); }}
                    className="btn min-h-[48px] flex-1 border border-line text-[15px] font-medium text-bad disabled:opacity-50">Reject</button>
            <button disabled={busy} onClick={() => { setText(data.text); setSheet("edit"); }}
                    className="btn min-h-[48px] flex-1 bg-surface-2 text-[15px] font-medium disabled:opacity-50">Edit</button>
            <button disabled={busy || !!data.verify.hard_fails?.length} onClick={approve}
                    className={`btn min-h-[48px] flex-[1.4] text-[15px] font-semibold disabled:opacity-40 ${armed ? "bg-warn text-bg" : "bg-accent text-bg"}`}>
              {armed ? "Tap again to approve" : "Approve"}
            </button>
          </div>
        </div>
      )}

      {sheet === "edit" && (
        <Sheet title="Edit" onClose={() => setSheet(null)}>
          <textarea value={text} onChange={(e) => setText(e.target.value)} autoFocus
                    className="card min-h-0 flex-1 resize-none p-3 text-[16px] leading-[1.6] outline-none" />
          <p className="py-2 text-[13px] text-muted">{text.split(/\s+/).filter(Boolean).length} words · saved as a new round and checked again</p>
          <button disabled={busy || !text.trim()} onClick={saveEdit} className="btn min-h-[48px] bg-accent text-[15px] font-semibold text-bg disabled:opacity-40">Save edit</button>
        </Sheet>
      )}

      {sheet === "reject" && (
        <Sheet title="Reject" onClose={() => setSheet(null)}>
          <label className="pb-2 text-[15px]" htmlFor="reason">What's wrong with it? This guides the redraft.</label>
          <textarea id="reason" value={text} onChange={(e) => setText(e.target.value)} autoFocus
                    className="card min-h-[160px] resize-none p-3 text-[16px] outline-none" />
          <div className="flex-1" />
          <button disabled={busy} onClick={reject} className="btn min-h-[48px] bg-bad text-[15px] font-semibold text-bg disabled:opacity-40">Reject draft</button>
        </Sheet>
      )}
    </div>
  );
}

export default function Review({ app, stem, toast }: { app?: number; stem?: string; toast: (msg: string) => void }) {
  if (app && stem) return <DraftView id={app} stem={stem} toast={toast} />;
  if (app) return <ApplicationView id={app} />;
  return <ApplicationList />;
}
