"""Shared source adapter contracts."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Generic, TypeVar

from herald.models import SourceObservation


CursorT = TypeVar("CursorT")


class SourceAccessError(RuntimeError):
    """A redacted source failure safe to include in run reports."""


@dataclass(frozen=True, slots=True)
class FetchedObservation:
    observation: SourceObservation
    media_urls: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class FetchBatch(Generic[CursorT]):
    items: tuple[FetchedObservation, ...]
    cursor: CursorT
