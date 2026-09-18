"""Bounded AI work and public, credential-free retry metadata."""

import asyncio
from datetime import datetime, timedelta
from time import monotonic

from .ai import AIProviderError


def record_failure(store, pending, now, error):
    category = getattr(error, 'category', 'validation')
    if category == 'deferred':
        return
    attempts = pending.attempts + 1
    delay = max(getattr(error, 'retry_after_seconds', None) or 0,
                86400 if category in ('quota', 'authentication') else
                min(86400, 10800 * 2 ** min(attempts - 1, 3)))
    store.save_pending_extraction(pending.model_copy(update={
        'attempts': attempts, 'next_retry_at': now + timedelta(seconds=delay),
        'error_category': category, 'reason': 'AI processing failed; retry scheduled',
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

    def __getattr__(self, name):
        method = getattr(self.provider, name)
        if name not in ('extract', 'extract_batch', 'merge_campaign'):
            return method

        async def call(*args, **kwargs):
            import json
            state = json.loads(self.path.read_text(encoding='utf-8')) if self.path.exists() else {}
            until = state.get('next_retry_at')
            available = self.deadline - monotonic()
            if self.remaining <= 0 or available <= 0 or (until and datetime.fromisoformat(until) > self.now):
                raise AIProviderError('AI work deferred', category='deferred')
            self.remaining -= 1
            try:
                return await asyncio.wait_for(method(*args, **kwargs), timeout=available)
            except TimeoutError:
                raise AIProviderError('AI time budget exhausted', category='deferred') from None
            except AIProviderError as error:
                if error.category in ('rate_limit', 'quota', 'authentication'):
                    delay = max(error.retry_after_seconds or 0,
                                86400 if error.category != 'rate_limit' else 10800)
                    self.store._atomic_json_write(self.path, {
                        'next_retry_at': (self.now + timedelta(seconds=delay)).isoformat(),
                        'category': error.category,
                    })
                raise
        return call
