#!/bin/bash
# ──────────────────────────────────────────────
# notion-school-sync 초기 세팅 스크립트
# 클론 후 한 번만 실행하면 됩니다
# 사용법: bash setup.sh
# ──────────────────────────────────────────────

set -e  # 오류 발생 시 즉시 중단

echo ""
echo "🚀 notion-school-sync 세팅 시작"
echo "──────────────────────────────"

# 1. Python 버전 확인 (3.9 이상 필요 - zoneinfo 내장)
PYTHON_VERSION=$(python3 --version 2>&1 | awk '{print $2}')
REQUIRED="3.9"
if python3 -c "import sys; exit(0 if sys.version_info >= (3,9) else 1)" 2>/dev/null; then
    echo "✅ Python $PYTHON_VERSION 확인"
else
    echo "❌ Python 3.9 이상이 필요합니다. (현재: $PYTHON_VERSION)"
    exit 1
fi

# 2. 가상환경 생성
if [ ! -d "venv" ]; then
    echo "📦 가상환경 생성 중..."
    python3 -m venv venv
    echo "✅ 가상환경 생성 완료"
else
    echo "✅ 가상환경 이미 존재"
fi

# 3. 의존성 설치
echo "📥 패키지 설치 중..."
source venv/bin/activate
pip install --upgrade pip -q
pip install -r requirements.txt -q
echo "✅ 패키지 설치 완료"

# 4. .env 파일 세팅
if [ ! -f ".env" ]; then
    cp .env.example .env
    echo ""
    echo "⚠️  .env 파일이 생성되었습니다."
    echo "   아래 4가지 값을 직접 입력해주세요:"
    echo ""
    echo "   NOTION_TOKEN     → https://www.notion.so/my-integrations"
    echo "   NOTION_PAGE_ID   → 노션 부모 페이지 URL에서 추출"
    echo "   SCHOOL_API_KEY   → 1000.school 계정 설정"
    echo "   GEMINI_API_KEY   → https://aistudio.google.com/app/apikey"
    echo ""
    echo "   편집: nano .env  또는  open .env"
else
    echo "✅ .env 파일 이미 존재"
fi

# 5. DB 초기화
echo "🗄️  DB 초기화 중..."
python3 db.py
echo "✅ DB 초기화 완료"

echo ""
echo "──────────────────────────────"
echo "✅ 세팅 완료!"
echo ""
echo "📌 다음 단계:"
echo "   1. .env 파일에 API 키 4개 입력"
echo "   2. 노션 부모 페이지에 Integration 연결"
echo "   3. 실행: source venv/bin/activate && python main.py --watch"
echo ""
