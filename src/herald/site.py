"""Minimal static JSON and HTML publisher.

Visual design is intentionally absent. This module exists to verify filtering,
date indexes, details, and privacy boundaries before a later frontend redesign.
"""

from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .media import CachedMediaAsset
from .models import Campaign
from .notifications import NotificationService
from .reachability import ReachabilityClassifier
from .scheduler import ScheduleCompiler, schedule_actions
from .storage import StateStore


STYLE_CSS = r"""body{font:16px/1.6 system-ui,sans-serif;color:#222;background:#fff;margin:0}main{max-width:1050px;margin:auto;padding:24px}nav{display:flex;gap:24px;border-bottom:1px solid #bbb;padding-bottom:12px}a{color:#146c59}h1{font-size:28px}h2{font-size:22px}h3{font-size:19px}input{font:inherit;padding:6px;max-width:100%;box-sizing:border-box}#events{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(280px,100%),1fr));gap:16px;list-style:none;padding:0}.card{border:1px solid #ccc;border-radius:6px;padding:16px;overflow-wrap:anywhere}.card h2{font-size:20px;margin:4px 0}.deadline{font-weight:600;color:#a52b35}section{border-top:1px solid #ddd;padding:16px 0;scroll-margin-top:16px}p,li,dd{overflow-wrap:anywhere}img{max-width:100%;height:auto}figure{margin:12px 0}summary{cursor:pointer}dt{font-weight:600}dd{margin:0 0 10px}button{font:inherit}small{color:#555}:target{outline:2px solid #146c59;outline-offset:4px}*{letter-spacing:0}"""

INDEX_HTML = r"""<!doctype html>
<html lang="zh-CN">
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>活动卡片 · HERALD</title><link rel="stylesheet" href="style.css"></head>
<body><main><nav><a href="index.html">活动卡片</a><a href="today.html">每日提醒</a></nav><h1>活动卡片</h1><p id="generated-at"></p><label>搜索活动 <input id="search" type="search"></label><ul id="events"></ul></main><script src="app.js"></script></body>
</html>
"""

APP_JS = r"""function displayTime(at, day) {
  return at ? new Date(at).toLocaleString('zh-CN', {timeZone:'Asia/Shanghai',hour12:false,year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit'}) : day ? day + '（时刻待公布）' : '待公布';
}
function node(tag, text) { const el=document.createElement(tag); if(text !== undefined) el.textContent=text; return el; }
function sourceLabel(s) { return s.summary || (s.excerpt || '').split('\n')[0].slice(0,100) || s.account_name; }
function sourceLinks(parent, sources) {
  const list=node('ul'); const seen=new Set();
  for(const s of sources || []) { if(seen.has(s.url))continue; seen.add(s.url);
    const li=node('li'), a=node('a',sourceLabel(s)); a.href=s.url;a.rel='noreferrer';li.append(a);list.append(li);
  } parent.append(list);
}
const list=document.getElementById('events'), search=document.getElementById('search');
let cards=[];
function render() {
 list.replaceChildren(); const query=search.value.trim().toLowerCase();
 for(const c of cards.filter(c=>JSON.stringify(c).toLowerCase().includes(query))) {
   const a=c.activity, li=node('li');li.className='card';
   const link=node('a',a.title);link.href=c.url;
   const heading=node('h2');heading.append(link);
   li.append(node('small',c.campaign_title),heading);
   if(c.next){const remaining=Math.ceil((new Date(c.next.at)-new Date())/86400000);const p=node('p',c.next.title+' · '+displayTime(c.next.date?null:c.next.at,c.next.date)+(remaining>0?' · 还有'+remaining+'天':''));p.className='deadline';li.append(p);}
   li.append(node('p',displayTime(a.start_at,a.start_date)+' 至 '+displayTime(a.end_at,a.end_date)));
   li.append(node('p',(a.venues||[]).map(v=>v.online_platform||[v.city,v.name].filter(Boolean).join(' ')).filter(Boolean).join(' · ')));
   if(a.rules)li.append(node('p',a.rules));
   if(!c.next)li.append(node('p','暂无后续明确时间，请查看原帖'));
   const details=node('details');details.append(node('summary','参与事项与时间'));
   for(const action of a.actions||[]){
     const expired=action.end_at?new Date(action.end_at)<new Date():action.end_date?action.end_date<new Date().toLocaleDateString('sv-SE',{timeZone:'Asia/Shanghai'}):false;
     const item=node('p',action.title+' · '+(action.cancelled?'已取消':action.ended?'已结束':(expired?'已过截止时间 · ':'')+displayTime(action.at,action.start_date)+' 至 '+displayTime(action.end_at,action.end_date)));
     for(const key of ['scope','rules','quantity_limit','end_condition'])if(action[key])item.append(node('small',' · '+action[key]));
     details.append(item);
   }li.append(details);
   list.append(li);
 } if(!list.children.length)list.append(node('li','暂无符合条件的活动'));
}
search.addEventListener('input',render);
fetch('data/active.json').then(r=>{if(!r.ok)throw Error();return r.json();}).then(data=>{
 cards=data.cards;document.getElementById('generated-at').textContent='更新时间：'+displayTime(data.generated_at);render();
}).catch(()=>{list.textContent='活动加载失败，请刷新重试';});
// Details use event.html?id=<campaign>#activity-<activity>.
"""

TODAY_HTML = r"""<!doctype html>
<html lang="zh-CN">
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>每日提醒 · HERALD</title><link rel="stylesheet" href="style.css"></head>
<body><main><nav><a href="index.html">活动卡片</a><a href="today.html">每日提醒</a></nav><h1>每日提醒</h1><label>查看日期 <select id="history-date" aria-label="选择提醒日期"></select></label><p id="day"></p><h2>今日新消息</h2><div id="news"></div><h2>即将开始或结束</h2><div id="upcoming"></div></main><script src="today.js"></script></body>
</html>
"""

TODAY_JS = r"""function displayTime(at, day) {
  return at ? new Date(at).toLocaleString('zh-CN', {timeZone:'Asia/Shanghai',hour12:false,year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit'}) : day ? day + '（时刻待公布）' : '待公布';
}
function node(tag, text) { const el=document.createElement(tag); if(text !== undefined) el.textContent=text; return el; }
function sourceLabel(s) { return s.summary || (s.excerpt || '').split('\n')[0].slice(0,100) || s.account_name; }
function sourceLinks(parent, sources) {
  const list=node('ul'); const seen=new Set();
  for(const s of sources || []) { if(seen.has(s.url))continue; seen.add(s.url);
    const li=node('li'), a=node('a',sourceLabel(s)); a.href=s.url;a.rel='noreferrer';li.append(a);list.append(li);
  } parent.append(list);
}
let loadVersion=0;
async function loadDay(day) {
 const version=++loadVersion;
 try {
  const response=await fetch('data/reminders/'+encodeURIComponent(day)+'.json',{cache:'no-store'});
  if(!response.ok)throw Error();
  const data=await response.json();if(version!==loadVersion)return;
  document.getElementById('day').textContent=data.date+' · 当日提醒记录';
  for(const [targetId,isNews] of [['news',true],['upcoming',false]]) {
   const target=document.getElementById(targetId);target.replaceChildren();
   for(const c of data.cards) {
    const reasons=c.reasons.filter(r=>['announcement','update'].includes(r.kind)===isNews);
    if(!reasons.length)continue;
    const section=node('section'),h=node('h3');
    const title=c.activity?c.activity.title:c.campaign_title;
    if(c.detail_url){const link=node('a',title);link.href=c.detail_url;h.append(link);}else h.textContent=title;
    section.append(h,node('p',c.campaign_title));
    for(const r of reasons)section.append(node('p',r.summary+(r.expected_at?' · '+displayTime(r.date_only?null:r.expected_at,r.date_only):'')));
    if(!c.detail_url)section.append(node('small','详情已下线，请参考原帖'));
    sourceLinks(section,c.sources);target.append(section);
   }
   if(!target.children.length)target.textContent=isNews?'当日暂无新消息':'当日暂无开始或结束提醒';
  }
 } catch(error){if(version===loadVersion){document.getElementById('news').textContent='提醒加载失败，请重试';document.getElementById('upcoming').replaceChildren();}}
}
const dates=document.getElementById('history-date');
dates.addEventListener('change',()=>loadDay(dates.value));
fetch('data/reminders/index.json',{cache:'no-store'}).then(r=>{if(!r.ok)throw Error();return r.json();}).then(data=>{
 for(const day of data.dates){const option=node('option',day);option.value=day;dates.append(option);}
 if(data.dates.length)loadDay(data.dates[0]);
 else document.getElementById('news').textContent='暂无提醒记录';
}).catch(()=>{document.getElementById('news').textContent='提醒加载失败，请刷新重试';});
"""

DETAIL_HTML = r"""<!doctype html>
<html lang="zh-CN">
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>企划详情 · HERALD</title><link rel="stylesheet" href="style.css"></head>
<body><main><nav><a href="index.html">活动卡片</a><a href="today.html">每日提醒</a></nav><h1 id="title">企划详情</h1><dl id="summary"></dl><h2>活动</h2><div id="activities"></div><h2>原帖与配图</h2><div id="sources"></div></main><script src="event.js"></script></body>
</html>
"""

DETAIL_JS = r"""function displayTime(at, day) {
  return at ? new Date(at).toLocaleString('zh-CN', {timeZone:'Asia/Shanghai',hour12:false,year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit'}) : day ? day + '（时刻待公布）' : '待公布';
}
function node(tag, text) { const el=document.createElement(tag); if(text !== undefined) el.textContent=text; return el; }
function sourceLabel(s) { return s.summary || (s.excerpt || '').split('\n')[0].slice(0,100) || s.account_name; }
function sourceLinks(parent, sources) {
  const list=node('ul'); const seen=new Set();
  for(const s of sources || []) { if(seen.has(s.url))continue; seen.add(s.url);
    const li=node('li'), a=node('a',sourceLabel(s)); a.href=s.url;a.rel='noreferrer';li.append(a);list.append(li);
  } parent.append(list);
}
const eventId=new URLSearchParams(location.search).get('id')||'';
function render(data){
 document.getElementById('title').textContent=data.title;document.title=data.title;
 const summary=document.getElementById('summary');
 for(const [key,value] of [['IP',data.ip_name],['合作方',data.partner],['公布时间',displayTime(data.announced_at)]])if(value)summary.append(node('dt',key),node('dd',value));
 const activities=document.getElementById('activities');
 for(const a of data.activities){
  const section=node('section');section.id='activity-'+a.id;
  section.append(node('h3',a.title),node('p',displayTime(a.start_at,a.start_date)+' 至 '+displayTime(a.end_at,a.end_date)));
  for(const v of a.venues)section.append(node('p',v.online_platform||[v.province,v.city,v.name,v.address,v.business_hours].filter(Boolean).join(' · ')));
  if(a.rules)section.append(node('p',a.rules));
  const list=node('ul');
  for(const action of a.actions){
   if(action.cancelled)continue;
   const li=node('li',action.title+' · '+displayTime(action.at,action.start_date)+(action.end_at||action.end_date?' 至 '+displayTime(action.end_at,action.end_date):''));
   if(action.platform)li.append(' · '+action.platform);
   if(action.requires_reservation===true)li.append(' · 需预约');
   if(action.requires_rush===true)li.append(' · 需抢购或抢名额');
   if(action.rules)li.append(node('p',action.rules));
   li.id='action-'+action.id;
   if(action.ended)li.append(node('p','已结束'));
   for(const key of ['scope','quantity_limit','end_condition'])if(action[key])li.append(node('p',action[key]));
   if(action.url){const link=node('a','预约 / 购买入口');link.href=action.url;link.rel='noreferrer';li.append(link);}
   list.append(li);
  }section.append(list);
  if(a.related_offers)section.append(node('h4','关联优惠'),node('p',a.related_offers));
  if(a.uncertainties?.length)section.append(node('p','待确认：'+a.uncertainties.join('；')));
  activities.append(section);
 }
 const sources=document.getElementById('sources');
 for(const s of data.sources){
  const section=node('section'),link=node('a',sourceLabel(s));link.href=s.url;link.rel='noreferrer';
  section.append(link,node('p',s.account_name+' · '+displayTime(s.published_at)));
  const pictures=(data.media||[]).filter(m=>m.source_id===s.id);
  for(const m of pictures){
   const figure=node('figure');
   if(m.asset_path){const img=document.createElement('img');img.src=m.asset_path;img.alt='原帖配图';img.loading='lazy';figure.append(img);}
   const a=node('a','查看原图');a.href=m.source_url;a.rel='noreferrer';figure.append(a);section.append(figure);
  }sources.append(section);
 }
 const anchor=document.getElementById(decodeURIComponent(location.hash.slice(1)));
 if(anchor)anchor.scrollIntoView();
}
if(!/^[A-Za-z0-9][A-Za-z0-9._-]{0,180}$/.test(eventId))document.getElementById('title').textContent='无效的企划编号';
else fetch('events/'+encodeURIComponent(eventId)+'.json').then(r=>{if(!r.ok)throw Error();return r.json();}).then(render).catch(()=>{document.getElementById('title').textContent='企划不存在或已经过期';});
"""


class StaticSiteBuilder:
    def __init__(self, timezone_name: str = "Asia/Shanghai") -> None:
        try:
            self.timezone = ZoneInfo(timezone_name)
        except ZoneInfoNotFoundError:
            self.timezone = ScheduleCompiler(timezone_name).timezone
        self.reachability = ReachabilityClassifier()

    def build(
        self,
        *,
        store: StateStore,
        output_dir: Path | str,
        now: datetime,
        watched_ip_slugs: set[str],
        origin_city: str | None = None,
        reachable_cities: list[str] | None = None,
        forbidden_values: list[str] | None = None,
        media_assets: dict[str, CachedMediaAsset] | None = None,
    ) -> list[Campaign]:
        output = Path(output_dir)
        data_dir = output / "data"
        event_dir = output / "events"
        calendar_dir = data_dir / "calendar"
        for directory in (output, data_dir, event_dir, calendar_dir):
            directory.mkdir(parents=True, exist_ok=True)

        # The page branch is a generated view, not an archive. Remove only files
        # owned by this builder so expired event detail URLs cannot linger.
        self._remove_json_files(event_dir)
        self._remove_json_files(calendar_dir)

        campaigns = [
            campaign
            for campaign in store.list_campaigns()
            if campaign.ip_slug in watched_ip_slugs and campaign.is_visible(now)
        ]
        campaigns = [c.model_copy(update={'activities': [a for a in c.activities if a.is_visible(now)]}) for c in campaigns]
        campaigns.sort(key=lambda item: (item.updated_at, item.id), reverse=True)
        notification_service = NotificationService(str(self.timezone))
        due, _ = notification_service.collect_due(
            store, now.astimezone(self.timezone).date(), include_delivered=True
        )
        visible_ids = {campaign.id for campaign in campaigns}
        self._write_json(data_dir / "today.json", {
            "date": now.astimezone(self.timezone).date().isoformat(),
            "cards": notification_service.build_cards([
                item for item in due if item.campaign.id in visible_ids and
                (not item.job.activity_id or any(a.id == item.job.activity_id and a.is_visible(now) for a in item.campaign.activities))
            ]),
        })
        self._publish_reminder_history(store, data_dir, now, campaigns,
                                       watched_ip_slugs, notification_service, forbidden_values or [])
        reachable = reachable_cities or []
        summaries = [
            self._campaign_summary(
                campaign,
                now=now,
                origin_city=origin_city,
                reachable_cities=reachable,
            )
            for campaign in campaigns
        ]
        self._write_json(
            data_dir / "active.json",
            {
                "generated_at": now.astimezone(self.timezone).isoformat(),
                "campaigns": summaries,
                "cards": self._activity_cards(campaigns, now),
            },
        )

        for campaign in campaigns:
            detail = campaign.model_dump(mode="json")
            detail["media"] = self._campaign_media(
                campaign, media_assets or {}
            )
            detail["reachability"] = {
                activity.id: self.reachability.classify(
                    activity,
                    origin_city=origin_city,
                    reachable_cities=reachable,
                ).value
                for activity in campaign.activities
            }
            self._write_json(event_dir / f"{campaign.id}.json", detail)

        for month, days in self._calendar(campaigns, now).items():
            self._write_json(calendar_dir / f"{month}.json", {"days": days})

        (output / "index.html").write_text(INDEX_HTML, encoding="utf-8")
        (output / "app.js").write_text(APP_JS, encoding="utf-8")
        (output / "event.html").write_text(DETAIL_HTML, encoding="utf-8")
        (output / "event.js").write_text(DETAIL_JS, encoding="utf-8")
        (output / "today.html").write_text(TODAY_HTML, encoding="utf-8")
        (output / "today.js").write_text(TODAY_JS, encoding="utf-8")
        (output / "style.css").write_text(STYLE_CSS, encoding="utf-8")
        (output / ".nojekyll").write_text("", encoding="utf-8")
        self._scan_forbidden(output, forbidden_values or [])
        return campaigns

    def _publish_reminder_history(self, store, data_dir, now, campaigns,
                                  watched_slugs, service, forbidden_values):
        day = now.astimezone(self.timezone).date()
        due, _ = service.collect_due(store, day, include_delivered=True)
        old = store.load_reminder_snapshot(day) or {'date': str(day), 'cards': []}
        cards = {(c['campaign_id'], c['activity_id']): c for c in old['cards']}
        for card in service.build_cards(due):
            key = (card['campaign_id'], card['activity_id'])
            previous = cards.get(key)
            if previous:
                card['reasons'] = previous['reasons'] + [r for r in card['reasons'] if r not in previous['reasons']]
            cards[key] = card
        snapshot = {'date': str(day), 'cards': list(cards.values())}
        serialized = json.dumps(snapshot, ensure_ascii=False)
        if any(value in serialized for value in forbidden_values if len(value) >= 8):
            raise ValueError('sensitive value detected in reminder snapshot')
        store.save_reminder_snapshot(day, snapshot)
        store.prune_reminders(day)
        directory = data_dir / 'reminders'
        directory.mkdir(exist_ok=True)
        self._remove_json_files(directory)
        visible = {c.id: {a.id for a in c.activities} for c in campaigns}
        watched_ids = {c.id for c in store.list_campaigns() if c.ip_slug in watched_slugs}
        dates = []
        for saved in store.list_reminder_snapshots(day):
            published = []
            for card in saved['cards']:
                if card['campaign_id'] not in watched_ids:
                    continue
                cid, aid = card['campaign_id'], card['activity_id']
                url = None
                if cid in visible and (not aid or aid in visible[cid]):
                    url = f'event.html?id={cid}' + (f'#activity-{aid}' if aid else '')
                published.append({
                    'campaign_id': cid, 'activity_id': aid,
                    'campaign_title': card['campaign_title'], 'ip_name': card['ip_name'],
                    'activity': {'title': card['activity']['title']} if card['activity'] else None,
                    'reasons': card['reasons'], 'detail_url': url,
                    'sources': [{k: source.get(k) for k in ('url', 'summary', 'account_name')}
                                for source in card['sources']],
                })
            dates.append(saved['date'])
            self._write_json(directory / f"{saved['date']}.json", {'date': saved['date'], 'cards': published})
        self._write_json(directory / 'index.json', {'dates': dates})

    def _activity_cards(self, campaigns, now):
        cards = []
        for campaign in campaigns:
            for activity in campaign.activities:
                points = sorted((p for p in schedule_actions(activity, self.timezone)
                    if p.at >= now or (p.start_date and p.start_date == now.astimezone(self.timezone).date())), key=lambda p: p.at)
                cards.append({'campaign_id': campaign.id, 'campaign_title': campaign.title,
                    'ip_name': campaign.ip_name, 'activity': activity.model_dump(mode='json'),
                    'next': {'title': points[0].title, 'at': points[0].at.isoformat(),
                             'date': str(points[0].start_date) if points[0].start_date else None} if points else None,
                    'url': f'event.html?id={campaign.id}#activity-{activity.id}'})
        return sorted(cards, key=lambda c: (c['next']['at'] if c['next'] else '9999', c['activity']['id']))

    @staticmethod
    def _campaign_media(
        campaign: Campaign,
        media_assets: dict[str, CachedMediaAsset],
    ) -> list[dict[str, object]]:
        items: list[dict[str, object]] = []
        seen: set[tuple[str, str]] = set()
        for source in campaign.sources:
            for index, media_url in enumerate(source.media_urls):
                url = str(media_url)
                key = (source.id, url)
                if key in seen:
                    continue
                seen.add(key)
                item: dict[str, object] = {
                    "source_id": source.id,
                    "account_name": source.account_name,
                    "source_url": url,
                    "source_media_hash": (
                        source.media_hashes[index]
                        if index < len(source.media_hashes)
                        else None
                    ),
                    "asset_path": None,
                    "sha256": None,
                    "content_type": None,
                    "size_bytes": None,
                }
                asset = media_assets.get(url)
                if asset is not None:
                    item.update(
                        {
                            "asset_path": asset.asset_path,
                            "sha256": asset.sha256,
                            "content_type": asset.content_type,
                            "size_bytes": asset.size_bytes,
                        }
                    )
                items.append(item)
        return items

    def _campaign_summary(
        self,
        campaign: Campaign,
        *,
        now: datetime,
        origin_city: str | None,
        reachable_cities: list[str],
    ) -> dict[str, object]:
        return {
            "id": campaign.id,
            "ip_slug": campaign.ip_slug,
            "ip_name": campaign.ip_name,
            "partner": campaign.partner,
            "title": campaign.title,
            "status": campaign.status.value,
            "updated_at": campaign.updated_at.isoformat(),
            "activities": [
                {
                    "id": activity.id,
                    "kind": activity.kind.value,
                    "title": activity.title,
                    "status": activity.status.value,
                    "start_at": activity.start_at.isoformat() if activity.start_at else None,
                    "end_at": activity.end_at.isoformat() if activity.end_at else None,
                    "reachability": self.reachability.classify(
                        activity,
                        origin_city=origin_city,
                        reachable_cities=reachable_cities,
                    ).value,
                }
                for activity in campaign.activities
                if activity.is_visible(now)
            ],
        }

    def _calendar(
        self, campaigns: list[Campaign], now: datetime
    ) -> dict[str, dict[str, list[dict[str, object]]]]:
        local_today = now.astimezone(self.timezone).date()
        months: dict[str, dict[str, list[dict[str, object]]]] = defaultdict(
            lambda: defaultdict(list)
        )
        seen: set[tuple[str, str, str]] = set()
        for campaign in campaigns:
            for activity in campaign.activities:
                if not activity.is_visible(now):
                    continue
                for action in schedule_actions(activity, self.timezone):
                    if action.cancelled or action.at is None:
                        continue
                    local_action = action.at.astimezone(self.timezone)
                    if local_action.date() < local_today:
                        continue
                    key = (campaign.id, activity.id, action.id)
                    if key in seen:
                        continue
                    seen.add(key)
                    month = local_action.strftime("%Y-%m")
                    day = local_action.strftime("%Y-%m-%d")
                    months[month][day].append(
                        {
                            "campaign_id": campaign.id,
                            "activity_id": activity.id,
                            "action_id": action.id,
                            "ip_name": campaign.ip_name,
                            "title": action.title,
                            "kind": action.kind.value,
                            "at": local_action.isoformat(),
                        }
                    )
        return {
            month: {day: items for day, items in sorted(days.items())}
            for month, days in sorted(months.items())
        }

    @staticmethod
    def _write_json(path: Path, payload: object) -> None:
        path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    @staticmethod
    def _remove_json_files(directory: Path) -> None:
        for path in directory.glob("*.json"):
            if path.is_file():
                path.unlink()

    @staticmethod
    def _scan_forbidden(output: Path, forbidden_values: list[str]) -> None:
        high_risk_values = [
            value.encode("utf-8")
            for value in forbidden_values
            if len(value) >= 8
        ]
        for path in output.rglob("*"):
            if not path.is_file():
                continue
            content = path.read_bytes()
            for value in high_risk_values:
                if value in content:
                    raise ValueError(f"sensitive value detected in generated site: {path.name}")
