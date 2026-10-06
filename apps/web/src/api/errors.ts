import type { WsServerEventSchema } from './generated/schemas';

/** Server error codes (packages/schemas ErrorCode) plus client-side codes. */
export type ServerErrorCode = WsServerEventSchema.ErrorCode;
export type ClientErrorCode = 'NETWORK_ERROR' | 'UNKNOWN';
export type ApiErrorCode = ServerErrorCode | ClientErrorCode;

/** RFC 9457 problem details as returned by the API (`application/problem+json`). */
export interface Problem {
  type: string;
  title: string;
  status: number;
  code: ApiErrorCode;
  detail?: string;
  instance?: string;
  /** Extension members (e.g. `errors` for VALIDATION_FAILED) are kept verbatim. */
  [extension: string]: unknown;
}

// Record (not a list) so the compiler flags any ErrorCode added to packages/schemas but not here.
const SERVER_CODE_MAP: Record<ServerErrorCode, true> = {
  VALIDATION_FAILED: true,
  NOT_FOUND: true,
  UNAUTHENTICATED: true,
  FORBIDDEN: true,
  METHOD_NOT_ALLOWED: true,
  CONFLICT: true,
  IDEMPOTENCY_KEY_REQUIRED: true,
  IDEMPOTENCY_KEY_REUSED: true,
  QUOTA_EXCEEDED: true,
  PLAN_REQUIRED: true,
  RATE_LIMITED: true,
  INTERNAL: true,
  EMAIL_TAKEN: true,
  INVALID_CREDENTIALS: true,
  EMAIL_NOT_VERIFIED: true,
  TOKEN_EXPIRED: true,
  LAST_OWNER: true,
  INVITE_EMAIL_MISMATCH: true,
  REGION_MISMATCH: true,
  VIDEO_UNREADABLE: true,
  NO_WIDE_SHOTS: true,
  TEAM_SEPARATION_FAILED: true,
  HOMOGRAPHY_UNAVAILABLE: true,
  INGEST_FAILED: true,
  EXPORT_FAILED: true,
};
const SERVER_CODES: ReadonlySet<string> = new Set(Object.keys(SERVER_CODE_MAP));

/** Fallback code when a non-problem error body arrives (proxy HTML page, empty 502…). */
function codeForStatus(status: number): ApiErrorCode {
  if (status === 0) return 'NETWORK_ERROR';
  if (status === 400 || status === 422) return 'VALIDATION_FAILED';
  if (status === 401) return 'UNAUTHENTICATED';
  if (status === 403) return 'FORBIDDEN';
  if (status === 404) return 'NOT_FOUND';
  if (status === 409) return 'CONFLICT';
  if (status === 410) return 'TOKEN_EXPIRED'; // identity: expired / used email or invite tokens
  if (status === 429) return 'RATE_LIMITED';
  if (status >= 500) return 'INTERNAL';
  return 'UNKNOWN';
}

function isRecord(v: unknown): v is Record<string, unknown> {
  return typeof v === 'object' && v !== null && !Array.isArray(v);
}

/** Normalises any error body into a Problem; never throws. */
export function parseProblem(body: unknown, status: number, fallbackTitle = ''): Problem {
  const b = isRecord(body) ? body : {};
  const rawCode = typeof b.code === 'string' ? b.code : undefined;
  const code: ApiErrorCode =
    rawCode && SERVER_CODES.has(rawCode) ? (rawCode as ServerErrorCode) : codeForStatus(status);
  return {
    ...b,
    type: typeof b.type === 'string' ? b.type : 'about:blank',
    title: typeof b.title === 'string' ? b.title : fallbackTitle,
    status: typeof b.status === 'number' ? b.status : status,
    code,
    detail: typeof b.detail === 'string' ? b.detail : undefined,
    instance: typeof b.instance === 'string' ? b.instance : undefined,
  };
}

export class ApiError extends Error {
  override readonly name = 'ApiError';
  readonly problem: Problem;

  constructor(problem: Problem, options?: { cause?: unknown }) {
    super(problem.detail ?? (problem.title || problem.code), options);
    this.problem = problem;
  }

  get status(): number {
    return this.problem.status;
  }
  get code(): ApiErrorCode {
    return this.problem.code;
  }
  get type(): string {
    return this.problem.type;
  }
  get title(): string {
    return this.problem.title;
  }
  get detail(): string | undefined {
    return this.problem.detail;
  }
  get instance(): string | undefined {
    return this.problem.instance;
  }

  /** Worth retrying automatically (network, 408, 429, 5xx). 4xx business errors are not. */
  get retryable(): boolean {
    const s = this.status;
    return s === 0 || s === 408 || s === 429 || s >= 500;
  }

  static async fromResponse(response: Response): Promise<ApiError> {
    let body: unknown = null;
    const text = await response.text().catch(() => '');
    if (text) {
      try {
        body = JSON.parse(text);
      } catch {
        body = null; // non-JSON (e.g. proxy error page)
      }
    }
    const problem = parseProblem(body, response.status, response.statusText);
    // 429 RATE_LIMITED carries Retry-After (seconds); expose it as `retry_after` for countdowns.
    const retryAfter = Number(response.headers.get('Retry-After'));
    if (problem.retry_after === undefined && Number.isFinite(retryAfter) && retryAfter > 0) {
      problem.retry_after = retryAfter;
    }
    return new ApiError(problem);
  }

  static network(cause: unknown): ApiError {
    return new ApiError(parseProblem(null, 0), { cause });
  }
}

export function isApiError(e: unknown): e is ApiError {
  return e instanceof ApiError;
}
