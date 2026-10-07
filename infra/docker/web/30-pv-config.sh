#!/bin/sh
# nginx image hook (/docker-entrypoint.d): render the SPA's runtime config from env (see apps/web/src/lib/monitoring.ts).
set -eu
json_escape() { printf '%s' "$1" | sed 's/\\/\\\\/g; s/"/\\"/g'; }
cat > /usr/share/nginx/html/config.js <<JS
window.__PV_CONFIG__ = {"sentryDsn":"$(json_escape "${PV_WEB_SENTRY_DSN:-}")","environment":"$(json_escape "${PV_ENV:-local}")","release":"$(json_escape "${PV_IMAGE_TAG:-dev}")"};
JS
