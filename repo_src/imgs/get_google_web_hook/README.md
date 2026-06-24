# Google Chat 웹훅 URL 발급 가이드

이 문서는 Google Workspace 관리 화면에서 Google Chat incoming webhook URL을 발급하고,
봇에 적용하는 과정을 정리한 가이드입니다.

## 1) 대상 공간(스페이스) 열기

1. Google Chat을 실행하고 보고 메시지를 받을 **채팅 공간**을 엽니다.
2. 공간 우측 상단의 `⋮` 또는 설정 메뉴를 엽니다.
3. `Apps & integrations` 메뉴로 이동해 웹훅 관리 화면으로 들어갑니다.

![step1](/repo_src/imgs/get_google_web_hook/page1_1.png)
![step1-b](/repo_src/imgs/get_google_web_hook/page2_1.png)

## 2) Webhook 추가

1. `Add webhook` 버튼을 클릭합니다.
2. 웹훅 이름(`Display name`)을 입력합니다.
3. 사용자를 식별할 이름 또는 설명을 입력(선택)
4. 저장을 완료합니다.

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

## 4) 메시지 수신 확인

1. `.env`를 설정한 뒤, `bash run_research.sh`를 1회 실행해봅니다.
2. 스페이스에서 메시지가 도착하면 웹훅이 정상 동작한 것입니다.
3. 도착하지 않으면 웹훅 권한/통합 허용 범위/스페이스 멤버십을 확인합니다.

![step4](/repo_src/imgs/get_google_web_hook/page5_1.png)
