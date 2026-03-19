"""
SQLite DB 관리

테이블:
  snippets   - 1000.school 스니펫 원본 + 추출된 구조화 데이터
  analysis   - Gemini 분석 결과 (리포트)
"""

import sqlite3
import json
import re
import os
from datetime import datetime

DB_PATH = os.path.join(os.path.dirname(__file__), "snippets.db")


def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row  # dict처럼 접근 가능
    return conn


def init_db():
    """DB 테이블 초기화 (없으면 생성)"""
    conn = get_conn()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS snippets (
            id              INTEGER PRIMARY KEY,   -- 1000.school snippet id
            date            TEXT UNIQUE NOT NULL,  -- "2026-03-18"
            content         TEXT,
            feedback_raw    TEXT,                  -- 원본 JSON 문자열

            -- 추출된 구조화 데이터
            health_score    REAL,                  -- 헬스 체크 점수 (0~10)
            feedback_score  INTEGER,               -- 피드백 총점 (0~100)
            highlights      TEXT,                  -- 하이라이트 텍스트
            lowlights       TEXT,                  -- 로우라이트 텍스트
            tomorrow_goals  TEXT,                  -- 내일의 우선순위
            team_mentions   TEXT,                  -- 팀 관련 언급
            learnings       TEXT,                  -- 오늘의 배움

            created_at      TEXT,
            updated_at      TEXT,
            synced_at       TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS analysis (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at      TEXT DEFAULT (datetime('now')),
            snippet_count   INTEGER,               -- 분석에 사용된 스니펫 수

            -- 7개 지표 (0~100 점수)
            burnout_risk        INTEGER,
            team_health         INTEGER,
            diligence           INTEGER,
            recurrence          INTEGER,
            growth              INTEGER,
            execution           INTEGER,
            emotional_energy    INTEGER,

            -- 상세 분석 JSON
            details_json    TEXT,   -- 각 지표별 이유, 근거 날짜 등
            alert_days      TEXT,   -- 주의 필요 날짜 JSON
            improvement_areas TEXT, -- 개선 필요 영역 JSON
            positive_trends TEXT,   -- 긍정 변화 JSON
            overall_summary TEXT,   -- 전체 흐름 요약

            notion_page_id  TEXT    -- 업데이트된 노션 페이지 ID
        );
    """)
    conn.commit()
    conn.close()


# ─── 스니펫 저장 ──────────────────────────────────────────────────────────────

def extract_health_score(content: str) -> float | None:
    """
    헬스 체크 점수 추출
    '#### 헬스 체크 (10점)' 섹션 이후 줄에서 실제 점수를 찾음
    예: '- 6/10', '- 2/10 (수면 부족)', '- (6점)', '- 8점'
    """
    if not content:
        return None

    # '헬스 체크' 섹션 이후 텍스트만 추출
    m = re.search(r'헬스\s*체크[^\n]*\n(.*?)(?=\n#+\s|\Z)', content, re.DOTALL | re.IGNORECASE)
    if not m:
        return None

    section = m.group(1)  # 헬스 체크 이후 내용 (제목 제외)

    # 섹션 내에서 점수 패턴 탐색 (0~10 사이 숫자)
    patterns = [
        r'[-•]\s*(\d+(?:\.\d+)?)\s*/\s*10',   # - 6/10
        r'\((\d+(?:\.\d+)?)\s*점\)',            # (6점)
        r'[-•]\s*(\d+(?:\.\d+)?)\s*점',         # - 6점
        r'(\d+(?:\.\d+)?)\s*/\s*10',            # 6/10 (앞에 - 없어도)
    ]
    for pat in patterns:
        hit = re.search(pat, section, re.IGNORECASE)
        if hit:
            val = float(hit.group(1))
            if 0 <= val <= 10:
                return val
    return None


def extract_section(content: str, section_name: str) -> str:
    """특정 섹션 텍스트 추출 (예: '하이라이트', '로우라이트')"""
    if not content:
        return ""
    pattern = rf'#+\s*{section_name}[^\n]*\n(.*?)(?=\n#+\s|\Z)'
    m = re.search(pattern, content, re.DOTALL | re.IGNORECASE)
    if m:
        return m.group(1).strip()
    return ""


def upsert_snippet(snippet: dict):
    """스니펫 저장 (있으면 업데이트, 없으면 삽입)"""
    content = snippet.get("content", "")
    feedback_raw = snippet.get("feedback")
    feedback_score = None
    if feedback_raw:
        try:
            fb = json.loads(feedback_raw) if isinstance(feedback_raw, str) else feedback_raw
            feedback_score = fb.get("total_score")
        except Exception:
            pass

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
        feedback_raw if isinstance(feedback_raw, str) else json.dumps(feedback_raw, ensure_ascii=False),
        extract_health_score(content),
        feedback_score,
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
    """전체 스니펫 날짜순 반환"""
    conn = get_conn()
    rows = conn.execute("SELECT * FROM snippets ORDER BY date ASC").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_snippet_count() -> int:
    conn = get_conn()
    count = conn.execute("SELECT COUNT(*) FROM snippets").fetchone()[0]
    conn.close()
    return count


# ─── 우선순위 달성률 계산 ─────────────────────────────────────────────────────

def calc_priority_achievement(snippets: list[dict]) -> float:
    """
    내일의 우선순위 달성률 계산
    N일의 '내일 우선순위' 키워드가 N+1일 '오늘 한 일'에 등장하는 비율
    """
    if len(snippets) < 2:
        return 0.0

    achieved = 0
    total = 0

    for i in range(len(snippets) - 1):
        goals_text = snippets[i].get("tomorrow_goals", "") or ""
        next_content = snippets[i + 1].get("content", "") or ""

        if not goals_text.strip():
            continue

        # 목표 항목 파싱 (- 또는 숫자. 로 시작하는 줄)
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
    """분석 결과 저장, 생성된 row id 반환"""
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
    conn = get_conn()
    conn.execute(
        "UPDATE analysis SET notion_page_id = ? WHERE id = ?",
        (notion_page_id, row_id)
    )
    conn.commit()
    conn.close()


def get_latest_analysis() -> dict | None:
    """가장 최근 분석 결과 반환"""
    conn = get_conn()
    row = conn.execute(
        "SELECT * FROM analysis ORDER BY created_at DESC LIMIT 1"
    ).fetchone()
    conn.close()
    return dict(row) if row else None


if __name__ == "__main__":
    init_db()
    print(f"✅ DB 초기화 완료: {DB_PATH}")
