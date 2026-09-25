import { useState } from "react";
import { get, type Board as BoardData } from "../api";
import { Card, Empty, Failure, Loading, Row } from "../components/ui";
import { useData } from "../components/useData";

const DRIVER_NAME: Record<string, string> = { "claude-fable": "Claude Fable", "claude-opus": "Claude Opus", "codex-sol": "Codex" };

/** "T-015 Gmail poller: Google consent deferred) — detail..." -> id, title, short reason */
function split(line: string): { id: string; title: string; reason: string } {
  const m = line.match(/^(T-\d+)\s+([^:]+):?\s*(.*)$/);
  if (!m) return { id: "", title: line.slice(0, 80), reason: "" };
  return { id: m[1], title: m[2].trim(), reason: m[3].split(/ — |\)\s—/)[0].replace(/\)$/, "").slice(0, 140) };
}

export default function Board() {
  const { data, error } = useData(() => get<BoardData>("/board"), "board");
  const [full, setFull] = useState(false);
  if (error) return <Failure error={error} />;
  if (!data) return <Loading />;
  return (
    <div className="space-y-4">
      <header className="pt-2">
        <h1 className="text-[26px] tracking-tight">Board</h1>
        <p className="text-[13px] text-muted">What the AIs will pick up next, and what only you can unblock.</p>
      </header>

      <Card title={`Waiting on you (${data.waiting_on_ali.length})`}>
        {data.waiting_on_ali.length === 0 ? <Empty>Nothing is waiting on you.</Empty> : data.waiting_on_ali.map((line) => {
          const t = split(line);
          return <Row key={line} title={`${t.id} ${t.title}`.trim()} sub={t.reason || undefined} />;
        })}
      </Card>

      {Object.entries(data.drivers).map(([driver, info]) => (
        <Card key={driver} title={DRIVER_NAME[driver] ?? driver}>
          {info.go.length === 0 ? <Empty>Nothing ready to take.</Empty> :
            info.go.slice(0, 5).map((t, i) => <Row key={t.id} title={`${t.id} ${t.title}`} sub={i === 0 ? "next" : undefined} />)}
        </Card>
      ))}

      <Card title="Full board" action={<button onClick={() => setFull((f) => !f)} className="min-h-[36px] text-[13px] text-info">{full ? "Hide" : "Show"}</button>}>
        {full && <pre className="overflow-x-auto whitespace-pre-wrap py-2 font-mono text-[12px] leading-relaxed text-muted">{data.board_text}</pre>}
      </Card>
    </div>
  );
}
