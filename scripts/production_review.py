"""Actual production pipeline, fixed public sources, in-memory email only."""
import asyncio
import hashlib
import json
import os
from pathlib import Path
import sys
import io
import tarfile
import subprocess
from datetime import datetime
import httpx
from two_pass_review import public_dataset
from herald.ai import OpenAICompatibleProvider
from herald.config import load_settings
from herald.cli import _make_provider
from herald.models import SourceObservation, SourceRef
from herald.pipeline import ObservationPipeline
from herald.notifications import NotificationService
from herald.registry import RegisteredIp
from herald.storage import StateStore
from herald.site import StaticSiteBuilder


async def main(directory, audit=False):
    root = Path(directory)
    root.mkdir(parents=True, exist_ok=True)
    store = StateStore(root / 'state')
    store.initialize()
    if audit and not (root / 'audit-seed.json').exists():
        revision = subprocess.check_output(['git', 'rev-parse', 'origin/state']).decode().strip()
        with tarfile.open(fileobj=io.BytesIO(subprocess.check_output(['git', 'archive', revision]))) as archive:
            for member in archive.getmembers():
                if not member.isfile() or not member.name.endswith('.json'):
                    continue
                target = store.root / member.name
                if not target.resolve().is_relative_to(store.root.resolve()):
                    raise ValueError('invalid archive path')
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(archive.extractfile(member).read())
        (root / 'audit-seed.json').write_text(json.dumps({'revision': revision}), encoding='utf8')
    data = public_dataset()
    secrets = [v for k, v in os.environ.items() if k in ('AI_API_KEY', 'SMTP_PASSWORD', 'SMTP_USERNAME',
        'NOTIFY_EMAIL', 'WEIBO_COOKIE', 'ORIGIN_CITY', 'REACHABLE_CITIES') and v]
    def save(name, value):
        text = json.dumps(value, ensure_ascii=False, indent=2)
        if any(s in text for s in secrets):
            raise ValueError('private value detected')
        (root / name).write_text(text, encoding='utf8')
    save('dataset.json', data)
    summary = []
    async with httpx.AsyncClient() as client:
        provider = _make_provider(load_settings(), client)
        if provider is None or 'deepseek' not in provider.model_name.lower():
            raise ValueError('DeepSeek configuration required')
        complete = provider._complete
        async def cached(payload, schema):
            key = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
            path = root / (key + '-response.json')
            if path.exists():
                return schema.model_validate_json(path.read_text(encoding='utf8'))
            save(key + '-request.json', payload)
            print('AI ' + schema.__name__, flush=True)
            result = await complete(payload, schema)
            save(key + '-response.json', result.model_dump(mode='json'))
            return result
        provider._complete = cached
        if audit:
            now = datetime.fromisoformat('2026-10-25T12:00:00+08:00')
            by_ip = {}
            names = {n.ip_slug: n.ip_name for n in store.list_candidates()}
            for pending in store.list_pending_extractions():
                store.save_pending_extraction(pending.model_copy(update={'next_retry_at': None}))
                for oid in pending.observation_ids:
                    item = store.load_observation(oid)
                    if item:
                        by_ip.setdefault(pending.ip_slug, {})[oid] = item
            for slug, items in by_ip.items():
                result = await ObservationPipeline().process(store=store, ip=RegisteredIp(slug=slug, name=names[slug]),
                    observations=list(items.values()), now=now, provider=provider)
                print(slug, 'pending', result.pending_extractions, flush=True)
            save('audit-report.json', {'pending': [p.model_dump(mode='json') for p in store.list_pending_extractions()],
                                      'diagnostics': provider.diagnostics})
            for p in store.list_pending_extractions():
                print(p.ip_slug, p.reason, flush=True)
            return
        for batch in data['batches']:
            now = datetime.fromisoformat(batch['day'] + 'T12:00:00+08:00')
            by_ip = {}
            for identifier, post in batch['posts'].items():
                slug = 'arknights-endfield' if '终末地' in post['ip'] else 'arknights'
                item = SourceObservation(id=identifier, text=post['text'], source=SourceRef(
                    id=identifier, kind='website' if identifier.startswith('website') else 'skland',
                    url=post['url'], account_name=post['ip'], published_at=post['published_at'],
                    first_seen_at=now, content_hash=hashlib.sha256(post['text'].encode()).hexdigest()))
                by_ip.setdefault((slug, post['ip']), []).append(item)
            results = []
            for (slug, name), items in by_ip.items():
                result = await ObservationPipeline().process(store=store, ip=RegisteredIp(slug=slug, name=name),
                    observations=items, now=now, provider=provider)
                results.append(result.__dict__ if hasattr(result, '__dict__') else {k: getattr(result, k) for k in result.__dataclass_fields__})
                print(batch['day'], slug, 'pending', result.pending_extractions, flush=True)
            before_repeat = {j.id for j in store.list_queue_jobs()}
            repeat_calls = 0
            for (slug, name), items in by_ip.items():
                repeated = await ObservationPipeline().process(store=store, ip=RegisteredIp(slug=slug, name=name),
                    observations=items, now=now, provider=provider)
                repeat_calls += repeated.ai_calls
            if not store.list_pending_extractions() and (repeat_calls or before_repeat != {j.id for j in store.list_queue_jobs()}):
                raise ValueError('replay was not idempotent')
            service = NotificationService()
            due, _ = service.collect_due(store, now.date(), now=now)
            digest = service.render_digest(due, now)
            class MemorySender:
                def send(self, **kwargs):
                    (root / (batch['day'] + '-email.html')).write_text(kwargs['html'], encoding='utf8')
                    (root / (batch['day'] + '-email.txt')).write_text(kwargs['text'], encoding='utf8')
            pending = len(store.list_pending_extractions())
            if not pending:
                service.deliver_due(store=store, day=now.date(), generated_at=now,
                                    sender=MemorySender(), recipient='memory-only@example.invalid')
            StaticSiteBuilder().build(store=store, output_dir=root / 'page', now=now,
                watched_ip_slugs={slug for slug, _ in by_ip}, forbidden_values=secrets)
            summary.append({'day': batch['day'], 'pipeline': results, 'cards': service.build_cards(due),
                            'pending': pending, 'repeat_ai_calls': repeat_calls})
        save('report.json', {'model': provider.model_name, 'days': summary, 'diagnostics': provider.diagnostics})
    # Scan all generated state/page/request artifacts against known private values.
    for path in root.rglob('*'):
        if path.is_file() and path.suffix in ('.json', '.html', '.txt'):
            raw = path.read_text(encoding='utf8')
            if any(s in raw for s in secrets):
                raise ValueError('private value detected')
    print('Production replay complete; no real mail sent.', flush=True)


if __name__ == '__main__':
    try:
        asyncio.run(main(sys.argv[1], '--audit' in sys.argv[2:]))
    except Exception as error:
        print('Production review failed: ' + type(error).__name__, file=sys.stderr)
        raise SystemExit(1)
