"""건양대학교 뀨 스탑 (KYU STOP) — 버스 대기 라운지 & 카페 웹앱
실행:  streamlit run app.py
"""
import base64
import csv
import hmac
import io
import json
import random
import string
from datetime import date, datetime, timedelta, timezone
from html import escape as esc
from pathlib import Path
from urllib.parse import quote

import pandas as pd
import requests
import streamlit as st
import streamlit.components.v1 as components

st.set_page_config(page_title="뀨 스탑 | 건양대학교 버스 라운지", page_icon="🐑",
                   layout="wide", initial_sidebar_state="collapsed")

BASE = Path(__file__).parent
ASSETS = BASE / "assets"

# ═════════════ 여기만 고치면 내용이 바뀝니다 (아래 값은 모두 예시예요) ═════════════
STOP_NAME = "뀨 스탑 (건양대학교 메디컬캠퍼스)"                         # 메디컬캠퍼스(대전) 정류장 이름
STOP_ADDRESS = "대전 서구 관저동로 158 건양대학교 메디컬캠퍼스 내"   # ← 예시: 실제 주소로 수정 (메디컬캠퍼스)
STOP_LAT, STOP_LON = 36.30389, 127.34352                      # 메디컬캠퍼스 뀨 스탑 자리 (구글 지도 Plus Code 8Q89883V+HC2 에서 계산)
OPEN_HOURS = "평일 07:00 – 20:00"                              # ← 예시: 실제 운영 시간으로 수정

# 캠퍼스 2곳 (글로벌=논산, 메디컬=대전). 아래 글로벌캠퍼스 주소·좌표는 예시예요 → 실제 값으로 수정
CAMPUSES = {
    "글로벌캠퍼스": dict(city="논산", name="뀨 스탑 (건양대학교 글로벌캠퍼스)",
                     address="충남 논산시 대학로 121 건양대학교 글로벌캠퍼스 내",   # ← 예시
                     lat=36.18119, lon=127.10870, hours=OPEN_HOURS),               # 글로벌캠퍼스 뀨 스탑 자리 (Plus Code 8Q8954J5+FFG 에서 계산)
    "메디컬캠퍼스": dict(city="대전", name=STOP_NAME, address=STOP_ADDRESS,
                     lat=STOP_LAT, lon=STOP_LON, hours=OPEN_HOURS),
}
CAMPUS_LIST = list(CAMPUSES)
# 특정 캠퍼스에만 가는 지역 노선은 여기에 적어요. 예: {"선사": ["글로벌캠퍼스"]}  (비워 두면 두 캠퍼스 모두)
ROUTE_ONLY = {}
SEAT_ROWS = 11                                                         # 좌석 줄 수 (한 줄 4석 → 44석)
# (노선 이름, 구분) — 호차 번호는 화면에 표시하지 않아요
REGIONAL = [("선사", "주중"), ("삼천중학교", "주중"), ("대전복합터미널", "주중"), ("송강", "주중"),
            ("신탄진", "주중"), ("북세종", "주중"), ("남세종·공주", "주중"), ("아산·천안", "주중"),
            ("청주·오송", "주중"), ("서울 노선", "주말"), ("인천·경기 노선", "주말"),
            ("태안·서산·당진", "주말"), ("예산·홍성·광천·보령", "주말"), ("대전복합터미널", "주말"),
            ("논산·대전", "방학")]
LOOPS = {"메디컬캠퍼스": ["건양대 메디컬캠퍼스", "대전복합터미널", "대전역 동광장"]}   # ← 예시: 캠퍼스별 순환 노선 정류장
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
PAGES = ["홈", "버스 예약", "음료 주문", "예약 조회"]
QUERY_TO_PAGE = {"bus": "버스 예약", "drink": "음료 주문", "lookup": "예약 조회", "home": "홈"}
PAGE_TO_QUERY = {v: k for k, v in QUERY_TO_PAGE.items()}


def routes_for(campus: str):
    """선택한 캠퍼스에서 이용할 수 있는 노선: 구분 → {노선 표시이름: (분류, 정류장 순서)}"""
    r = {"등교": {}, "하교": {}, "순환": {}}
    me = f"건양대 {campus}"
    for n, k in REGIONAL:
        if campus not in ROUTE_ONLY.get(n, CAMPUS_LIST):
            continue
        r["등교"][f"{k} · {n} → 건양대학교"] = (k, [n, me])      # 등교: 이 캠퍼스로 오는 버스
        r["하교"][f"{k} · 건양대학교 → {n}"] = (k, [me, n])      # 하교: 이 캠퍼스에서 출발
    for o in CAMPUS_LIST:                                         # 셔틀: 이 캠퍼스에서 출발해 다른 캠퍼스로
        if o != campus:
            r["순환"][f"셔틀 · {CAMPUSES[campus]['city']} → {CAMPUSES[o]['city']}"] = ("셔틀", [me, f"건양대 {o}"])
    if campus in LOOPS:
        r["순환"][f"주중 · {CAMPUSES[campus]['city']} 순환"] = ("주중", LOOPS[campus])
    return r


SEATS = [f"{i}{c}" for i in range(1, SEAT_ROWS + 1) for c in "ABCD"]
KST = timezone(timedelta(hours=9))


def now_kst():
    return datetime.now(KST)        # 배포 서버는 UTC라서 한국 시간으로 맞춰 써요


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


FIELDS = {
    "reservations": ["code", "created_at", "campus", "kind", "route", "ride_date", "depart_time", "name",
                     "board_stop", "alight_stop", "seat", "status"],
    "orders": ["code", "created_at", "name", "items", "total", "pickup_how", "pickup_when", "bus_code", "status"],
}


class DBError(requests.RequestException):
    """Supabase가 돌려준 오류 (상태 코드와 내용을 함께 보관)"""
    def __init__(self, status, msg, code=""):
        super().__init__(f"{status}: {msg}")
        self.status, self.msg, self.code = status, msg, code


def _sb():
    """Streamlit Secrets에서 Supabase 주소/키를 읽어요. 실수로 붙은 공백·따옴표·/rest/v1 은 자동으로 정리해요."""
    try:
        c = st.secrets["supabase"]
        url = str(c["url"]).strip().strip("\"'").rstrip("/")
        if url.endswith("/rest/v1"):
            url = url[: -len("/rest/v1")]
        key = str(c["key"]).strip().strip("\"'")
        return (url, key) if url and key else None
    except Exception:
        return None


def _key_kind(key: str) -> str:
    """키 종류 확인: secret / service_role (OK) · publishable / anon (공개용, 저장 불가)"""
    if key.startswith("sb_secret_"):
        return "secret"
    if key.startswith("sb_publishable_"):
        return "publishable"
    try:
        p = key.split(".")[1]
        p += "=" * (-len(p) % 4)
        return str(json.loads(base64.urlsafe_b64decode(p)).get("role") or "unknown")
    except Exception:
        return "unknown"


def _hdr(key, extra=None):
    # 새 방식 키(sb_...)는 JWT가 아니라서 Authorization 헤더에 넣으면 거절돼요 → apikey 헤더만 사용
    h = {"apikey": key, "Content-Type": "application/json"}
    if not key.startswith("sb_"):
        h["Authorization"] = f"Bearer {key}"
    return {**h, **(extra or {})}


def _check(r):
    if r.ok:
        return
    try:
        j = r.json()
        msg, code = str(j.get("message") or j.get("hint") or r.text)[:300], str(j.get("code") or "")
    except Exception:
        msg, code = r.text[:300], ""
    raise DBError(r.status_code, msg, code)


def explain(e) -> str:
    """오류를 초보자도 알 수 있는 한국어로 바꿔요 (키·주소 같은 비밀 정보는 화면에 안 보여요)"""
    cfg = _sb()
    kind = _key_kind(cfg[1]) if cfg else ""
    if isinstance(e, DBError):
        if e.code == "23505":
            return "방금 다른 분이 이 좌석을 예약했어요. 다른 좌석을 골라 주세요."
        if e.status in (401, 403):
            if kind in ("anon", "publishable"):
                return "저장 키가 ‘공개용(anon/publishable)’ 키예요. Streamlit Secrets의 key를 Supabase의 secret(service_role) 키로 바꿔 주세요."
            return "저장소 접속 권한이 없어요. Streamlit Secrets의 url과 key(secret 또는 service_role 키)를 다시 확인해 주세요."
        if e.status == 404 or e.code in ("PGRST205", "42P01"):
            return "Supabase에 reservations / orders 표가 없어요. SQL Editor에서 SQL을 다시 실행해 주세요."
        return f"저장소 오류가 났어요 ({e.status}). 잠시 후 다시 시도해 주세요."
    if isinstance(e, requests.exceptions.RequestException):
        return "저장소에 연결하지 못했어요. 인터넷이나 Supabase 주소를 확인해 주세요."
    return "알 수 없는 오류가 났어요. 잠시 후 다시 시도해 주세요."


def _lread(t):
    f = BASE / "data" / f"{t}.csv"
    if not f.exists():
        return []
    with f.open(encoding="utf-8-sig", newline="") as fh:
        return list(csv.DictReader(fh))


def _lwrite(t, rows):
    try:
        (BASE / "data").mkdir(exist_ok=True)
        with (BASE / "data" / f"{t}.csv").open("w", newline="", encoding="utf-8-sig") as fh:
            w = csv.DictWriter(fh, fieldnames=FIELDS[t])
            w.writeheader()
            w.writerows(rows)
    except OSError:
        pass  # 배포 환경에서 쓰기가 막혀도 화면 동작은 유지


def db_insert(t, row):
    cfg = _sb()
    if cfg:
        r = requests.post(f"{cfg[0]}/rest/v1/{t}", headers=_hdr(cfg[1], {"Prefer": "return=minimal"}), json=row, timeout=15)
        _check(r)
    else:
        _lwrite(t, _lread(t) + [{k: row.get(k, "") for k in FIELDS[t]}])


def db_select(t, **eq):
    cfg = _sb()
    if cfg:
        params = {"select": "*", "order": "created_at.desc", **{k: f"eq.{v}" for k, v in eq.items()}}
        r = requests.get(f"{cfg[0]}/rest/v1/{t}", headers=_hdr(cfg[1]), params=params, timeout=15)
        _check(r)
        return r.json()
    rows = [x for x in _lread(t) if all(str(x.get(k)) == str(v) for k, v in eq.items())]
    return sorted(rows, key=lambda x: x.get("created_at", ""), reverse=True)


def db_update(t, code, **vals):
    cfg = _sb()
    if cfg:
        r = requests.patch(f"{cfg[0]}/rest/v1/{t}", headers=_hdr(cfg[1]), params={"code": f"eq.{code}"}, json=vals, timeout=15)
        _check(r)
    else:
        rows = _lread(t)
        for x in rows:
            if x["code"] == code:
                x.update(vals)
        _lwrite(t, rows)


def taken_seats(campus, kind, route, day, time_):
    try:
        rows = db_select("reservations", campus=campus, kind=kind, route=route, ride_date=day, depart_time=time_, status="예약")
        return {r["seat"] for r in rows}
    except Exception as e:
        st.warning(f"좌석 정보를 불러오지 못했어요. {explain(e)}")
        return set()


def seat_map(taken, selected) -> str:
    rows = []
    for i in range(1, SEAT_ROWS + 1):
        cells = []
        for j, c in enumerate("ABCD"):
            sid = f"{i}{c}"
            cls = "taken" if sid in taken else ("sel" if sid == selected else "free")
            cells.append(f'<div class="seat {cls}">{sid}</div>')
            if j == 1:
                cells.append('<div class="aisle"></div>')
        rows.append(f'<div class="srow">{"".join(cells)}</div>')
    left = len(SEATS) - len(taken)
    return (f'<div class="bus"><div class="bus-head">▲ 앞(운전석) · 남은 좌석 {left}/{len(SEATS)}</div>{"".join(rows)}'
            '<div class="legend"><span class="lg free"></span>선택 가능<span class="lg sel"></span>내 좌석<span class="lg taken"></span>예약됨</div></div>')


ICONS = {
    "home": '<path d="M15 21v-8a1 1 0 0 0-1-1h-4a1 1 0 0 0-1 1v8"/><path d="M3 10a2 2 0 0 1 .709-1.528l7-5.999a2 2 0 0 1 2.582 0l7 5.999A2 2 0 0 1 21 10v9a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/>',
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
:root{--g:#0E4A38;--g2:#17694F;--gd:#07261C;--ink:#13241D;--mut:#5C6B62;--bg:#FFF8EE;--line:#E7DFCC;--mint:#E2F0E6;--led:#FFC24B;
--sky:#9CCBEA;--pink:#F3A6B8;--sun:#F7B885;--sunset:linear-gradient(100deg,#9CCBEA 0%,#F3A6B8 52%,#F7B885 100%);
--sh:0 26px 44px -30px rgba(7,38,28,.45);--pad:max(24px,calc(50vw - 590px));--bpad:72px;--navh:76px;--off:7px;--ext:__EXT__;
--hearts:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='44' height='30'%3E%3Cpath d='M22 21c-6-4.2-9.5-7-9.5-10.6a4.7 4.7 0 0 1 9.5-1.4 4.7 4.7 0 0 1 9.5 1.4C31.5 14 28 16.8 22 21z' fill='white' fill-opacity='.92'/%3E%3C/svg%3E")}
html{scroll-behavior:smooth}
#MainMenu,[data-testid="stHeader"],[data-testid="stToolbar"],[data-testid="stDecoration"],[data-testid="stStatusWidget"],[data-testid="stHeaderActionElements"],footer{display:none!important}
.stApp{background:radial-gradient(900px 520px at 100% 900px,rgba(247,184,133,.30),transparent 70%),radial-gradient(800px 520px at 0% 1800px,rgba(156,203,234,.32),transparent 70%),radial-gradient(800px 520px at 100% 2800px,rgba(243,166,184,.26),transparent 70%),radial-gradient(rgba(14,74,56,.07) 1.3px,transparent 1.7px) 0 0/26px 26px,var(--bg);overflow-x:hidden;color:var(--ink)}
[data-testid="stMain"],[data-testid="stAppViewContainer"]{overflow-x:hidden}
.stApp,.stApp :is(p,div,span,label,input,button,li,a,small,b,dt,dd,textarea,h1,h2,h3,h4,em):not([data-testid="stIconMaterial"]):not(.material-icons){font-family:'Pretendard','Apple SD Gothic Neo','Malgun Gothic',sans-serif}
.stApp [data-testid="stMarkdownContainer"] :is(h1,h2,h3,h4){padding:0;margin:0;line-height:1.25;font-weight:800;letter-spacing:-.025em;color:var(--ink)}
.stApp [data-testid="stMarkdownContainer"] p{margin:0}
:focus-visible{outline:3px solid rgba(255,194,75,.9)!important;outline-offset:2px}
.block-container,.stMainBlockContainer{max-width:1180px!important;padding:0 24px var(--bpad)!important}
.block-container>[data-testid="stVerticalBlock"],.stMainBlockContainer>[data-testid="stVerticalBlock"]{gap:0!important}
svg.i{width:22px;height:22px;fill:none;stroke:currentColor;stroke-width:1.9;stroke-linecap:round;stroke-linejoin:round;flex:none}

/* ═══ 상단 내비게이션: 초록 버스 차체 + 노을 라인 ═══ */
.st-key-topnav{position:fixed!important;top:0;left:0;right:0;z-index:1000;height:var(--navh);display:flex!important;flex-direction:row!important;
align-items:center;justify-content:space-between;gap:16px!important;padding:0 var(--pad)!important;box-sizing:border-box;
background:linear-gradient(180deg,#14604A 0%,#0A3A2C 100%);box-shadow:0 14px 30px -16px rgba(7,38,28,.8)}
.st-key-topnav:after{content:"";position:absolute;left:0;right:0;bottom:-5px;height:5px;background:var(--sunset)}
.st-key-topnav>*{width:auto!important;min-width:0;flex:0 0 auto}
.brand{display:flex;align-items:center;gap:13px;color:#fff}
.logo{position:relative;width:48px;height:48px;border-radius:50%;background:var(--led);color:var(--gd);display:grid;place-items:center;font-family:'Jua','Pretendard','Apple SD Gothic Neo','Malgun Gothic',sans-serif!important;font-size:25px;box-shadow:0 0 0 3px rgba(255,255,255,.28),0 6px 14px -4px rgba(0,0,0,.5)}
.stApp .brand b{display:block;font-family:'Jua','Pretendard','Apple SD Gothic Neo','Malgun Gothic',sans-serif!important;font-size:24px;line-height:1.05;font-weight:400;color:#fff;letter-spacing:.01em}
.brand small{display:block;color:#9FD6BA;font-weight:800;font-size:11.5px;letter-spacing:.2em;margin-top:2px}
.st-key-topnav [role="radiogroup"]{gap:6px!important;flex-wrap:nowrap!important;background:rgba(255,255,255,.1);padding:6px;border-radius:99px;border:1px solid rgba(255,255,255,.16)}
.st-key-topnav label:is([data-baseweb="radio"],[data-testid="stRadioOption"]){border:0!important;background:transparent!important;padding:13px 26px!important;border-radius:99px!important;gap:10px;box-shadow:none!important;color:rgba(255,255,255,.9)}
.st-key-topnav label:is([data-baseweb="radio"],[data-testid="stRadioOption"]):before{content:"";width:23px;height:23px;flex:none;background:currentColor;-webkit-mask:var(--ico) center/contain no-repeat;mask:var(--ico) center/contain no-repeat}
__NAVICONS__
.st-key-topnav label:is([data-baseweb="radio"],[data-testid="stRadioOption"]) p{font-size:17.5px;font-weight:800;white-space:nowrap;color:inherit!important}
.st-key-topnav label:is([data-baseweb="radio"],[data-testid="stRadioOption"]):hover{background:rgba(255,255,255,.14)!important;border-color:transparent!important}
.st-key-topnav label:is([data-baseweb="radio"],[data-testid="stRadioOption"]):has(input:checked){background:var(--led)!important;color:var(--gd);box-shadow:0 0 0 3px rgba(255,194,75,.3),0 10px 22px -8px rgba(255,194,75,.85)!important}

/* ── 라디오를 알약/세그먼트 버튼처럼 ── */
[data-testid="stRadio"] [role="radiogroup"]{gap:10px;flex-wrap:wrap}
[data-testid="stRadio"] label:is([data-baseweb="radio"],[data-testid="stRadioOption"]){margin:0;padding:13px 20px;border-radius:16px;border:2px solid var(--line);background:#fff;cursor:pointer;justify-content:center;transition:background .15s,border-color .15s,transform .1s;box-shadow:0 3px 0 #EADFC6}
[data-testid="stRadio"] label:is([data-baseweb="radio"],[data-testid="stRadioOption"])>div:not(:has(p)){display:none!important}
[data-testid="stRadio"] label[data-testid="stRadioOption"]>div>div:not([data-testid="stMarkdownContainer"]){display:none!important}
[data-testid="stRadio"] label:is([data-baseweb="radio"],[data-testid="stRadioOption"]):hover{border-color:var(--g2)}
[data-testid="stRadio"] label:is([data-baseweb="radio"],[data-testid="stRadioOption"]):has(input:checked){background:var(--g);border-color:var(--g);box-shadow:0 3px 0 var(--gd)}
[data-testid="stRadio"] label:is([data-baseweb="radio"],[data-testid="stRadioOption"]) p{font-weight:800;font-size:15.5px;color:var(--ink)}
[data-testid="stRadio"] label:is([data-baseweb="radio"],[data-testid="stRadioOption"]):has(input:checked) p{color:#fff}
.st-key-kind [role="radiogroup"],.st-key-d_cat [role="radiogroup"]{display:grid!important;grid-template-columns:repeat(3,1fr)}
.st-key-d_cat label:is([data-baseweb="radio"],[data-testid="stRadioOption"]){padding:13px 6px}

/* ═══ 버스 전광판(LED) · 다음 정류장 표시 ═══ */
.led{display:inline-flex;align-items:center;gap:12px;max-width:100%;padding:10px 18px 10px 14px;border-radius:14px;background:#08130F;border:2px solid rgba(255,255,255,.25);box-shadow:0 0 0 5px rgba(7,38,28,.55),inset 0 0 20px rgba(255,194,75,.14)}
.led-lamp{width:11px;height:11px;border-radius:50%;background:var(--led);box-shadow:0 0 12px 4px rgba(255,194,75,.7);animation:blink 1.4s steps(2,jump-none) infinite;flex:none}
.led-win{position:relative;overflow:hidden;width:min(440px,58vw)}
.led-win:after{content:"";position:absolute;inset:0;background:radial-gradient(rgba(0,0,0,.6) 1px,transparent 1.6px) 0 0/4px 4px}
.led-run{display:inline-flex;white-space:nowrap;animation:led 22s linear infinite}
.led-run span{padding-right:2.6em;font-family:'Jua','Pretendard','Apple SD Gothic Neo','Malgun Gothic',sans-serif!important;color:var(--led);font-size:18px;letter-spacing:.06em;text-shadow:0 0 9px rgba(255,194,75,.7)}
@keyframes led{to{transform:translateX(-50%)}}
@keyframes blink{50%{opacity:.35}}
.chipled{display:inline-flex;align-items:center;gap:8px;background:#08130F;color:var(--led);border-radius:10px;padding:6px 12px;font-family:'Jua','Pretendard','Apple SD Gothic Neo','Malgun Gothic',sans-serif!important;letter-spacing:.1em;font-size:14px;line-height:1.2;text-shadow:0 0 8px rgba(255,194,75,.6);border:1px solid rgba(255,255,255,.2)}
.chipled b{font-weight:400;font-family:inherit!important;color:#fff;text-shadow:none}
.nextstop{display:flex;align-items:center;gap:12px;margin-bottom:18px}
.nextstop .dot{width:22px;height:22px;border-radius:50%;background:#fff;border:6px solid var(--g);box-shadow:0 0 0 5px rgba(14,74,56,.14);flex:none}
.nextstop .stopname{font-weight:800;color:var(--g);font-size:15px}
.nextstop:after{content:"";height:3px;flex:1;max-width:160px;border-radius:3px;opacity:.45;background:repeating-linear-gradient(90deg,var(--g) 0 10px,transparent 10px 18px)}

/* ═══ 페이지 상단 띠 (버스 예약·음료 주문·예약 조회) ═══ */
.pagehead{position:relative;isolation:isolate;padding:calc(var(--navh) + 54px) 0 84px}
.pagehead:before{content:"";position:absolute;z-index:-1;top:0;bottom:0;left:50%;width:100vw;transform:translateX(-50%);
background:radial-gradient(55% 140% at 88% 130%,rgba(247,184,133,.8),transparent 62%),radial-gradient(45% 120% at 68% 140%,rgba(243,166,184,.65),transparent 62%),radial-gradient(40% 100% at 100% -10%,rgba(156,203,234,.5),transparent 60%),linear-gradient(120deg,#07261C,#0E4A38 70%)}
.pagehead:after{content:"";position:absolute;z-index:-1;bottom:0;left:50%;width:100vw;height:32px;transform:translateX(-50%);background:var(--hearts) repeat-x center/44px 30px,var(--g2);box-shadow:0 -4px 0 rgba(255,255,255,.25)}
.stApp .pagehead h1{font-family:'Jua','Pretendard','Apple SD Gothic Neo','Malgun Gothic',sans-serif!important;font-weight:400;font-size:clamp(38px,5.6vw,58px);color:#fff!important;margin-top:18px;letter-spacing:0}
.stApp .pagehead p{margin:12px 0 0;color:rgba(255,255,255,.88);font-size:17.5px;line-height:1.7;max-width:580px}
.sp{height:40px}
.sec{padding:112px 0 0;scroll-margin-top:96px}
.stApp .sec h2{font-family:'Jua','Pretendard','Apple SD Gothic Neo','Malgun Gothic',sans-serif!important;font-weight:400;font-size:clamp(32px,4.4vw,50px);line-height:1.2;letter-spacing:-.005em;margin:0 0 16px}
.stApp p.lead{color:var(--mut);font-size:17.5px;line-height:1.85;max-width:580px;margin:0}

/* ═══ 홈: 히어로 (사진은 화면 가득, 글은 왼쪽) ═══ */
.st-key-hero{position:relative!important;isolation:isolate;min-height:min(92vh,880px);padding:calc(var(--navh) + 76px) 0 140px!important;display:flex!important;flex-direction:column;justify-content:center;gap:0!important}
.st-key-hero:before{content:"";position:absolute;z-index:-1;top:0;bottom:0;left:50%;width:100vw;transform:translateX(-50%);
background:linear-gradient(95deg,rgba(5,30,22,.94) 0%,rgba(5,30,22,.62) 42%,rgba(5,30,22,.04) 100%),linear-gradient(0deg,rgba(243,166,184,.4),rgba(247,184,133,0) 42%),var(--ext) center/cover}
.st-key-hero:after{content:"";position:absolute;z-index:-1;top:calc(var(--navh) + 4px);left:50%;width:100vw;height:46px;transform:translateX(-50%);pointer-events:none;
background:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='120' height='46'%3E%3Cpath d='M0 6Q60 40 120 6' fill='none' stroke='white' stroke-opacity='.4' stroke-width='1.4'/%3E%3Cg fill='%23FFD9A0'%3E%3Ccircle cx='30' cy='19' r='7' fill-opacity='.28'/%3E%3Ccircle cx='60' cy='23' r='7' fill-opacity='.28'/%3E%3Ccircle cx='90' cy='19' r='7' fill-opacity='.28'/%3E%3Ccircle cx='30' cy='19' r='3.4'/%3E%3Ccircle cx='60' cy='23' r='3.4'/%3E%3Ccircle cx='90' cy='19' r='3.4'/%3E%3C/g%3E%3C/svg%3E") repeat-x top left}
.stApp .hero-copy h1{font-family:'Jua','Pretendard','Apple SD Gothic Neo','Malgun Gothic',sans-serif!important;font-weight:400;color:#fff;font-size:clamp(48px,7.6vw,96px);line-height:1.1;margin:30px 0 24px;letter-spacing:-.005em;animation:rise .8s cubic-bezier(.2,.7,.2,1) both;text-shadow:0 6px 30px rgba(0,0,0,.35)}
.stApp .hero-copy h1 em{font-style:normal;background:var(--sunset);-webkit-background-clip:text;background-clip:text;-webkit-text-fill-color:transparent;text-shadow:none}
.stApp .hero-copy p{color:rgba(255,255,255,.92);font-size:clamp(16.5px,1.7vw,20px);line-height:1.85;max-width:560px;margin:0 0 40px}
.st-key-hero [data-testid="stHorizontalBlock"]{max-width:580px;gap:14px}
@keyframes rise{from{opacity:0;transform:translateY(22px)}}
.infobar{position:relative;z-index:2;margin:-70px 0 0;display:grid;grid-template-columns:repeat(3,1fr)}
.infobar>div{display:flex;gap:15px;align-items:center;padding:24px 28px;border-right:2px dashed #CDE0D3}.infobar>div:last-child{border:0}
.infobar .ic{width:52px;height:52px;border-radius:50%;background:var(--sunset);color:var(--g);display:grid;place-items:center;flex:none;border:2px solid var(--g)}
.infobar small{display:block;color:var(--mut);font-size:13.5px;font-weight:700}.infobar b{font-size:17px;font-weight:800}

/* ═══ 창문 카드 (초록 버스 창틀 + 노을색 그림자) ═══ */
:is(.info,.floor,.pass,.infobar,.st-key-mapbox,.st-key-resv,.st-key-opt,.st-key-cartbox,.st-key-lookup){background:linear-gradient(112deg,transparent 60%,rgba(156,203,234,.16) 60% 68%,transparent 68%),#fff;border:2px solid var(--g);border-radius:28px;box-shadow:var(--off) var(--off) 0 var(--ofs,rgba(247,184,133,.6)),0 28px 40px -30px rgba(7,38,28,.4)}
.info,.st-key-mapbox{--ofs:rgba(156,203,234,.7)}
.floor:nth-child(1){--ofs:rgba(156,203,234,.7)}.floor:nth-child(2){--ofs:rgba(243,166,184,.65)}
.st-key-resv{--ofs:rgba(243,166,184,.55)}.st-key-opt{--ofs:rgba(156,203,234,.65)}.st-key-cartbox{--ofs:rgba(247,184,133,.65)}.st-key-lookup{--ofs:rgba(156,203,234,.65)}
.info{padding:32px;height:100%;box-sizing:border-box}
.stApp .info h3{font-family:'Jua','Pretendard','Apple SD Gothic Neo','Malgun Gothic',sans-serif!important;font-weight:400;font-size:26px;margin:0 0 18px}
.row{padding:15px 0;border-top:2px dashed #E3EAE0}.row small{display:block;color:var(--mut);font-weight:700;margin-bottom:4px}.row span{font-weight:700;line-height:1.6}
.row a{font-weight:800;color:var(--g)!important}
.chips{display:flex;flex-wrap:wrap;gap:8px;margin-top:8px}
.chip{border:2px solid var(--g);background:#fff;border-radius:99px;padding:6px 14px;font-weight:800;font-size:14px;color:var(--g)}
.st-key-mapbox{overflow:hidden}
.st-key-mapbox iframe{display:block;width:100%;border:0}
.split{display:grid;grid-template-columns:1.2fr 1fr;gap:64px;align-items:center}
.photo{aspect-ratio:3/2;border-radius:48px 48px 26px 26px;border:3px solid var(--g);background:var(--ext) center/cover;box-shadow:0 30px 60px -28px rgba(7,38,28,.55)}
.photo-wrap{position:relative}.photo-wrap:before{content:"";position:absolute;inset:26px -22px -22px 26px;border-radius:50px 50px 30px 30px;background:var(--sunset);opacity:.7}.photo-wrap .photo{position:relative}
.feat{list-style:none;padding:0;margin:30px 0 0;display:grid;gap:16px}
.feat li{display:flex;gap:15px;align-items:flex-start;line-height:1.6}
.feat i{width:50px;height:50px;border-radius:50%;background:var(--mint);color:var(--g);display:grid;place-items:center;flex:none;border:2px solid var(--g)}
.feat b{display:block;font-size:17px}.feat span{color:var(--mut);font-size:15.5px}
.floors{display:grid;grid-template-columns:1fr 1fr;gap:32px;margin-top:40px}
.floor{overflow:hidden}
.floor .pic{position:relative;background:linear-gradient(135deg,#DCEBE2,#F6E3E6);border-bottom:2px solid var(--g)}.floor img,.floor .ph{width:100%;display:block;aspect-ratio:4/3;object-fit:contain;background:#FBF8F1}
.floor .lv{position:absolute;left:16px;top:16px;background:var(--led);color:var(--gd);font-family:'Jua','Pretendard','Apple SD Gothic Neo','Malgun Gothic',sans-serif!important;font-size:18px;padding:5px 15px;border-radius:99px;border:2px solid var(--g)}
.floor .tx{padding:26px 28px 30px}.stApp .floor h3{font-family:'Jua','Pretendard','Apple SD Gothic Neo','Malgun Gothic',sans-serif!important;font-weight:400;font-size:25px;margin:0 0 8px}.floor p{color:var(--mut);line-height:1.8;font-size:16px}
.rgroup{margin:48px 0 16px;display:flex;align-items:baseline;gap:12px;flex-wrap:wrap}.stApp .rgroup h3{font-family:'Jua','Pretendard','Apple SD Gothic Neo','Malgun Gothic',sans-serif!important;font-weight:400;font-size:28px}.rgroup span{color:var(--mut);font-size:14.5px}
.rgrid{display:grid;grid-template-columns:repeat(auto-fill,minmax(220px,1fr));gap:14px}
.rc{position:relative;background:#fff;border:2px solid var(--line);border-left:8px solid var(--g2);border-radius:20px;padding:18px 18px 18px 16px;display:flex;flex-direction:column;gap:9px;transition:transform .15s,box-shadow .15s}
.rc:hover{transform:translateY(-3px);box-shadow:0 14px 24px -16px rgba(7,38,28,.5)}
.rc.e{border-left-color:#E58BA3}.rc.v{border-left-color:#EFA05F}.rc.s{border-left-color:#6FAEDC}
.rc b{font-size:18px;line-height:1.35}.rc small{color:var(--mut);font-weight:600}
.k{align-self:flex-start;font-size:12.5px;font-weight:800;padding:4px 11px;border-radius:99px}
.k.w{background:#E1F1E8;color:#146344}.k.e{background:#FBE4EA;color:#B03A5B}.k.v{background:#FFE8D2;color:#A5571B}.k.s{background:#DDEBFA;color:#1F5C99}
.st-key-cta{margin-top:112px;border-radius:34px;border:2px solid var(--g);padding:56px 52px!important;gap:0!important;position:relative;overflow:hidden;box-shadow:var(--off) var(--off) 0 rgba(243,166,184,.6);
background:radial-gradient(60% 140% at 100% 120%,rgba(247,184,133,.95),transparent 60%),radial-gradient(50% 120% at 80% 130%,rgba(243,166,184,.85),transparent 60%),radial-gradient(45% 100% at 100% -20%,rgba(156,203,234,.75),transparent 60%),linear-gradient(120deg,#07261C,#0E4A38 75%)}
.stApp .st-key-cta h2{font-family:'Jua','Pretendard','Apple SD Gothic Neo','Malgun Gothic',sans-serif!important;font-weight:400;color:#fff;font-size:clamp(30px,3.8vw,44px);margin:0 0 12px}
.stApp .st-key-cta p{color:rgba(255,255,255,.88);font-size:17.5px;line-height:1.7;margin:0 0 30px;max-width:500px}
.st-key-cta [data-testid="stHorizontalBlock"]{max-width:560px;gap:14px}

/* ═══ 푸터: 도로 위를 달리는 뀨 스탑 버스 ═══ */
.notice{position:relative;isolation:isolate;margin-top:110px;color:#fff}
.notice:before{content:"";position:absolute;z-index:-1;top:0;bottom:calc(-1*var(--bpad));left:50%;width:100vw;transform:translateX(-50%);background:var(--gd)}
.road{position:relative;height:84px}
.road:before{content:"";position:absolute;z-index:-1;top:0;bottom:0;left:50%;width:100vw;transform:translateX(-50%);background:#26332D;border-top:10px solid #E4DCC8}
.road:after{content:"";position:absolute;z-index:-1;bottom:20px;left:50%;width:100vw;height:5px;transform:translateX(-50%);background:repeating-linear-gradient(90deg,#FFE3A3 0 30px,transparent 30px 56px)}
.road-bus{position:absolute;left:calc(50% - 50vw - 220px);bottom:14px;width:170px;animation:drive 28s linear infinite}
.road-bus svg{display:block;width:100%;height:auto;filter:drop-shadow(0 6px 4px rgba(0,0,0,.35))}
@keyframes drive{to{transform:translateX(calc(100vw + 440px))}}
.notice-in{padding:60px 0 38px;display:grid;grid-template-columns:1.1fr 1fr 1fr;gap:44px}
.stApp .notice h2{font-family:'Jua','Pretendard','Apple SD Gothic Neo','Malgun Gothic',sans-serif!important;font-weight:400;font-size:32px;color:#fff}.stApp .notice h4{margin:0 0 10px;font-size:17px;color:var(--led)}.notice p{color:#A9BFB3;line-height:1.85;font-size:15.5px}
.foot{padding:24px 0 34px;border-top:1px solid rgba(255,255,255,.12);color:#86A094;font-size:13.5px;display:flex;justify-content:space-between;gap:16px;flex-wrap:wrap}

/* ── 폼 카드 ── */
:is(.st-key-resv,.st-key-opt,.st-key-cartbox){padding:34px 34px 36px!important;gap:.85rem!important}
.form-h{display:flex;gap:15px;align-items:center;margin-bottom:6px}.form-h .ico{width:54px;height:54px;border-radius:50%;background:var(--mint);color:var(--g);display:grid;place-items:center;flex:none;border:2px solid var(--g)}
.stApp .form-h h3{font-family:'Jua','Pretendard','Apple SD Gothic Neo','Malgun Gothic',sans-serif!important;font-weight:400;font-size:27px}.stApp .form-h p{margin:3px 0 0;color:var(--mut);font-size:14.5px}
.fs{display:flex;align-items:center;gap:12px;margin:22px 0 6px;font-weight:800;font-size:16.5px}
.fs em{font-style:normal;width:30px;height:30px;border-radius:50%;background:var(--g);color:#fff;display:grid;place-items:center;font-size:14px;box-shadow:0 0 0 4px rgba(14,74,56,.15);flex:none}
.fs:after{content:"";flex:1;height:3px;border-radius:3px;opacity:.35;background:repeating-linear-gradient(90deg,var(--g) 0 8px,transparent 8px 15px)}
[data-testid="stWidgetLabel"] p{font-weight:800;font-size:14.5px;color:var(--ink)}
[data-baseweb="base-input"]{background:transparent!important}
[data-testid="stTextInputRootElement"],.stNumberInput [data-baseweb="input"],.stDateInput [data-baseweb="input"],[data-baseweb="select"]>div{border-radius:14px!important;border:2px solid var(--line)!important;background:#fff!important;min-height:52px}
[data-testid="stTextInputRootElement"] :is([data-baseweb="input"],[data-baseweb="base-input"],input){background:transparent!important;border:0!important}
[data-testid="stTextInputRootElement"]:focus-within,.stNumberInput [data-baseweb="input"]:focus-within,.stDateInput [data-baseweb="input"]:focus-within,[data-baseweb="select"]>div:focus-within{border-color:var(--g2)!important;box-shadow:0 0 0 4px rgba(23,105,79,.15)!important}
.stTextInput input,.stNumberInput input,.stDateInput input{font-size:16px;min-height:48px}
[data-testid="stAlert"]{border-radius:16px}
[data-testid="stCaptionContainer"]{color:var(--mut)}
[data-baseweb="tab"]{font-weight:800;font-size:16px}

/* ── 버튼: 꾹 눌리는 느낌 ── */
.stButton,.stButton>button{width:100%}
.stElementContainer:has(>.stButton){width:100%!important}
.stElementContainer:not(.st-key-page):has(>[data-testid="stRadio"]){width:100%!important}
[data-testid="stRadio"] label:is([data-baseweb="radio"],[data-testid="stRadioOption"]){display:flex;align-items:center;width:100%;box-sizing:border-box}
.stButton>button{min-height:56px;border-radius:17px;font-weight:800;font-size:17px;transition:transform .1s,box-shadow .1s,background .15s}
.stButton>button:active{transform:translateY(4px)}
.stButton>button:is([kind="primary"],[data-testid="stBaseButton-primary"]){background:var(--g);color:#fff;border:0;box-shadow:0 5px 0 var(--gd),0 18px 24px -16px rgba(7,38,28,.7)}
.stButton>button:is([kind="primary"],[data-testid="stBaseButton-primary"]):hover{background:var(--g2);color:#fff}
.stButton>button:is([kind="primary"],[data-testid="stBaseButton-primary"]):active{box-shadow:0 1px 0 var(--gd)}
.stButton>button:is([kind="secondary"],[data-testid="stBaseButton-secondary"]){background:#fff;color:var(--g);border:2px solid var(--g);box-shadow:0 4px 0 rgba(14,74,56,.22)}
.stButton>button:is([kind="secondary"],[data-testid="stBaseButton-secondary"]):hover{background:var(--mint);color:var(--g)}
.stButton>button:is([kind="secondary"],[data-testid="stBaseButton-secondary"]):active{box-shadow:0 1px 0 rgba(14,74,56,.22)}
.stButton>button p{font-weight:800;color:inherit!important}
.st-key-hb_bus button,.st-key-cb_bus button{background:var(--led)!important;color:var(--gd)!important;border:0!important;min-height:66px;box-shadow:0 6px 0 #B9801C,0 20px 28px -14px rgba(0,0,0,.55)!important}
.st-key-hb_bus button:hover,.st-key-cb_bus button:hover{background:#FFD173!important}
.st-key-hb_bus button:active,.st-key-cb_bus button:active{box-shadow:0 1px 0 #B9801C!important}
.st-key-hb_drink button,.st-key-cb_drink button{background:rgba(255,255,255,.14)!important;color:#fff!important;border:2px solid rgba(255,255,255,.75)!important;min-height:66px;box-shadow:0 6px 0 rgba(255,255,255,.2)!important;backdrop-filter:blur(6px)}
.st-key-hb_drink button:hover,.st-key-cb_drink button:hover{background:rgba(255,255,255,.26)!important}
.st-key-hb_drink button:active,.st-key-cb_drink button:active{box-shadow:0 1px 0 rgba(255,255,255,.2)!important}
.st-key-hb_bus p,.st-key-hb_drink p,.st-key-cb_bus p,.st-key-cb_drink p{font-size:18px}
.st-key-hb_bus button p,.st-key-cb_bus button p{color:var(--gd)!important}
.st-key-hb_drink button p,.st-key-cb_drink button p{color:#fff!important}

/* ── 승차권 / 영수증 카드 ── */
.pass{overflow:hidden;position:relative;--ofs:rgba(247,184,133,.7)}
.pass-top{position:relative;overflow:hidden;padding:28px 30px 26px;background:linear-gradient(120deg,#07261C,#0E4A38 75%);color:#fff}
.pass-top:before{content:"";position:absolute;inset:0;background:radial-gradient(55% 130% at 100% 130%,rgba(247,184,133,.75),transparent 62%),radial-gradient(45% 110% at 78% 140%,rgba(243,166,184,.6),transparent 62%)}
.pass-top>*{position:relative}
.pass-lab{display:flex;align-items:center;gap:8px;font-weight:800;font-size:14.5px;color:var(--led)}
.pass-lab svg.i{width:19px;height:19px;stroke-width:2.6}
.stApp .pass-title{margin:10px 0 4px;font-size:22px;font-weight:800;line-height:1.35;letter-spacing:-.02em}
.stApp .pass.ok .pass-title{font-size:34px;letter-spacing:.03em;font-variant-numeric:tabular-nums;font-family:'Jua','Pretendard','Apple SD Gothic Neo','Malgun Gothic',sans-serif!important;font-weight:400;color:var(--led);text-shadow:0 0 14px rgba(255,194,75,.45)}
.pass-sub{font-size:15px;opacity:.92}
.pass-cut{position:relative;height:0;margin:0 22px;border-top:3px dashed #BFD3C6}
.pass-cut:before,.pass-cut:after{content:"";position:absolute;top:-15px;width:28px;height:28px;border-radius:50%;background:var(--bg);border:2px solid var(--g)}.pass-cut:before{left:-37px}.pass-cut:after{right:-37px}
.pass-body{padding:26px 30px 28px}
.pass dl{display:grid;grid-template-columns:auto 1fr;gap:10px 20px;margin:18px 0 0}.pass dt{color:var(--mut);font-size:14px}.pass dd{margin:0;font-weight:700;line-height:1.5}
.pass-note{margin-top:18px;padding-top:16px;border-top:2px dashed #DCE6DF;color:var(--mut);font-size:13.5px;line-height:1.7}
.tl{list-style:none;margin:0;padding:0}
.tl li{position:relative;display:flex;align-items:center;gap:14px;padding:0 0 18px 0;color:#97A79E;font-weight:600}.tl li:last-child{padding-bottom:0}
.tl li:before{content:"";position:absolute;left:6px;top:16px;bottom:-4px;width:3px;background:#DCE6DF}.tl li:last-child:before{display:none}
.tl li.on:before{background:var(--g2)}
.tl i{width:15px;height:15px;border-radius:50%;background:#fff;border:3px solid #C9D6CE;flex:none;position:relative;z-index:1}
.tl li.on{color:var(--ink)}.tl li.on i{border-color:var(--g2);background:var(--g2)}
.tl em{font-style:normal;margin-left:auto;font-size:12.5px;font-weight:800;color:var(--gd);background:var(--led);padding:3px 11px;border-radius:99px}
.warnchip{display:inline-block;margin-top:12px;padding:5px 12px;border-radius:99px;background:#FFE8D2;color:#A5571B;font-weight:800;font-size:13px}
.codewarn{display:flex;gap:12px;align-items:flex-start;margin:0 0 16px;padding:14px 16px;border-radius:18px;background:#FFF1E0;border:2px solid #F0A55C;color:#7A3F0E;line-height:1.6;font-size:14.5px}
.codewarn .cw-ic{flex:none;width:26px;height:26px;margin-top:1px;border-radius:50%;background:#E8741C;color:#fff;display:grid;place-items:center;font-size:16px;font-weight:900;font-style:normal}
.codewarn strong{display:block;font-size:15.5px;margin-bottom:2px;color:#6A3208}
.cancelask{margin:8px 0 10px;padding:12px 14px;border-radius:14px;background:#FFE8EC;color:#9A3A52;font-weight:700;font-size:14.5px;line-height:1.6}
.notes{margin:20px 4px 0;padding:0;list-style:none;display:grid;gap:8px;color:var(--mut);font-size:14px;line-height:1.6}
.notes li{display:flex;gap:10px}.notes li:before{content:"";width:7px;height:7px;border-radius:50%;background:var(--sun);margin-top:9px;flex:none}
.ol{display:grid;gap:10px;margin:0}.ol div{display:flex;justify-content:space-between;gap:14px;font-weight:700}.ol small{display:block;color:var(--mut);font-weight:500}
.tot{display:flex;justify-content:space-between;align-items:baseline;margin-top:14px;padding-top:14px;border-top:2px dashed #CFDCD3;font-weight:800}.tot b{font-size:23px;color:var(--g)}

/* ── 음료 주문 ── */
.cup{width:58px;height:72px;flex:none}
:is([class*="st-key-mi_"],[class*="st-key-mis_"]){background:#fff;border:2px solid var(--line);border-radius:24px;padding:16px!important;gap:.6rem!important;transition:border-color .15s,box-shadow .15s,transform .15s}
:is([class*="st-key-mi_"]):hover{border-color:var(--g2);transform:translateY(-2px)}
[class*="st-key-mis_"]{border-color:var(--g);box-shadow:5px 5px 0 rgba(255,194,75,.75)}
.mc{display:flex;justify-content:space-between;align-items:flex-start;gap:8px}
.mc .tg{font-size:12px;font-weight:800;color:#B03A5B;background:#FBE4EA;border-radius:99px;padding:4px 10px}
.stApp .mc-n{font-weight:800;font-size:17.5px;margin-top:2px}.stApp .mc-d{color:var(--mut);font-size:13px;line-height:1.55;min-height:40px;margin:3px 0 8px}
.mc-p{font-weight:800;color:var(--g);font-size:16.5px}.mc-i{display:inline-block;margin-left:6px;font-size:12px;color:var(--mut);font-weight:700}
.st-key-menu [data-testid="stHorizontalBlock"]{gap:14px}
.st-key-menu .stButton>button{min-height:44px;font-size:14.5px;border-radius:13px}
.st-key-opt{margin-top:26px}
.opt-h{display:flex;gap:18px;align-items:center}.opt-h .cup{width:72px;height:90px}.opt-h>div{flex:1}
.stApp .opt-h h3{font-family:'Jua','Pretendard','Apple SD Gothic Neo','Malgun Gothic',sans-serif!important;font-weight:400;font-size:27px}.stApp .opt-h p{color:var(--mut);margin-top:4px;font-size:14.5px;line-height:1.55}.opt-h .pr{font-size:23px;font-weight:800;color:var(--g);white-space:nowrap}
.st-key-cartlines [data-testid="stHorizontalBlock"]{flex-wrap:nowrap!important;gap:10px!important;align-items:center}
.st-key-cartlines :is([data-testid="stColumn"],[data-testid="column"]){min-width:0!important}
.st-key-cartlines :is([data-testid="stColumn"],[data-testid="column"]):last-child{flex:0 0 70px!important;width:70px!important}
.st-key-cartlines .stButton>button{min-height:40px;font-size:13.5px;border-radius:11px;color:#9A3A52;border-color:#F1D3DB;box-shadow:none}
.cl{background:var(--mint);border-radius:16px;padding:12px 16px;display:flex;justify-content:space-between;gap:12px;font-weight:700;line-height:1.45}
.cl small{display:block;color:var(--mut);font-weight:600}
.empty{border:2px dashed #CBD9D0;border-radius:18px;padding:28px 18px;text-align:center;color:var(--mut);line-height:1.7;font-size:15px}
.ch{display:flex;align-items:center;gap:10px}.ch .ico{width:46px;height:46px;border-radius:50%;background:var(--mint);color:var(--g);display:grid;place-items:center;border:2px solid var(--g)}
.stApp .ch h3{font-family:'Jua','Pretendard','Apple SD Gothic Neo','Malgun Gothic',sans-serif!important;font-weight:400;font-size:24px}.ch .n{margin-left:auto;background:var(--g);color:#fff;font-weight:800;font-size:13px;border-radius:99px;padding:4px 11px}

/* ── 분위기 장식 ── */
.stripe{height:32px;margin:80px 0 -40px;border-radius:99px;border:2px solid var(--gd);background:var(--hearts) repeat-x center/44px 30px,var(--g);box-shadow:0 12px 24px -16px rgba(7,38,28,.6)}
.moods{display:grid;grid-template-columns:repeat(3,1fr);gap:20px;margin-top:36px}
.mood{position:relative;overflow:hidden;min-height:220px;border-radius:28px;border:2px solid var(--g);padding:28px;display:flex;flex-direction:column;justify-content:flex-end;box-shadow:var(--off) var(--off) 0 rgba(14,74,56,.18)}
.mood:before{content:"";position:absolute;width:130px;height:130px;border-radius:50%;right:-24px;top:-24px;background:radial-gradient(circle,rgba(255,255,255,.9),rgba(255,255,255,0) 70%)}
.mood.m1{background:linear-gradient(160deg,#D8ECF8,#9CCBEA)}.mood.m2{background:linear-gradient(160deg,#E4F2E8,#9CCDB2)}.mood.m3{background:linear-gradient(160deg,#F7B885,#F3A6B8 70%,#9CCBEA)}
.mood b{position:relative;font-family:'Jua','Pretendard','Apple SD Gothic Neo','Malgun Gothic',sans-serif!important;font-weight:400;font-size:34px;color:#07261C;margin-bottom:6px}
.mood span{position:relative;color:#1B3A2E;font-weight:700;line-height:1.6;font-size:15.5px}
.st-key-map_campus{margin-bottom:20px}
:is(.st-key-b_campus,.st-key-map_campus) [role="radiogroup"]{display:grid!important;grid-template-columns:repeat(2,1fr)}
.st-key-lookup{padding:34px!important;gap:.85rem!important}
/* 좌석표 */
.bus{background:#fff;border:3px solid var(--g);border-radius:40px 40px 20px 20px;padding:16px 14px;max-width:330px;margin:4px auto 8px;box-shadow:5px 5px 0 rgba(156,203,234,.7)}
.bus-head{display:block;text-align:center;font-size:13px;font-family:'Jua','Pretendard','Apple SD Gothic Neo','Malgun Gothic',sans-serif!important;color:var(--led);background:#08130F;border-radius:10px;padding:7px 10px;margin-bottom:14px;letter-spacing:.04em;text-shadow:0 0 8px rgba(255,194,75,.6)}
.srow{display:flex;gap:6px;justify-content:center;margin-bottom:6px}
.seat{width:46px;height:40px;border-radius:11px 11px 7px 7px;display:grid;place-items:center;font-size:12.5px;font-weight:800}
.seat.free{background:#fff;border:2px solid var(--g2);color:var(--g)}
.seat.taken{background:#E8EAE5;border:2px solid #E8EAE5;color:#A3ADA6;text-decoration:line-through}
.seat.sel{background:var(--led);border:2px solid var(--g);color:var(--gd);box-shadow:0 8px 16px -8px rgba(185,128,28,.9)}
.aisle{width:16px}
.legend{margin-top:12px;font-size:12px;color:var(--mut);text-align:center;font-weight:700}
.lg{display:inline-block;width:12px;height:12px;border-radius:4px;margin:0 5px 0 12px;vertical-align:-2px}
.lg.free{background:#fff;border:2px solid var(--g2)}.lg.sel{background:var(--led);border:2px solid var(--g)}.lg.taken{background:#E8EAE5}
[data-testid="stButtonGroup"] button{border-radius:13px!important;border:2px solid var(--line)!important;background:#fff!important;color:var(--ink)!important;font-weight:800;min-height:42px}
[data-testid="stButtonGroup"] button p{color:inherit!important}
[data-testid="stButtonGroup"] button[data-testid$="Active"]{background:var(--g)!important;border-color:var(--g)!important;color:#fff!important}

/* ── 태블릿 / 모바일 ── */
@media(max-width:980px){
.st-key-topnav label:is([data-baseweb="radio"],[data-testid="stRadioOption"]){padding:11px 16px!important;gap:8px}.st-key-topnav label:is([data-baseweb="radio"],[data-testid="stRadioOption"]) p{font-size:16px}
.brand small{display:none}
}
@media(max-width:860px){.split,.floors,.notice-in,.infobar,.moods{grid-template-columns:1fr}.mood{min-height:160px}.split{gap:40px}.infobar>div{border-right:0;border-bottom:2px dashed #CDE0D3}.infobar>div:last-child{border:0}
:root{--off:5px}
:is(.st-key-resv,.st-key-opt,.st-key-cartbox,.st-key-lookup){padding:24px 20px 26px!important}.st-key-cta{padding:38px 24px!important}.sec{padding-top:88px}}
@media(max-width:760px){
:root{--navh:62px;--bpad:116px}
.st-key-topnav{padding:0 16px!important}
.logo{width:42px;height:42px;font-size:22px}.stApp .brand b{font-size:22px}.brand small{display:block;font-size:10.5px}
.st-key-topnav [data-testid="stRadio"]{position:fixed;left:10px;right:10px;bottom:max(10px,env(safe-area-inset-bottom));z-index:1001;width:auto!important}
.st-key-topnav [role="radiogroup"]{display:grid!important;grid-template-columns:repeat(4,1fr);gap:4px!important;background:#0A3A2C;border:2px solid rgba(255,255,255,.18);padding:7px;border-radius:28px;box-shadow:0 20px 44px -10px rgba(7,38,28,.75)}
.st-key-topnav label:is([data-baseweb="radio"],[data-testid="stRadioOption"]){flex-direction:column;padding:11px 0 10px!important;gap:5px;border-radius:21px!important}
.st-key-topnav label:is([data-baseweb="radio"],[data-testid="stRadioOption"]):before{width:27px;height:27px}
.st-key-topnav label:is([data-baseweb="radio"],[data-testid="stRadioOption"]) p{font-size:14px}
.pagehead{padding-bottom:70px}.pagehead p{font-size:16px}
.st-key-hero{min-height:auto;padding-top:calc(var(--navh) + 70px)!important;padding-bottom:110px!important}
.st-key-hero:before{background:linear-gradient(180deg,rgba(5,30,22,.88) 0%,rgba(5,30,22,.62) 55%,rgba(5,30,22,.25) 100%),linear-gradient(0deg,rgba(243,166,184,.4),rgba(247,184,133,0) 42%),var(--ext) 62% center/cover}
.led-win{width:min(440px,56vw)}.led-run span{font-size:16px}
.stApp .hero-copy h1{font-size:clamp(38px,11.4vw,54px);margin:24px 0 20px}
.st-key-mapbox iframe{height:320px}
.pass-top{padding:24px 22px 22px}.pass-body{padding:22px 22px 24px}.stApp .pass.ok .pass-title{font-size:29px}
.road{height:72px}.road-bus{width:130px}
}
@media(max-width:640px){
.st-key-menu [data-testid="stHorizontalBlock"]{flex-wrap:wrap!important;gap:10px!important}
.st-key-menu :is([data-testid="stColumn"],[data-testid="column"]){flex:0 0 calc(50% - 5px)!important;min-width:calc(50% - 5px)!important;width:calc(50% - 5px)!important}
[class*="st-key-mi_"],[class*="st-key-mis_"]{padding:12px!important}.mc .cup{width:46px;height:58px}.mc-d{font-size:12.5px;min-height:58px}
.opt-h{flex-wrap:wrap}.opt-h .cup{width:56px;height:70px}
.mc-p{margin-bottom:8px}.st-key-d_cat label:is([data-baseweb="radio"],[data-testid="stRadioOption"]) p{font-size:13px;white-space:nowrap}.st-key-d_cat [role="radiogroup"]{gap:6px}
}
@media(prefers-reduced-motion:reduce){html{scroll-behavior:auto}.stApp .hero-copy h1,.led-run,.led-lamp,.road-bus{animation:none}.road-bus{left:auto;right:12%}.stButton>button{transition:none}}
</style>
"""


def _mask(paths: str) -> str:
    svg = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="#000" stroke-width="2.2" '
           f'stroke-linecap="round" stroke-linejoin="round">{paths}</svg>')
    return 'url("data:image/svg+xml,' + quote(svg, safe="") + '")'


def nav_icon_css() -> str:
    """내비게이션 탭 앞에 붙는 아이콘 (홈·버스 예약·음료 주문·예약 조회 순서)"""
    return "".join(f'.st-key-topnav [role="radiogroup"]>:nth-child({i}){{--ico:{_mask(ICONS[k])}}}\n'
                   for i, k in enumerate(["home", "bus", "coffee", "ticket"], 1))



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


def nextstop(n: int, name: str) -> str:
    """‘다음 정류장’ 전광판 + 노선도 점 (섹션 제목 위에 붙어요)"""
    return (f'<div class="nextstop"><span class="dot"></span><span class="chipled">NEXT STOP <b>{n:02d}</b></span>'
            f'<span class="stopname">{name}</span></div>')


def pagehead(title: str, sub: str, tag: str = ""):
    chip = f'<span class="chipled">{tag}</span>' if tag else ""
    md(f'<div class="pagehead">{chip}<h1>{title}</h1><p>{sub}</p></div><div class="sp"></div>')


def pass_card(label, title, sub, body, ok=False, note=""):
    mark = "[[check]]" if ok else "[[ticket]]"
    note_html = f'<div class="pass-note">{note}</div>' if note else ""
    md(f'<div class="pass{" ok" if ok else ""}"><div class="pass-top"><div class="pass-lab">{mark}{label}</div>'
       f'<div class="pass-title">{title}</div><div class="pass-sub">{sub}</div></div><div class="pass-cut"></div>'
       f'<div class="pass-body">{body}{note_html}</div></div>')


def code_warning(kind: str) -> str:
    """번호를 잊지 않도록 알려 주는 경고 상자"""
    if kind == "bus":
        t, d = "예약번호를 꼭 기억하세요!", "예약번호가 없으면 셔틀을 탈 수 없어요. 지금 화면을 캡처하거나, 아래 복사 버튼으로 번호를 저장해 두세요."
    else:
        t, d = "주문번호를 꼭 기억하세요!", "주문번호가 없으면 음료를 받을 수 없어요. 지금 화면을 캡처하거나, 아래 복사 버튼으로 번호를 저장해 두세요."
    return f'<div class="codewarn" role="alert"><b class="cw-ic">!</b><div><strong>{t}</strong>{d}</div></div>'


def bus_cancelable(day: str, time_: str) -> bool:
    """출발 시간이 아직 안 지났을 때만 취소할 수 있어요"""
    try:
        return datetime.fromisoformat(f"{day} {time_}").replace(tzinfo=KST) > now_kst()
    except Exception:
        return True


def ask_cancel(flag: str, code: str):
    st.session_state[flag] = code


def stop_cancel(flag: str):
    st.session_state.pop(flag, None)


def do_cancel(table: str, code: str, flag: str, ticket_key, msg_key):
    try:
        db_update(table, code, status="취소")
    except Exception as e:
        st.session_state["_tk_err"] = explain(e)
        st.session_state.pop(flag, None)
    else:
        st.session_state.pop(flag, None)
        if ticket_key:
            st.session_state.pop(ticket_key, None)
        if msg_key:
            st.session_state[msg_key] = code
        st.toast("취소했어요", icon="✅")


def cancel_box(table: str, code: str, label: str, pfx: str, ticket_key=None, msg_key=None):
    """취소 버튼 → '정말 취소할까요?' 확인 → 취소 (실수로 누르는 걸 막아 줘요)"""
    flag = f"{pfx}_confirm"
    err = st.session_state.pop("_tk_err", None)
    if err:
        st.error(f"취소에 실패했어요. {err}")
    if st.session_state.get(flag) == code:
        md('<div class="cancelask">정말 취소할까요? 취소하면 되돌릴 수 없어요.</div>')
        a, b = st.columns(2)
        a.button("네, 취소할게요", key=f"{pfx}_yes", type="primary", on_click=do_cancel, args=(table, code, flag, ticket_key, msg_key))
        b.button("아니요", key=f"{pfx}_no", on_click=stop_cancel, args=(flag,))
    else:
        st.button(label, key=f"{pfx}_ask", on_click=ask_cancel, args=(flag, code))


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


BUS_SVG = ('<svg viewBox="0 0 230 124" aria-hidden="true"><rect x="6" y="8" width="208" height="94" rx="20" fill="#17694F" stroke="#07261C" stroke-width="4"/>'
           '<rect x="150" y="1" width="52" height="9" rx="3" fill="#08130F"/><rect x="154" y="4" width="44" height="3" rx="1.5" fill="#FFC24B"/>'
           '<rect x="6" y="54" width="208" height="8" fill="#0E4A38"/>'
           '<g fill="#CFE9F5" stroke="#07261C" stroke-width="2.5"><rect x="20" y="20" width="34" height="26" rx="6"/><rect x="62" y="20" width="34" height="26" rx="6"/>'
           '<rect x="104" y="20" width="34" height="26" rx="6"/><rect x="146" y="20" width="34" height="26" rx="6"/><rect x="188" y="20" width="18" height="26" rx="6"/>'
           '<rect x="20" y="68" width="34" height="26" rx="6"/><rect x="62" y="68" width="34" height="26" rx="6"/><rect x="104" y="68" width="34" height="26" rx="6"/>'
           '<rect x="188" y="68" width="18" height="26" rx="6"/></g><rect x="146" y="66" width="34" height="36" rx="6" fill="#FFE3A3" stroke="#07261C" stroke-width="2.5"/>'
           '<path d="M163 90c-6-4-9-6.4-9-9.6a4.3 4.3 0 0 1 9-1.3 4.3 4.3 0 0 1 9 1.3c0 3.200-3 5.600-9 9.600z" fill="#F3A6B8"/>'
           '<circle cx="209" cy="96" r="5" fill="#FFC24B"/>'
           '<g><circle cx="56" cy="104" r="15" fill="#13241D"/><circle cx="56" cy="104" r="6" fill="#9AA9A0"/>'
           '<circle cx="168" cy="104" r="15" fill="#13241D"/><circle cx="168" cy="104" r="6" fill="#9AA9A0"/></g></svg>')


def footer():
    md(f"""<div class="notice"><div class="road"><div class="road-bus">{BUS_SVG}</div></div>
    <div class="notice-in"><h2>탑승 전 확인해 주세요</h2>
    <div><h4>탑승 시간 준수</h4><p>출발 10분 전까지 정류장에 도착해 주세요. 정시 출발을 원칙으로 합니다.</p></div>
    <div><h4>예약 취소 문의</h4><p>예약 취소는 ‘예약 조회’ 화면에서 직접 할 수 있어요. 무단 미탑승이 반복되면 이용이 제한될 수 있습니다.</p></div></div>
    <div class="foot"><span>건양대학교 뀨 스탑 버스 예약 서비스</span><span>가격·시간표·이미지는 이해를 돕기 위한 예시입니다.</span></div></div>""")


# ───────────────────────── 홈 ─────────────────────────
def home_hero():
    with st.container(key="hero"):
        led = "KONYANG UNIVERSITY ♥ 건양대학교 버스 대기 라운지 &amp; 카페 ♥ 다음 정류장은 뀨 스탑입니다 ♥"
        md(f"""<div class="hero-copy"><div class="led"><span class="led-lamp"></span><div class="led-win"><div class="led-run"><span>{led}</span><span aria-hidden="true">{led}</span></div></div></div>
        <h1>기다림도<br><em>설렘</em>이 될 수 있도록</h1>
        <p>버스를 기다리는 시간, 따뜻한 라운지에서 음료 한 잔과 함께 쉬어 가세요. 좌석과 음료를 미리 예약해 두면 도착해서 바로 쉴 수 있어요.</p></div>""")
        c1, c2 = st.columns(2)
        c1.button("버스 예약하기", key="hb_bus", type="primary", on_click=go, args=("버스 예약",))
        c2.button("음료 주문하기", key="hb_drink", on_click=go, args=("음료 주문",))
    md(f"""<div class="infobar">
    <div><span class="ic">[[pin]]</span><span><small>위치</small><b>글로벌 · 메디컬 캠퍼스 승차장 옆</b></span></div>
    <div><span class="ic">[[clock]]</span><span><small>운영 시간</small><b>{esc(OPEN_HOURS)}</b></span></div>
    <div><span class="ic">[[coffee]]</span><span><small>음료</small><b>사전 주문 가능</b></span></div></div>""")


def home_location():
    md(f"""<div id="location" class="sec">{nextstop(1, "정류장 위치")}<h2>뀨 스탑은 여기에 있어요</h2>
    <p class="lead">글로벌캠퍼스와 메디컬캠퍼스, 두 곳 모두 버스 승차장 바로 옆에서 노란 불빛이 켜진 초록색 2층 건물을 찾아보세요.</p></div><div class="sp"></div>""")
    cname = st.radio("캠퍼스 위치", CAMPUS_LIST, horizontal=True, key="map_campus", label_visibility="collapsed")
    c = CAMPUSES[cname]
    lat, lon = c["lat"], c["lon"]
    left, right = st.columns([1.5, 1], gap="large")
    d = 0.004
    bbox = f"{lon-d},{lat-d*.6},{lon+d},{lat+d*.6}"
    src = (f"https://www.openstreetmap.org/export/embed.html?bbox={quote(bbox, safe=',')}"
           f"&layer=mapnik&marker={lat},{lon}")
    with left:
        with st.container(key="mapbox"):
            if hasattr(st, "iframe"):          # 최신 Streamlit
                st.iframe(src, height=430)
            else:                              # 구버전 호환
                components.iframe(src, height=430)
    with right:
        md(f"""<div class="info"><h3>{esc(c["name"])}</h3>
        <div class="row"><small>주소</small><span>{esc(c["address"])}</span></div>
        <div class="row"><small>운영 시간</small><span>{esc(c["hours"])}</span></div>
        <div class="row"><small>정차 노선</small><div class="chips"><span class="chip">{esc(c["city"])} 출발</span><span class="chip">셔틀</span><span class="chip">순환</span></div></div>
        <div class="row"><a href="https://www.openstreetmap.org/?mlat={lat}&amp;mlon={lon}#map={18}/{lat}/{lon}" target="_blank" rel="noopener">큰 지도에서 보기</a></div></div>""")


def home_service():
    md(f"""<div id="service" class="sec"><div class="split"><div class="photo-wrap"><div class="photo" role="img" aria-label="뀨 스탑 외관"></div></div>
    <div>{nextstop(2, "서비스 안내")}<h2>버스를 기다리는 시간이 쉼이 되는 곳</h2>
    <p class="lead">뀨 스탑은 2층 통유리 라운지와 1층 카페로 이루어진 버스 대기 공간이에요. 비 오는 날에도, 추운 아침에도 따뜻하게 기다릴 수 있어요.</p>
    <ul class="feat"><li><i>[[bus]]</i><div><b>버스 정류장과 바로 연결</b><span>승차장 옆이라 출발 직전까지 편하게 쉴 수 있어요.</span></div></li>
    <li><i>[[coffee]]</i><div><b>카페 음료 사전 주문</b><span>도착하기 전에 미리 주문해 두면 바로 받을 수 있어요.</span></div></li>
    <li><i>[[sun]]</i><div><b>노을이 보이는 통유리 라운지</b><span>2층에서 하늘을 보며 기다릴 수 있어요.</span></div></li></ul></div></div></div>""")


def home_inside():
    def pic(name, alt, lv):
        b = img(name, 1100)
        tag = f'<img src="data:image/jpeg;base64,{b}" alt="{alt}">' if b else f'<div class="ph" role="img" aria-label="{alt}"></div>'
        return f'<div class="pic">{tag}<span class="lv">{lv}</span></div>'
    md(f"""<div id="inside" class="sec">{nextstop(3, "내부 구조")}<h2>1층은 카페, 2층은 라운지</h2>
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
    out = [f'<div id="routes" class="sec">{nextstop(5, "운행 노선")}<h2>어디서든 학교까지</h2>'
           '<p class="lead">지역별 통학 노선과 캠퍼스 셔틀이에요. 버스 예약 화면에서 등교·하교·순환을 골라 예약하세요.</p>']
    for k, c, desc in groups:
        if k == "셔틀":
            cards = [("논산 → 대전", "순환"), ("대전 → 논산", "순환")]
        else:
            cards = [(n, "등교 · 하교") for n, kk in REGIONAL if kk == k]
            if k == "주중":
                cards.append(("대전 순환", "순환"))
        out.append(f'<div class="rgroup"><h3>{k}</h3><span>{desc}</span></div><div class="rgrid">')
        out += [f'<div class="rc {c}"><span class="k {c}">{k}</span><b>{esc(n)}</b><small>{s}</small></div>' for n, s in cards]
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


def home_day():
    md(f"""<div id="day" class="sec">{nextstop(4, "이용 시간대")}<h2>하루 중 언제 와도 좋아요</h2>
    <p class="lead">아침엔 따뜻한 한 잔, 한낮엔 통유리 라운지, 해 질 무렵엔 노을을 보며 기다려요.</p>
    <div class="moods"><div class="mood m1"><b>아침</b><span>등굣길, 버스 오기 전에 따뜻한 음료 한 잔</span></div>
    <div class="mood m2"><b>한낮</b><span>통유리 라운지에서 쉬어 가는 공강 시간</span></div>
    <div class="mood m3"><b>노을</b><span>하굣길, 하늘이 물드는 걸 보며 기다리기</span></div></div></div>""")


def page_home():
    home_hero()
    home_location()
    home_service()
    md('<div class="stripe"></div>')
    home_inside()
    home_day()
    home_routes()
    home_cta()


# ───────────────────────── 버스 예약 ─────────────────────────
def page_bus():
    pagehead("버스 예약", "캠퍼스를 고르고, 원하는 좌석까지 미리 잡아 두세요. 이름만 입력하면 돼요.", "BUS · 좌석 예약")
    ss = st.session_state
    left, right = st.columns([1.2, 1], gap="large")
    with left:
        with st.container(key="resv"):
            md("""<div class="form-h"><span class="ico">[[ticket]]</span><div><h3>예약 정보 입력</h3><p>캠퍼스와 노선을 고르고 좌석을 선택해 주세요.</p></div></div>""")
            md('<div class="fs"><em>1</em>캠퍼스 · 운행 선택</div>')
            campus = st.radio("내 캠퍼스", CAMPUS_LIST, horizontal=True, key="b_campus", label_visibility="collapsed")
            routes = routes_for(campus)
            kind = st.radio("운행 구분", list(routes), horizontal=True, key="kind", label_visibility="collapsed")
            st.caption({"등교": f"{campus}로 오는 버스예요.", "하교": f"{campus}에서 출발하는 버스예요.",
                        "순환": f"{campus}에서 출발하는 셔틀·순환 버스예요."}[kind])
            if not routes[kind]:
                st.info("이 캠퍼스에는 해당 구분의 노선이 아직 없어요.")
                return
            route = st.selectbox("노선", list(routes[kind]), key=f"route_{campus}_{kind}")
            rk, stops = routes[kind][route]
            md('<div class="fs"><em>2</em>탑승 정보</div>')
            c1, c2 = st.columns(2)
            time_ = c1.selectbox("출발 시간", TIMES[kind], key=f"time_{kind}")
            today = now_kst().date()
            day = c2.date_input("탑승일", value=today, min_value=today, max_value=today + timedelta(days=30),
                                format="YYYY-MM-DD", key="b_day")
            if rk in DAYS:
                st.caption("주중 노선은 월~금에 운행해요." if rk == "주중" else "주말 노선은 금·토·일에 운행해요.")
            c3, c4 = st.columns(2)
            board = c3.selectbox("승차 정류장", stops[:-1], key=f"board_{campus}_{route}")
            alight = c4.selectbox("하차 정류장", stops[stops.index(board) + 1:], key=f"alight_{campus}_{route}_{board}")
            md('<div class="fs"><em>3</em>좌석 선택</div>')
            day_s = f"{day:%Y-%m-%d}"
            taken = taken_seats(campus, kind, route, day_s, time_)
            free = [x for x in SEATS if x not in taken]
            holder = st.empty()
            seat = None
            if free:
                seat = st.pills("좌석", free, selection_mode="single", label_visibility="collapsed",
                                key=f"seat_{ss.get('nonce', 0)}_{campus}_{kind}_{route}_{day_s}_{time_}")
            else:
                st.error("이 시간은 만석이에요. 다른 시간을 선택해 주세요.")
            holder.markdown(squash(seat_map(taken, seat)), unsafe_allow_html=True)
            md('<div class="fs"><em>4</em>예약자</div>')
            name = st.text_input("이름", placeholder="홍길동", max_chars=20, key="b_name")
            if st.button("좌석 예약하기", type="primary", key="b_go"):
                if not name.strip():
                    st.error("이름을 입력해 주세요.")
                elif rk in DAYS and day.weekday() not in DAYS[rk]:
                    st.error(f"{route}은(는) {'월~금' if rk == '주중' else '금·토·일'}에만 운행해요. 탑승일을 바꿔 주세요.")
                elif not seat:
                    st.error("좌석을 선택해 주세요.")
                elif seat in taken_seats(campus, kind, route, day_s, time_):
                    st.error("방금 다른 분이 이 좌석을 예약했어요. 다른 좌석을 골라 주세요.")
                else:
                    code = f"KYU-{day:%y%m%d}-{rand4()}"
                    row = dict(code=code, created_at=now_kst().isoformat(timespec="seconds"), campus=campus, kind=kind,
                               route=route, ride_date=day_s, depart_time=time_, name=name.strip(),
                               board_stop=board, alight_stop=alight, seat=seat, status="예약")
                    try:
                        db_insert("reservations", row)
                    except Exception as e:
                        st.error(f"저장에 실패했어요. {explain(e)}")
                    else:
                        ss["ticket"] = dict(code=code, kind=kind, route=route, campus=campus, seat=seat, time=time_, day=day_s,
                                            when=f"{day.month}월 {day.day}일 ({WEEKDAY_KO[day.weekday()]}) {time_}",
                                            name=name.strip(), board=board, alight=alight, stops=stops)
                        ss["nonce"] = ss.get("nonce", 0) + 1
                        st.toast("예약이 완료됐어요!", icon="✅")
                        st.rerun()
    with right:
        t = ss.get("ticket")
        gone = ss.pop("b_cancelled", None)
        if gone and not t:
            st.success(f"예약({gone})을 취소했어요. 그 좌석은 다른 분이 다시 예약할 수 있어요.")
        if t:
            md(code_warning("bus"))
            pass_card("예약이 완료됐어요", t["code"], esc(f"{t['campus']} · {t['kind']} · {t['route']}"),
                      timeline(t["stops"], t["board"], t["alight"])
                      + f'<dl><dt>탑승</dt><dd>{esc(t["when"])}</dd><dt>좌석</dt><dd>{esc(t["seat"])}</dd><dt>이름</dt><dd>{esc(t["name"])}</dd></dl>',
                      ok=True, note="탑승할 때 학생증과 예약번호를 보여 주세요. 예약번호는 음료 주문·예약 조회 화면에서도 쓸 수 있어요. 취소는 아래 버튼이나 ‘예약 조회’ 탭에서 할 수 있어요.")
            st.code(t["code"], language=None)
            if bus_cancelable(t["day"], t["time"]):
                cancel_box("reservations", t["code"], "이 예약 취소하기", "bk", "ticket", "b_cancelled")
            st.button("다른 좌석 예약하기", key="b_new", on_click=clear_key, args=("ticket",))
        else:
            warn = ""
            if rk in DAYS and day.weekday() not in DAYS[rk]:
                warn = '<span class="warnchip">이 날은 운행하지 않아요. 탑승일을 바꿔 주세요.</span>'
            pass_card("승차권 미리보기", esc(route), f"{day.month}월 {day.day}일 ({WEEKDAY_KO[day.weekday()]}) · {time_}",
                      timeline(stops, board, alight)
                      + f'<dl><dt>캠퍼스</dt><dd>{esc(campus)}</dd><dt>구분</dt><dd>{esc(kind)}</dd><dt>좌석</dt><dd>{esc(seat) if seat else "선택 전"}</dd>'
                        f'<dt>이름</dt><dd>{esc(name.strip()) or "입력 전"}</dd></dl>{warn}')
            md("""<ul class="notes"><li>탑승일은 오늘부터 30일 뒤까지 고를 수 있어요.</li>
            <li>같은 시간·노선에서 이미 예약된 좌석은 회색으로 표시돼요.</li>
            <li>탑승할 때 학생증과 예약번호를 보여 주세요. 예약번호가 없으면 탈 수 없으니 꼭 저장해 두세요.</li><li>출발 10분 전까지 정류장에 도착해 주세요.</li></ul>""")


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
    pagehead("음료 주문", "버스를 기다리는 동안 마실 음료를 미리 주문해 두세요. 도착하면 1층 카운터에서 받아요.", "CAFE · 음료 주문")
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
            gone = st.session_state.pop("d_cancelled", None)
            if gone and not order:
                st.success(f"주문({gone})을 취소했어요.")
            if order:
                md(code_warning("drink"))
                rows = "".join(f'<div><span>{esc(n)}</span><span>{esc(q)}</span></div>' for n, q in order["lines"])
                pass_card("주문이 접수됐어요", order["code"], esc(f"{order['how']} · {order['when']}"),
                          f'<div class="ol">{rows}</div><div class="tot"><span>합계</span><b>{won(order["total"])}</b></div>'
                          f'<dl><dt>이름</dt><dd>{esc(order["name"])}</dd></dl>',
                          ok=True, note="1층 카운터에서 주문번호를 말씀해 주세요. 결제는 수령할 때 카운터에서 해요. 취소는 아래 버튼이나 ‘예약 조회’ 탭에서 할 수 있어요.")
                st.code(order["code"], language=None)
                cancel_box("orders", order["code"], "이 주문 취소하기", "dk", "d_ticket", "d_cancelled")
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
                        code = f"DR-{now_kst():%H%M}-{rand4()}"
                        bus_c = bus.strip().upper()
                        try:
                            if bus_c and not db_select("reservations", code=bus_c):
                                st.error("입력한 버스 예약번호를 찾을 수 없어요. 비우거나 다시 확인해 주세요.")
                            else:
                                db_insert("orders", dict(code=code, created_at=now_kst().isoformat(timespec="seconds"), name=name.strip(),
                                                         items=items_txt, total=total, pickup_how=how, pickup_when=when,
                                                         bus_code=bus_c, status="접수"))
                                st.session_state["d_ticket"] = dict(code=code, name=name.strip(), lines=lines, total=total, how=how, when=when)
                                st.session_state["cart"] = []
                                st.rerun()
                        except requests.RequestException as e:
                            st.error(f"저장에 실패했어요. {explain(e)}")
                if cart:
                    st.button("장바구니 비우기", key="clr", on_click=clear_cart)


# ───────────────────────── 예약 조회 · 취소 / 관리자 ─────────────────────────
def show_lookup(code: str, name: str):
    table = "orders" if code.startswith("DR-") else "reservations"
    try:
        rows = db_select(table, code=code)
    except Exception as e:
        st.error(f"조회에 실패했어요. {explain(e)}")
        return
    row = rows[0] if rows and str(rows[0]["name"]).strip() == name else None
    if not row:
        st.error("일치하는 내역을 찾을 수 없어요. 번호와 이름을 다시 확인해 주세요.")
        return
    done = row["status"] == "취소"
    if table == "reservations":
        pass_card(f"버스 예약 · {row['status']}", row["code"], esc(f"{row['campus']} · {row['kind']} · {row['route']}"),
                  f'<dl><dt>탑승</dt><dd>{esc(row["ride_date"])} {esc(row["depart_time"])}</dd>'
                  f'<dt>구간</dt><dd>{esc(row["board_stop"])} → {esc(row["alight_stop"])}</dd>'
                  f'<dt>좌석</dt><dd>{esc(row["seat"])}</dd><dt>이름</dt><dd>{esc(row["name"])}</dd></dl>', ok=not done)
        if not done:
            when = datetime.fromisoformat(f'{row["ride_date"]} {row["depart_time"]}').replace(tzinfo=KST)
            if when > now_kst():
                cancel_box("reservations", row["code"], "예약 취소하기", "lc")
            else:
                st.caption("이미 지난 예약은 취소할 수 없어요.")
    else:
        pass_card(f"음료 주문 · {row['status']}", row["code"], esc(f"{row['pickup_how']} · {row['pickup_when']}"),
                  f'<div class="ol"><div><span>{esc(row["items"])}</span></div></div>'
                  f'<div class="tot"><span>합계</span><b>{won(int(row["total"] or 0))}</b></div>', ok=not done)
        if not done:
            cancel_box("orders", row["code"], "주문 취소하기", "lc")


def show_df(df):
    try:
        st.dataframe(df, width="stretch", hide_index=True)      # 최신 Streamlit
    except Exception:
        st.dataframe(df, use_container_width=True, hide_index=True)   # 구버전


def admin_panel():
    with st.expander("관리자"):
        try:
            pw = str(st.secrets["admin"]["password"])
        except Exception:
            st.info("관리자 비밀번호가 아직 없어요. Secrets에 [admin] password 를 추가해 주세요.")
            return
        inp = st.text_input("비밀번호", type="password", key="adm_pw")
        if not inp:
            return
        if not hmac.compare_digest(inp.encode(), pw.encode()):
            st.error("비밀번호가 맞지 않아요.")
            return
        cfg = _sb()
        st.caption("저장 방식: " + ("Supabase (영구 저장)" if cfg else "임시 CSV (앱이 재시작되면 사라져요)"))
        if cfg:
            kind = _key_kind(cfg[1])
            ok_kind = kind in ("secret", "service_role")
            (st.success if ok_kind else st.error)(
                f"저장 키 종류: {kind} " + ("— 정상이에요." if ok_kind else "— 공개용 키예요. secret(service_role) 키로 바꿔야 저장돼요."))
            if st.button("연결 테스트", key="adm_test"):
                try:
                    db_select("reservations")
                    db_select("orders")
                    st.success("Supabase 연결과 표(reservations, orders)가 모두 정상이에요.")
                except Exception as e:
                    st.error(explain(e))
                    st.code(str(e))
        try:
            res, orders = db_select("reservations"), db_select("orders")
        except Exception as e:
            st.error(f"데이터를 불러오지 못했어요. ({e})")
            return
        active = [r for r in res if r["status"] == "예약"]
        m = st.columns(3)
        m[0].metric("유효 예약", len(active))
        m[1].metric("오늘 탑승", sum(r["ride_date"] == f"{now_kst():%Y-%m-%d}" for r in active))
        m[2].metric("접수 주문", sum(o["status"] == "접수" for o in orders))
        a, b = st.tabs(["버스 예약", "음료 주문"])
        with a:
            camp = st.selectbox("캠퍼스", ["전체"] + CAMPUS_LIST, key="adm_campus")
            df = pd.DataFrame([r for r in res if camp == "전체" or r["campus"] == camp])
            show_df(df)
            if not df.empty:
                st.download_button("CSV 내려받기", df.to_csv(index=False).encode("utf-8-sig"), "reservations.csv", "text/csv")
        with b:
            df = pd.DataFrame(orders)
            show_df(df)
            if not df.empty:
                st.download_button("CSV 내려받기", df.to_csv(index=False).encode("utf-8-sig"), "orders.csv", "text/csv", key="dl_o")
        codes = [r["code"] for r in active] + [o["code"] for o in orders if o["status"] == "접수"]
        if codes:
            c1, c2 = st.columns([3, 1], vertical_alignment="bottom")
            target = c1.selectbox("취소 처리할 번호", codes, key="adm_cancel")
            if c2.button("취소 처리", key="adm_go"):
                try:
                    db_update("orders" if target.startswith("DR-") else "reservations", target, status="취소")
                    st.rerun()
                except Exception as e:
                    st.error(explain(e))


def page_lookup():
    pagehead("예약 조회", "예약번호(KYU-…)나 주문번호(DR-…)와 이름으로 확인하고, 필요하면 취소할 수 있어요.", "TICKET · 예약 조회")
    _, mid, _ = st.columns([1, 2.2, 1])
    with mid:
        with st.container(key="lookup"):
            code = st.text_input("예약번호 · 주문번호", placeholder="KYU-261117-AB12", key="l_code")
            name = st.text_input("이름", placeholder="홍길동", max_chars=20, key="l_name")
            st.caption("번호를 잊어버리면 조회도 취소도 할 수 없어요. 예약번호·주문번호는 꼭 저장해 두세요.")
            if st.button("조회하기", type="primary", key="l_go"):
                st.session_state["lk"] = (code.strip().upper(), name.strip())
            q = st.session_state.get("lk")
            if q and q[0]:
                show_lookup(*q)
    md('<div class="sp"></div>')
    admin_panel()


# ───────────────────────── 실행 ─────────────────────────
# 다른 화면에 갔다 와도 입력한 값이 사라지지 않게 유지
for _k in list(st.session_state.keys()):
    if _k in ("kind", "b_day", "b_name", "b_campus", "map_campus", "d_cat", "d_name", "d_how", "d_when", "d_bus", "qty", "l_code", "l_name") \
            or _k.startswith(("route_", "time_", "board_", "alight_", "t_", "shot_")):
        st.session_state[_k] = st.session_state[_k]

if "page" not in st.session_state:   # 주소 끝에 ?p=bus 또는 ?p=drink 를 붙이면 바로 그 화면이 열려요 (QR코드용)
    st.session_state["page"] = QUERY_TO_PAGE.get(st.query_params.get("p", "home"), "홈")

md(CSS.replace("__EXT__", f"url(data:image/jpeg;base64,{img('exterior.jpg', 1920)})" if img("exterior.jpg", 1920)
               else "linear-gradient(135deg,#17694F,#9CCBEA 62%,#F3A6B8)").replace("__NAVICONS__", nav_icon_css()))
topnav()
page = st.session_state["page"]
try:
    st.query_params["p"] = PAGE_TO_QUERY.get(page, "home")
except Exception:
    pass

{"홈": page_home, "버스 예약": page_bus, "음료 주문": page_drink, "예약 조회": page_lookup}[page]()
footer()

if st.session_state.get("_last_page") != page:     # 화면을 바꾸면 맨 위로 이동
    st.session_state["_last_page"] = page
    _js = "<script>try{const d=window.parent.document;const m=d.querySelector('[data-testid=stMain]')||d.querySelector('section.main');if(m)m.scrollTo({top:0});}catch(e){}</script>"
    try:
        if hasattr(st, "iframe"):          # 최신 Streamlit
            st.iframe(_js, height=1)
        else:                              # 구버전 호환
            components.html(_js, height=0)
    except Exception:
        pass
