# notion-school-sync

노션(Notion) 데일리 페이지 ↔ 1000.school 일간 스니펫 자동 동기화 + Gemini AI 감독 리포트

---

## 개요

매일 노션에 데일리 기록을 작성하면:

1. **자동 감지** → Gemini AI가 내용을 정형화해서 1000.school에 업로드
2. **역방향 동기화** → 1000.school에서 변경된 내용(피드백 등)을 노션에 반영
3. **AI 감독 리포트** → 전체 스니펫 흐름을 분석해 번아웃·성장·팀 건강도 등 7개 지표로 리포트 생성

---

## 전체 흐름

```
노션 데일리 페이지 작성
        │
        ▼ (10분마다 변경 감지)
Gemini AI 다듬기 (정형화)
        │
        ▼
1000.school 스니펫 업로드 (POST/PUT)
        │
        ├──► SQLite DB 저장 (구조화 데이터)
        │
        ▼ (역방향, 해시 비교)
1000.school → 노션 반영 (피드백·외부 수정 시만)
        │
        ▼ (매일 오전 9시)
Gemini AI 감독 리포트 갱신 → 노션 리포트 페이지
```

---

## 기능

### 1. Watch 모드 (`python main.py --watch`)

10분 간격으로 반복 실행되며 아래 작업을 수행합니다.

**변경 감지 & 업로드**
- 오늘 날짜 노션 페이지의 `last_edited_time`을 감지
- 변경이 있으면 Gemini AI가 내용을 정형화한 뒤 1000.school에 업로드
- 내용이 없으면 업로드 스킵

**역방향 동기화 (1000.school → 노션)**
- 10분마다 1000.school 스니펫 내용+피드백의 해시를 비교
- 해시가 같으면 스킵 (노션 깜빡임 방지)
- 해시가 다르면 (피드백 추가·1000.school 직접 수정 등) 노션 페이지에 반영

**매일 오전 9시 자동 실행**
- 오늘 날짜 노션 페이지 자동 생성 (템플릿 포함) — 실패 시 다음 루프에서 재시도
- 전날 스니펫 최종본을 노션에 반영
- AI 감독 리포트 갱신 (하루 1회)

### 2. Gemini AI 다듬기

노션의 짧은 메모를 아래 형식으로 정형화합니다.

```
## 오늘 한 일
- 작업1 (어느 정도까지 완료했는지 포함)
- 작업2 ...

## 수행 목적
- 왜 했는지

## 하이라이트
- 오늘 가장 의미 있었던 성과 (1~3개)

## 로우라이트
- 아쉬웠던 점 (1~3개)

## 내일의 우선순위
- 영역: 구체적 행동 (세부 목표)

## 오늘 내가 팀에 기여한 가치
- 팀에 미친 영향

## 오늘의 배움 또는 남길 말
- 오늘의 인사이트

## 헬스 체크 (10점)
- 6/10 (이유)
```

### 3. AI 감독 리포트 (`python main.py --report`)

전체 스니펫을 분석해 노션 리포트 페이지에 작성합니다.

**7개 지표 (0~100점)**

| 지표 | 설명 | 방향 |
|------|------|------|
| 🔥 번아웃 위험도 | 헬스 점수 저하·피로 패턴 | 높을수록 위험 |
| 👥 팀 건강도 | 팀 기여·갈등 조짐 | 높을수록 좋음 |
| 💪 성실도 | 작성 연속성·우선순위 달성률 | 높을수록 좋음 |
| 🔁 문제 재발성 | 동일 로우라이트 반복 여부 | 높을수록 재발 많음 |
| 🧠 성장 지수 | 새로운 학습·피드백 이행 | 높을수록 좋음 |
| ⚡ 실행 집중도 | 목표-행동 일치도 | 높을수록 좋음 |
| 💬 감정 에너지 | 글의 온도·자기효능감 | 높을수록 긍정 |

토큰 절약을 위해 원문을 DB에서 요약본으로 압축한 뒤 Gemini에 전송합니다.

### 4. 노션 템플릿 (`python main.py --template`)

오늘 날짜 페이지에 데일리 템플릿을 추가합니다.

```
## What
## Why
## Team Value Added
## Highlight
## Lowlight
## Tomorrow
```

### 5. 단일 실행 (`python main.py`)

현재 노션 페이지 내용을 한 번만 Gemini로 다듬어 업로드합니다.

---

## 파일 구조

```
notion-school-sync/
├── main.py            # 메인 실행 파일 (watch 모드, Gemini polish, 업로드)
├── sync_to_notion.py  # 1000.school → 노션 역방향 동기화
├── report.py          # AI 감독 리포트 생성 (Gemini 분석 → 노션)
├── db.py              # SQLite DB 관리 (snippets + analysis 테이블)
├── requirements.txt
└── .env               # 환경 변수 (gitignore)
```

---

## 설치 및 설정

### 1. 의존성 설치

```bash
pip install -r requirements.txt
```

### 2. 환경 변수 설정

`.env` 파일을 생성하고 아래 값을 입력합니다.

```env
NOTION_TOKEN=secret_xxxx           # 노션 Integration 토큰
NOTION_PAGE_ID=xxxx                # 데일리 페이지들이 들어갈 부모 페이지 ID
SCHOOL_API_KEY=I_wrdo_xxxx         # 1000.school API 키
GEMINI_API_KEY=AIzaSy_xxxx         # Gemini API 키
```

**노션 Integration 설정**
1. https://www.notion.so/my-integrations 에서 Integration 생성
2. 부모 페이지에서 **연결** → Integration 추가

**Gemini API 키**
- https://aistudio.google.com/app/apikey 에서 발급

### 3. DB 초기화

```bash
python db.py
```

---

## 실행

```bash
# Watch 모드 (백그라운드 권장)
python main.py --watch

# 백그라운드 실행
nohup python main.py --watch > watch.log 2>&1 &

# 감독 리포트 수동 갱신
python main.py --report

# 오늘 템플릿 추가
python main.py --template

# 과거 스니펫 전체 노션 동기화
python sync_to_notion.py
```

---

## 날짜 기준

**KST 오전 9시** 기준으로 날짜가 전환됩니다.
- 오전 8:59 → 어제 날짜 페이지에 기록
- 오전 9:00 → 오늘 날짜 페이지 자동 생성

---

## 역방향 동기화 로직

단순히 덮어쓰면 노션에서 작성 중인 내용이 깜빡이며 사라지는 문제가 발생합니다.
이를 방지하기 위해 **업로드된 실제 내용 기반 해시 비교 방식**을 사용합니다.

```
노션 원문 → Gemini 다듬기 → 다듬어진 내용 → 1000.school 업로드
                                   │
                            hash(다듬어진 내용) 저장  ← 핵심: 원문이 아닌 실제 업로드본 기준

10분 후 역방향 동기화 체크
    → 1000.school 현재 내용+피드백 해시 계산
    → 저장된 해시와 비교
    → 같으면 스킵 (노션 덮어쓰기 없음)
    → 다르면 노션 업데이트 (1000.school 직접 수정·피드백 추가 반영)
```

> **주의**: 해시는 노션 원문이 아닌 **Gemini가 다듬어 업로드한 내용** 기준으로 저장합니다.
> 원문 기준으로 저장하면 다듬어진 내용과 해시가 달라져 10분마다 노션을 덮어쓰는 루프가 발생합니다.
