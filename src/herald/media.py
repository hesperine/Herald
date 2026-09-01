"""Download public source images into the generated static page branch."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

import httpx
from pydantic import BaseModel, ConfigDict, Field, HttpUrl


MAX_MEDIA_BYTES = 10 * 1024 * 1024
MEDIA_EXTENSIONS = {
    "image/avif": ".avif",
    "image/gif": ".gif",
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
}


class CachedMediaAsset(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_url: HttpUrl
    asset_path: str
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    content_type: str
    size_bytes: int = Field(gt=0, le=MAX_MEDIA_BYTES)


@dataclass(frozen=True, slots=True)
class MediaCacheResult:
    assets: dict[str, CachedMediaAsset]
    failed_count: int


class PublicMediaCache:
    def __init__(self, client: httpx.AsyncClient) -> None:
        self.client = client

    async def cache(
        self,
        urls: list[str],
        output_dir: Path | str,
    ) -> MediaCacheResult:
        output = Path(output_dir)
        requested = list(dict.fromkeys(str(url) for url in urls))
        assets = self._load_existing(output, set(requested))
        failed_count = 0
        for url in requested:
            if url in assets:
                continue
            try:
                assets[url] = await self._download(url, output)
            except (httpx.HTTPError, OSError, ValueError):
                # URLs are public, but upstream bodies and local paths still do
                # not belong in run reports or exceptions.
                failed_count += 1

        self._prune_assets(output, assets)
        self._write_index(output, assets)
        return MediaCacheResult(assets=assets, failed_count=failed_count)

    async def _download(self, url: str, output: Path) -> CachedMediaAsset:
        headers = {
            "Accept": "image/avif,image/webp,image/png,image/jpeg,image/gif,*/*",
            "Referer": "https://m.weibo.cn/",
            "User-Agent": "Mozilla/5.0 HERALD/0.1",
        }
        chunks: list[bytes] = []
        size = 0
        async with self.client.stream(
            "GET", url, headers=headers, timeout=30
        ) as response:
            response.raise_for_status()
            content_type = (
                response.headers.get("Content-Type", "")
                .partition(";")[0]
                .strip()
                .lower()
            )
            extension = MEDIA_EXTENSIONS.get(content_type)
            if extension is None:
                raise ValueError("public media returned an unsupported type")
            declared_size = response.headers.get("Content-Length")
            if declared_size is not None:
                try:
                    if int(declared_size) > MAX_MEDIA_BYTES:
                        raise ValueError("public media exceeds the size limit")
                except ValueError as exc:
                    raise ValueError("public media has an invalid size") from exc
            async for chunk in response.aiter_bytes():
                size += len(chunk)
                if size > MAX_MEDIA_BYTES:
                    raise ValueError("public media exceeds the size limit")
                chunks.append(chunk)

        content = b"".join(chunks)
        if not content:
            raise ValueError("public media was empty")
        digest = hashlib.sha256(content).hexdigest()
        asset_dir = output / "assets" / "media"
        asset_dir.mkdir(parents=True, exist_ok=True)
        path = asset_dir / f"{digest}{extension}"
        if not path.is_file():
            temporary = asset_dir / f"{digest}{extension}.tmp"
            temporary.write_bytes(content)
            temporary.replace(path)
        return CachedMediaAsset(
            source_url=url,
            asset_path=path.relative_to(output).as_posix(),
            sha256=digest,
            content_type=content_type,
            size_bytes=size,
        )

    @staticmethod
    def _load_existing(
        output: Path, requested: set[str]
    ) -> dict[str, CachedMediaAsset]:
        index_path = output / "data" / "media-index.json"
        if not index_path.is_file():
            return {}
        try:
            payload = json.loads(index_path.read_text(encoding="utf-8"))
            raw_assets = payload.get("assets", [])
        except (OSError, ValueError, AttributeError):
            return {}
        if not isinstance(raw_assets, list):
            return {}

        media_root = (output / "assets" / "media").resolve()
        assets: dict[str, CachedMediaAsset] = {}
        for raw in raw_assets:
            try:
                asset = CachedMediaAsset.model_validate(raw)
                url = str(asset.source_url)
                path = (output / asset.asset_path).resolve()
            except (ValueError, TypeError):
                continue
            if (
                url in requested
                and path.is_relative_to(media_root)
                and path.is_file()
            ):
                try:
                    if path.stat().st_size != asset.size_bytes:
                        continue
                    content = path.read_bytes()
                except OSError:
                    continue
                if (
                    len(content) == asset.size_bytes
                    and hashlib.sha256(content).hexdigest() == asset.sha256
                ):
                    assets[url] = asset
        return assets

    @staticmethod
    def _prune_assets(
        output: Path, assets: dict[str, CachedMediaAsset]
    ) -> None:
        media_dir = output / "assets" / "media"
        if not media_dir.is_dir():
            return
        keep = {Path(asset.asset_path).name for asset in assets.values()}
        for path in media_dir.iterdir():
            if path.is_file() and path.name not in keep:
                path.unlink()

    @staticmethod
    def _write_index(
        output: Path, assets: dict[str, CachedMediaAsset]
    ) -> None:
        data_dir = output / "data"
        data_dir.mkdir(parents=True, exist_ok=True)
        index_path = data_dir / "media-index.json"
        payload = {
            "assets": [
                assets[url].model_dump(mode="json") for url in sorted(assets)
            ]
        }
        temporary = data_dir / "media-index.json.tmp"
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        temporary.replace(index_path)
