"""Blob-backed durable queue for knowledge gaps."""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any, Protocol

import helpdesk.shared as shared
from helpdesk.shared.config import get_settings

_LOGGER = logging.getLogger(__name__)

GAP_STATUSES = {"new", "triaged", "in_progress", "authored", "resolved", "dismissed"}
GAP_BLOB_PREFIX = "_system/kb-gaps/"
_TRUTHY = {"1", "true", "yes", "on"}
_FALSY = {"0", "false", "no", "off"}
_CLIENT_TIMEOUTS = {"connection_timeout": 5, "read_timeout": 10}


class KnowledgeGapLike(Protocol):
    reason: str
    tool: str | None
    had_citations: bool
    question_hash: str
    question: str | None


def gap_queue_enabled(environ: Mapping[str, str] | None = None) -> bool:
    """Return whether durable knowledge-gap queue writes are enabled."""

    env = os.environ if environ is None else environ
    value = (env.get("KB_GAP_QUEUE_ENABLED") or "").strip().lower()
    if value == "":
        return True
    if value in _FALSY:
        return False
    return value in _TRUTHY


def upsert_gap(gap: KnowledgeGapLike | Mapping[str, Any]) -> None:
    """Create or merge a knowledge gap.

    This is the request-path API and is intentionally best-effort: it never
    raises to callers.
    """

    if not gap_queue_enabled():
        return

    try:
        _upsert_gap(gap)
    except Exception:
        _LOGGER.warning("Failed to persist knowledge gap to Blob queue", exc_info=True)


def list_gaps(status: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
    """List queued gaps newest-first by ``last_seen_at``."""

    if status is not None:
        _validate_status(status)

    container = _container_client()
    records: list[dict[str, Any]] = []
    for blob in container.list_blobs(name_starts_with=GAP_BLOB_PREFIX):
        blob_name = getattr(blob, "name", str(blob))
        record = _download_blob_record(container.get_blob_client(blob_name))
        if record is None:
            continue
        if status is None or record.get("status") == status:
            records.append(record)

    records.sort(key=lambda item: str(item.get("last_seen_at") or ""), reverse=True)
    return records[: max(limit, 0)]


def get_gap(question_hash: str) -> dict[str, Any] | None:
    """Return one queued gap by hash, or ``None`` when absent."""

    return _download_blob_record(_blob_client(question_hash))


def set_status(question_hash: str, status: str, note: str | None = None) -> dict[str, Any]:
    """Set workflow status for a queued gap using optimistic Blob concurrency."""

    _validate_status(status)
    blob = _blob_client(question_hash)
    for _attempt in range(3):
        downloaded = _download_blob_record(blob, include_etag=True)
        if downloaded is None:
            raise KeyError(f"Knowledge gap '{question_hash}' was not found")

        record, etag = downloaded
        record["status"] = status
        if note:
            record["reason"] = note
            record["reasons"] = _dedupe_reasons([*record.get("reasons", []), note])
        record["last_seen_at"] = _utc_now_iso()

        try:
            _upload_record(blob, record, etag=etag)
            return record
        except Exception as exc:
            if exc.__class__.__name__ != "ResourceModifiedError":
                raise

    raise RuntimeError(f"Knowledge gap '{question_hash}' changed too often; retry the update")


def _upsert_gap(gap: KnowledgeGapLike | Mapping[str, Any]) -> None:
    incoming = _record_from_gap(gap)
    blob = _blob_client(str(incoming["question_hash"]))

    for _attempt in range(3):
        downloaded = _download_blob_record(blob, include_etag=True)
        if downloaded is None:
            _upload_record(blob, incoming)
            return

        existing, etag = downloaded
        merged = _merge_records(existing, incoming)
        try:
            _upload_record(blob, merged, etag=etag)
            return
        except Exception as exc:
            if exc.__class__.__name__ != "ResourceModifiedError":
                raise

    raise RuntimeError(f"Knowledge gap '{incoming['question_hash']}' changed too often")


def _record_from_gap(gap: KnowledgeGapLike | Mapping[str, Any]) -> dict[str, Any]:
    now = _utc_now_iso()
    question_hash = str(_get_value(gap, "question_hash") or "").strip()
    if not question_hash:
        raise ValueError("Knowledge gap is missing question_hash")

    reason = str(_get_value(gap, "reason") or "").strip()
    return {
        "question_hash": question_hash,
        "question": str(_get_value(gap, "question") or ""),
        "status": "new",
        "reason": reason,
        "reasons": _dedupe_reasons([reason]),
        "tool": _get_value(gap, "tool"),
        "had_citations": bool(_get_value(gap, "had_citations")),
        "occurrence_count": 1,
        "first_seen_at": now,
        "last_seen_at": now,
    }


def _merge_records(existing: dict[str, Any], incoming: dict[str, Any]) -> dict[str, Any]:
    status = str(existing.get("status") or "new")
    if status not in GAP_STATUSES:
        status = "new"

    question = str(existing.get("question") or incoming.get("question") or "")
    existing_count = int(existing.get("occurrence_count") or 0)
    reasons = _dedupe_reasons(
        [
            *list(existing.get("reasons") or []),
            str(existing.get("reason") or ""),
            *list(incoming.get("reasons") or []),
            str(incoming.get("reason") or ""),
        ]
    )
    first_seen = min(
        str(existing.get("first_seen_at") or incoming["first_seen_at"]),
        str(incoming.get("first_seen_at") or existing.get("first_seen_at")),
    )

    return {
        "question_hash": incoming["question_hash"],
        "question": question,
        "status": status,
        "reason": incoming.get("reason") or existing.get("reason") or "",
        "reasons": reasons,
        "tool": incoming.get("tool") or existing.get("tool"),
        "had_citations": bool(existing.get("had_citations")) or bool(incoming.get("had_citations")),
        "occurrence_count": existing_count + 1,
        "first_seen_at": first_seen,
        "last_seen_at": incoming["last_seen_at"],
    }


def _dedupe_reasons(reasons: list[Any]) -> list[str]:
    deduped: list[str] = []
    seen: set[str] = set()
    for reason in reasons:
        text = str(reason or "").strip()
        if text and text not in seen:
            seen.add(text)
            deduped.append(text)
    return deduped


def _download_blob_record(blob: Any, *, include_etag: bool = False) -> Any:
    try:
        props = blob.get_blob_properties() if include_etag else None
        data = blob.download_blob().readall()
    except Exception as exc:
        if exc.__class__.__name__ == "ResourceNotFoundError":
            return None
        raise

    record = json.loads(data.decode("utf-8"))
    if not isinstance(record, dict):
        raise ValueError("Knowledge gap blob does not contain a JSON object")
    if include_etag:
        return record, getattr(props, "etag", None)
    return record


def _upload_record(blob: Any, record: dict[str, Any], *, etag: str | None = None) -> None:
    kwargs: dict[str, Any] = {"overwrite": True}
    if etag:
        from azure.core import MatchConditions

        kwargs["etag"] = etag
        kwargs["match_condition"] = MatchConditions.IfNotModified

    blob.upload_blob(
        json.dumps(record, ensure_ascii=False, sort_keys=True).encode("utf-8"),
        **kwargs,
    )


def _blob_client(question_hash: str) -> Any:
    return _container_client().get_blob_client(f"{GAP_BLOB_PREFIX}{question_hash}.json")


def _container_client() -> Any:
    settings = get_settings()
    if not settings.storage_blob_endpoint:
        raise RuntimeError("Required configuration 'AZURE_STORAGE_BLOB_ENDPOINT' is not set")

    from azure.storage.blob import BlobServiceClient

    service = BlobServiceClient(
        account_url=settings.storage_blob_endpoint,
        credential=shared.get_credential(),
        **_CLIENT_TIMEOUTS,
    )
    return service.get_container_client(settings.kb_container or "kbdocs")


def _validate_status(status: str) -> None:
    if status not in GAP_STATUSES:
        allowed = "|".join(sorted(GAP_STATUSES))
        raise ValueError(f"Knowledge gap status must be one of: {allowed}")


def _get_value(gap: KnowledgeGapLike | Mapping[str, Any], name: str) -> Any:
    if isinstance(gap, Mapping):
        return gap.get(name)
    return getattr(gap, name)


def _utc_now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")
