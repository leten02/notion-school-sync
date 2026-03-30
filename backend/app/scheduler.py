from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from threading import Lock
from zoneinfo import ZoneInfo

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from .legacy_runner import (
    UserSecrets,
    run_daily_sync,
    run_monthly_report,
    run_weekly_report,
    trigger_daily_ai_score,
    trigger_weekly_ai_score,
)
from .repositories import (
    SettingsRepository,
    UserRepository,
    UserStateRepository,
    get_settings_repo,
    get_user_repo,
    get_user_state_repo,
)
from .security import SecretCipher, get_cipher

logger = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class SchedulerJobRunner:
    def __init__(
        self,
        timezone: str = "Asia/Seoul",
        user_repo: UserRepository | None = None,
        settings_repo: SettingsRepository | None = None,
        state_repo: UserStateRepository | None = None,
        cipher: SecretCipher | None = None,
    ):
        self._timezone = ZoneInfo(timezone)
        self.user_repo = user_repo or get_user_repo()
        self.settings_repo = settings_repo or get_settings_repo()
        self.state_repo = state_repo or get_user_state_repo()
        self.cipher = cipher or get_cipher()
        self._lock = Lock()

    @staticmethod
    def _short_error(exc: Exception) -> str:
        text = str(exc).strip() or exc.__class__.__name__
        return text[:500]

    def _build_user_secrets(self, user_id: str, settings: dict) -> UserSecrets:
        notion_token_enc = settings.get("notion_token_enc")
        school_api_key_enc = settings.get("school_api_key_enc")
        notion_page_id = settings.get("notion_page_id")
        if not notion_token_enc or not school_api_key_enc or not notion_page_id:
            raise ValueError("Notion/1000.school required settings are missing.")

        notion_token = self.cipher.decrypt(str(notion_token_enc))
        school_api_key = self.cipher.decrypt(str(school_api_key_enc))
        gemini_api_key_enc = settings.get("gemini_api_key_enc")
        gemini_api_key = self.cipher.decrypt(str(gemini_api_key_enc)) if gemini_api_key_enc else None

        return UserSecrets(
            user_id=user_id,
            notion_token=notion_token,
            notion_page_id=str(notion_page_id),
            school_api_key=school_api_key,
            gemini_api_key=gemini_api_key,
        )

    def _run_for_all_users(self, job_name: str):
        if not self._lock.acquire(blocking=False):
            logger.info("Scheduler lock active. Skip job=%s", job_name)
            return

        try:
            users = self.user_repo.list_active()
            for user in users:
                user_id = str(user["id"])
                now = _now_iso()
                settings = self.settings_repo.get(user_id)
                state = self.state_repo.get(user_id) or {}
                has_required = bool(settings and settings.get("notion_token_enc") and settings.get("notion_page_id") and settings.get("school_api_key_enc"))

                if not has_required:
                    payload = {
                        "last_status": "skipped_missing_settings",
                        "last_error": "Notion/1000.school required settings are missing.",
                    }
                    if job_name == "notion-sync":
                        payload["last_notion_check_at"] = now
                    elif job_name == "weekly-report":
                        payload["last_weekly_report_at"] = now
                    else:
                        payload["last_monthly_report_at"] = now
                    self.state_repo.upsert(user_id, payload)
                    continue

                try:
                    secrets = self._build_user_secrets(user_id, settings or {})
                    if job_name == "notion-sync":
                        result = run_daily_sync(secrets, state.get("last_notion_sync_at"))
                        payload = {
                            "last_notion_check_at": now,
                            "last_status": f"notion_sync_{result.get('status', 'unknown')}",
                            "last_error": None,
                        }
                        if result.get("status") == "synced":
                            payload["last_notion_sync_at"] = now
                    elif job_name == "weekly-report":
                        today = datetime.now(self._timezone).date()
                        # 일요일(weekday=6) 20:30 실행: 이번 주 월요일 기준
                        # 그 외(월요일 등 수동 실행): 지난 주 월요일 기준
                        if today.weekday() == 6:
                            target_monday = today - timedelta(days=6)
                        else:
                            target_monday = today - timedelta(days=today.weekday() + 7)
                        run_weekly_report(secrets, target_monday)
                        payload = {
                            "last_weekly_report_at": now,
                            "last_status": "weekly_report_ok",
                            "last_error": None,
                        }
                    else:
                        today = datetime.now(self._timezone).date()
                        prev_month_last_day = today.replace(day=1) - timedelta(days=1)
                        run_monthly_report(secrets, prev_month_last_day.year, prev_month_last_day.month)
                        payload = {
                            "last_monthly_report_at": now,
                            "last_status": "monthly_report_ok",
                            "last_error": None,
                        }
                    self.state_repo.upsert(user_id, payload)
                except Exception as exc:
                    logger.exception("Scheduler job failed user_id=%s job=%s", user_id, job_name)
                    payload = {
                        "last_status": f"{job_name}_error",
                        "last_error": self._short_error(exc),
                    }
                    if job_name == "notion-sync":
                        payload["last_notion_check_at"] = now
                    elif job_name == "weekly-report":
                        payload["last_weekly_report_at"] = now
                    else:
                        payload["last_monthly_report_at"] = now
                    self.state_repo.upsert(user_id, payload)
        finally:
            self._lock.release()

    def run_notion_sync(self):
        self._run_for_all_users("notion-sync")

    def run_weekly_report(self):
        self._run_for_all_users("weekly-report")

    def run_monthly_report(self):
        self._run_for_all_users("monthly-report")

    def run_daily_ai_score(self):
        """매일 8:59 - 일간 스니펫 AI 채점"""
        if not self._lock.acquire(blocking=False):
            logger.info("Scheduler lock active. Skip job=daily-ai-score")
            return
        try:
            users = self.user_repo.list_active()
            for user in users:
                user_id = str(user["id"])
                settings = self.settings_repo.get(user_id)
                if not settings or not settings.get("school_api_key_enc"):
                    continue
                try:
                    secrets = self._build_user_secrets(user_id, settings)
                    result = trigger_daily_ai_score(secrets)
                    self.state_repo.upsert(user_id, {
                        "last_status": f"daily_ai_score_{result.get('status', 'unknown')}",
                        "last_error": None,
                    })
                    logger.info("daily_ai_score user=%s score=%s", user_id, result.get("total_score"))
                except Exception as exc:
                    logger.exception("daily_ai_score failed user=%s", user_id)
                    self.state_repo.upsert(user_id, {
                        "last_status": "daily_ai_score_error",
                        "last_error": self._short_error(exc),
                    })
        finally:
            self._lock.release()

    def run_weekly_ai_score(self):
        """월요일 8:59 - 전주 주간 스니펫 최신화 + AI 채점"""
        if not self._lock.acquire(blocking=False):
            logger.info("Scheduler lock active. Skip job=weekly-ai-score")
            return
        try:
            today = datetime.now(self._timezone).date()
            target_monday = today - timedelta(days=today.weekday() + 7)  # 지난주 월요일
            users = self.user_repo.list_active()
            for user in users:
                user_id = str(user["id"])
                settings = self.settings_repo.get(user_id)
                if not settings or not settings.get("school_api_key_enc"):
                    continue
                try:
                    secrets = self._build_user_secrets(user_id, settings)
                    result = trigger_weekly_ai_score(secrets, target_monday)
                    self.state_repo.upsert(user_id, {
                        "last_status": f"weekly_ai_score_{result.get('status', 'unknown')}",
                        "last_error": None,
                    })
                    logger.info("weekly_ai_score user=%s score=%s", user_id, result.get("total_score"))
                except Exception as exc:
                    logger.exception("weekly_ai_score failed user=%s", user_id)
                    self.state_repo.upsert(user_id, {
                        "last_status": "weekly_ai_score_error",
                        "last_error": self._short_error(exc),
                    })
        finally:
            self._lock.release()


def create_scheduler(timezone: str) -> tuple[AsyncIOScheduler, SchedulerJobRunner]:
    runner = SchedulerJobRunner(timezone=timezone)
    scheduler = AsyncIOScheduler(timezone=timezone)
    scheduler.add_job(
        runner.run_notion_sync,
        trigger="cron",
        minute="*/10",
        id="notion-sync-all-users",
        replace_existing=True,
        coalesce=True,
        max_instances=1,
    )
    scheduler.add_job(
        runner.run_weekly_report,
        trigger="cron",
        day_of_week="sun",
        hour=20,
        minute=30,
        id="weekly-report-all-users",
        replace_existing=True,
        coalesce=True,
        max_instances=1,
    )
    scheduler.add_job(
        runner.run_monthly_report,
        trigger="cron",
        day=1,
        hour=9,
        minute=0,
        id="monthly-report-all-users",
        replace_existing=True,
        coalesce=True,
        max_instances=1,
    )
    # ── 매일 8:58 강제 노션 동기화 (채점 1분 전 최신화)
    scheduler.add_job(
        runner.run_notion_sync,
        trigger="cron",
        hour=8,
        minute=58,
        id="pre-score-notion-sync",
        replace_existing=True,
        coalesce=True,
        max_instances=1,
    )
    # ── 매일 8:59 일간 스니펫 AI 채점
    scheduler.add_job(
        runner.run_daily_ai_score,
        trigger="cron",
        hour=8,
        minute=59,
        id="daily-ai-score-all-users",
        replace_existing=True,
        coalesce=True,
        max_instances=1,
    )
    # ── 월요일 8:57 전주 주간 스니펫 최신화 + AI 채점
    scheduler.add_job(
        runner.run_weekly_ai_score,
        trigger="cron",
        day_of_week="mon",
        hour=8,
        minute=57,
        id="weekly-ai-score-all-users",
        replace_existing=True,
        coalesce=True,
        max_instances=1,
    )
    return scheduler, runner
