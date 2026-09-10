"""Bounded public history recording using the production community adapters."""
import argparse
import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path

import httpx

from .candidates import CandidateFilter
from .candidate_stage import prepare_candidates
from .cli import _parse_now
from .registry import IpRegistry
from .sources.miyoushe import MiyousheTimelineClient
from .sources.skland import SklandTimelineClient
from .storage import StateStore


PUBLIC_PATHS = {'/painter/wapi/userPostList', '/post/wapi/getPostFull', '/web/v2/user/items', '/web/v1/item'}


async def collect(directory, since, pages, before=None):
    if directory.exists():
        raise ValueError('use a new collection directory')
    directory.mkdir(parents=True)
    writer = StateStore(directory)
    reports = []
    for ip in IpRegistry.load_builtin().entries:
        for source in ip.sources:
            name = f'{ip.slug}-{source.account_id}'
            output = directory / name
            observations, raw, skipped = [], [], []
            async def before_request(request):
                if source.kind.value != 'miyoushe':
                    await asyncio.sleep(1.2)
            async def after_response(response):
                if response.request.url.path not in PUBLIC_PATHS:
                    return  # Never record device identity or auth refresh.
                await response.aread()
                raw.append({'path': response.request.url.path,
                            'status': response.status_code, 'body': response.text})
                writer._atomic_json_write(output / 'raw-responses.json', raw)
            def persist(item):
                observations.append(item.observation)
                writer._atomic_json_write(output / 'observations.json', {
                    'ip_slug': ip.slug, 'observations': [o.model_dump(mode='json') for o in observations]})
                return item
            class RecordedMiyoushe(MiyousheTimelineClient):
                async def _fetch_detail(self, **kwargs):
                    return persist(await super()._fetch_detail(**kwargs))
            class RecordedSkland(SklandTimelineClient):
                async def _fetch_detail(self, **kwargs):
                    return persist(await super()._fetch_detail(**kwargs))
            status = 'incomplete'
            async with httpx.AsyncClient(timeout=35, event_hooks={'request': [before_request], 'response': [after_response]}) as client:
                adapter = RecordedMiyoushe(client) if source.kind.value == 'miyoushe' else RecordedSkland(client)
                parameters = dict(account_id=source.account_id, account_name=source.account_name,
                    cursor=None, first_seen_at=datetime.now(timezone.utc), published_since=since,
                    max_pages=pages)
                if before is not None:
                    parameters['published_before'] = before
                if source.kind.value == 'miyoushe':
                    parameters['account_url'] = source.url
                try:
                    await adapter.fetch_account(**parameters)
                    status = 'complete' if getattr(adapter, 'last_fetch_complete', False) else 'incomplete:pagination_limit_or_boundary'
                except Exception as exc:
                    status = 'interrupted:' + type(exc).__name__
            candidates = []
            for observation in observations:
                decision = CandidateFilter().evaluate(observation, ip_names=[ip.name, *ip.aliases], from_official_ip_account=True)
                candidates.append({'id': observation.id, 'published_at': observation.source.published_at.isoformat(),
                    'title': observation.text.split('\n')[0], 'accepted': decision.relevant,
                    'matched_terms': decision.matched_terms,
                    'exclude_from_evaluation': any(x in observation.text for x in ('大白兔', '国家图书馆'))})
            writer._atomic_json_write(output / 'review.json', {'candidates': candidates, 'skipped_previews': skipped})
            selections = prepare_candidates(observations, [ip.name, *ip.aliases])
            passed_ids = {i for s in selections if s.accepted for i in s.group.observation_ids}
            writer._atomic_json_write(output / 'candidates.json', {
                'ip_slug': ip.slug, 'boundary': 'after_candidate_filter',
                'observations': [o.model_dump(mode='json') for o in observations if o.id in passed_ids]})
            writer._atomic_json_write(output / 'candidate-groups.json', [
                {'primary_id': s.group.primary_id, 'observation_ids': s.group.observation_ids}
                for s in selections if s.accepted])
            report = {'ip': ip.name, 'account': source.account_name, 'directory': name,
                'status': status, 'raw_responses': len(raw), 'observations': len(observations),
                'eligible_candidates': sum(c['accepted'] and not c['exclude_from_evaluation'] for c in candidates)}
            reports.append(report)
            writer._atomic_json_write(directory / 'manifest.json', {'since': since.isoformat(), 'before_exclusive': before.isoformat() if before else None, 'max_pages_per_account': pages,
                'note': 'No sampling preview filter. candidates.json contains every CandidateFilter PASS; few-shot exclusions are marked separately.', 'sources': reports})
            print(json.dumps(report, ensure_ascii=True), flush=True)
    # Deduplicate across accounts of the same IP using the same production stage.
    for ip in IpRegistry.load_builtin().entries:
        items = []
        for source in ip.sources:
            path = directory / f'{ip.slug}-{source.account_id}' / 'observations.json'
            if path.exists():
                from .models import SourceObservation
                items.extend(SourceObservation.model_validate(o) for o in json.loads(path.read_text(encoding='utf-8'))['observations'])
        selections = prepare_candidates(items, [ip.name, *ip.aliases])
        passed_ids = {i for s in selections if s.accepted for i in s.group.observation_ids}
        writer._atomic_json_write(directory / 'candidates' / f'{ip.slug}.json', {
            'ip_slug': ip.slug, 'boundary': 'after_dedupe_and_candidate_filter',
            'observations': [o.model_dump(mode='json') for o in sorted(items, key=lambda o: (o.source.published_at, o.id)) if o.id in passed_ids],
            'groups': [{'primary_id': s.group.primary_id, 'observation_ids': s.group.observation_ids} for s in selections if s.accepted]})


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--directory', type=Path, required=True)
    parser.add_argument('--since', default='2026-06-01T00:00:00+08:00')
    parser.add_argument('--before', help='exclusive timezone-aware upper bound')
    parser.add_argument('--pages', type=int, default=12)
    args = parser.parse_args()
    if not 1 <= args.pages <= 300:
        parser.error('pages must be between 1 and 300')
    asyncio.run(collect(args.directory, _parse_now(args.since), args.pages,
                        _parse_now(args.before) if args.before else None))


if __name__ == '__main__':
    main()
