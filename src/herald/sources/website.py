"""Cookie-free Chinese official news lists and complete announcement text."""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from html.parser import HTMLParser
from zoneinfo import ZoneInfo

import httpx
from pydantic import BaseModel, ConfigDict

from herald.models import SourceKind, SourceObservation, SourceRef
from herald.sources.base import FetchBatch, FetchedObservation, SourceAccessError


HOYO_SITES = {
    "genshin-impact-official": (
        "https://act-api-takumi-static.mihoyo.com/content_v2_user/app/16471662a82d418a", "719",
        "https://ys.mihoyo.com/main/news/detail/",
    ),
    "honkai-star-rail-official": (
        "https://act-api-takumi-static.mihoyo.com/content_v2_user/app/1963de8dc19e461c", "255",
        "https://sr.mihoyo.com/news/",
    ),
    "zenless-zone-zero-official": (
        "https://api-takumi-static.mihoyo.com/content_v2_user/app/706fd13a87294881", "273",
        "https://zzz.mihoyo.com/news/",
    ),
}
ARKNIGHTS_LIST = "https://ak.hypergryph.com/api/news"
CHINA_TIME = ZoneInfo("Asia/Shanghai")


class WebsiteCursor(BaseModel):
    model_config = ConfigDict(extra="forbid")

    latest_post_id: str | None = None


class _MaterialParser(HTMLParser):
    BLOCKS = {"p", "div", "li", "h1", "h2", "h3", "h4", "h5", "h6", "br"}
    PARAGRAPHS = BLOCKS - {"div", "br"}
    IGNORED = {"script", "style", "nav", "header", "footer", "noscript", "iframe"}
    VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}

    def __init__(self, *, paragraphs_only=False):
        super().__init__()
        self.paragraphs_only = paragraphs_only
        self.stack: list[str] = []
        self.parts: list[str] = []
        self.images: list[str] = []
        self.links: list[str] = []

    def _visible(self):
        return not self.IGNORED.intersection(self.stack)

    def _capture_text(self):
        return self._visible() and (not self.paragraphs_only or bool(self.PARAGRAPHS.intersection(self.stack)))

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag not in self.VOID:
            self.stack.append(tag)
        if not self._visible():
            return
        if tag in self.BLOCKS:
            self.parts.append("\n")
        if tag == "img":
            url = attrs.get("src") or ""
            if url.startswith(("https://", "http://")) and (
                not self.paragraphs_only or url.startswith("https://web.hycdn.cn/upload/image/")
            ):
                self.images.append(url)
        if tag == "a" and self._capture_text():
            url = attrs.get("href") or ""
            if url.startswith(("https://", "http://")):
                self.links.append(url)

    def handle_endtag(self, tag):
        if self._visible() and tag in self.BLOCKS:
            self.parts.append("\n")
        for index in range(len(self.stack)-1, -1, -1):
            if self.stack[index] == tag:
                del self.stack[index:]
                break

    def handle_data(self, data):
        if self._capture_text():
            self.parts.append(data)

    def text(self):
        return "\n".join(line.strip() for line in "".join(self.parts).replace("\xa0", " ").splitlines() if line.strip())


def _published(item, arknights):
    try:
        if arknights:
            return datetime.fromtimestamp(int(item["displayTime"]), tz=timezone.utc)
        value = datetime.fromisoformat(item["dtStartTime"])
        return value if value.tzinfo is not None else value.replace(tzinfo=CHINA_TIME)
    except (KeyError, TypeError, ValueError, OSError) as exc:
        raise SourceAccessError("Official news publication time was unavailable") from exc


class WebsiteTimelineClient:
    def __init__(self, client: httpx.AsyncClient):
        self.client = client

    async def _get(self, url, params=None):
        try:
            response = await self.client.get(url, params=params)
            response.raise_for_status()
            return response
        except httpx.HTTPError as exc:
            raise SourceAccessError("Official news request failed") from exc

    async def _json(self, url, params, *, arknights=False):
        response = await self._get(url, params)
        try:
            payload = response.json()
            if not isinstance(payload, dict) or payload.get("code" if arknights else "retcode") != 0:
                raise ValueError
            data = payload.get("data")
            if not isinstance(data, dict):
                raise ValueError
            return data
        except ValueError as exc:
            raise SourceAccessError("Official news returned an unsupported response") from exc

    async def fetch_account(self, *, account_id, account_name, cursor, first_seen_at,
                            published_since, max_pages=6, published_before=None):
        if account_id not in HOYO_SITES and account_id != "arknights-official":
            raise ValueError("unknown official news source")
        if published_since.tzinfo is None or published_since.utcoffset() is None:
            raise ValueError("published_since must include a timezone")
        if max_pages < 1:
            raise ValueError("max_pages must be at least 1")
        if published_before is not None and (published_before.tzinfo is None or published_before <= published_since):
            raise ValueError("published_before must be aware and after published_since")
        self.last_fetch_complete = False
        arknights = account_id == "arknights-official"
        boundary = (cursor or WebsiteCursor()).latest_post_id
        seen: set[str] = set()
        encountered = []
        fetched = []
        for page in range(1, max_pages+1):
            if arknights:
                data = await self._json(ARKNIGHTS_LIST, {"category": "LATEST", "page": str(page)}, arknights=True)
            else:
                api, channel, _ = HOYO_SITES[account_id]
                data = await self._json(api+"/getContentList", {
                    "iChanId": channel, "iPageSize": "20", "iPage": str(page), "sLangKey": "zh-cn"})
            items = data.get("list")
            if not isinstance(items, list):
                raise SourceAccessError("Official news list was unavailable")
            try:
                if arknights:
                    if not isinstance(data.get("end"), bool):
                        raise ValueError
                    ended = data["end"]
                else:
                    total = int(data["iTotal"])
                    if total < 0:
                        raise ValueError
                    ended = page*20 >= total
            except (KeyError, TypeError, ValueError) as exc:
                raise SourceAccessError("Official news pagination was unavailable") from exc
            if not items:
                self.last_fetch_complete = True
                break
            dates = []
            reached_boundary = False
            for item in items:
                if not isinstance(item, dict):
                    raise SourceAccessError("Official news item was unavailable")
                identifier = str(item.get("cid" if arknights else "iInfoId") or "")
                if not identifier.isdecimal():
                    raise SourceAccessError("Official news identifier was unavailable")
                if identifier in seen:
                    continue
                seen.add(identifier)
                published_at = _published(item, arknights)
                encountered.append((identifier, published_at))
                dates.append(published_at)
                if (published_at >= published_since or identifier == boundary) and (
                    published_before is None or published_at < published_before
                ):
                    fetched.append(await self._fetch_detail(item=item, account_id=account_id,
                        account_name=account_name, first_seen_at=first_seen_at))
                if identifier == boundary:
                    reached_boundary = True
                    break
            if reached_boundary or (dates and all(value < published_since for value in dates)):
                self.last_fetch_complete = True
                break
            if ended:
                self.last_fetch_complete = True
                break
            if not dates:
                break
        newest = max(encountered, key=lambda value: value[1], default=None)
        fetched.sort(key=lambda item: (item.observation.source.published_at, item.observation.id), reverse=True)
        return FetchBatch(items=tuple(fetched), cursor=WebsiteCursor(
            latest_post_id=newest[0] if newest else boundary))

    async def _fetch_detail(self, *, item, account_id, account_name, first_seen_at):
        arknights = account_id == "arknights-official"
        identifier = str(item["cid" if arknights else "iInfoId"])
        if arknights:
            url = f"https://ak.hypergryph.com/news/{identifier}.html"
            response = await self._get(url)
            html = response.text
            title = item.get("title", "")
            published_at = _published(item, True)
        else:
            api, _, article_base = HOYO_SITES[account_id]
            detail = await self._json(api+"/getContent", {"iInfoId": identifier, "sLangKey": "zh-cn"})
            if str(detail.get("iInfoId")) != identifier:
                raise SourceAccessError("Official news detail did not match its identifier")
            url = article_base+identifier
            html = detail.get("sContent") or ""
            title = detail.get("sTitle") or item.get("sTitle", "")
            published_at = _published(detail, False)
        if not isinstance(html, str) or not isinstance(title, str) or not title.strip():
            raise SourceAccessError("Official news content was unavailable")
        parser = _MaterialParser(paragraphs_only=arknights)
        parser.feed(html)
        text = "\n".join(part for part in (title.strip(), parser.text()) if part)
        images = list(dict.fromkeys(parser.images))
        source_id = f"website-{account_id}-{identifier}"
        content_hash = hashlib.sha256("\n".join([text, *images, *parser.links]).encode()).hexdigest()
        observation = SourceObservation(id=source_id, source=SourceRef(
            id=source_id, kind=SourceKind.WEBSITE, account_id=account_id, account_name=account_name,
            url=url, canonical_content_id=identifier, published_at=published_at,
            first_seen_at=first_seen_at, content_hash=content_hash, excerpt=text[:500]),
            text=text, media_urls=images, media_hashes=[hashlib.sha256(url.encode()).hexdigest() for url in images],
            outbound_urls=list(dict.fromkeys(parser.links)))
        return FetchedObservation(observation=observation, media_urls=tuple(images))
