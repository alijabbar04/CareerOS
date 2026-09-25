import { get, type Notice, type SettingsView } from "../api";
import { go } from "../router";
import { Card, Empty, Failure, Loading, Pill, Row, type Tone } from "../components/ui";
import { useData } from "../components/useData";

const LEVELS = ["Observe only", "Draft and fill, you submit", "Submits after your approval", "Autonomous"];
const RULE_TONE: Record<string, Tone> = { auto: "ok", ask: "warn", never: "neutral" };
const nice = (key: string) => key.replace(/_/g, " ").replace(/^./, (c) => c.toUpperCase());

export default function Settings() {
  const { data, error } = useData(async () => {
    const [s, notices] = await Promise.all([get<SettingsView>("/settings"), get<Notice[]>("/notifications?limit=15")]);
    return { s, notices };
  }, "settings");
  if (error) return <Failure error={error} />;
  if (!data) return <Loading />;
  const { s, notices } = data;

  return (
    <div className="space-y-4">
      <button onClick={() => go("/")} className="min-h-[44px] pt-2 text-[15px] text-info">‹ Home</button>
      <h1 className="text-[26px] tracking-tight">Settings</h1>

      <Card title="Autonomy">
        <Row title={`Level ${s.level ?? "?"}`} sub={s.level != null ? LEVELS[s.level] : undefined} />
        {s.recall_window_minutes != null && <Row title="Cancel window" sub="Time to stop a submission after you approve it" right={<Pill>{s.recall_window_minutes} min</Pill>} />}
        <p className="pt-2 text-[12px] text-muted">Read-only here; the rules live in settings.yaml on the laptop.</p>
      </Card>

      <Card title="What CareerOS may do">
        {Object.entries(s.overrides).map(([key, rule]) => (
          <Row key={key} title={nice(key)} right={<Pill tone={RULE_TONE[rule] ?? "neutral"}>{rule}</Pill>} />
        ))}
      </Card>

      <Card title="Limits">
        {Object.entries(s.caps).map(([key, value]) => <Row key={key} title={nice(key)} right={<Pill>{value}</Pill>} />)}
      </Card>

      <Card title="Notifications">
        <Row title="Discord" sub="Alerts to your phone through the CareerOS server" right={<Pill tone={s.discord ? "ok" : "neutral"}>{s.discord ? "on" : "off"}</Pill>} />
        <Row title="Phone push" sub="Set up with phone access (next step)" right={<Pill>soon</Pill>} />
      </Card>

      <Card title="Recent alerts">
        {notices.length === 0 ? <Empty>No alerts yet.</Empty> : notices.map((n) => (
          <Row key={n.id} title={n.detail.title ?? "Notification"} sub={`${n.at.slice(0, 16).replace("T", " ")}${n.detail.body ? ` · ${n.detail.body}` : ""}`} />
        ))}
      </Card>

      <p className="pb-4 text-center text-[12px] text-muted">CareerOS companion {s.version}</p>
    </div>
  );
}
