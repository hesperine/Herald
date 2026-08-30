"""Compile event changes and future action times into date-bucketed jobs."""

from __future__ import annotations

import hashlib
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .models import (
    ActionKind,
    Campaign,
    ChangeKind,
    ChangeRecord,
    EventStatus,
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
    ActionKind.QUEUE_RELEASE,
    ActionKind.EVENT_START,
}
DEADLINE_ACTIONS = {ActionKind.RESERVATION_CLOSE, ActionKind.SALE_CLOSE}


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
            for action in activity.actions:
                if action.cancelled or action.at is None or action.kind not in REMINDABLE_ACTIONS:
                    continue
                local_action = action.at.astimezone(self.timezone)
                if local_action <= local_now:
                    continue
                if remind_day_before:
                    due_date = local_action.date() - timedelta(days=1)
                    if due_date >= local_now.date():
                        notification_kind = (
                            NotificationKind.DEADLINE
                            if action.kind in DEADLINE_ACTIONS
                            else NotificationKind.DAY_BEFORE
                        )
                        semantic_key = (
                            f"schedule:{campaign.id}:{activity.id}:{action.id}:"
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
        desired_by_id = {job.id: job for job in desired}
        previous = store.load_schedule_manifest(campaign.id)

        if previous:
            for old_ref in previous.jobs:
                if old_ref.job_id in desired_by_id:
                    continue
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
            for action in activity.actions:
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
