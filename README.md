# Document.AI 주요 모델 트렌드 Report

[Google Chat 웹훅 발급 가이드](repo_src/imgs/get_google_web_hook/README.md)

이 프로젝트는 `main.py`를 중심으로 로컬 README 요약 → 변동 비교 → 최종 리포트 작성 → Google Chat 전송까지
자동화합니다.

## 1) 준비

```bash
cd "$(git rev-parse --show-toplevel)"
source .venv-pj-trend/bin/activate
pip install -r requirements.txt
```

### .env 설정 예시

```bash
GOOGLE_CHAT_WEBHOOK_URL="https://chat.googleapis.com/v1/spaces/AAAA.../messages?key=...&token=..."
LOCAL_LLM_BASE_URL="http://<llm-host>:<port>/v1"
LOCAL_LLM_API_KEY="<api-key-or-placeholder>"
LOCAL_LLM_MODEL="<llm-model-name>"
REPO_ORDER="PaddleOCR,OmniDocBench"
```

실제 값은 운영 환경 값으로 치환하고, `.env`는 절대 커밋하지 않습니다.

`.env.example`를 기준으로 원하는 값만 채워도 됩니다.

`.env`가 있으면 자동 로드됩니다.

## 2) 단일 실행

`run_research.sh`는 현재 설정대로 1회 실행하고 보고서를 저장한 뒤 전송합니다.

```bash
bash run_research.sh
```

`--dry-run` 형태로 확인만 할 때는

```bash
cd "$(git rev-parse --show-toplevel)"
source .venv-pj-trend/bin/activate
python main.py --dry-run --output ./reports/dryrun.md
```

## 3) 스케줄 제어 (매주 월요일 09:00)

아래는 사용자 요청대로 “실행 / 중단 / 단일실행”만 기억하면 됩니다.

```bash
bash manage_weekly_schedule.sh 실행      # 실행
bash manage_weekly_schedule.sh 중단      # 중단
bash manage_weekly_schedule.sh 단일실행  # 단일실행
```

키워드도 지원됩니다(`start`, `stop`, `once`).

```bash
bash manage_weekly_schedule.sh 실행         # 실행
bash manage_weekly_schedule.sh 중단         # 중단
bash manage_weekly_schedule.sh 단일실행     # 단일실행
```

### 3-1) disable_weekly_schedule.txt 스크립트 반영

`disable_weekly_schedule.txt`의 핵심 동작은 아래와 같습니다.

```bash
# 현재 등록 확인
crontab -l | grep DOCAI_TREND_RESEARCH_BOT_WEEKLY

# cron 등록 해제
crontab -l | grep -v DOCAI_TREND_RESEARCH_BOT_WEEKLY | crontab -

# 해제 확인
crontab -l | grep DOCAI_TREND_RESEARCH_BOT_WEEKLY || echo "해제됨"

# launchd 환경인 경우
launchctl bootout gui/$(id -u) ~/Library/LaunchAgents/com.genai.docai.trendbot.plist
launchctl remove com.genai.docai.trendbot
rm -f ~/Library/LaunchAgents/com.genai.docai.trendbot.plist
```

`install_weekly_schedule.sh`로 재설치 가능합니다.

```bash
bash install_weekly_schedule.sh
```

## 4) 레포 순서 / 필터링

`REPO_ORDER`는 **출력 순서뿐 아니라 처리 대상 필터링**에도 사용됩니다. 즉, 값이 비어있지 않으면 그 목록에 포함된 레포만 처리합니다.

```bash
# .env 또는 쉘 변수
export REPO_ORDER="PaddleOCR,OmniDocBench"

# 또는 실행 시 지정
python main.py --repo-order "PaddleOCR,OmniDocBench"
```

이 설정이면 `HunyuanOCR, GLM-OCR, MinerU`는 생략되고 `PaddleOCR,OmniDocBench`만 집계됩니다.

- `--repo-order-file`도 동일하게 지원합니다.
- 알 수 없는 이름은 실행 로그에서 경고로 표시됩니다.

```bash
# 레포 순서 고정 예시
# (README 기본: .env 내 REPO_ORDER)
export REPO_ORDER="PaddleOCR,OmniDocBench"
python main.py --dry-run
```

## 5) 저장/비교 동작

- 실행할 때마다 `reports/YYYY-MM-DD.md`가 저장됩니다.
- 이전 주차(`reports/.latest_snapshot.json` 또는 최근 리포트) 기준으로 각 레포의 변동을 계산합니다.
- 제목은 기본 `Document.AI 주요 모델 트렌드 Report 📩` 입니다.
- 작성일은 항상 본문 `작성일: YYYY-MM-DD`로 고정되어 표시됩니다.
