"""
Notion 페이지 → 1000.school 일간 스니펫 자동 작성

사용법:
  python main.py          # 한 번만 실행
  python main.py --watch  # 30초마다 자동 체크 (변경 시에만 업로드)

노션 구조:
  부모 페이지 (NOTION_PAGE_ID)
  └─ 오늘 날짜 하위 페이지 (예: "2026-03-18")  ← 여기 내용을 읽어서 올림
"""

import os
import sys
import time
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

NOTION_TOKEN   = os.getenv("NOTION_TOKEN")    # 노션 Integration 토큰
NOTION_PAGE_ID = os.getenv("NOTION_PAGE_ID")  # 부모 페이지 ID
SCHOOL_API_KEY = os.getenv("SCHOOL_API_KEY")  # 1000.school API 토큰

API_BASE = "https://api.1000.school"

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


def run_once() -> bool:
    """한 번 실행. 업로드 성공 시 True 반환"""
    today = effective_date()
    title = today.strftime("%Y-%m-%d")

    page_id = find_today_child_page(NOTION_PAGE_ID)
    if not page_id:
        print(f"❌ '{title}' 하위 페이지 없음. 노션에서 페이지를 만들어주세요.", flush=True)
        return False

    content = get_notion_content(page_id)
    if not content.strip():
        print("⏳ 페이지 내용이 비어있습니다.", flush=True)
        return False

    result = save_snippet(content)
    print(f"✅ 업로드 완료! 스니펫 ID: {result['id']} | {result['date']}", flush=True)
    print(f"   내용: {content[:60]}{'...' if len(content) > 60 else ''}", flush=True)
    return True


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


def watch(interval: int = 60):
    """interval초마다 노션 변경 감지 후 자동 업로드 + 리포트 갱신"""
    print(f"👀 Watch 모드 시작 (매 {interval//60}분마다 체크, Ctrl+C로 종료)", flush=True)
    print(f"   📅 날짜 기준: KST 오전 {DAY_START_HOUR}시\n", flush=True)

    last_edited           = None
    last_report_date      = None   # 마지막으로 리포트를 갱신한 날짜
    last_page_created     = None   # 마지막으로 페이지를 생성한 날짜
    last_sync_date        = None   # 오전 9시 전날 최종본 동기화한 날짜
    last_reverse_sync_at  = None   # 마지막 10분 주기 역방향 동기화 시각

    while True:
        try:
            now   = kst_now()
            today = effective_date()
            title = today.strftime("%Y-%m-%d")

            # ── 오전 9시 이후 하루 1번: 오늘 날짜 페이지 자동 생성 ──────────
            if now.hour >= DAY_START_HOUR and last_page_created != today:
                if not find_today_child_page(NOTION_PAGE_ID):
                    print(f"[{_now()}] 📄 오전 9시 지남 → '{title}' 노션 페이지 자동 생성 중...", flush=True)
                    new_id = create_today_notion_page(title)
                    if new_id:
                        print(f"[{_now()}] ✅ '{title}' 페이지 생성 완료", flush=True)
                last_page_created = today

            # ── 오전 9시 이후 하루 1번: 전날 스니펫 최종본 → 노션 반영 ────────
            if now.hour >= DAY_START_HOUR and last_sync_date != today:
                from datetime import timedelta
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
                except Exception as re:
                    print(f"[{_now()}] ⚠️  리포트 갱신 실패: {re}", flush=True)

            # ── 노션 변경 감지 후 스니펫 업로드 ─────────────────────────────
            page_id = find_today_child_page(NOTION_PAGE_ID)

            if not page_id:
                print(f"[{_now()}] ⏳ '{title}' 페이지 없음. 대기 중...", flush=True)
            else:
                edited = get_page_last_edited(page_id)
                if edited != last_edited:
                    print(f"[{_now()}] 🔄 변경 감지! 스니펫 업로드 중...", flush=True)
                    success = run_once()
                    if success:
                        last_edited = edited
                        # 업로드 성공 시에도 리포트 즉시 갱신
                        print(f"[{_now()}] 📊 AI 감독 리포트 갱신 중...", flush=True)
                        try:
                            report_module.run()
                            last_report_date = today
                        except Exception as re:
                            print(f"[{_now()}] ⚠️  리포트 갱신 실패: {re}", flush=True)
                else:
                    print(f"[{_now()}] ✓ 변경 없음", flush=True)

            # ── 10분마다: 1000.school → 오늘 노션 페이지 역방향 동기화 ─────
            # (변경 감지 이후에 실행해서 루프 방지)
            REVERSE_SYNC_INTERVAL = 600  # 10분 (초)
            if last_reverse_sync_at is None or \
               (datetime.now() - last_reverse_sync_at).total_seconds() >= REVERSE_SYNC_INTERVAL:
                try:
                    sync_module.main(update_existing=True, only_date=title)
                    last_reverse_sync_at = datetime.now()
                    print(f"[{_now()}] 🔁 1000.school → 노션 동기화 완료 ({title})", flush=True)
                    # 역방향 동기화 후 last_edited 갱신 → 다음 루프에서 루프 방지
                    page_id = find_today_child_page(NOTION_PAGE_ID)
                    if page_id:
                        last_edited = get_page_last_edited(page_id)
                except Exception as se:
                    print(f"[{_now()}] ⚠️  역방향 동기화 실패: {se}", flush=True)

        except Exception as e:
            print(f"[{_now()}] ⚠️  오류: {e}", flush=True)

        time.sleep(interval)


def _now() -> str:
    return kst_now().strftime("%H:%M:%S")


if __name__ == "__main__":
    if "--watch" in sys.argv:
        watch()
    elif "--report" in sys.argv:
        # 리포트만 단독 실행
        report_module.run()
    else:
        today = effective_date()
        title = today.strftime("%Y-%m-%d")
        print(f"📅 오늘 날짜 하위 페이지 찾는 중: '{title}' (KST 9시 기준)")
        run_once()
