from __future__ import annotations

import logging
import re
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, HTTPException, status

from ..auth import get_current_user
from ..repositories import SettingsRepository, get_settings_repo
from ..schemas import CurrentUser, SettingsResponse, SettingsUpsertRequest
from ..security import SecretCipher, get_cipher

router = APIRouter(prefix="/settings", tags=["settings"])
logger = logging.getLogger(__name__)


_UUID32_RE = re.compile(r"^[0-9a-fA-F]{32}$")
_UUID36_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)


def _normalize_notion_page_id(raw: str) -> str:
    value = raw.strip()
    if not value:
        return ""

    if _UUID32_RE.fullmatch(value):
        return value.lower()
    if _UUID36_RE.fullmatch(value):
        return value.replace("-", "").lower()

    parsed = urlparse(value)
    if parsed.scheme in {"http", "https"} and parsed.netloc:
        path_last = parsed.path.rstrip("/").split("/")[-1]
        if "-" in path_last:
            path_last = path_last.rsplit("-", 1)[-1]
        candidate = path_last.replace("-", "")
        if _UUID32_RE.fullmatch(candidate):
            return candidate.lower()

    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail=(
            "Invalid NOTION_PAGE_ID. Use 32-char page ID "
            "or a Notion page URL containing that ID."
        ),
    )


def _to_settings_response(user_id: str, record: dict | None) -> SettingsResponse:
    record = record or {}
    return SettingsResponse(
        user_id=user_id,
        notion_page_id=record.get("notion_page_id"),
        has_notion_token=bool(record.get("notion_token_enc")),
        has_school_api_key=bool(record.get("school_api_key_enc")),
        has_gemini_api_key=bool(record.get("gemini_api_key_enc")),
        updated_at=record.get("updated_at"),
    )


@router.get("/me", response_model=SettingsResponse)
def get_my_settings(
    current_user: CurrentUser = Depends(get_current_user),
    settings_repo: SettingsRepository = Depends(get_settings_repo),
):
    try:
        record = settings_repo.get(current_user.id)
        return _to_settings_response(current_user.id, record)
    except Exception as exc:
        logger.exception("get_my_settings failed")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"settings/me failed: {exc}",
        ) from exc


@router.put("/me", response_model=SettingsResponse)
def save_my_settings(
    body: SettingsUpsertRequest,
    current_user: CurrentUser = Depends(get_current_user),
    settings_repo: SettingsRepository = Depends(get_settings_repo),
    cipher: SecretCipher = Depends(get_cipher),
):
    payload: dict[str, str | None] = {}

    if body.notion_page_id is not None:
        normalized = _normalize_notion_page_id(body.notion_page_id)
        payload["notion_page_id"] = normalized or None

    if body.notion_token is not None:
        payload["notion_token_enc"] = (
            cipher.encrypt(body.notion_token.strip()) if body.notion_token.strip() else None
        )

    if body.school_api_key is not None:
        payload["school_api_key_enc"] = (
            cipher.encrypt(body.school_api_key.strip()) if body.school_api_key.strip() else None
        )

    if body.gemini_api_key is not None:
        payload["gemini_api_key_enc"] = (
            cipher.encrypt(body.gemini_api_key.strip()) if body.gemini_api_key.strip() else None
        )

    try:
        record = settings_repo.upsert(current_user.id, payload)
        return _to_settings_response(current_user.id, record)
    except Exception as exc:
        logger.exception("save_my_settings failed")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"settings/me save failed: {exc}",
        ) from exc
