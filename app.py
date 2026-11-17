"""건양대학교 뀨 스탑 (KYU STOP) — 버스 대기 라운지 & 카페 웹앱
실행:  streamlit run app.py
"""
import base64
import csv
import io
import random
import string
from datetime import date, datetime, timedelta
from html import escape as esc
from pathlib import Path
from urllib.parse import quote

import streamlit as st
import streamlit.components.v1 as components

st.set_page_config(page_title="뀨 스탑 | 건양대학교 버스 라운지", page_icon="🐑",
                   layout="wide", initial_sidebar_state="collapsed")

BASE = Path(__file__).parent
ASSETS = BASE / "assets"

# ═════════════ 여기만 고치면 내용이 바뀝니다 (아래 값은 모두 예시예요) ═════════════
STOP_NAME = "뀨 스탑 (건양대학교 대전캠퍼스)"
STOP_ADDRESS = "대전 서구 관저동로 158 건양대학교 대전캠퍼스 내"   # ← 예시: 실제 주소로 수정
STOP_LAT, STOP_LON = 36.3040, 127.3400                        # ← 예시: 실제 설치 위치 좌표로 수정
OPEN_HOURS = "평일 07:00 – 20:00"                              # ← 예시: 실제 운영 시간으로 수정

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

# ── 음료 메뉴 (가격·설명은 모두 예시예요. 실제 메뉴판에 맞게 고쳐 주세요) ──
#   name 이름 / price 가격(원) / desc 한 줄 설명 / c 컵 색상 2개 / ice_only ICE만 가능 / shot 샷 추가 가능 / tag 배지
SHOT_PRICE = 500
MENU = {
    "COFFEE": [
        dict(name="아메리카노", price=3000, desc="진하게 내린 에스프레소에 물을 더한 깔끔한 한 잔", c=("#7A5440", "#432C20"), shot=True),
        dict(name="라떼", price=3800, desc="에스프레소에 부드러운 우유를 듬뿍", c=("#DDBE9A", "#BC9068"), shot=True),
        dict(name="바닐라라떼", price=4300, desc="바닐라 시럽의 은은한 단맛", c=("#EBD3AA", "#CFA874"), shot=True),
        dict(name="카라멜라떼", price=4300, desc="달콤한 카라멜 향이 퍼지는 라떼", c=("#E0A96C", "#BC7E40"), shot=True),
        dict(name="카페모카", price=4500, desc="초콜릿과 에스프레소의 진한 조화", c=("#A47760", "#6F4735"), shot=True),
        dict(name="노을 라떼", price=4800, desc="우유 위로 에스프레소가 번지는 노을빛 라떼", c=("#F7B885", "#F3A6B8"), ice_only=True, shot=True, tag="시그니처"),
    ],
    "NON COFFEE": [
        dict(name="그린티라떼", price=4300, desc="고소한 녹차와 우유의 부드러운 만남", c=("#BFD59A", "#8FB063")),
        dict(name="초콜릿", price=4300, desc="진한 초콜릿을 우유에 녹인 달콤한 음료", c=("#85543F", "#55321F")),
        dict(name="딸기라떼", price=4800, desc="딸기청을 우유에 섞은 상큼한 라떼", c=("#F8B6C2", "#EE8EA4"), ice_only=True),
        dict(name="복숭아에이드", price=4500, desc="복숭아 향이 가득한 시원한 에이드", c=("#FFD6B6", "#FFB68E"), ice_only=True),
        dict(name="레몬에이드", price=4500, desc="직접 만든 레몬청으로 상큼하게", c=("#FAE88F", "#F1D14F"), ice_only=True),
        dict(name="석양 에이드", price=5000, desc="하늘색에서 분홍으로 물드는 블루레몬 에이드", c=("#9CCBEA", "#F3A6B8"), ice_only=True, tag="시그니처"),
    ],
    "TEA & ETC": [
        dict(name="얼그레이", price=3500, desc="베르가못 향이 은은한 홍차", c=("#CF985E", "#A8692F")),
        dict(name="복숭아티", price=3800, desc="달콤한 복숭아를 우린 과일 티", c=("#F6BE96", "#E99A68")),
        dict(name="히비스커스", price=3800, desc="새콤한 맛과 붉은 빛깔의 허브티", c=("#DF5C78", "#B02E4C")),
        dict(name="유자차", price=3800, desc="따뜻하게 마시기 좋은 달콤 쌉싸름한 유자", c=("#F7D775", "#E3B240")),
        dict(name="요거트스무디", price=5000, desc="플레인 요거트를 갈아 만든 부드러운 스무디", c=("#F6E7EC", "#E8C9D4"), ice_only=True),
        dict(name="말차스무디", price=5000, desc="진한 말차를 곱게 갈아 만든 스무디", c=("#B1CB86", "#80A453"), ice_only=True),
    ],
}
PICKUP_HOW = ["매장에서 마실게요", "테이크아웃"]
PICKUP_WHEN = ["지금", "10분 후", "20분 후", "30분 후"]
# ═════════════════════════════════════════════════════════════════════════════════

WEEKDAY_KO = "월화수목금토일"
PAGES = ["홈", "버스 예약", "음료 주문"]
QUERY_TO_PAGE = {"bus": "버스 예약", "drink": "음료 주문", "home": "홈"}
PAGE_TO_QUERY = {v: k for k, v in QUERY_TO_PAGE.items()}


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
ALL = {m["name"]: dict(m, cat=c) for c, ms in MENU.items() for m in ms}
ORDER = list(ALL)


# ───────────────────────── 공통 도우미 ─────────────────────────
@st.cache_data(show_spinner=False)
def img(name: str, width: int = 1400) -> str:
    """사진을 줄여서 base64로 바꿔요. 파일이 없으면 빈 문자열(→ 그라데이션으로 대체)"""
    p = ASSETS / name
    if not p.exists():
        return ""
    raw = p.read_bytes()
    try:
        from PIL import Image
        im = Image.open(io.BytesIO(raw)).convert("RGB")
        if im.width > width:
            im = im.resize((width, round(im.height * width / im.width)), Image.LANCZOS)
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=82, optimize=True, progressive=True)
        raw = buf.getvalue()
    except Exception:
        pass
    return base64.b64encode(raw).decode()


def squash(s: str) -> str:
    """st.markdown이 들여쓰기·빈 줄을 코드블록으로 오해하지 않도록 정리"""
    return "\n".join(l.strip() for l in s.splitlines() if l.strip())


def md(s: str):
    for k, v in ICONS.items():
        s = s.replace(f"[[{k}]]", f'<svg class="i" viewBox="0 0 24 24" aria-hidden="true">{v}</svg>')
    st.markdown(squash(s), unsafe_allow_html=True)


def won(n: int) -> str:
    return f"{n:,}원"


def rand4() -> str:
    return "".join(random.choices(string.ascii_uppercase + string.digits, k=4))


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


ICONS = {
    "bus": '<path d="M8 6v6"/><path d="M15 6v6"/><path d="M2 12h19.6"/><path d="M18 18h3s.5-1.7.8-2.8c.1-.4.2-.8.2-1.2 0-.4-.1-.8-.2-1.2l-1.4-5C20.1 6.8 19.1 6 18 6H4a2 2 0 0 0-2 2v10h3"/><circle cx="7" cy="18" r="2"/><path d="M9 18h5"/><circle cx="16" cy="18" r="2"/>',
    "coffee": '<path d="M10 2v2"/><path d="M14 2v2"/><path d="M16 8a1 1 0 0 1 1 1v8a4 4 0 0 1-4 4H7a4 4 0 0 1-4-4V9a1 1 0 0 1 1-1h14a4 4 0 1 1 0 8h-1"/><path d="M6 2v2"/>',
    "sun": '<path d="M12 10V2"/><path d="m4.93 10.93 1.41 1.41"/><path d="M2 18h2"/><path d="M20 18h2"/><path d="m19.07 10.93-1.41 1.41"/><path d="M22 22H2"/><path d="m16 6-4 4-4-4"/><path d="M16 18a4 4 0 0 0-8 0"/>',
    "pin": '<path d="M20 10c0 4.993-5.539 10.193-7.399 11.799a1 1 0 0 1-1.202 0C9.539 20.193 4 14.993 4 10a8 8 0 0 1 16 0"/><circle cx="12" cy="10" r="3"/>',
    "clock": '<circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/>',
    "ticket": '<path d="M2 9a3 3 0 0 1 0 6v2a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2v-2a3 3 0 0 1 0-6V7a2 2 0 0 0-2-2H4a2 2 0 0 0-2 2Z"/><path d="M13 5v2"/><path d="M13 17v2"/><path d="M13 11v2"/>',
    "cart": '<circle cx="8" cy="21" r="1"/><circle cx="19" cy="21" r="1"/><path d="M2.05 2.05h2l2.66 12.42a2 2 0 0 0 2 1.58h9.78a2 2 0 0 0 1.95-1.57l1.65-7.43H5.12"/>',
    "check": '<path d="M20 6 9 17l-5-5"/>',
}

CSS = r"""
<style>
@import url('https://cdn.jsdelivr.net/gh/orioncactus/pretendard@v1.3.9/dist/web/static/pretendard.min.css');
@import url('https://fonts.googleapis.com/css2?family=Jua&display=swap');
:root{--g:#0E4A38;--g2:#17694F;--gd:#07261C;--ink:#13241D;--mut:#5A6D63;--bg:#F4F7F4;--line:#DFE8E1;--mint:#E6F1EA;
--sky:#9CCBEA;--pink:#F3A6B8;--sun:#F7B885;--sunset:linear-gradient(100deg,#9CCBEA 0%,#F3A6B8 52%,#F7B885 100%);
--sh:0 1px 2px rgba(7,38,28,.05),0 22px 44px -26px rgba(7,38,28,.38);--pad:max(24px,calc(50vw - 590px));
--ext:__EXT__}
html{scroll-behavior:smooth}
#MainMenu,[data-testid="stHeader"],[data-testid="stToolbar"],[data-testid="stDecoration"],[data-testid="stStatusWidget"],[data-testid="stHeaderActionElements"],footer{display:none!important}
.stApp{background:var(--bg);overflow-x:hidden;color:var(--ink)}
.stApp,.stApp :is(p,div,span,label,input,button,li,a,small,b,dt,dd,textarea,h1,h2,h3,h4,em):not([data-testid="stIconMaterial"]):not(.material-icons){font-family:'Pretendard','Apple SD Gothic Neo','Malgun Gothic',sans-serif}
.stApp [data-testid="stMarkdownContainer"] :is(h1,h2,h3,h4){padding:0;margin:0;line-height:1.25;font-weight:800;letter-spacing:-.025em;color:var(--ink)}
.stApp [data-testid="stMarkdownContainer"] p{margin:0}
:focus-visible{outline:3px solid rgba(23,105,79,.5)!important;outline-offset:2px}
.block-container,.stMainBlockContainer{max-width:1180px!important;padding:0 24px 64px!important}
.block-container>[data-testid="stVerticalBlock"],.stMainBlockContainer>[data-testid="stVerticalBlock"]{gap:0!important}

/* ── 상단 내비게이션 (탭) ── */
.st-key-topnav{position:fixed!important;top:0;left:0;right:0;z-index:1000;height:68px;display:flex!important;flex-direction:row!important;
align-items:center;justify-content:space-between;gap:16px!important;padding:0 var(--pad)!important;box-sizing:border-box;
background:rgba(255,255,255,.88);backdrop-filter:saturate(1.5) blur(16px);-webkit-backdrop-filter:saturate(1.5) blur(16px);border-bottom:1px solid var(--line)}
.st-key-topnav:after{content:"";position:absolute;left:0;right:0;bottom:-1px;height:3px;background:var(--sunset)}
.st-key-topnav>*{width:auto!important;min-width:0;flex:0 0 auto}
.brand{display:flex;align-items:center;gap:12px;color:var(--ink)}
.logo{position:relative;width:42px;height:42px;border-radius:14px;background:var(--g);color:#fff;display:grid;place-items:center;font-family:'Jua','Pretendard'!important;font-size:22px}
.logo:after{content:"";position:absolute;right:-3px;bottom:-3px;width:13px;height:13px;border-radius:50%;background:var(--sun);border:2px solid #fff}
.stApp .brand b{display:block;font-family:'Jua','Pretendard'!important;font-size:20px;line-height:1.1;font-weight:400}
.brand small{display:block;color:var(--mut);font-weight:700;font-size:11px;letter-spacing:.12em}
.st-key-topnav [role="radiogroup"]{gap:4px!important;flex-wrap:nowrap!important;background:var(--mint);padding:4px;border-radius:99px}
.st-key-topnav label[data-baseweb="radio"]{border:0!important;background:transparent!important;padding:9px 22px!important;border-radius:99px!important}
.st-key-topnav label[data-baseweb="radio"]:has(input:checked){background:var(--g)!important;box-shadow:0 6px 14px -8px rgba(14,74,56,.8)}
.st-key-topnav label[data-baseweb="radio"] p{font-size:15px;white-space:nowrap}

/* ── 라디오를 알약/세그먼트 버튼처럼 ── */
[data-testid="stRadio"] [role="radiogroup"]{gap:8px;flex-wrap:wrap}
[data-testid="stRadio"] label[data-baseweb="radio"]{margin:0;padding:11px 18px;border-radius:14px;border:1.5px solid var(--line);background:#fff;cursor:pointer;
justify-content:center;transition:background .15s,border-color .15s}
[data-testid="stRadio"] label[data-baseweb="radio"]>div:not(:has(p)){display:none!important}
[data-testid="stRadio"] label[data-baseweb="radio"]:hover{border-color:var(--g2)}
[data-testid="stRadio"] label[data-baseweb="radio"]:has(input:checked){background:var(--g);border-color:var(--g)}
[data-testid="stRadio"] label[data-baseweb="radio"] p{font-weight:800;font-size:15px;color:var(--ink)}
[data-testid="stRadio"] label[data-baseweb="radio"]:has(input:checked) p{color:#fff}
.st-key-kind [role="radiogroup"],.st-key-d_cat [role="radiogroup"]{display:grid!important;grid-template-columns:repeat(3,1fr)}
.st-key-d_cat label[data-baseweb="radio"]{padding:12px 6px}

/* ── 페이지 상단 띠 / 공통 제목 ── */
.pagehead{width:100vw;margin-left:calc(50% - 50vw);box-sizing:border-box;padding:calc(68px + 52px) var(--pad) 56px;position:relative;overflow:hidden;
background:radial-gradient(55% 140% at 88% 130%,rgba(247,184,133,.75),transparent 62%),radial-gradient(45% 120% at 70% 140%,rgba(243,166,184,.6),transparent 62%),
radial-gradient(40% 100% at 100% -10%,rgba(156,203,234,.45),transparent 60%),linear-gradient(120deg,#07261C,#0E4A38 70%)}
.stApp .pagehead h1{font-size:clamp(34px,5vw,48px);color:#fff!important}
.stApp .pagehead p{margin:12px 0 0;color:rgba(255,255,255,.82);font-size:17px;line-height:1.7;max-width:560px}
.sp{height:36px}
.sec{padding:104px 0 0;scroll-margin-top:84px}
.stApp .sec h2{font-size:clamp(30px,4vw,44px);margin:0 0 14px}
.stApp p.lead{color:var(--mut);font-size:17px;line-height:1.85;max-width:560px;margin:0}
svg.i{width:22px;height:22px;fill:none;stroke:currentColor;stroke-width:1.9;stroke-linecap:round;stroke-linejoin:round;flex:none}

/* ── 홈: 히어로 ── */
.st-key-hero{width:100vw;margin-left:calc(50% - 50vw);box-sizing:border-box;min-height:82vh;padding:150px var(--pad) 118px!important;display:flex!important;flex-direction:column;justify-content:center;gap:0!important;
background:linear-gradient(95deg,rgba(5,30,22,.93) 0%,rgba(5,30,22,.6) 46%,rgba(5,30,22,.08) 100%),linear-gradient(0deg,rgba(243,166,184,.32),rgba(247,184,133,0) 45%),var(--ext) center/cover}
.badge{display:inline-block;padding:9px 18px;border-radius:99px;border:1px solid rgba(255,255,255,.35);background:rgba(255,255,255,.1);color:#fff;font-weight:700;font-size:14px;backdrop-filter:blur(6px)}
.stApp .hero-copy h1{font-family:'Jua','Pretendard'!important;font-weight:400;color:#fff;font-size:clamp(46px,7.4vw,92px);line-height:1.12;margin:26px 0 22px;letter-spacing:-.01em;animation:rise .8s cubic-bezier(.2,.7,.2,1) both}
.stApp .hero-copy p{color:rgba(255,255,255,.9);font-size:clamp(16px,1.7vw,19px);line-height:1.8;max-width:540px;margin:0 0 36px}
.st-key-hero [data-testid="stHorizontalBlock"]{max-width:540px;gap:12px}
@keyframes rise{from{opacity:0;transform:translateY(22px)}}
.infobar{position:relative;z-index:2;margin:-52px 0 0;display:grid;grid-template-columns:repeat(3,1fr);background:#fff;border:1px solid var(--line);border-radius:26px;box-shadow:var(--sh)}
.infobar>div{display:flex;gap:14px;align-items:center;padding:22px 26px;border-right:1px solid var(--line)}.infobar>div:last-child{border:0}
.infobar .ic{width:46px;height:46px;border-radius:15px;background:var(--sunset);color:var(--g);display:grid;place-items:center;flex:none}
.infobar small{display:block;color:var(--mut);font-size:13px;font-weight:600}.infobar b{font-size:16px;font-weight:800}

/* ── 홈: 정보 카드/서비스/내부/노선 ── */
.info{background:#fff;border:1px solid var(--line);border-radius:28px;padding:30px;height:100%;box-sizing:border-box;box-shadow:var(--sh)}
.stApp .info h3{font-size:23px;margin:0 0 18px}
.row{padding:14px 0;border-top:1px solid var(--line)}.row small{display:block;color:var(--mut);font-weight:700;margin-bottom:4px}.row span{font-weight:700;line-height:1.6}
.row a{font-weight:800;color:var(--g)!important}
.chips{display:flex;flex-wrap:wrap;gap:8px;margin-top:8px}
.chip{border:1px solid var(--line);background:#fff;border-radius:99px;padding:7px 14px;font-weight:700;font-size:14px;color:#33463d}
.st-key-mapbox{border-radius:28px;overflow:hidden;border:1px solid var(--line);box-shadow:var(--sh)}
.st-key-mapbox iframe{display:block;width:100%;border:0}
.split{display:grid;grid-template-columns:1.2fr 1fr;gap:56px;align-items:center}
.photo{aspect-ratio:3/2;border-radius:32px;background:var(--ext) center/cover;box-shadow:0 30px 60px -28px rgba(7,38,28,.55)}
.photo-wrap{position:relative}.photo-wrap:before{content:"";position:absolute;inset:24px -20px -20px 24px;border-radius:36px;background:var(--sunset);opacity:.55}.photo-wrap .photo{position:relative}
.feat{list-style:none;padding:0;margin:28px 0 0;display:grid;gap:14px}
.feat li{display:flex;gap:14px;align-items:flex-start;line-height:1.6}
.feat i{width:46px;height:46px;border-radius:15px;background:var(--mint);color:var(--g);display:grid;place-items:center;flex:none}
.feat b{display:block}.feat span{color:var(--mut);font-size:15px}
.floors{display:grid;grid-template-columns:1fr 1fr;gap:24px;margin-top:36px}
.floor{background:#fff;border:1px solid var(--line);border-radius:28px;overflow:hidden;box-shadow:var(--sh)}
.floor .pic{position:relative;background:linear-gradient(135deg,#DCEBE2,#F6E3E6)}.floor img,.floor .ph{width:100%;display:block;aspect-ratio:4/3;object-fit:cover}
.floor .lv{position:absolute;left:16px;top:16px;background:var(--g);color:#fff;font-weight:800;font-size:14px;padding:6px 13px;border-radius:99px}
.floor .tx{padding:24px 26px 28px}.stApp .floor h3{font-size:22px;margin:0 0 8px}.floor p{color:var(--mut);line-height:1.75;font-size:15.5px}
.rgroup{margin:44px 0 14px;display:flex;align-items:baseline;gap:12px}.stApp .rgroup h3{font-size:24px}.rgroup span{color:var(--mut);font-size:14px}
.rgrid{display:grid;grid-template-columns:repeat(auto-fill,minmax(210px,1fr));gap:12px}
.rc{background:#fff;border:1px solid var(--line);border-radius:18px;padding:18px;display:flex;flex-direction:column;gap:9px}
.rc b{font-size:17px;line-height:1.35}.rc small{color:var(--mut)}
.k{align-self:flex-start;font-size:12.5px;font-weight:800;padding:4px 11px;border-radius:99px}
.k.w{background:#E1F1E8;color:#146344}.k.e{background:#FBE4EA;color:#B03A5B}.k.v{background:#FFE8D2;color:#A5571B}.k.s{background:#DDEBFA;color:#1F5C99}
.st-key-cta{margin-top:104px;border-radius:32px;padding:52px 48px!important;gap:0!important;position:relative;overflow:hidden;
background:radial-gradient(60% 140% at 100% 120%,rgba(247,184,133,.9),transparent 60%),radial-gradient(50% 120% at 80% 130%,rgba(243,166,184,.8),transparent 60%),
radial-gradient(45% 100% at 100% -20%,rgba(156,203,234,.7),transparent 60%),linear-gradient(120deg,#07261C,#0E4A38 75%)}
.stApp .st-key-cta h2{color:#fff;font-size:clamp(28px,3.6vw,40px);margin:0 0 12px}
.stApp .st-key-cta p{color:rgba(255,255,255,.85);font-size:17px;line-height:1.7;margin:0 0 28px;max-width:480px}
.st-key-cta [data-testid="stHorizontalBlock"]{max-width:520px;gap:12px}
.notice{width:100vw;margin:96px 0 0 calc(50% - 50vw);background:var(--gd);color:#fff}
.notice-in{max-width:1180px;margin:0 auto;padding:60px 24px 36px;display:grid;grid-template-columns:1.1fr 1fr 1fr;gap:40px;box-sizing:border-box}
.stApp .notice h2{font-size:30px;color:#fff}.stApp .notice h4{margin:0 0 10px;font-size:17px;color:#fff}.notice p{color:#9FB5AA;line-height:1.8;font-size:15px}
.foot{max-width:1180px;margin:0 auto;padding:22px 24px 34px;border-top:1px solid rgba(255,255,255,.1);color:#7F988B;font-size:13.5px;display:flex;justify-content:space-between;gap:16px;flex-wrap:wrap;box-sizing:border-box}

/* ── 폼 카드 ── */
:is(.st-key-resv,.st-key-opt,.st-key-cartbox){background:#fff;border:1px solid var(--line);border-radius:30px;padding:32px 32px 34px!important;box-shadow:var(--sh);gap:.85rem!important}
.form-h{display:flex;gap:14px;align-items:center;margin-bottom:6px}.form-h .ico{width:50px;height:50px;border-radius:16px;background:var(--mint);color:var(--g);display:grid;place-items:center;flex:none}
.stApp .form-h h3{font-size:24px}.stApp .form-h p{margin:3px 0 0;color:var(--mut);font-size:14.5px}
.fs{display:flex;align-items:center;gap:10px;margin:14px 0 -2px;font-weight:800;font-size:15px}
.fs em{font-style:normal;width:26px;height:26px;border-radius:50%;background:var(--g);color:#fff;display:grid;place-items:center;font-size:13px}
[data-testid="stWidgetLabel"] p{font-weight:700;font-size:14px;color:var(--ink)}
[data-baseweb="base-input"]{background:transparent!important}
.stTextInput [data-baseweb="input"],.stNumberInput [data-baseweb="input"],.stDateInput [data-baseweb="input"],[data-baseweb="select"]>div{border-radius:13px!important;border:1.5px solid var(--line)!important;background:#fff!important;min-height:48px}
.stTextInput [data-baseweb="input"]:focus-within,.stNumberInput [data-baseweb="input"]:focus-within,.stDateInput [data-baseweb="input"]:focus-within,[data-baseweb="select"]>div:focus-within{border-color:var(--g2)!important;box-shadow:0 0 0 4px rgba(23,105,79,.13)!important}
.stTextInput input,.stNumberInput input,.stDateInput input{font-size:15.5px;min-height:46px}
[data-testid="stAlert"]{border-radius:14px}
[data-testid="stCaptionContainer"]{color:var(--mut)}

/* ── 버튼 ── */
.stButton,.stButton>button{width:100%}
.stButton>button{min-height:50px;border-radius:14px;font-weight:800;font-size:16px;transition:transform .12s,background .15s,box-shadow .15s}
.stButton>button:active{transform:scale(.985)}
.stButton>button:is([kind="primary"],[data-testid="stBaseButton-primary"]){background:var(--g);color:#fff;border:0;box-shadow:0 12px 22px -14px rgba(14,74,56,.85)}
.stButton>button:is([kind="primary"],[data-testid="stBaseButton-primary"]):hover{background:var(--g2);color:#fff}
.stButton>button:is([kind="secondary"],[data-testid="stBaseButton-secondary"]){background:#fff;color:var(--g);border:1.5px solid var(--line)}
.stButton>button:is([kind="secondary"],[data-testid="stBaseButton-secondary"]):hover{border-color:var(--g2);color:var(--g2);background:#fff}
.stButton>button p{font-weight:800}
.st-key-hb_bus button{background:#fff!important;color:var(--g)!important;border:0!important;min-height:56px;box-shadow:none!important}.st-key-hb_bus button:hover{background:var(--sun)!important}
.st-key-hb_drink button,.st-key-cb_drink button{background:rgba(255,255,255,.12)!important;color:#fff!important;border:1px solid rgba(255,255,255,.55)!important;min-height:56px;backdrop-filter:blur(6px)}
.st-key-hb_drink button:hover,.st-key-cb_drink button:hover{background:rgba(255,255,255,.24)!important}
.st-key-cb_bus button{background:#fff!important;color:var(--g)!important;border:0!important;min-height:56px;box-shadow:none!important}.st-key-cb_bus button:hover{background:var(--sun)!important}
.st-key-hb_bus p,.st-key-hb_drink p,.st-key-cb_bus p,.st-key-cb_drink p{font-size:16px}

/* ── 승차권 / 영수증 카드 ── */
.pass{background:#fff;border:1px solid var(--line);border-radius:28px;box-shadow:var(--sh);overflow:hidden;position:relative}
.pass-top{position:relative;overflow:hidden;padding:26px 28px 24px;background:linear-gradient(120deg,#07261C,#0E4A38 75%);color:#fff}
.pass-top:before{content:"";position:absolute;inset:0;background:radial-gradient(55% 130% at 100% 130%,rgba(247,184,133,.7),transparent 62%),radial-gradient(45% 110% at 78% 140%,rgba(243,166,184,.55),transparent 62%)}
.pass-top>*{position:relative}
.pass-lab{display:flex;align-items:center;gap:8px;font-weight:700;font-size:14px;opacity:.88}
.pass-lab svg.i{width:18px;height:18px;stroke-width:2.6;color:var(--sun)}
.stApp .pass-title{margin:8px 0 4px;font-size:22px;font-weight:800;line-height:1.35;letter-spacing:-.02em}
.stApp .pass.ok .pass-title{font-size:32px;letter-spacing:.02em;font-variant-numeric:tabular-nums;font-family:'Jua','Pretendard'!important;font-weight:400;color:var(--sun)}
.pass-sub{font-size:15px;opacity:.9}
.pass-cut{position:relative;height:0;margin:0 20px;border-top:2px dashed #CFDCD3}
.pass-cut:before,.pass-cut:after{content:"";position:absolute;top:-13px;width:24px;height:24px;border-radius:50%;background:var(--bg)}.pass-cut:before{left:-32px}.pass-cut:after{right:-32px}
.pass-body{padding:24px 28px 26px}
.pass dl{display:grid;grid-template-columns:auto 1fr;gap:10px 20px;margin:18px 0 0}.pass dt{color:var(--mut);font-size:14px}.pass dd{margin:0;font-weight:700;line-height:1.5}
.pass-note{margin-top:18px;padding-top:16px;border-top:1px solid var(--line);color:var(--mut);font-size:13.5px;line-height:1.7}
.tl{list-style:none;margin:0;padding:0}
.tl li{position:relative;display:flex;align-items:center;gap:14px;padding:0 0 18px 0;color:#97A79E;font-weight:600}.tl li:last-child{padding-bottom:0}
.tl li:before{content:"";position:absolute;left:6px;top:16px;bottom:-4px;width:2px;background:#DCE6DF}.tl li:last-child:before{display:none}
.tl li.on:before{background:var(--g2)}
.tl i{width:14px;height:14px;border-radius:50%;background:#fff;border:2.5px solid #C9D6CE;flex:none;position:relative;z-index:1}
.tl li.on{color:var(--ink)}.tl li.on i{border-color:var(--g2);background:var(--g2)}
.tl em{font-style:normal;margin-left:auto;font-size:12.5px;font-weight:800;color:var(--g);background:var(--mint);padding:3px 10px;border-radius:99px}
.warnchip{display:inline-block;margin-top:12px;padding:5px 12px;border-radius:99px;background:#FFE8D2;color:#A5571B;font-weight:800;font-size:13px}
.notes{margin:18px 4px 0;padding:0;list-style:none;display:grid;gap:8px;color:var(--mut);font-size:14px;line-height:1.6}
.notes li{display:flex;gap:10px}.notes li:before{content:"";width:6px;height:6px;border-radius:50%;background:var(--sun);margin-top:9px;flex:none}
.ol{display:grid;gap:10px;margin:0}.ol div{display:flex;justify-content:space-between;gap:14px;font-weight:700}.ol small{display:block;color:var(--mut);font-weight:500}
.tot{display:flex;justify-content:space-between;align-items:baseline;margin-top:14px;padding-top:14px;border-top:1px dashed #CFDCD3;font-weight:800}.tot b{font-size:22px;color:var(--g)}

/* ── 음료 주문 ── */
.cup{width:58px;height:72px;flex:none}
:is([class*="st-key-mi_"],[class*="st-key-mis_"]){background:#fff;border:1.5px solid var(--line);border-radius:22px;padding:16px 16px 16px!important;gap:.6rem!important;transition:border-color .15s,box-shadow .15s}
[class*="st-key-mis_"]{border-color:var(--g2);box-shadow:0 0 0 4px rgba(23,105,79,.13)}
.mc{display:flex;justify-content:space-between;align-items:flex-start;gap:8px}
.mc .tg{font-size:12px;font-weight:800;color:#B03A5B;background:#FBE4EA;border-radius:99px;padding:4px 10px}
.stApp .mc-n{font-weight:800;font-size:17px;margin-top:2px}.stApp .mc-d{color:var(--mut);font-size:13px;line-height:1.55;min-height:40px;margin:3px 0 8px}
.mc-p{font-weight:800;color:var(--g);font-size:16px}.mc-i{display:inline-block;margin-left:6px;font-size:12px;color:var(--mut);font-weight:700}
.st-key-menu [data-testid="stHorizontalBlock"]{gap:14px}
.st-key-menu .stButton>button{min-height:42px;font-size:14.5px;border-radius:12px}
.st-key-opt{margin-top:22px}
.opt-h{display:flex;gap:18px;align-items:center}.opt-h .cup{width:72px;height:90px}.opt-h>div{flex:1}
.stApp .opt-h h3{font-size:24px}.stApp .opt-h p{color:var(--mut);margin-top:4px;font-size:14.5px;line-height:1.55}.opt-h .pr{font-size:22px;font-weight:800;color:var(--g);white-space:nowrap}
.st-key-cartlines [data-testid="stHorizontalBlock"]{flex-wrap:nowrap!important;gap:10px!important;align-items:center}
.st-key-cartlines :is([data-testid="stColumn"],[data-testid="column"]){min-width:0!important}
.st-key-cartlines :is([data-testid="stColumn"],[data-testid="column"]):last-child{flex:0 0 66px!important;width:66px!important}
.st-key-cartlines .stButton>button{min-height:38px;font-size:13.5px;border-radius:10px;color:#9A3A52;border-color:#F1D3DB}
.cl{background:var(--mint);border-radius:16px;padding:12px 16px;display:flex;justify-content:space-between;gap:12px;font-weight:700;line-height:1.45}
.cl small{display:block;color:var(--mut);font-weight:600}
.empty{border:1.5px dashed #CBD9D0;border-radius:18px;padding:26px 18px;text-align:center;color:var(--mut);line-height:1.7;font-size:15px}
.ch{display:flex;align-items:center;gap:10px}.ch .ico{width:44px;height:44px;border-radius:14px;background:var(--mint);color:var(--g);display:grid;place-items:center}
.stApp .ch h3{font-size:21px}.ch .n{margin-left:auto;background:var(--g);color:#fff;font-weight:800;font-size:13px;border-radius:99px;padding:4px 11px}

/* ── 모바일 ── */
@media(max-width:860px){.split,.floors,.notice-in,.infobar{grid-template-columns:1fr}.split{gap:34px}.infobar>div{border-right:0;border-bottom:1px solid var(--line)}.infobar>div:last-child{border:0}
:is(.st-key-resv,.st-key-opt,.st-key-cartbox){padding:22px 20px 24px!important}.st-key-cta{padding:36px 24px!important}.sec{padding-top:84px}}
@media(max-width:760px){
.st-key-topnav{height:58px;padding:0 16px!important;background:rgba(255,255,255,.97);backdrop-filter:none;-webkit-backdrop-filter:none}
.logo{width:38px;height:38px;font-size:20px}
.st-key-topnav [data-testid="stRadio"]{position:fixed;left:12px;right:12px;bottom:max(12px,env(safe-area-inset-bottom));z-index:1001;width:auto!important}
.st-key-topnav [role="radiogroup"]{display:grid!important;grid-template-columns:repeat(3,1fr);background:rgba(255,255,255,.96);border:1px solid var(--line);padding:6px;
box-shadow:0 18px 40px -12px rgba(7,38,28,.5);backdrop-filter:blur(14px)}
.st-key-topnav label[data-baseweb="radio"]{padding:13px 0!important}
.pagehead{padding-top:calc(58px + 36px);padding-bottom:40px}.pagehead p{font-size:15.5px}
.st-key-hero{min-height:auto;padding-top:112px!important;padding-bottom:96px!important}
.block-container,.stMainBlockContainer{padding-bottom:110px!important}
.st-key-mapbox iframe{height:320px}
.pass-top{padding:22px 22px 20px}.pass-body{padding:20px 22px 22px}.stApp .pass.ok .pass-title{font-size:28px}
}
@media(max-width:640px){
.st-key-menu [data-testid="stHorizontalBlock"]{flex-wrap:wrap!important;gap:10px!important}
.st-key-menu :is([data-testid="stColumn"],[data-testid="column"]){flex:0 0 calc(50% - 5px)!important;min-width:calc(50% - 5px)!important;width:calc(50% - 5px)!important}
[class*="st-key-mi_"],[class*="st-key-mis_"]{padding:12px!important}.mc .cup{width:46px;height:58px}.mc-d{font-size:12.5px;min-height:58px}
.opt-h{flex-wrap:wrap}.opt-h .cup{width:56px;height:70px}
}
@media(prefers-reduced-motion:reduce){html{scroll-behavior:auto}.stApp .hero-copy h1{animation:none}.stButton>button{transition:none}}
</style>
"""


# ───────────────────────── 상태·이동 ─────────────────────────
def go(page: str):
    st.session_state["page"] = page


def pick(name: str):
    st.session_state["sel"] = name


def add_to_cart(name: str, temp: str, shot: bool, qty: int):
    cart = st.session_state.setdefault("cart", [])
    for it in cart:
        if (it["n"], it["t"], it["s"]) == (name, temp, shot):
            it["q"] = min(it["q"] + qty, 20)
            break
    else:
        cart.append(dict(n=name, t=temp, s=shot, q=qty))
    st.toast(f"{name}을(를) 담았어요", icon="✅")


def remove_item(i: int):
    cart = st.session_state.get("cart", [])
    if 0 <= i < len(cart):
        del cart[i]


def clear_cart():
    st.session_state["cart"] = []


def clear_key(key: str):
    st.session_state.pop(key, None)


def unit_price(it) -> int:
    return ALL[it["n"]]["price"] + (SHOT_PRICE if it["s"] else 0)


def cart_total(cart) -> int:
    return sum(unit_price(i) * i["q"] for i in cart)


# ───────────────────────── 조각 컴포넌트 ─────────────────────────
def cup(c1: str, c2: str, uid: str) -> str:
    body = "M17 32 L23.5 90 Q24 94 28 94 L52 94 Q56 94 56.5 90 L63 32 Z"
    return (f'<svg class="cup" viewBox="0 0 80 100" aria-hidden="true"><defs>'
            f'<linearGradient id="lg{uid}" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="{c1}"/><stop offset="1" stop-color="{c2}"/></linearGradient>'
            f'<clipPath id="cp{uid}"><path d="{body}"/></clipPath></defs>'
            '<path d="M46 3 L41 24" stroke="#F7B885" stroke-width="4.5" stroke-linecap="round"/>'
            f'<path d="{body}" fill="#fff" fill-opacity=".6" stroke="#C5D6CB" stroke-width="2" stroke-linejoin="round"/>'
            f'<g clip-path="url(#cp{uid})"><rect x="10" y="44" width="60" height="54" fill="url(#lg{uid})"/>'
            '<rect x="28" y="52" width="13" height="13" rx="3.5" fill="#fff" fill-opacity=".45" transform="rotate(-14 34 58)"/>'
            '<rect x="42" y="66" width="12" height="12" rx="3.5" fill="#fff" fill-opacity=".4" transform="rotate(12 48 72)"/></g>'
            '<rect x="13" y="24" width="54" height="9" rx="4.5" fill="#0E4A38"/><rect x="19" y="18" width="42" height="8" rx="4" fill="#17694F"/></svg>')


def pagehead(title: str, sub: str):
    md(f'<div class="pagehead"><h1>{title}</h1><p>{sub}</p></div><div class="sp"></div>')


def pass_card(label, title, sub, body, ok=False, note=""):
    mark = "[[check]]" if ok else "[[ticket]]"
    note_html = f'<div class="pass-note">{note}</div>' if note else ""
    md(f'<div class="pass{" ok" if ok else ""}"><div class="pass-top"><div class="pass-lab">{mark}{label}</div>'
       f'<div class="pass-title">{title}</div><div class="pass-sub">{sub}</div></div><div class="pass-cut"></div>'
       f'<div class="pass-body">{body}{note_html}</div></div>')


def timeline(stops, board, alight) -> str:
    bi, ai = stops.index(board), stops.index(alight)
    li = []
    for i, s in enumerate(stops):
        mark = "승차" if i == bi else "하차" if i == ai else ""
        li.append(f'<li class="{"on" if bi <= i <= ai else ""}"><i></i><span>{esc(s)}</span>{f"<em>{mark}</em>" if mark else ""}</li>')
    return f'<ul class="tl">{"".join(li)}</ul>'


# ───────────────────────── 공통 레이아웃 ─────────────────────────
def topnav():
    with st.container(key="topnav"):
        md('<div class="brand"><span class="logo">뀨</span><span><b>뀨 스탑</b><small>KYU STOP</small></span></div>')
        st.radio("메뉴", PAGES, horizontal=True, key="page", label_visibility="collapsed")


def footer():
    md("""<div class="notice"><div class="notice-in"><h2>탑승 전 확인해 주세요</h2>
    <div><h4>탑승 시간 준수</h4><p>출발 10분 전까지 정류장에 도착해 주세요. 정시 출발을 원칙으로 합니다.</p></div>
    <div><h4>예약 취소 문의</h4><p>변경 및 취소는 학생복지팀으로 문의해 주세요. 무단 미탑승이 반복되면 이용이 제한될 수 있습니다.</p></div></div>
    <div class="foot"><span>건양대학교 뀨 스탑 버스 예약 서비스</span><span>가격·시간표·이미지는 이해를 돕기 위한 예시입니다.</span></div></div>""")


# ───────────────────────── 홈 ─────────────────────────
def home_hero():
    with st.container(key="hero"):
        md("""<div class="hero-copy"><span class="badge">건양대학교 버스 대기 라운지 &amp; 카페</span>
        <h1>기다림도<br>설렘이 될 수 있도록</h1>
        <p>버스를 기다리는 시간, 따뜻한 라운지에서 음료 한 잔과 함께 쉬어 가세요. 좌석과 음료를 미리 예약해 두면 도착해서 바로 쉴 수 있어요.</p></div>""")
        c1, c2 = st.columns(2)
        c1.button("버스 예약하기", key="hb_bus", type="primary", on_click=go, args=("버스 예약",))
        c2.button("음료 주문하기", key="hb_drink", on_click=go, args=("음료 주문",))
    md(f"""<div class="infobar">
    <div><span class="ic">[[pin]]</span><span><small>위치</small><b>대전캠퍼스 버스 승차장 옆</b></span></div>
    <div><span class="ic">[[clock]]</span><span><small>운영 시간</small><b>{esc(OPEN_HOURS)}</b></span></div>
    <div><span class="ic">[[coffee]]</span><span><small>음료</small><b>사전 주문 가능</b></span></div></div>""")


def home_location():
    md("""<div id="location" class="sec"><h2>뀨 스탑은 여기에 있어요</h2>
    <p class="lead">버스 승차장 바로 옆에서 노란 불빛이 켜진 초록색 2층 건물을 찾아보세요.</p></div><div class="sp"></div>""")
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
        md(f"""<div class="info"><h3>{esc(STOP_NAME)}</h3>
        <div class="row"><small>주소</small><span>{esc(STOP_ADDRESS)}</span></div>
        <div class="row"><small>운영 시간</small><span>{esc(OPEN_HOURS)}</span></div>
        <div class="row"><small>정차 노선</small><div class="chips"><span class="chip">대전 방면</span><span class="chip">논산 방면</span><span class="chip">순환</span></div></div>
        <div class="row"><a href="https://www.openstreetmap.org/?mlat={STOP_LAT}&amp;mlon={STOP_LON}#map=18/{STOP_LAT}/{STOP_LON}" target="_blank" rel="noopener">큰 지도에서 보기</a></div></div>""")


def home_service():
    md("""<div id="service" class="sec"><div class="split"><div class="photo-wrap"><div class="photo" role="img" aria-label="뀨 스탑 외관"></div></div>
    <div><h2>버스를 기다리는 시간이 쉼이 되는 곳</h2>
    <p class="lead">뀨 스탑은 2층 통유리 라운지와 1층 카페로 이루어진 버스 대기 공간이에요. 비 오는 날에도, 추운 아침에도 따뜻하게 기다릴 수 있어요.</p>
    <ul class="feat"><li><i>[[bus]]</i><div><b>버스 정류장과 바로 연결</b><span>승차장 옆이라 출발 직전까지 편하게 쉴 수 있어요.</span></div></li>
    <li><i>[[coffee]]</i><div><b>카페 음료 사전 주문</b><span>도착하기 전에 미리 주문해 두면 바로 받을 수 있어요.</span></div></li>
    <li><i>[[sun]]</i><div><b>노을이 보이는 통유리 라운지</b><span>2층에서 하늘을 보며 기다릴 수 있어요.</span></div></li></ul></div></div></div>""")


def home_inside():
    def pic(name, alt, lv):
        b = img(name, 1100)
        tag = f'<img src="data:image/jpeg;base64,{b}" alt="{alt}">' if b else f'<div class="ph" role="img" aria-label="{alt}"></div>'
        return f'<div class="pic">{tag}<span class="lv">{lv}</span></div>'
    md(f"""<div id="inside" class="sec"><h2>1층은 카페, 2층은 라운지</h2>
    <p class="lead">입구로 들어오면 1층 카운터와 대기 좌석이 있고, 계단으로 올라가면 통유리 라운지가 나와요.</p>
    <div class="floors">
    <article class="floor">{pic("floor1.jpg", "1층 내부 구성", "1층")}<div class="tx"><h3>카페와 대기 공간</h3>
    <p>입구로 들어오면 왼쪽에 2층으로 가는 계단이 있어요. 가운데 카운터에서 메뉴판을 보고 주문하고, 학생들이 대기할 수 있는 좌석과 테이크아웃 코너를 이용할 수 있어요.</p></div></article>
    <article class="floor">{pic("floor2.jpg", "2층 통유리 라운지", "2층")}<div class="tx"><h3>통유리 라운지</h3>
    <p>천장까지 이어진 통유리창으로 하늘이 보여요. 창가 긴 테이블과 둥근 테이블이 있어 혼자 쉬기도, 친구와 이야기하기도 좋아요.</p></div></article>
    </div></div>""")


def home_routes():
    groups = [("주중", "w", "평일에 운행하는 노선"), ("주말", "e", "주말에 운행하는 노선"),
              ("방학", "v", "방학 중에 운행하는 노선"), ("셔틀", "s", "캠퍼스 사이를 오가는 셔틀")]
    out = ['<div id="routes" class="sec"><h2>어디서든 학교까지</h2>'
           '<p class="lead">지역별 통학 노선과 캠퍼스 셔틀이에요. 버스 예약 화면에서 등교·하교·순환을 골라 예약하세요.</p>']
    for k, c, desc in groups:
        if k == "셔틀":
            cards = [("논산 → 대전", "순환"), ("대전 → 논산", "순환")]
        else:
            cards = [(n, "등교 · 하교") for n, kk in REGIONAL if kk == k]
            if k == "주중":
                cards.append(("대전 순환", "순환"))
        out.append(f'<div class="rgroup"><h3>{k}</h3><span>{desc}</span></div><div class="rgrid">')
        out += [f'<div class="rc"><span class="k {c}">{k}</span><b>{esc(n)}</b><small>{s}</small></div>' for n, s in cards]
        out.append("</div>")
    out.append("</div>")
    md("".join(out))


def home_cta():
    with st.container(key="cta"):
        md("""<h2>오늘 탈 버스, 오늘 마실 음료</h2>
        <p>좌석은 이름만으로 예약하고, 음료는 도착 시간에 맞춰 미리 주문해 두세요.</p>""")
        c1, c2 = st.columns(2)
        c1.button("버스 예약하기", key="cb_bus", type="primary", on_click=go, args=("버스 예약",))
        c2.button("음료 주문하기", key="cb_drink", on_click=go, args=("음료 주문",))


def page_home():
    home_hero()
    home_location()
    home_service()
    home_inside()
    home_routes()
    home_cta()


# ───────────────────────── 버스 예약 ─────────────────────────
def page_bus():
    pagehead("버스 예약", "건양대 통학버스 좌석을 미리 잡아 두세요. 이름만 입력하면 돼요.")
    left, right = st.columns([1.2, 1], gap="large")
    with left:
        with st.container(key="resv"):
            md("""<div class="form-h"><span class="ico">[[ticket]]</span><div><h3>예약 정보 입력</h3><p>구분과 노선을 고르고 이름을 입력해 주세요.</p></div></div>""")
            md('<div class="fs"><em>1</em>운행 선택</div>')
            kind = st.radio("운행 구분", list(ROUTES), horizontal=True, key="kind", label_visibility="collapsed")
            route = st.selectbox("노선", list(ROUTES[kind]), key=f"route_{kind}")
            rk, stops = ROUTES[kind][route]
            md('<div class="fs"><em>2</em>탑승 정보</div>')
            c1, c2 = st.columns(2)
            time_ = c1.selectbox("출발 시간", TIMES[kind], key=f"time_{kind}")
            today = date.today()
            day = c2.date_input("탑승일", value=today, min_value=today, max_value=today + timedelta(days=30),
                                format="YYYY-MM-DD", key="b_day")
            if rk in DAYS:
                st.caption("주중 노선은 월~금에 운행해요." if rk == "주중" else "주말 노선은 금·토·일에 운행해요.")
            c3, c4 = st.columns(2)
            board = c3.selectbox("승차 정류장", stops[:-1], key=f"board_{route}")
            alight = c4.selectbox("하차 정류장", stops[stops.index(board) + 1:], key=f"alight_{route}_{board}")
            md('<div class="fs"><em>3</em>예약자</div>')
            name = st.text_input("이름", placeholder="홍길동", max_chars=20, key="b_name")
            if st.button("좌석 예약하기", type="primary", key="b_go"):
                if not name.strip():
                    st.error("이름을 입력해 주세요.")
                elif rk in DAYS and day.weekday() not in DAYS[rk]:
                    st.error(f"{route}은(는) {'월~금' if rk == '주중' else '금·토·일'}에만 운행해요. 탑승일을 바꿔 주세요.")
                else:
                    code = f"KYU-{day:%y%m%d}-{rand4()}"
                    t = dict(code=code, kind=kind, route=route, time=time_, day=f"{day:%Y-%m-%d}",
                             when=f"{day.month}월 {day.day}일 ({WEEKDAY_KO[day.weekday()]}) {time_}",
                             name=name.strip(), board=board, alight=alight, stops=stops)
                    save_csv("reservations.csv", ["예약번호", "신청시각", "구분", "노선", "출발", "탑승일", "이름", "승차", "하차"],
                             [code, datetime.now().strftime("%Y-%m-%d %H:%M"), kind, route, time_, t["day"], t["name"], board, alight])
                    st.session_state["ticket"] = t
                    st.toast("예약이 완료됐어요!", icon="✅")
    with right:                      # 왼쪽 폼이 먼저 실행되므로 방금 한 예약도 바로 보여요
        t = st.session_state.get("ticket")
        if t:
            pass_card("예약이 완료됐어요", t["code"], esc(f"{t['kind']} · {t['route']}"),
                      timeline(t["stops"], t["board"], t["alight"])
                      + f'<dl><dt>탑승</dt><dd>{esc(t["when"])}</dd><dt>이름</dt><dd>{esc(t["name"])}</dd></dl>',
                      ok=True, note="탑승할 때 학생증과 예약번호를 보여 주세요. 예약번호는 음료 주문 화면에서도 쓸 수 있어요.")
            st.button("다른 좌석 예약하기", key="b_new", on_click=clear_key, args=("ticket",))
        else:
            warn = ""
            if rk in DAYS and day.weekday() not in DAYS[rk]:
                warn = '<span class="warnchip">이 날은 운행하지 않아요. 탑승일을 바꿔 주세요.</span>'
            pass_card("승차권 미리보기", esc(route), f"{day.month}월 {day.day}일 ({WEEKDAY_KO[day.weekday()]}) · {time_}",
                      timeline(stops, board, alight)
                      + f'<dl><dt>구분</dt><dd>{esc(kind)}</dd><dt>이름</dt><dd>{esc(name.strip()) or "입력 전"}</dd></dl>{warn}')
            md("""<ul class="notes"><li>탑승일은 오늘부터 30일 뒤까지 고를 수 있어요.</li>
            <li>탑승할 때 학생증과 예약번호를 보여 주세요.</li><li>출발 10분 전까지 정류장에 도착해 주세요.</li></ul>""")


# ───────────────────────── 음료 주문 ─────────────────────────
def menu_card(i: int, m: dict, selected: bool):
    tag = f'<span class="tg">{m["tag"]}</span>' if m.get("tag") else ""
    ice = '<span class="mc-i">ICE 전용</span>' if m.get("ice_only") else ""
    with st.container(key=f"{'mis' if selected else 'mi'}_{i}"):
        md(f'<div class="mc">{cup(*m["c"], uid=f"m{i}")}{tag}</div>'
           f'<div class="mc-n">{m["name"]}</div><div class="mc-d">{m["desc"]}</div>'
           f'<div class="mc-p">{won(m["price"])}{ice}</div>')
        st.button("선택됨" if selected else "선택", key=f"pick_{i}", type="primary" if selected else "secondary",
                  on_click=pick, args=(m["name"],))


def page_drink():
    pagehead("음료 주문", "버스를 기다리는 동안 마실 음료를 미리 주문해 두세요. 도착하면 1층 카운터에서 받아요.")
    cart = st.session_state.setdefault("cart", [])
    st.session_state.setdefault("sel", ORDER[0])
    left, right = st.columns([1.5, 1], gap="large")

    with left:
        with st.container(key="menu"):
            cat = st.radio("종류", list(MENU), horizontal=True, key="d_cat", label_visibility="collapsed")
            if ALL[st.session_state["sel"]]["cat"] != cat:
                st.session_state["sel"] = MENU[cat][0]["name"]
            items = MENU[cat]
            for r in range(0, len(items), 3):
                cols = st.columns(3)
                for col, m in zip(cols, items[r:r + 3]):
                    with col:
                        menu_card(ORDER.index(m["name"]), m, m["name"] == st.session_state["sel"])
        sel = st.session_state["sel"]
        m = ALL[sel]
        with st.container(key="opt"):
            md(f'<div class="opt-h">{cup(*m["c"], uid="big")}<div><h3>{sel}</h3><p>{m["desc"]}</p></div><div class="pr">{won(m["price"])}</div></div>')
            c1, c2 = st.columns(2)
            temp_opts = ["ICE"] if m.get("ice_only") else ["ICE", "HOT"]
            temp = c1.radio("온도", temp_opts, horizontal=True, key=f"t_{sel}")
            qty = int(c2.number_input("수량", 1, 10, 1, key="qty"))
            shot = False
            if m.get("shot"):
                shot = st.checkbox(f"샷 추가 (+{won(SHOT_PRICE)})", key=f"shot_{sel}")
            if m.get("ice_only"):
                st.caption("이 메뉴는 ICE로만 만들 수 있어요.")
            unit = m["price"] + (SHOT_PRICE if shot else 0)
            st.button(f"장바구니 담기 · {won(unit * qty)}", key="add", type="primary",
                      on_click=add_to_cart, args=(sel, temp, shot, qty))
        st.caption("메뉴와 가격은 예시예요. 실제 메뉴판과 다를 수 있어요.")

    with right:
        with st.container(key="cartbox"):
            order = st.session_state.get("d_ticket")
            if order:
                rows = "".join(f'<div><span>{esc(n)}</span><span>{esc(q)}</span></div>' for n, q in order["lines"])
                pass_card("주문이 접수됐어요", order["code"], esc(f"{order['how']} · {order['when']}"),
                          f'<div class="ol">{rows}</div><div class="tot"><span>합계</span><b>{won(order["total"])}</b></div>'
                          f'<dl><dt>이름</dt><dd>{esc(order["name"])}</dd></dl>',
                          ok=True, note="1층 카운터에서 주문번호를 말씀해 주세요. 결제는 수령할 때 카운터에서 해요. (예시 문구)")
                st.button("새로 주문하기", key="d_new", on_click=clear_key, args=("d_ticket",))
            else:
                n_items = sum(c["q"] for c in cart)
                md(f'<div class="ch"><span class="ico">[[cart]]</span><h3>주문 내역</h3>{f"<span class=n>{n_items}잔</span>" if n_items else ""}</div>')
                if not cart:
                    md('<div class="empty">아직 담은 메뉴가 없어요.<br>메뉴를 고르고 장바구니에 담아 주세요.</div>')
                else:
                    with st.container(key="cartlines"):
                        for i, it in enumerate(cart):
                            a, b = st.columns([5, 1], vertical_alignment="center")
                            opt = f'{it["t"]}{" · 샷 추가" if it["s"] else ""} · {it["q"]}잔'
                            a.markdown(squash(f'<div class="cl"><span>{esc(it["n"])}<small>{opt}</small></span><span>{won(unit_price(it) * it["q"])}</span></div>'),
                                       unsafe_allow_html=True)
                            b.button("삭제", key=f"rm_{i}", on_click=remove_item, args=(i,))
                    md(f'<div class="tot"><span>합계</span><b>{won(cart_total(cart))}</b></div>')
                name = st.text_input("이름", placeholder="홍길동", max_chars=20, key="d_name")
                how = st.radio("수령 방법", PICKUP_HOW, horizontal=True, key="d_how")
                when = st.selectbox("수령 시간", PICKUP_WHEN, key="d_when")
                bus = st.text_input("버스 예약번호 (선택)", placeholder="KYU-261117-AB12", key="d_bus")
                if st.button("음료 주문하기", type="primary", key="d_order"):
                    if not cart:
                        st.error("먼저 메뉴를 담아 주세요.")
                    elif not name.strip():
                        st.error("이름을 입력해 주세요.")
                    else:
                        lines = [(it["n"], f'{it["t"]}{" · 샷 추가" if it["s"] else ""} · {it["q"]}잔') for it in cart]
                        items_txt = ", ".join(f"{n}({q})" for n, q in lines)
                        total = cart_total(cart)
                        code = f"DR-{datetime.now():%H%M}-{rand4()}"
                        save_csv("drink_orders.csv", ["주문번호", "주문시각", "이름", "메뉴", "합계", "수령방법", "수령시간", "버스예약번호"],
                                 [code, datetime.now().strftime("%Y-%m-%d %H:%M"), name.strip(), items_txt, total, how, when, bus.strip()])
                        st.session_state["d_ticket"] = dict(code=code, name=name.strip(), lines=lines, total=total, how=how, when=when)
                        st.session_state["cart"] = []
                        st.rerun()
                if cart:
                    st.button("장바구니 비우기", key="clr", on_click=clear_cart)


# ───────────────────────── 실행 ─────────────────────────
# 다른 화면에 갔다 와도 입력한 값이 사라지지 않게 유지
for _k in list(st.session_state.keys()):
    if _k in ("kind", "b_day", "b_name", "d_cat", "d_name", "d_how", "d_when", "d_bus", "qty") \
            or _k.startswith(("route_", "time_", "board_", "alight_", "t_", "shot_")):
        st.session_state[_k] = st.session_state[_k]

if "page" not in st.session_state:   # 주소 끝에 ?p=bus 또는 ?p=drink 를 붙이면 바로 그 화면이 열려요 (QR코드용)
    st.session_state["page"] = QUERY_TO_PAGE.get(st.query_params.get("p", "home"), "홈")

md(CSS.replace("__EXT__", f"url(data:image/jpeg;base64,{img('exterior.jpg', 1920)})" if img("exterior.jpg", 1920)
               else "linear-gradient(135deg,#17694F,#9CCBEA 62%,#F3A6B8)"))
topnav()
page = st.session_state["page"]
try:
    st.query_params["p"] = PAGE_TO_QUERY.get(page, "home")
except Exception:
    pass

{"홈": page_home, "버스 예약": page_bus, "음료 주문": page_drink}[page]()
footer()

if st.session_state.get("_last_page") != page:     # 화면을 바꾸면 맨 위로 이동
    st.session_state["_last_page"] = page
    components.html("<script>try{const d=window.parent.document;const m=d.querySelector('[data-testid=\"stMain\"]')||d.querySelector('section.main');if(m)m.scrollTo({top:0});}catch(e){}</script>", height=0)
