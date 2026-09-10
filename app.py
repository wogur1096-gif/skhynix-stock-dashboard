from flask import Flask, jsonify, render_template
from datetime import date, timedelta, datetime
from urllib.parse import quote
import requests, xml.etree.ElementTree as ET

app = Flask(__name__)
KRX_URL = "https://data.krx.co.kr/comm/bldAttendant/getJsonData.cmd"
REFERER = "https://data.krx.co.kr/contents/MDC/MDI/mdiLoader/index.cmd?menuId=MDC0201010105"
ISU_CD = "KR7000660001"

def num(v):
    if v is None: return None
    s=str(v).strip().replace(',', '')
    if s in ('','-'): return None
    try: return float(s)
    except: return None

def fetch_krx():
    end=date.today(); start=end-timedelta(days=365)
    payload={
      'bld':'dbms/MDC/STAT/standard/MDCSTAT01701','locale':'ko_KR','isuCd':ISU_CD,
      'strtDd':start.strftime('%Y%m%d'),'endDd':end.strftime('%Y%m%d'),
      'adjStkPrc_check':'Y','adjStkPrc':'2','share':'1','money':'1','csvxls_isNo':'false'}
    h={'Referer':REFERER,'User-Agent':'Mozilla/5.0','Accept':'application/json,text/plain,*/*'}
    r=requests.post(KRX_URL,data=payload,headers=h,timeout=15); r.raise_for_status()
    raw=r.json().get('output',[])
    rows=[]
    for x in raw:
      d=(x.get('TRD_DD') or '').replace('/','-'); c=num(x.get('TDD_CLSPRC'))
      if not d or c is None: continue
      rows.append({'date':d,'close':c,'change':num(x.get('CMPPREVDD_PRC')),'pct':num(x.get('FLUC_RT')),
        'open':num(x.get('TDD_OPNPRC')),'high':num(x.get('TDD_HGPRC')),'low':num(x.get('TDD_LWPRC')),
        'volume':num(x.get('ACC_TRDVOL')),'value':num(x.get('ACC_TRDVAL'))})
    rows.sort(key=lambda z:z['date'])
    if len(rows)<20: raise RuntimeError('KRX 응답 데이터가 충분하지 않습니다.')
    return rows,start.isoformat(),end.isoformat()

def fetch_news():
    end=date.today(); start=end-timedelta(days=365)
    q=f'SK하이닉스 OR \\"SK hynix\\" after:{start.isoformat()} before:{(end+timedelta(days=1)).isoformat()}'
    url='https://news.google.com/rss/search?q='+quote(q)+'&hl=ko&gl=KR&ceid=KR:ko'
    r=requests.get(url,headers={'User-Agent':'Mozilla/5.0'},timeout=12); r.raise_for_status()
    root=ET.fromstring(r.text); out=[]
    for item in root.findall('.//item')[:80]:
      title=(item.findtext('title') or '').strip(); link=(item.findtext('link') or '').strip(); pub=(item.findtext('pubDate') or '').strip()
      src=item.find('source'); source=src.text.strip() if src is not None and src.text else 'Google News'
      try: d=datetime.strptime(pub,'%a, %d %b %Y %H:%M:%S %Z').date().isoformat()
      except: continue
      low=title.lower(); tag='주요 이슈' if any(k in low for k in ['실적','hbm','ai','엔비디아','nvidia','중국','규제','관세','adr','증자','배당','자사주','메모리','반도체','투자','공급','수요','급락','급등']) else '관련 뉴스'
      out.append({'date':d,'impact':'mixed','tag':tag,'title':title,'summary':'접속 시점 기준 자동 수집된 SK하이닉스 관련 뉴스입니다.','url':link,'source':source})
    out.sort(key=lambda x:x['date']); return out

@app.get('/')
def home(): return render_template('index.html')

@app.get('/api/data')
def data():
    rows,start,end=fetch_krx()
    try: news=fetch_news(); news_error=None
    except Exception as e: news=[]; news_error=str(e)
    return jsonify({'ticker':'000660','name':'SK하이닉스','source':'KRX Data Marketplace','requested_start':start,'requested_end':end,'last_trade_date':rows[-1]['date'],'updated_at':datetime.now().isoformat(timespec='seconds'),'rows':rows,'news':news,'news_error':news_error})

@app.get('/api/health')
def health(): return jsonify({'ok':True,'date':date.today().isoformat()})

if __name__=='__main__': app.run(host='0.0.0.0',port=8000)
