import { get, type Home as HomeData } from "../api";
import { go } from "../router";
import { Card, Empty, Failure, Loading, Pill, Row, daysLeft } from "../components/ui";
import { useData } from "../components/useData";

function greeting(): string {
  const h = new Date().getHours();
  return h < 12 ? "Good morning" : h < 18 ? "Good afternoon" : "Good evening";
}

function Tile({ label, value, tone, onClick }: { label: string; value: number; tone: string; onClick: () => void }) {
  return (
    <button onClick={onClick} className="card flex min-h-[84px] flex-1 flex-col justify-between p-3 text-left active:opacity-70">
      <span className={`text-[28px] font-semibold leading-none ${value ? tone : "text-muted"}`}>{value}</span>
      <span className="text-[12px] text-muted">{label}</span>
    </button>
  );
}

export default function Home() {
  const { data, error } = useData(() => get<HomeData>("/home"), "home");
  const today = new Date().toLocaleDateString("en-GB", { weekday: "long", day: "numeric", month: "long" });

  return (
    <div className="space-y-4">
      <header className="flex items-start justify-between pt-2">
        <div>
          <p className="text-[13px] text-muted">{today}</p>
          <h1 className="text-[26px] tracking-tight">{greeting()}, Ali</h1>
        </div>
        <button onClick={() => go("/settings")} aria-label="Settings" className="-mr-2 flex h-11 w-11 items-center justify-center rounded-full text-muted active:opacity-70">
          <svg viewBox="0 0 24 24" className="h-6 w-6" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
            <circle cx="12" cy="12" r="3" /><path d="M19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1.1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z" />
          </svg>
        </button>
      </header>

      {error ? <Failure error={error} /> : !data ? <Loading /> : (
        <>
          <div className="flex gap-3">
            <Tile label="To review" value={data.to_review.length} tone="text-warn" onClick={() => go("/review")} />
            <Tile label="Deadlines this week" value={data.deadlines.filter((d) => daysLeft(d.deadline).tone !== "neutral").length} tone="text-warn" onClick={() => go("/review")} />
            <Tile label="Assessments due" value={data.assessments_due.length} tone="text-bad" onClick={() => go("/review")} />
          </div>

          {data.parked.length > 0 && (
            <Card title="Needs your attention">
              {data.parked.map((p) => (
                <Row key={p.application_id} title={`Application ${p.application_id}`}
                     sub={String(p.detail.reason ?? p.detail.outcome ?? "submission stopped")}
                     right={<Pill tone="bad">stopped</Pill>} onClick={() => go(`/review/${p.application_id}`)} />
              ))}
            </Card>
          )}

          <Card title="To review">
            {data.to_review.length === 0 ? <Empty>Nothing waiting for you. New drafts will appear here.</Empty> :
              data.to_review.map((a) => (
                <Row key={a.id} title={a.company} sub={a.role}
                     right={<Pill tone="warn">{a.drafts} {a.drafts === 1 ? "draft" : "drafts"}</Pill>}
                     onClick={() => go(`/review/${a.id}`)} />
              ))}
          </Card>

          <Card title="Deadlines">
            {data.deadlines.length === 0 ? <Empty>No open deadlines.</Empty> :
              data.deadlines.map((d) => {
                const left = daysLeft(d.deadline);
                return (
                  <Row key={d.application_id} title={d.company} sub={d.role}
                       right={<Pill tone={left.tone}>{left.label}</Pill>} onClick={() => go(`/review/${d.application_id}`)} />
                );
              })}
          </Card>

          <Card title="Assessments">
            {data.assessments_due.length === 0 ? <Empty>No assessments due this week.</Empty> :
              data.assessments_due.map((a) => (
                <Row key={a.id} title={a.company ?? "Assessment"} sub={a.kind}
                     right={a.deadline ? <Pill tone={daysLeft(a.deadline).tone}>{daysLeft(a.deadline).label}</Pill> : undefined} />
              ))}
          </Card>
        </>
      )}
    </div>
  );
}
