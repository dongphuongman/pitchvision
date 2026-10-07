#!/usr/bin/env bash
# Public smoke test of a PitchVision deployment: web, identity (JWKS), api, auth guards, object storage.
#   infra/staging/smoke.sh [base-url]     default: https://$PV_SITE from infra/staging/.env
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
envval() { grep -E "^$1=" "$here/.env" 2>/dev/null | tail -1 | cut -d= -f2- || true; }
base="${1:-}"
[ -n "$base" ] || { site="$(envval PV_SITE)"; [ -z "$site" ] || base="https://$site"; }
base="${base:?usage: smoke.sh https://<site>}"
tmp="$(mktemp)"; trap 'rm -f "$tmp"' EXIT
fail=0

check() {  # name, expected status (regex), url, [body grep pattern]
  local code
  code="$(curl -sS -m 15 -o "$tmp" -w '%{http_code}' "$3" || echo 000)"
  if [[ "$code" =~ ^($2)$ ]] && { [ -z "${4:-}" ] || grep -q -- "$4" "$tmp"; }; then
    printf 'ok    %-30s %s\n' "$1" "$code"
  else
    printf 'FAIL  %-30s got %s, want %s%s\n' "$1" "$code" "$2" "${4:+ with \"$4\"}"; fail=1
  fi
}

check "web /healthz"              200 "$base/healthz" "ok"
check "web index"                 200 "$base/" 'id="root"'
check "web /config.js"            200 "$base/config.js" "__PV_CONFIG__"
check "identity JWKS"             200 "$base/.well-known/jwks.json" '"keys"'
check "api /meta"                 200 "$base/api/v1/meta" "version"
check "api auth guard"            401 "$base/api/v1/videos"
check "identity auth guard"       401 "$base/api/v1/me"
endpoint="$(envval PV_STORAGE_PUBLIC_ENDPOINT)"
if [ -n "$endpoint" ]; then  # anonymous S3 request: an XML error, never a listing or a 5xx
  check "object storage (private)" "40[0-9]" "$endpoint/$(envval PV_STORAGE_BUCKET)" "<Error"
fi
exit $fail
