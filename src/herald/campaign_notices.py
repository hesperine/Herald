"""Campaign-level delivery deltas with durable legacy baselines."""
import hashlib
import json
from .candidate_notices import public_facts, _already_shown
from .models import QueueJob, NotificationKind
from .scheduler import ScheduleCompiler


def facts(campaign):
    result = public_facts(campaign)
    result['campaign:状态'] = campaign.status.value if campaign.status.value in ('ended', 'cancelled') else 'active'
    for activity in campaign.activities:
        result[activity.id + ':活动'] = activity.title
        result[activity.id + ':状态'] = activity.status.value if activity.status.value in ('ended', 'cancelled') else 'active'
        for action in activity.actions:
            result[action.id + ':事项'] = action.title
            for field, label in [('cancelled', '已取消'), ('ended', '已结束'),
                                 ('requires_reservation', '需预约'), ('requires_rush', '需抢购')]:
                value = getattr(action, field)
                if value is not None:
                    result[action.id + ':' + label] = str(value)
    return result


def related_notices(store, campaign):
    tokens = {(s.id, s.content_hash) for s in campaign.sources}
    return [n for n in store.list_candidates() if n.ip_slug == campaign.ip_slug and
            (campaign.id in n.campaign_ids or n.campaign_id == campaign.id or
             tokens.intersection((s.id, s.content_hash) for s in n.sources))]


def prepare_baselines(store):
    """Run before extraction. Never turn unmailed current facts into a receipt."""
    receipts = store.list_receipts()
    for campaign in store.list_campaigns():
        path = store.root / 'notice-baselines' / (campaign.id + '.json')
        if path.exists():
            continue
        related = {n.id for n in related_notices(store, campaign)}
        old = [r for r in receipts if r.candidate_id in related or r.job_id in related]
        if not old:
            continue
        old.sort(key=lambda r: (r.sent_at, r.job_id))
        baseline = {k: v for r in old for k, v in r.facts.items()}
        if all(not r.public_text and not r.facts for r in old):
            baseline = facts(campaign)
        # Legacy schema did not snapshot entity names/status or explicit false.
        # Seed ONLY those newly tracked fields, before any new extraction.
        previous_schema = public_facts(campaign)
        baseline.update({k: v for k, v in facts(campaign).items() if k not in previous_schema})
        store._atomic_json_write(path, {'facts': baseline, 'receipt_id': old[-1].job_id,
            'public_text': '\n'.join(r.public_text for r in old)})


def migrate_contextual(store, ips, now):
    """One-time, restartable audit of active legacy sources; keep all receipts."""
    from .candidate_stage import prepare_candidates
    from .models import PendingExtraction
    from .pipeline import ObservationPipeline
    prepare_baselines(store)
    for ip in ips:
        marker = 'contextual-v2-' + ip.slug
        if store.load_source_cursor(marker):
            continue
        campaigns = [c for c in store.list_campaigns() if c.ip_slug == ip.slug]
        for notice in store.list_candidates():
            if notice.ip_slug != ip.slug or notice.resolution_version == 2 or notice.status != 'structured':
                continue
            tokens = {(s.id, s.content_hash) for s in notice.sources}
            ids = [c.id for c in campaigns if tokens.intersection((s.id, s.content_hash) for s in c.sources)]
            if ids:
                notice.campaign_ids = ids
                notice.resolution_version = 2
                store.save_candidate(notice)
        source_ids = {s.id for c in campaigns if c.is_visible(now) for s in c.sources}
        observations = [o for oid in sorted(source_ids) if (o := store.load_observation(oid))]
        for selection in prepare_candidates(observations, [ip.name, *ip.aliases]):
            if not selection.accepted:
                continue
            ids = selection.group.observation_ids
            pid = ObservationPipeline._id('pending-ai', *ids)
            if store.load_pending_extraction(pid) is None:
                store.save_pending_extraction(PendingExtraction(id=pid, observation_ids=list(ids),
                    ip_slug=ip.slug, queued_at=now, reason='one-time contextual scope audit'))
        store.save_source_cursor(marker, {'version': 2})


def describe(campaign, changes, previous=None):
    previous = previous or {}
    entities = {'campaign': (campaign.title, 'campaign')}
    for activity in campaign.activities:
        entities[activity.id] = (activity.title, activity.id)
        for action in activity.actions:
            entities[action.id] = (activity.title + ' / ' + action.title, activity.id)
    grouped, seen = {}, set()
    for key, value in changes.items():
        identifier, label = key.split(':', 1)
        title, scope = entities.get(identifier, (campaign.title, identifier))
        if label == '状态' and value == 'active' and previous.get(key) not in ('ended', 'cancelled'):
            continue
        if value == 'False' and label in ('已取消', '已结束') and previous.get(key) != 'True':
            continue
        signature = (scope, label.replace('开始时间', '时间'), value)
        if signature in seen:
            continue
        seen.add(signature)
        display = {'True': '是', 'False': '否', 'cancelled': '已取消', 'ended': '已结束', 'active': '恢复开放'}.get(value, value)
        if label in ('活动', '事项'):
            grouped.setdefault(title, [])
            continue
        if key in previous and previous[key] != value and label not in ('状态', '已取消', '已结束'):
            display = previous[key] + ' → ' + display
        grouped.setdefault(title, []).append(f'{label}：{display}')
    return '\n'.join(title + ('\n' + '\n'.join(lines) if lines else '（详情待公布）')
        for title, lines in grouped.items()
        if lines or not any(other.startswith(title + ' / ') for other in grouped))


def resolve_campaign_notices(store, now, timezone_name):
    day = now.astimezone(ScheduleCompiler(timezone_name).timezone).date()
    notices = store.list_candidates()
    managed = {cid for n in notices if n.resolution_version == 2 and n.status == 'structured'
               for cid in n.campaign_ids}
    jobs, receipts = store.list_queue_jobs(), store.list_receipts()
    for cid in managed:
        campaign = store.load_campaign(cid)
        if campaign is None:
            continue
        related = related_notices(store, campaign)
        covered = [n.id for n in related if n.resolution_version == 2 and n.status == 'structured']
        snapshot = facts(campaign)
        delivered = sorted((r for r in receipts if r.campaign_id and
            (parent := store.load_campaign(r.campaign_id)) and parent.id == campaign.id), key=lambda r: (r.sent_at, r.job_id))
        path = store.root / 'notice-baselines' / (campaign.id + '.json')
        baseline = json.loads(path.read_text(encoding='utf8')) if path.exists() else None
        if delivered:
            baseline = {'facts': {k: v for r in delivered for k, v in r.facts.items()},
                        'receipt_id': delivered[-1].job_id, 'public_text': ''}
        if baseline is None:
            fallback = [r for r in receipts if r.candidate_id in covered or r.job_id in covered]
            if fallback:
                baseline = {'facts': {k: v for r in fallback for k, v in r.facts.items()},
                            'receipt_id': fallback[-1].job_id, 'public_text': '\n'.join(r.public_text for r in fallback)}
        added = {k: v for k, v in snapshot.items() if baseline is None or
                 (baseline['facts'].get(k) != v and not _already_shown(v, baseline.get('public_text', '')))}
        summary = describe(campaign, added, baseline['facts'] if baseline else None)
        # Retire source-level unsent announcements only after completed extraction.
        for job in jobs:
            if not store.find_receipt(job.id) and (job.candidate_id in covered or
                    (job.campaign_notice and job.campaign_id == campaign.id)):
                store.delete_queue_job(job)
        if baseline is not None and not summary:
            continue
        signature = json.dumps([campaign.id, baseline['receipt_id'] if baseline else None, snapshot], sort_keys=True)
        jid = 'notice-' + hashlib.sha256(signature.encode()).hexdigest()[:24]
        if store.find_receipt(jid):
            continue
        store.save_queue_job(QueueJob(id=jid, campaign_id=campaign.id, campaign_notice=True,
            covered_candidate_ids=covered, facts=snapshot,
            kind=NotificationKind.UPDATE if baseline else NotificationKind.ANNOUNCEMENT,
            due_date=day, semantic_key=jid,
            summary=('活动更新' if baseline else '新联动') + ('\n' + summary if summary else '：详情待公布')))
