"""Compile event changes and future action times into date-bucketed jobs."""

from __future__ import annotations

import hashlib
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .models import (
    ActionKind,
    Campaign,
    ChangeKind,
    ChangeRecord,
    EventStatus,
    EventAction,
    NotificationKind,
    QueueJob,
    ScheduleManifest,
    ScheduledJobRef,
)
from .storage import StateStore


REMINDABLE_ACTIONS = {
    ActionKind.RESERVATION_OPEN,
    ActionKind.RESERVATION_CLOSE,
    ActionKind.LOTTERY_OPEN,
    ActionKind.LOTTERY_RESULT,
    ActionKind.SALE_OPEN,
    ActionKind.SALE_CLOSE,
    ActionKind.GIFT,
    ActionKind.DISCOUNT,
    ActionKind.QUEUE_RELEASE,
    ActionKind.EVENT_START,
    ActionKind.EVENT_END,
}
DEADLINE_ACTIONS = {ActionKind.RESERVATION_CLOSE, ActionKind.SALE_CLOSE, ActionKind.EVENT_END}


def schedule_actions(activity, tz):
    """Derived schedule points; date-only midnight is never written to facts."""
    points = []
    for action in activity.actions:
        if action.cancelled or action.ended or action.kind not in REMINDABLE_ACTIONS:
            continue
        start = action.at or (datetime.combine(action.start_date, time(), tz) if action.start_date else None)
        if start:
            points.append(action.model_copy(update={'at': start}))
        end = action.end_at or (datetime.combine(action.end_date, time(), tz) if action.end_date else None)
        if end:
            kind = ActionKind.RESERVATION_CLOSE if action.kind == ActionKind.RESERVATION_OPEN else ActionKind.SALE_CLOSE if action.kind == ActionKind.SALE_OPEN else ActionKind.EVENT_END
            points.append(EventAction(id=action.id + '--end', kind=kind, title=action.title + '截止', at=end, start_date=action.end_date))
    for field, kind in [('start', ActionKind.EVENT_START), ('end', ActionKind.EVENT_END)]:
        value = getattr(activity, field + '_at')
        day = getattr(activity, field + '_date')
        value = value or (datetime.combine(day, time(), tz) if day else None)
        is_end = kind in DEADLINE_ACTIONS
        if value and not any(p.at == value and (p.kind in DEADLINE_ACTIONS) == is_end for p in points):
            points.append(EventAction(id=activity.id + '--' + field, kind=kind,
                title=activity.title + ('结束' if is_end else '开始'), at=value, start_date=day))
    return points


def _job_id(semantic_key: str) -> str:
    digest = hashlib.sha256(semantic_key.encode("utf-8")).hexdigest()[:20]
    return f"job-{digest}"


class ScheduleCompiler:
    def __init__(self, timezone_name: str = "Asia/Shanghai") -> None:
        try:
            self.timezone = ZoneInfo(timezone_name)
        except ZoneInfoNotFoundError:
            if timezone_name != "Asia/Shanghai":
                raise
            self.timezone = timezone(timedelta(hours=8), name="Asia/Shanghai")

    def jobs_for_changes(self, changes: list[ChangeRecord]) -> list[QueueJob]:
        jobs: list[QueueJob] = []
        for change in changes:
            if change.kind is ChangeKind.SOURCE_ADDED:
                continue
            kind = (
                NotificationKind.ANNOUNCEMENT
                if change.kind is ChangeKind.NEW_CAMPAIGN
                else NotificationKind.UPDATE
            )
            semantic_key = f"change:{change.semantic_key}"
            jobs.append(
                QueueJob(
                    id=_job_id(semantic_key),
                    campaign_id=change.campaign_id,
                    activity_id=change.activity_id,
                    change_id=change.id,
                    kind=kind,
                    due_date=change.detected_at.astimezone(self.timezone).date(),
                    semantic_key=semantic_key,
                    summary=change.summary,
                )
            )
        return jobs

    def future_jobs(
        self,
        campaign: Campaign,
        now: datetime,
        *,
        remind_day_before: bool = True,
    ) -> list[QueueJob]:
        if campaign.status in {EventStatus.CANCELLED, EventStatus.ENDED}:
            return []
        local_now = now.astimezone(self.timezone)
        jobs: list[QueueJob] = []
        for activity in campaign.activities:
            if activity.status in {EventStatus.CANCELLED, EventStatus.ENDED}:
                continue
            for action in schedule_actions(activity, self.timezone):
                if action.cancelled or action.at is None or action.kind not in REMINDABLE_ACTIONS:
                    continue
                local_action = action.at.astimezone(self.timezone)
                if (action.start_date < local_now.date() if action.start_date else local_action <= local_now):
                    continue
                if remind_day_before:
                    due_date = local_action.date() - timedelta(days=1)
                    if action.start_date or local_action > local_now:
                        notification_kind = (
                            NotificationKind.DEADLINE
                            if action.kind in DEADLINE_ACTIONS
                            else NotificationKind.DAY_BEFORE
                        )
                        semantic_key = (
                            f"schedule:{activity.id}:{action.id}:"
                            f"{notification_kind.value}:{local_action.isoformat()}"
                        )
                        jobs.append(
                            QueueJob(
                                id=_job_id(semantic_key),
                                campaign_id=campaign.id,
                                activity_id=activity.id,
                                action_id=action.id,
                                kind=notification_kind,
                                due_date=due_date,
                                expected_at=action.at,
                                expected_date=action.start_date,
                                semantic_key=semantic_key,
                                summary=self._summary(action.kind, action.title),
                            )
                        )
        return sorted(jobs, key=lambda job: (job.due_date, job.expected_at, job.id))

    def reconcile_campaign(
        self,
        store: StateStore,
        campaign: Campaign,
        now: datetime,
        *,
        remind_day_before: bool = True,
    ) -> list[QueueJob]:
        desired = self.future_jobs(
            campaign, now, remind_day_before=remind_day_before
        )
        # Reuse old IDs (and therefore receipts) even after an activity moves.
        existing_jobs = store.list_queue_jobs()
        for job in desired:
            old = next((j for j in existing_jobs if j.activity_id == job.activity_id
                        and j.action_id == job.action_id and j.expected_at == job.expected_at
                        and j.kind == job.kind), None)
            if old:
                job.id, job.semantic_key = old.id, old.semantic_key
        desired_by_id = {job.id: job for job in desired}
        previous = store.load_schedule_manifest(campaign.id)

        if previous:
            for old_ref in previous.jobs:
                if old_ref.job_id in desired_by_id:
                    continue
                old_job = next((j for j in existing_jobs if j.id == old_ref.job_id), None)
                if old_job and old_job.activity_id and any(c.id != campaign.id and
                        any(a.id == old_job.activity_id for a in c.activities) for c in store.list_campaigns()):
                    continue  # Its new parent must retain the old ID and receipt.
                store.delete_queue_job(
                    QueueJob(
                        id=old_ref.job_id,
                        campaign_id=campaign.id,
                        kind=NotificationKind.DAY_BEFORE,
                        due_date=old_ref.due_date,
                        semantic_key=old_ref.job_id,
                        summary="obsolete scheduled job",
                    )
                )

        for job in desired:
            store.save_queue_job(job)
        store.save_schedule_manifest(
            ScheduleManifest(
                campaign_id=campaign.id,
                campaign_revision=campaign.revision,
                jobs=[
                    ScheduledJobRef(job_id=job.id, due_date=job.due_date)
                    for job in desired
                ],
            )
        )
        return desired

    @staticmethod
    def validate_due_job(job: QueueJob, campaign: Campaign) -> bool:
        if not job.action_id:
            return True
        if campaign.status in {EventStatus.CANCELLED, EventStatus.ENDED}:
            return False
        for activity in campaign.activities:
            if job.activity_id and activity.id != job.activity_id:
                continue
            if activity.status in {EventStatus.CANCELLED, EventStatus.ENDED}:
                return False
            for action in schedule_actions(activity, ScheduleCompiler().timezone):
                if action.id != job.action_id:
                    continue
                return (
                    not action.cancelled
                    and action.at is not None
                    and action.at == job.expected_at
                )
        return False

    @staticmethod
    def _summary(kind: ActionKind, title: str) -> str:
        if kind is ActionKind.EVENT_START:
            return f"明天开始：{title}"
        if kind is ActionKind.SALE_OPEN:
            return f"明天开售：{title}"
        if kind is ActionKind.RESERVATION_OPEN:
            return f"明天开放预约：{title}"
        if kind in DEADLINE_ACTIONS:
            return f"明天截止：{title}"
        return f"明天需要关注：{title}"
