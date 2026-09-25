// Thin client for the companion API (app/api/openapi.yaml). Writes carry the header the API requires.
export type Application = {
  id: number; company: string; role: string; track: string | null; status: string; deadline: string | null;
  drafting_mode: string | null; drafts: number; finals: number; fact_requests: number; folder: string | null;
};
export type Verdicts = { verifier: string | null; style: string | null; red_team: string | null };
export type Draft = {
  stem: string; kind: string | null; question: string | null; round: number | null; words: number; text: string;
  verify: { hard_fails?: string[]; warnings?: string[]; metrics?: Record<string, number | null> };
  verdicts: Verdicts; approved: boolean;
};
export type ApplicationDetail = Application & {
  brief?: Record<string, unknown>; drafts: Draft[]; finals: Draft[]; fact_requests: string[];
};
export type Home = {
  to_review: Application[];
  deadlines: { application_id: number; company: string; role: string; deadline: string }[];
  assessments_due: { id: number; company: string | null; kind: string; deadline: string | null }[];
  parked: { application_id: number; at: string; detail: Record<string, unknown> }[];
  unread_notifications: number;
};

export type TrackerSummary = {
  by_status: Record<string, number>; deadlines_7d: number; assessments_due_7d: number;
  weekly_cap: { used: number; cap: number | null }; source_failures: number;
};
export type Assessment = {
  id: number; application_id: number | null; kind: string; vendor: string | null; deadline: string | null;
  deadline_confidence: string | null; status: string; company: string | null; role: string | null;
};
export type ShortlistRow = {
  rank: number; posting_id: number | null; company: string; title: string; score: number; location: string | null;
  closes_at: string | null; url: string | null; application_id: number | null;
};
export type Board = {
  drivers: Record<string, { go: { id: string; title: string }[] }>; waiting_on_ali: string[]; board_text: string;
};
export type Notice = {
  id: number; at: string;
  detail: { title?: string; body?: string; level?: string; discord_sent?: boolean; toast_sent?: boolean };
};
export type SettingsView = {
  level: number | null; overrides: Record<string, string>; recall_window_minutes: number | null;
  caps: Record<string, number>; discord: boolean; version: string;
};

export class ApiError extends Error {
  constructor(public status: number, public detail: string) {
    super(`${status}: ${detail}`);
  }
}

async function handle<T>(res: Response): Promise<T> {
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      detail = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail);
    } catch {
      /* not JSON */
    }
    throw new ApiError(res.status, detail);
  }
  return res.json() as Promise<T>;
}

export const get = <T,>(path: string) => fetch(path).then((r) => handle<T>(r));

export const post = <T,>(path: string, body?: unknown) =>
  fetch(path, {
    method: "POST",
    headers: { "X-CareerOS-Client": "companion", "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  }).then((r) => handle<T>(r));
