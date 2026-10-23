/* Only what telemetry.js uses, so the lazily loaded chunk can be tree-shaken. */
export {init, captureException, globalHandlersIntegration, dedupeIntegration} from '@sentry/browser';
