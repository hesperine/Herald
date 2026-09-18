"""Due-job validation, digest rendering, and email delivery interfaces."""

from __future__ import annotations

import smtplib
from dataclasses import dataclass
from datetime import date, datetime
from email.message import EmailMessage
from email.headerregistry import Address
from email.errors import HeaderParseError
from html import escape
from urllib.parse import urlsplit
from typing import Protocol
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .models import Campaign, NotificationKind, NotificationReceipt, QueueJob
from .scheduler import ScheduleCompiler, schedule_actions
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
    html: str = ""


@dataclass(frozen=True, slots=True)
class DeliveryResult:
    sent: tuple[QueueJob, ...]
    skipped: tuple[QueueJob, ...]
    email_sent: bool = False


class EmailSender(Protocol):
    def send(self, *, recipient: str, subject: str, text: str, html: str | None = None) -> None: ...


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

    def send(self, *, recipient: str, subject: str, text: str, html: str | None = None) -> None:
        recipients = []
        try:
            if '\r' in recipient or '\n' in recipient:
                raise ValueError
            for value in recipient.split(','):
                value = value.strip()
                if not value:
                    continue
                address = Address(addr_spec=value)
                if not address.username or not address.domain:
                    raise ValueError
                if address.addr_spec not in recipients:
                    recipients.append(address.addr_spec)
            if not recipients:
                raise ValueError
        except (ValueError, IndexError, HeaderParseError):
            raise ValueError('invalid NOTIFY_EMAIL recipient list') from None
        message = EmailMessage()
        message["From"] = self.username
        message["To"] = recipients[0] if len(recipients) == 1 else 'undisclosed-recipients:;'
        message["Subject"] = subject
        message.set_content(text)
        if html:
            message.add_alternative(html, subtype="html")

        if self.use_ssl:
            with smtplib.SMTP_SSL(self.host, self.port, timeout=30) as client:
                client.login(self.username, self.password)
                refused = client.send_message(message, to_addrs=recipients)
                if refused:
                    raise RuntimeError('email delivery incomplete')
            return

        with smtplib.SMTP(self.host, self.port, timeout=30) as client:
            client.starttls()
            client.login(self.username, self.password)
            refused = client.send_message(message, to_addrs=recipients)
            if refused:
                raise RuntimeError('email delivery incomplete')


class NotificationService:
    @staticmethod
    def _reason_summary(campaign, job):
        path = job.summary.removeprefix('更新：')
        if path == job.summary:
            return job.summary
        from .fact_updates import _target
        try:
            target, field = _target(campaign, path)
        except (ValueError, AttributeError):
            return '活动信息已更新，请查看企划详情'
        labels = {'rules': '规则', 'related_offers': '关联优惠', 'start_at': '开始时间',
                  'at': '开放时间', 'end_at': '截止时间', 'start_date': '开始日期',
                  'end_date': '截止日期', 'title': '名称', 'partner': '合作方',
                  'status': '状态', 'requires_reservation': '预约要求'}
        value = getattr(target, field)
        if isinstance(value, datetime):
            value = value.isoformat()
        return f"{getattr(target, 'title', campaign.title)} · {labels.get(field, '信息')}已更新：{value}"

    def __init__(self, timezone_name: str = "Asia/Shanghai") -> None:
        try:
            self.timezone = ZoneInfo(timezone_name)
        except ZoneInfoNotFoundError:
            # ScheduleCompiler already implements the default UTC+8 fallback.
            self.timezone = ScheduleCompiler(timezone_name).timezone

    def collect_due(
        self, store: StateStore, day: date, *, include_delivered: bool = False
    ) -> tuple[list[NotificationItem], list[QueueJob]]:
        items: list[NotificationItem] = []
        skipped: list[QueueJob] = []
        jobs = store.load_queue_jobs(day)
        # Carry unsent announcements/updates across midnight; never replay old countdowns.
        for directory in sorted((store.root / 'queue').glob('*/*/*')):
            try:
                queued_day = date(*map(int, directory.relative_to(store.root / 'queue').parts))
            except ValueError:
                continue
            if queued_day < day:
                jobs.extend(job for job in store.load_queue_jobs(queued_day)
                            if job.kind in (NotificationKind.ANNOUNCEMENT, NotificationKind.UPDATE))
        delivered_ids = {p.stem for p in (store.root / 'notified').glob('*/*/*/*.json')}
        for job in jobs:
            delivered = job.id in delivered_ids
            if delivered and (not include_delivered or not store.has_receipt(job.id, day)):
                skipped.append(job)
                continue
            campaign = store.load_campaign(job.campaign_id)
            if campaign is None or not ScheduleCompiler.validate_due_job(job, campaign):
                skipped.append(job)
                continue
            items.append(NotificationItem(job=job, campaign=campaign))
        return items, skipped

    @staticmethod
    def build_cards(items: list[NotificationItem]) -> list[dict]:
        """Public presentation model; current facts, grouped reasons, no receipts."""
        cards: dict[tuple[str, str | None], dict] = {}
        for item in sorted(items, key=lambda item: item.job.id):
            campaign, job = item.campaign, item.job
            activity = next((a for a in campaign.activities if a.id == job.activity_id), None)
            key = (campaign.id, job.activity_id)
            card = cards.setdefault(key, {
                "campaign_id": campaign.id,
                "campaign_title": campaign.title,
                "ip_name": campaign.ip_name,
                "activity_id": job.activity_id,
                "activity": activity.model_dump(mode="json") if activity else None,
                "reasons": [],
                "sources": [s.model_dump(mode="json") for s in campaign.sources],
            })
            reason = {"kind": job.kind.value, "summary": NotificationService._reason_summary(campaign, job),
                      "action_id": job.action_id,
                      "expected_at": job.expected_at.isoformat() if job.expected_at else None}
            point = next((p for p in schedule_actions(activity, ScheduleCompiler().timezone) if p.id == job.action_id), None) if activity else None
            reason['date_only'] = str(point.start_date) if point and point.start_date else None
            if reason not in card["reasons"]:
                card["reasons"].append(reason)
        return list(cards.values())

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
        cards = self.build_cards(ordered)
        for card in cards:
            activity = card["activity"]
            lines.append(f"【{activity['title'] if activity else card['campaign_title']}】")
            lines.append(card["campaign_title"])
            for reason in card["reasons"]:
                lines.append(reason["summary"])
            if activity:
                for venue in activity["venues"]:
                    lines.append("地点：" + " · ".join(str(venue[k]) for k in
                        ("province", "city", "name", "address", "online_platform") if venue[k]))
                for action in activity["actions"]:
                    if action["cancelled"]:
                        continue
                    when = self._display_time(action["at"], action.get("start_date"))
                    lines.append(f"{action['title']}：{when}")
                    if action.get('end_at') or action.get('end_date'):
                        lines.append("截止：" + self._display_time(action.get("end_at"), action.get("end_date")))
                    if action.get('rules'):
                        lines.append(action['rules'])
                    for field in ('scope', 'quantity_limit', 'end_condition'):
                        if action.get(field):
                            lines.append(action[field])
                    if action["url"]:
                        lines.append(action["url"])
            for source in card["sources"]:
                lines.append(f"信息依据：{source.get('summary') or source['account_name']} {source['url']}")
            lines.append("")
        subject = f"游戏联动提醒：{len(cards)} 项需要关注"
        return NotificationDigest(subject, "\n".join(lines).rstrip() + "\n", tuple(ordered),
                                  self._render_html(cards, generated_at))

    def _display_time(self, at, day=None):
        if at:
            value = datetime.fromisoformat(str(at).replace('Z', '+00:00'))
            return value.astimezone(self.timezone).strftime('%Y-%m-%d %H:%M')
        return f'{day}（时刻待公布）' if day else '时间待公布'

    def _render_html(self, cards, generated_at):
        def text(value):
            return escape(str(value or '')).replace('\n', '<br>')

        def link(url, label):
            if urlsplit(str(url)).scheme not in ('http', 'https'):
                return text(label)
            return f'<a href="{escape(str(url), quote=True)}" style="color:#146c59;text-decoration:underline;">{text(label)}</a>'

        def paragraph(value, *, strong=False):
            style = 'margin:8px 0;line-height:1.7;overflow-wrap:anywhere;'
            if strong:
                style += 'font-weight:600;color:#933743;'
            return f'<p style="{style}">{text(value)}</p>'

        sections = []
        for is_news, heading in ((True, '今日新消息'), (False, '即将开始或结束')):
            blocks = []
            for card in cards:
                reasons = [r for r in card['reasons'] if (r['kind'] in ('announcement', 'update')) == is_news]
                if not reasons:
                    continue
                activity = card['activity']
                title = activity['title'] if activity else card['campaign_title']
                parts = [f'<p style="margin:0 0 6px;color:#617170;font-size:12px;">{text(card["ip_name"])} · {text(card["campaign_title"])}</p>',
                         f'<h3 style="margin:0 0 12px;font-size:19px;line-height:1.5;">{text(title)}</h3>']
                for reason in reasons:
                    summary = reason['summary']
                    if reason.get('expected_at'):
                        summary += ' · ' + self._display_time(None if reason.get('date_only') else reason['expected_at'], reason.get('date_only'))
                    parts.append(paragraph(summary, strong=True))
                if activity:
                    for venue in activity['venues']:
                        place = ' · '.join(str(venue[k]) for k in ('province', 'city', 'name', 'address', 'online_platform') if venue.get(k))
                        if place:
                            parts.append(paragraph('地点：' + place))
                    if activity.get('rules'):
                        parts.append(paragraph(activity['rules']))
                    for action in activity['actions']:
                        if action['cancelled']:
                            continue
                        when = self._display_time(action.get('at'), action.get('start_date'))
                        parts.append(paragraph(action['title'] + ' · ' + when))
                        if action.get('end_at') or action.get('end_date'):
                            parts.append(paragraph('截止：' + self._display_time(action.get('end_at'), action.get('end_date')), strong=True))
                        tags = [label for key, label in (('requires_reservation', '需预约'), ('requires_rush', '需抢购或抢名额'), ('ended', '已结束')) if action.get(key)]
                        if tags:
                            parts.append(paragraph(' / '.join(tags)))
                        for field in ('rules', 'scope', 'quantity_limit', 'end_condition'):
                            if action.get(field):
                                parts.append(paragraph(action[field]))
                        if action.get('url'):
                            parts.append('<p style="margin:12px 0;">' + link(action['url'], '预约 / 购买入口 →') + '</p>')
                sources = []
                seen = set()
                for source in card['sources']:
                    if source['url'] not in seen:
                        seen.add(source['url'])
                        sources.append(link(source['url'], source.get('summary') or source['account_name']))
                if sources:
                    parts.append('<p style="margin:16px 0 0;padding-top:12px;border-top:1px solid #e3e9e8;color:#617170;font-size:13px;">信息依据：' + ' · '.join(sources) + '</p>')
                blocks.append('<tr><td style="padding:20px;border:1px solid #dfe5e5;border-radius:10px;background:#ffffff;">' + ''.join(parts) + '</td></tr><tr><td height="14"></td></tr>')
            if blocks:
                sections.append(f'<h2 style="margin:24px 0 12px;font-size:17px;color:#146c59;">{heading}</h2><table role="presentation" width="100%" cellspacing="0" cellpadding="0">' + ''.join(blocks) + '</table>')
        if not cards:
            sections.append('<p style="padding:24px;background:#ffffff;border:1px solid #dfe5e5;border-radius:10px;">今日暂无需要关注的更新。</p>')
        day = generated_at.astimezone(self.timezone).strftime('%Y-%m-%d')
        summary = f'{len(cards)} 项活动需要关注' if cards else '暂无更新，愿你度过轻松的一天'
        return ('<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">'
                '<meta name="viewport" content="width=device-width,initial-scale=1"><title>HERALD 每日提醒</title></head>'
                '<body style="margin:0;background:#f6f7f8;color:#263238;font-family:Arial,sans-serif;font-size:15px;line-height:1.6;">'
                '<table role="presentation" width="100%" cellspacing="0" cellpadding="0" bgcolor="#f6f7f8"><tr><td align="center" style="padding:24px 12px;">'
                '<table role="presentation" width="640" cellspacing="0" cellpadding="0" style="width:100%;max-width:640px;"><tr><td>'
                '<p style="margin:0;color:#146c59;font-size:12px;letter-spacing:2px;font-weight:bold;">HERALD</p>'
                '<h1 style="margin:6px 0;font-size:26px;">游戏联动日报</h1>'
                f'<p style="margin:0;color:#617170;">{day} · {text(summary)}</p>' + ''.join(sections) +
                '<p style="margin-top:24px;padding-top:16px;border-top:1px solid #dfe5e5;color:#617170;font-size:12px;">'
                '信息整理自官方公告，参与条件与最终安排请以原帖为准。<br>'
                f'时间以 {text(str(self.timezone))} 显示；未公布时刻的日期不代表零点开始。</p>'
                '</td></tr></table></td></tr></table></body></html>')

    def render_empty_digest(self, generated_at: datetime) -> NotificationDigest:
        local_day = generated_at.astimezone(self.timezone).date()
        subject = f"游戏联动日报：{local_day:%Y-%m-%d} 暂无更新"
        text = (
            f"游戏联动日报 · {local_day:%Y-%m-%d}\n\n"
            "今日暂无需要关注的更新。\n"
        )
        return NotificationDigest(subject, text, (), self._render_html([], generated_at))

    @staticmethod
    def _daily_digest_receipt_id(day: date) -> str:
        return f"daily-digest-{day.isoformat()}"

    def _save_daily_digest_receipt(
        self, store: StateStore, day: date, sent_at: datetime
    ) -> None:
        receipt_id = self._daily_digest_receipt_id(day)
        store.save_receipt(
            NotificationReceipt(
                job_id=receipt_id,
                semantic_key=f"daily-digest:{day.isoformat()}",
                sent_at=sent_at,
                delivery_day=day,
            )
        )

    def deliver_due(
        self,
        *,
        store: StateStore,
        day: date,
        generated_at: datetime,
        sender: EmailSender,
        recipient: str,
        send_empty_digest: bool = False,
    ) -> DeliveryResult:
        items, skipped = self.collect_due(store, day)
        if store.has_receipt(self._daily_digest_receipt_id(day), day):
            return DeliveryResult((), tuple(skipped), email_sent=False)
        if not items:
            receipt_id = self._daily_digest_receipt_id(day)
            if not send_empty_digest or store.has_receipt(receipt_id, day):
                return DeliveryResult((), tuple(skipped))
            digest = self.render_empty_digest(generated_at)
            sender.send(recipient=recipient, subject=digest.subject, text=digest.text, html=digest.html)
            self._save_daily_digest_receipt(store, day, generated_at)
            return DeliveryResult((), tuple(skipped), email_sent=True)
        digest = self.render_digest(items, generated_at)
        sender.send(recipient=recipient, subject=digest.subject, text=digest.text, html=digest.html)
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
        self._save_daily_digest_receipt(store, day, generated_at)
        return DeliveryResult(tuple(sent_jobs), tuple(skipped), email_sent=True)
