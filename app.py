from flask import Flask, jsonify, render_template, request
from datetime import date, timedelta, datetime, timezone
from urllib.parse import quote
import calendar
import math
import os
import re
import requests
import xml.etree.ElementTree as ET

app = Flask(__name__)

KRX_URL = "https://data.krx.co.kr/comm/bldAttendant/getJsonData.cmd"
REFERER = "https://data.krx.co.kr/contents/MDC/MDI/mdiLoader/index.cmd?menuId=MDC0201010105"
ISU_CD = "KR7000660001"
YAHOO_SYMBOL = "000660.KS"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124 Safari/537.36"


def num(v):
    if v is None:
        return None
    s = str(v).strip().replace(",", "")
    if s in ("", "-", "null", "None"):
        return None
    try:
        return float(s)
    except Exception:
        return None


def date_window():
    end = date.today()
    start = end - timedelta(days=365)
    return start, end


def fetch_krx():
    """Primary source. KRX can reject server/cloud requests depending on session/policy."""
    start, end = date_window()
    payload = {
        "bld": "dbms/MDC/STAT/standard/MDCSTAT01701",
        "locale": "ko_KR",
        "isuCd": ISU_CD,
        "isuCd2": ISU_CD,
        "strtDd": start.strftime("%Y%m%d"),
        "endDd": end.strftime("%Y%m%d"),
        "adjStkPrc_check": "Y",
        "adjStkPrc": "2",
        "share": "1",
        "money": "1",
        "csvxls_isNo": "false",
    }
    headers = {
        "Referer": REFERER,
        "Origin": "https://data.krx.co.kr",
        "User-Agent": UA,
        "Accept": "application/json,text/plain,*/*",
        "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8",
        "X-Requested-With": "XMLHttpRequest",
    }
    with requests.Session() as session:
        # Establish cookies first; harmless when KRX does not require them.
        try:
            session.get(REFERER, headers={"User-Agent": UA}, timeout=10)
        except Exception:
            pass
        r = session.post(KRX_URL, data=payload, headers=headers, timeout=20)
        r.raise_for_status()
        try:
            obj = r.json()
        except Exception as e:
            raise RuntimeError(f"KRX non-JSON response ({r.status_code}): {r.text[:120]}") from e

    raw = obj.get("output", [])
    rows = []
    for x in raw:
        d = (x.get("TRD_DD") or "").replace("/", "-")
        c = num(x.get("TDD_CLSPRC"))
        if not d or c is None:
            continue
        rows.append({
            "date": d,
            "close": c,
            "change": num(x.get("CMPPREVDD_PRC")),
            "pct": num(x.get("FLUC_RT")),
            "open": num(x.get("TDD_OPNPRC")),
            "high": num(x.get("TDD_HGPRC")),
            "low": num(x.get("TDD_LWPRC")),
            "volume": num(x.get("ACC_TRDVOL")),
            "value": num(x.get("ACC_TRDVAL")),
        })
    rows.sort(key=lambda z: z["date"])
    if len(rows) < 20:
        raise RuntimeError(f"KRX returned too few rows: {len(rows)}")
    return rows, start.isoformat(), end.isoformat(), "KRX Data Marketplace"


def fetch_yahoo():
    """Free no-key fallback for Render/cloud environments where KRX blocks the request."""
    start, end = date_window()
    # Add one day because period2 is exclusive.
    period1 = int(datetime.combine(start, datetime.min.time(), tzinfo=timezone.utc).timestamp())
    period2 = int(datetime.combine(end + timedelta(days=1), datetime.min.time(), tzinfo=timezone.utc).timestamp())
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{YAHOO_SYMBOL}"
    params = {
        "period1": period1,
        "period2": period2,
        "interval": "1d",
        "events": "history",
        "includeAdjustedClose": "true",
    }
    r = requests.get(url, params=params, headers={"User-Agent": UA, "Accept": "application/json"}, timeout=20)
    r.raise_for_status()
    obj = r.json()
    err = obj.get("chart", {}).get("error")
    if err:
        raise RuntimeError(f"Yahoo Finance error: {err}")
    result = (obj.get("chart", {}).get("result") or [None])[0]
    if not result:
        raise RuntimeError("Yahoo Finance returned no result")

    ts = result.get("timestamp") or []
    quote_data = ((result.get("indicators") or {}).get("quote") or [{}])[0]
    adj_data = ((result.get("indicators") or {}).get("adjclose") or [{}])[0].get("adjclose") or []
    closes = quote_data.get("close") or []
    opens = quote_data.get("open") or []
    highs = quote_data.get("high") or []
    lows = quote_data.get("low") or []
    vols = quote_data.get("volume") or []

    rows = []
    prev = None
    for i, t in enumerate(ts):
        raw_close = num(closes[i]) if i < len(closes) else None
        # Use adjusted close for return continuity when available, otherwise raw close.
        c = num(adj_data[i]) if i < len(adj_data) else raw_close
        if c is None:
            continue
        d = datetime.fromtimestamp(t, tz=timezone.utc).date().isoformat()
        if d < start.isoformat() or d > end.isoformat():
            continue
        change = None if prev is None else c - prev
        pct = None if prev in (None, 0) else (c / prev - 1.0) * 100.0
        rows.append({
            "date": d,
            "close": c,
            "change": change,
            "pct": pct,
            "open": num(opens[i]) if i < len(opens) else None,
            "high": num(highs[i]) if i < len(highs) else None,
            "low": num(lows[i]) if i < len(lows) else None,
            "volume": num(vols[i]) if i < len(vols) else None,
            "value": None,
        })
        prev = c

    rows.sort(key=lambda z: z["date"])
    if len(rows) < 20:
        raise RuntimeError(f"Yahoo Finance returned too few rows: {len(rows)}")
    return rows, start.isoformat(), end.isoformat(), "Yahoo Finance (KRX fallback)"


def fetch_market_data():
    errors = []
    for name, func in (("KRX", fetch_krx), ("Yahoo Finance", fetch_yahoo)):
        try:
            rows, start, end, source = func()
            return rows, start, end, source, errors
        except Exception as e:
            errors.append(f"{name}: {type(e).__name__}: {e}")
    raise RuntimeError(" | ".join(errors))


def fetch_news():
    end = date.today()
    start = end - timedelta(days=365)
    q = f'SK하이닉스 OR "SK hynix" after:{start.isoformat()} before:{(end + timedelta(days=1)).isoformat()}'
    url = "https://news.google.com/rss/search?q=" + quote(q) + "&hl=ko&gl=KR&ceid=KR:ko"
    r = requests.get(url, headers={"User-Agent": UA}, timeout=12)
    r.raise_for_status()
    root = ET.fromstring(r.text)
    out = []
    for item in root.findall(".//item")[:80]:
        title = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        pub = (item.findtext("pubDate") or "").strip()
        src = item.find("source")
        source = src.text.strip() if src is not None and src.text else "Google News"
        try:
            d = datetime.strptime(pub, "%a, %d %b %Y %H:%M:%S %Z").date().isoformat()
        except Exception:
            continue
        low = title.lower()
        tag = "주요 이슈" if any(k in low for k in [
            "실적", "hbm", "ai", "엔비디아", "nvidia", "중국", "규제", "관세",
            "adr", "증자", "배당", "자사주", "메모리", "반도체", "투자", "공급", "수요", "급락", "급등"
        ]) else "관련 뉴스"
        out.append({
            "date": d,
            "impact": "mixed",
            "tag": tag,
            "title": title,
            "summary": "접속 시점 기준 자동 수집된 SK하이닉스 관련 뉴스입니다.",
            "url": link,
            "source": source,
        })
    out.sort(key=lambda x: x["date"])
    return out


def pct_change(first, last):
    return None if not first or first == 0 else (last / first - 1.0) * 100.0


def nearest_row(rows, target):
    """Return the latest trading row on or before target (or first row if older)."""
    eligible = [x for x in rows if x["date"] <= target.isoformat()]
    return eligible[-1] if eligible else rows[0]


def subtract_months(d, months):
    month_index = d.year * 12 + d.month - 1 - months
    year, month0 = divmod(month_index, 12)
    month = month0 + 1
    return date(year, month, min(d.day, calendar.monthrange(year, month)[1]))


def calc_rsi(rows, periods=14):
    if len(rows) < periods + 1:
        return None
    changes = [rows[i]["close"] / rows[i - 1]["close"] - 1 for i in range(1, len(rows))][-periods:]
    gain = sum(max(x, 0) for x in changes) / periods
    loss = sum(max(-x, 0) for x in changes) / periods
    return 100.0 if loss == 0 else 100.0 - 100.0 / (1.0 + gain / loss)


def answer_question(question, rows, news):
    """No-key Korean market-data agent. Answers only from fetched/derived evidence."""
    q = re.sub(r"\s+", " ", (question or "").strip())
    if not q:
        return {"answer": "질문을 입력해주세요.", "evidence": []}

    latest = rows[-1]
    today = date.today()
    target = None
    period_start = None

    # Explicit dates: 2026-08-31 / 2026.8.31 / 8월 31일
    m = re.search(r"(20\d{2})[.\-/년 ]+(\d{1,2})[.\-/월 ]+(\d{1,2})일?", q)
    if m:
        try:
            target = date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            target = None
    if target is None:
        m = re.search(r"(?<!\d)(\d{1,2})월\s*(\d{1,2})일", q)
        if m:
            try:
                target = date(today.year, int(m.group(1)), int(m.group(2)))
                if target > today:
                    target = target.replace(year=today.year - 1)
            except ValueError:
                target = None

    # Relative point/period: 2주 전, 최근 3개월, 한 달 수익률
    m = re.search(r"(\d+)\s*(거래일|주일|일|주|개월|달|년)\s*(전|간)?", q)
    amount = int(m.group(1)) if m else None
    unit = m.group(2) if m else None
    if "어제" in q:
        target = today - timedelta(days=1)
    elif target is None and amount is not None:
        if unit == "거래일":
            idx = max(0, len(rows) - 1 - amount)
            target = date.fromisoformat(rows[idx]["date"])
        elif unit == "일":
            target = today - timedelta(days=amount)
        elif unit in ("주", "주일"):
            target = today - timedelta(weeks=amount)
        elif unit in ("개월", "달"):
            target = subtract_months(today, amount)
        elif unit == "년":
            target = subtract_months(today, amount * 12)
    elif target is None and re.search(r"한\s*(달|개월)", q):
        target = subtract_months(today, 1)

    if target is not None:
        period_start = nearest_row(rows, target)

    won = lambda v: f"{v:,.0f}원"
    evidence = [{"label": "최종 거래일", "value": latest["date"]}, {"label": "데이터 출처", "value": "KRX/Yahoo 실시간 조회"}]

    if any(k in q for k in ["뉴스", "이슈", "호재", "악재"]):
        items = sorted(news, key=lambda x: x["date"], reverse=True)[:3]
        if not items:
            return {"answer": "현재 불러온 외부 뉴스가 없습니다.", "evidence": evidence, "links": []}
        summary = "최근 관련 이슈는 " + " / ".join(f'{x["date"]} {x["title"]}' for x in items) + " 입니다."
        return {"answer": summary, "evidence": evidence, "links": [{"title": x["title"], "url": x["url"]} for x in items]}

    if "rsi" in q.lower() or "과매수" in q or "과매도" in q:
        value = calc_rsi(rows)
        state = "과매수 주의" if value >= 70 else "과매도 가능" if value <= 30 else "중립"
        return {"answer": f'최종 거래일 {latest["date"]} 기준 RSI(14)는 {value:.1f}로 {state} 구간입니다.', "evidence": evidence}

    if "모멘텀" in q:
        if len(rows) < 21:
            return {"answer": "20거래일 모멘텀을 계산할 데이터가 부족합니다.", "evidence": evidence}
        value = pct_change(rows[-21]["close"], latest["close"])
        return {"answer": f'최근 20거래일 모멘텀은 {value:+.1f}%입니다.', "evidence": evidence}

    if any(k in q for k in ["현재가", "지금 가격", "최근 종가", "오늘 종가"]):
        return {"answer": f'가장 최근 거래일인 {latest["date"]} 종가는 {won(latest["close"])}입니다.', "evidence": evidence}

    if period_start is not None and any(k in q for k in ["수익률", "등락률", "얼마나 올", "얼마나 내"]):
        value = pct_change(period_start["close"], latest["close"])
        return {"answer": f'{period_start["date"]} 종가 {won(period_start["close"])}에서 {latest["date"]} 종가 {won(latest["close"])}까지 수익률은 {value:+.2f}%입니다.', "evidence": evidence}

    if period_start is not None and any(k in q for k in ["최고", "최저", "평균", "변동성", "낙폭", "mdd"]):
        sample = [x for x in rows if x["date"] >= period_start["date"]]
        closes = [x["close"] for x in sample]
        if "최고" in q:
            x = max(sample, key=lambda z: z["close"])
            answer = f'{period_start["date"]} 이후 최고 종가는 {x["date"]}의 {won(x["close"])}입니다.'
        elif "최저" in q:
            x = min(sample, key=lambda z: z["close"])
            answer = f'{period_start["date"]} 이후 최저 종가는 {x["date"]}의 {won(x["close"])}입니다.'
        elif "평균" in q:
            answer = f'{period_start["date"]} 이후 평균 종가는 {won(sum(closes)/len(closes))}입니다.'
        elif "변동성" in q:
            rr = [closes[i] / closes[i-1] - 1 for i in range(1, len(closes))]
            mean_r = sum(rr) / len(rr)
            sd = math.sqrt(sum((x-mean_r)**2 for x in rr)/(len(rr)-1)) if len(rr) > 1 else 0
            answer = f'{period_start["date"]} 이후 연환산 변동성은 {sd*math.sqrt(252)*100:.1f}%입니다.'
        else:
            peak, worst = closes[0], 0
            for c in closes:
                peak = max(peak, c)
                worst = min(worst, c/peak-1)
            answer = f'{period_start["date"]} 이후 최대 낙폭(MDD)은 {worst*100:.1f}%입니다.'
        return {"answer": answer, "evidence": evidence}

    if target is not None or "종가" in q or "가격" in q:
        x = period_start or latest
        requested = target.isoformat() if target else latest["date"]
        note = ""
        if x["date"] != requested:
            note = f' (해당일이 휴장일이면 직전 거래일 적용; 요청일 {requested})'
        return {"answer": f'{x["date"]} 종가는 {won(x["close"])}입니다.{note}', "evidence": evidence}

    return {"answer": "현재는 종가·수익률·최고/최저/평균·변동성·MDD·RSI·20거래일 모멘텀·최근 뉴스를 질문할 수 있습니다. 예: ‘2주 전 종가는?’, ‘최근 3개월 최고가는?’", "evidence": evidence}


def infer_followup(question, history):
    """Carry the previous metric into short follow-ups such as '그럼 한 달 전은?'"""
    q = question.strip()
    metric_words = ["종가", "수익률", "등락률", "최고가", "최저가", "평균", "변동성", "MDD", "RSI", "모멘텀", "뉴스", "이슈"]
    if any(word.lower() in q.lower() for word in metric_words):
        return q
    prior_users = [x.get("content", "") for x in history if x.get("role") == "user"]
    if not prior_users:
        return q
    previous = prior_users[-1]
    for word in metric_words:
        if word.lower() in previous.lower():
            return f"{q} {word}"
    return q


def groq_chat(question, history, verified):
    api_key = os.getenv("GROQ_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("GROQ_API_KEY is not configured")

    clean_history = []
    for item in history[-8:]:
        role = item.get("role")
        content = str(item.get("content", ""))[:1200]
        if role in ("user", "assistant") and content:
            clean_history.append({"role": role, "content": content})

    system = """당신은 SK하이닉스 주가 분석 웹사이트의 친절한 한국어 AI Agent다.
인사와 일상적인 대화에는 자연스럽고 짧게 응답한다. 주가 질문은 서버가 제공한 '검증된 계산 결과'를 최우선 사실로 사용한다.
검증 결과에 없는 가격·날짜·뉴스·수치를 추측하거나 만들어내지 않는다. 부족하면 어떤 질문을 할 수 있는지 자연스럽게 안내한다.
후속 질문은 대화 기록을 이어서 이해한다. 투자 추천은 단정하지 말고 데이터 기반 참고 의견과 위험을 함께 말한다.
답변은 보통 2~5문장으로 간결하게 하고, 마크다운 표는 꼭 필요할 때만 쓴다."""
    context = (
        "사용자 질문: " + question + "\n"
        "서버의 검증된 계산 결과: " + str(verified.get("answer", "")) + "\n"
        "근거: " + ", ".join(f'{x.get("label")}: {x.get("value")}' for x in verified.get("evidence", []))
    )
    messages = [{"role": "system", "content": system}, *clean_history, {"role": "user", "content": context}]
    r = requests.post(
        "https://api.groq.com/openai/v1/chat/completions",
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json={
            "model": os.getenv("GROQ_MODEL", "openai/gpt-oss-20b"),
            "messages": messages,
            "temperature": 0.45,
            "max_completion_tokens": 450,
        },
        timeout=25,
    )
    r.raise_for_status()
    data = r.json()
    return data["choices"][0]["message"]["content"].strip()


@app.get("/")
def home():
    return render_template("index.html")


@app.get("/api/data")
def data():
    try:
        rows, start, end, source, source_errors = fetch_market_data()
    except Exception as e:
        # Return JSON instead of a generic Render 500 page, making failures diagnosable.
        return jsonify({
            "ok": False,
            "error": str(e),
            "ticker": "000660",
            "name": "SK하이닉스",
        }), 503

    try:
        news = fetch_news()
        news_error = None
    except Exception as e:
        news = []
        news_error = f"{type(e).__name__}: {e}"

    return jsonify({
        "ok": True,
        "ticker": "000660",
        "name": "SK하이닉스",
        "source": source,
        "source_fallback": source != "KRX Data Marketplace",
        "source_errors": source_errors,
        "requested_start": start,
        "requested_end": end,
        "last_trade_date": rows[-1]["date"],
        "updated_at": datetime.now().isoformat(timespec="seconds"),
        "rows": rows,
        "news": news,
        "news_error": news_error,
    })


@app.get("/api/health")
def health():
    return jsonify({"ok": True, "date": date.today().isoformat()})


@app.post("/api/ask")
def ask():
    payload = request.get_json(silent=True) or {}
    question = str(payload.get("question", ""))[:300]
    history = payload.get("history", [])
    if not isinstance(history, list):
        history = []
    try:
        rows, _, _, source, _ = fetch_market_data()
        try:
            news = fetch_news()
        except Exception:
            news = []
        standalone = infer_followup(question, history)
        result = answer_question(standalone, rows, news)
        try:
            result["answer"] = groq_chat(question, history, result)
            agent_mode = "Groq · openai/gpt-oss-20b"
        except Exception as llm_error:
            agent_mode = "계산형 fallback"
            result["llm_notice"] = f"무료 대화 모델을 사용할 수 없어 계산형 답변으로 전환했습니다: {type(llm_error).__name__}"
        result.update({"ok": True, "source": source, "agent_mode": agent_mode})
        return jsonify(result)
    except Exception as e:
        return jsonify({"ok": False, "error": str(e), "answer": "최신 데이터를 불러오지 못해 답변할 수 없습니다."}), 503


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000)
