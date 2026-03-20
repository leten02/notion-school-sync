"""
Notion 페이지 → 1000.school 일간 스니펫 자동 작성

사용법:
  python main.py          # 한 번만 실행
  python main.py --watch  # 10분마다 자동 체크 (변경 → 원문 즉시 업로드, Tomorrow+30분 → Gemini polish)

노션 구조:
  부모 페이지 (NOTION_PAGE_ID)
  └─ 오늘 날짜 하위 페이지 (예: "2026-03-18")  ← 여기 내용을 읽어서 올림
"""

import os
import re
import sys
import time
import json
import hashlib
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo
import requests
from dotenv import load_dotenv
from notion_client import Client
import report as report_module
import sync_to_notion as sync_module

KST = ZoneInfo("Asia/Seoul")
DAY_START_HOUR = 9  # 오전 9시 기준으로 날짜 전환


def kst_now() -> datetime:
    """현재 KST 시각 반환"""
    return datetime.now(KST)


def effective_date() -> date:
    """
    실질적 오늘 날짜 반환 (KST 기준, 오전 9시 이후부터 다음날로 인식)
    예: 오전 8:59 → 어제 날짜 / 오전 9:00 → 오늘 날짜
    """
    now = kst_now()
    if now.hour < DAY_START_HOUR:
        return (now - timedelta(days=1)).date()
    return now.date()

load_dotenv()

# ─── 설정 ────────────────────────────────────────────────────────────────────

NOTION_TOKEN   = os.getenv("NOTION_TOKEN")
NOTION_PAGE_ID = os.getenv("NOTION_PAGE_ID")
SCHOOL_API_KEY = os.getenv("SCHOOL_API_KEY")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

API_BASE      = "https://api.1000.school"
GEMINI_MODEL  = "gemini-2.5-flash"
GEMINI_URL    = (
    f"https://generativelanguage.googleapis.com/v1beta/models/"
    f"{GEMINI_MODEL}:generateContent"
)

notion = Client(auth=NOTION_TOKEN)

# ─── 노션 오늘 날짜 하위 페이지 찾기 ─────────────────────────────────────────

def find_today_child_page(parent_id: str) -> str | None:
    """부모 페이지에서 오늘 날짜 하위 페이지 ID를 찾아 반환 (KST 9시 기준)"""
    today = effective_date()
    title = today.strftime("%Y-%m-%d")

    cursor = None
    while True:
        resp = notion.blocks.children.list(
            block_id=parent_id,
            start_cursor=cursor,
            page_size=100,
        )
        for block in resp["results"]:
            if block.get("type") == "child_page":
                if block["child_page"]["title"] == title:
                    return block["id"]
        if not resp.get("has_more"):
            break
        cursor = resp["next_cursor"]

    return None


# ─── 노션 콘텐츠 가져오기 ────────────────────────────────────────────────────

def get_notion_content(page_id: str) -> str:
    """노션 페이지 블록을 읽어서 텍스트로 변환"""
    blocks, cursor = [], None

    while True:
        resp = notion.blocks.children.list(
            block_id=page_id,
            start_cursor=cursor,
            page_size=100,
        )
        blocks.extend(resp["results"])
        if not resp.get("has_more"):
            break
        cursor = resp["next_cursor"]

    return _parse_blocks(blocks)


def _parse_blocks(blocks: list) -> str:
    lines = []
    numbered_counter = 0

    for block in blocks:
        btype = block.get("type")
        data  = block.get(btype, {})
        text  = _rich_text(data.get("rich_text", []))

        if btype == "paragraph":
            numbered_counter = 0
            lines.append(text)

        elif btype == "heading_1":
            numbered_counter = 0
            lines.append(f"# {text}")

        elif btype == "heading_2":
            numbered_counter = 0
            lines.append(f"## {text}")

        elif btype == "heading_3":
            numbered_counter = 0
            lines.append(f"### {text}")

        elif btype == "bulleted_list_item":
            numbered_counter = 0
            lines.append(f"- {text}")

        elif btype == "numbered_list_item":
            numbered_counter += 1
            lines.append(f"{numbered_counter}. {text}")

        elif btype == "to_do":
            numbered_counter = 0
            check = "✅" if data.get("checked") else "☐"
            lines.append(f"{check} {text}")

        elif btype == "quote":
            numbered_counter = 0
            lines.append(f"> {text}")

        elif btype == "code":
            numbered_counter = 0
            lang = data.get("language", "")
            lines.append(f"```{lang}\n{text}\n```")

        elif btype == "divider":
            numbered_counter = 0
            lines.append("---")

        elif btype == "callout":
            numbered_counter = 0
            emoji = data.get("icon", {}).get("emoji", "")
            lines.append(f"{emoji} {text}".strip())

    return "\n\n".join(line for line in lines if line.strip())


def _rich_text(rich_text: list) -> str:
    return "".join(t.get("plain_text", "") for t in rich_text)



# ─── Gemini Polish (노션 메모 → 정형화된 스니펫) ─────────────────────────────

def gemini_polish_content(raw_content: str, snippet_date: str) -> str:
    """
    노션 원문(짧은 메모 or 템플릿 작성본)을
    Gemini로 다듬어 1000.school 스니펫 형식으로 변환
    """
    if not GEMINI_API_KEY:
        print("   ⚠️  GEMINI_API_KEY 없음 → polish 스킵", flush=True)
        return raw_content

    prompt = "\n".join([
        f"입력된 노션 데일리 메모를 바탕으로 {snippet_date}의 데일리 회고를 작성해라.",
        "출력은 반드시 JSON 객체 하나만 반환해라. 입력에 없는 사실은 절대 지어내지 마라.",
        "",
        "### 필드별 작성 기준",
        "",
        "today_work (배열, 필수):",
        "  - 오늘 수행한 작업을 항목별로 분리해서 배열로 반환해라.",
        "  - 각 항목은 '무엇을 했는지 + 어느 정도까지 했는지'를 한 줄로 담아라.",
        "  - 예: '인간본성의 과학적 이해 notebookLM 정리와 문제 풀이 (중요 개념 정리 및 오답 노트 보강)'",
        "  - 너무 짧거나 뭉뚱그리지 말고, 맥락이 느껴지도록 구체적으로 작성해라.",
        "",
        "purpose (배열, 필수):",
        "  - today_work 항목들의 수행 목적을 각각 1줄로 배열로 반환해라.",
        "  - 단순 나열이 아니라 '왜 했는지'가 드러나도록 작성해라.",
        "",
        "highlight (배열, 1~3개):",
        "  - 오늘 중 가장 의미 있었던 성과나 긍정적 순간을 추려서 배열로 반환해라.",
        "  - 구체적인 결과나 변화가 드러나도록 작성해라.",
        "  - 예: 'Daily Snippet 적용으로 작업 우선순위 및 집중 시간이 개선됨'",
        "",
        "lowlight (배열, 1~3개):",
        "  - 아쉬웠거나 문제가 된 상황을 배열로 반환해라.",
        "  - 단순 감상이 아니라 무엇이 문제였는지 구체적으로 작성해라.",
        "  - 명확한 문제가 없으면 ['특별한 로우라이트 없음'] 으로 반환해라.",
        "",
        "tomorrow_priority (배열, 필수):",
        "  - 내일 해야 할 일을 항목별로 배열로 반환해라.",
        "  - 각 항목은 '영역: 구체적 행동 (세부 방법 또는 목표량)' 형식으로 작성해라.",
        "  - 예: 'NotionAPI 개발: 소수점 처리 로직 버그 수정 및 리포트 엔드포인트 완료 (테스트 케이스 작성 포함)'",
        "  - 막연한 계획이 아니라 실행 가능한 수준으로 구체화해라.",
        "",
        "team_value (배열, 필수):",
        "  - 오늘 팀에 기여한 내용을 항목별로 배열로 반환해라.",
        "  - 팀에 어떤 영향을 주었는지 결과 중심으로 작성해라.",
        "",
        "learning_or_note (문자열):",
        "  - 오늘 새롭게 깨달은 점이나 남길 말을 1~2문장으로 작성해라.",
        "  - 구체적인 경험에서 나온 인사이트여야 한다.",
        "",
        "health_score (정수, 1~10):",
        "  - 오늘의 신체적·정신적 컨디션을 1~10으로 평가해라.",
        "",
        "health_reason (문자열):",
        "  - health_score의 이유를 한 줄로 작성해라.",
        "  - 예: '허리 통증으로 활동성 저하 → 스트레칭/운동 계획 필요'",
        "",
        "JSON 키는 다음만 사용해라: today_work, purpose, highlight, lowlight, tomorrow_priority, team_value, learning_or_note, health_score, health_reason",
        "today_work, purpose, highlight, lowlight, tomorrow_priority, team_value 는 반드시 JSON 배열(array)로 반환해라.",
        "learning_or_note, health_reason 은 문자열(string)로 반환해라.",
        "",
        "[노션 원문 시작]",
        raw_content,
        "[노션 원문 끝]",
    ])

    payload = {
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": 0.5,
            "responseMimeType": "application/json",
        },
    }

    # 최대 3번 재시도
    for attempt in range(1, 4):
        resp = requests.post(
            GEMINI_URL,
            params={"key": GEMINI_API_KEY},
            json=payload,
            timeout=60,
        )
        if resp.ok:
            break
        if resp.status_code not in (429, 500, 503) or attempt == 3:
            raise RuntimeError(f"Gemini 요청 실패: {resp.status_code} / {resp.text[:200]}")
        time.sleep(attempt * 1.5)

    # 응답 파싱
    parts = resp.json().get("candidates", [{}])[0].get("content", {}).get("parts", [])
    raw_text = "".join(p.get("text", "") for p in parts).strip()

    # JSON 추출
    if raw_text.startswith("{"):
        parsed = json.loads(raw_text)
    else:
        import re
        m = re.search(r'\{[\s\S]*\}', raw_text)
        if not m:
            raise ValueError("Gemini 응답에서 JSON을 찾지 못했습니다.")
        parsed = json.loads(m.group(0))

    health = int(parsed.get("health_score", 5))
    health = max(1, min(10, health))
    health_reason = parsed.get("health_reason", "")

    def to_bullets(val) -> str:
        """문자열 또는 배열을 '- 항목' 형태 여러 줄로 변환"""
        if isinstance(val, list):
            return "\n".join(f"- {item}" for item in val if item)
        return f"- {val}" if val else ""

    health_line = f"- {health}/10"
    if health_reason:
        health_line += f" ({health_reason})"

    return "\n".join([
        "## 오늘 한 일",
        to_bullets(parsed.get("today_work", [])),
        "",
        "## 수행 목적",
        to_bullets(parsed.get("purpose", [])),
        "",
        "## 하이라이트",
        to_bullets(parsed.get("highlight", [])),
        "",
        "## 로우라이트",
        to_bullets(parsed.get("lowlight", [])),
        "",
        "## 내일의 우선순위",
        to_bullets(parsed.get("tomorrow_priority", [])),
        "",
        "## 오늘 내가 팀에 기여한 가치",
        to_bullets(parsed.get("team_value", [])),
        "",
        "## 오늘의 배움 또는 남길 말",
        f"- {parsed.get('learning_or_note', '')}",
        "",
        "## 헬스 체크 (10점)",
        health_line,
    ])


# ─── 1000.school 일간 스니펫 작성 ─────────────────────────────────────────────

def get_today_snippet(headers: dict) -> dict | None:
    """오늘의 스니펫 정보 조회 (없으면 None)"""
    resp = requests.get(f"{API_BASE}/daily-snippets/page-data", headers=headers)
    resp.raise_for_status()
    data = resp.json()
    return data.get("snippet")  # None이면 오늘 작성한 스니펫 없음


def save_snippet(content: str) -> dict:
    """스니펫 저장 (없으면 POST, 있으면 PUT)"""
    headers = {
        "Authorization": f"Bearer {SCHOOL_API_KEY}",
        "Content-Type": "application/json",
    }

    existing = get_today_snippet(headers)

    if existing:
        snippet_id = existing["id"]
        print(f"📝 기존 스니펫(id={snippet_id}) 수정 중...")
        resp = requests.put(
            f"{API_BASE}/daily-snippets/{snippet_id}",
            json={"content": content},
            headers=headers,
        )
    else:
        print("✏️  새 스니펫 작성 중...")
        resp = requests.post(
            f"{API_BASE}/daily-snippets",
            json={"content": content},
            headers=headers,
        )

    resp.raise_for_status()
    return resp.json()


# ─── 메인 ────────────────────────────────────────────────────────────────────

def get_page_last_edited(page_id: str) -> str:
    """페이지 마지막 수정 시간 반환"""
    resp = notion.pages.retrieve(page_id=page_id)
    return resp.get("last_edited_time", "")


def run_once(polish: bool = False) -> str | None:
    """
    한 번 실행. 업로드 성공 시 실제 업로드된 content 반환, 실패 시 None 반환.
    polish=True 이면 Gemini로 내용을 다듬은 후 업로드.
    반환값을 해시로 저장해야 역방향 동기화 루프를 막을 수 있음.
    """
    today = effective_date()
    title = today.strftime("%Y-%m-%d")

    page_id = find_today_child_page(NOTION_PAGE_ID)
    if not page_id:
        print(f"❌ '{title}' 하위 페이지 없음.", flush=True)
        return None

    content = get_notion_content(page_id)
    if not content.strip():
        print("⏳ 페이지 내용이 비어있습니다.", flush=True)
        return None

    if polish:
        print(f"[{_now()}] ✨ Gemini 다듬기 시작...", flush=True)
        try:
            content = gemini_polish_content(content, title)
            print(f"[{_now()}] ✅ Gemini 다듬기 완료", flush=True)
        except Exception as e:
            print(f"[{_now()}] ⚠️  Gemini 다듬기 실패 → 원문 그대로 업로드: {e}", flush=True)

    result = save_snippet(content)
    print(f"✅ 업로드 완료! 스니펫 ID: {result['id']} | {result['date']}", flush=True)
    print(f"   내용: {content[:60]}{'...' if len(content) > 60 else ''}", flush=True)
    return content  # 실제 업로드된 내용 반환 (해시 저장용)


def _heading_block(level: int, text: str) -> dict:
    t = f"heading_{level}"
    return {"object": "block", "type": t, t: {
        "rich_text": [{"type": "text", "text": {"content": text}}]
    }}


def _paragraph_block(text: str = "") -> dict:
    return {"object": "block", "type": "paragraph", "paragraph": {
        "rich_text": [{"type": "text", "text": {"content": text}}]
    }}


DAILY_TEMPLATE = [
    _heading_block(2, "What"),
    _paragraph_block(),
    _heading_block(2, "Why"),
    _paragraph_block(),
    _heading_block(2, "Team Value Added"),
    _paragraph_block(),
    _heading_block(2, "Highlight"),
    _paragraph_block(),
    _heading_block(2, "Lowlight"),
    _paragraph_block(),
    _heading_block(2, "Tomorrow"),
    _paragraph_block(),
]


def create_today_notion_page(title: str) -> str | None:
    """오늘 날짜 노션 페이지 자동 생성 (템플릿 포함), 생성된 page_id 반환"""
    try:
        page = notion.pages.create(
            parent={"page_id": NOTION_PAGE_ID},
            properties={
                "title": {"title": [{"type": "text", "text": {"content": title}}]}
            },
            children=DAILY_TEMPLATE,
        )
        return page["id"]
    except Exception as e:
        print(f"[{_now()}] ⚠️  페이지 생성 실패: {e}", flush=True)
        return None


def watch(interval: int = 600):
    """interval초마다 노션 변경 감지 후 자동 업로드 + 리포트 갱신"""
    print(f"👀 Watch 모드 시작 (매 {interval//60}분마다 체크, Ctrl+C로 종료)", flush=True)
    print(f"   📅 날짜 기준: KST 오전 {DAY_START_HOUR}시\n", flush=True)

    last_edited             = None
    last_report_date        = None
    last_page_created       = None
    last_sync_date          = None
    last_reverse_sync_at    = None
    last_reverse_sync_hash  = None   # 마지막으로 역방향 동기화된 1000.school 내용 해시
    last_tracked_date       = None   # 날짜 변경 감지용
    last_change_detected_at = None   # 마지막 노션 변경 감지 시각 (polish 타이밍 계산용)
    polish_done_date        = None   # 해당 날짜 polish 완료 여부 (중복 방지)

    REVERSE_SYNC_INTERVAL = 600  # 10분 (초)
    POLISH_WAIT_MIN       = 30   # Tomorrow 섹션 감지 후 polish까지 대기 분

    while True:
        try:
            now   = kst_now()
            today = effective_date()
            title = today.strftime("%Y-%m-%d")

            # ── 날짜가 바뀌면 상태 리셋 ──────────────────────────────────────
            if today != last_tracked_date:
                last_edited             = None
                last_reverse_sync_hash  = None
                last_change_detected_at = None  # 새 날 변경 감지 초기화
                polish_done_date        = None  # 새 날 polish 가능하도록
                last_tracked_date       = today

            # ── 오늘 페이지 한 번만 조회 (루프당 API 1번으로 절약) ───────────
            page_id = find_today_child_page(NOTION_PAGE_ID)

            # ── 오전 9시 이후 하루 1번: 오늘 날짜 페이지 자동 생성 ──────────
            if now.hour >= DAY_START_HOUR and last_page_created != today:
                if not page_id:
                    print(f"[{_now()}] 📄 오전 9시 지남 → '{title}' 노션 페이지 자동 생성 중...", flush=True)
                    page_id = create_today_notion_page(title)
                    if page_id:
                        print(f"[{_now()}] ✅ '{title}' 페이지 생성 완료", flush=True)
                        last_page_created = today  # 성공했을 때만 저장 (실패 시 다음 루프에서 재시도)
                    else:
                        print(f"[{_now()}] ⚠️  페이지 생성 실패 → 다음 루프에서 재시도", flush=True)
                else:
                    last_page_created = today  # 페이지가 이미 존재하면 생성 불필요

            # ── 오전 9시 이후 하루 1번: 전날 스니펫 최종본 → 노션 반영 ────────
            if now.hour >= DAY_START_HOUR and last_sync_date != today:
                yesterday = (now - timedelta(days=1)).date().strftime("%Y-%m-%d")
                print(f"[{_now()}] 🔄 전날({yesterday}) 스니펫 최종본 → 노션 반영 중...", flush=True)
                try:
                    sync_module.main(update_existing=True, only_date=yesterday)
                    last_sync_date = today
                    print(f"[{_now()}] ✅ '{yesterday}' 노션 반영 완료", flush=True)
                except Exception as se:
                    print(f"[{_now()}] ⚠️  동기화 실패: {se}", flush=True)

            # ── 오전 9시 이후 하루 1번: AI 감독 리포트 자동 갱신 ────────────
            if now.hour >= DAY_START_HOUR and last_report_date != today:
                print(f"[{_now()}] 📊 오전 9시 지남 → AI 감독 리포트 자동 갱신 중...", flush=True)
                try:
                    report_module.run()
                    last_report_date = today
                    print(f"[{_now()}] ✅ 리포트 갱신 완료", flush=True)
                except Exception as re_err:
                    print(f"[{_now()}] ⚠️  리포트 갱신 실패: {re_err}", flush=True)

            # ── 노션 변경 감지 → 원문 즉시 업로드 ───────────────────────────
            if not page_id:
                print(f"[{_now()}] ⏳ '{title}' 페이지 없음. 대기 중...", flush=True)
            else:
                edited = get_page_last_edited(page_id)

                if edited != last_edited:
                    print(f"[{_now()}] 🔄 변경 감지! 원문 업로드 중...", flush=True)
                    uploaded_content = run_once(polish=False)
                    if uploaded_content is not None:
                        last_edited             = edited
                        last_change_detected_at = now   # 변경 시각 기록
                        last_reverse_sync_hash  = _content_hash(uploaded_content)
                else:
                    print(f"[{_now()}] ✓ 변경 없음", flush=True)

            # ── Gemini Polish 조건 체크 ──────────────────────────────────
            # 조건 1: Tomorrow 섹션 있음 + 30분 무변경 → polish (작성 완료 판단)
            # 조건 2: 8:50~8:59 폴백 → Tomorrow 못 적어도 9시 전에 강제 polish
            if page_id and polish_done_date != today and last_change_detected_at:
                minutes_since = (now - last_change_detected_at).total_seconds() / 60
                should_polish = False
                polish_reason = ""

                if minutes_since >= POLISH_WAIT_MIN:
                    has_tmr, _ = _has_tomorrow_content(page_id)
                    if has_tmr:
                        should_polish = True
                        polish_reason = f"Tomorrow 섹션 확인 + {POLISH_WAIT_MIN}분 경과"

                if not should_polish and now.hour == 8 and now.minute >= 50:
                    should_polish = True
                    polish_reason = "8:50 폴백 (Tomorrow 미입력 대비 강제 polish)"

                if should_polish:
                    print(f"[{_now()}] ✨ Gemini Polish 시작 ({polish_reason})", flush=True)
                    polished = run_once(polish=True)
                    if polished is not None:
                        polish_done_date       = today
                        last_reverse_sync_hash = _content_hash(polished)
                        try:
                            last_edited = get_page_last_edited(page_id)  # 루프 방지
                        except Exception:
                            pass
                        print(f"[{_now()}] ✅ Polish 완료 — 오늘 추가 polish 없음", flush=True)

            # ── 10분마다: 1000.school → 노션 역방향 동기화 ─────────────────
            # 해시 비교로 실제 변경된 경우만 덮어씀 (깜빡임 방지)
            now_ts = now  # kst_now() 결과 재사용 (datetime.now() 혼용 방지)
            if last_reverse_sync_at is None or \
               (now_ts - last_reverse_sync_at).total_seconds() >= REVERSE_SYNC_INTERVAL:

                last_reverse_sync_at = now_ts  # 타이머 항상 리셋

                try:
                    school_headers = {"Authorization": f"Bearer {SCHOOL_API_KEY}"}
                    school_snippet = get_today_snippet(school_headers)

                    if school_snippet:
                        school_content  = school_snippet.get("content", "") or ""
                        school_feedback = school_snippet.get("feedback") or ""
                        if isinstance(school_feedback, dict):
                            school_feedback = json.dumps(school_feedback, ensure_ascii=False)
                        school_hash = _content_hash(school_content + school_feedback)

                        if school_hash != last_reverse_sync_hash:
                            print(f"[{_now()}] 🔁 1000.school 변경 감지 → 노션 업데이트 중... ({title})", flush=True)
                            sync_module.main(update_existing=True, only_date=title)
                            last_reverse_sync_hash = school_hash
                            print(f"[{_now()}] ✅ 역방향 동기화 완료 ({title})", flush=True)
                            # 역방향 동기화 후 last_edited 갱신 → 순방향 업로드 루프 방지
                            if page_id:
                                last_edited = get_page_last_edited(page_id)
                        else:
                            print(f"[{_now()}] ✓ 1000.school 내용 동일 → 역방향 동기화 스킵", flush=True)
                    else:
                        print(f"[{_now()}] ℹ️  오늘 스니펫 없음 → 역방향 동기화 스킵", flush=True)

                except Exception as se:
                    print(f"[{_now()}] ⚠️  역방향 동기화 실패: {se}", flush=True)

        except Exception as e:
            print(f"[{_now()}] ⚠️  오류: {e}", flush=True)

        time.sleep(interval)


def _now() -> str:
    return kst_now().strftime("%H:%M:%S")


def _content_hash(text: str) -> str:
    """텍스트 내용의 해시 반환 (공백 정규화 후 비교용)"""
    normalized = " ".join(text.split())
    return hashlib.md5(normalized.encode()).hexdigest()


def _has_tomorrow_content(page_id: str) -> tuple[bool, str]:
    """
    Tomorrow / 내일 섹션에 실제 내용이 있는지 확인.
    반환: (내용 있음 여부, 전체 content 문자열)
    content도 같이 반환해서 호출 측에서 재사용 가능하게.
    """
    content = get_notion_content(page_id)
    pattern = r'#+[^\n]*(?:Tomorrow|내일)[^\n]*\n(.*?)(?=\n#+|\Z)'
    m = re.search(pattern, content, re.DOTALL | re.IGNORECASE)
    if m and m.group(1) and m.group(1).strip():
        return True, content
    return False, content


def make_template():
    """
    오늘 날짜 노션 페이지에 데일리 템플릿 추가
    - 페이지 없으면 새로 생성 (템플릿 포함)
    - 페이지 있으면 맨 위에 템플릿 블록 추가
    """
    today = effective_date()
    title = today.strftime("%Y-%m-%d")
    print(f"📄 '{title}' 페이지 템플릿 설정 중...")

    page_id = find_today_child_page(NOTION_PAGE_ID)

    if not page_id:
        # 페이지 없으면 새로 생성
        page_id = create_today_notion_page(title)
        if page_id:
            print(f"✅ '{title}' 페이지 새로 생성 + 템플릿 완료!")
        else:
            print("❌ 페이지 생성 실패")
    else:
        # 페이지 있으면 기존 블록 확인 후 템플릿 추가
        existing = notion.blocks.children.list(block_id=page_id, page_size=5)
        if existing["results"]:
            print(f"⚠️  '{title}' 페이지에 이미 내용이 있어요.")
            answer = input("기존 내용 위에 템플릿을 추가할까요? (y/n): ").strip().lower()
            if answer != "y":
                print("취소됐어요.")
                return
        notion.blocks.children.append(block_id=page_id, children=DAILY_TEMPLATE)
        print(f"✅ '{title}' 페이지에 템플릿 추가 완료!")


if __name__ == "__main__":
    if "--watch" in sys.argv:
        watch()
    elif "--report" in sys.argv:
        report_module.run()
    elif "--template" in sys.argv:
        make_template()
    else:
        today = effective_date()
        title = today.strftime("%Y-%m-%d")
        print(f"📅 오늘 날짜 하위 페이지 찾는 중: '{title}' (KST 9시 기준)")
        result = run_once()
        if result is None:
            print("❌ 업로드 실패 또는 내용 없음")
