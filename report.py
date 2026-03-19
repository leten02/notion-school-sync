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
import json
import requests
from datetime import datetime
from dotenv import load_dotenv
from notion_client import Client

import db

load_dotenv()

# ─── 설정 ─────────────────────────────────────────────────────────────────────

SCHOOL_API_KEY  = os.getenv("SCHOOL_API_KEY")
NOTION_TOKEN    = os.getenv("NOTION_TOKEN")
NOTION_PAGE_ID  = os.getenv("NOTION_PAGE_ID")   # 리포트 페이지가 들어갈 부모 페이지
GEMINI_API_KEY  = os.getenv("GEMINI_API_KEY")    # 나중에 추가

API_BASE        = "https://api.1000.school"
REPORT_TITLE    = "📊 AI 감독 리포트"

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
        dates = [item["date"] for item in items]
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
        date          = s.get("date", "")
        health        = s.get("health_score")
        fb_score      = s.get("feedback_score")
        highlights    = (s.get("highlights") or "").strip()[:120]
        lowlights     = (s.get("lowlights") or "").strip()[:120]
        goals         = (s.get("tomorrow_goals") or "").strip()[:100]
        team          = (s.get("team_mentions") or "").strip()[:100]
        learnings     = (s.get("learnings") or "").strip()[:100]

        block = f"""[{date}]
헬스:{health}/10 | 피드백:{fb_score}점
하이라이트: {highlights}
로우라이트: {lowlights}
내일목표: {goals}
팀기여: {team}
배움: {learnings}"""
        lines.append(block)

    priority_rate = db.calc_priority_achievement(snippets)
    lines.append(f"\n[통계] 우선순위 달성률: {priority_rate}%")
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
    resp = requests.post(url, json=payload, timeout=60)
    resp.raise_for_status()

    raw = resp.json()["candidates"][0]["content"]["parts"][0]["text"]
    # JSON 파싱
    raw = raw.strip().lstrip("```json").lstrip("```").rstrip("```").strip()
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


def build_report_blocks(analysis: dict, snippets: list[dict], priority_rate: float) -> list[dict]:
    """분석 결과 → 노션 블록 리스트"""
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    blocks = []

    # ── 헤더
    blocks.append(_callout(
        f"마지막 업데이트: {now}  |  분석 스니펫: {len(snippets)}개  |  기간: {snippets[0]['date']} ~ {snippets[-1]['date']}",
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


def find_or_create_report_page() -> str:
    """리포트 페이지가 있으면 ID 반환, 없으면 새로 생성"""
    cursor = None
    while True:
        resp = notion.blocks.children.list(
            block_id=NOTION_PAGE_ID, start_cursor=cursor, page_size=100
        )
        for block in resp["results"]:
            if block.get("type") == "child_page":
                if block["child_page"]["title"] == REPORT_TITLE:
                    return block["id"]
        if not resp.get("has_more"):
            break
        cursor = resp["next_cursor"]

    # 없으면 생성
    page = notion.pages.create(
        parent={"page_id": NOTION_PAGE_ID},
        properties={"title": {"title": [{"type": "text", "text": {"content": REPORT_TITLE}}]}},
    )
    return page["id"]


def clear_page_blocks(page_id: str):
    """페이지 기존 블록 전체 삭제"""
    cursor = None
    while True:
        resp = notion.blocks.children.list(
            block_id=page_id, start_cursor=cursor, page_size=100
        )
        for block in resp["results"]:
            notion.blocks.delete(block_id=block["id"])
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


# ─── 메인 ─────────────────────────────────────────────────────────────────────

def run():
    print("=" * 50)
    print("📊 AI 감독 리포트 생성 시작")
    print("=" * 50)

    # 1. DB 초기화
    db.init_db()

    # 2. 스니펫 동기화
    print("\n[1/4] 스니펫 동기화 중...")
    sync_snippets()

    # 3. Gemini 분석
    print("\n[2/4] Gemini 분석 중...")
    snippets       = db.get_all_snippets()
    priority_rate  = db.calc_priority_achievement(snippets)
    summary        = build_gemini_summary(snippets)
    prompt         = build_gemini_prompt(summary)
    analysis       = analyze_with_gemini(prompt)
    print("   ✅ 분석 완료")

    # 4. DB에 결과 저장 (각 지표에서 score만 추출)
    analysis["snippet_count"] = len(snippets)
    analysis_for_db = {
        "snippet_count":   len(snippets),
        "burnout_risk":    analysis.get("burnout_risk", {}).get("score"),
        "team_health":     analysis.get("team_health", {}).get("score"),
        "diligence":       analysis.get("diligence", {}).get("score"),
        "recurrence":      analysis.get("recurrence", {}).get("score"),
        "growth":          analysis.get("growth", {}).get("score"),
        "execution":       analysis.get("execution", {}).get("score"),
        "emotional_energy":analysis.get("emotional_energy", {}).get("score"),
        "details":         {k: analysis[k] for k in ["burnout_risk","team_health","diligence","recurrence","growth","execution","emotional_energy"] if k in analysis},
        "alert_days":      analysis.get("alert_days", []),
        "improvement_areas": analysis.get("improvement_areas", []),
        "positive_trends": analysis.get("positive_trends", []),
        "overall_summary": analysis.get("overall_summary", ""),
    }
    row_id = db.save_analysis(analysis_for_db)

    # 5. 노션 리포트 페이지 업데이트
    print("\n[3/4] 노션 리포트 페이지 업데이트 중...")
    page_id = find_or_create_report_page()
    clear_page_blocks(page_id)
    blocks = build_report_blocks(analysis, snippets, priority_rate)
    write_report_to_notion(page_id, blocks)
    db.update_analysis_notion_id(row_id, page_id)
    print(f"   ✅ 노션 페이지 업데이트 완료 (id: {page_id})")

    print("\n[4/4] 완료!")
    print(f"   📄 리포트 페이지: https://notion.so/{page_id.replace('-', '')}")
    print("=" * 50)


if __name__ == "__main__":
    run()
