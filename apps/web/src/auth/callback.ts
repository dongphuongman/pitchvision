/**
 * Identity Google OIDC redirect fragment (W2 clarification of docs/contracts/identity-api.md):
 * success `#status=ok&new_user=<bool>&return_to=<path>`, failure `#error=<ErrorCode>`.
 * Anything else is treated as a failure (`UNKNOWN`). `returnTo` is NOT sanitised here.
 */
export function parseCallbackFragment(hash: string): {
  error?: string;
  returnTo?: string;
  newUser: boolean;
} {
  const params = new URLSearchParams(hash.replace(/^#/, ''));
  const error = params.get('error') ?? (params.get('status') === 'ok' ? undefined : 'UNKNOWN');
  return {
    error: error ?? undefined,
    returnTo: params.get('return_to') ?? undefined,
    newUser: params.get('new_user') === 'true',
  };
}
