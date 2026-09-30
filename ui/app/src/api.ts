// The backend's JSON API. Types come from @beacon/shared, the contract both halves build against.
import type { ErrorResponse } from '@beacon/shared';

export class ApiError extends Error {
  constructor(message: string, readonly status: number) { super(message); }
}

export async function api<T>(path: string, init: { method?: string; body?: unknown; raw?: BodyInit } = {}): Promise<T> {
  const headers: Record<string, string> = {};
  let body: BodyInit | null = null;
  if (init.raw !== undefined) {
    body = init.raw;
    headers['Content-Type'] = 'application/octet-stream';
  } else if (init.body !== undefined) {
    body = JSON.stringify(init.body);
    headers['Content-Type'] = 'application/json';
  }
  const res = await fetch(path, { method: init.method ?? (body ? 'POST' : 'GET'), headers, body });
  if (res.status === 401 && !path.startsWith('/api/auth/')) {
    // The session cookie is missing or expired: a full reload is simplest and correct
    // here, since it also drops any in-memory app state built for the previous session.
    window.location.href = '/login.html';
    return new Promise<T>(() => { /* navigating away; never resolves */ });
  }
  const text = await res.text();
  let data: unknown = null;
  try { data = text ? JSON.parse(text) : null; } catch { data = { error: text }; }
  if (!res.ok) throw new ApiError((data as ErrorResponse | null)?.error || `${res.status} ${res.statusText}`, res.status);
  return data as T;
}

/** A URL for a file under the programme root, reloaded only when that file's mtime changes. */
export function fileUrl(path: string, version?: string | null, extra = ''): string {
  const q = [version ? `v=${encodeURIComponent(version)}` : '', extra].filter(Boolean).join('&');
  return `/files/${path}${q ? `?${q}` : ''}`;
}
