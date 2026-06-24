# AGENTS: Document.AI Trend Report Bot

## 목적
주간/수동 실행을 통해 `source/` 내 레포지토리 README를 요약하고,
변동 비교 후 Google Chat으로 가독성 높은 최종 리포트를 보내는 파이프라인의
신뢰성(구조 일관성, 비교 정확성, 재현 가능성)을 유지한다.

## 핵심 실행 흐름
1. `run_research.sh` → 환경 변수 로드 및 가상환경 활성화
2. `main.py` 실행
3. `source/` 하위 git 레포지토리 README 조회/요약
4. `reports/.latest_snapshot.json` 또는 이전 `reports/*.md`와 비교
5. 최종 JSON 구조화 후 보고서 텍스트 생성
6. `Google Chat` 웹훅 전송 및 `reports/YYYY-MM-DD.md` 저장

## 고정 정책
- 제목은 기본 `Document.AI 주요 모델 트렌드 Report 📩`.
- 제목에 날짜를 넣지 않는다. 날짜는 본문 `작성일: YYYY-MM-DD`에만 표시.
- `REPO_ORDER`/`--repo-order`는 **순서 지정 + 필터링 대상 제한**으로 동작.
  - 값이 비어있지 않으면 해당 목록의 레포만 처리.
- 모델별 브리핑은 2개 섹션만 출력한다.
  1) 전체 핵심 요약
  2) 모델 별 상세 브리핑

## 최종 리포트 템플릿(텍스트)
- 첫 줄: `Document.AI 주요 모델 트렌드 Report 📩`
- 다음 줄: `작성일: YYYY-MM-DD`
- `1) 전체 핵심 요약 🔎`
  - 핵심 요약 항목을 줄 단위로 나열
- `2) 모델 별 상세 브리핑 📚`
  - 각 레포는 아래 형식으로 헤더 1줄 + 최대 3개 bullet + 빈 줄 분리:
    - `레포명 ⚠️ 변동 있음` 또는 `레포명 ✅ 변동 없음`
    - `* 핵심 변화/영향 문장`

예시:

```
PaddleOCR ⚠️ 변동 있음
* PP-OCRv6 출시로 ...
* 주요 변화점 2
* 주요 변화점 3

OmniDocBench ✅ 변동 없음
* 기존 비교지표 변동 없음
```

## 최종 오케스트레이터 시스템 프롬프트 규칙
`main.py`의 시스템 프롬프트(최종 구조화 단계)는 다음을 강제한다.
- JSON 형태만 반환: `{"title": "...", "overall_summary": [...], "model_briefings": [...]}`
- markdown 제약: 텍스트/줄바꿈 중심
- model_briefings는 위 형식의 문자열 항목을 반환하며 레포당 bullet은 최대 3개
- 별도 코드블록/해설 없이 순수 JSON

## 스케줄 관련 운영 규칙
- 실행 켜기/중단/단일실행은
  - `bash manage_weekly_schedule.sh 실행`
  - `bash manage_weekly_schedule.sh 중단`
  - `bash manage_weekly_schedule.sh 단일실행`
- 중단 스크립트 텍스트는 `disable_weekly_schedule.txt` 기준으로 cron/launchd 해제 절차를 따름.
- `install_weekly_schedule.sh`는 매주 월 09:00 등록용(launchd 우선, fallback cron).

## 변경 시 검증
- `python3 -m py_compile main.py`
- 단일 실행 로그에서 출력되는 `repo blocks`에 REPO_ORDER만 반영되는지 확인
- `reports/YYYY-MM-DD.md`의 헤더와 섹션이 2개로 유지되는지 확인
