"""Due-job validation, digest rendering, and email delivery interfaces."""

from __future__ import annotations

import smtplib
from dataclasses import dataclass
from datetime import date, datetime
from email.message import EmailMessage
from typing import Protocol
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .models import Campaign, NotificationReceipt, QueueJob
from .scheduler import ScheduleCompiler
from .storage import StateStore


@dataclass(frozen=True, slots=True)
class NotificationItem:
    job: QueueJob
    campaign: Campaign


@dataclass(frozen=True, slots=True)
class NotificationDigest:
    subject: str
    text: str
    items: tuple[NotificationItem, ...]


@dataclass(frozen=True, slots=True)
class DeliveryResult:
    sent: tuple[QueueJob, ...]
    skipped: tuple[QueueJob, ...]


class EmailSender(Protocol):
    def send(self, *, recipient: str, subject: str, text: str) -> None: ...


class SmtpEmailSender:
    """Small SMTP sender; credentials never enter state or logs."""

    def __init__(
        self,
        *,
        host: str,
        port: int,
        username: str,
        password: str,
        use_ssl: bool = True,
    ) -> None:
        self.host = host
        self.port = port
        self.username = username
        self.password = password
        self.use_ssl = use_ssl

    def send(self, *, recipient: str, subject: str, text: str) -> None:
        message = EmailMessage()
        message["From"] = self.username
        message["To"] = recipient
        message["Subject"] = subject
        message.set_content(text)

        if self.use_ssl:
            with smtplib.SMTP_SSL(self.host, self.port, timeout=30) as client:
                client.login(self.username, self.password)
                client.send_message(message)
            return

        with smtplib.SMTP(self.host, self.port, timeout=30) as client:
            client.starttls()
            client.login(self.username, self.password)
            client.send_message(message)


class NotificationService:
    def __init__(self, timezone_name: str = "Asia/Shanghai") -> None:
        try:
            self.timezone = ZoneInfo(timezone_name)
        except ZoneInfoNotFoundError:
            # ScheduleCompiler already implements the default UTC+8 fallback.
            self.timezone = ScheduleCompiler(timezone_name).timezone

    def collect_due(
        self, store: StateStore, day: date
    ) -> tuple[list[NotificationItem], list[QueueJob]]:
        items: list[NotificationItem] = []
        skipped: list[QueueJob] = []
        for job in store.load_queue_jobs(day):
            if store.has_receipt(job.id, day):
                skipped.append(job)
                continue
            campaign = store.load_campaign(job.campaign_id)
            if campaign is None or not ScheduleCompiler.validate_due_job(job, campaign):
                skipped.append(job)
                continue
            items.append(NotificationItem(job=job, campaign=campaign))
        return items, skipped

    def render_digest(
        self, items: list[NotificationItem], generated_at: datetime
    ) -> NotificationDigest:
        ordered = sorted(
            items,
            key=lambda item: (
                item.job.expected_at or generated_at,
                item.campaign.ip_name,
                item.job.id,
            ),
        )
        lines = [
            f"游戏联动提醒 · {generated_at.astimezone(self.timezone):%Y-%m-%d}",
            "",
        ]
        for item in ordered:
            lines.append(f"【{item.job.summary}】")
            lines.append(item.campaign.title)
            if item.job.expected_at:
                lines.append(
                    "目标时间："
                    + item.job.expected_at.astimezone(self.timezone).strftime(
                        "%Y-%m-%d %H:%M"
                    )
                )
            if item.campaign.sources:
                latest_source = max(
                    item.campaign.sources, key=lambda source: source.published_at
                )
                lines.append(
                    "信息依据："
                    + latest_source.published_at.astimezone(self.timezone).strftime(
                        "%Y-%m-%d"
                    )
                    + f" {latest_source.account_name}"
                )
                lines.append(str(latest_source.url))
            lines.append("")
        subject = f"游戏联动提醒：{len(ordered)} 项需要关注"
        return NotificationDigest(subject, "\n".join(lines).rstrip() + "\n", tuple(ordered))

    def deliver_due(
        self,
        *,
        store: StateStore,
        day: date,
        generated_at: datetime,
        sender: EmailSender,
        recipient: str,
    ) -> DeliveryResult:
        items, skipped = self.collect_due(store, day)
        if not items:
            return DeliveryResult((), tuple(skipped))
        digest = self.render_digest(items, generated_at)
        sender.send(recipient=recipient, subject=digest.subject, text=digest.text)
        sent_jobs: list[QueueJob] = []
        for item in digest.items:
            store.save_receipt(
                NotificationReceipt(
                    job_id=item.job.id,
                    semantic_key=item.job.semantic_key,
                    sent_at=generated_at,
                    delivery_day=day,
                )
            )
            sent_jobs.append(item.job)
        return DeliveryResult(tuple(sent_jobs), tuple(skipped))
