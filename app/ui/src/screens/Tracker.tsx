import { useState } from "react";
import { get, type Application, type Assessment, type TrackerSummary } from "../api";
import { go } from "../router";
import { Card, Empty, Failure, Loading, Pill, Row, STATUS_LABEL, STATUS_TONE, daysLeft } from "../components/ui";
import { useData } from "../components/useData";

const GROUPS: Record<string, string[]> = {
  Active: ["draft", "ready-for-review", "approved"],
  Sent: ["submitted", "acknowledged"],
  "In process": ["assessment", "interview", "offer", "accepted"],
  Closed: ["rejected", "ghosted", "withdrawn"],
};

type Data = { summary: TrackerSummary; apps: Application[]; assessments: Assessment[] };

function count(summary: TrackerSummary, statuses: string[]): number {
  return statuses.reduce((n, s) => n + (summary.by_status[s] ?? 0), 0);
}

export default function Tracker() {
  const { data, error } = useData<Data>(async () => {
    const [summary, apps, assessments] = await Promise.all([
      get<TrackerSummary>("/tracker/summary"), get<Application[]>("/applications"), get<Assessment[]>("/assessments"),
    ]);
    return { summary, apps, assessments };
  }, "tracker");
  const [filter, setFilter] = useState<string>("Active");

  if (error) return <Failure error={error} />;
  if (!data) return <Loading />;
  const { summary, apps, assessments } = data;
  const shown = apps.filter((a) => GROUPS[filter].includes(a.status));
  const cap = summary.weekly_cap;

  return (
    <div className="space-y-4">
      <header className="pt-2">
        <h1 className="text-[26px] tracking-tight">Tracker</h1>
        <p className="text-[13px] text-muted">
          {cap.used} of {cap.cap ?? "?"} applications this week{summary.source_failures ? ` · ${summary.source_failures} job source failing` : ""}
        </p>
      </header>

      <div className="grid grid-cols-4 gap-2">
        {Object.entries(GROUPS).map(([name, statuses]) => (
          <button key={name} onClick={() => setFilter(name)} aria-pressed={filter === name}
                  className={`card flex min-h-[72px] flex-col justify-between p-2.5 text-left ${filter === name ? "border-accent" : ""}`}>
            <span className={`text-[24px] font-semibold leading-none ${filter === name ? "text-accent" : ""}`}>{count(summary, statuses)}</span>
            <span className="text-[11px] text-muted">{name}</span>
          </button>
        ))}
      </div>

      {assessments.some((a) => a.status === "invited" || a.status === "scheduled") && (
        <Card title="Assessments and interviews">
          {assessments.filter((a) => a.status === "invited" || a.status === "scheduled").map((a) => (
            <Row key={a.id} title={a.company ?? "Assessment"} sub={`${a.kind}${a.vendor ? ` · ${a.vendor}` : ""}`}
                 right={a.deadline ? <Pill tone={daysLeft(a.deadline).tone}>{daysLeft(a.deadline).label}</Pill> : undefined}
                 onClick={a.application_id ? () => go(`/review/${a.application_id}`) : undefined} />
          ))}
        </Card>
      )}

      <Card title={filter}>
        {shown.length === 0 ? <Empty>Nothing here.</Empty> : shown.map((a) => {
          const left = a.deadline && GROUPS.Active.includes(a.status) ? daysLeft(a.deadline) : null;
          return (
            <Row key={a.id} title={a.company} sub={a.role}
                 right={
                   <div className="flex flex-col items-end gap-1">
                     <Pill tone={STATUS_TONE[a.status] ?? "neutral"}>{STATUS_LABEL[a.status] ?? a.status}</Pill>
                     {left && <span className={`text-[11px] ${left.tone === "neutral" ? "text-muted" : "text-warn"}`}>{left.label}</span>}
                   </div>
                 }
                 onClick={a.folder ? () => go(`/review/${a.id}`) : undefined} />
          );
        })}
      </Card>
    </div>
  );
}
