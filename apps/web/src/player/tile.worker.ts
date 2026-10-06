import { decodeTile } from './tileCodec';
import type { DecodeRequest, DecodeResponse } from './TileDecoder';

/** Tile decode worker (AD §11): gunzip + JSON parse off the main thread. */
const scope = self as unknown as {
  onmessage: ((e: MessageEvent<DecodeRequest>) => void) | null;
  postMessage: (msg: DecodeResponse) => void;
};

scope.onmessage = (e) => {
  const { id, buf } = e.data;
  decodeTile(buf).then(
    (tile) => scope.postMessage({ id, ok: true, tile }),
    (err: unknown) =>
      scope.postMessage({ id, ok: false, error: err instanceof Error ? err.message : String(err) }),
  );
};
