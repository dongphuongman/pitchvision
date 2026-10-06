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
export type WsServerEvent = WsServerEventSchema.WsServerEvent;
export type JobProgressEvent = WsServerEventSchema.ProgressEvent;
export type JobCompletedEvent = WsServerEventSchema.CompletedEvent;
export type JobFailedEvent = WsServerEventSchema.FailedEvent;
