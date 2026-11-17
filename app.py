"""건양대학교 뀨 스탑 (KYU STOP) — 버스 대기 라운지 & 카페 웹앱
실행:  streamlit run app.py
"""
import base64
import csv
import random
import string
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.parse import quote

import streamlit as st
import streamlit.components.v1 as components

st.set_page_config(page_title="뀨 스탑 | 건양대학교 버스 라운지", page_icon="🐑",
                   layout="wide", initial_sidebar_state="collapsed")

BASE = Path(__file__).parent
ASSETS = BASE / "assets"

# ───────────── 여기만 고치면 내용이 바뀝니다 ─────────────
STOP_NAME = "뀨 스탑 (건양대학교 대전캠퍼스)"
STOP_ADDRESS = "대전 서구 관저동로 158 건양대학교 대전캠퍼스 내"
STOP_LAT, STOP_LON = 36.3040, 127.3400   # ← 실제 설치 위치 좌표로 바꿔 주세요 (구글/네이버 지도에서 복사)
OPEN_HOURS = "평일 07:00 – 20:00"        # ← 예시입니다. 실제 운영 시간으로 수정

CAMPUS = ["대전캠퍼스", "논산캠퍼스"]
# (노선 이름, 구분) — 호차 번호는 화면에 표시하지 않아요
REGIONAL = [("선사", "주중"), ("삼천중학교", "주중"), ("대전복합터미널", "주중"), ("송강", "주중"),
            ("신탄진", "주중"), ("북세종", "주중"), ("남세종·공주", "주중"), ("아산·천안", "주중"),
            ("청주·오송", "주중"), ("서울 노선", "주말"), ("인천·경기 노선", "주말"),
            ("태안·서산·당진", "주말"), ("예산·홍성·광천·보령", "주말"), ("대전복합터미널", "주말"),
            ("논산·대전", "방학")]
LOOP_STOPS = ["대전캠퍼스", "대전복합터미널", "대전역 동광장"]          # ← 예시: 실제 정류장으로 수정
TIMES = {"등교": ["07:30", "08:30"], "하교": ["17:30", "19:00"],       # ← 예시: 실제 출발 시간으로 수정
         "순환": ["09:00", "12:00", "15:00"]}
DAYS = {"주중": {0, 1, 2, 3, 4}, "주말": {4, 5, 6}}                   # 그 외(방학·셔틀)는 요일 제한 없음


def build_routes():
    r = {"등교": {}, "하교": {}, "순환": {}}   # 구분 → {노선 표시이름: (분류, 정류장 순서)}
    for n, k in REGIONAL:
        r["등교"][f"{k} · {n} → 건양대학교"] = (k, [n] + CAMPUS)
        r["하교"][f"{k} · 건양대학교 → {n}"] = (k, CAMPUS[::-1] + [n])
    r["순환"]["셔틀 · 논산 → 대전"] = ("셔틀", CAMPUS[::-1])
    r["순환"]["셔틀 · 대전 → 논산"] = ("셔틀", CAMPUS)
    r["순환"]["주중 · 대전 순환"] = ("주중", LOOP_STOPS)
    return r


ROUTES = build_routes()
DRINKS = {
    "COFFEE": ["아메리카노", "라떼", "바닐라라떼", "카라멜라떼", "카페모카"],
    "NON COFFEE": ["그린티라떼", "초콜릿", "딸기라떼", "복숭아에이드", "레몬에이드"],
    "TEA & ETC": ["얼그레이", "복숭아티", "히비스커스", "요거트스무디", "말차스무디"],
}
ICE_ONLY = {"복숭아에이드", "레몬에이드", "딸기라떼", "요거트스무디", "말차스무디"}
# ─────────────────────────────────────────────────────────


@st.cache_data
def img(name: str) -> str:
    return base64.b64encode((ASSETS / name).read_bytes()).decode()


def html(s: str) -> str:
    """st.markdown이 들여쓰기·빈 줄을 코드블록으로 오해하지 않도록 정리"""
    return "\n".join(l.strip() for l in s.splitlines() if l.strip())


def md(s: str):
    for k, v in ICONS.items():
        s = s.replace(f"[[{k}]]", f'<svg class="i" viewBox="0 0 24 24" aria-hidden="true">{v}</svg>')
    st.markdown(html(s), unsafe_allow_html=True)


CSS = """
<style>
@import url('https://cdn.jsdelivr.net/gh/orioncactus/pretendard@v1.3.9/dist/web/static/pretendard.min.css');
@import url('https://fonts.googleapis.com/css2?family=Jua&display=swap');
:root{--g:#0F4C3A;--g2:#176B50;--gd:#082B20;--ink:#14231D;--mut:#5C6E65;--bg:#F2F5F0;--line:#DCE5DE;
--sun:#F7B885;--lav:#A99BE8;--mint:#E4EFE7;--ext:url(data:image/jpeg;base64,__EXT__)}
html{scroll-behavior:smooth}
#MainMenu,footer,[data-testid="stHeader"],[data-testid="stToolbar"],[data-testid="stDecoration"]{display:none!important}
.stApp{background:var(--bg);overflow-x:hidden;font-family:'Pretendard',sans-serif;color:var(--ink)}
.block-container{max-width:1180px!important;padding:0 24px 0!important}
.stApp *{font-family:'Pretendard',sans-serif}
h1,h2,h3,.jua{font-family:'Jua','Pretendard',sans-serif!important;letter-spacing:-.01em}
/* nav */
.nav{position:fixed;top:0;left:0;right:0;z-index:999;display:flex;align-items:center;justify-content:space-between;
padding:12px max(24px,calc(50vw - 590px));background:rgba(255,255,255,.88);backdrop-filter:blur(14px);border-bottom:1px solid var(--line)}
.nav a{text-decoration:none!important}
.brand{display:flex;align-items:center;gap:12px;color:var(--ink)!important}
.logo{width:42px;height:42px;border-radius:14px;background:var(--g);color:#fff;display:grid;place-items:center;font-family:'Jua';font-size:22px}
.brand b{display:block;font-family:'Jua';font-size:20px;line-height:1.1}.brand small{color:var(--mut);font-weight:700;font-size:11px;letter-spacing:.14em}
.links{display:flex;gap:30px}.links a{color:#3a4c43;font-weight:700;font-size:15px}.links a:hover{color:var(--g)}
.cta{background:var(--g);color:#fff!important;padding:11px 22px;border-radius:12px;font-weight:800;font-size:15px}
.cta:hover{background:var(--g2)}
/* hero */
.hero{width:100vw;margin-left:calc(50% - 50vw);min-height:100vh;display:flex;align-items:center;
background:linear-gradient(90deg,rgba(5,30,22,.92) 0%,rgba(5,30,22,.62) 42%,rgba(5,30,22,.08) 100%),var(--ext) center/cover}
.hero-in{width:100%;max-width:1180px;margin:0 auto;padding:130px 24px 90px}
.badge{display:inline-block;padding:9px 18px;border-radius:99px;border:1px solid rgba(255,255,255,.35);background:rgba(255,255,255,.1);
color:#fff;font-weight:700;font-size:14px;backdrop-filter:blur(6px)}
.hero h1{color:#fff;font-size:clamp(46px,7.4vw,92px);line-height:1.12;margin:26px 0 22px}
.hero p{color:rgba(255,255,255,.88);font-size:clamp(16px,1.7vw,19px);line-height:1.8;max-width:540px;margin:0 0 38px}
.btns{display:flex;gap:12px;flex-wrap:wrap}
.b1,.b2{padding:16px 30px;border-radius:14px;font-weight:800;font-size:16px;text-decoration:none!important}
.b1{background:#fff;color:var(--g)!important}.b1:hover{background:var(--sun)}
.b2{border:1px solid rgba(255,255,255,.5);color:#fff!important;background:rgba(255,255,255,.08)}.b2:hover{background:rgba(255,255,255,.2)}
/* sections */
.sec{padding:96px 0 28px;scroll-margin-top:64px}
.sec h2{font-size:clamp(32px,4.4vw,50px);margin:0 0 14px;line-height:1.2}
.lead{color:var(--mut);font-size:17px;line-height:1.85;max-width:560px;margin:0}
.tag{display:inline-block;color:var(--g);background:var(--mint);font-weight:800;font-size:13px;padding:6px 14px;border-radius:99px;margin-bottom:16px}
.info{background:#fff;border:1px solid var(--line);border-radius:26px;padding:30px;height:100%;box-sizing:border-box}
.info h3{margin:0 0 18px;font-size:24px}
.row{padding:14px 0;border-top:1px solid var(--line)}.row small{display:block;color:var(--mut);font-weight:700;margin-bottom:4px}
.row span{font-weight:700;line-height:1.6}
.chips{display:flex;flex-wrap:wrap;gap:8px;margin-top:8px}
.chip{border:1px solid var(--line);background:#fff;border-radius:99px;padding:7px 14px;font-weight:700;font-size:14px;color:#33463d}
.split{display:grid;grid-template-columns:1.25fr 1fr;gap:52px;align-items:center}
.photo{aspect-ratio:3/2;border-radius:32px;background:var(--ext) center/cover;box-shadow:0 30px 60px -28px rgba(8,43,32,.55)}
.feat{list-style:none;padding:0;margin:26px 0 0;display:grid;gap:12px}
.feat li{display:flex;gap:14px;align-items:flex-start;line-height:1.6}.feat i{font-style:normal;font-size:22px;width:44px;height:44px;border-radius:14px;background:var(--mint);display:grid;place-items:center;flex:none}
.feat b{display:block}.feat span{color:var(--mut);font-size:15px}
.floors{display:grid;grid-template-columns:1fr 1fr;gap:24px;margin-top:34px}
.floor{background:#fff;border:1px solid var(--line);border-radius:28px;overflow:hidden}
.floor img{width:100%;display:block}.floor div{padding:24px 26px 28px}.floor h3{margin:0 0 8px;font-size:23px}.floor p{margin:0;color:var(--mut);line-height:1.75;font-size:15.5px}
.steps{list-style:none;padding:0;margin:34px 0 0;display:grid;gap:22px}
.steps li{display:flex;gap:16px}.steps em{font-style:normal;width:42px;height:42px;border-radius:50%;border:2px solid var(--g2);color:var(--g);display:grid;place-items:center;font-weight:800;flex:none}
.steps b{display:block;font-size:18px}.steps span{color:var(--mut);font-size:15px}
.form-h{display:flex;gap:14px;align-items:center;margin-bottom:8px}.form-h .ico{width:50px;height:50px;border-radius:16px;background:var(--mint);display:grid;place-items:center;font-size:24px}
.form-h h3{margin:0;font-size:26px}.form-h p{margin:2px 0 0;color:var(--mut);font-size:14px}
.ticket{margin-top:30px;border-radius:26px;background:var(--g);color:#fff;padding:26px 28px;position:relative;overflow:hidden}
.ticket:after{content:"";position:absolute;right:-50px;top:-50px;width:160px;height:160px;border-radius:50%;background:rgba(247,184,133,.25)}
.ticket small{opacity:.75;font-weight:700}.ticket .code{font-family:'Jua';font-size:32px;margin:4px 0 16px;color:var(--sun)}
.ticket dl{display:grid;grid-template-columns:auto 1fr;gap:8px 18px;margin:0}.ticket dt{opacity:.7}.ticket dd{margin:0;font-weight:700}
.ticket p{margin:18px 0 0;font-size:13.5px;opacity:.8}
.notice{width:100vw;margin:110px 0 0 calc(50% - 50vw);background:var(--gd);color:#fff}
.notice-in{max-width:1180px;margin:0 auto;padding:64px 24px 40px;display:grid;grid-template-columns:1.1fr 1fr 1fr;gap:40px}
.notice h2{font-size:32px;margin:0;color:#fff}.notice h4{margin:0 0 10px;font-size:18px;color:#fff}.notice p{margin:0;color:#9FB5AA;line-height:1.8;font-size:15px}
.foot{max-width:1180px;margin:0 auto;padding:22px 24px 34px;border-top:1px solid rgba(255,255,255,.1);color:#7F988B;font-size:13.5px;display:flex;justify-content:space-between;gap:16px;flex-wrap:wrap}
/* widgets */
:is(.st-key-resv,.st-key-dform){background:#fff;border:1px solid var(--line);border-radius:30px;padding:34px 34px 38px;box-shadow:0 30px 60px -36px rgba(8,43,32,.4)}
[data-testid="stWidgetLabel"] p{font-weight:800;font-size:14.5px;color:var(--ink)}
.stTextInput input,[data-baseweb="select"]>div,[data-testid="stDateInput"] input{border-radius:14px!important;min-height:48px}
:is(.st-key-resv,.st-key-dform) .stButton,:is(.st-key-resv,.st-key-dform) .stButton>button{width:100%}
:is(.st-key-resv,.st-key-dform) .stButton>button{height:58px;border-radius:16px;font-size:17px;font-weight:800;background:var(--g);color:#fff;border:0;margin-top:10px}
:is(.st-key-resv,.st-key-dform) .stButton>button:hover{background:var(--g2);color:#fff}
.st-key-mapbox iframe{border-radius:26px;border:1px solid var(--line)}
.hero h1,.hero p,.badge,.btns,.glass{animation:rise .8s cubic-bezier(.2,.7,.2,1) both}.hero h1{animation-delay:.08s}.hero p{animation-delay:.16s}.btns{animation-delay:.24s}.glass{animation-delay:.34s}
@keyframes rise{from{opacity:0;transform:translateY(22px)}}
.glass{margin-top:58px;display:flex;flex-wrap:wrap;max-width:780px;border:1px solid rgba(255,255,255,.28);border-radius:20px;background:rgba(255,255,255,.12);backdrop-filter:blur(12px);overflow:hidden}
.glass>div{flex:1 1 200px;padding:16px 20px;color:#fff;display:flex;gap:12px;align-items:center;border-right:1px solid rgba(255,255,255,.18)}.glass>div:last-child{border:0}
.glass small{display:block;opacity:.72;font-size:12.5px}.glass b{font-size:15px}
svg.i{width:22px;height:22px;fill:none;stroke:currentColor;stroke-width:1.9;stroke-linecap:round;stroke-linejoin:round;flex:none}
.feat i svg,.form-h .ico svg{color:var(--g);width:24px;height:24px}
.photo-wrap{position:relative}.photo-wrap:before{content:"";position:absolute;inset:24px -20px -20px 24px;border-radius:36px;background:var(--lav);opacity:.35}.photo-wrap .photo{position:relative}
.rgroup{margin:38px 0 14px;display:flex;align-items:baseline;gap:12px}.rgroup h3{margin:0;font-size:25px}.rgroup span{color:var(--mut);font-size:14px}
.rgrid{display:grid;grid-template-columns:repeat(auto-fill,minmax(210px,1fr));gap:12px}
.rc{background:#fff;border:1px solid var(--line);border-radius:18px;padding:18px;display:flex;flex-direction:column;gap:9px;transition:transform .2s,box-shadow .2s}
.rc:hover{transform:translateY(-3px);box-shadow:0 18px 30px -22px rgba(8,43,32,.6)}.rc b{font-size:17px;line-height:1.35}.rc small{color:var(--mut)}
.k{align-self:flex-start;font-size:12.5px;font-weight:800;padding:4px 11px;border-radius:99px}
.k.w{background:#E1F1E8;color:#146344}.k.e{background:#EAE5FB;color:#5B49B8}.k.v{background:#FFE8D2;color:#A5571B}.k.s{background:#DDEBFA;color:#1F5C99}
.board{display:grid;grid-template-columns:repeat(3,1fr);gap:18px;background:#0E3B2D;border:9px solid #D8BE94;border-radius:22px;padding:24px;color:#F3EEDD;margin-bottom:22px}
.board h4{margin:0 0 12px;font-family:'Jua';font-size:18px;color:var(--sun)}.board ul{list-style:none;margin:0;padding:0;display:grid;gap:8px;font-size:14.5px}
.cart{margin:6px 0 14px;display:grid;gap:8px}.cl{display:flex;justify-content:space-between;background:var(--mint);border-radius:14px;padding:12px 16px;font-weight:700}
.stTabs [data-baseweb="tab-list"]{gap:8px;border:0;margin-bottom:26px}
.stTabs [data-baseweb="tab"]{height:48px;padding:0 26px;border-radius:99px;background:#fff;border:1px solid var(--line)}
.stTabs [data-baseweb="tab"] p{font-weight:800;font-size:15.5px}
.stTabs [aria-selected="true"]{background:var(--g);border-color:var(--g)}.stTabs [aria-selected="true"] p{color:#fff}
.stTabs [data-baseweb="tab-highlight"],.stTabs [data-baseweb="tab-border"]{display:none}
.st-key-add button,.st-key-clr button{background:var(--mint)!important;color:var(--g)!important;border:1px solid var(--line)!important;height:50px!important;margin:0!important}
@media(max-width:860px){.links{display:none}.split,.floors,.notice-in,.board{grid-template-columns:1fr}.split{gap:28px}:is(.st-key-resv,.st-key-dform){padding:22px}}
@media(prefers-reduced-motion:reduce){html{scroll-behavior:auto}}
</style>
"""


def nav_and_hero():
    md(CSS.replace("__EXT__", img("exterior.jpg")))
    md("""
    <nav class="nav"><a class="brand" href="#top"><span class="logo">뀨</span><span><b>뀨 스탑</b><small>KYU STOP</small></span></a>
    <div class="links"><a href="#location">정류장 위치</a><a href="#service">서비스 안내</a><a href="#inside">내부 구조</a><a href="#routes">운행 노선</a><a href="#reserve">예약·주문</a></div>
    <a class="cta" href="#reserve">예약하기</a></nav>
    <header id="top" class="hero"><div class="hero-in">
    <span class="badge">건양대학교 버스 대기 라운지 &amp; 카페</span>
    <h1>기다림도<br>설렘이 될 수 있도록</h1>
    <p>버스를 기다리는 시간, 따뜻한 라운지에서 음료 한 잔과 함께 쉬어 가세요. 뀨 스탑에서 좌석과 음료를 미리 예약할 수 있어요.</p>
    <div class="btns"><a class="b1" href="#reserve">지금 예약하기</a><a class="b2" href="#location">정류장 위치 보기</a></div>
    <div class="glass"><div>[[pin]]<span><small>위치</small><b>대전캠퍼스 버스 승차장 옆</b></span></div>
    <div>[[clock]]<span><small>운영 시간</small><b>__HRS__</b></span></div><div>[[coffee]]<span><small>음료</small><b>사전 주문 가능</b></span></div></div>
    </div></header>""".replace("__HRS__", OPEN_HOURS))


def location():
    md("""<section id="location" class="sec"><span class="tag">정류장 위치</span><h2>뀨 스탑은 여기에 있어요</h2>
    <p class="lead">버스 승차장 바로 옆에서 노란 불빛이 켜진 초록색 2층 건물을 찾아보세요.</p></section>""")
    left, right = st.columns([1.5, 1], gap="large")
    d = 0.004
    bbox = f"{STOP_LON-d},{STOP_LAT-d*.6},{STOP_LON+d},{STOP_LAT+d*.6}"
    src = (f"https://www.openstreetmap.org/export/embed.html?bbox={quote(bbox, safe=',')}"
           f"&layer=mapnik&marker={STOP_LAT},{STOP_LON}")
    with left:
        with st.container(key="mapbox"):
            if hasattr(st, "iframe"):          # 최신 Streamlit
                st.iframe(src, height=430)
            else:                              # 구버전 호환
                components.iframe(src, height=430)
    with right:
        md(f"""<div class="info"><h3>{STOP_NAME}</h3>
        <div class="row"><small>주소</small><span>{STOP_ADDRESS}</span></div>
        <div class="row"><small>운영 시간</small><span>{OPEN_HOURS}</span></div>
        <div class="row"><small>정차 노선</small><div class="chips"><span class="chip">대전 방면</span><span class="chip">논산 방면</span><span class="chip">순환</span></div></div>
        <div class="row"><a href="https://www.openstreetmap.org/?mlat={STOP_LAT}&amp;mlon={STOP_LON}#map=18/{STOP_LAT}/{STOP_LON}" target="_blank" style="font-weight:800;color:var(--g)">큰 지도에서 보기</a></div></div>""")


def service_and_inside():
    md("""<section id="service" class="sec"><div class="split"><div class="photo-wrap"><div class="photo" role="img" aria-label="뀨 스탑 외관"></div></div>
    <div><span class="tag">서비스 안내</span><h2>버스를 기다리는 시간이 쉼이 되는 곳</h2>
    <p class="lead">뀨 스탑은 2층 통유리 라운지와 1층 카페로 이루어진 버스 대기 공간이에요. 비 오는 날에도, 추운 아침에도 따뜻하게 기다릴 수 있어요.</p>
    <ul class="feat"><li><i>[[bus]]</i><div><b>버스 정류장과 바로 연결</b><span>승차장 옆이라 출발 직전까지 편하게 쉴 수 있어요.</span></div></li>
    <li><i>[[coffee]]</i><div><b>카페 음료 사전 선택</b><span>버스 예약할 때 음료를 함께 골라 두세요.</span></div></li>
    <li><i>[[sun]]</i><div><b>노을이 보이는 통유리 라운지</b><span>2층에서 하늘을 보며 기다릴 수 있어요.</span></div></li></ul></div></div></section>
    <section id="inside" class="sec"><span class="tag">내부 구조</span><h2>1층은 카페, 2층은 라운지</h2>
    <p class="lead">입구로 들어오면 1층 카운터와 대기 좌석이 있고, 계단으로 올라가면 통유리 라운지가 나와요.</p>
    <div class="floors">
    <article class="floor"><img src="data:image/jpeg;base64,__F1__" alt="1층 내부 구성"><div><h3>1층 · 카페와 대기 공간</h3>
    <p>입구로 들어오면 왼쪽에 2층으로 가는 계단이 있어요. 가운데 카운터에서 메뉴판을 보고 주문하고, 학생들이 대기할 수 있는 좌석과 테이크아웃 코너를 이용할 수 있어요.</p></div></article>
    <article class="floor"><img src="data:image/jpeg;base64,__F2__" alt="2층 통유리 라운지"><div><h3>2층 · 통유리 라운지</h3>
    <p>천장까지 이어진 통유리창으로 하늘이 보여요. 창가 긴 테이블과 둥근 테이블이 있어 혼자 쉬기도, 친구와 이야기하기도 좋아요.</p></div></article>
    </div></section>""".replace("__F1__", img("floor1.jpg")).replace("__F2__", img("floor2.jpg")))


ICONS = {
    "bus": '<path d="M8 6v6"/><path d="M15 6v6"/><path d="M2 12h19.6"/><path d="M18 18h3s.5-1.7.8-2.8c.1-.4.2-.8.2-1.2 0-.4-.1-.8-.2-1.2l-1.4-5C20.1 6.8 19.1 6 18 6H4a2 2 0 0 0-2 2v10h3"/><circle cx="7" cy="18" r="2"/><path d="M9 18h5"/><circle cx="16" cy="18" r="2"/>',
    "coffee": '<path d="M10 2v2"/><path d="M14 2v2"/><path d="M16 8a1 1 0 0 1 1 1v8a4 4 0 0 1-4 4H7a4 4 0 0 1-4-4V9a1 1 0 0 1 1-1h14a4 4 0 1 1 0 8h-1"/><path d="M6 2v2"/>',
    "sun": '<path d="M12 10V2"/><path d="m4.93 10.93 1.41 1.41"/><path d="M2 18h2"/><path d="M20 18h2"/><path d="m19.07 10.93-1.41 1.41"/><path d="M22 22H2"/><path d="m16 6-4 4-4-4"/><path d="M16 18a4 4 0 0 0-8 0"/>',
    "pin": '<path d="M20 10c0 4.993-5.539 10.193-7.399 11.799a1 1 0 0 1-1.202 0C9.539 20.193 4 14.993 4 10a8 8 0 0 1 16 0"/><circle cx="12" cy="10" r="3"/>',
    "clock": '<circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/>',
    "ticket": '<path d="M2 9a3 3 0 0 1 0 6v2a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2v-2a3 3 0 0 1 0-6V7a2 2 0 0 0-2-2H4a2 2 0 0 0-2 2Z"/><path d="M13 5v2"/><path d="M13 17v2"/><path d="M13 11v2"/>',
}


def routes_section():
    groups = [("주중", "w", "평일에 운행하는 노선"), ("주말", "e", "주말에 운행하는 노선"),
              ("방학", "v", "방학 중에 운행하는 노선"), ("셔틀", "s", "캠퍼스 사이를 오가는 셔틀")]
    out = ['<section id="routes" class="sec"><span class="tag">운행 노선</span><h2>어디서든 학교까지</h2>'
           '<p class="lead">지역별 통학 노선과 캠퍼스 셔틀이에요. 아래 예약 화면에서 등교·하교·순환을 골라 예약하세요.</p>']
    for k, c, desc in groups:
        if k == "셔틀":
            cards = [("논산 → 대전", "순환"), ("대전 → 논산", "순환")]
        else:
            cards = [(n, "등교 · 하교") for n, kk in REGIONAL if kk == k]
            if k == "주중":
                cards.append(("대전 순환", "순환"))
        out.append(f'<div class="rgroup"><h3>{k}</h3><span>{desc}</span></div><div class="rgrid">')
        out += [f'<div class="rc"><span class="k {c}">{k}</span><b>{n}</b><small>{s}</small></div>' for n, s in cards]
        out.append("</div>")
    out.append("</section>")
    md("".join(out))


def save_csv(fname, header, row):
    f = BASE / fname
    try:
        new = not f.exists()
        with f.open("a", newline="", encoding="utf-8-sig") as fh:
            w = csv.writer(fh)
            if new:
                w.writerow(header)
            w.writerow(row)
    except OSError:
        pass  # 배포 환경에서 쓰기가 막혀도 화면 동작은 유지


def ticket(small, code, rows, note):
    dl = "".join(f"<dt>{a}</dt><dd>{b}</dd>" for a, b in rows)
    md(f'<div class="ticket" role="status"><small>{small}</small><div class="code">{code}</div><dl>{dl}</dl><p>{note}</p></div>')


def rand4():
    return "".join(random.choices(string.ascii_uppercase + string.digits, k=4))


def reservation():
    md("""<section id="reserve" class="sec"><span class="tag">예약·주문</span><h2>탈 버스와 마실 음료를 정해요</h2>
    <p class="lead">버스 좌석은 예약하고, 기다리는 동안 마실 음료는 따로 주문할 수 있어요.</p></section>""")
    t_bus, t_drink = st.tabs(["버스 예약", "음료 주문"])
    with t_bus:
        bus_tab()
    with t_drink:
        drink_tab()


def bus_tab():
    left, right = st.columns([1, 1.45], gap="large")
    with right:
        with st.container(key="resv"):
            md("""<div class="form-h"><span class="ico">[[ticket]]</span><div><h3>예약 정보 입력</h3><p>구분과 노선을 고르고 이름을 입력해 주세요.</p></div></div>""")
            kind = st.segmented_control("운행 구분", list(ROUTES), default="등교", key="kind") or "등교"
            route = st.selectbox("노선", list(ROUTES[kind]), key=f"route_{kind}")
            rk, stops = ROUTES[kind][route]
            c1, c2 = st.columns(2)
            time_ = c1.selectbox("출발 시간", TIMES[kind], key=f"time_{kind}")
            today = date.today()
            day = c2.date_input("탑승일", value=today, min_value=today,
                                max_value=today + timedelta(days=30), format="YYYY-MM-DD")
            if rk in DAYS:
                st.caption("주중 노선은 월~금에 운행해요." if rk == "주중" else "주말 노선은 금·토·일에 운행해요.")
            name = st.text_input("이름", placeholder="홍길동", max_chars=20)
            c3, c4 = st.columns(2)
            board = c3.selectbox("승차 정류장", stops[:-1], key=f"board_{route}")
            alight = c4.selectbox("하차 정류장", stops[stops.index(board) + 1:], key=f"alight_{route}_{board}")
            if st.button("좌석 예약하기", type="primary"):
                if not name.strip():
                    st.error("이름을 입력해 주세요.")
                elif rk in DAYS and day.weekday() not in DAYS[rk]:
                    st.error(f"{route}은(는) {'월~금' if rk == '주중' else '금·토·일'}에만 운행해요. 탑승일을 바꿔 주세요.")
                else:
                    code = f"KYU-{day:%y%m%d}-{rand4()}"
                    t = dict(code=code, kind=kind, route=route, time=time_, day=f"{day:%Y-%m-%d}",
                             name=name.strip(), board=board, alight=alight)
                    save_csv("reservations.csv", ["예약번호", "신청시각", "구분", "노선", "출발", "탑승일", "이름", "승차", "하차"],
                             [code, datetime.now().strftime("%Y-%m-%d %H:%M"), kind, route, time_, t["day"], t["name"], board, alight])
                    st.session_state["ticket"] = t
                    st.toast("예약이 완료됐어요!", icon="✅")
    with left:
        md("""<p class="lead">예약은 탑승일 30일 전부터 가능해요. 탑승할 때 학생증과 예약번호를 보여 주세요.</p>
        <ol class="steps"><li><em>1</em><div><b>운행 선택</b><span>등교·하교·순환 중 고르고 노선과 시간을 선택해요.</span></div></li>
        <li><em>2</em><div><b>정보 입력</b><span>탑승일, 이름, 승·하차 정류장을 선택해요.</span></div></li>
        <li><em>3</em><div><b>예약 확정</b><span>발급된 예약번호를 탑승 전까지 보관해요.</span></div></li></ol>""")
        t = st.session_state.get("ticket")
        if t:
            ticket("예약이 완료됐어요 · 예약번호", t["code"],
                   [("구분", f"{t['kind']} · {t['route']}"), ("탑승", f"{t['day']} {t['time']}"),
                    ("구간", f"{t['board']} → {t['alight']}"), ("이름", t["name"])],
                   "탑승할 때 학생증과 예약번호를 보여 주세요.")


def drink_tab():
    left, right = st.columns([1, 1.45], gap="large")
    cart = st.session_state.setdefault("cart", [])
    with right:
        with st.container(key="dform"):
            md("""<div class="form-h"><span class="ico">[[coffee]]</span><div><h3>음료 주문</h3><p>메뉴를 담고 수령 정보를 입력해 주세요.</p></div></div>""")
            cat = st.segmented_control("종류", list(DRINKS), default="COFFEE", key="d_cat") or "COFFEE"
            drink = st.pills("메뉴", DRINKS[cat], default=DRINKS[cat][0], key=f"d_{cat}") or DRINKS[cat][0]
            c1, c2 = st.columns(2)
            temp = c1.selectbox("온도", ["ICE"] if drink in ICE_ONLY else ["ICE", "HOT"], key=f"t_{drink}")
            qty = c2.number_input("수량", 1, 10, 1, key="qty")
            if st.button("메뉴 담기", key="add"):
                cart.append(dict(d=drink, t=temp, q=int(qty)))
            if cart:
                md('<div class="cart">' + "".join(f'<div class="cl"><span>{c["d"]} ({c["t"]})</span><span>{c["q"]}잔</span></div>' for c in cart) + "</div>")
                if st.button("장바구니 비우기", key="clr"):
                    cart.clear()
                    st.rerun()
            else:
                st.caption("아직 담은 메뉴가 없어요.")
            name = st.text_input("이름", placeholder="홍길동", max_chars=20, key="d_name")
            c3, c4 = st.columns(2)
            how = c3.selectbox("수령 방법", ["매장에서 마실게요", "테이크아웃"])
            when = c4.selectbox("수령 시간", ["지금", "10분 후", "20분 후", "30분 후"])
            bus = st.text_input("버스 예약번호 (선택)", placeholder="KYU-261117-AB12", key="d_bus")
            if st.button("음료 주문하기", type="primary", key="d_order"):
                if not cart:
                    st.error("먼저 메뉴를 담아 주세요.")
                elif not name.strip():
                    st.error("이름을 입력해 주세요.")
                else:
                    items = ", ".join(f"{c['d']}({c['t']}) {c['q']}잔" for c in cart)
                    code = f"DR-{datetime.now():%H%M}-{rand4()}"
                    save_csv("drink_orders.csv", ["주문번호", "주문시각", "이름", "메뉴", "수령방법", "수령시간", "버스예약번호"],
                             [code, datetime.now().strftime("%Y-%m-%d %H:%M"), name.strip(), items, how, when, bus.strip()])
                    st.session_state["d_ticket"] = dict(code=code, name=name.strip(), items=items, how=how, when=when)
                    cart.clear()
                    st.rerun()
    with left:
        board = "".join(f"<div><h4>{c}</h4><ul>" + "".join(f"<li>{d}</li>" for d in ds) + "</ul></div>" for c, ds in DRINKS.items())
        md(f'<div class="board">{board}</div><p class="lead">주문한 음료는 뀨 스탑 1층 카운터에서 받아요. 버스 출발 시간에 맞춰 수령 시간을 골라 주세요.</p>')
        o = st.session_state.get("d_ticket")
        if o:
            ticket("주문이 접수됐어요 · 주문번호", o["code"],
                   [("메뉴", o["items"]), ("수령", f"{o['how']} · {o['when']}"), ("이름", o["name"])],
                   "1층 카운터에서 주문번호를 말씀해 주세요.")


def footer():
    md("""<footer class="notice"><div class="notice-in"><h2>탑승 전 확인해 주세요</h2>
    <div><h4>탑승 시간 준수</h4><p>출발 10분 전까지 정류장에 도착해 주세요. 정시 출발을 원칙으로 합니다.</p></div>
    <div><h4>예약 취소 문의</h4><p>변경 및 취소는 학생복지팀으로 문의해 주세요. 무단 미탑승이 반복되면 이용이 제한될 수 있습니다.</p></div></div>
    <div class="foot"><span>건양대학교 뀨 스탑 버스 예약 서비스</span><span>이미지는 이해를 돕기 위한 예시입니다.</span></div></footer>""")


nav_and_hero()
location()
service_and_inside()
routes_section()
reservation()
footer()
