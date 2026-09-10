from flask import Flask, jsonify, render_template
from datetime import date, timedelta, datetime, timezone
from urllib.parse import quote
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


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000)
