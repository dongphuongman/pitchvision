// Runtime config. The web image overwrites this file at container start (infra/docker/web/30-pv-config.sh)
// from PV_WEB_SENTRY_DSN / PV_ENV / PV_IMAGE_TAG; in `vite dev` nothing is configured.
// eslint-disable-next-line no-undef
window.__PV_CONFIG__ = {};
