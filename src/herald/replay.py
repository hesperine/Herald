"""Explicit local recording and offline-source replay. Never constructs SMTP."""
import argparse
import asyncio
import json
import hashlib
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

import httpx

from .cli import _make_provider, _parse_now
from .config import load_settings
from .models import SourceObservation
from .pipeline import ObservationPipeline
from .registry import IpRegistry
from .storage import StateStore
from .sources.miyoushe import MiyousheTimelineClient


def publish_replay(store, output, now, ip_slug, secrets, assets):
    from .site import StaticSiteBuilder
    from .notifications import NotificationService
    builder = StaticSiteBuilder()
    builder.build(store=store, output_dir=output, now=now, watched_ip_slugs={ip_slug},
                  forbidden_values=secrets, media_assets=assets)
    service = NotificationService()
    due, _ = service.collect_due(store, now.astimezone(service.timezone).date())
    (output / 'digest.txt').write_text(service.render_digest(due, now).text, encoding='utf-8')
    builder._scan_forbidden(output, secrets)
    builder._scan_forbidden(store.root, secrets)


def split_materials(items, cutoff):
    from importlib.resources import files
    examples = json.loads(files('herald').joinpath('data/activity-examples.json').read_text(encoding='utf-8'))
    if any(item.id in {e['input']['observation_id'] for e in examples} for item in items):
        raise ValueError('few-shot posts cannot enter evaluation')
    ordered = sorted(items, key=lambda o: (o.source.published_at, o.id))
    before = [o for o in ordered if o.source.published_at < cutoff]
    after = [o for o in ordered if o.source.published_at >= cutoff]
    if not ordered:
        raise ValueError('replay materials are required')
    return before, after


async def run(args):
    root = args.directory.resolve()
    writer = StateStore(root)
    if args.mode in {'record', 'normalize-recording'}:
        if args.mode == 'record' and root.exists():
            raise ValueError('record directory must be new')
        registry = IpRegistry.load_builtin()
        ip = next(i for i in registry.entries if i.slug == args.ip)
        source = next(s for s in ip.sources if s.kind.value == 'miyoushe')
        responses = []
        async def record_response(response):
            await response.aread()
            # No request/response headers, cookies, or authentication metadata.
            responses.append({'url': str(response.request.url), 'status': response.status_code,
                              'body': response.text})
        if args.mode == 'normalize-recording':
            responses = json.loads((root / 'raw-responses.json').read_text(encoding='utf-8'))
            usable = {r['url']: r for r in responses if r['status'] == 200 and 'getPostFull' in r['url'] and json.loads(r['body']).get('retcode') == 0}
            def handler(request):
                r = usable.get(str(request.url))
                return httpx.Response(r['status'], text=r['body']) if r else httpx.Response(404)
            items = []
            async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
                adapter = MiyousheTimelineClient(client)
                for url in usable:
                    item = await adapter._fetch_detail(post_id=httpx.URL(url).params['post_id'],
                        account_id=source.account_id, account_name=source.account_name,
                        subsite=source.url.split('/')[3], first_seen_at=datetime.now(timezone.utc))
                    items.append(item.observation.model_dump(mode='json'))
            writer._atomic_json_write(root / 'observations.json', {'ip_slug': ip.slug, 'observations': items})
            return {'normalized_successful_details': len(items), 'responses': len(responses), 'complete_fetch': False}
        async with httpx.AsyncClient(timeout=30, event_hooks={'response': [record_response]}) as client:
            try:
                batch = await MiyousheTimelineClient(client).fetch_account(
                    account_id=source.account_id, account_name=source.account_name,
                    account_url=source.url, cursor=None, first_seen_at=datetime.now(timezone.utc),
                    published_since=_parse_now(args.since), max_pages=args.pages)
            finally:
                writer._atomic_json_write(root / 'raw-responses.json', responses)
        writer._atomic_json_write(root / 'observations.json', {
            'ip_slug': ip.slug, 'observations': [i.observation.model_dump(mode='json') for i in batch.items]})
        return {'recorded': len(batch.items), 'responses': len(responses)}
    dataset = json.loads((root / 'dataset.json').read_text(encoding='utf-8'))
    dataset_hash = hashlib.sha256(json.dumps(dataset, sort_keys=True).encode()).hexdigest()
    observations = [SourceObservation.model_validate(o) for o in dataset['observations']]
    before, after = split_materials(observations, _parse_now(dataset['cutoff']))
    ip = next(i for i in IpRegistry.load_builtin().entries if i.slug == dataset['ip_slug'])
    store = StateStore(root / 'replay-state')
    marker = root / 'bootstrap-complete.json'
    if args.mode == 'bootstrap' and marker.exists():
        raise ValueError('bootstrap is already complete; use a new dataset directory to reset')
    if args.mode == 'incremental' and not marker.exists():
        raise ValueError('complete bootstrap before incremental replay')
    if marker.exists() and json.loads(marker.read_text(encoding='utf-8')).get('dataset_hash') != dataset_hash:
        raise ValueError('dataset changed after bootstrap; create a new replay directory')
    store.initialize()
    settings = load_settings()
    secret_values = [v.get_secret_value() for v in settings.private.__dict__.values()
                     if hasattr(v, 'get_secret_value') and v.get_secret_value()]
    trace_dir = root / 'traces' / datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%f')
    sequence = 0
    async def trace(response):
        nonlocal sequence
        sequence += 1
        await response.aread()
        # Persist only the model conversation and successful model answer.
        request = json.loads(response.request.content)
        answer = None
        if response.is_success:
            try:
                answer = response.json()['choices'][0]['message']['content']
            except (KeyError, ValueError, IndexError):
                pass
        def scrub(value):
            if isinstance(value, str):
                for secret in secret_values:
                    value = value.replace(secret, '[REDACTED]')
            elif isinstance(value, list):
                value = [scrub(v) for v in value]
            elif isinstance(value, dict):
                value = {k: scrub(v) for k, v in value.items()}
            return value
        writer._atomic_json_write(trace_dir / f'{sequence:03}.json', scrub({
            'model': request.get('model'), 'messages': request.get('messages'),
            'status': response.status_code, 'answer': answer}))
    # This client is used only by the AI provider. No source clients are created.
    async with httpx.AsyncClient(timeout=90, event_hooks={'response': [trace]}) as client:
        provider = _make_provider(settings, client)
        if provider is None:
            raise ValueError('AI provider is required')
        pipeline = ObservationPipeline()
        writer._atomic_json_write(trace_dir / 'before.json', [c.model_dump(mode='json') for c in store.list_campaigns()])
        selected = before if args.mode == 'bootstrap' else after
        groups = [selected] if args.mode == 'bootstrap' else [[o] for o in selected]
        reports = []
        for group in groups:
            now = _parse_now(dataset['cutoff']) if args.mode == 'bootstrap' else group[0].source.published_at
            result = await pipeline.process(store=store, ip=ip, observations=group, now=now,
                provider=provider, suppress_immediate_for={o.id for o in group} if args.mode == 'bootstrap' else set())
            reports.append(asdict(result))
            writer._atomic_json_write(trace_dir / 'diagnostics.json', getattr(provider, 'diagnostics', []))
            if result.pending_extractions:
                break
        writer._atomic_json_write(trace_dir / 'after.json', [c.model_dump(mode='json') for c in store.list_campaigns()])
        writer._atomic_json_write(trace_dir / 'report.json', reports)
    if args.mode == 'bootstrap' and not store.list_pending_extractions():
        writer._atomic_json_write(marker, {'cutoff': dataset['cutoff'], 'dataset_hash': dataset_hash})
    preview_at = _parse_now(args.now) if getattr(args, 'now', None) else (_parse_now(dataset['cutoff']) if args.mode == 'bootstrap' or not selected else selected[-1].source.published_at)
    output = root / ('page-' + args.mode)
    assets = {}
    media_failures = 0
    if getattr(args, 'cache_media', False):
        from .media import PublicMediaCache
        urls = [str(u) for c in store.list_campaigns() if c.is_visible(preview_at) for s in c.sources for u in s.media_urls]
        async with httpx.AsyncClient(timeout=30) as media_client:
            cached = await PublicMediaCache(media_client).cache(urls, output)
            assets, media_failures = cached.assets, cached.failed_count
    publish_replay(store, output, preview_at, ip.slug, secret_values, assets)
    return {'mode': args.mode, 'reports': reports, 'pending': len(store.list_pending_extractions()), 'trace_directory': str(trace_dir), 'page_directory': str(output), 'cached_images': len(assets), 'failed_images': media_failures}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('mode', choices=['record', 'normalize-recording', 'bootstrap', 'incremental'])
    parser.add_argument('--directory', type=Path, required=True)
    parser.add_argument('--ip', default='genshin-impact')
    parser.add_argument('--since', default='2026-08-01T00:00:00+08:00')
    parser.add_argument('--pages', type=int, default=3)
    parser.add_argument('--now', help='view snapshot timestamp; original publication dates are preserved')
    parser.add_argument('--cache-media', action='store_true')
    args = parser.parse_args()
    try:
        result = asyncio.run(run(args))
    except Exception:
        print('Replay failed; configuration or source/model access requires checking. No email was sent.')
        return 1
    print(json.dumps(result, ensure_ascii=False))
    return 1 if result.get('pending') else 0


if __name__ == '__main__':
    raise SystemExit(main())
