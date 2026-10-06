import { api } from './client';
import type { components } from './generated/openapi';

export type UploadCreated = components['schemas']['UploadCreated'];
export type UploadState = components['schemas']['UploadState'];
export type PartUrl = components['schemas']['PartUrl'];
export type CompletedPart = components['schemas']['CompletedPart'];
export type UrlPreview = components['schemas']['UrlPreview'];
type Video = components['schemas']['VideoOut'];
type Job = components['schemas']['JobOut'];

/** Containers the API accepts (apps/api services/uploads.py CONTAINERS); the extension decides. */
export const VIDEO_EXTENSIONS = [
  'mp4',
  'm4v',
  'mov',
  'mkv',
  'webm',
  'avi',
  'ts',
  'mts',
  'm2ts',
] as const;
/** API default `max_upload_bytes` (10 GiB). */
export const MAX_UPLOAD_BYTES = 10 * 1024 ** 3;
/** Same ceiling as YouTube ingest (> 3 h is rejected server-side). */
export const MAX_DURATION_S = 3 * 3600;

function required<T>(data: T | undefined, what: string): T {
  if (data === undefined) throw new Error(`Empty ${what} response`);
  return data;
}

/** `POST /videos/uploads` → video row + first batch of presigned part URLs (fixed 16 MiB parts). */
export async function createUpload(body: {
  filename: string;
  size: number;
  mime: string;
  title?: string | null;
}): Promise<UploadCreated> {
  const { data } = await api.POST('/api/v1/videos/uploads', { body });
  return required(data, 'upload');
}

/** More presigned URLs (≤ 100 per call). */
export async function signParts(videoId: string, partNumbers: number[]): Promise<PartUrl[]> {
  const { data } = await api.POST('/api/v1/videos/{video_id}/uploads/sign', {
    params: { path: { video_id: videoId } },
    body: { part_numbers: partNumbers },
  });
  return required(data, 'sign').parts;
}

/** Parts the object store already holds — resume uploads only the rest. */
export async function getUploadState(videoId: string): Promise<UploadState> {
  const { data } = await api.GET('/api/v1/videos/{video_id}/uploads', {
    params: { path: { video_id: videoId } },
  });
  return required(data, 'upload state');
}

export async function completeUpload(videoId: string, parts: CompletedPart[]): Promise<Video> {
  const { data } = await api.POST('/api/v1/videos/{video_id}/uploads/complete', {
    params: { path: { video_id: videoId } },
    body: { parts },
  });
  return required(data, 'complete');
}

/** E14 Cancel upload: abort the multipart upload and drop the video. */
export async function abortUpload(videoId: string): Promise<void> {
  await api.DELETE('/api/v1/videos/{video_id}/uploads', {
    params: { path: { video_id: videoId } },
  });
}

/** E17 Fetch: metadata via the ingest worker (≤ 20 s); rejections come back as problem+json. */
export async function previewYoutube(url: string, signal?: AbortSignal): Promise<UrlPreview> {
  const { data } = await api.POST('/api/v1/videos/from-url/preview', { body: { url }, signal });
  return required(data, 'preview');
}

/** E22: create the video (status `ingesting`); the download runs on the ingest worker. */
export async function createFromUrl(url: string, title: string | null): Promise<Video> {
  const { data } = await api.POST('/api/v1/videos/from-url', {
    body: { url, title, rights_confirmed: true },
  });
  return required(data, 'from-url');
}

/** `POST /jobs` (Idempotency-Key required, AD §10.1). A YouTube video still ingesting is accepted. */
export async function createJob(
  videoId: string,
  idempotencyKey: string = crypto.randomUUID(),
): Promise<Job> {
  const { data } = await api.POST('/api/v1/jobs', {
    body: { video_id: videoId },
    headers: { 'Idempotency-Key': idempotencyKey },
  });
  return required(data, 'job');
}
