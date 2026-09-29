// Types shared by the server and the app. Envelopes are generated from `bcn schema`
// (envelopes.gen.d.ts); everything else here is the UI's own HTTP API, which is the
// contract between the two halves (spec §2.3).
import type {
  BcnCodesEnvelope, BcnDiagnosticsEnvelope, BcnDoctorEnvelope, BcnEditEnvelope, BcnIntakeEnvelope, BcnReviewEnvelope,
  BcnShowEnvelope, BcnStatusEnvelope, BcnSyncEnvelope, BcnTranslationEnvelope,
} from './envelopes.gen.js';

export * from './envelopes.gen.js';

// -- pieces of envelopes, named ---------------------------------------------------------------
// Where bcn's schema leaves an object open ({}), the shape is spelled out here from real output.
export type Lang = 'en' | 'zh';
export type Level = 'error' | 'warn' | 'info';
type GenDiagnostic = BcnStatusEnvelope['diagnostics'][number];
export type DiagnosticCode = GenDiagnostic['code'];
/** Per-code extras. Only the fields the UI reads are named. */
export interface DiagnosticData {
  fingerprint?: string;
  acknowledged?: { by?: string; at?: string; note?: string } | null;
  step?: string;
  document?: boolean;
  time?: number | null;
  // CUE_MISTRANSCRIPTION
  id?: string; slide?: number; cue?: number | null; script?: string; srt?: string;
  review?: { decision: 'accept' | 'correct'; text?: string; by?: string; at?: string } | null;
  [k: string]: unknown;
}
export type Diagnostic = Omit<GenDiagnostic, 'data'> & { data?: DiagnosticData };
export interface Blocker { code: string; message: string; file?: string | null }
export interface StepState { exists: boolean; ok: boolean | null; fresh: boolean | null; time: string | null; skipped: boolean | null }
type GenTopic = BcnStatusEnvelope['results'][number];
export type LangState = Omit<NonNullable<GenTopic['en']>, 'blockers' | 'steps'> & {
  stage: string; stage_index: number; stages: string[]; stale: boolean; stale_steps: string[]; blocked: boolean;
  blockers: Blocker[]; next: string | null; complete: boolean; diagnostics: Record<Level, number>;
  steps: Record<string, StepState>;
};
export interface StatusArtifact {
  key: string; path: string; lang: Lang | null; kind: string; exists: boolean; bytes: number | null;
  mtime: string | null; hydration: 'local' | 'cloud' | 'partial' | 'unknown'; stale: boolean | null; count?: number;
}
export type TopicStatus = Omit<GenTopic, 'en' | 'zh' | 'artifacts' | 'outcomes'> & {
  module: string; unit: string; code: string; path: string; title: string; minutes: number | null; outcomes: string[];
  has_dir: boolean; hydration: StatusArtifact['hydration']; unreviewed_mistranscriptions: number;
  en: LangState; zh: LangState; artifacts: StatusArtifact[];
};
export interface ModuleDocument { path: string; exists: boolean; errors: number; warnings: number }
export interface ModuleSummary {
  topics: number; units: string[]; en: Record<string, number>; zh: Record<string, number>; complete: Record<Lang, number>;
  blocked: number; stale: number; cloud: number; diagnostics: Record<Level, number>; unreviewed: number;
  course_map: boolean; documents: ModuleDocument[]; errors: number; title: string; unit_titles: Record<string, string>;
}
export interface StatusSummary {
  topics: number; complete: Record<Lang, number>; blocked: number; stale: number; cloud: number; cloud_share: number;
  unreviewed: number; diagnostics: Record<Level, number>; modules: Record<string, ModuleSummary>;
}
export type StatusEnvelope = Omit<BcnStatusEnvelope, 'results' | 'summary' | 'diagnostics'> & {
  results: TopicStatus[]; summary: StatusSummary; diagnostics: Diagnostic[];
};
export type DiagnosticsEnvelope = Omit<BcnDiagnosticsEnvelope, 'outstanding' | 'diagnostics' | 'counts'> & {
  outstanding: Diagnostic[]; diagnostics: Diagnostic[]; counts: Record<Level, number>;
};
export type CodeInfo = NonNullable<BcnCodesEnvelope['codes']>[number];
export type SyncPlanItem = NonNullable<BcnSyncEnvelope['plan']>[number];
/** Any envelope: what a job stores, whatever the command. */
export interface AnyEnvelope {
  tool: string; schema: number; target: string; ok: boolean; cancelled?: boolean; started: string; duration_ms: number;
  results: Array<{ topic: string; ok: boolean; skipped: boolean; [k: string]: unknown }>;
  artifacts: Array<{ path: string; kind: string; bytes: number; sha256: string }>;
  diagnostics: Diagnostic[];
  [k: string]: unknown;
}
/** What the backend adds to every envelope it passes on from a query. */
export type Queried<T> = T & { exit_code: number };

// -- bcn show: its schema leaves these open, so they are spelled out here from real output ------
export interface ShowSlide {
  index: number; title: string; line: number; content_html: string; narration: string; paragraphs: string[];
  words: number; running_words: number; words_ok: boolean; images: string[];
}
export interface ShowScript {
  front: { topic_id?: string; title?: string; minutes?: string | number; lang?: string; [k: string]: unknown };
  slides: ShowSlide[];
  words: number; target_words: number; range: [number, number]; words_ok: boolean;
  words_per_minute: number; narration_limits: [number, number];
}
export interface ShowCue {
  slide: number; time: number; confidence: number | null; source: 'auto' | 'manual' | string; reason?: string | null;
}
export interface RenderFlag { level: Level; message: string }
export interface ShowRenderLang { slides: string[]; flagged: Record<string, RenderFlag[]>; pdf: string | null }
export interface ShowRender {
  en?: ShowRenderLang; zh?: ShowRenderLang;
  theme: { name: string; width: number; height: number; safe_bottom: number } | null;
}
export interface ShowMedia {
  master: string | null;
  draft: Record<Lang, string | null>;
  draft_offset: Record<Lang, number>;
  subtitles: Record<Lang, string | null>;
}
export interface ShowResult {
  topic: string; ok: boolean; skipped: boolean;
  en: ShowScript | null; zh: ShowScript | null;
  cues: ShowCue[]; cue_threshold: number; render: ShowRender; media: ShowMedia;
  review: { cue_overrides: Record<string, unknown>; transcripts: Record<string, unknown> };
}
export type ShowEnvelope = Omit<BcnShowEnvelope, 'results'> & { results: ShowResult[] };

// -- the UI's own records ------------------------------------------------------------------------
export interface Prefs {
  operator: string;
  jobs: number;               // passed through to bcn --jobs
  parallel_jobs: number;      // queue jobs running at once (never two on the same topic)
  theme: string;              // empty: let bcn resolve it
  poll_seconds: number;
  module_columns: Record<Lang, boolean>;
  topic_layout: 'side' | 'stacked';
}

export type JobState = 'queued' | 'running' | 'stalled' | 'done' | 'failed' | 'cancelled' | 'interrupted';
export type JobArgs = Record<string, string | number | boolean | string[]>;

/** A progress event from bcn's NDJSON on stderr, as bcn wrote it. */
export interface ProgressEvent {
  event: 'progress'; topic?: string; pct?: number; topic_pct?: number | null; item?: number; items?: number;
  message?: string; elapsed_ms?: number; eta_ms?: number; heartbeat?: boolean; [k: string]: unknown;
}
export interface BcnEvent { event: string; level?: Level; message?: string; cancelled?: boolean; [k: string]: unknown }

export interface JobSummary {
  id: number; command: string; label: string; targets: string[]; args: JobArgs; state: JobState;
  created: string; started: string | null; finished: string | null; by: string;
  exit_code: number | null; duration_ms: number | null;
  progress: ProgressEvent | null; target_index: number; target_count: number;
  last_event_age_ms: number | null; ok: boolean | null;
}
export interface JobDetail extends JobSummary {
  log: string[];              // NDJSON lines as received, plus the UI's own notes
  envelopes: AnyEnvelope[];   // exactly as bcn printed them, one per target
}

// -- routes --------------------------------------------------------------------------------------
export interface BootResponse {
  root: string; prefs: Prefs; operator: string; warnings: string[];
  doctor: Queried<BcnDoctorEnvelope> | null; codes: CodeInfo[]; status_version: number;
}
export type StatusResponse = Queried<StatusEnvelope> & { version: number; jobs_running: number };
export interface TopicResponse { show: Queried<ShowEnvelope> & { _status_version?: number }; status: TopicStatus | null }
export type TopicVerifyResponse = Queried<StatusEnvelope>;
export interface TopicSourceResponse { exists: boolean; text: string; sha256: string | null; path: string }
export interface TopicCheckRequest { text: string; lang: Lang }
export type TopicCheckResponse = Queried<BcnEditEnvelope>;
export interface TopicSaveRequest { text: string; lang: Lang; expect_sha?: string | null; overwrite?: boolean }
export type DiagnosticsResponse = Queried<DiagnosticsEnvelope>;
export type ReviewResponse = Queried<BcnReviewEnvelope>;
export interface SyncResponse { pull: Queried<BcnSyncEnvelope>; push: Queried<BcnSyncEnvelope> }
export interface JobsResponse { jobs: JobSummary[] }
export interface CreateJobRequest { command: string; targets: string[]; args?: JobArgs }
export interface IntakeRequest { text: string; dry_run?: boolean; path?: string }
export interface TranslationItem { name: string; path: string; kind: 'zip' | 'folder'; bytes: number | null; mtime: string }
export interface TranslationListResponse { exports: TranslationItem[]; returned: TranslationItem[] }
export interface TranslationImportRequest { source: string; path?: string }
export interface ErrorResponse { error: string }

// Envelopes a job can carry, by command, for callers that know which one they ran.
export interface JobEnvelopes {
  intake: BcnIntakeEnvelope; translation: BcnTranslationEnvelope; edit: BcnEditEnvelope; sync: BcnSyncEnvelope;
}

// -- server-sent events on /api/events ----------------------------------------------------------
/** Sent once as `event: hello` when the stream opens. */
export interface HelloEvent { status_version: number }
export type ServerEvent =
  | { type: 'status'; version: number; reason: string; summary: StatusSummary | null | undefined }
  | { type: 'status-error'; error: string }
  | { type: 'job'; job: JobSummary }
  | { type: 'job-event'; job: number; target_index: number; target_count: number; event: BcnEvent }
  | { type: 'warnings'; warnings: string[] };
