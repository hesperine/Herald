"""Build offline UI/email samples using the production renderers. No secrets or network."""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta
from hashlib import sha256
from pathlib import Path
import sys
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))

from herald.media import CachedMediaAsset
from herald.models import Activity, Campaign, EventAction, NotificationReceipt, QueueJob, SourceRef
from herald.notifications import NotificationItem, NotificationService
from herald.site import StaticSiteBuilder
from herald.storage import StateStore


def build_preview(output: Path, now: datetime) -> Path:
    if now.tzinfo is None:
        raise ValueError('--now must include a timezone offset')
    output.mkdir(parents=True, exist_ok=False)
    page = output / 'page'
    media_dir = page / 'assets/media'
    media_dir.mkdir(parents=True)
    store = StateStore(output / 'state')
    store.initialize()
    assets = {}
    for name, width, height, caption in (
        ('landscape', 1200, 700, '横图 · 活动主视觉'),
        ('square', 900, 900, '方图 · 周边展示'),
        ('long-poster', 900, 3000, '长图 · 参与规则'),
    ):
        labels = ''.join(f'<text x="72" y="{y}" font-size="28" fill="#46615a">{line}</text>'
                         for y, line in ((360, '01 / 预约与参与方式'), (550, '02 / 周边与满赠规则'),
                                         (850, '03 / 请以官方原帖为准'), (1600, '原尺寸模式可滚动查看'),
                                         (2800, 'END / 演示长图底部')) if y < height - 40)
        svg = (f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">'
               f'<rect width="{width}" height="{height}" fill="#e7eeeb"/>'
               f'<rect x="28" y="28" width="{width-56}" height="{height-56}" rx="20" fill="#f8faf9" stroke="#afc3b9"/>'
               '<g font-family="system-ui, sans-serif"><text x="72" y="120" font-size="24" fill="#617170">HERALD / PREVIEW</text>'
               f'<text x="72" y="205" font-size="44" fill="#194e40">{caption}</text>'
               f'<text x="72" y="265" font-size="24" fill="#617170">演示图片 · 不含真实活动信息</text>{labels}</g></svg>')
        content = svg.encode('utf8')
        asset_path = f'assets/media/{name}.svg'
        (page / asset_path).write_bytes(content)
        url = f'https://example.com/{name}.svg'
        assets[url] = CachedMediaAsset(source_url=url, asset_path=asset_path,
            sha256=sha256(content).hexdigest(), content_type='image/svg+xml', size_bytes=len(content))

    source = SourceRef(id='preview-source', kind='skland', url='https://example.com/announcement',
        account_name='演示官方账号', published_at=now-timedelta(days=2), first_seen_at=now,
        content_hash='a'*64, summary='演示公告：预约开放、周边预售与满赠安排；以下均为演示数据。',
        media_urls=list(assets))
    tomorrow = (now + timedelta(days=1)).replace(hour=12, minute=0, second=0, microsecond=0)
    campaign = Campaign(id='preview', ip_slug='preview', ip_name='演示游戏',
        title='【演示数据】秋日主题联动企划', partner='示例合作方',
        first_seen_at=now-timedelta(days=8), updated_at=now, announced_at=now-timedelta(days=8),
        sources=[source], activities=[
            Activity(id='popup', kind='popup', title='【演示】主题快闪',
                start_date=tomorrow.date(), end_date=(now+timedelta(days=14)).date(),
                venues=[{'id':'city','city':'上海','name':'示例活动空间'}],
                rules='参加活动前请先预约，具体安排以原帖为准。',
                actions=[EventAction(id='reservation',kind='reservation_open',title='第一轮预约',
                    at=tomorrow, requires_reservation=True)]),
            Activity(id='merch',kind='merchandise',title='【演示】主题周边预售',
                actions=[EventAction(id='sale',kind='sale_open',title='周边购买（时间待公布）'),
                         EventAction(id='gift',kind='gift',title='指定系列满赠',
                             at=now-timedelta(days=2), end_at=tomorrow.replace(hour=23,minute=59),
                             rules='单笔订单满259元赠特典卡组一套。', scope='指定系列商品', end_condition='赠完即止')])])
    store.save_campaign(campaign)
    ended = campaign.model_copy(deep=True)
    ended.id='preview-ended'
    ended.title='【演示数据】已结束的活动'
    ended.status='ended'
    store.save_campaign(ended)
    service = NotificationService('Asia/Shanghai')
    for offset in range(7, 0, -1):
        stamp = now-timedelta(days=offset)
        archive_job = QueueJob(id=f'archive-{offset}', campaign_id=ended.id, kind='update',
            due_date=stamp.date(), semantic_key=f'preview:{offset}', summary='这是当日保存的演示提醒，活动已结束，仍可查看原帖。')
        store.save_queue_job(archive_job)
        store.save_receipt(NotificationReceipt(job_id=archive_job.id, semantic_key=archive_job.semantic_key,
            sent_at=stamp,delivery_day=stamp.date()))
        store.save_reminder_snapshot(stamp.date(), {'date':str(stamp.date()),
            'cards':service.build_cards([NotificationItem(archive_job,ended)])})
    news = QueueJob(id='preview-news',campaign_id=campaign.id,activity_id='popup',kind='update',
        due_date=now.date(),semantic_key='preview:news',summary='新公布第一轮预约时间，请提前查看参与规则。')
    deadline = QueueJob(id='preview-deadline',campaign_id=campaign.id,activity_id='merch',action_id='gift--end',kind='deadline',
        due_date=now.date(),expected_at=tomorrow.replace(hour=23,minute=59),semantic_key='preview:deadline',summary='明天截止：指定系列满赠')
    for job in (news, deadline):
        store.save_queue_job(job)
    StaticSiteBuilder().build(store=store, output_dir=page, now=now,
        watched_ip_slugs={'preview'}, media_assets=assets)
    digest = service.render_digest([NotificationItem(job,campaign) for job in (news,deadline)],now)
    (page/'email-preview.html').write_text(digest.html,encoding='utf8')
    (page/'email-preview.txt').write_text(digest.text,encoding='utf8')
    (page/'email-empty.html').write_text(service.render_empty_digest(now).html,encoding='utf8')
    (page/'preview.html').write_text('''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>HERALD 本地展示预览</title>
<link rel="stylesheet" href="style.css"></head><body><main><h1>HERALD 本地展示预览</h1>
<p>以下均为演示数据，图片与日期仅用于检查展示效果。没有访问来源、调用AI或发送邮件。</p>
<ul><li><a href="event.html?id=preview#sources">图片缩略图与放大查看</a></li>
<li><a href="index.html">活动卡片</a></li><li><a href="today.html">最近七天每日提醒</a></li>
<li><a href="email-preview.html">有更新的HTML邮件</a> · <a href="email-preview.txt">纯文本版本</a></li>
<li><a href="email-empty.html">暂无更新的HTML邮件</a></li></ul>
<p>图片：完整比例缩略图，点击放大；方向键切换，Esc关闭；原尺寸模式可滚动查看长图。</p>
<p>历史：可查看今天及前六天。更早快照已清除；已结束活动只保留提醒摘要和原帖链接。</p>
</main></body></html>''',encoding='utf8')
    return page


if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,default=ROOT/'.herald-work/ui-preview')
    parser.add_argument('--now',help='ISO8601 timestamp with timezone; defaults to current China time')
    args=parser.parse_args()
    now=datetime.fromisoformat(args.now) if args.now else datetime.now(ZoneInfo('Asia/Shanghai'))
    print(build_preview(args.output,now))
