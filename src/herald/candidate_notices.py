"""Durable public candidate notices, independent of extraction and campaign IDs."""

import hashlib
import re
from datetime import datetime

from .candidate_stage import prepare_candidates
from .models import CandidateNotice, ChangeRecord, NotificationKind, QueueJob
from .scheduler import ScheduleCompiler, _job_id


def _id(*parts):
    return 'candidate-' + hashlib.sha256('\x1f'.join(parts).encode()).hexdigest()[:24]


def register_candidates(store, ip, observations, now, timezone_name='Asia/Shanghai', *, adopt_legacy=False):
    records = store.list_candidates()
    jobs = store.list_queue_jobs()
    by_id = {o.id: o for o in observations}
    day = now.astimezone(ScheduleCompiler(timezone_name).timezone).date()
    for selection in prepare_candidates(observations, [ip.name, *ip.aliases]):
        if not selection.accepted:
            continue
        members = [by_id[i] for i in selection.group.observation_ids]
        tokens = {(o.source.id, o.source.content_hash) for o in members}
        hashes = {o.source.content_hash for o in members}
        record = next((r for r in records if r.ip_slug == ip.slug and any(
            (s.id, s.content_hash) in tokens or s.content_hash in hashes for s in r.sources)), None)
        if record is None:
            primary = by_id[selection.group.primary_id]
            record = CandidateNotice(id=_id(ip.slug, primary.id, primary.source.content_hash),
                ip_slug=ip.slug, ip_name=ip.name, detected_at=now,
                public_text=primary.text[:500], sources=[o.source.model_copy(deep=True) for o in members])
            # Adopt a pre-upgrade job/receipt instead of creating a second announcement.
            source_ids = {s.id for s in record.sources}
            for path in sorted((store.root / 'daily').glob('*/*/*/*.json')) if adopt_legacy else ():
                change = store._load(path, ChangeRecord)
                if not source_ids.intersection(change.source_ids):
                    continue
                old = next((j for j in jobs if j.change_id == change.id and not j.candidate_id), None)
                if old and old.kind == NotificationKind.ANNOUNCEMENT:
                    record.id, record.campaign_id = old.id, old.campaign_id
                    old.candidate_id = record.id
                    store.save_queue_job(old)
                    break
            if adopt_legacy and record.campaign_id is None:
                for campaign in store.list_campaigns(include_redirects=True):
                    if campaign.ip_slug != ip.slug or not source_ids.intersection(s.id for s in campaign.sources):
                        continue
                    legacy_id = _job_id(f'change:{campaign.id}:new-campaign')
                    if store.find_receipt(legacy_id):
                        record.id, record.campaign_id = legacy_id, campaign.id
                        break
            previous = next((r for r in records if r.id == record.id), None)
            if previous:
                known = {s.id for s in previous.sources}
                previous.sources.extend(s for s in record.sources if s.id not in known)
                if record.public_text not in previous.public_text:
                    previous.public_text += '\n' + record.public_text
                record = previous
            else:
                records.append(record)
        else:
            known = {s.id for s in record.sources}
            record.sources.extend(o.source.model_copy(deep=True) for o in members if o.source.id not in known)
        store.save_candidate(record)
        if not any(j.id == record.id for j in jobs) and not store.find_receipt(record.id):
            job = QueueJob(id=record.id, candidate_id=record.id, campaign_id=record.campaign_id,
                kind=NotificationKind.ANNOUNCEMENT, due_date=day, semantic_key=record.id,
                summary='发现联动相关信息（待解析，请以原帖为准）')
            store.save_queue_job(job)
            jobs.append(job)


FACT_LABELS = {'start_at': '开始时间', 'start_date': '开始日期', 'end_at': '截止时间',
    'end_date': '截止日期', 'at': '时间', 'url': '参与入口', 'rules': '规则',
    'scope': '适用范围', 'quantity_limit': '数量限制', 'end_condition': '结束条件',
    'requires_reservation': '需预约', 'requires_rush': '需抢购', 'ended': '已结束',
    'cancelled': '已取消', 'related_offers': '关联优惠'}


def public_facts(campaign):
    facts = {}
    for activity in campaign.activities:
        for entity in [activity, *activity.actions]:
            for field, label in FACT_LABELS.items():
                value = getattr(entity, field, None)
                if value is None or value is False:
                    continue
                value = value.isoformat() if hasattr(value, 'isoformat') else str(value)
                facts[f'{entity.id}:{label}'] = value
        for venue in activity.venues:
            for field in ('city', 'name', 'address', 'online_platform', 'business_hours'):
                value = getattr(venue, field)
                if value:
                    facts[f'{activity.id}:地点:{field}'] = value
    return facts


def _already_shown(value, text):
    compact = lambda s: re.sub(r'\s+', '', s)
    if compact(value) in compact(text):
        return True
    if re.match(r'^\d{4}-\d{2}-\d{2}', value):
        at = datetime.fromisoformat(value)
        date_forms = (f'{at.month}月{at.day}日', f'{at.month:02}月{at.day:02}日', value[:10])
        date_seen = any(s in text for s in date_forms)
        return date_seen and ('T' not in value or f'{at.hour:02}:{at.minute:02}' in text or f'{at.hour}:{at.minute:02}' in text)
    return False


def resolve_candidates(store, now, timezone_name='Asia/Shanghai'):
    campaigns = store.list_campaigns()
    jobs = store.list_queue_jobs()
    day = now.astimezone(ScheduleCompiler(timezone_name).timezone).date()
    records = store.list_candidates()
    all_receipts = store.list_receipts()
    associations = {r.id: next((c for c in campaigns if c.ip_slug == r.ip_slug and
        {(s.id, s.content_hash) for s in r.sources}.intersection(
            (s.id, s.content_hash) for s in c.sources)), None) for r in records}
    # Older versions of an already structured source keep their parent association.
    for record in records:
        if associations[record.id] is None and record.campaign_id:
            associations[record.id] = store.load_campaign(record.campaign_id)
    for record in records:
        campaign = associations[record.id]
        if campaign is None:
            continue
        record.campaign_id, record.status = campaign.id, 'structured'
        record.facts = public_facts(campaign)
        store.save_candidate(record)
        related = {key for key, value in associations.items() if value and value.id == campaign.id}
        receipts = [r for j in jobs if j.candidate_id in related if (r := store.find_receipt(j.id))]
        receipts.extend(r for r in all_receipts if r.candidate_id in related and r not in receipts)
        initial = store.find_receipt(record.id)
        if initial and initial not in receipts:
            receipts.append(initial)
        summary = '联动信息：' + campaign.title
        if not initial:
            job = next((j for j in jobs if j.id == record.id), None)
            if job:
                job.campaign_id = campaign.id
                job.summary = summary + ''.join('\n' + k.split(':', 1)[1] + '：' + v for k, v in record.facts.items())
                store.save_queue_job(job)
            continue
        seen_facts = {k: v for r in sorted(receipts, key=lambda r: r.sent_at) for k, v in r.facts.items()}
        sent_text = '\n'.join(r.public_text for r in receipts)
        # Old receipts have no content snapshot: trust their completed announcement.
        if initial and not initial.public_text and not initial.facts:
            initial.facts = record.facts
            store.save_receipt(initial)
            continue
        added = {k: v for k, v in record.facts.items() if seen_facts.get(k) != v
                 and (k in seen_facts or not _already_shown(v, sent_text))}
        pending_updates = [j for j in jobs if j.candidate_id in related
                           and j.kind == NotificationKind.UPDATE and not store.find_receipt(j.id)]
        pending_initial = any(j.id in related and not store.find_receipt(j.id) for j in jobs)
        if not added or pending_initial:
            # Reverted facts and new-post summaries must not leave stale updates queued.
            for job in pending_updates:
                store.delete_queue_job(job)
                jobs.remove(job)
            continue
        if added:
            baseline = max(receipts, key=lambda r: (r.sent_at, r.job_id)).job_id
            key = _id(record.id, baseline, *[f'{k}={v}' for k, v in sorted(added.items())])
            if not any(j.id == key for j in jobs) and not store.find_receipt(key):
                update = QueueJob(id=key, candidate_id=record.id, campaign_id=campaign.id,
                    kind=NotificationKind.UPDATE, due_date=day, semantic_key=key,
                    summary='补充联动信息：' + campaign.title + ''.join('\n' + k.split(':', 1)[1] + '：' + v for k, v in added.items()))
                existing = next((j for j in jobs if j.candidate_id in related and j.kind == NotificationKind.UPDATE and not store.find_receipt(j.id)), None)
                if existing:
                    update.id, update.semantic_key, update.due_date = existing.id, existing.semantic_key, existing.due_date
                    jobs.remove(existing)
                store.save_queue_job(update)
                jobs.append(update)
