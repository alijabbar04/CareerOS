import type { ReactNode } from "react";

export function Card({ title, action, children }: { title?: string; action?: ReactNode; children: ReactNode }) {
  return (
    <section className="card px-4 py-3">
      {title && (
        <div className="mb-1 flex items-center justify-between">
          <h2 className="card-label text-[12px] font-semibold text-muted">{title}</h2>
          {action}
        </div>
      )}
      {children}
    </section>
  );
}

const TONES = {
  neutral: "bg-surface-2 text-muted",
  ok: "bg-accent/10 text-accent",
  warn: "bg-warn/10 text-warn",
  bad: "bg-bad/10 text-bad",
  info: "bg-info/10 text-info",
};
export type Tone = keyof typeof TONES;

export function Pill({ tone = "neutral", children }: { tone?: Tone; children: ReactNode }) {
  return (
    <span className={`inline-flex items-center whitespace-nowrap rounded-full px-2.5 py-0.5 text-[12px] font-medium ${TONES[tone]}`}>
      {children}
    </span>
  );
}

export function Row({ title, sub, right, onClick }: { title: ReactNode; sub?: ReactNode; right?: ReactNode; onClick?: () => void }) {
  const body = (
    <>
      <div className="min-w-0 flex-1">
        <div className="truncate font-semibold">{title}</div>
        {sub && <div className="truncate text-[13px] text-muted">{sub}</div>}
      </div>
      {right && <div className="shrink-0">{right}</div>}
    </>
  );
  const cls = "flex min-h-[56px] w-full items-center gap-3 border-t border-line py-2.5 text-left first:border-t-0";
  return onClick ? (
    <button className={`${cls} active:opacity-70`} onClick={onClick}>
      {body}
    </button>
  ) : (
    <div className={cls}>{body}</div>
  );
}

export function Empty({ children }: { children: ReactNode }) {
  return <p className="py-3 text-[15px] text-muted">{children}</p>;
}

export function Loading() {
  return (
    <div className="space-y-3">
      {[0, 1, 2].map((i) => (
        <div key={i} className="card h-24 animate-pulse" />
      ))}
    </div>
  );
}

export function Failure({ error }: { error: unknown }) {
  const offline = error instanceof TypeError;
  return (
    <Card>
      <p className="py-2 text-[15px]">{offline ? "Can't reach the laptop right now." : "Something went wrong loading this."}</p>
      <p className="pb-2 text-[13px] text-muted">
        {offline ? "Drafts you opened before are still readable." : String(error)}
      </p>
    </Card>
  );
}

/** Days until an ISO date as a short label and a tone. */
export function daysLeft(iso: string): { label: string; tone: Tone } {
  const today = new Date(new Date().toDateString()).getTime();
  const days = Math.ceil((new Date(iso.slice(0, 10)).getTime() - today) / 86_400_000);
  if (days < 0) return { label: "overdue", tone: "bad" };
  if (days === 0) return { label: "today", tone: "bad" };
  return { label: days === 1 ? "1 day" : `${days} days`, tone: days <= 7 ? "warn" : "neutral" };
}

export const STATUS_TONE: Record<string, Tone> = {
  draft: "neutral", "ready-for-review": "warn", approved: "info", submitted: "ok", acknowledged: "ok",
  assessment: "ok", interview: "ok", offer: "ok", accepted: "ok", rejected: "bad", ghosted: "neutral", withdrawn: "neutral",
};

export const STATUS_LABEL: Record<string, string> = {
  draft: "Drafting", "ready-for-review": "To review", approved: "Approved", submitted: "Submitted",
  acknowledged: "Received", assessment: "Assessment", interview: "Interview", offer: "Offer", accepted: "Accepted",
  rejected: "Rejected", ghosted: "No reply", withdrawn: "Withdrawn",
};
