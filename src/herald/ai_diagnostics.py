"""Small allowlisted AI diagnostics; never serialize provider text or exceptions."""

import math

from pydantic import ValidationError


MAX_DIAGNOSTICS = 240
OPERATIONS = {'extract', 'extract_batch', 'merge_campaign', 'route_campaigns',
              'resolve_campaign', 'pipeline'}
STAGES = {'request', 'http_status', 'response_json', 'response_shape', 'completion',
          'schema', 'validated', 'reconciliation', 'budget', 'cooldown'}
CATEGORIES = {'transient', 'validation', 'rate_limit', 'quota', 'authentication', 'deferred'}
EXCEPTIONS = {'ReadTimeout', 'ConnectTimeout', 'WriteTimeout', 'PoolTimeout',
              'ConnectError', 'ReadError', 'WriteError', 'CloseError', 'ProxyError',
              'RemoteProtocolError', 'LocalProtocolError', 'UnsupportedProtocol',
              'DecodingError', 'HTTPStatusError', 'HTTPError', 'TimeoutError',
              'JSONDecodeError', 'ValidationError', 'ValueError', 'TypeError',
              'KeyError', 'IndexError', 'AttributeError', 'AIProviderError'}
FINISH_REASONS = {'stop', 'length', 'content_filter', 'tool_calls', 'function_call'}
FIELDS = {'relevant', 'source_summaries', 'campaign_title', 'partner', 'activities',
          'claims', 'uncertainties', 'candidate_campaign_id', 'groups',
          'ignored_observation_ids', 'unconfirmed', 'extraction', 'merge', 'exclusions',
          'decision', 'updates', 'new_activities', 'new_actions', 'new_venues'}
VALIDATION_TYPES = {'missing', 'extra_forbidden', 'enum', 'literal_error', 'json_invalid',
    'string_type', 'string_too_short', 'string_too_long', 'bool_parsing', 'bool_type',
    'list_type', 'dict_type', 'model_type', 'int_parsing', 'int_type', 'float_parsing',
    'float_type', 'finite_number', 'greater_than_equal', 'less_than_equal',
    'date_parsing', 'date_from_datetime_parsing', 'datetime_parsing',
    'datetime_from_date_parsing', 'timezone_aware', 'url_parsing', 'value_error'}
REASONS = {
    'date requires field evidence', 'invalid scope exclusion evidence',
    'scope exclusion must refer to legacy activity', 'older conflicting fact without provenance',
    'unverified extraction quote', 'context budget exceeded', 'conflicting contextual partner',
    'new activity already exists', 'new activity must identify one extracted activity',
    'same-time conflicting evidence requires review', 'invalid addition target or evidence',
    'existing campaign requires reconciliation', 'invalid fact target', 'field is not editable',
    'fact target must identify one existing entity', 'null is not a deletion instruction',
    'invalid campaign reference', 'invalid route coverage', 'invalid campaign candidate',
    'invalid batch source coverage', 'source text exceeds extraction budget',
    'new campaign cannot update existing entities', 'missing contextual merge',
    'new campaign decision cannot contain updates', 'invalid merged time interval',
    'invalid extracted time interval', 'duplicate field operation',
    'fact must cite supplied original text', 'only explicit official status is editable',
    'end date precedes start date', 'end precedes start',
}
CODES = {'ok', 'transport_error', 'http_error', 'invalid_response_json',
         'invalid_response_shape', 'incomplete_response', 'invalid_json',
         'schema_validation', 'validation_failed', 'provider_failure',
         'call_budget_exhausted', 'time_budget_exhausted', 'provider_cooldown',
         *(reason.replace(' ', '_') for reason in REASONS)}


def _known(value, allowed, default='other'):
    return value if isinstance(value, str) and value in allowed else default


def safe_number(value, maximum):
    if type(value) in (int, float) and 0 <= value <= maximum and math.isfinite(value):
        return value
    return None


def safe_diagnostic(value):
    """Copy only fixed enums and bounded numbers, including hostile API metadata."""
    value = value if isinstance(value, dict) else {}
    entry = {key: _known(value.get(key), allowed) for key, allowed in (
        ('operation', OPERATIONS), ('stage', STAGES), ('code', CODES))}
    for key, allowed in (('exception_type', EXCEPTIONS), ('category', CATEGORIES)):
        if key in value:
            entry[key] = _known(value[key], allowed)
    entry['finish_reason'] = (None if value.get('finish_reason') is None else
                              _known(value['finish_reason'], FINISH_REASONS))
    for key, maximum in (('attempt', 1000), ('elapsed_seconds', 86400), ('http_status', 599)):
        if key in value:
            entry[key] = safe_number(value[key], maximum)
    usage = value.get('usage')
    if isinstance(usage, dict):
        entry['usage'] = {key: number for key in ('prompt_tokens', 'completion_tokens', 'total_tokens')
                         if type(number := usage.get(key)) is int
                         and safe_number(number, 10**9) is not None}
    issues = value.get('validation_errors')
    if isinstance(issues, list):
        entry['validation_errors'] = [{'type': _known(issue.get('type'), VALIDATION_TYPES),
            'field': _known(issue.get('field'), FIELDS)} for issue in issues[:8] if isinstance(issue, dict)]
    return entry


def safe_diagnostics(values):
    return [safe_diagnostic(value) for value in values[-MAX_DIAGNOSTICS:]] if isinstance(values, list) else []


def validation_issues(error, result_type=None):
    """Only top-level schema fields; extra keys, input values and ctx are private."""
    fields = set(result_type.model_fields) if result_type else set()
    return [{'type': issue['type'],
             'field': issue['loc'][0] if issue['loc'] and issue['loc'][0] in fields else 'other'}
            for issue in error.errors(include_input=False, include_context=False, include_url=False)[:8]]


def failure_diagnostics(error, *, operation='pipeline', stage='reconciliation'):
    if entries := safe_diagnostics(getattr(error, 'diagnostics', None)):
        return entries
    reason = str(error)
    code = ('provider_failure' if getattr(error, 'category', 'validation') == 'transient'
            else 'validation_failed')
    entry = {'operation': operation, 'stage': stage, 'exception_type': type(error).__name__,
             'category': getattr(error, 'category', 'validation'),
             'code': reason.replace(' ', '_') if reason in REASONS else code}
    if isinstance(error, ValidationError):
        entry['validation_errors'] = validation_issues(error)
    return [safe_diagnostic(entry)]
