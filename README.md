# SK hynix Live 1Y Stock Dashboard

페이지 접속 시 KRX Data Marketplace의 SK하이닉스(000660) 개별종목 시세를 현재일 기준 최근 365일로 자동 조회합니다.

## 실행
```bash
pip install -r requirements.txt
python app.py
```
브라우저에서 http://localhost:8000

## 공개 배포
Render / Railway / Fly.io / PythonAnywhere 같은 Python 웹앱 호스팅에 이 폴더를 배포하고 시작 명령을 `gunicorn app:app`으로 설정하세요.

## 동작
- KRX 최근 1년 일별 시세 자동조회
- 일/주/월 탭 및 X/Y축 슬라이더
- 누적수익률, 연환산 변동성, MDD, RSI(14), SMA20/SMA60, 로그회귀 R², 상승기간 비율
- 규칙 기반 분할매수 추천 / 관망 / 구매 비추천
- 접속 시점 기준 Google News RSS 관련 기사 자동 수집 및 선택 기간 연동

KRX의 점검·봇 차단·엔드포인트 정책 변경 시 서버 조회 로직을 수정해야 할 수 있습니다.
