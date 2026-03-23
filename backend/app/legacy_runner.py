from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import dataclass
from datetime import date
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[2]
RESULT_PREFIX = "__RESULT__"


@dataclass(frozen=True)
class UserSecrets:
    user_id: str
    notion_token: str
    notion_page_id: str
    school_api_key: str
    gemini_api_key: str | None = None


def _build_env(secrets: UserSecrets) -> dict[str, str]:
    env = os.environ.copy()
    env["NOTION_TOKEN"] = secrets.notion_token
    env["NOTION_PAGE_ID"] = secrets.notion_page_id
    env["SCHOOL_API_KEY"] = secrets.school_api_key
    env["GEMINI_API_KEY"] = secrets.gemini_api_key or ""
    env["USE_SUPABASE_SNIPPETS"] = "1"
    env["CURRENT_USER_ID"] = secrets.user_id
    env.pop("SNIPPETS_DB_PATH", None)
    return env


def _extract_result(stdout: str) -> dict:
    for line in reversed(stdout.splitlines()):
        if line.startswith(RESULT_PREFIX):
            payload = line[len(RESULT_PREFIX) :].strip()
            return json.loads(payload)
    return {"status": "unknown", "raw_stdout": stdout.strip()[-1000:]}


def _run_python(code: str, env: dict[str, str], timeout: int) -> dict:
    proc = subprocess.run(
        [sys.executable, "-c", code],
        cwd=ROOT_DIR,
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if proc.returncode != 0:
        raise RuntimeError(
            f"Legacy runner failed ({proc.returncode}): "
            f"{(proc.stderr or proc.stdout).strip()[-1000:]}"
        )
    return _extract_result(proc.stdout)


def run_daily_sync(secrets: UserSecrets, last_sync_at: str | None) -> dict:
    code = r"""
import datetime, json, os
from zoneinfo import ZoneInfo
import main

KST = ZoneInfo("Asia/Seoul")
DAY_START_HOUR = 9

def parse_iso(value):
    if not value:
        return None
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    return datetime.datetime.fromisoformat(value)

page_id = main.find_today_child_page(os.environ["NOTION_PAGE_ID"])
if not page_id:
    now = datetime.datetime.now(KST)
    if now.hour >= DAY_START_HOUR:
        # 오전 9시 이후 → 오늘 날짜 페이지 자동 생성
        today = main.effective_date()
        title = today.strftime("%Y-%m-%d")
        page_id = main.create_today_notion_page(title)
        if not page_id:
            print("__RESULT__" + json.dumps({"status": "page_creation_failed"}))
            raise SystemExit(0)
    else:
        print("__RESULT__" + json.dumps({"status": "no_page"}))
        raise SystemExit(0)

edited = main.get_page_last_edited(page_id)
last_sync = os.getenv("LAST_SYNC_AT", "")
if last_sync:
    try:
        if parse_iso(edited) and parse_iso(last_sync) and parse_iso(edited) <= parse_iso(last_sync):
            print("__RESULT__" + json.dumps({"status": "unchanged", "edited": edited}))
            raise SystemExit(0)
    except Exception:
        pass

uploaded = main.run_once(polish=True)
if uploaded is None:
    print("__RESULT__" + json.dumps({"status": "no_content", "edited": edited}))
else:
    print("__RESULT__" + json.dumps({"status": "synced", "edited": edited}))
"""
    env = _build_env(secrets)
    if last_sync_at:
        env["LAST_SYNC_AT"] = last_sync_at
    return _run_python(code, env, timeout=240)


def run_weekly_report(secrets: UserSecrets, target_monday: date) -> dict:
    code = r"""
import datetime, json, os
import report

y, m, d = [int(x) for x in os.environ["TARGET_MONDAY"].split("-")]
report.sync_snippets()
report.run_weekly(datetime.date(y, m, d))
print("__RESULT__" + json.dumps({"status": "ok"}))
"""
    env = _build_env(secrets)
    env["TARGET_MONDAY"] = target_monday.strftime("%Y-%m-%d")
    return _run_python(code, env, timeout=600)


def run_monthly_report(secrets: UserSecrets, year: int, month: int) -> dict:
    code = r"""
import json, os
import report

y = int(os.environ["TARGET_YEAR"])
m = int(os.environ["TARGET_MONTH"])
report.sync_snippets()
report.run_monthly(y, m)
print("__RESULT__" + json.dumps({"status": "ok"}))
"""
    env = _build_env(secrets)
    env["TARGET_YEAR"] = str(year)
    env["TARGET_MONTH"] = str(month)
    return _run_python(code, env, timeout=900)
