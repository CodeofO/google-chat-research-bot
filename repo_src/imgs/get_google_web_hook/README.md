# Google Chat 웹훅 URL 발급 가이드

이 문서는 Google Workspace 관리 화면에서 Google Chat incoming webhook URL을 발급하고,
봇에 적용하는 과정을 정리한 가이드입니다.

## 1) 대상 공간(스페이스) 열기

1. Google Chat을 실행하고 보고 메시지를 받을 **채팅 공간**을 엽니다.
2. 공간 우측 상단의 `⋮` 또는 설정 메뉴를 엽니다.
3. `Apps & integrations`(또는 `앱 및 통합`) 메뉴로 이동합니다.

![step1](/repo_src/imgs/get_google_web_hook/page1_1.png)
![step1-b](/repo_src/imgs/get_google_web_hook/page2_1.png)

## 2) Webhook 추가

1. `Add webhook` 버튼 클릭
2. 웹훅 이름(`Display name`)을 지정
3. 사용자를 식별할 이름 또는 설명을 입력(선택)
4. `Save`를 눌러 저장

![step2](/repo_src/imgs/get_google_web_hook/page3_1.png)

## 3) Webhook URL 복사

1. 생성된 항목에서 **Webhook URL**을 확인
2. `Copy` 버튼으로 전체 URL을 복사
3. 복사한 URL을 `.env`의 `GOOGLE_CHAT_WEBHOOK_URL` 값에 넣습니다.

예시
```bash
GOOGLE_CHAT_WEBHOOK_URL=https://chat.googleapis.com/v1/spaces/AAAA....
```

![step3](/repo_src/imgs/get_google_web_hook/page4_1.png)

## 4) 수신 테스트 및 권한 점검

1. 채팅방에 테스트 메시지를 직접 전송해봅니다.
2. 메시지가 오지 않으면 메시지 권한, 앱 통합 허용 범위, 스페이스 멤버십을 다시 확인합니다.

![step4](/repo_src/imgs/get_google_web_hook/page5_1.png)

## 5) 메시지 포맷 확인

기본 텍스트 테스트와 실제 리포트 포맷으로 수신 유효성을 함께 확인합니다.

```bash
curl -X POST "$GOOGLE_CHAT_WEBHOOK_URL" \
  -H 'Content-Type: application/json; charset=UTF-8' \
  -d '{"text": "[테스트] Google Chat webhook 연결 확인"}'
```

수신 확인이 끝나면 바로 `bash run_research.sh` 단일 실행으로 최종 리포트 전송 결과를 확인합니다.

## 6) 주의사항

- Webhook URL은 민감정보이므로 `.env`에만 저장하고 커밋하지 않습니다.
- 채팅 공간 권한이 바뀌면 URL이 무효가 될 수 있습니다.
- Workspace 관리 정책에 따라 수신 메시지 정책이 제한될 수 있습니다.
