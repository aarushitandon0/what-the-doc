/* Optional browser error reporting. Off unless VITE_SENTRY_DSN is set at build time,
 * and the SDK is only downloaded when it is.
 *
 * Sent: an error's class name, where it was caught (a closed set), the route name
 * (a closed set) and code locations inside this app's own bundle. Never sent: the
 * error message, a file name, document text, a complaint, anything the API returned,
 * the page URL, cookies, the user agent or breadcrumbs. A message can quote any of
 * those, so it is not read rather than redacted. No tracing, replay, sessions or
 * feedback: none of those integrations is installed. */

export const SITES = new Set(['window.error', 'window.unhandledrejection',
  'react.uncaught', 'react.caught']);
export const ROUTES = new Set(['home', 'upload', 'workspace', 'clarify', 'review',
  'results', 'history', 'settings', 'guide', 'privacy', 'design']);
// The mechanism names @sentry/browser 11 gives its two window handlers. A wire test
// against the real transport caught an earlier map of the bare 'onerror' names, which
// silently dropped every uncaught error.
const MECHANISM_SITE = {
  'auto.browser.global_handlers.onerror': 'window.error',
  'auto.browser.global_handlers.onunhandledrejection': 'window.unhandledrejection',
};
const NAME = /^[A-Za-z_$][\w$]{0,63}$/;
const PATH = /^\/[\w./@-]{0,200}$/;
const HEX32 = /^[a-f0-9]{32}$/;
const MAX_FRAMES = 30;
const PENDING_LIMIT = 10;

let sdk = null;
let pending = [];

function routeName(hash) {
  const name = String(hash || '').replace(/^#/, '') || 'home';
  return ROUTES.has(name) ? name : 'other';
}

/* A frame survives only if it points into this page's own origin, and then only as a
 * path: the origin is a deployment detail and a query string can carry anything. */
function cleanFrame(frame, origin) {
  if (!frame || typeof frame !== 'object') return null;
  let path;
  try {
    const url = new URL(frame.filename);
    if (url.origin !== origin) return null;
    path = url.pathname;
  } catch {
    return null;
  }
  if (!PATH.test(path)) return null;
  const clean = {filename: path, in_app: true};
  if (typeof frame.function === 'string' && NAME.test(frame.function)) clean.function = frame.function;
  if (Number.isInteger(frame.lineno) && frame.lineno > 0) clean.lineno = frame.lineno;
  if (Number.isInteger(frame.colno) && frame.colno > 0) clean.colno = frame.colno;
  return clean;
}

/* The final export boundary. It rebuilds the event from a small schema instead of
 * trying to redact what the SDK collected, so a field nobody thought of cannot leak. */
export function sanitizeEvent(event, {origin = globalThis.location?.origin, hash = globalThis.location?.hash} = {}) {
  try {
    if (!event || event.type !== undefined) return null;
    const value = event.exception?.values?.[event.exception.values.length - 1];
    if (!value) return null;
    const site = SITES.has(event.tags?.['wtd.site']) ? event.tags['wtd.site']
      : MECHANISM_SITE[value.mechanism?.type];
    if (!site) return null;
    const type = typeof value.type === 'string' && NAME.test(value.type) ? value.type : 'Error';
    const frames = (value.stacktrace?.frames || []).slice(-MAX_FRAMES)
      .map((frame) => cleanFrame(frame, origin)).filter(Boolean);
    const exception = {type, value: site, mechanism: {type: 'wtd', handled: site === 'react.caught'}};
    if (frames.length) exception.stacktrace = {frames};
    const clean = {level: 'error', platform: 'javascript',
      tags: {'wtd.site': site, 'wtd.route': routeName(hash)},
      exception: {values: [exception]}, fingerprint: [site, type]};
    if (typeof event.event_id === 'string' && HEX32.test(event.event_id)) clean.event_id = event.event_id;
    if (typeof event.timestamp === 'number') clean.timestamp = event.timestamp;
    for (const key of ['release', 'environment']) {
      if (typeof event[key] === 'string') clean[key] = event[key];
    }
    return clean;
  } catch {
    return null;
  }
}

export function telemetryOptions(Sentry, dsn, extra = {}) {
  return {
    dsn,
    release: 'what-the-doc-frontend',
    environment: import.meta.env?.VITE_SENTRY_ENVIRONMENT || 'local',
    // Only the two window handlers. No breadcrumbs, HTTP context, browser session,
    // tracing, replay or feedback integration exists to collect anything.
    defaultIntegrations: false,
    integrations: [Sentry.globalHandlersIntegration(), Sentry.dedupeIntegration()],
    sendDefaultPii: false,
    attachStacktrace: false,
    maxBreadcrumbs: 0,
    beforeBreadcrumb: () => null,
    beforeSend: (event) => sanitizeEvent(event),
    // No tracesSampleRate and no tracing integration: tracing stays disabled, so
    // there are no spans to filter. The envelope test asserts only events are sent.
    sendClientReports: false,
    ...extra,
  };
}

/* Resolves to true only when a client was constructed. A missing or failing SDK is
 * never the application's problem. */
export async function initTelemetry({dsn = import.meta.env?.VITE_SENTRY_DSN, load = () => import('./sentry-sdk.js'), extra} = {}) {
  if (!dsn || sdk) return Boolean(sdk);
  try {
    const Sentry = await load();
    Sentry.init(telemetryOptions(Sentry, dsn, extra));
    sdk = Sentry;
    const queued = pending;
    pending = [];
    queued.forEach(([error, site]) => reportError(error, site));
    return true;
  } catch {
    sdk = null;
    return false;
  }
}

export function reportError(error, site) {
  if (!SITES.has(site)) return;
  try {
    if (sdk) sdk.captureException(error, {tags: {'wtd.site': site}});
    else if (import.meta.env?.VITE_SENTRY_DSN && pending.length < PENDING_LIMIT) pending.push([error, site]);
  } catch {
    // Reporting must never become a second failure.
  }
}

export function resetTelemetryForTests() {
  sdk = null;
  pending = [];
}
