"""
AI 감독 리포트 생성기

흐름:
  1. 1000.school 스니펫 전체 가져와서 SQLite DB에 저장
  2. DB에서 구조화 데이터 빌드 (토큰 절약용 요약본)
  3. Gemini API로 7개 지표 분석 요청
  4. 분석 결과를 노션 리포트 페이지에 작성/업데이트

사용법:
  python report.py
"""

import os
import re
import json
import time
import requests
from datetime import datetime, date, timedelta
from zoneinfo import ZoneInfo
from dotenv import load_dotenv
from notion_client import Client
from notion_client.errors import APIResponseError

KST = ZoneInfo("Asia/Seoul")

import db

load_dotenv()

# ─── 설정 ─────────────────────────────────────────────────────────────────────

SCHOOL_API_KEY  = os.getenv("SCHOOL_API_KEY")
NOTION_TOKEN    = os.getenv("NOTION_TOKEN")
NOTION_PAGE_ID  = os.getenv("NOTION_PAGE_ID")   # 리포트 페이지가 들어갈 부모 페이지
GEMINI_API_KEY  = os.getenv("GEMINI_API_KEY")    # 나중에 추가

API_BASE               = "https://api.1000.school"
WEEKLY_CONTAINER_TITLE = "📅 주간 리포트"
MONTHLY_CONTAINER_TITLE= "📆 월간 리포트"

notion = Client(auth=NOTION_TOKEN)


# ─── 1. 스니펫 동기화 ─────────────────────────────────────────────────────────

def sync_snippets():
    """1000.school → SQLite DB 동기화 (최신 내용으로 덮어쓰기)"""
    headers = {"Authorization": f"Bearer {SCHOOL_API_KEY}"}
    r = requests.get(f"{API_BASE}/daily-snippets", headers=headers)
    r.raise_for_status()
    items = r.json().get("items", [])

    for item in items:
        db.upsert_snippet(item)

    if items:
        dates = [item.get("date", "unknown") for item in items]
        print(f"   ✅ {len(items)}개 스니펫 최신화 완료")
        print(f"   📅 동기화된 날짜: {', '.join(sorted(dates))}")
    return items


# ─── 2. Gemini용 요약본 빌드 (토큰 절약) ─────────────────────────────────────

def build_gemini_summary(snippets: list[dict]) -> str:
    """
    전체 스니펫을 Gemini에 보낼 압축 요약본으로 변환
    원문(2000자) → 요약(200자) 수준으로 압축해 토큰 절약
    """
    lines = []
    for s in snippets:
        snippet_date  = s.get("date", "")
        health        = s.get("health_score")
        fb_score      = s.get("feedback_score")
        highlights    = (s.get("highlights") or "").strip()[:120]
        lowlights     = (s.get("lowlights") or "").strip()[:120]
        goals         = (s.get("tomorrow_goals") or "").strip()[:100]
        team          = (s.get("team_mentions") or "").strip()[:100]
        learnings     = (s.get("learnings") or "").strip()[:100]

        block = f"""[{snippet_date}]
헬스:{health}/10 | 피드백:{fb_score}점
하이라이트: {highlights}
로우라이트: {lowlights}
내일목표: {goals}
팀기여: {team}
배움: {learnings}"""
        lines.append(block)

    priority_rate = db.calc_priority_achievement(snippets)
    lines.append(f"\n[통계] 우선순위 달성률: {priority_rate}%")
    if snippets:
        lines.append(f"[통계] 총 스니펫: {len(snippets)}개 / 기간: {snippets[0]['date']} ~ {snippets[-1]['date']}")

    return "\n\n".join(lines)


def build_gemini_prompt(summary: str) -> str:
    return f"""당신은 학습자의 일간 스니펫을 분석하는 AI 감독관입니다.
아래 데이터를 분석해서 반드시 JSON 형식으로만 반환하세요. (설명 없이 JSON만)

=== 스니펫 데이터 ===
{summary}

=== 분석 기준 ===
다음 7개 지표를 각각 0~100 점수로 평가하세요.

1. burnout_risk (번아웃 위험도) - 높을수록 위험
   - 헬스 점수 5 이하 날의 맥락과 사유
   - 수면 부족, 피로, 과부하 언급 빈도
   - 연속 저헬스 패턴

2. team_health (팀 건강도) - 높을수록 좋음
   - 팀원 간 갈등/불화 조짐 언급
   - 팀 기여 언급이 갑자기 줄어드는 고립 패턴
   - 부정적 감정 표현 대상이 팀인 경우

3. diligence (성실도) - 높을수록 좋음
   - 스니펫 작성 연속성 (빠진 날)
   - 우선순위 달성률 데이터 활용
   - 피드백 점수 추이

4. recurrence (문제 재발성) - 높을수록 재발 많음
   - 동일한 로우라이트가 반복 등장
   - 개선했다고 했으나 다시 같은 문제 발생

5. growth (성장 지수) - 높을수록 좋음
   - 매번 새로운 것을 배우는지
   - 피드백 next_action을 실제로 이행하는지
   - 학습 다양성과 깊이

6. execution (실행 집중도) - 높을수록 좋음
   - 하루에 너무 많은 것을 벌여놓는지
   - 목표와 실제 행동의 일치도

7. emotional_energy (감정 에너지) - 높을수록 긍정
   - 글의 온도 변화 (점점 차가워지는지)
   - 자기효능감 표현 ("할 수 있다" vs "힘들다")
   - 전반적 감정 방향성

=== 반환 JSON 형식 ===
{{
  "burnout_risk":     {{ "score": 0~100, "level": "위험|주의|양호|우수", "reason": "2~3문장 근거", "evidence_dates": ["2026-03-xx"] }},
  "team_health":      {{ "score": 0~100, "level": "위험|주의|양호|우수", "reason": "2~3문장 근거", "evidence_dates": [] }},
  "diligence":        {{ "score": 0~100, "level": "위험|주의|양호|우수", "reason": "2~3문장 근거", "evidence_dates": [] }},
  "recurrence":       {{ "score": 0~100, "level": "위험|주의|양호|우수", "reason": "2~3문장 근거", "evidence_dates": [] }},
  "growth":           {{ "score": 0~100, "level": "위험|주의|양호|우수", "reason": "2~3문장 근거", "evidence_dates": [] }},
  "execution":        {{ "score": 0~100, "level": "위험|주의|양호|우수", "reason": "2~3문장 근거", "evidence_dates": [] }},
  "emotional_energy": {{ "score": 0~100, "level": "위험|주의|양호|우수", "reason": "2~3문장 근거", "evidence_dates": [] }},
  "overall_summary":  "전체 흐름 요약 (3~5문장)",
  "alert_days":       [ {{ "date": "2026-03-xx", "reason": "주의 사유" }} ],
  "improvement_areas":[ "지속적으로 개선 안 되는 영역 1", "영역 2" ],
  "positive_trends":  [ "긍정적 변화 1", "변화 2" ]
}}"""


# ─── 3. Gemini 분석 ───────────────────────────────────────────────────────────

def analyze_with_gemini(prompt: str) -> dict:
    """
    Gemini API 호출
    API 키가 없으면 더미 데이터 반환 (개발/테스트용)
    """
    if not GEMINI_API_KEY:
        print("   ⚠️  GEMINI_API_KEY 없음 → 더미 데이터로 실행")
        return _dummy_analysis()

    url = (
        "https://generativelanguage.googleapis.com/v1beta/models/"
        f"gemini-2.5-flash:generateContent?key={GEMINI_API_KEY}"
    )
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": 0.3,
            "responseMimeType": "application/json",
        },
    }
    # 최대 3번 재시도 (429·500·503 일시 오류 대응)
    for attempt in range(1, 4):
        resp = requests.post(url, json=payload, timeout=60)
        if resp.ok:
            break
        if resp.status_code not in (429, 500, 503) or attempt == 3:
            raise RuntimeError(f"Gemini 요청 실패: {resp.status_code} / {resp.text[:200]}")
        time.sleep(attempt * 2)

    data = resp.json()
    candidates = data.get("candidates", [])
    if not candidates:
        raise ValueError(f"Gemini 응답에 candidates 없음: {data}")
    raw = candidates[0].get("content", {}).get("parts", [{}])[0].get("text", "").strip()
    # responseMimeType: application/json 이지만 혹시 ``` 래핑된 경우 제거 (substring 제거)
    raw = re.sub(r'^```(?:json)?\s*', '', raw)
    raw = re.sub(r'\s*```$', '', raw).strip()
    if not raw:
        raise ValueError("Gemini 응답 텍스트가 비어있음")
    return json.loads(raw)


def _dummy_analysis() -> dict:
    """Gemini API 키 없을 때 사용하는 더미 데이터"""
    return {
        "burnout_risk":     {"score": 35, "level": "양호", "reason": "헬스 점수가 전반적으로 안정적입니다. 3월 12일 수면 부족 이슈가 있었으나 일회성으로 보입니다.", "evidence_dates": ["2026-03-12"]},
        "team_health":      {"score": 80, "level": "우수", "reason": "팀 기여 내용이 일관되게 긍정적입니다. 갈등 조짐은 발견되지 않습니다.", "evidence_dates": []},
        "diligence":        {"score": 75, "level": "양호", "reason": "스니펫 작성이 꾸준합니다. 우선순위 달성률은 보통 수준입니다.", "evidence_dates": []},
        "recurrence":       {"score": 40, "level": "양호", "reason": "수면 관리 문제가 2회 반복 등장했습니다. 주의가 필요합니다.", "evidence_dates": ["2026-03-12", "2026-03-17"]},
        "growth":           {"score": 70, "level": "양호", "reason": "매일 새로운 학습 내용이 기록되고 있습니다.", "evidence_dates": []},
        "execution":        {"score": 65, "level": "양호", "reason": "하루 목표량이 다소 많은 경향이 있습니다.", "evidence_dates": []},
        "emotional_energy": {"score": 72, "level": "양호", "reason": "전반적으로 긍정적인 톤을 유지하고 있습니다.", "evidence_dates": []},
        "overall_summary":  "⚠️ Gemini API 키를 .env에 추가하면 실제 분석이 시작됩니다. 현재는 더미 데이터입니다.",
        "alert_days":       [{"date": "2026-03-12", "reason": "수면 2시간 미만, 헬스 2점"}],
        "improvement_areas":["수면 관리", "일일 목표량 조절"],
        "positive_trends":  ["프롬프트 엔지니어링 학습", "팀 기여 일관성"],
    }


# ─── 4. 노션 리포트 페이지 생성/업데이트 ─────────────────────────────────────

LEVEL_EMOJI = {"위험": "🔴", "주의": "🟡", "양호": "🟢", "우수": "🔵"}
SCORE_BAR_LEN = 10

def score_to_bar(score: int) -> str:
    """점수를 시각적 바로 변환 (예: ████████░░ 80)"""
    filled = round(score / 100 * SCORE_BAR_LEN)
    empty  = SCORE_BAR_LEN - filled
    return f"{'█' * filled}{'░' * empty} {score}"


METRIC_LABELS = {
    "burnout_risk":     ("🔥 번아웃 위험도",     True),   # True = 높을수록 나쁨
    "team_health":      ("👥 팀 건강도",          False),
    "diligence":        ("💪 성실도",             False),
    "recurrence":       ("🔁 문제 재발성",        True),
    "growth":           ("🧠 성장 지수",          False),
    "execution":        ("⚡ 실행 집중도",        False),
    "emotional_energy": ("💬 감정 에너지",        False),
}


def _text(content: str) -> dict:
    return {"type": "text", "text": {"content": content}}


def _paragraph(content: str) -> dict:
    return {"object": "block", "type": "paragraph",
            "paragraph": {"rich_text": [_text(content)]}}


def _heading(level: int, content: str) -> dict:
    t = f"heading_{level}"
    return {"object": "block", "type": t, t: {"rich_text": [_text(content)]}}


def _divider() -> dict:
    return {"object": "block", "type": "divider", "divider": {}}


def _callout(content: str, emoji: str = "💡") -> dict:
    return {
        "object": "block", "type": "callout",
        "callout": {
            "rich_text": [_text(content)],
            "icon": {"type": "emoji", "emoji": emoji},
        },
    }


def _bullet(content: str) -> dict:
    return {"object": "block", "type": "bulleted_list_item",
            "bulleted_list_item": {"rich_text": [_text(content)]}}


def build_report_blocks(analysis: dict, snippets: list[dict], priority_rate: float, period_label: str = "") -> list[dict]:
    """분석 결과 → 노션 블록 리스트"""
    if not snippets:
        return [_callout("분석할 스니펫이 없습니다.", "⚠️")]

    now = datetime.now(KST).strftime("%Y-%m-%d %H:%M KST")
    blocks = []

    # ── 헤더
    period_str = period_label or f"{snippets[0]['date']} ~ {snippets[-1]['date']}"
    blocks.append(_callout(
        f"마지막 업데이트: {now}  |  분석 스니펫: {len(snippets)}개  |  기간: {period_str}",
        "📊"
    ))
    blocks.append(_divider())

    # ── 전체 요약
    blocks.append(_heading(2, "📝 전체 흐름 요약"))
    blocks.append(_paragraph(analysis.get("overall_summary", "")))
    blocks.append(_divider())

    # ── 주요 경보
    alert_days = analysis.get("alert_days", [])
    if alert_days:
        blocks.append(_heading(2, "🚨 주의 날짜"))
        for a in alert_days:
            blocks.append(_bullet(f"{a.get('date')}  —  {a.get('reason')}"))
        blocks.append(_divider())

    # ── 7개 지표
    blocks.append(_heading(2, "📈 7대 지표"))
    for key, (label, higher_is_bad) in METRIC_LABELS.items():
        metric = analysis.get(key, {})
        score  = metric.get("score", 0)
        level  = metric.get("level", "")
        reason = metric.get("reason", "")
        dates  = metric.get("evidence_dates", [])
        emoji  = LEVEL_EMOJI.get(level, "⚪")

        blocks.append(_heading(3, f"{label}"))
        blocks.append(_paragraph(f"{emoji} {level}  |  {score_to_bar(score)}"))
        blocks.append(_paragraph(reason))
        if dates:
            blocks.append(_paragraph(f"📅 근거 날짜: {', '.join(dates)}"))
        blocks.append(_paragraph(""))  # 간격

    blocks.append(_divider())

    # ── 통계 요약
    blocks.append(_heading(2, "📊 데이터 통계"))
    health_scores  = [s["health_score"] for s in snippets if s.get("health_score") is not None]
    feedback_scores = [s["feedback_score"] for s in snippets if s.get("feedback_score") is not None]

    if health_scores:
        avg_h = round(sum(health_scores) / len(health_scores), 1)
        min_h = min(health_scores)
        blocks.append(_bullet(f"헬스 점수 평균: {avg_h}/10  |  최저: {min_h}/10"))
    if feedback_scores:
        avg_f = round(sum(feedback_scores) / len(feedback_scores), 1)
        blocks.append(_bullet(f"피드백 점수 평균: {avg_f}/100"))
    blocks.append(_bullet(f"우선순위 달성률: {priority_rate}%"))

    blocks.append(_divider())

    # ── 개선 필요 영역
    improve = analysis.get("improvement_areas", [])
    if improve:
        blocks.append(_heading(2, "🔧 지속 개선 필요 영역"))
        for item in improve:
            blocks.append(_bullet(item))
        blocks.append(_divider())

    # ── 긍정 변화
    positive = analysis.get("positive_trends", [])
    if positive:
        blocks.append(_heading(2, "✨ 긍정적 변화"))
        for item in positive:
            blocks.append(_bullet(item))

    return blocks


def find_or_create_child_page(parent_id: str, title: str) -> str:
    """부모 페이지 아래에서 title 하위 페이지를 찾거나 새로 생성 후 ID 반환"""
    cursor = None
    try:
        while True:
            resp = notion.blocks.children.list(
                block_id=parent_id, start_cursor=cursor, page_size=100
            )
            for block in resp["results"]:
                if block.get("type") == "child_page":
                    if block["child_page"]["title"] == title:
                        return block["id"]
            if not resp.get("has_more"):
                break
            cursor = resp["next_cursor"]

        page = notion.pages.create(
            parent={"page_id": parent_id},
            properties={"title": {"title": [{"type": "text", "text": {"content": title}}]}},
        )
        return page["id"]
    except APIResponseError as exc:
        # Keep this short and structured so backend dashboard truncation still shows root cause.
        raise RuntimeError(
            "Notion API failed in find_or_create_child_page "
            f"(parent_id={parent_id}, title={title}, "
            f"code={getattr(exc, 'code', 'unknown')}, status={getattr(exc, 'status', 'unknown')}, "
            f"message={str(exc)})"
        ) from exc


def clear_page_blocks(page_id: str):
    """페이지 기존 블록 전체 삭제 (일부 실패해도 계속 진행)"""
    cursor = None
    while True:
        resp = notion.blocks.children.list(
            block_id=page_id, start_cursor=cursor, page_size=100
        )
        for block in resp["results"]:
            try:
                notion.blocks.delete(block_id=block["id"])
            except Exception:
                pass  # 이미 삭제됐거나 권한 없는 블록은 스킵
        if not resp.get("has_more"):
            break
        cursor = resp["next_cursor"]


def write_report_to_notion(page_id: str, blocks: list[dict]):
    """블록을 100개씩 나눠서 노션에 작성"""
    for i in range(0, len(blocks), 100):
        notion.blocks.children.append(
            block_id=page_id,
            children=blocks[i:i + 100],
        )


# ─── 공통 리포트 생성 헬퍼 ────────────────────────────────────────────────────

def _run_analysis(snippets: list[dict]) -> tuple[dict, float]:
    """
    스니펫 목록 → Gemini 7지표 분석 수행
    반환: (analysis dict, priority_rate float)
    """
    priority_rate = db.calc_priority_achievement(snippets)
    summary       = build_gemini_summary(snippets)
    prompt        = build_gemini_prompt(summary)
    analysis      = analyze_with_gemini(prompt)
    return analysis, priority_rate


def _generate_report(snippets: list[dict], page_id: str, period_label: str,
                     analysis: dict = None, priority_rate: float = None):
    """
    스니펫 리스트 → 노션 페이지 업데이트
    analysis가 주어지면 Gemini 재호출 없이 재사용 (run_weekly에서 분석 공유 시)
    """
    if not snippets:
        print(f"   ℹ️  [{period_label}] 스니펫 없음 → 스킵")
        return

    if analysis is None:
        analysis, priority_rate = _run_analysis(snippets)
    elif priority_rate is None:
        priority_rate = db.calc_priority_achievement(snippets)

    analysis_for_db = {
        "snippet_count":    len(snippets),
        "burnout_risk":     analysis.get("burnout_risk", {}).get("score"),
        "team_health":      analysis.get("team_health", {}).get("score"),
        "diligence":        analysis.get("diligence", {}).get("score"),
        "recurrence":       analysis.get("recurrence", {}).get("score"),
        "growth":           analysis.get("growth", {}).get("score"),
        "execution":        analysis.get("execution", {}).get("score"),
        "emotional_energy": analysis.get("emotional_energy", {}).get("score"),
        "details":          {k: analysis[k] for k in ["burnout_risk","team_health","diligence","recurrence","growth","execution","emotional_energy"] if k in analysis},
        "alert_days":       analysis.get("alert_days", []),
        "improvement_areas":analysis.get("improvement_areas", []),
        "positive_trends":  analysis.get("positive_trends", []),
        "overall_summary":  analysis.get("overall_summary", ""),
    }
    row_id = db.save_analysis(analysis_for_db)

    clear_page_blocks(page_id)
    blocks = build_report_blocks(analysis, snippets, priority_rate, period_label=period_label)
    write_report_to_notion(page_id, blocks)
    db.update_analysis_notion_id(row_id, page_id)
    print(f"   ✅ [{period_label}] 리포트 완료 → https://notion.so/{page_id.replace('-', '')}")


# ─── 1000.school 주간 스니펫 ──────────────────────────────────────────────────

def _get_weekly_snippet_from_school(headers: dict, week_monday: str) -> dict | None:
    """해당 주(월요일 날짜)의 기존 주간 스니펫 조회"""
    resp = requests.get(f"{API_BASE}/weekly-snippets", headers=headers)
    resp.raise_for_status()
    for item in resp.json().get("items", []):
        if item.get("week") == week_monday:
            return item
    return None


def save_weekly_snippet_to_school(content: str, week_monday: str) -> dict:
    """주간 스니펫 저장 (없으면 POST, 있으면 PUT)"""
    headers = {
        "Authorization": f"Bearer {SCHOOL_API_KEY}",
        "Content-Type": "application/json",
    }
    existing = _get_weekly_snippet_from_school(headers, week_monday)

    if existing:
        snippet_id = existing["id"]
        print(f"   📝 기존 주간 스니펫(id={snippet_id}) 수정 중...")
        resp = requests.put(
            f"{API_BASE}/weekly-snippets/{snippet_id}",
            json={"content": content},
            headers=headers,
        )
    else:
        print("   ✏️  새 주간 스니펫 작성 중...")
        resp = requests.post(
            f"{API_BASE}/weekly-snippets",
            json={"content": content},
            headers=headers,
        )

    resp.raise_for_status()
    return resp.json()


def gemini_weekly_snippet(snippets: list[dict], week_monday: date,
                          analysis: dict = None) -> str:
    """
    일간 스니펫 + AI 감독 분석 → 1000.school용 주간 스니펫 콘텐츠 생성 (Gemini)
    analysis가 주어지면 7지표 점수·요약·개선영역도 프롬프트에 포함해 더 풍부한 내용 생성
    """
    if not GEMINI_API_KEY:
        raise RuntimeError("GEMINI_API_KEY 없음")

    week_end = week_monday + timedelta(days=6)
    period   = f"{week_monday.strftime('%m/%d')}~{week_end.strftime('%m/%d')}"

    # ── 일간 스니펫 요약 블록
    daily_summaries = []
    for s in snippets:
        highlights = (s.get("highlights") or "").strip()[:120]
        lowlights  = (s.get("lowlights") or "").strip()[:120]
        goals      = (s.get("tomorrow_goals") or "").strip()[:100]
        health     = s.get("health_score")
        daily_summaries.append(
            f"[{s['date']}]\n하이라이트: {highlights}\n로우라이트: {lowlights}\n"
            f"내일목표: {goals}\n헬스: {health}/10"
        )

    # ── AI 감독 분석 블록 (있을 때만 추가)
    analysis_block_lines = []
    if analysis:
        scores = {
            "번아웃 위험도":  analysis.get("burnout_risk",     {}).get("score"),
            "팀 건강도":      analysis.get("team_health",      {}).get("score"),
            "성실도":         analysis.get("diligence",        {}).get("score"),
            "문제 재발성":    analysis.get("recurrence",       {}).get("score"),
            "성장 지수":      analysis.get("growth",           {}).get("score"),
            "실행 집중도":    analysis.get("execution",        {}).get("score"),
            "감정 에너지":    analysis.get("emotional_energy", {}).get("score"),
        }
        score_lines = [f"  {label}: {score}/100" for label, score in scores.items() if score is not None]
        analysis_block_lines = [
            "=== AI 감독 분석 결과 ===",
            "[ 7대 지표 점수 ]",
            *score_lines,
            "",
            "[ 전체 흐름 요약 ]",
            analysis.get("overall_summary", ""),
        ]
        improve = analysis.get("improvement_areas", [])
        if improve:
            analysis_block_lines += ["", "[ 지속 개선 필요 영역 ]", *[f"  - {i}" for i in improve]]
        positive = analysis.get("positive_trends", [])
        if positive:
            analysis_block_lines += ["", "[ 긍정적 변화 ]", *[f"  - {p}" for p in positive]]
        alert_days = analysis.get("alert_days", [])
        if alert_days:
            analysis_block_lines += ["", "[ 주의 날짜 ]",
                *[f"  - {a.get('date')}: {a.get('reason')}" for a in alert_days]]

    prompt_parts = [
        f"아래 일간 스니펫과 AI 감독 분석을 종합해서 {period} 주간 회고를 작성해라.",
        "출력은 반드시 JSON 객체 하나만 반환해라. 입력에 없는 사실은 절대 지어내지 마라.",
        "",
        "=== 일간 스니펫 ===",
        "\n\n".join(daily_summaries),
    ]
    if analysis_block_lines:
        prompt_parts += ["", *analysis_block_lines]

    prompt_parts += [
        "",
        "=== 필드별 작성 기준 ===",
        "",
        "weekly_highlight (배열, 3~5개):",
        "  - 이번 주 가장 의미 있는 성과나 긍정적 순간.",
        "  - AI 감독의 긍정적 변화·성장 지수도 반영해 구체적으로 작성.",
        "",
        "weekly_lowlight (배열, 1~3개):",
        "  - 이번 주 아쉬웠거나 반복된 문제.",
        "  - AI 감독의 지속 개선 필요 영역·주의 날짜도 반영해 작성.",
        "  - 문제가 없으면 ['특별한 로우라이트 없음'].",
        "",
        "next_week_priority (배열, 3~5개):",
        "  - 다음 주 집중할 우선순위. '영역: 행동 (목표량)' 형식 권장.",
        "  - AI 감독의 개선 필요 영역을 우선순위에 반드시 반영해라.",
        "",
        "growth_summary (문자열):",
        "  - 이번 주 전반적인 성장 또는 배움을 2~3문장으로.",
        "  - AI 감독의 전체 흐름 요약·성장 지수를 근거로 작성.",
        "",
        "team_contribution (배열, 1~3개):",
        "  - 이번 주 팀에 기여한 내용. AI 감독 팀 건강도도 참고.",
        "",
        "avg_health_score (정수, 1~10):",
        "  - 이번 주 평균 헬스 점수 (일간 스니펫 헬스 평균 기준).",
        "",
        "supervisor_comment (문자열):",
        "  - AI 감독 관점에서 이번 주 핵심 피드백 1~2문장.",
        "  - 번아웃 위험·팀 건강·감정 에너지 등 지표를 토대로 작성.",
        "  - AI 감독 분석이 없으면 빈 문자열 반환.",
        "",
        "JSON 키: weekly_highlight, weekly_lowlight, next_week_priority, "
        "growth_summary, team_contribution, avg_health_score, supervisor_comment",
    ]

    prompt = "\n".join(prompt_parts)

    url = (
        "https://generativelanguage.googleapis.com/v1beta/models/"
        f"gemini-2.5-flash:generateContent?key={GEMINI_API_KEY}"
    )
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0.4, "responseMimeType": "application/json"},
    }

    for attempt in range(1, 4):
        resp = requests.post(url, json=payload, timeout=60)
        if resp.ok:
            break
        if resp.status_code not in (429, 500, 503) or attempt == 3:
            raise RuntimeError(f"Gemini 주간 스니펫 요청 실패: {resp.status_code} / {resp.text[:200]}")
        time.sleep(attempt * 2)

    raw = resp.json().get("candidates", [{}])[0].get("content", {}).get("parts", [{}])[0].get("text", "").strip()
    raw = re.sub(r'^```(?:json)?\s*', '', raw)
    raw = re.sub(r'\s*```$', '', raw).strip()
    if not raw:
        raise ValueError("Gemini 주간 스니펫 응답 텍스트가 비어있음")
    parsed = json.loads(raw)

    def to_bullets(val) -> str:
        if isinstance(val, list):
            return "\n".join(f"- {item}" for item in val if item)
        return f"- {val}" if val else ""

    try:
        health = max(1, min(10, int(parsed.get("avg_health_score") or 5)))
    except (TypeError, ValueError):
        health = 5

    lines = [
        f"## 이번 주 하이라이트 ({period})",
        to_bullets(parsed.get("weekly_highlight", [])),
        "",
        "## 로우라이트",
        to_bullets(parsed.get("weekly_lowlight", [])),
        "",
        "## 다음 주 우선순위",
        to_bullets(parsed.get("next_week_priority", [])),
        "",
        "## 팀 기여",
        to_bullets(parsed.get("team_contribution", [])),
        "",
        "## 성장 요약",
        f"- {parsed.get('growth_summary', '')}",
        "",
        "## 헬스 체크 (10점)",
        f"- {health}/10 (주간 평균)",
    ]

    supervisor = parsed.get("supervisor_comment", "").strip()
    if supervisor:
        lines += ["", "## AI 감독 코멘트", f"- {supervisor}"]

    return "\n".join(lines)


# ─── 주간 리포트 ───────────────────────────────────────────────────────────────

def run_weekly(target_monday: date = None):
    """
    target_monday가 포함된 주(월~일)의 주간 리포트 생성/갱신
    target_monday=None → 이번 주 월요일 기준 (현재 진행 중인 주)
    """
    today = datetime.now(KST).date()
    if target_monday is None:
        target_monday = today - timedelta(days=today.weekday())  # 이번 주 월요일

    week_end = target_monday + timedelta(days=6)
    yesterday = today - timedelta(days=1)
    if week_end >= today:
        week_end = yesterday  # 오늘은 아직 작성 중이므로 제외

    week_num  = target_monday.isocalendar()[1]
    year      = target_monday.year
    page_title = f"W{week_num:02d} ({target_monday.strftime('%m/%d')}~{week_end.strftime('%m/%d')})"
    period_label = f"{year}-W{week_num:02d} ({target_monday.strftime('%m/%d')}~{week_end.strftime('%m/%d')})"

    print(f"\n📅 주간 리포트 생성 중: {period_label}")
    snippets = db.get_snippets_by_date_range(
        target_monday.strftime("%Y-%m-%d"),
        week_end.strftime("%Y-%m-%d"),
    )

    if not snippets:
        print(f"   ℹ️  [{period_label}] 스니펫 없음 → 스킵")
        return

    # ── Gemini 7지표 분석 (1번만 호출 → 주간 스니펫·노션 리포트 공유)
    print(f"   🤖 AI 감독 분석 중...")
    try:
        analysis, priority_rate = _run_analysis(snippets)
        print(f"   ✅ 분석 완료")
    except Exception as e:
        print(f"   ⚠️  AI 분석 실패: {e} → 분석 없이 진행")
        analysis, priority_rate = None, db.calc_priority_achievement(snippets)

    # ── 1000.school 주간 스니펫 업로드 (일간 스니펫 + AI 분석 종합)
    print(f"   📤 1000.school 주간 스니펫 업로드 중...")
    try:
        weekly_content = gemini_weekly_snippet(snippets, target_monday, analysis=analysis)
        result = save_weekly_snippet_to_school(weekly_content, target_monday.strftime("%Y-%m-%d"))
        print(f"   ✅ 주간 스니펫 업로드 완료 (id={result.get('id')})")
    except Exception as e:
        print(f"   ⚠️  주간 스니펫 업로드 실패: {e}")

    # ── 노션 주간 리포트 (분석 결과 재사용)
    container_id = find_or_create_child_page(NOTION_PAGE_ID, WEEKLY_CONTAINER_TITLE)
    page_id      = find_or_create_child_page(container_id, page_title)
    _generate_report(snippets, page_id, period_label, analysis=analysis, priority_rate=priority_rate)


# ─── 월간 리포트 ───────────────────────────────────────────────────────────────

def run_monthly(year: int = None, month: int = None):
    """
    year/month 월의 월간 리포트 생성/갱신
    None → 이번 달 기준
    """
    today = datetime.now(KST).date()
    if year is None:
        year = today.year
    if month is None:
        month = today.month

    start = date(year, month, 1)
    # 해당 월의 마지막 날
    if month == 12:
        end = date(year + 1, 1, 1) - timedelta(days=1)
    else:
        end = date(year, month + 1, 1) - timedelta(days=1)
    yesterday = today - timedelta(days=1)
    if end >= today:
        end = yesterday  # 오늘은 아직 작성 중이므로 제외

    page_title   = f"{year}-{month:02d} ({month}월)"
    period_label = f"{year}년 {month}월 ({start.strftime('%m/%d')}~{end.strftime('%m/%d')})"

    print(f"\n📆 월간 리포트 생성 중: {period_label}")
    snippets = db.get_snippets_by_date_range(
        start.strftime("%Y-%m-%d"),
        end.strftime("%Y-%m-%d"),
    )

    if not snippets:
        print(f"   ℹ️  [{period_label}] 스니펫 없음 → 스킵")
        return

    # ── Gemini 7지표 분석 (1번만 호출 → 노션 리포트에 사용)
    print(f"   🤖 AI 감독 분석 중...")
    try:
        analysis, priority_rate = _run_analysis(snippets)
        print(f"   ✅ 분석 완료")
    except Exception as e:
        print(f"   ⚠️  AI 분석 실패: {e} → 분석 없이 진행")
        analysis, priority_rate = None, db.calc_priority_achievement(snippets)

    container_id = find_or_create_child_page(NOTION_PAGE_ID, MONTHLY_CONTAINER_TITLE)
    page_id      = find_or_create_child_page(container_id, page_title)
    _generate_report(snippets, page_id, period_label, analysis=analysis, priority_rate=priority_rate)


# ─── 메인 ─────────────────────────────────────────────────────────────────────

def run():
    """이번 주 + 이번 달 리포트 생성/갱신 (수동 실행 또는 9시 자동 실행)"""
    print("=" * 50)
    print("📊 AI 감독 리포트 생성 시작")
    print("=" * 50)

    db.init_db()

    print("\n[1/3] 스니펫 동기화 중...")
    sync_snippets()

    print("\n[2/3] 주간 리포트...")
    run_weekly()

    print("\n[3/3] 월간 리포트...")
    run_monthly()

    print("\n" + "=" * 50)
    print("✅ 모든 리포트 생성 완료")
    print("=" * 50)


if __name__ == "__main__":
    run()
