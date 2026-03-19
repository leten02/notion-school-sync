"""
1000.school 일간 스니펫 → 노션 하위 페이지 동기화

사용법:
  python sync_to_notion.py

- NOTION_PAGE_ID 하위에 날짜 형식(예: 2026-03-18) 제목의 페이지를 자동 생성
- 이미 해당 날짜 페이지가 있으면 스킵
"""

import os
import json
import requests
from dotenv import load_dotenv
from notion_client import Client

load_dotenv()

NOTION_TOKEN   = os.getenv("NOTION_TOKEN")
NOTION_PAGE_ID = os.getenv("NOTION_PAGE_ID")
SCHOOL_API_KEY = os.getenv("SCHOOL_API_KEY")

API_BASE = "https://api.1000.school"

notion = Client(auth=NOTION_TOKEN)


# ─── 1000.school 스니펫 전체 가져오기 ────────────────────────────────────────

def fetch_all_snippets() -> list:
    headers = {"Authorization": f"Bearer {SCHOOL_API_KEY}"}
    r = requests.get(f"{API_BASE}/daily-snippets", headers=headers)
    r.raise_for_status()
    return r.json().get("items", [])


# ─── 노션 기존 하위 페이지 날짜 목록 ─────────────────────────────────────────

def get_existing_page_titles(parent_id: str) -> dict:
    """이미 있는 하위 페이지의 {제목: 페이지id} 딕셔너리 반환"""
    existing = {}
    cursor = None
    while True:
        resp = notion.blocks.children.list(
            block_id=parent_id,
            start_cursor=cursor,
            page_size=100,
        )
        for block in resp["results"]:
            if block.get("type") == "child_page":
                title = block["child_page"]["title"]
                existing[title] = block["id"]
        if not resp.get("has_more"):
            break
        cursor = resp["next_cursor"]
    return existing


# ─── 마크다운 → 노션 블록 변환 ───────────────────────────────────────────────

def md_to_notion_blocks(text: str) -> list:
    blocks = []
    lines = text.split("\n")
    i = 0
    while i < len(lines):
        line = lines[i]

        # 코드 블록
        if line.startswith("```"):
            lang = line[3:].strip()
            code_lines = []
            i += 1
            while i < len(lines) and not lines[i].startswith("```"):
                code_lines.append(lines[i])
                i += 1
            blocks.append({
                "object": "block",
                "type": "code",
                "code": {
                    "rich_text": [{"type": "text", "text": {"content": "\n".join(code_lines)}}],
                    "language": lang or "plain text",
                },
            })

        elif line.startswith("#### "):
            blocks.append(_heading(3, line[5:]))
        elif line.startswith("### "):
            blocks.append(_heading(3, line[4:]))
        elif line.startswith("## "):
            blocks.append(_heading(2, line[3:]))
        elif line.startswith("# "):
            blocks.append(_heading(1, line[2:]))

        elif line.startswith("- ") or line.startswith("* "):
            blocks.append(_bullet(line[2:]))

        elif line.startswith("> "):
            blocks.append({
                "object": "block",
                "type": "quote",
                "quote": {"rich_text": [{"type": "text", "text": {"content": line[2:]}}]},
            })

        elif line.strip() == "---":
            blocks.append({"object": "block", "type": "divider", "divider": {}})

        elif line.strip():
            # 번호 목록 (1. 2. ...)
            if len(line) > 2 and line[0].isdigit() and line[1] in ".)" and line[2] == " ":
                blocks.append({
                    "object": "block",
                    "type": "numbered_list_item",
                    "numbered_list_item": {
                        "rich_text": [{"type": "text", "text": {"content": line[3:]}}]
                    },
                })
            else:
                blocks.append(_paragraph(line.strip()))
        else:
            # 빈 줄 → 빈 단락
            if blocks and blocks[-1]["type"] != "divider":
                pass  # 연속 빈 줄은 무시

        i += 1

    return blocks


def _heading(level: int, text: str) -> dict:
    t = f"heading_{level}"
    return {"object": "block", "type": t, t: {
        "rich_text": [{"type": "text", "text": {"content": text}}]
    }}


def _bullet(text: str) -> dict:
    return {"object": "block", "type": "bulleted_list_item", "bulleted_list_item": {
        "rich_text": [{"type": "text", "text": {"content": text}}]
    }}


def _paragraph(text: str) -> dict:
    return {"object": "block", "type": "paragraph", "paragraph": {
        "rich_text": [{"type": "text", "text": {"content": text}}]
    }}


# ─── 노션 페이지 기존 블록 전체 삭제 ─────────────────────────────────────────

def clear_page_blocks(page_id: str):
    """페이지 안의 블록을 전부 삭제"""
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


def update_notion_page(page_id: str, content: str, feedback_json: str | None):
    """기존 페이지 내용을 최신 스니펫으로 덮어쓰기"""
    clear_page_blocks(page_id)
    blocks = _build_blocks(content, feedback_json)

    for i in range(0, len(blocks), 100):
        notion.blocks.children.append(block_id=page_id, children=blocks[i:i + 100])


# ─── 노션 페이지 생성 ─────────────────────────────────────────────────────────

def _build_blocks(content: str, feedback_json: str | None) -> list:
    """마크다운 + 피드백 → 노션 블록 리스트"""
    blocks = md_to_notion_blocks(content)

    if feedback_json:
        try:
            fb = json.loads(feedback_json) if isinstance(feedback_json, str) else feedback_json
            blocks.append({"object": "block", "type": "divider", "divider": {}})
            blocks.append(_heading(2, "📊 피드백"))
            total = fb.get("total_score")
            if total:
                blocks.append(_paragraph(f"총점: {total}점"))
            key_learning = fb.get("key_learning")
            if key_learning:
                blocks.append(_paragraph(f"핵심 학습: {key_learning}"))
            mentor = fb.get("mentor_comment")
            if mentor:
                blocks.append({
                    "object": "block", "type": "quote",
                    "quote": {"rich_text": [{"type": "text", "text": {"content": mentor}}]},
                })
            next_action = fb.get("next_action")
            if next_action:
                blocks.append(_paragraph(f"다음 액션: {next_action}"))
        except Exception:
            pass

    return blocks


def create_notion_page(parent_id: str, title: str, content: str, feedback_json: str | None):
    blocks = _build_blocks(content, feedback_json)

    # 첫 100개로 페이지 생성
    page = notion.pages.create(
        parent={"page_id": parent_id},
        properties={
            "title": {"title": [{"type": "text", "text": {"content": title}}]}
        },
        children=blocks[:100],
    )

    # 나머지 블록 100개씩 이어붙이기
    for i in range(100, len(blocks), 100):
        notion.blocks.children.append(
            block_id=page["id"],
            children=blocks[i:i + 100],
        )


# ─── 메인 ─────────────────────────────────────────────────────────────────────

def main(update_existing: bool = True, only_date: str | None = None):
    """
    update_existing=True  → 기존 페이지도 최신 내용으로 덮어씀 (기본값)
    update_existing=False → 없는 날짜만 새로 생성
    only_date="2026-03-18" → 해당 날짜 스니펫만 처리 (전날 동기화용)
    """
    print("📥 1000.school 스니펫 가져오는 중...")
    snippets = fetch_all_snippets()

    # 특정 날짜만 처리
    if only_date:
        snippets = [s for s in snippets if s["date"] == only_date]
        if not snippets:
            print(f"   ⚠️  {only_date} 스니펫 없음 (아직 작성 전일 수 있음)")
            return
        print(f"   📅 {only_date} 스니펫 1개 처리\n")
    else:
        print(f"   총 {len(snippets)}개 스니펫 발견\n")

    print("📄 노션 기존 페이지 확인 중...")
    existing = get_existing_page_titles(NOTION_PAGE_ID)

    created = 0
    updated = 0
    skipped = 0

    for snippet in sorted(snippets, key=lambda x: x["date"]):
        title    = snippet["date"]
        content  = snippet.get("content", "")
        feedback = snippet.get("feedback")

        if title in existing:
            if update_existing:
                print(f"🔄 {title} → 최신 내용으로 업데이트 중...")
                try:
                    update_notion_page(existing[title], content, feedback)
                    print(f"✅ {title} 업데이트 완료")
                    updated += 1
                except Exception as e:
                    print(f"❌ {title} 업데이트 실패: {e}")
            else:
                print(f"⏭  {title} → 이미 존재, 스킵")
                skipped += 1
            continue

        print(f"✏️  {title} → 노션 페이지 생성 중...")
        try:
            create_notion_page(NOTION_PAGE_ID, title, content, feedback)
            print(f"✅ {title} 생성 완료")
            created += 1
        except Exception as e:
            print(f"❌ {title} 생성 실패: {e}")

    print(f"\n🎉 완료! 생성: {created}개, 업데이트: {updated}개, 스킵: {skipped}개")


if __name__ == "__main__":
    main()
