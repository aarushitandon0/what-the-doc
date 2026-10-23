"""Optional, content-free Sentry transactions and error events for the local pipeline.

Only this module talks to the SDK. There are no automatic integrations, request
capture, logs, breadcrumbs, profiles or attachments. Error events are built here, by
`capture_error`, from an exception's class name, a closed-set code and this
project's own frame locations; the exception message is never read. Both final
hooks rebuild the payload from a small schema instead of trying to redact prose.
All SDK failures are isolated from the operation being observed.
"""
from contextlib import contextmanager
from contextvars import ContextVar
import hashlib
import os
from pathlib import Path
import re
import threading
import traceback

TRANSACTIONS = frozenset(('repair.execute', 'review.accept', 'review.reject',
                          'download.execute'))
STAGES = frozenset(('structure.inspect', 'diagnosis.execute', 'model.inference',
                    'diagnosis.validate', 'proposal.validate', 'proposal.prepare',
                    'candidate.create', 'render.original', 'render.candidate',
                    'preservation.check', 'review.integrity', 'download.integrity'))
# What kind of change a plan makes. A label on work already validated elsewhere; it
# grants nothing and is a closed set, so no free-form string can ride out on it.
OPERATION_KINDS = frozenset(('paragraph_property', 'explicit_break_removal',
                             'section_start_change', 'mixed', 'none'))
ERRORS = frozenset(('model_unavailable', 'model_timeout', 'invalid_response',
                    'invalid_selection', 'unsupported_formatting', 'invalid_operation',
                    'render_failure', 'candidate_integrity_failed', 'invalid_state',
                    'internal_error'))
OUTCOMES = frozenset(('queued', 'running', 'failed', 'awaiting_review', 'accepted',
                     'rejected', 'no_change', 'unsupported', 'needs_clarification',
                     'passed', 'skipped', 'completed'))
_HEX = re.compile(r'^[a-f0-9]{32}$')
_HASH = re.compile(r'^[a-f0-9]{64}$')
_MODEL = re.compile(r'^[a-zA-Z0-9][a-zA-Z0-9_.:-]{0,95}$')
# Where a failure was caught. A closed set, so an event cannot be named by content.
ERROR_SITES = frozenset(('job.execute', 'render.failure', 'request.unhandled'))
_ERROR_TAGS = ('wtd.error_code', 'wtd.job_id', 'wtd.mode', 'wtd.operation_kind',
               'wtd.source_revision')
_IDENTIFIER = re.compile(r'^[A-Za-z_][A-Za-z0-9_]{0,63}$')
_MAX_FRAMES = 30
_SOURCE_DIR = Path(__file__).resolve().parent
_current = ContextVar('wtd_telemetry_transaction', default=None)
_client = None
_attempted = False
_lock = threading.Lock()


def _safe_data(data):
    """Allow only typed application metadata, never model/user prose."""
    clean = {}
    for key, value in (data or {}).items():
        if key == 'wtd.job_id':
            valid = isinstance(value, str) and _HEX.fullmatch(value)
        elif key in ('wtd.prompt_sha256', 'wtd.source_revision'):
            valid = isinstance(value, str) and _HASH.fullmatch(value)
        elif key == 'wtd.operation_kind':
            valid = isinstance(value, str) and value in OPERATION_KINDS
        elif key == 'wtd.break_type':
            valid = isinstance(value, str) and value in ('page', 'column')
        elif key == 'wtd.mode':
            valid = isinstance(value, str) and value in ('live', 'fixture')
        elif key == 'wtd.outcome':
            valid = isinstance(value, str) and value in OUTCOMES
        elif key == 'wtd.error_code':
            valid = isinstance(value, str) and value in ERRORS
        elif key in ('gen_ai.request.model', 'gen_ai.response.model'):
            valid = isinstance(value, str) and _MODEL.fullmatch(value)
        elif key == 'gen_ai.operation.name':
            valid = value == 'chat'
        elif key == 'gen_ai.operation.type':
            valid = value == 'ai_client'
        elif key == 'gen_ai.provider.name':
            valid = value == 'ollama'
        elif key in ('gen_ai.usage.input_tokens', 'gen_ai.usage.output_tokens',
                     'gen_ai.usage.total_tokens'):
            valid = type(value) is int and value >= 0
        else:
            valid = False
        if valid:
            clean[key] = value
    return clean


def _clean_trace(trace):
    clean = {k: trace[k] for k in ('trace_id', 'span_id', 'parent_span_id')
             if k in trace and isinstance(trace[k], str)
             and re.fullmatch(r'[a-f0-9]{16}|[a-f0-9]{32}', trace[k])}
    if trace.get('op') in ('task', 'wtd.stage', 'gen_ai.chat'):
        clean['op'] = trace['op']
    if trace.get('status') in ('ok', 'internal_error', 'unknown_error'):
        clean['status'] = trace['status']
    clean['data'] = _safe_data(trace.get('data'))
    return clean


def _sanitize_transaction(event, hint):
    """Final export boundary: discard every field that isn't explicitly needed."""
    try:
        # Attachments are separate envelope items and do not live in event JSON.
        hint.pop('attachments', None)
        if event.get('type') != 'transaction' or event.get('transaction') not in TRANSACTIONS:
            return None
        result = {k: event[k] for k in ('event_id', 'start_timestamp', 'timestamp')
                  if k in event}
        result.update(type='transaction', platform='python',
                      transaction=event['transaction'],
                      contexts={'trace': _clean_trace(event.get('contexts', {}).get('trace', {}))})
        result['spans'] = []
        for span_event in event.get('spans', []):
            if span_event.get('description') not in STAGES:
                continue
            clean = _clean_trace(span_event)
            clean.update({k: span_event[k] for k in ('start_timestamp', 'timestamp')
                          if k in span_event})
            clean['description'] = span_event['description']
            result['spans'].append(clean)
        return result
    except Exception:
        return None


def _clean_frames(frames):
    clean = []
    for frame in (frames or [])[-_MAX_FRAMES:]:
        if not isinstance(frame, dict):
            continue
        module, function, lineno = frame.get('module'), frame.get('function'), frame.get('lineno')
        if (isinstance(module, str) and _IDENTIFIER.fullmatch(module)
                and isinstance(function, str) and _IDENTIFIER.fullmatch(function)
                and type(lineno) is int and lineno > 0):
            clean.append({'module': module, 'function': function, 'lineno': lineno,
                          'in_app': True})
    return clean


def _sanitize_error(event, hint):
    """Final export boundary for errors: only events capture_error built survive."""
    try:
        if hint:
            hint.pop('attachments', None)
        tags = event.get('tags') or {}
        site = tags.get('wtd.site')
        if event.get('type') not in (None, 'event') or site not in ERROR_SITES:
            return None
        clean_tags = {k: v for k, v in _safe_data(tags).items() if k in _ERROR_TAGS}
        clean_tags['wtd.site'] = site
        code = clean_tags.setdefault('wtd.error_code', 'internal_error')
        values = (event.get('exception') or {}).get('values') or [{}]
        value = values[0] if isinstance(values[0], dict) else {}
        kind = value.get('type')
        if not (isinstance(kind, str) and _IDENTIFIER.fullmatch(kind)):
            kind = 'Error'
        exception = {'type': kind, 'value': code,
                     'mechanism': {'type': 'wtd', 'handled': True}}
        frames = _clean_frames((value.get('stacktrace') or {}).get('frames'))
        if frames:
            exception['stacktrace'] = {'frames': frames}
        result = {}
        if isinstance(event.get('event_id'), str) and _HEX.fullmatch(event['event_id']):
            result['event_id'] = event['event_id']
        if 'timestamp' in event:
            result['timestamp'] = event['timestamp']
        result.update(level='error', platform='python', release='what-the-doc',
                      environment='local', tags=clean_tags,
                      exception={'values': [exception]},
                      fingerprint=[site, code, kind])
        return result
    except Exception:
        return None


def _own_frames(error):
    """Module, function and line of frames in this project's own top-level sources.

    Library frames, absolute paths, source lines and locals are left out: a path
    names the user's machine and a local can hold document text.
    """
    frames = []
    try:
        for frame in traceback.extract_tb(error.__traceback__):
            path = Path(frame.filename)
            if path.suffix == '.py' and path.parent.resolve() == _SOURCE_DIR:
                frames.append({'module': path.stem, 'function': frame.name,
                               'lineno': frame.lineno})
    except Exception:
        return []
    return _clean_frames(frames)


def capture_error(error, *, site, error_code=None, job_id=None, mode=None,
                  operation_kind=None):
    """Report one caught failure as a content-free error event. Never raises.

    Sent: the exception's class name, a closed-set code, where it was caught, the
    job's opaque id and this project's own frame locations. Not sent: the exception
    message, which can quote a path, the renderer's output or the model, and any of
    those can quote the document. Without a DSN this does nothing.
    """
    try:
        if site not in ERROR_SITES or not initialize():
            return False
        parent = _current.get()
        inherited = parent.data if parent is not None else {}
        tags = {'wtd.site': site}
        tags.update(_safe_data({
            'wtd.error_code': error_code if error_code in ERRORS else _error_code(error),
            'wtd.job_id': job_id or inherited.get('wtd.job_id'),
            'wtd.mode': mode or inherited.get('wtd.mode'),
            'wtd.operation_kind': operation_kind or inherited.get('wtd.operation_kind'),
            'wtd.source_revision': source_revision()}))
        event = {'level': 'error', 'tags': tags,
                 'exception': {'values': [{'type': type(error).__name__,
                                           'stacktrace': {'frames': _own_frames(error)}}]}}
        return _client.capture_event(event) is not None
    except Exception:
        return False


def initialize(*, transport=None):
    """Read SENTRY_DSN and WTD_SENTRY_TRACES_SAMPLE_RATE once; absent DSN is offline.

    ``transport`` is an explicit test seam. It is never selected from a request.
    The client is bound only inside our isolation scopes, not the global SDK scope.
    """
    global _client, _attempted
    with _lock:
        if _attempted:
            return _client is not None
        _attempted = True
        dsn = os.environ.get('SENTRY_DSN', '').strip()
        if not dsn:
            return False
        try:
            import sentry_sdk
            from sentry_sdk.envelope import Envelope
            from sentry_sdk.transport import Transport, make_transport

            class SafeTransport(Transport):
                """The final envelope boundary also refuses separate attachments."""
                def __init__(self, options):
                    super().__init__(options)
                    self.delegate = make_transport(dict(options, transport=transport))

                def capture_envelope(self, envelope):
                    try:
                        clean = Envelope(headers={k: envelope.headers[k]
                                         for k in ('event_id', 'sent_at') if k in envelope.headers})
                        for item in envelope.items:
                            if item.type == 'transaction':
                                event = _sanitize_transaction(item.get_transaction_event(), {})
                                if event is not None:
                                    clean.add_transaction(event)
                            elif item.type == 'event':
                                event = _sanitize_error(item.get_event(), {})
                                if event is not None:
                                    clean.add_event(event)
                        if clean.items and self.delegate is not None:
                            self.delegate.capture_envelope(clean)
                    except Exception:
                        pass

                def flush(self, timeout, callback=None):
                    try:
                        if self.delegate is not None:
                            self.delegate.flush(timeout, callback)
                    except Exception:
                        pass

                def kill(self):
                    try:
                        if self.delegate is not None:
                            self.delegate.kill()
                    except Exception:
                        pass

            rate = float(os.environ.get('WTD_SENTRY_TRACES_SAMPLE_RATE', '1.0'))
            if not 0 <= rate <= 1:
                return False
            _client = sentry_sdk.Client(
                dsn=dsn, transport=SafeTransport, traces_sample_rate=rate,
                default_integrations=False, auto_enabling_integrations=False,
                integrations=[], max_request_body_size='never',
                # Every SDK 2.72 category is explicit: partial dictionaries opt in
                # to more permissive defaults. Keep this paired with the SDK pin.
                data_collection={
                    'user_info': False, 'cookies': {'mode': 'off'},
                    'http_headers': {'request': {'mode': 'off'}}, 'http_bodies': [],
                    'url_query_params': {'mode': 'off'},
                    'graphql': {'document': False, 'variables': False},
                    'gen_ai': {'inputs': False, 'outputs': False},
                    'database_query_data': False, 'queues': False,
                    'stack_frame_variables': False, 'frame_context_lines': 0,
                },
                include_local_variables=False, include_source_context=False,
                attach_stacktrace=False, max_breadcrumbs=0,
                before_breadcrumb=lambda *_: None, before_send=_sanitize_error,
                before_send_transaction=_sanitize_transaction,
                enable_logs=False, auto_session_tracking=False,
                profiles_sample_rate=0, send_client_reports=False,
                trace_propagation_targets=[], server_name='', debug=False,
                stream_gen_ai_spans=False,
                release='what-the-doc', environment='local',
            )
            return True
        except Exception:
            _client = None
            return False


def source_revision():
    """Hash the actual implementation sources; never invent a schema version."""
    try:
        digest = hashlib.sha256()
        for name in ('observability.py', 'api.py', 'pipeline.py', 'gemma_adapter.py',
                     'diagnosis.py', 'workflow.py', 'engine.py', 'render.py'):
            digest.update(name.encode('ascii'))
            digest.update((Path(__file__).parent / name).read_bytes())
        return digest.hexdigest()
    except Exception:
        return None


class Trace:
    """Safe facade; callers cannot add arbitrary span attributes."""
    def __init__(self, raw=None, name=None, inherited=None):
        self.raw, self.name = raw, name
        self.data = dict(inherited or {})

    def set(self, *, outcome=None, error_code=None, model=None, mode=None,
            job_id=None, prompt_sha256=None, source_revision=None,
            input_tokens=None, output_tokens=None, response_model=None,
            operation_kind=None, break_type=None):
        proposed = {'wtd.outcome': outcome, 'wtd.error_code': error_code,
                    'wtd.operation_kind': operation_kind,
                    'wtd.break_type': break_type,
                    'gen_ai.request.model': model, 'wtd.mode': mode,
                    'gen_ai.response.model': response_model,
                    'wtd.job_id': job_id,
                    'wtd.prompt_sha256': prompt_sha256,
                    'wtd.source_revision': source_revision,
                    'gen_ai.usage.input_tokens': input_tokens,
                    'gen_ai.usage.output_tokens': output_tokens}
        if (type(input_tokens) is int and input_tokens >= 0
                and type(output_tokens) is int and output_tokens >= 0):
            proposed['gen_ai.usage.total_tokens'] = input_tokens + output_tokens
        self.data.update(_safe_data(proposed))
        try:
            if self.raw is not None:
                for key, value in self.data.items():
                    self.raw.set_data(key, value)
                self.raw.set_status('internal_error' if self.data.get('wtd.error_code') else 'ok')
        except Exception:
            pass


def _error_code(error):
    code = getattr(error, 'code', None)
    if code in ERRORS:
        return code
    if type(error).__name__ in ('RenderFailed', 'RendererUnavailable'):
        return 'render_failure'
    if isinstance(error, ValueError):
        return 'invalid_operation'
    return 'internal_error'


@contextmanager
def transaction(name, *, job_id=None, mode=None, model=None, operation_kind=None):
    """A finite transaction inside the actual worker or review/download request.

    Pipeline calls reuse the worker's repair transaction. Later human requests open
    separate transactions with the same opaque job ID, with no open human wait span.
    """
    existing = _current.get()
    if name == 'repair.execute' and existing is not None and existing.name == name:
        existing.set(job_id=job_id, mode=mode, model=model,
                     operation_kind=operation_kind)
        yield existing
        return
    trace, isolation, current_scope_cm, raw_cm = Trace(name=name), None, None, None
    try:
        if name in TRANSACTIONS and initialize():
            import sentry_sdk
            isolation = sentry_sdk.isolation_scope()
            scope = isolation.__enter__()
            scope.set_client(_client)
            # Current scope clients take precedence over the isolation client.
            # Bind both so an unrelated ambient SDK cannot receive our spans.
            current_scope_cm = sentry_sdk.new_scope()
            current_scope = current_scope_cm.__enter__()
            current_scope.set_client(_client)
            raw_cm = sentry_sdk.start_transaction(name=name, op='task')
            trace.raw = raw_cm.__enter__()
        trace.set(job_id=job_id, mode=mode, model=model, operation_kind=operation_kind,
                  source_revision=source_revision())
    except Exception:
        trace.raw = None
    token = _current.set(trace)
    try:
        yield trace
    except BaseException as error:
        trace.set(outcome='failed', error_code=_error_code(error))
        raise
    finally:
        _current.reset(token)
        if raw_cm is not None:
            try:
                # Never pass the application exception/traceback to the SDK.
                raw_cm.__exit__(None, None, None)
            except Exception:
                pass
        if isolation is not None:
            if current_scope_cm is not None:
                try:
                    current_scope_cm.__exit__(None, None, None)
                except Exception:
                    pass
            try:
                isolation.__exit__(None, None, None)
            except Exception:
                pass


@contextmanager
def span(name, **metadata):
    parent = _current.get()
    trace = Trace(name=name, inherited=parent.data if parent else None)
    raw_cm = None
    try:
        if name in STAGES and parent is not None and parent.raw is not None:
            import sentry_sdk
            raw_cm = sentry_sdk.start_span(name=name,
                                           op='gen_ai.chat' if name == 'model.inference' else 'wtd.stage')
            trace.raw = raw_cm.__enter__()
            if name == 'model.inference':
                trace.data.update({'gen_ai.operation.name': 'chat',
                                   'gen_ai.operation.type': 'ai_client', 'gen_ai.provider.name': 'ollama'})
        trace.set(**metadata)
    except Exception:
        trace.raw = None
    try:
        yield trace
    except BaseException as error:
        trace.set(outcome='failed', error_code=_error_code(error))
        raise
    else:
        if 'wtd.outcome' not in trace.data or trace.data['wtd.outcome'] in ('running', 'queued'):
            trace.set(outcome='completed')
    finally:
        if raw_cm is not None:
            try:
                raw_cm.__exit__(None, None, None)
            except Exception:
                pass


def flush(timeout=2):
    """Best-effort shutdown helper; it proves no hosted delivery or visibility."""
    try:
        if _client is not None:
            _client.flush(timeout=timeout)
    except Exception:
        pass
