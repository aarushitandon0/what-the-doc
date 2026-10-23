import React from 'react';
import {render, screen} from '@testing-library/react';
import {afterEach, describe, expect, it, vi} from 'vitest';
import * as Sentry from '@sentry/browser';
import {ScreenFailed} from './components.jsx';
import {initTelemetry, reportError, resetTelemetryForTests, sanitizeEvent} from './telemetry.js';

const SENTINELS = ['PRIVATE_MESSAGE_SENTINEL', 'PRIVATE_FILENAME_SENTINEL', 'PRIVATE_DOCUMENT_SENTINEL',
  'PRIVATE_SCOPE_SENTINEL', 'PRIVATE_BREADCRUMB_SENTINEL', 'PRIVATE_QUERY_SENTINEL'];
const ORIGIN = 'https://app.example';

afterEach(async () => {
  await Sentry.close?.(0);
  resetTelemetryForTests();
});

describe('sanitizeEvent', () => {
  it('rebuilds an event from its schema and keeps no prose, path query or foreign frame', () => {
    const hostile = {
      event_id: 'a'.repeat(32), timestamp: 1, release: 'r', environment: 'local',
      message: 'PRIVATE_MESSAGE_SENTINEL',
      user: {email: 'PRIVATE_SCOPE_SENTINEL'}, request: {url: `${ORIGIN}/?q=PRIVATE_QUERY_SENTINEL`},
      breadcrumbs: [{message: 'PRIVATE_BREADCRUMB_SENTINEL'}], extra: {doc: 'PRIVATE_DOCUMENT_SENTINEL'},
      contexts: {state: {name: 'PRIVATE_FILENAME_SENTINEL.docx'}},
      tags: {'wtd.site': 'react.uncaught', other: 'PRIVATE_SCOPE_SENTINEL'},
      exception: {values: [{type: 'TypeError', value: 'PRIVATE_MESSAGE_SENTINEL',
        stacktrace: {frames: [
          {filename: `${ORIGIN}/assets/index-abc.js?v=PRIVATE_QUERY_SENTINEL`, function: 'describe', lineno: 4, colno: 9,
            vars: {text: 'PRIVATE_DOCUMENT_SENTINEL'}, context_line: 'PRIVATE_DOCUMENT_SENTINEL'},
          {filename: 'chrome-extension://x/PRIVATE_FILENAME_SENTINEL.js', function: 'f', lineno: 1},
          {filename: `${ORIGIN}/assets/a.js`, function: 'PRIVATE MESSAGE SENTINEL', lineno: 2},
        ]}}]},
    };
    const clean = sanitizeEvent(hostile, {origin: ORIGIN, hash: '#review'});
    const text = JSON.stringify(clean);
    for (const sentinel of SENTINELS) expect(text).not.toContain(sentinel);
    expect(text).not.toContain('PRIVATE');
    expect(Object.keys(clean).sort()).toEqual(['environment', 'event_id', 'exception', 'fingerprint',
      'level', 'platform', 'release', 'tags', 'timestamp']);
    expect(clean.tags).toEqual({'wtd.site': 'react.uncaught', 'wtd.route': 'review'});
    const [value] = clean.exception.values;
    expect(value.type).toBe('TypeError');
    expect(value.value).toBe('react.uncaught');
    expect(value.stacktrace.frames).toEqual([
      {filename: '/assets/index-abc.js', function: 'describe', lineno: 4, colno: 9, in_app: true},
      {filename: '/assets/a.js', lineno: 2, in_app: true},
    ]);
    expect(clean.fingerprint).toEqual(['react.uncaught', 'TypeError']);
  });

  it('drops events with no known site, transactions, and unknown routes become "other"', () => {
    expect(sanitizeEvent({message: 'PRIVATE_MESSAGE_SENTINEL'})).toBeNull();
    expect(sanitizeEvent({type: 'transaction', exception: {values: [{type: 'E'}]}, tags: {'wtd.site': 'window.error'}})).toBeNull();
    expect(sanitizeEvent({exception: {values: [{type: 'E', mechanism: {type: 'generic'}}]}})).toBeNull();
    const fromWindow = sanitizeEvent({exception: {values: [{type: 'Bad name!', mechanism: {type: 'auto.browser.global_handlers.onunhandledrejection'}}]}},
      {origin: ORIGIN, hash: '#PRIVATE_FILENAME_SENTINEL'});
    expect(fromWindow.tags).toEqual({'wtd.site': 'window.unhandledrejection', 'wtd.route': 'other'});
    expect(fromWindow.exception.values[0].type).toBe('Error');
  });
});

describe('initTelemetry', () => {
  it('does nothing, and loads nothing, without a DSN', async () => {
    const load = vi.fn();
    expect(await initTelemetry({dsn: '', load})).toBe(false);
    expect(load).not.toHaveBeenCalled();
    expect(() => reportError(new Error('x'), 'react.caught')).not.toThrow();
  });

  it('survives an SDK that fails to load', async () => {
    expect(await initTelemetry({dsn: 'https://public@example.invalid/1', load: () => Promise.reject(new Error('offline'))})).toBe(false);
  });

  it('sends only the sanitized event through a real SDK client and transport', async () => {
    const bodies = [];
    const transport = (options) => Sentry.createTransport(options, (request) => {
      bodies.push(typeof request.body === 'string' ? request.body : new TextDecoder().decode(request.body));
      return Promise.resolve({statusCode: 200});
    });
    expect(await initTelemetry({dsn: 'https://public@example.invalid/1', load: async () => Sentry, extra: {transport}})).toBe(true);
    // Everything an ambient caller could put on a scope, before the error.
    Sentry.setUser({email: 'PRIVATE_SCOPE_SENTINEL@example.invalid'});
    Sentry.setTag('document', 'PRIVATE_SCOPE_SENTINEL');
    Sentry.setExtra('complaint', 'PRIVATE_DOCUMENT_SENTINEL');
    Sentry.setContext('file', {name: 'PRIVATE_FILENAME_SENTINEL.docx'});
    Sentry.addBreadcrumb({message: 'PRIVATE_BREADCRUMB_SENTINEL'});
    window.history.replaceState(null, '', '/?q=PRIVATE_QUERY_SENTINEL#workspace');

    reportError(new TypeError('PRIVATE_MESSAGE_SENTINEL in PRIVATE_FILENAME_SENTINEL.docx'), 'react.uncaught');
    Sentry.captureMessage('PRIVATE_MESSAGE_SENTINEL');            // not built by us: dropped
    reportError(new Error('PRIVATE_MESSAGE_SENTINEL'), 'not.a.site'); // unknown site: never captured
    await Sentry.flush(2000);

    const text = bodies.join('\n');
    for (const sentinel of SENTINELS) expect(text).not.toContain(sentinel);
    // Envelope item headers: only error events, never spans, sessions, replays or reports.
    const itemTypes = text.split('\n').filter((line) => line.startsWith('{"type"'))
      .map((line) => JSON.parse(line).type);
    expect(new Set(itemTypes)).toEqual(new Set(['event']));
    const events = text.split('\n').filter((line) => line.startsWith('{"level"') || line.includes('"exception"'))
      .map((line) => JSON.parse(line)).filter((value) => value.exception);
    expect(events).toHaveLength(1);
    const [event] = events;
    expect(event.tags).toEqual({'wtd.site': 'react.uncaught', 'wtd.route': 'workspace'});
    expect(event.exception.values[0]).toMatchObject({type: 'TypeError', value: 'react.uncaught'});
    for (const absent of ['user', 'request', 'breadcrumbs', 'extra', 'contexts', 'message']) {
      expect(event).not.toHaveProperty(absent);
    }
    window.history.replaceState(null, '', '/');
  });
});

describe('window handlers', () => {
  it('an uncaught window error goes through the real SDK handler and arrives sanitized', async () => {
    // Synthetic events with guessed mechanism names passed while the real handler's
    // events were all dropped; this drives the handler the SDK actually installs.
    const bodies = [];
    const transport = (options) => Sentry.createTransport(options, (request) => {
      bodies.push(typeof request.body === 'string' ? request.body : new TextDecoder().decode(request.body));
      return Promise.resolve({statusCode: 200});
    });
    expect(await initTelemetry({dsn: 'https://public@example.invalid/1', load: async () => Sentry, extra: {transport}})).toBe(true);
    const error = new RangeError('PRIVATE_MESSAGE_SENTINEL');
    window.onerror?.('PRIVATE_MESSAGE_SENTINEL', 'https://app.example/x.js', 1, 1, error);
    await Sentry.flush(2000);
    const text = bodies.join('\n');
    expect(text).not.toContain('PRIVATE');
    const events = text.split('\n').filter((line) => line.includes('"exception"')).map((line) => JSON.parse(line));
    expect(events).toHaveLength(1);
    expect(events[0].tags['wtd.site']).toBe('window.error');
    expect(events[0].exception.values[0].type).toBe('RangeError');
  });
});

describe('ScreenFailed', () => {
  it('replaces a crashed screen with a plain statement instead of a blank page', () => {
    const Broken = () => { throw new TypeError("Cannot read properties of undefined (reading 'replace')"); };
    const quiet = vi.spyOn(console, 'error').mockImplementation(() => {});
    render(<ScreenFailed><Broken/></ScreenFailed>);
    expect(screen.getByRole('alert')).toHaveTextContent('This screen failed to draw.');
    expect(screen.getByRole('button', {name: 'Reload at My documents'})).toBeInTheDocument();
    quiet.mockRestore();
  });
});
