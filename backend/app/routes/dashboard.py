from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, status

from ..auth import get_current_user
from ..legacy_runner import UserSecrets, create_today_page
from ..repositories import (
    SettingsRepository,
    UserStateRepository,
    get_settings_repo,
    get_user_state_repo,
)
from ..schemas import CurrentUser, DashboardResponse, SettingsResponse, UserStateResponse
from ..security import SecretCipher, get_cipher

router = APIRouter(prefix="/dashboard", tags=["dashboard"])
logger = logging.getLogger(__name__)


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


def _to_state_response(user_id: str, record: dict | None) -> UserStateResponse | None:
    if not record:
        return None
    return UserStateResponse(
        user_id=user_id,
        last_notion_check_at=record.get("last_notion_check_at"),
        last_notion_sync_at=record.get("last_notion_sync_at"),
        last_weekly_report_at=record.get("last_weekly_report_at"),
        last_monthly_report_at=record.get("last_monthly_report_at"),
        last_status=record.get("last_status"),
        last_error=record.get("last_error"),
        updated_at=record.get("updated_at"),
    )


@router.get("/me", response_model=DashboardResponse)
def get_my_dashboard(
    current_user: CurrentUser = Depends(get_current_user),
    settings_repo: SettingsRepository = Depends(get_settings_repo),
    state_repo: UserStateRepository = Depends(get_user_state_repo),
):
    try:
        settings_row = settings_repo.get(current_user.id)
        state_row = state_repo.get(current_user.id)
        return DashboardResponse(
            settings=_to_settings_response(current_user.id, settings_row),
            state=_to_state_response(current_user.id, state_row),
        )
    except Exception as exc:
        logger.exception("get_my_dashboard failed")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"dashboard/me failed: {exc}",
        ) from exc


@router.post("/me/create-today-page")
def create_today_notion_page(
    current_user: CurrentUser = Depends(get_current_user),
    settings_repo: SettingsRepository = Depends(get_settings_repo),
    cipher: SecretCipher = Depends(get_cipher),
):
    try:
        row = settings_repo.get(current_user.id)
        if not row or not row.get("notion_token_enc") or not row.get("notion_page_id") or not row.get("school_api_key_enc"):
            raise HTTPException(status_code=400, detail="Notion 설정이 완료되지 않았습니다.")
        secrets = UserSecrets(
            user_id=current_user.id,
            notion_token=cipher.decrypt(str(row["notion_token_enc"])),
            notion_page_id=str(row["notion_page_id"]),
            school_api_key=cipher.decrypt(str(row["school_api_key_enc"])),
            gemini_api_key=cipher.decrypt(str(row["gemini_api_key_enc"])) if row.get("gemini_api_key_enc") else None,
        )
        result = create_today_page(secrets)
        return result
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("create_today_notion_page failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc
