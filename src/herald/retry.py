"""Bounded AI work and public, credential-free retry metadata."""

import asyncio
from datetime import datetime, timedelta
from time import monotonic

from .ai import AIProviderError
from .ai_diagnostics import failure_diagnostics, safe_diagnostics, MAX_DIAGNOSTICS


def retry_delay(error):
    explicit = getattr(error, 'retry_after_seconds', None)
    category = getattr(error, 'category', 'validation')
    if category in ('quota', 'authentication'):
        return max(explicit or 0, 86400)
    if explicit is not None:
        return explicit
    return 300 if category == 'rate_limit' else 0


def retry_is_due(pending, now):
    """Old ordinary 3/6/12/24h timers no longer block scheduled runs."""
    cooldown = pending.error_category in ('rate_limit', 'quota', 'authentication') or pending.retry_after_explicit
    return not (cooldown and pending.next_retry_at and pending.next_retry_at > now)


def record_pipeline_error(provider, error, *, operation='pipeline'):
    entries = failure_diagnostics(error, operation=operation)
    if not getattr(error, 'diagnostics', None):
        recorder = getattr(provider, 'record_diagnostics', None)
        if callable(recorder):
            recorder(entries)
        elif isinstance(getattr(provider, 'diagnostics', None), list):
            provider.diagnostics.extend(entries)
    return entries


def record_failure(store, pending, now, error, *, diagnostics=None):
    category = getattr(error, 'category', 'validation')
    if category == 'deferred':
        return
    attempts = pending.attempts + 1
    delay = retry_delay(error)
    store.save_pending_extraction(pending.model_copy(update={
        'attempts': attempts, 'next_retry_at': now + timedelta(seconds=delay) if delay else None,
        'retry_after_explicit': getattr(error, 'retry_after_seconds', None) is not None,
        'error_category': category, 'reason': 'AI processing failed; retry scheduled',
        'diagnostics': safe_diagnostics(diagnostics if diagnostics is not None else failure_diagnostics(error))[-8:],
    }))


class BudgetedProvider:
    """Preserve provider capabilities while bounding all extract/merge calls.

    The deadline starts at runner entry, so fetching consumes the same budget.
    A provider-wide cooldown stops later packets hammering an exhausted account.
    """

    def __init__(self, provider, store, now, *, max_calls=40, seconds=720, started=None):
        self.provider, self.store, self.now = provider, store, now
        self.remaining = max_calls
        self.deadline = (monotonic() if started is None else started) + seconds
        self.path = store.root / 'ai-retry.json'
        self.diagnostics = []
        self.record_diagnostics([])

    def record_diagnostics(self, entries):
        self.diagnostics.extend(safe_diagnostics(entries))
        del self.diagnostics[:-MAX_DIAGNOSTICS]
        self.store._atomic_json_write(self.store.root / 'ai-diagnostics.json', {
            'run_at': self.now.isoformat(), 'entries': self.diagnostics,
        })

    def __getattr__(self, name):
        method = getattr(self.provider, name)
        if name not in ('extract', 'extract_batch', 'merge_campaign', 'route_campaigns', 'resolve_campaign'):
            return method

        async def call(*args, **kwargs):
            import json
            state = json.loads(self.path.read_text(encoding='utf-8')) if self.path.exists() else {}
            until = state.get('next_retry_at')
            available = self.deadline - monotonic()
            offset = len(getattr(self.provider, 'diagnostics', []))
            try:
                code = ('call_budget_exhausted' if self.remaining <= 0 else
                        'time_budget_exhausted' if available <= 0 else
                        'provider_cooldown' if until and datetime.fromisoformat(until) > self.now else None)
                if code:
                    raise AIProviderError('AI work deferred', category='deferred', diagnostics=[{
                        'operation': name, 'stage': 'cooldown' if code == 'provider_cooldown' else 'budget',
                        'code': code, 'category': 'deferred'}])
                self.remaining -= 1
                result = await asyncio.wait_for(method(*args, **kwargs), timeout=available)
            except TimeoutError:
                entries = safe_diagnostics(getattr(self.provider, 'diagnostics', [])[offset:])
                error = AIProviderError('AI time budget exhausted', category='deferred', diagnostics=[{
                    'operation': name, 'stage': 'budget', 'code': 'time_budget_exhausted',
                    'category': 'deferred', 'exception_type': 'TimeoutError'}])
                self.record_diagnostics(entries + error.diagnostics)
                raise error from None
            except (AIProviderError, ValueError, KeyError) as error:
                entries = safe_diagnostics(getattr(self.provider, 'diagnostics', [])[offset:])
                details = failure_diagnostics(error, operation=name)
                error.diagnostics = details
                self.record_diagnostics(entries + [entry for entry in details if entry not in entries])
                if isinstance(error, AIProviderError) and retry_delay(error):
                    delay = retry_delay(error)
                    self.store._atomic_json_write(self.path, {
                        'next_retry_at': (self.now + timedelta(seconds=delay)).isoformat(),
                        'category': error.category,
                    })
                raise
            else:
                self.record_diagnostics(getattr(self.provider, 'diagnostics', [])[offset:])
                return result
        return call
