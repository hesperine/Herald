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
from .reachability import ReachabilityClassifier
from .scheduler import ScheduleCompiler
from .storage import StateStore


INDEX_HTML = """<main>
  <h1>游戏联动提醒</h1>
  <p id="generated-at"></p>
  <label>搜索 <input id="search" type="search"></label>
  <ul id="events"></ul>
</main>
<script src="app.js"></script>
"""


APP_JS = """const list = document.getElementById('events');
const search = document.getElementById('search');
let campaigns = [];
function render() {
  const query = search.value.trim().toLowerCase();
  list.replaceChildren();
  for (const campaign of campaigns.filter(item => JSON.stringify(item).toLowerCase().includes(query))) {
    const li = document.createElement('li');
    const link = document.createElement('a');
    link.href = `event.html?id=${encodeURIComponent(campaign.id)}`;
    link.textContent = `${campaign.ip_name} · ${campaign.title}`;
    li.append(link);
    list.append(li);
  }
}
fetch('data/active.json').then(response => response.json()).then(data => {
  campaigns = data.campaigns;
  document.getElementById('generated-at').textContent = `更新时间：${data.generated_at}`;
  render();
});
search.addEventListener('input', render);
"""


DETAIL_HTML = """<main>
  <p><a href="index.html">返回全部联动</a></p>
  <h1 id="title">活动详情</h1>
  <dl id="summary"></dl>
  <h2>子活动</h2>
  <div id="activities"></div>
  <h2>公告图片</h2>
  <div id="media"></div>
  <h2>信息来源</h2>
  <ul id="sources"></ul>
</main>
<script src="event.js"></script>
"""


DETAIL_JS = """const params = new URLSearchParams(location.search);
const eventId = params.get('id') || '';
const title = document.getElementById('title');
const summary = document.getElementById('summary');
const activities = document.getElementById('activities');
const media = document.getElementById('media');
const sources = document.getElementById('sources');
function addDefinition(term, value) {
  if (value === null || value === undefined || value === '') return;
  const dt = document.createElement('dt'); dt.textContent = term;
  const dd = document.createElement('dd'); dd.textContent = String(value);
  summary.append(dt, dd);
}
function localTime(value) {
  return value ? new Date(value).toLocaleString('zh-CN', {hour12: false}) : '待公布';
}
function yesNo(value) {
  return value === true ? '是' : value === false ? '否' : '待确认';
}
function venueText(venue) {
  if (venue.online_platform) return `线上：${venue.online_platform}`;
  if (venue.nationwide) return '全国范围';
  return [venue.province, venue.city, venue.name, venue.address].filter(Boolean).join(' · ') || '地点待公布';
}
function render(data) {
  title.textContent = data.title;
  document.title = data.title;
  addDefinition('IP', data.ip_name);
  addDefinition('合作品牌', data.partner || '待确认');
  addDefinition('状态', data.status);
  addDefinition('公布时间', localTime(data.announced_at));
  for (const activity of data.activities) {
    const section = document.createElement('section');
    const heading = document.createElement('h3'); heading.textContent = activity.title;
    const info = document.createElement('p');
    info.textContent = `类型：${activity.kind}；日期：${localTime(activity.start_at)} 至 ${localTime(activity.end_at)}；可达性：${data.reachability[activity.id] || 'unknown'}`;
    section.append(heading, info);
    const venueList = document.createElement('ul');
    for (const venue of activity.venues) {
      const li = document.createElement('li'); li.textContent = venueText(venue); venueList.append(li);
    }
    if (activity.venues.length) section.append(venueList);
    const actionList = document.createElement('ul');
    for (const action of activity.actions) {
      const li = document.createElement('li');
      li.textContent = `${action.title}｜${localTime(action.at)} 至 ${localTime(action.end_at)}｜预约：${yesNo(action.requires_reservation)}｜抢购/抢号：${yesNo(action.requires_rush)}`;
      if (action.platform) li.append(`｜平台：${action.platform}`);
      if (action.url) {
        const link = document.createElement('a'); link.href = action.url; link.textContent = ' 操作链接'; link.rel = 'noreferrer'; li.append(link);
      }
      actionList.append(li);
    }
    if (activity.actions.length) section.append(actionList);
    activities.append(section);
  }
  for (const item of data.media || []) {
    const figure = document.createElement('figure');
    if (item.asset_path) {
      const image = document.createElement('img');
      image.src = item.asset_path;
      image.alt = `${item.account_name} 公告图片`;
      image.loading = 'lazy';
      figure.append(image);
    }
    const caption = document.createElement('figcaption');
    caption.append(`${item.account_name} · `);
    const original = document.createElement('a');
    original.href = item.source_url;
    original.textContent = '查看原图';
    original.rel = 'noreferrer';
    caption.append(original);
    figure.append(caption);
    media.append(figure);
  }
  for (const source of data.sources) {
    const li = document.createElement('li');
    const link = document.createElement('a'); link.href = source.url; link.textContent = `${source.account_name} · ${localTime(source.published_at)}`; link.rel = 'noreferrer';
    li.append(link); sources.append(li);
  }
}
if (!/^[A-Za-z0-9][A-Za-z0-9._-]{0,180}$/.test(eventId)) {
  title.textContent = '无效的活动编号';
} else {
  fetch(`events/${encodeURIComponent(eventId)}.json`).then(response => {
    if (!response.ok) throw new Error('not found');
    return response.json();
  }).then(render).catch(() => { title.textContent = '活动不存在或已经过期'; });
}
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
        campaigns.sort(key=lambda item: (item.updated_at, item.id), reverse=True)
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
        (output / ".nojekyll").write_text("", encoding="utf-8")
        self._scan_forbidden(output, forbidden_values or [])
        return campaigns

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
                for action in activity.actions:
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
