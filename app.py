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
STOP_NAME = "뀨 스탑 (건양대학교 메디컬캠퍼스)"
STOP_ADDRESS = "대전 서구 관저동로 158 건양대학교 메디컬캠퍼스 내"
STOP_LAT, STOP_LON = 36.3040, 127.3400   # ← 실제 설치 위치 좌표로 바꿔 주세요 (구글/네이버 지도에서 복사)
OPEN_HOURS = "평일 07:00 – 20:00"        # ← 예시입니다. 실제 운영 시간으로 수정

CAMPUS_A = ["대전역 동광장", "서대전네거리역", "건양대학교병원", "메디컬캠퍼스"]
CAMPUS_B = ["논산역", "논산시외버스터미널", "내동사거리", "창의융합캠퍼스"]
LOOP = ["창의융합캠퍼스", "논산역", "서대전네거리역", "메디컬캠퍼스"]
ROUTES = {   # 구분 → {노선 이름: (출발 시간들, 정류장 순서)}  ※ 시간은 예시
    "등교": {"대전 → 메디컬캠퍼스": (["07:40", "09:10"], CAMPUS_A),
             "논산 → 창의융합캠퍼스": (["08:00", "09:30"], CAMPUS_B)},
    "하교": {"메디컬캠퍼스 → 대전": (["17:30", "19:10"], CAMPUS_A[::-1]),
             "창의융합캠퍼스 → 논산": (["17:30", "19:10"], CAMPUS_B[::-1])},
    "순환": {"논산 → 대전": (["12:00", "15:00"], LOOP),
             "대전 → 논산": (["12:30", "15:30"], LOOP[::-1])},
}
DRINKS = {
    "COFFEE": ["아메리카노", "라떼", "바닐라라떼", "카라멜라떼", "카페모카"],
    "NON COFFEE": ["그린티라떼", "초콜릿", "딸기라떼", "복숭아에이드", "레몬에이드"],
    "TEA & ETC": ["얼그레이", "복숭아티", "히비스커스", "요거트스무디", "말차스무디"],
}
ICE_ONLY = {"복숭아에이드", "레몬에이드", "딸기라떼", "요거트스무디", "말차스무디"}
DRINK_CAT = {d: c for c, ds in DRINKS.items() for d in ds}
NONE = "선택 안 함"
# ─────────────────────────────────────────────────────────


@st.cache_data
def img(name: str) -> str:
    return base64.b64encode((ASSETS / name).read_bytes()).decode()


def html(s: str) -> str:
    """st.markdown이 들여쓰기·빈 줄을 코드블록으로 오해하지 않도록 정리"""
    return "\n".join(l.strip() for l in s.splitlines() if l.strip())


def md(s: str):
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
.st-key-resv{background:#fff;border:1px solid var(--line);border-radius:30px;padding:34px 34px 38px;box-shadow:0 30px 60px -36px rgba(8,43,32,.4)}
[data-testid="stWidgetLabel"] p{font-weight:800;font-size:14.5px;color:var(--ink)}
.stTextInput input,[data-baseweb="select"]>div,[data-testid="stDateInput"] input{border-radius:14px!important;min-height:48px}
.st-key-resv .stButton,.st-key-resv .stButton>button{width:100%}
.st-key-resv .stButton>button{height:58px;border-radius:16px;font-size:17px;font-weight:800;background:var(--g);color:#fff;border:0;margin-top:10px}
.st-key-resv .stButton>button:hover{background:var(--g2);color:#fff}
.st-key-mapbox iframe{border-radius:26px;border:1px solid var(--line)}
@media(max-width:860px){.links{display:none}.split,.floors,.notice-in{grid-template-columns:1fr}.split{gap:28px}.st-key-resv{padding:22px}}
@media(prefers-reduced-motion:reduce){html{scroll-behavior:auto}}
</style>
"""


def nav_and_hero():
    md(CSS.replace("__EXT__", img("exterior.jpg")))
    md("""
    <nav class="nav"><a class="brand" href="#top"><span class="logo">뀨</span><span><b>뀨 스탑</b><small>KYU STOP</small></span></a>
    <div class="links"><a href="#location">정류장 위치</a><a href="#service">서비스 안내</a><a href="#inside">내부 구조</a><a href="#reserve">버스 예약</a></div>
    <a class="cta" href="#reserve">예약하기</a></nav>
    <header id="top" class="hero"><div class="hero-in">
    <span class="badge">건양대학교 버스 대기 라운지 &amp; 카페</span>
    <h1>기다림도<br>설렘이 될 수 있도록</h1>
    <p>버스를 기다리는 시간, 따뜻한 라운지에서 음료 한 잔과 함께 쉬어 가세요. 뀨 스탑에서 좌석과 음료를 미리 예약할 수 있어요.</p>
    <div class="btns"><a class="b1" href="#reserve">지금 예약하기</a><a class="b2" href="#location">정류장 위치 보기</a></div>
    </div></header>""")


def location():
    md("""<section id="location" class="sec"><span class="tag">정류장 위치</span><h2>뀨 스탑은 여기에 있어요</h2>
    <p class="lead">캠퍼스 안 버스 승차장 바로 옆에서 노란 불빛이 켜진 초록색 2층 건물을 찾아보세요.</p></section>""")
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
    md("""<section id="service" class="sec"><div class="split"><div class="photo" role="img" aria-label="뀨 스탑 외관"></div>
    <div><span class="tag">서비스 안내</span><h2>버스를 기다리는 시간이 쉼이 되는 곳</h2>
    <p class="lead">뀨 스탑은 2층 통유리 라운지와 1층 카페로 이루어진 버스 대기 공간이에요. 비 오는 날에도, 추운 아침에도 따뜻하게 기다릴 수 있어요.</p>
    <ul class="feat"><li><i>🚌</i><div><b>버스 정류장과 바로 연결</b><span>승차장 옆이라 출발 직전까지 편하게 쉴 수 있어요.</span></div></li>
    <li><i>☕</i><div><b>카페 음료 사전 선택</b><span>버스 예약할 때 음료를 함께 골라 두세요.</span></div></li>
    <li><i>🌇</i><div><b>노을이 보이는 통유리 라운지</b><span>2층에서 하늘을 보며 기다릴 수 있어요.</span></div></li></ul></div></div></section>
    <section id="inside" class="sec"><span class="tag">내부 구조</span><h2>1층은 카페, 2층은 라운지</h2>
    <p class="lead">입구로 들어오면 1층 카운터와 대기 좌석이 있고, 계단으로 올라가면 통유리 라운지가 나와요.</p>
    <div class="floors">
    <article class="floor"><img src="data:image/jpeg;base64,__F1__" alt="1층 내부 구성"><div><h3>1층 · 카페와 대기 공간</h3>
    <p>입구로 들어오면 왼쪽에 2층으로 가는 계단이 있어요. 가운데 카운터에서 메뉴판을 보고 주문하고, 학생들이 대기할 수 있는 좌석과 테이크아웃 코너를 이용할 수 있어요.</p></div></article>
    <article class="floor"><img src="data:image/jpeg;base64,__F2__" alt="2층 통유리 라운지"><div><h3>2층 · 통유리 라운지</h3>
    <p>천장까지 이어진 통유리창으로 하늘이 보여요. 창가 긴 테이블과 둥근 테이블이 있어 혼자 쉬기도, 친구와 이야기하기도 좋아요.</p></div></article>
    </div></section>""".replace("__F1__", img("floor1.jpg")).replace("__F2__", img("floor2.jpg")))


def save_row(row):
    f = BASE / "reservations.csv"
    try:
        new = not f.exists()
        with f.open("a", newline="", encoding="utf-8-sig") as fh:
            w = csv.writer(fh)
            if new:
                w.writerow(["예약번호", "신청시각", "구분", "노선", "출발", "탑승일", "이름", "승차", "하차", "음료"])
            w.writerow(row)
    except OSError:
        pass  # 배포 환경에서 쓰기가 막혀도 화면 예약은 동작


def reservation():
    md("""<section id="reserve" class="sec"><span class="tag">버스 예약</span><h2>버스 좌석 예약</h2></section>""")
    left, right = st.columns([1, 1.45], gap="large")

    with right:
        with st.container(key="resv"):
            md("""<div class="form-h"><span class="ico">🎫</span><div><h3>예약 정보 입력</h3><p>구분과 노선을 고르고 이름을 입력해 주세요.</p></div></div>""")
            kind = st.segmented_control("운행 구분", list(ROUTES), default="등교", key="kind") or "등교"
            route = st.selectbox("노선", list(ROUTES[kind]), key=f"route_{kind}")
            times, stops = ROUTES[kind][route]
            c1, c2 = st.columns(2)
            time_ = c1.selectbox("출발 시간", times, key=f"time_{route}")
            today = date.today()
            day = c2.date_input("탑승일", value=today, min_value=today,
                                max_value=today + timedelta(days=30), format="YYYY-MM-DD")
            name = st.text_input("이름", placeholder="홍길동", max_chars=20)
            c3, c4 = st.columns(2)
            board = c3.selectbox("승차 정류장", stops[:-1], key=f"board_{route}")
            alight = c4.selectbox("하차 정류장", stops[stops.index(board) + 1:], key=f"alight_{route}_{board}")
            drink = st.selectbox("음료 선택", [NONE] + list(DRINK_CAT), key="drink",
                                 format_func=lambda d: d if d == NONE else f"{DRINK_CAT[d]} · {d}")
            temp = ""
            if drink != NONE:
                if drink in ICE_ONLY:
                    temp = "ICE"
                    st.caption("이 음료는 ICE로만 준비돼요.")
                else:
                    temp = st.segmented_control("온도", ["ICE", "HOT"], default="ICE", key=f"temp_{drink}") or "ICE"
            go = st.button("좌석 예약하기", type="primary")

            if go:
                if not name.strip():
                    st.error("이름을 입력해 주세요.")
                else:
                    code = f"KYU-{day:%y%m%d}-" + "".join(random.choices(string.ascii_uppercase + string.digits, k=4))
                    drink_txt = NONE if drink == NONE else f"{drink} ({temp})"
                    t = dict(code=code, kind=kind, route=route, time=time_, day=f"{day:%Y-%m-%d}",
                             name=name.strip(), board=board, alight=alight, drink=drink_txt)
                    save_row([code, datetime.now().strftime("%Y-%m-%d %H:%M"), kind, route, time_, t["day"],
                              t["name"], board, alight, drink_txt])
                    st.session_state["ticket"] = t
                    st.toast("예약이 완료됐어요!", icon="✅")

    with left:
        md("""<p class="lead">예약은 탑승일 30일 전부터 가능해요. 탑승할 때 학생증과 예약번호를 보여 주세요.</p>
        <ol class="steps"><li><em>1</em><div><b>운행 선택</b><span>등교·하교·순환 중 고르고 노선과 시간을 선택해요.</span></div></li>
        <li><em>2</em><div><b>정보 입력</b><span>탑승일, 이름, 승·하차 정류장, 음료를 선택해요.</span></div></li>
        <li><em>3</em><div><b>예약 확정</b><span>발급된 예약번호를 탑승 전까지 보관해요.</span></div></li></ol>""")
        t = st.session_state.get("ticket")
        if t:
            md(f"""<div class="ticket" role="status"><small>예약이 완료됐어요 · 예약번호</small><div class="code">{t['code']}</div>
            <dl><dt>구분</dt><dd>{t['kind']} · {t['route']}</dd><dt>탑승</dt><dd>{t['day']} {t['time']}</dd>
            <dt>구간</dt><dd>{t['board']} → {t['alight']}</dd><dt>음료</dt><dd>{t['drink']}</dd><dt>이름</dt><dd>{t['name']}</dd></dl>
            <p>음료는 뀨 스탑 1층 카운터에서 받을 수 있어요.</p></div>""")


def footer():
    md("""<footer class="notice"><div class="notice-in"><h2>탑승 전 확인해 주세요</h2>
    <div><h4>탑승 시간 준수</h4><p>출발 10분 전까지 정류장에 도착해 주세요. 정시 출발을 원칙으로 합니다.</p></div>
    <div><h4>예약 취소 문의</h4><p>변경 및 취소는 학생복지팀으로 문의해 주세요. 무단 미탑승이 반복되면 이용이 제한될 수 있습니다.</p></div></div>
    <div class="foot"><span>건양대학교 뀨 스탑 버스 예약 서비스</span><span>이미지는 이해를 돕기 위한 예시입니다.</span></div></footer>""")


nav_and_hero()
location()
service_and_inside()
reservation()
footer()
