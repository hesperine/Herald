"""Built-in IP and official-source registry."""

from __future__ import annotations

import json
from importlib.resources import files

from pydantic import BaseModel, ConfigDict, Field

from .config import PublicSettings
from .models import SourceKind


class RegisteredSource(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: SourceKind
    account_name: str
    account_id: str
    url: str


class RegisteredIp(BaseModel):
    model_config = ConfigDict(extra="forbid")

    slug: str
    name: str
    aliases: list[str] = Field(default_factory=list)
    sources: list[RegisteredSource] = Field(default_factory=list)


class RegistryResolution(BaseModel):
    model_config = ConfigDict(extra="forbid")

    supported: list[RegisteredIp] = Field(default_factory=list)
    unsupported: list[str] = Field(default_factory=list)


class IpRegistry:
    def __init__(self, entries: list[RegisteredIp]) -> None:
        self.entries = entries
        self._by_name: dict[str, RegisteredIp] = {}
        for entry in entries:
            for name in {entry.name, entry.slug, *entry.aliases}:
                self._by_name[name.casefold()] = entry

    @classmethod
    def load_builtin(cls) -> "IpRegistry":
        path = files("herald.data").joinpath("ip_sources.json")
        raw = json.loads(path.read_text(encoding="utf-8"))
        return cls([RegisteredIp.model_validate(item) for item in raw])

    def resolve(self, settings: PublicSettings) -> RegistryResolution:
        supported: list[RegisteredIp] = []
        unsupported: list[str] = []
        seen_slugs: set[str] = set()

        for requested_name in settings.watched_ips:
            entry = self._by_name.get(requested_name.casefold())
            if entry is None:
                unsupported.append(requested_name)
                continue
            if entry.slug in seen_slugs:
                continue
            seen_slugs.add(entry.slug)
            supported.append(entry.model_copy(deep=True))

        extras_by_entry: dict[str, list[str]] = {}
        for configured_name, uids in settings.extra_weibo_uids.items():
            entry = self._by_name.get(configured_name.casefold())
            if entry is None or entry.slug not in seen_slugs:
                unsupported.append(configured_name)
                continue
            extras_by_entry.setdefault(entry.slug, []).extend(uids)

        for entry in supported:
            existing = {source.account_id for source in entry.sources}
            for uid in extras_by_entry.get(entry.slug, []):
                if uid in existing:
                    continue
                entry.sources.append(
                    RegisteredSource(
                        kind=SourceKind.WEIBO,
                        account_name=f"{entry.name}（用户补充）",
                        account_id=uid,
                        url=f"https://weibo.com/u/{uid}",
                    )
                )
                existing.add(uid)

        return RegistryResolution(supported=supported, unsupported=sorted(set(unsupported)))
