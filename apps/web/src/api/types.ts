/**
 * Friendly names for generated contract types. Import from here, not from ./generated.
 *   - REST shapes: OpenAPI (apps/api/openapi.json) → components['schemas']
 *   - Browser artefacts + WS: packages/schemas JSON Schemas
 */
import type { components } from './generated/openapi';
import type {
  ManifestSchema,
  ShotTimelineSchema,
  TileSchema,
  WsClientOpSchema,
  WsServerEventSchema,
} from './generated/schemas';

export type Meta = components['schemas']['Meta'];

export type Manifest = ManifestSchema.Manifest;
export type TeamInfo = ManifestSchema.TeamInfo;
export type Tile = TileSchema.Tile;
export type TileFrame = TileSchema.TileFrame;
export type ShotTimeline = ShotTimelineSchema.ShotTimeline;
export type Shot = ShotTimelineSchema.Shot;
export type ShotType = ShotTimelineSchema.ShotType;
export type WsClientOp = WsClientOpSchema.ClientOp;
/** Every server → client frame: job events + socket control messages (discriminated on `type`). */
export type WsServerEvent = WsServerEventSchema.WsServerEvent;
export type JobProgressEvent = WsServerEventSchema.ProgressEvent;
export type JobCompletedEvent = WsServerEventSchema.CompletedEvent;
export type JobFailedEvent = WsServerEventSchema.FailedEvent;
export type JobCancelledEvent = WsServerEventSchema.CancelledEvent;
/** Events published on a `job:{id}` topic (what topic listeners receive). */
export type WsJobEvent = JobProgressEvent | JobCompletedEvent | JobFailedEvent | JobCancelledEvent;
/** The socket session's own frames: subscribed · unsubscribed · ping · pong · error. */
export type WsControlMessage = Exclude<WsServerEvent, WsJobEvent>;
