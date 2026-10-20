"""Strict schema for a model diagnosis, and the failure codes the pipeline reports.

A diagnosis *wraps* the plan the engine already understands rather than replacing it:
`source_sha256` and `operations` keep exactly the meaning `engine.validate` gives them,
and `plan_of` hands that pair straight to the existing validator and workflow. The new
keys around them carry what a user needs to see and what a reviewer needs to check -
a status, one plain-language explanation, the evidence the model says it used, and a
clarification question when it cannot proceed without one.

Four rules shape the validation below, and each exists because the opposite is a way to
look successful while doing nothing or the wrong thing:

  * An empty operations list is never `proposed`. Abstention is a valid, useful answer,
    but it is reported as `no_change`, `unsupported` or `needs_clarification` so that
    nothing downstream can describe "I changed nothing" as a completed repair.
  * Evidence must reference paragraph ids and fields that were actually supplied. A
    diagnosis citing a paragraph the model never saw is a fabrication, not a finding.
  * Operations may only touch the selected paragraph, and the hash must be copied
    exactly, so a stale or wandering proposal is refused before anything is written.
  * No confidence score is accepted at all. The strict key set rejects one, because a
    number the model chose for itself must never become an approval condition.

Everything here is deterministic and offline. Nothing in this module contacts a model,
reads a document or writes a file.
"""
import json
import math
import re

import engine
import styles as styles_module

STATUSES = ('proposed', 'needs_clarification', 'unsupported', 'no_change')
REQUIRED_KEYS = {'status', 'explanation', 'evidence', 'source_sha256', 'operations'}
OPTIONAL_KEYS = {'question'}
EVIDENCE_KEYS = {'paragraph_id', 'field'}
MAX_EXPLANATION = 400
MAX_QUESTION = 200
MAX_EVIDENCE = 8

# Fields a diagnosis may *cite*: everything the resolver reports, including the two
# (contextual_spacing, outline_level) that are context for a judgement but not editable.
CITABLE_FIELDS = frozenset(styles_module.RESOLVED_FIELDS)
# Fields a diagnosis may *change*: only what the deterministic patcher supports.
EDITABLE_FIELDS = frozenset(engine.FIELDS)

# Failure codes. The pipeline reports exactly one of these, so an operator can tell a
# model being absent from a model being wrong, and either from a renderer failing.
MODEL_UNAVAILABLE = 'model_unavailable'
MODEL_TIMEOUT = 'model_timeout'
INVALID_RESPONSE = 'invalid_response'
INVALID_SELECTION = 'invalid_selection'
UNSUPPORTED_FORMATTING = 'unsupported_formatting'
INVALID_OPERATION = 'invalid_operation'
RENDER_FAILURE = 'render_failure'

_CONTROL = re.compile(r'[\x00-\x08\x0b-\x1f\x7f]')


class DiagnosisError(ValueError):
    """A diagnosis that cannot be used, carrying a code the caller can branch on."""

    def __init__(self, code, message):
        super().__init__(message)
        self.code = code
        self.message = message


def _text(value, label, limit):
    """A bounded single-line string. Model prose is untrusted output, not markup."""
    if not isinstance(value, str):
        raise DiagnosisError(INVALID_RESPONSE, '%s must be a string' % label)
    cleaned = _CONTROL.sub(' ', value).strip()
    if not cleaned:
        raise DiagnosisError(INVALID_RESPONSE, '%s must not be empty' % label)
    if len(cleaned) > limit:
        raise DiagnosisError(INVALID_RESPONSE,
                             '%s exceeds %d characters' % (label, limit))
    return cleaned


def _evidence(entries, supplied_ids):
    if not isinstance(entries, list) or len(entries) > MAX_EVIDENCE:
        raise DiagnosisError(INVALID_RESPONSE,
                             'evidence must be a list of at most %d entries' % MAX_EVIDENCE)
    cleaned, seen = [], set()
    for entry in entries:
        if not isinstance(entry, dict) or set(entry) != EVIDENCE_KEYS:
            raise DiagnosisError(INVALID_RESPONSE,
                                 'each evidence entry needs exactly paragraph_id and field')
        paragraph_id, field = entry['paragraph_id'], entry['field']
        # bool is an int subclass; True must not pass as paragraph 1.
        if type(paragraph_id) is not int or paragraph_id not in supplied_ids:
            raise DiagnosisError(INVALID_RESPONSE,
                                 'evidence cites paragraph %r, which was not supplied'
                                 % (paragraph_id,))
        if field not in CITABLE_FIELDS:
            raise DiagnosisError(INVALID_RESPONSE,
                                 'evidence cites unknown property %r' % (field,))
        if (paragraph_id, field) in seen:
            raise DiagnosisError(INVALID_RESPONSE, 'duplicate evidence entry')
        seen.add((paragraph_id, field))
        cleaned.append({'paragraph_id': paragraph_id, 'field': field})
    return cleaned


def _operations(operations, selected_id):
    """Shape-check operations before engine.validate sees them.

    Only the structure and the scope are checked here. Value bounds, the field
    allowlist and the alternative-measurement refusal stay in engine.validate, which
    is the single place those rules live.
    """
    if not isinstance(operations, list):
        raise DiagnosisError(INVALID_RESPONSE, 'operations must be a list')
    for op in operations:
        if not isinstance(op, dict) or set(op) != {'paragraph_id', 'field', 'value'}:
            raise DiagnosisError(INVALID_RESPONSE,
                                 'each operation needs exactly paragraph_id, field and value')
        if type(op['paragraph_id']) is not int or op['paragraph_id'] != selected_id:
            raise DiagnosisError(INVALID_RESPONSE,
                                 'operations may only target the selected paragraph %d'
                                 % selected_id)
        if op['field'] not in EDITABLE_FIELDS:
            raise DiagnosisError(INVALID_RESPONSE,
                                 'operation targets unsupported field %r' % (op['field'],))
        value = op['value']
        if isinstance(value, float) and not math.isfinite(value):
            raise DiagnosisError(INVALID_RESPONSE, 'operation value must be finite')
    return operations


def check(raw, selected_id, source_sha256, supplied_ids):
    """Validate a model response. Returns a normalised diagnosis or raises DiagnosisError.

    `supplied_ids` is the set of paragraph ids the model was actually shown, so a
    citation of anything else is rejected as invented rather than merely out of range.
    """
    if not isinstance(raw, dict):
        raise DiagnosisError(INVALID_RESPONSE, 'expected a JSON object')
    keys = set(raw)
    missing = REQUIRED_KEYS - keys
    if missing:
        raise DiagnosisError(INVALID_RESPONSE,
                             'missing required key(s): ' + ', '.join(sorted(missing)))
    extra = keys - REQUIRED_KEYS - OPTIONAL_KEYS
    if extra:
        # Catches a self-assigned confidence score as well as any invented field.
        raise DiagnosisError(INVALID_RESPONSE,
                             'unexpected key(s): ' + ', '.join(sorted(extra)))

    status = raw['status']
    if status not in STATUSES:
        raise DiagnosisError(INVALID_RESPONSE,
                             'status must be one of ' + ', '.join(STATUSES))
    if raw['source_sha256'] != source_sha256:
        raise DiagnosisError(INVALID_RESPONSE,
                             'source_sha256 does not match the inspected document')

    explanation = _text(raw['explanation'], 'explanation', MAX_EXPLANATION)
    evidence = _evidence(raw['evidence'], supplied_ids)
    operations = _operations(raw['operations'], selected_id)

    if status == 'proposed':
        if not operations:
            # The rule this whole module exists for.
            raise DiagnosisError(INVALID_RESPONSE,
                                 'status "proposed" with no operations: an empty plan is '
                                 'not a repair. Use no_change, unsupported or '
                                 'needs_clarification to abstain.')
        if not evidence:
            raise DiagnosisError(INVALID_RESPONSE,
                                 'status "proposed" must cite the evidence it relies on')
    elif operations:
        raise DiagnosisError(INVALID_RESPONSE,
                             'status %r must carry no operations' % status)

    question = raw.get('question')
    if status == 'needs_clarification':
        # Abstaining for clarification without asking anything would leave the user
        # with nothing to do, so the question is required here.
        question = _text(question, 'question', MAX_QUESTION)
    elif question is None or isinstance(question, str):
        # Small models routinely fill every key in the template, and a stray question
        # beside a correct proposal is cosmetic, not unsafe. It is *dropped* rather
        # than rejected, so the user-visible rule still holds exactly - a question is
        # only ever surfaced with needs_clarification - without discarding a sound
        # diagnosis over a redundant string. The checks that bear on safety (status,
        # operations, scope, hash, evidence, no confidence score) are unchanged.
        question = None
    else:
        raise DiagnosisError(INVALID_RESPONSE, 'question must be a string or null')

    return {'status': status, 'explanation': explanation, 'evidence': evidence,
            'source_sha256': source_sha256, 'operations': operations,
            'question': question}


def plan_of(diagnosis):
    """The exact pair engine.validate, engine.apply and workflow.repair already take."""
    return {'source_sha256': diagnosis['source_sha256'],
            'operations': diagnosis['operations']}


def parse(text, selected_id, source_sha256, supplied_ids):
    """Decode a model's raw text as JSON, then validate it."""
    if not isinstance(text, str) or not text.strip():
        raise DiagnosisError(INVALID_RESPONSE, 'model returned no content')
    try:
        raw = json.loads(text)
    except ValueError as error:
        raise DiagnosisError(INVALID_RESPONSE, 'model returned invalid JSON: %s' % error)
    return check(raw, selected_id, source_sha256, supplied_ids)
