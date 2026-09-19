"""Idempotent repair of legacy state. No network, AI, email, or user configuration."""

import hashlib

from .assembly import CampaignAssembler
from .candidate_notices import register_candidates, resolve_candidates
from .merge import CampaignIdentityResolver, CampaignMerger, normalize_name
from .models import PendingReview, SourceObservation
from .scheduler import ScheduleCompiler


def consolidate_campaigns(store, watched):
    groups = {}
    for campaign in sorted(store.list_campaigns(), key=lambda c: (c.first_seen_at, c.id)):
        if campaign.ip_slug not in watched or not normalize_name(campaign.partner):
            continue
        key = (campaign.ip_slug, normalize_name(campaign.partner))
        target = groups.get(key)
        if target is None:
            groups[key] = campaign
            continue
        ids = {a.id: a for a in target.activities}
        for activity in campaign.activities:
            original = activity.id
            if original in ids:
                if activity == ids[original]:
                    continue
                # Older assemblers reused IDs across editions. Preserve both and
                # retain the old deep link through the alias map.
                activity = activity.model_copy(deep=True)
                activity.id = 'activity-' + hashlib.sha256(f'{campaign.id}:{original}'.encode()).hexdigest()[:24]
                campaign.activity_redirects[original] = activity.id
                for job in store.list_queue_jobs():
                    if job.campaign_id == campaign.id and job.activity_id == original:
                        job.activity_id = activity.id
                        if job.action_id in (original + '--start', original + '--end'):
                            job.action_id = activity.id + job.action_id[len(original):]
                        store.save_queue_job(job)
                if activity.id in ids:
                    continue
            target.activities.append(activity)
            ids[activity.id] = activity
        CampaignMerger._merge_sources(target.sources, campaign.sources)
        for path, fact in campaign.fact_provenance.items():
            for old_id, new_id in campaign.activity_redirects.items():
                path = path.replace(f'activities.{old_id}.', f'activities.{new_id}.')
            target.fact_provenance.setdefault(path, fact)
        target.updated_at = max(target.updated_at, campaign.updated_at)
        target.status = CampaignAssembler._campaign_status(target.activities)
        target.revision += 1
        store.save_campaign(target)
        campaign.redirected_to = target.id
        store.save_campaign(campaign)


def repair_state(store, ips, now, *, timezone_name='Asia/Shanghai', remind_day_before=True):
    watched = {ip.slug for ip in ips}
    for path in sorted((store.root / 'pending-review').glob('*/*/*/*.json')):
        review = store._load(path, PendingReview)
        if review.resolved_campaign_id or review.candidate.ip_slug not in watched:
            continue
        matched, _ = CampaignIdentityResolver().find_match(review.candidate, store.list_campaigns())
        outcome = CampaignMerger().merge(matched, review.candidate, now)
        store.save_campaign(outcome.campaign)
        review.resolved_campaign_id = outcome.campaign.id
        store.save_pending_review(review)
    consolidate_campaigns(store, watched)
    # One-time scan of legacy observations; subsequent runs only reconcile schedules.
    if any(store.load_source_cursor('notification-schema-v1-' + ip.slug) is None for ip in ips):
        all_observations = [o for p in (store.root / 'observation-index').glob('*.json')
                            if (o := store.load_observation(p.stem))]
        for ip in ips:
            marker = 'notification-schema-v1-' + ip.slug
            if store.load_source_cursor(marker) is not None:
                continue
            campaigns = [c for c in store.list_campaigns() if c.ip_slug == ip.slug]
            sources = {s.id: s for c in campaigns for s in c.sources}
            pending_ids = {oid for p in store.list_pending_extractions() if p.ip_slug == ip.slug for oid in p.observation_ids}
            accounts = {(s.kind, s.account_id) for s in ip.sources}
            observations = {o.id: o for o in all_observations if o.id in sources or o.id in pending_ids
                            or (o.source.kind, o.source.account_id) in accounts}
            for source in sources.values():
                observations.setdefault(source.id, SourceObservation(id=source.id, source=source,
                    text=source.excerpt or source.summary or ''))
            register_candidates(store, ip, list(observations.values()), now, timezone_name, adopt_legacy=True)
            store.save_source_cursor(marker, {'version': 1})
    scheduler = ScheduleCompiler(timezone_name)
    for campaign in store.list_campaigns():
        if campaign.ip_slug in watched:
            scheduler.reconcile_campaign(store, campaign, now, remind_day_before=remind_day_before)
    resolve_candidates(store, now, timezone_name)
