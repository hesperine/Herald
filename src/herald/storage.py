"""Filesystem-backed state storage for the dedicated ``state`` branch."""

from __future__ import annotations

import json
import re
import uuid
from datetime import date, timedelta
from pathlib import Path
from typing import TypeVar

from pydantic import BaseModel

from .models import (
    Campaign,
    ChangeRecord,
    NotificationReceipt,
    ObservationIndexRecord,
    PendingExtraction,
    PendingExtractionIndexRecord,
    PendingReview,
    QueueJob,
    RunReport,
    ScheduleManifest,
    SourceObservation,
)


ModelT = TypeVar("ModelT", bound=BaseModel)
SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,180}$")


class UnsafeStateId(ValueError):
    pass


def _require_safe_id(value: str) -> str:
    if not SAFE_ID.fullmatch(value):
        raise UnsafeStateId(f"unsafe state id: {value!r}")
    return value


def _date_directory(root: Path, day: date) -> Path:
    return root / f"{day:%Y}" / f"{day:%m}" / f"{day:%d}"


class StateStore:
    """Read and write normalized state without any database service.

    Every write is atomic within one filesystem. Date-bucketed data can be read
    directly for one run date without scanning historical files.
    """

    def __init__(self, root: Path | str) -> None:
        self.root = Path(root)

    def initialize(self) -> None:
        for name in (
            "events",
            "observations",
            "observation-index",
            "extractions",
            "daily",
            "queue",
            "notified",
            "schedules",
            "pending-extraction",
            "pending-extraction-index",
            "pending-review",
            "runs",
            "sources",
            "reminders",
        ):
            (self.root / name).mkdir(parents=True, exist_ok=True)

    def save_campaign(self, campaign: Campaign) -> Path:
        return self._save_model(
            self.root / "events" / f"{_require_safe_id(campaign.id)}.json",
            campaign,
        )

    def save_reminder_snapshot(self, day: date, payload: dict) -> Path:
        return self._atomic_json_write(self.root / 'reminders' / f'{day.isoformat()}.json', payload)

    def load_reminder_snapshot(self, day: date) -> dict | None:
        path = self.root / 'reminders' / f'{day.isoformat()}.json'
        return json.loads(path.read_text(encoding='utf-8')) if path.exists() else None

    def list_reminder_snapshots(self, today: date) -> list[dict]:
        return [snapshot for offset in range(7)
                if (snapshot := self.load_reminder_snapshot(today - timedelta(days=offset))) is not None]

    def prune_reminders(self, today: date) -> None:
        """Seven local calendar days; never remove facts, sources or future jobs."""
        cutoff = today - timedelta(days=6)
        root = self.root.resolve()
        for bucket in ('reminders', 'daily', 'queue', 'notified'):
            directory = self.root / bucket
            pattern = '*.json' if bucket == 'reminders' else '*/*/*/*.json'
            for path in directory.glob(pattern):
                if path.is_symlink() or not path.resolve().is_relative_to(root):
                    continue
                try:
                    day = date.fromisoformat(path.stem) if bucket == 'reminders' else date(*map(int, path.relative_to(directory).parts[:3]))
                except ValueError:
                    continue
                if day < cutoff:
                    if bucket == 'queue':
                        payload = json.loads(path.read_text(encoding='utf-8'))
                        if payload.get('kind') in ('announcement', 'update') and not any(
                            (self.root / 'notified').glob(f'*/*/*/{path.name}')
                        ):
                            continue
                    path.unlink()
                    parent = path.parent
                    while parent != directory and parent.is_dir() and not any(parent.iterdir()):
                        parent.rmdir()
                        parent = parent.parent

    def load_campaign(self, campaign_id: str) -> Campaign | None:
        path = self.root / "events" / f"{_require_safe_id(campaign_id)}.json"
        return self._load_optional(path, Campaign)

    def list_campaigns(self) -> list[Campaign]:
        directory = self.root / "events"
        if not directory.exists():
            return []
        return [self._load(path, Campaign) for path in sorted(directory.glob("*.json"))]

    def save_observation(self, observation: SourceObservation) -> Path:
        day = observation.source.first_seen_at.date()
        path = _date_directory(self.root / "observations", day)
        suffix = observation.source.content_hash[:12]
        saved = self._save_model(
            path / f"{_require_safe_id(observation.id)}--{suffix}.json", observation
        )
        self._save_model(
            self.root / "observation-index" / f"{_require_safe_id(observation.id)}.json",
            ObservationIndexRecord(
                observation_id=observation.id,
                content_hash=observation.source.content_hash,
                stored_day=day,
            ),
        )
        return saved

    def observation_exists(self, observation_id: str, first_seen_day: date) -> bool:
        record = self.load_observation_index(observation_id)
        return record is not None and record.stored_day == first_seen_day

    def load_observation_index(
        self, observation_id: str
    ) -> ObservationIndexRecord | None:
        path = self.root / "observation-index" / f"{_require_safe_id(observation_id)}.json"
        return self._load_optional(path, ObservationIndexRecord)

    def observation_content_hash(self, observation_id: str) -> str | None:
        record = self.load_observation_index(observation_id)
        return record.content_hash if record else None

    def load_observation(self, observation_id: str) -> SourceObservation | None:
        record = self.load_observation_index(observation_id)
        if record is None:
            return None
        directory = _date_directory(self.root / "observations", record.stored_day)
        path = directory / (
            f"{_require_safe_id(observation_id)}--{record.content_hash[:12]}.json"
        )
        return self._load_optional(path, SourceObservation)

    def save_extraction(self, content_hash: str, payload: dict[str, object]) -> Path:
        return self._atomic_json_write(
            self.root / "extractions" / f"{_require_safe_id(content_hash)}.json", payload
        )

    def load_extraction(self, content_hash: str) -> dict[str, object] | None:
        path = self.root / "extractions" / f"{_require_safe_id(content_hash)}.json"
        if not path.exists():
            return None
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError(f"extraction cache must be an object: {path}")
        return payload

    def save_pending_extraction(self, pending: PendingExtraction) -> Path:
        previous = self.load_pending_extraction(pending.id)
        if previous is not None and previous.queued_at.date() != pending.queued_at.date():
            old_path = _date_directory(
                self.root / "pending-extraction", previous.queued_at.date()
            ) / f"{_require_safe_id(previous.id)}.json"
            if old_path.exists():
                old_path.unlink()
        path = _date_directory(self.root / "pending-extraction", pending.queued_at.date())
        saved = self._save_model(
            path / f"{_require_safe_id(pending.id)}.json", pending
        )
        self._save_model(
            self.root
            / "pending-extraction-index"
            / f"{_require_safe_id(pending.id)}.json",
            PendingExtractionIndexRecord(
                pending_id=pending.id,
                queued_day=pending.queued_at.date(),
            ),
        )
        return saved

    def load_pending_extraction(
        self, pending_id: str
    ) -> PendingExtraction | None:
        index_path = (
            self.root
            / "pending-extraction-index"
            / f"{_require_safe_id(pending_id)}.json"
        )
        record = self._load_optional(index_path, PendingExtractionIndexRecord)
        if record is None:
            return None
        path = _date_directory(
            self.root / "pending-extraction", record.queued_day
        ) / f"{_require_safe_id(pending_id)}.json"
        return self._load_optional(path, PendingExtraction)

    def list_pending_extractions(self) -> list[PendingExtraction]:
        index_dir = self.root / "pending-extraction-index"
        if not index_dir.exists():
            return []
        pending: list[PendingExtraction] = []
        for path in sorted(index_dir.glob("*.json")):
            record = self._load(path, PendingExtractionIndexRecord)
            item = self.load_pending_extraction(record.pending_id)
            if item is not None:
                pending.append(item)
        return pending

    def delete_pending_extraction(self, pending_id: str) -> None:
        pending = self.load_pending_extraction(pending_id)
        if pending is not None:
            path = _date_directory(
                self.root / "pending-extraction", pending.queued_at.date()
            ) / f"{_require_safe_id(pending_id)}.json"
            if path.exists():
                path.unlink()
        index_path = (
            self.root
            / "pending-extraction-index"
            / f"{_require_safe_id(pending_id)}.json"
        )
        if index_path.exists():
            index_path.unlink()

    def save_pending_review(self, pending: PendingReview) -> Path:
        path = _date_directory(self.root / "pending-review", pending.queued_at.date())
        return self._save_model(path / f"{_require_safe_id(pending.id)}.json", pending)

    def save_change(self, change: ChangeRecord) -> Path:
        path = _date_directory(self.root / "daily", change.detected_at.date())
        return self._save_model(path / f"{_require_safe_id(change.id)}.json", change)

    def load_changes(self, day: date) -> list[ChangeRecord]:
        return self._load_bucket(self.root / "daily", day, ChangeRecord)

    def save_queue_job(self, job: QueueJob) -> Path:
        path = _date_directory(self.root / "queue", job.due_date)
        return self._save_model(path / f"{_require_safe_id(job.id)}.json", job)

    def delete_queue_job(self, job: QueueJob) -> None:
        path = _date_directory(self.root / "queue", job.due_date)
        target = path / f"{_require_safe_id(job.id)}.json"
        if target.exists():
            target.unlink()

    def load_queue_jobs(self, day: date) -> list[QueueJob]:
        return self._load_bucket(self.root / "queue", day, QueueJob)

    def save_schedule_manifest(self, manifest: ScheduleManifest) -> Path:
        path = self.root / "schedules" / f"{_require_safe_id(manifest.campaign_id)}.json"
        return self._save_model(path, manifest)

    def load_schedule_manifest(self, campaign_id: str) -> ScheduleManifest | None:
        path = self.root / "schedules" / f"{_require_safe_id(campaign_id)}.json"
        return self._load_optional(path, ScheduleManifest)

    def save_receipt(self, receipt: NotificationReceipt) -> Path:
        path = _date_directory(self.root / "notified", receipt.delivery_day)
        return self._save_model(
            path / f"{_require_safe_id(receipt.job_id)}.json", receipt
        )

    def has_receipt(self, job_id: str, day: date) -> bool:
        path = _date_directory(self.root / "notified", day)
        return (path / f"{_require_safe_id(job_id)}.json").is_file()

    def save_run_report(self, report: RunReport) -> Path:
        day = report.started_at.date()
        path = _date_directory(self.root / "runs", day)
        filename = report.started_at.strftime("%H%M%S") + ".json"
        return self._save_model(path / filename, report)

    def save_source_cursor(self, source_id: str, payload: dict[str, object]) -> Path:
        path = self.root / "sources" / f"{_require_safe_id(source_id)}.json"
        return self._atomic_json_write(path, payload)

    def load_source_cursor(self, source_id: str) -> dict[str, object] | None:
        path = self.root / "sources" / f"{_require_safe_id(source_id)}.json"
        if not path.exists():
            return None
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise ValueError(f"source cursor must be an object: {path}")
        return value

    def _load_bucket(
        self, root: Path, day: date, model: type[ModelT]
    ) -> list[ModelT]:
        directory = _date_directory(root, day)
        if not directory.exists():
            return []
        return [self._load(path, model) for path in sorted(directory.glob("*.json"))]

    def _save_model(self, path: Path, model: BaseModel) -> Path:
        return self._atomic_json_write(path, model.model_dump(mode="json"))

    def _atomic_json_write(self, path: Path, payload: object) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
        serialized = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
        try:
            temporary.write_text(serialized + "\n", encoding="utf-8")
            temporary.replace(path)
        finally:
            if temporary.exists():
                temporary.unlink()
        return path

    @staticmethod
    def _load(path: Path, model: type[ModelT]) -> ModelT:
        return model.model_validate_json(path.read_text(encoding="utf-8"))

    @classmethod
    def _load_optional(cls, path: Path, model: type[ModelT]) -> ModelT | None:
        if not path.exists():
            return None
        return cls._load(path, model)
