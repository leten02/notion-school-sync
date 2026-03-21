"""
데이터 저장소 어댑터

기본: SQLite (기존 단일 사용자 스크립트 호환)
백엔드 스케줄러 모드: Supabase snippets/analysis 테이블 사용
"""

import sqlite3
import json
import re
import os
from datetime import datetime, timezone

import requests

DB_PATH = os.getenv("SNIPPETS_DB_PATH") or os.path.join(os.path.dirname(__file__), "snippets.db")
USE_SUPABASE_SNIPPETS = os.getenv("USE_SUPABASE_SNIPPETS", "").lower() in {"1", "true", "yes", "on"}


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _require_current_user_id() -> str:
    user_id = os.getenv("CURRENT_USER_ID", "").strip()
    if not user_id:
        raise RuntimeError("CURRENT_USER_ID is required when USE_SUPABASE_SNIPPETS is enabled.")
    return user_id


def _supabase_base_url() -> str:
    url = os.getenv("SUPABASE_URL", "").strip()
    if not url:
        raise RuntimeError("SUPABASE_URL is missing.")
    return url.rstrip("/")


def _supabase_key() -> str:
    # write/query 안정성을 위해 서비스 롤 우선 사용
    key = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "").strip() or os.getenv("SUPABASE_ANON_KEY", "").strip()
    if not key:
        raise RuntimeError("SUPABASE_SERVICE_ROLE_KEY or SUPABASE_ANON_KEY is required.")
    return key


def _supabase_request(
    method: str,
    table: str,
    *,
    params: dict | None = None,
    json_body=None,
    prefer: str | None = None,
):
    headers = {
        "apikey": _supabase_key(),
        "Authorization": f"Bearer {_supabase_key()}",
    }
    if json_body is not None:
        headers["Content-Type"] = "application/json"
    if prefer:
        headers["Prefer"] = prefer

    resp = requests.request(
        method=method.upper(),
        url=f"{_supabase_base_url()}/rest/v1/{table}",
        headers=headers,
        params=params,
        json=json_body,
        timeout=30,
    )
    if not resp.ok:
        raise RuntimeError(f"Supabase {method} {table} failed: {resp.status_code} {resp.text[:500]}")
    if not resp.text.strip():
        return None
    try:
        return resp.json()
    except Exception:
        return resp.text


def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    """DB 테이블 초기화 (Supabase 모드에서는 no-op)"""
    if USE_SUPABASE_SNIPPETS:
        return
    conn = get_conn()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS snippets (
            id              INTEGER PRIMARY KEY,
            date            TEXT UNIQUE NOT NULL,
            content         TEXT,
            feedback_raw    TEXT,
            health_score    REAL,
            feedback_score  INTEGER,
            highlights      TEXT,
            lowlights       TEXT,
            tomorrow_goals  TEXT,
            team_mentions   TEXT,
            learnings       TEXT,
            created_at      TEXT,
            updated_at      TEXT,
            synced_at       TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS analysis (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at      TEXT DEFAULT (datetime('now')),
            snippet_count   INTEGER,
            burnout_risk        INTEGER,
            team_health         INTEGER,
            diligence           INTEGER,
            recurrence          INTEGER,
            growth              INTEGER,
            execution           INTEGER,
            emotional_energy    INTEGER,
            details_json    TEXT,
            alert_days      TEXT,
            improvement_areas TEXT,
            positive_trends TEXT,
            overall_summary TEXT,
            notion_page_id  TEXT
        );
    """)
    conn.commit()
    conn.close()


# ─── 스니펫 저장 ──────────────────────────────────────────────────────────────

def extract_health_score(content: str) -> float | None:
    if not content:
        return None

    m = re.search(r'헬스\s*체크[^\n]*\n(.*?)(?=\n#+\s|\Z)', content, re.DOTALL | re.IGNORECASE)
    if not m:
        return None
    section = m.group(1)
    patterns = [
        r'[-•]\s*(\d+(?:\.\d+)?)\s*/\s*10',
        r'\((\d+(?:\.\d+)?)\s*점\)',
        r'[-•]\s*(\d+(?:\.\d+)?)\s*점',
        r'(\d+(?:\.\d+)?)\s*/\s*10',
    ]
    for pat in patterns:
        hit = re.search(pat, section, re.IGNORECASE)
        if hit:
            val = float(hit.group(1))
            if 0 <= val <= 10:
                return val
    return None


def extract_section(content: str, section_name: str) -> str:
    if not content:
        return ""
    pattern = rf'#+[^\n]*(?:{section_name})[^\n]*\n(.*?)(?=\n#+\s|\Z)'
    m = re.search(pattern, content, re.DOTALL | re.IGNORECASE)
    if m and m.group(1) is not None:
        return m.group(1).strip()
    return ""


def _extract_feedback_score(feedback_raw) -> int | None:
    if not feedback_raw:
        return None
    try:
        fb = json.loads(feedback_raw) if isinstance(feedback_raw, str) else feedback_raw
        return fb.get("total_score")
    except Exception:
        return None


def _snippet_to_payload(snippet: dict) -> dict:
    content = snippet.get("content", "")
    feedback_raw = snippet.get("feedback")
    return {
        "user_id": _require_current_user_id(),
        "snippet_date": snippet.get("date"),
        "source": "1000school",
        "content": content,
        "health_score": extract_health_score(content),
        "feedback_score": _extract_feedback_score(feedback_raw),
        "highlights": extract_section(content, "하이라이트"),
        "lowlights": extract_section(content, "로우라이트"),
        "tomorrow_goals": extract_section(content, "내일의 우선순위"),
        "team_mentions": extract_section(content, "팀.*기여"),
        "learnings": extract_section(content, "배움|남길 말"),
        "external_id": str(snippet.get("id")) if snippet.get("id") is not None else None,
        "synced_at": _utc_now_iso(),
    }


def _snippet_row_to_legacy(row: dict) -> dict:
    return {
        "id": row.get("external_id") or row.get("id"),
        "date": row.get("snippet_date"),
        "content": row.get("content"),
        "health_score": row.get("health_score"),
        "feedback_score": row.get("feedback_score"),
        "highlights": row.get("highlights"),
        "lowlights": row.get("lowlights"),
        "tomorrow_goals": row.get("tomorrow_goals"),
        "team_mentions": row.get("team_mentions"),
        "learnings": row.get("learnings"),
        "synced_at": row.get("synced_at"),
    }


def upsert_snippet(snippet: dict):
    if USE_SUPABASE_SNIPPETS:
        payload = _snippet_to_payload(snippet)
        _supabase_request(
            "POST",
            "snippets",
            params={"on_conflict": "user_id,snippet_date"},
            json_body=[payload],
            prefer="resolution=merge-duplicates,return=minimal",
        )
        return

    content = snippet.get("content", "")
    feedback_raw = snippet.get("feedback")
    conn = get_conn()
    conn.execute("""
        INSERT INTO snippets (
            id, date, content, feedback_raw,
            health_score, feedback_score,
            highlights, lowlights, tomorrow_goals, team_mentions, learnings,
            created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(date) DO UPDATE SET
            content        = excluded.content,
            feedback_raw   = excluded.feedback_raw,
            health_score   = excluded.health_score,
            feedback_score = excluded.feedback_score,
            highlights     = excluded.highlights,
            lowlights      = excluded.lowlights,
            tomorrow_goals = excluded.tomorrow_goals,
            team_mentions  = excluded.team_mentions,
            learnings      = excluded.learnings,
            updated_at     = excluded.updated_at,
            synced_at      = datetime('now')
    """, (
        snippet.get("id"),
        snippet.get("date"),
        content,
        feedback_raw if isinstance(feedback_raw, str) else (json.dumps(feedback_raw, ensure_ascii=False) if feedback_raw is not None else None),
        extract_health_score(content),
        _extract_feedback_score(feedback_raw),
        extract_section(content, "하이라이트"),
        extract_section(content, "로우라이트"),
        extract_section(content, "내일의 우선순위"),
        extract_section(content, "팀.*기여"),
        extract_section(content, "배움|남길 말"),
        snippet.get("created_at"),
        snippet.get("updated_at"),
    ))
    conn.commit()
    conn.close()


def get_all_snippets() -> list[dict]:
    if USE_SUPABASE_SNIPPETS:
        rows = _supabase_request(
            "GET",
            "snippets",
            params={
                "select": "*",
                "user_id": f"eq.{_require_current_user_id()}",
                "order": "snippet_date.asc",
            },
        ) or []
        return [_snippet_row_to_legacy(r) for r in rows]

    conn = get_conn()
    rows = conn.execute("SELECT * FROM snippets ORDER BY date ASC").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_snippets_by_date_range(start_date: str, end_date: str) -> list[dict]:
    if USE_SUPABASE_SNIPPETS:
        rows = _supabase_request(
            "GET",
            "snippets",
            params={
                "select": "*",
                "user_id": f"eq.{_require_current_user_id()}",
                "order": "snippet_date.asc",
            },
        ) or []
        filtered = [r for r in rows if start_date <= (r.get("snippet_date") or "") <= end_date]
        return [_snippet_row_to_legacy(r) for r in filtered]

    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM snippets WHERE date >= ? AND date <= ? ORDER BY date ASC",
        (start_date, end_date)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_snippet_count() -> int:
    if USE_SUPABASE_SNIPPETS:
        return len(get_all_snippets())
    conn = get_conn()
    count = conn.execute("SELECT COUNT(*) FROM snippets").fetchone()[0]
    conn.close()
    return count


# ─── 우선순위 달성률 계산 ─────────────────────────────────────────────────────

def calc_priority_achievement(snippets: list[dict]) -> float:
    if len(snippets) < 2:
        return 0.0

    achieved = 0
    total = 0
    for i in range(len(snippets) - 1):
        goals_text = snippets[i].get("tomorrow_goals", "") or ""
        next_content = snippets[i + 1].get("content", "") or ""
        if not goals_text.strip():
            continue
        goals = re.findall(r'[-•]\s*(.+)', goals_text)
        if not goals:
            goals = [goals_text]
        for goal in goals:
            keywords = [w for w in goal.split() if len(w) > 1][:3]
            if keywords:
                total += 1
                if any(kw in next_content for kw in keywords):
                    achieved += 1
    return round(achieved / total * 100, 1) if total > 0 else 0.0


# ─── 분석 결과 저장/조회 ──────────────────────────────────────────────────────

def save_analysis(result: dict) -> int:
    if USE_SUPABASE_SNIPPETS:
        payload = {
            "user_id": _require_current_user_id(),
            "snippet_count": result.get("snippet_count"),
            "burnout_risk": result.get("burnout_risk"),
            "team_health": result.get("team_health"),
            "diligence": result.get("diligence"),
            "recurrence": result.get("recurrence"),
            "growth": result.get("growth"),
            "execution": result.get("execution"),
            "emotional_energy": result.get("emotional_energy"),
            "details_json": result.get("details", {}),
            "alert_days": result.get("alert_days", []),
            "improvement_areas": result.get("improvement_areas", []),
            "positive_trends": result.get("positive_trends", []),
            "overall_summary": result.get("overall_summary", ""),
            "notion_page_id": result.get("notion_page_id"),
        }
        rows = _supabase_request(
            "POST",
            "analysis",
            json_body=[payload],
            prefer="return=representation",
        ) or []
        if not rows:
            raise RuntimeError("analysis insert succeeded but no row was returned.")
        return int(rows[0]["id"])

    conn = get_conn()
    cur = conn.execute("""
        INSERT INTO analysis (
            snippet_count,
            burnout_risk, team_health, diligence, recurrence,
            growth, execution, emotional_energy,
            details_json, alert_days, improvement_areas,
            positive_trends, overall_summary, notion_page_id
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        result.get("snippet_count"),
        result.get("burnout_risk"),
        result.get("team_health"),
        result.get("diligence"),
        result.get("recurrence"),
        result.get("growth"),
        result.get("execution"),
        result.get("emotional_energy"),
        json.dumps(result.get("details", {}), ensure_ascii=False),
        json.dumps(result.get("alert_days", []), ensure_ascii=False),
        json.dumps(result.get("improvement_areas", []), ensure_ascii=False),
        json.dumps(result.get("positive_trends", []), ensure_ascii=False),
        result.get("overall_summary", ""),
        result.get("notion_page_id"),
    ))
    row_id = cur.lastrowid
    conn.commit()
    conn.close()
    return row_id


def update_analysis_notion_id(row_id: int, notion_page_id: str):
    if USE_SUPABASE_SNIPPETS:
        _supabase_request(
            "PATCH",
            "analysis",
            params={
                "id": f"eq.{row_id}",
                "user_id": f"eq.{_require_current_user_id()}",
            },
            json_body={"notion_page_id": notion_page_id},
            prefer="return=minimal",
        )
        return

    conn = get_conn()
    conn.execute(
        "UPDATE analysis SET notion_page_id = ? WHERE id = ?",
        (notion_page_id, row_id)
    )
    conn.commit()
    conn.close()


def get_latest_analysis() -> dict | None:
    if USE_SUPABASE_SNIPPETS:
        rows = _supabase_request(
            "GET",
            "analysis",
            params={
                "select": "*",
                "user_id": f"eq.{_require_current_user_id()}",
                "order": "created_at.desc",
                "limit": "1",
            },
        ) or []
        if not rows:
            return None
        row = rows[0]
        # SQLite 반환 형태와 맞추기 위해 json 계열 필드를 문자열화
        for col in ("details_json", "alert_days", "improvement_areas", "positive_trends"):
            if col in row and not isinstance(row[col], str):
                row[col] = json.dumps(row[col], ensure_ascii=False)
        return row

    conn = get_conn()
    row = conn.execute(
        "SELECT * FROM analysis ORDER BY created_at DESC LIMIT 1"
    ).fetchone()
    conn.close()
    return dict(row) if row else None


if __name__ == "__main__":
    init_db()
    if USE_SUPABASE_SNIPPETS:
        print("✅ Supabase 모드: 로컬 SQLite 초기화 스킵")
    else:
        print(f"✅ DB 초기화 완료: {DB_PATH}")
