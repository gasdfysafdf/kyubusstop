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
import threading
from datetime import date, datetime, timedelta, timezone
from html import escape as esc
from pathlib import Path

try:                       # QR 그림을 만드는 도구 (없으면 QR만 빠지고 나머지는 그대로 동작해요)
    import segno
except Exception:
    segno = None
try:                       # 직원용 카메라 QR 읽기 도구 (없으면 번호 직접 입력만 써요)
    import cv2
    import numpy as np
    from PIL import Image
except Exception:
    cv2 = None
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
SEAT_ROWS = 11                                                         # 좌석 줄 수 (한 줄 4석 → 44석)

# ── 통학버스 시간표 (2026학년도 시간표 사진 기준) ──────────────────────────────
# 한 노선은  R(구분, 분류, 노선이름, [ (정류장 이름, 시간), ... ], days=운행요일, note=안내문)  으로 적어요.
#  · 구분: "등교"(캠퍼스로 오는 버스) / "하교"(캠퍼스에서 출발) / "순환"(셔틀·순환)
#  · 시간이 하나면 하루 1회 운행, 시간을 [ ] 안에 여러 개 적으면 회차별 시간이에요.
#  · 정류장 이름에 "대전캠퍼스"나 "논산캠퍼스"가 들어 있으면 그 캠퍼스 정류장으로 인식해요. 호차 번호는 화면에 표시하지 않아요.
WEEKDAYS, FRI, SUN = {0, 1, 2, 3, 4}, {4}, {6}                       # 운행 요일 (월=0 … 일=6)
CAMPUS_KEYS = {"대전캠퍼스": "메디컬캠퍼스", "논산캠퍼스": "글로벌캠퍼스"}   # 시간표 속 이름 → 이 앱의 캠퍼스 이름


def R(kind, cls, title, stops, days=None, note=""):
    names, times, camp = [], [], []
    for s_ in stops:
        names.append(s_[0])
        times.append([s_[1]] if isinstance(s_[1], str) else list(s_[1]))
        if len(s_) > 2:
            camp.append({s_[2]} if isinstance(s_[2], str) else set(s_[2]))
        else:
            camp.append({v for k, v in CAMPUS_KEYS.items() if k in s_[0]})
    if len({len(t) for t in times}) != 1:
        raise ValueError(f"[{title}] 정류장마다 시간 개수가 달라요. 시간표를 다시 확인해 주세요.")
    serves = set().union(*camp)                                   # 이 노선이 서는 캠퍼스들
    shared = "각 1대씩" not in note + "".join(names)               # '각 1대씩'이면 캠퍼스마다 다른 버스, 아니면 한 대를 같이 타요
    return dict(kind=kind, cls=cls, title=title, stops=names, times=times, campus_at=camp, days=days, note=note,
                serves=serves, shared=shared)


def _m(t):                      # "08:10" → 분
    return int(t[:2]) * 60 + int(t[3:])


def _t(m):                      # 분 → "08:10"
    return f"{m // 60:02d}:{m % 60:02d}"


def _every(a, b, step):         # a 부터 b 까지 step 분 간격 출발 시각들
    return [_t(m) for m in range(_m(a), _m(b) + 1, step)]


_LOOP_START = _every("08:00", "09:50", 10) + _every("10:00", "12:00", 20) + _every("13:00", "18:20", 20)
_LOOP = [("암센터(간호대학) 앞 출발", 0, "메디컬캠퍼스"), ("죽헌정보관 정문 건너편", 1), ("관저시외버스 승차장 앞", 3),
         ("파워존 앞 승강장 옆", 4), ("시외버스 하차장 (관저문예회관)", 5), ("암센터(간호대학) 앞 도착", 12, "메디컬캠퍼스")]

BUS_ROUTES = [
    # ── 캠퍼스 셔틀 ──
    R("순환", "셔틀", "셔틀 · 논산 → 대전", [
        ("논산캠퍼스 승강장", ["08:00", "09:00", "11:00", "13:00", "14:40", "15:40", "16:40", "17:40", "18:40", "19:40", "21:00"]),
        ("연산 신 사거리", ["08:15", "09:15", "11:15", "13:15", "14:55", "15:55", "16:55", "17:55", "18:55", "19:55", "21:15"]),
        ("양정삼거리 버스정류장", ["08:25", "09:25", "11:25", "13:25", "15:05", "16:05", "17:05", "18:05", "19:05", "20:05", "21:25"]),
        ("LG전자베스트샵 관저점 앞", ["08:35", "09:35", "11:35", "13:35", "15:15", "16:15", "17:15", "18:15", "19:15", "20:15", "21:35"]),
        ("대전캠퍼스 도착", ["08:40", "09:40", "11:40", "13:40", "15:20", "16:20", "17:20", "18:20", "19:20", "20:20", "21:40"]),
    ]),
    R("순환", "셔틀", "셔틀 · 대전 → 논산", [
        ("대전캠퍼스 승강장", ["07:10", "07:40", "09:30", "10:30", "11:30", "12:30", "14:30", "16:30", "17:30", "18:30"]),
        ("하이마트 관저점 앞", ["07:13", "07:43", "09:33", "10:33", "11:33", "12:33", "14:33", "16:33", "17:33", "18:33"]),
        ("진잠타운 103동 옆", ["07:14", "07:44", "09:34", "10:34", "11:34", "12:34", "14:34", "16:34", "17:34", "18:34"]),
        ("양정삼거리 주유소 옆", ["07:24", "07:54", "09:40", "10:40", "11:40", "12:40", "14:40", "16:40", "17:40", "18:40"]),
        ("연산사거리 추어탕집 앞", ["07:30", "08:00", "09:46", "10:46", "11:46", "12:46", "14:46", "16:46", "17:46", "18:46"]),
        ("논산캠퍼스 도착", ["07:48", "08:20", "10:10", "11:10", "12:10", "13:10", "15:10", "17:10", "18:10", "19:10"]),
    ]),
    # ── 주중 등교 (1~9호차) ──
    R("등교", "주중", "주중 · 선사 → 건양대학교", [
        ("선사병원 옆 선사유적지 출발", "07:20"), ("황실아파트 상가 앞 / (서대전고 약 3분)", "07:21"),
        ("월평동 하나로APT 버스정류장 옆 / (서대전고 약 5분)", "07:22"), ("이마트트레이더스 건너편 (은평공원쪽)", "07:24"),
        ("충대정문 네거리 GS주유소 앞", "07:27"), ("유성온천역 3번 출구 할매낙지 앞 / (유성고 약 10분)", "07:30"),
        ("도안동 신한은행 LG 유플러스 앞 / (대전예고 약 10분)", "07:32"), ("도안 트리플시티 917동 옆 버스정류장 / (대전도안고 약 10분)", "07:33"),
        ("도안 현대아이파크 205동 옆 버스정류장 / (대전도안고 약 10분)", "07:35"), ("목원대 네거리 버스정류장 / (대전체고 약 10분)", "07:38"),
        ("도안 수.목.토 버스정류장(버스전용차선)", "07:42"), ("도안 베르디움 버스정류장(버스전용차선)", "07:44"),
        ("가수원 육교 밑", "07:47"), ("대전캠퍼스 도착 (출발)", "07:50"), ("하이마트 관저점 앞", "07:55"),
        ("진잠타운 103동 옆 도로변 버스정류장 / (대전도시과학고 약 10분)", "07:57"), ("양정삼거리 주유소 옆", "08:07"),
        ("연산사거리 남원추어탕집 앞", "08:20"), ("논산캠퍼스 도착", "08:40"),
    ], WEEKDAYS),
    R("등교", "주중", "주중 · 삼천중학교 → 건양대학교", [
        ("삼천중학교 정문 앞", "07:40"), ("탄방중학교 정문 앞 / (충남고 약 1분)", "07:43"), ("탄방동 한양아파트 상가 앞", "07:45"),
        ("탄방동 대전고용복지센터 건너편 정류장(구 홈플러스)", "07:48"), ("둔산동 한밭주유소 옆 육교 밑", "07:50"),
        ("경성큰마을네거리 대한제분 앞 시내버스 정류장 / (둔산여고 약 15분)", "07:52"), ("갈마3거리 서부농협 앞 / (둔산여고 약 10분)", "07:55"),
        ("월평타운 건너편 버스정류장(대전일보 정류장)", "07:57"), ("원신흥동 신안 인스빌 앞 버스정류장(버스 전용차선)", "08:01"),
        ("목원대 입구(옥녀봉사거리) 버스정류장(버스 전용차선) / (대전도안고 약 10분)", "08:03"),
        ("도안 트리플시티 레이크포레 버스정류장(버스전용차선)", "08:04"), ("도안 수·목·토 버스정류장(버스 전용차선)", "08:05"),
        ("도안 베르디움 버스정류장(버스 전용차선)", "08:07"), ("가수원 육교 계단 옆", "08:10"), ("대전캠퍼스 도착 (출발)", "08:15"),
        ("하이마트 관저점 앞", "08:20"), ("진잠타운 103동 옆 도로변 버스정류장 / (대전도시과학고 약 10분)", "08:22"),
        ("양정삼거리 주유소 옆", "08:32"), ("연산사거리 추어탕집 앞", "08:40"), ("논산캠퍼스 도착", "09:05"),
    ], WEEKDAYS),
    R("등교", "주중", "주중 · 대전복합터미널 → 건양대학교", [
        ("복합터미널 건너편 호텔 선샤인 앞", "07:40"), ("가양도서관 버스정류장(영보교통 앞)", "07:41"),
        ("가양동 현대서비스 옆 현대자동차대리점 앞", "07:42"), ("우송대 서캠퍼스 동문앞 버스정류장", "07:43"),
        ("우송대학교 입구 한밭신협 앞 / (우송고 약 5분)", "07:45"), ("대동역1번 출구 대동크리닉 앞 / (대전여고 약 6분)", "07:47"),
        ("신흥마을 아파트 102동 옆 후문 앞", "07:50"), ("판암역 2번 출구 앞", "07:53"), ("논산캠퍼스 도착", "08:50"),
    ], WEEKDAYS),
    R("등교", "주중", "주중 · 송강 → 건양대학교", [
        ("송강동 북부여성가족원 버스정류장", "07:15"), ("롯데마트 옆 장배기공원 앞", "07:16"), ("중일고 정문 옆 네오미아 608동 앞 / (중일고 약 1분)", "07:20"),
        ("대덕테크노 우림필유 1108동 앞 / (용산고 약 3분)", "07:21"), ("용산동 호반써밋 307동 건너편 버스정류장", "07:24"),
        ("전민동 청구나래 아파트상가 앞 정류장(전민중학교 버스정류장)", "07:28"), ("전민동 삼성정밀연구소 앞 버스정류장 / (전민고 약 10분)", "07:30"),
        ("만년동 초원아파트 옆 도로변 / (만년고 약 10분)", "07:40"), ("갤러리아백화점 옆 우리은행 앞 / (둔산여고 약 10분)", "07:42"),
        ("경성큰마을 127동 건너편 대전평안교회 앞 / (둔원고 약 10분)", "07:45"), ("안골네거리 옆 노루페인트 앞 정류장", "07:47"),
        ("서대전여고 후문 앞 / (서대전여고 약 1분)", "07:50"), ("도마 경남아파트 103동 앞 도로변", "07:52"), ("대전캠퍼스 도착 (출발)", "08:10"),
        ("하이마트 관저점 앞", "08:15"), ("진잠타운 103동 옆 도로변 버스정류장 / (대전도시과학고 약 10분)", "08:17"),
        ("양정삼거리 주유소 옆", "08:27"), ("연산사거리 추어탕집 앞", "08:35"), ("논산캠퍼스 도착", "09:00"),
    ], WEEKDAYS),
    R("등교", "주중", "주중 · 신탄진 → 건양대학교", [
        ("신탄진역 옆 CU편의점 앞 / (신탄진고 약 15분)", "07:20"), ("신탄진 시온가스충전소 앞", "07:21"), ("신탄진 대원아파트 앞", "07:22"),
        ("신탄진 한일병원 앞", "07:23"), ("와동 신대주공상가 옆 띠울석갈비 앞", "07:29"), ("읍내동 현대Ⓐ상가 현대의원 앞", "07:34"),
        ("중리동 국세청 앞", "07:36"), ("중리동 중앙병원 앞", "07:40"), ("중리동 반도정형외과 앞", "07:42"), ("중리네거리 GS유원주유소 앞", "07:44"),
        ("용전동 국토관리청 정문 앞", "07:46"), ("성남네거리 감리교회 옆 광장셀프주유소 앞", "07:50"), ("대전역동광장 오일뱅크주유소 앞", "07:52"),
        ("논산캠퍼스 도착", "08:50"),
    ], WEEKDAYS),
    R("등교", "주중", "주중 · 북세종 → 건양대학교", [
        ("나성동 주택금융공사(세종)앞 버스정류장 / (새롬고 5분, 대성고 5분)", "07:50"), ("홈플러스 세종점 맞은편 버스정류장", "07:58"),
        ("범지기마을 4단지 버스정류장", "08:00"), ("범지기마을 1단지 버스정류장 / (아름고 5분)", "08:02"),
        ("고운동 고운유치원앞 버스정류장 / (고운고 5분)", "08:05"), ("논산캠퍼스 도착", "08:50"),
    ], WEEKDAYS),
    R("등교", "주중", "주중 · 남세종·공주 → 건양대학교", [
        ("[세종] 소담동 (새샘마을 3단지앞) 지선버스 정류장 / (소담고 3분)", "07:15"), ("[세종] 세종시청 (시의회,교육청,세무서) 지선버스 정류장", "07:17"),
        ("[세종] 보람동 (호려울3단지앞) 지선버스 정류장 / (보람고 1분)", "07:19"), ("[세종] 대평동 (해들마을) 지선버스 정류장", "07:20"),
        ("[세종] 세종고속시외버스 터미널버스정류장 / (주비뜰근린공원앞)", "07:22"), ("[세종] 가람동 (이마트앞) 지선버스 정류장 / (한솔고 10분)", "07:27"),
        ("[공주] 공주신월초등학교 옆 해성빌딩 앞", "07:47"), ("[공주] 공주 이솔아파트 건너편", "07:48"), ("[공주] KB국민은행(신관점) 건너편 박외과 앞", "07:50"),
        ("[공주] 신관동 공주가축약품 앞", "07:52"), ("[공주] 공주 구 터미널 옆 MG새마을금고 앞", "08:00"),
        ("[공주] 이디야커피(중동점) 옆 로이젠신사복 앞 / (공주고, 영명고 5분)", "08:02"), ("[공주] LG베스트샵(금강옥룡점) 앞", "08:05"),
        ("[공주] 공주대교 건너 아이젠아파트 정문", "08:08"), ("논산캠퍼스 도착", "09:00"),
    ], WEEKDAYS),
    R("등교", "주중", "주중 · 아산·천안 → 건양대학교", [
        ("배방역 1번출구 건너편 버스정류장 / (배방고 약 30분)", "07:10"), ("천안아산역 아산시패션1광장 버스정류장 / (상용고 약 15분)", "07:20"),
        ("불당동 쌍용도서관사거리 타이어테크 앞 / (월봉고 약 10분)", "07:22"), ("천안 이마트 건너편 신한은행 쌍용지점 앞", "07:25"),
        ("천안역 서부역 입구삼거리 부동산 앞", "07:30"), ("천안시외버스터미널 건너편 농협(다이소) 앞 / (천안공고 약 10분)", "07:35"),
        ("교보사거리 LG베스트샵 건너편(롯데리아 앞) / (천안제일고 약 5분)", "07:42"), ("익수당한의원 건너편(삼용교 앞) / (청수고 약 15분)", "07:45"),
        ("청룡동 골프존파크(청룡구성점) 앞 / (천안여고 약 25분)", "07:50"), ("논산캠퍼스 도착", "08:50"),
    ], WEEKDAYS),
    R("등교", "주중", "주중 · 청주·오송 → 건양대학교", [
        ("청주시청 정문 버스정류장 앞 / (청주대성고 약 5분)", "06:40"), ("청주종합운동장 삼거리옆 네파 앞 / (청주중앙여고 약 10분)", "06:45"),
        ("청주고 정문 버스정류장 / (청주고 약 1분)", "06:49"), ("복대동 죽천교 버스정류장", "06:54"),
        ("청주시외버스터미널 사거리옆 서청주 새마을금고 요양원 앞", "07:00"), ("오송역 6번출구 앞 / (오송고 약 20분)", "07:20"),
        ("세종도램마을 지선버스승강장 / (도담고 약 10분) (도램마을 1105동 건너편, 세종중앙농협 앞) 출발", "07:40"),
        ("정부세종청사 남측 지선버스승강장 (행정안전부 별관, 올리브영 앞) / (세종대성고 약 10분)", "07:44"),
        ("나성동 지선버스승강장(명동프라자, 웰비뇨기과 앞) / (새롬고 약 15분)", "07:48"), ("세종시외버스 터미널 앞 건너편 버스정류장 / (보람고 약 20분)", "07:55"),
        ("대전캠퍼스 도착", "08:15"),
    ], WEEKDAYS, note='※ 논산캠퍼스까지 가려면 "주중 · 삼천중학교" 07:40 차량을 예약해 주세요.'),
    # ── 주중 하교 / 순환 ──
    R("하교", "주중", "주중 · 건양대학교 → 온천역", [
        ("대전캠퍼스 간호대학 맞은편 (암센터 정문) - 출발", ["16:05", "18:05"]), ("대전캠퍼스 죽헌정보관 앞 승강장", ["16:06", "18:06"]),
        ("도솔초등학교 버스정류장(베르디움(a)101동 맞은편)", ["16:09", "18:09"]), ("수·목·토(a) 버스정류장(버스전용차선)", ["16:12", "18:12"]),
        ("아이파크(a) 버스정류장(버스전용차선)", ["16:14", "18:14"]), ("어울림하트(a) 네거리", ["16:16", "18:16"]),
        ("도안고 정문 앞 건너편", ["16:18", "18:18"]), ("트리플시티 9단지 승강장", ["16:20", "18:20"]), ("롯데리아", ["16:22", "18:22"]),
        ("도안마을 1단지 정문", ["16:23", "18:23"]), ("유성온천역 1번 출구 (하차)", ["16:35", "18:35"]),
    ], WEEKDAYS, note="방학 중에는 운행하지 않아요."),
    R("순환", "주중", "주중 · 대전 순환", [(n, [_t(_m(a) + o) for a in _LOOP_START]) + tuple(c) for n, o, *c in _LOOP], WEEKDAYS,
      note="오전 08:00~09:50은 10분, 10:00~12:00과 오후 13:00~18:20은 20분 간격이에요. 정류장별 시간은 오후 첫차(13:00) 기준이에요. 공휴일에는 운행하지 않아요."),
    # ── 주말: 금요일 하교 방향 ──
    R("하교", "주말", "주말 · 건양대학교 → 서울 (금요일)", [
        ("대전캠퍼스 / 논산캠퍼스 통학버스 승강장에서 각 1대씩 출발", "18:00"), ("안성 IC입구 (현대오일뱅크주유소 앞)", "19:10"),
        ("경부고속도로(상행) 신갈 간이버스정류장 (수원학생)", "19:30"), ("경부고속도로(상행) 죽전 간이버스정류장 (용인학생)", "19:40"),
        ("서울 교대역 9번출구 KEB하나은행 앞 도착", "20:00"),
    ], FRI),
    R("하교", "주말", "주말 · 건양대학교 → 인천·경기 (금요일)", [
        ("대전캠퍼스 / 논산캠퍼스 통학버스 승강장에서 각 1대씩 출발", "18:00"), ("경부고속도로(상행) 오산IC 지나 오산쉼터 간이버스정류장", "19:30"),
        ("안산터미널(성어공원) 앞 도착 / (송호고 약 10분)", "20:10"), ("원인재역 5번출구 정문앞 버스정류장 도착", "20:40"),
    ], FRI),
    R("하교", "주말", "주말 · 건양대학교 → 태안·서산·당진 (금요일)", [
        ("대전캠퍼스 통학버스 승강장 출발", "17:10"), ("논산캠퍼스 통학버스 승강장 도착 / 출발", "18:00"),
        ("당진 버스터미널(농협중앙회 당진시지부 건너편 버스정류장) / (호서고 약 15분)", "19:20"), ("타이어뱅크 서당진점 건너편 / (당진고 약 15분)", "19:25"),
        ("서산의료원 석림사거리옆(신주공아파트 버스정류장) / (서산중앙고 약 7분)", "19:50"), ("태안터미널 옆 MG새마을금고 앞 도착", "20:20"),
    ], FRI),
    R("하교", "주말", "주말 · 건양대학교 → 예산·홍성·광천·보령 (금요일)", [
        ("대전캠퍼스 통학버스 승강장 출발", "17:10"), ("논산캠퍼스 통학버스 승강장 도착 / 출발", "18:00"),
        ("보령(대천)시외버스터미널 사거리", "19:00"), ("광천 단아래사거리 묘지나라 앞 / (한국K-POP고 약 10분)", "19:20"),
        ("홍성 롯데마트 건너편 코오롱스포츠앞 / (홍성여고 약 20분)", "19:40"), ("예산 수덕사 IC입구 월곡 버스 정류장 도착", "20:00"),
    ], FRI),
    # ── 주말: 일요일 등교 방향 ──
    R("등교", "주말", "주말 · 서울 → 건양대학교 (일요일)", [
        ("서울 교대역 9번출구 KEB하나은행 앞 출발", "17:00"), ("경부고속도로(하행) 죽전 간이버스정류장 (용인학생)", "17:20"),
        ("경부고속도로(하행) 신갈 간이버스정류장 (수원학생)", "17:25"), ("안성 IC입구 (현대오일뱅크주유소 앞)", "17:45"),
        ("대전캠퍼스 / 논산캠퍼스 도착", "19:00"),
    ], SUN, note="※ 대전캠퍼스행 / 논산캠퍼스행 차량이 각 1대씩 출발해요."),
    R("등교", "주말", "주말 · 인천·경기 → 건양대학교 (일요일)", [
        ("원인재역 5번출구 정문앞 버스정류장 출발 / (연수고 약 10분)", "16:30"), ("안산터미널(성어공원) 앞 / (송호고 약 10분)", "17:00"),
        ("경부고속도로(하행) 오산IC 지나 오산쉼터 간이버스정류장", "17:30"), ("대전캠퍼스 / 논산캠퍼스 도착", "19:00"),
    ], SUN, note="※ 대전캠퍼스행 / 논산캠퍼스행 차량이 각 1대씩 출발해요."),
    R("등교", "주말", "주말 · 태안·서산·당진 → 건양대학교 (일요일)", [
        ("태안터미널 옆 MG새마을금고 앞 출발", "16:00"), ("서산의료원 건너편 버스정류장 / (서산중앙고 약 7분)", "16:20"),
        ("타이어뱅크 서당진점 앞 / (당진고 약 15분)", "16:45"), ("당진 버스터미널(농협중앙회 당진시지부 앞) 도착 / (호서고 약 15분)", "16:48"),
        ("논산캠퍼스 도착 (출발)", "18:20"), ("대전캠퍼스 도착", "19:10"),
    ], SUN),
    R("등교", "주말", "주말 · 예산·홍성·광천·보령 → 건양대학교 (일요일)", [
        ("예산 수덕사 IC입구 월곡 버스 정류장 출발(홍성방향)", "16:00"), ("홍성 롯데마트옆 코오롱스포츠 건너편 / (홍성여고 약 20분)", "16:20"),
        ("광천 단아래사거리 묘지나라 건너편(광천IC방향) / (한국K-POP고 약 10분)", "16:40"), ("보령(대천)시외버스터미널 사거리 대천카센터 앞", "17:00"),
        ("논산캠퍼스 도착 (출발)", "18:00"), ("대전캠퍼스 도착", "19:00"),
    ], SUN),
    R("등교", "주말", "주말 · 대전복합터미널 → 건양대학교 (일요일)", [
        ("대전복합터미널 건너편 호텔 선샤인 앞 출발", "18:20"), ("대전역 동광장 현대오일뱅크(대동오거리방향) 앞", "18:28"), ("논산캠퍼스 도착", "19:10"),
    ], SUN),
]
# 아직 시간표를 받지 못한 노선: 방학 · 논산↔대전(16호차), 주중 1~9호차의 하교 시간표

# ── 메뉴 (메뉴판 사진 기준이에요. 가격이 바뀌면 여기만 고치면 돼요) ──
#   name 이름 / price 가격(원) / price_to 가격이 범위일 때 높은 쪽 가격 / desc 한 줄 설명
#   c 컵 색상 2개 / img 사진 파일(assets 폴더) / emoji 컵 대신 보여 줄 그림(디저트·샌드위치용)
#   ice_only ICE만 가능 / decaf 디카페인 변경 가능 / food 온도 선택 없음 / tag 배지
# ※ ICE·HOT 가능 여부와 한 줄 설명은 메뉴판에 없어서 임시로 적었어요. 실제에 맞게 고쳐 주세요.
DECAF_PRICE = 500
DECAF_LABEL = "디카페인 변경"
MENU = {
    "시그니처": [
        dict(name="KYU 양말차", price=4000, desc="귀여운 양 마시멜로가 쏙! 진하고 달콤한 시그니처 말차 라떼", c=("#B1CB86", "#80A453"),
             img="yangmatcha.png", ice_only=True, tag="시그니처"),
    ],
    "커피": [
        dict(name="아메리카노", price=2500, desc="진하게 내린 에스프레소에 물을 더한 깔끔한 한 잔", c=("#7A5440", "#432C20"), decaf=True),
        dict(name="꿀아메리카노", price=3000, desc="달콤한 꿀을 더한 아메리카노", c=("#B98A4E", "#6B4524"), decaf=True),
        dict(name="바닐라 아메리카노", price=3000, desc="바닐라 향이 은은한 아메리카노", c=("#9C7350", "#563820"), decaf=True),
        dict(name="헤이즐넛 아메리카노", price=3000, desc="고소한 헤이즐넛 향의 아메리카노", c=("#8D6446", "#4D311D"), decaf=True),
        dict(name="헛개리카노", price=3300, desc="헛개 향을 더한 이색 아메리카노", c=("#9A7A4E", "#5D4426"), decaf=True),
        dict(name="카푸치노", price=3500, desc="에스프레소 위에 부드러운 우유 거품", c=("#E3C7A2", "#B98F66"), decaf=True),
        dict(name="하우스밀크 라떼", price=3500, desc="에스프레소에 부드러운 우유를 듬뿍", c=("#DDBE9A", "#BC9068"), decaf=True),
        dict(name="바닐라 라떼", price=3800, desc="바닐라 시럽의 은은한 단맛", c=("#EBD3AA", "#CFA874"), decaf=True),
        dict(name="헤이즐넛 라떼", price=3800, desc="헤이즐넛 향이 고소한 라떼", c=("#DDBB93", "#B58A5E"), decaf=True),
        dict(name="연유 라떼", price=3800, desc="달콤한 연유를 더한 부드러운 라떼", c=("#F0DDBA", "#D4AE7C"), decaf=True),
        dict(name="카라멜마끼아또", price=3900, desc="달콤한 카라멜 향이 퍼지는 마끼아또", c=("#E0A96C", "#BC7E40"), decaf=True),
        dict(name="카페모카", price=3900, desc="초콜릿과 에스프레소의 진한 조화", c=("#A47760", "#6F4735"), decaf=True),
        dict(name="초코라떼", price=4000, desc="진한 초콜릿을 우유에 녹인 달콤한 음료", c=("#85543F", "#55321F")),
        dict(name="큐브라떼", price=4200, desc="큐브 얼음이 들어간 시원한 라떼", c=("#E6CBA6", "#BF9669"), ice_only=True, decaf=True),
        dict(name="초코젤라또 말차라떼", price=4500, desc="초코 젤라또를 올린 말차 라떼", c=("#B1CB86", "#7A5440"), ice_only=True),
    ],
    "에이드": [
        dict(name="레몬 에이드", price=3500, desc="상큼한 레몬의 시원한 에이드", c=("#FAE88F", "#F1D14F"), ice_only=True),
        dict(name="블루레몬 에이드", price=3500, desc="푸른 빛깔의 새콤한 레몬 에이드", c=("#9CCBEA", "#5FA8DA"), ice_only=True),
        dict(name="자몽 에이드", price=3500, desc="쌉싸름하고 상큼한 자몽 에이드", c=("#F9B7A6", "#F08C79"), ice_only=True),
        dict(name="청포도 에이드", price=3500, desc="달콤한 청포도의 시원한 에이드", c=("#D9EBA4", "#A8CC5F"), ice_only=True),
        dict(name="라임라임모히또", price=3800, desc="라임 향이 가득한 청량한 모히또", c=("#C9E7A3", "#8DBF5A"), ice_only=True),
        dict(name="체리콜라", price=3800, desc="체리 향을 더한 톡 쏘는 콜라", c=("#B5485A", "#6E1F2D"), ice_only=True),
        dict(name="제로 부스트 에이드", price=3900, desc="당 걱정을 덜어낸 제로 에이드", c=("#BFE3EF", "#7FC4DB"), ice_only=True),
    ],
    "티 & 아이스티": [
        dict(name="복숭아아이스티", price=2800, desc="달콤한 복숭아 향의 아이스티", c=("#FFD6B6", "#FFB68E"), ice_only=True),
        dict(name="자몽차", price=3300, desc="자몽 과육이 들어간 새콤한 차", c=("#F9B7A6", "#EE8C77")),
        dict(name="레몬차", price=3300, desc="상큼한 레몬을 우린 차", c=("#FAE88F", "#EBC945")),
        dict(name="유자차", price=3300, desc="따뜻하게 마시기 좋은 달콤 쌉싸름한 유자", c=("#F7D775", "#E3B240")),
        dict(name="유자생강차", price=3500, desc="유자에 생강을 더해 속이 따뜻해지는 차", c=("#F2CE6A", "#D9A233")),
        dict(name="사과유자차", price=3500, desc="사과와 유자를 함께 우린 차", c=("#F4D98A", "#E0B04F")),
        dict(name="상큼 리치티", price=3800, desc="리치 향이 상큼한 티", c=("#F9D3D8", "#EBA3AE")),
        dict(name="저당 골든애플 블랙티", price=3800, desc="당을 줄인 사과 향 블랙티", c=("#E7C766", "#B98A2E")),
        dict(name="허니자몽블랙티", price=3800, desc="꿀과 자몽을 더한 블랙티", c=("#F2B58C", "#C97A47")),
        dict(name="제로 레몬말차 아이스티", price=3800, desc="레몬과 말차가 만난 제로 아이스티", c=("#D7E6A0", "#9FBE5E"), ice_only=True),
    ],
    "디저트 & 스낵": [
        dict(name="쿠키 (수제 버터·초코칩)", price=2000, price_to=2500, desc="고르는 종류에 따라 가격이 달라요", emoji="🍪", food=True),
        dict(name="마카롱 (뚱카롱 기준)", price=2500, price_to=2800, desc="크림이 듬뿍 든 뚱카롱", emoji="🧁", food=True),
        dict(name="칩 (바나나칩·섭칩·칩류)", price=2000, price_to=3000, desc="고르는 종류에 따라 가격이 달라요", emoji="🥔", food=True),
        dict(name="떡 (찰떡·크림 찹쌀떡 등)", price=2200, price_to=2800, desc="고르는 종류에 따라 가격이 달라요", emoji="🍡", food=True),
        dict(name="시리얼컵 (우유·요거트 포함)", price=3500, price_to=4000, desc="우유나 요거트와 함께 먹는 시리얼", emoji="🥣", food=True),
        dict(name="아사이볼", price=5500, price_to=6500, desc="과일과 그래놀라를 올린 아사이볼", emoji="🫐", food=True),
    ],
    "샌드위치 & 랩": [
        dict(name="에그 마요 샌드위치", price=4300, desc="부드러운 계란 마요 샌드위치", emoji="🥪", food=True),
        dict(name="햄치즈 샌드위치", price=4500, desc="햄과 치즈가 든 든든한 샌드위치", emoji="🥪", food=True),
        dict(name="치킨 텐더 샌드위치", price=5200, desc="바삭한 치킨 텐더가 들어간 샌드위치", emoji="🥪", food=True),
        dict(name="불고기 또띠아 랩", price=4800, desc="불고기를 돌돌 만 또띠아 랩", emoji="🌯", food=True),
        dict(name="치킨 샐러드 랩", price=4800, desc="치킨과 채소를 돌돌 만 랩", emoji="🌯", food=True),
        dict(name="랩모닝 샌드위치 세트", price=6000, desc="든든한 아침 & 브런치 세트 · 아메리카노 + 햄치즈 샌드위치 알찬 구성!", c=("#7A5440", "#432C20"), emoji="🥪", tag="세트"),
    ],
}
PICKUP_HOW = ["매장에서 마실게요", "테이크아웃"]
PICKUP_WHEN = ["지금", "10분 후", "20분 후", "30분 후"]
# ═════════════════════════════════════════════════════════════════════════════════

WEEKDAY_KO = "월화수목금토일"


def days_text(days) -> str:
    return "월~금" if days == WEEKDAYS else "·".join(WEEKDAY_KO[d] for d in sorted(days)) + "요일"
PAGES = ["홈", "버스 예약", "음료 주문", "예약 조회"]
QR_LINKS = True   # True: QR 주소(?p=bus 등)로 들어오면 그 화면을 열어줘요 / False: 무조건 홈
QUERY_TO_PAGE = {"bus": "버스 예약", "drink": "음료 주문", "lookup": "예약 조회", "home": "홈"}
PAGE_TO_QUERY = {v: k for k, v in QUERY_TO_PAGE.items()}


def routes_for(campus: str):
    """선택한 캠퍼스에서 이용할 수 있는 노선: 구분 → {노선 이름: 노선 정보(+ 승차 가능 정류장, 정류장별 하차 가능 정류장)}"""
    r = {"등교": {}, "하교": {}, "순환": {}}
    for rt in BUS_ROUTES:
        stops, last = rt["stops"], len(rt["stops"]) - 1
        mine = [i for i, cs in enumerate(rt["campus_at"]) if campus in cs]      # 이 캠퍼스 정류장 위치
        if rt["kind"] == "등교":                      # 이 캠퍼스로 오는 버스: 앞 정류장에서 타서 이 캠퍼스에서 내려요
            ci = [i for i in mine if i > 0]
            if not ci:
                continue
            board = stops[:ci[0]]
            alight_for = {x: [stops[ci[0]]] for x in board}
        elif rt["kind"] == "하교":                    # 이 캠퍼스에서 출발: 이 캠퍼스에서 타요
            ci = [i for i in mine if i < last]
            if not ci:
                continue
            board = [stops[i] for i in ci]
            alight_for = {stops[i]: stops[i + 1:] for i in ci}
        else:                                         # 셔틀·순환: 이 캠퍼스에서 출발하는 것만
            if 0 not in mine:
                continue
            board = stops[:-1]
            alight_for = {stops[i]: stops[i + 1:] for i in range(last)}
        r[rt["kind"]][rt["title"]] = dict(rt, board=board, alight_for=alight_for)
    return r


SEATS = [f"{i}{c}" for i in range(1, SEAT_ROWS + 1) for c in "ABCD"]
KST = timezone(timedelta(hours=9))


def now_kst():
    return datetime.now(KST)        # 배포 서버는 UTC라서 한국 시간으로 맞춰 써요


ALL = {m["name"]: dict(m, cat=c) for c, ms in MENU.items() for m in ms}
ORDER = list(ALL)


# ───────────────────────── 공통 도우미 ─────────────────────────
def find_asset(name: str):
    """assets 폴더에서 사진 찾기 (대소문자·확장자가 조금 달라도 찾아요)"""
    p = ASSETS / name
    if p.is_file():
        return p
    try:
        stem = Path(name).stem.lower()
        for q in ASSETS.iterdir():
            if q.is_file() and q.stem.lower() == stem and q.suffix.lower() in (".jpg", ".jpeg", ".png", ".webp"):
                return q
    except OSError:
        pass
    return None


@st.cache_data(show_spinner=False)
def _img(path: str, mtime: float, width: int) -> str:
    raw = Path(path).read_bytes()
    try:
        from PIL import Image
        im = Image.open(io.BytesIO(raw))
        has_alpha = im.mode in ("RGBA", "LA") or (im.mode == "P" and "transparency" in im.info)
        im = im.convert("RGBA" if has_alpha else "RGB")   # 투명 배경(누끼)은 그대로 살려요
        if im.width > width:
            im = im.resize((width, round(im.height * width / im.width)), Image.LANCZOS)
        buf = io.BytesIO()
        if has_alpha:
            im.save(buf, "PNG", optimize=True)
        else:
            im.save(buf, "JPEG", quality=82, optimize=True, progressive=True)
        raw = buf.getvalue()
    except Exception:
        pass
    return base64.b64encode(raw).decode()


def data_uri(b64: str) -> str:
    """base64 사진이 PNG면 png로, 아니면 jpeg로 표시해요"""
    return f"data:image/{'png' if b64.startswith('iVBOR') else 'jpeg'};base64,{b64}"


def img(name: str, width: int = 1400) -> str:
    """사진을 줄여서 base64로 바꿔요. 파일이 없으면 빈 문자열(→ 그라데이션으로 대체)"""
    p = find_asset(name)
    if not p:
        return ""
    return _img(str(p), p.stat().st_mtime, width)


# 양말차 사진 비상용 사본: assets 폴더에서 사진을 못 찾아도 이 사진이 나와요
FALLBACK_IMGS = {"yangmatcha.png": "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"}


def menu_photo(m: dict) -> str:
    """메뉴 사진(base64). assets 폴더 → 비상용 사본 순서로 찾아요. 없으면 빈 문자열"""
    if not m.get("img"):
        return ""
    return img(m["img"], 700) or FALLBACK_IMGS.get(m["img"], "")


def squash(s: str) -> str:
    """st.markdown이 들여쓰기·빈 줄을 코드블록으로 오해하지 않도록 정리"""
    return "\n".join(l.strip() for l in s.splitlines() if l.strip())


def md(s: str):
    for k, v in ICONS.items():
        s = s.replace(f"[[{k}]]", f'<svg class="i" viewBox="0 0 24 24" aria-hidden="true">{v}</svg>')
    st.markdown(squash(s), unsafe_allow_html=True)


def won(n: int) -> str:
    return f"{n:,}원"


CODE_CHARS = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"   # 헷갈리는 글자(0·O, 1·I)는 뺐어요


def new_code(table: str, prefix: str) -> str:
    """짧은 번호를 만들어요. 예: K-7HQ3X (버스 예약), D-4TM9B (음료 주문)"""
    code = ""
    for _ in range(10):
        code = prefix + "".join(random.choices(CODE_CHARS, k=5))
        try:
            if not db_select(table, code=code):
                break
        except Exception:
            break          # 조회가 안 되면 저장할 때 진짜 오류 이유를 보여 줘요
    return code


def is_order(code: str) -> bool:
    """D-… 와 예전 번호 DR-… 은 음료 주문, 나머지는 버스 예약"""
    return code.startswith(("D-", "DR-"))


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


_LOCK = threading.Lock()   # 여러 명이 동시에 저장해도 임시 저장 파일(CSV)이 꼬이지 않게 순서를 세워요


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
        with _LOCK:
            rows = _lread(t)
            if t == "reservations" and any(
                    x.get("status") == "예약" and all(str(x.get(k)) == str(row.get(k)) for k in ("campus", "kind", "route", "ride_date", "depart_time", "seat"))
                    for x in rows):
                raise DBError(409, "duplicate", "23505")      # 같은 좌석 중복 예약 막기
            _lwrite(t, rows + [{k: row.get(k, "") for k in FIELDS[t]}])


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
        with _LOCK:
            rows = _lread(t)
            for x in rows:
                if x["code"] == code:
                    x.update(vals)
            _lwrite(t, rows)


def taken_seats(campus, kind, route, day, time_):
    rt = next((x for x in BUS_ROUTES if x["title"] == route), None)
    # 두 캠퍼스 학생이 한 대를 같이 타는 노선이면 두 캠퍼스의 예약을 모두 합쳐서 좌석을 확인해요
    scope = [c for c in CAMPUS_LIST if c in rt["serves"]] if rt and rt["shared"] else [campus]
    try:
        rows = []
        for c in scope:
            for stt in ("예약", "탑승완료"):          # 이미 탄 사람의 좌석도 계속 '예약됨'으로 보여요
                rows += db_select("reservations", campus=c, kind=kind, route=route, ride_date=day, depart_time=time_, status=stt)
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
@import url('https://fonts.googleapis.com/css2?family=Jua&family=Gowun+Dodum&display=swap');
:root{--g:#0E4A38;--g2:#17694F;--gd:#07261C;--ink:#13241D;--mut:#5C6B62;--bg:#FFF8EE;--line:#E7DFCC;--mint:#E2F0E6;--led:#FFC24B;
--sky:#9CCBEA;--pink:#F3A6B8;--sun:#F7B885;--sunset:linear-gradient(100deg,#9CCBEA 0%,#F3A6B8 52%,#F7B885 100%);
--sh:0 26px 44px -30px rgba(7,38,28,.45);--pad:max(24px,calc(50vw - 590px));--bpad:72px;--navh:76px;--off:7px;--ext:__EXT__;
--hearts:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='44' height='30'%3E%3Cpath d='M22 21c-6-4.2-9.5-7-9.5-10.6a4.7 4.7 0 0 1 9.5-1.4 4.7 4.7 0 0 1 9.5 1.4C31.5 14 28 16.8 22 21z' fill='white' fill-opacity='.92'/%3E%3C/svg%3E")}
html{scroll-behavior:smooth}
#MainMenu,[data-testid="stHeader"],[data-testid="stToolbar"],[data-testid="stDecoration"],[data-testid="stStatusWidget"],[data-testid="stHeaderActionElements"],footer{display:none!important}
.stApp{background:radial-gradient(900px 520px at 100% 900px,rgba(247,184,133,.30),transparent 70%),radial-gradient(800px 520px at 0% 1800px,rgba(156,203,234,.32),transparent 70%),radial-gradient(800px 520px at 100% 2800px,rgba(243,166,184,.26),transparent 70%),radial-gradient(rgba(14,74,56,.07) 1.3px,transparent 1.7px) 0 0/26px 26px,var(--bg);overflow-x:hidden;color:var(--ink)}
[data-testid="stMain"],[data-testid="stAppViewContainer"]{overflow-x:hidden}
.stApp,.stApp :is(p,div,span,label,input,button,li,a,small,b,dt,dd,textarea,h1,h2,h3,h4,em):not([data-testid="stIconMaterial"]):not(.material-icons){font-family:'Jua','Gowun Dodum','Apple SD Gothic Neo','Malgun Gothic',sans-serif}
.stApp [data-testid="stMarkdownContainer"] :is(h1,h2,h3,h4){padding:0;margin:0;line-height:1.25;font-weight:400;letter-spacing:-.025em;color:var(--ink);font-family:'Jua','Gowun Dodum','Apple SD Gothic Neo','Malgun Gothic',sans-serif!important}
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
.logo{position:relative;width:48px;height:48px;border-radius:50%;background:var(--led);color:var(--gd);display:grid;place-items:center;font-family:'Jua','Gowun Dodum','Apple SD Gothic Neo','Malgun Gothic',sans-serif!important;font-size:25px;box-shadow:0 0 0 3px rgba(255,255,255,.28),0 6px 14px -4px rgba(0,0,0,.5)}
.stApp .brand b{display:block;font-family:'Jua','Gowun Dodum','Apple SD Gothic Neo','Malgun Gothic',sans-serif!important;font-size:24px;line-height:1.05;font-weight:400;color:#fff;letter-spacing:.01em}
.brand small{display:block;color:#9FD6BA;font-weight:400;font-size:11.5px;letter-spacing:.2em;margin-top:2px;font-family:'Jua','Gowun Dodum','Apple SD Gothic Neo','Malgun Gothic',sans-serif!important}
.st-key-topnav [role="radiogroup"]{gap:6px!important;flex-wrap:nowrap!important;background:rgba(255,255,255,.1);padding:6px;border-radius:99px;border:1px solid rgba(255,255,255,.16)}
.st-key-topnav label:is([data-baseweb="radio"],[data-testid="stRadioOption"]){border:0!important;background:transparent!important;padding:13px 26px!important;border-radius:99px!important;gap:10px;box-shadow:none!important;color:rgba(255,255,255,.9)}
.st-key-topnav label:is([data-baseweb="radio"],[data-testid="stRadioOption"]):before{content:"";width:23px;height:23px;flex:none;background:currentColor;-webkit-mask:var(--ico) center/contain no-repeat;mask:var(--ico) center/contain no-repeat}
__NAVICONS__
.st-key-topnav label:is([data-baseweb="radio"],[data-testid="stRadioOption"]) p{font-size:17.5px;font-weight:400;white-space:nowrap;color:inherit!important;font-family:'Jua','Gowun Dodum','Apple SD Gothic Neo','Malgun Gothic',sans-serif!important}
.st-key-topnav label:is([data-baseweb="radio"],[data-testid="stRadioOption"]):hover{background:rgba(255,255,255,.14)!important;border-color:transparent!important}
.st-key-topnav label:is([data-baseweb="radio"],[data-testid="stRadioOption"]):has(input:checked){background:var(--led)!important;color:var(--gd);box-shadow:0 0 0 3px rgba(255,194,75,.3),0 10px 22px -8px rgba(255,194,75,.85)!important}

/* ── 라디오를 알약/세그먼트 버튼처럼 ── */
[data-testid="stRadio"] [role="radiogroup"]{gap:10px;flex-wrap:wrap}
[data-testid="stRadio"] label:is([data-baseweb="radio"],[data-testid="stRadioOption"]){margin:0;padding:13px 20px;border-radius:16px;border:2px solid var(--line);background:#fff;cursor:pointer;justify-content:center;transition:background .15s,border-color .15s,transform .1s;box-shadow:0 3px 0 #EADFC6}
[data-testid="stRadio"] label:is([data-baseweb="radio"],[data-testid="stRadioOption"])>div:not(:has(p)){display:none!important}
[data-testid="stRadio"] label[data-testid="stRadioOption"]>div>div:not([data-testid="stMarkdownContainer"]){display:none!important}
[data-testid="stRadio"] label:is([data-baseweb="radio"],[data-testid="stRadioOption"]):hover{border-color:var(--g2)}
[data-testid="stRadio"] label:is([data-baseweb="radio"],[data-testid="stRadioOption"]):has(input:checked){background:var(--g);border-color:var(--g);box-shadow:0 3px 0 var(--gd)}
[data-testid="stRadio"] label:is([data-baseweb="radio"],[data-testid="stRadioOption"]) p{font-weight:400;font-size:15.5px;color:var(--ink);font-family:'Jua','Gowun Dodum','Apple SD Gothic Neo','Malgun Gothic',sans-serif!important}
[data-testid="stRadio"] label:is([data-baseweb="radio"],[data-testid="stRadioOption"]):has(input:checked) p{color:#fff}
.st-key-kind [role="radiogroup"],.st-key-d_cat [role="radiogroup"]{display:grid!important;grid-template-columns:repeat(3,1fr)}
.st-key-d_cat label:is([data-baseweb="radio"],[data-testid="stRadioOption"]){padding:13px 6px}

/* ═══ 버스 전광판(LED) · 다음 정류장 표시 ═══ */
.led{display:inline-flex;align-items:center;gap:12px;max-width:100%;padding:10px 18px 10px 14px;border-radius:14px;background:#08130F;border:2px solid rgba(255,255,255,.25);box-shadow:0 0 0 5px rgba(7,38,28,.55),inset 0 0 20px rgba(255,194,75,.14)}
.led-lamp{width:11px;height:11px;border-radius:50%;background:var(--led);box-shadow:0 0 12px 4px rgba(255,194,75,.7);animation:blink 1.4s steps(2,jump-none) infinite;flex:none}
.led-win{position:relative;overflow:hidden;width:min(440px,58vw)}
.led-win:after{content:"";position:absolute;inset:0;background:radial-gradient(rgba(0,0,0,.6) 1px,transparent 1.6px) 0 0/4px 4px}
.led-run{display:inline-flex;white-space:nowrap;animation:led 22s linear infinite}
.led-run span{padding-right:2.6em;font-family:'Jua','Gowun Dodum','Apple SD Gothic Neo','Malgun Gothic',sans-serif!important;color:var(--led);font-size:18px;letter-spacing:.06em;text-shadow:0 0 9px rgba(255,194,75,.7)}
@keyframes led{to{transform:translateX(-50%)}}
@keyframes blink{50%{opacity:.35}}
.chipled{display:inline-flex;align-items:center;gap:8px;background:#08130F;color:var(--led);border-radius:10px;padding:6px 12px;font-family:'Jua','Gowun Dodum','Apple SD Gothic Neo','Malgun Gothic',sans-serif!important;letter-spacing:.1em;font-size:14px;line-height:1.2;text-shadow:0 0 8px rgba(255,194,75,.6);border:1px solid rgba(255,255,255,.2)}
.chipled b{font-weight:400;font-family:inherit!important;color:#fff;text-shadow:none}
.nextstop{display:flex;align-items:center;gap:12px;margin-bottom:18px}
.nextstop .dot{width:22px;height:22px;border-radius:50%;background:#fff;border:6px solid var(--g);box-shadow:0 0 0 5px rgba(14,74,56,.14);flex:none}
.nextstop .stopname{font-weight:400;color:var(--g);font-size:15px;font-family:'Jua','Gowun Dodum','Apple SD Gothic Neo','Malgun Gothic',sans-serif!important}
.nextstop:after{content:"";height:3px;flex:1;max-width:160px;border-radius:3px;opacity:.45;background:repeating-linear-gradient(90deg,var(--g) 0 10px,transparent 10px 18px)}

/* ═══ 페이지 상단 띠 (버스 예약·음료 주문·예약 조회) ═══ */
.pagehead{position:relative;isolation:isolate;padding:calc(var(--navh) + 54px) 0 84px}
.pagehead:before{content:"";position:absolute;z-index:-1;top:0;bottom:0;left:50%;width:100vw;transform:translateX(-50%);
background:radial-gradient(55% 140% at 88% 130%,rgba(247,184,133,.8),transparent 62%),radial-gradient(45% 120% at 68% 140%,rgba(243,166,184,.65),transparent 62%),radial-gradient(40% 100% at 100% -10%,rgba(156,203,234,.5),transparent 60%),linear-gradient(120deg,#07261C,#0E4A38 70%)}
.pagehead:after{content:"";position:absolute;z-index:-1;bottom:0;left:50%;width:100vw;height:32px;transform:translateX(-50%);background:var(--hearts) repeat-x center/44px 30px,var(--g2);box-shadow:0 -4px 0 rgba(255,255,255,.25)}
.stApp .pagehead h1{font-family:'Jua','Gowun Dodum','Apple SD Gothic Neo','Malgun Gothic',sans-serif!important;font-weight:400;font-size:clamp(38px,5.6vw,58px);color:#fff!important;margin-top:18px;letter-spacing:0}
.stApp .pagehead p{margin:12px 0 0;color:rgba(255,255,255,.88);font-size:17.5px;line-height:1.7;max-width:580px}
.sp{height:40px}
.sec{padding:112px 0 0;scroll-margin-top:96px}
.stApp .sec h2{font-family:'Jua','Gowun Dodum','Apple SD Gothic Neo','Malgun Gothic',sans-serif!important;font-weight:400;font-size:clamp(32px,4.4vw,50px);line-height:1.2;letter-spacing:-.005em;margin:0 0 16px}
.stApp p.lead{color:var(--mut);font-size:17.5px;line-height:1.85;max-width:580px;margin:0}

/* ═══ 홈: 히어로 (사진은 화면 가득, 글은 왼쪽) ═══ */
.st-key-hero{position:relative!important;isolation:isolate;min-height:min(92vh,880px);padding:calc(var(--navh) + 76px) 0 140px!important;display:flex!important;flex-direction:column;justify-content:center;gap:0!important}
.st-key-hero:before{content:"";position:absolute;z-index:-1;top:0;bottom:0;left:50%;width:100vw;transform:translateX(-50%);
background:linear-gradient(95deg,rgba(5,30,22,.94) 0%,rgba(5,30,22,.62) 42%,rgba(5,30,22,.04) 100%),linear-gradient(0deg,rgba(243,166,184,.4),rgba(247,184,133,0) 42%),var(--ext) center/cover}
.st-key-hero:after{content:"";position:absolute;z-index:-1;top:calc(var(--navh) + 4px);left:50%;width:100vw;height:46px;transform:translateX(-50%);pointer-events:none;
background:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='120' height='46'%3E%3Cpath d='M0 6Q60 40 120 6' fill='none' stroke='white' stroke-opacity='.4' stroke-width='1.4'/%3E%3Cg fill='%23FFD9A0'%3E%3Ccircle cx='30' cy='19' r='7' fill-opacity='.28'/%3E%3Ccircle cx='60' cy='23' r='7' fill-opacity='.28'/%3E%3Ccircle cx='90' cy='19' r='7' fill-opacity='.28'/%3E%3Ccircle cx='30' cy='19' r='3.4'/%3E%3Ccircle cx='60' cy='23' r='3.4'/%3E%3Ccircle cx='90' cy='19' r='3.4'/%3E%3C/g%3E%3C/svg%3E") repeat-x top left}
.stApp .hero-copy h1{font-family:'Jua','Gowun Dodum','Apple SD Gothic Neo','Malgun Gothic',sans-serif!important;font-weight:400;color:#fff;font-size:clamp(48px,7.6vw,96px);line-height:1.1;margin:30px 0 24px;letter-spacing:-.005em;animation:rise .8s cubic-bezier(.2,.7,.2,1) both;text-shadow:0 6px 30px rgba(0,0,0,.35)}
.stApp .hero-copy h1 em{font-style:normal;background:var(--sunset);-webkit-background-clip:text;background-clip:text;-webkit-text-fill-color:transparent;text-shadow:none}
.stApp .hero-copy p{color:rgba(255,255,255,.92);font-size:clamp(16.5px,1.7vw,20px);line-height:1.85;max-width:560px;margin:0 0 40px}
.st-key-hero [data-testid="stHorizontalBlock"]{max-width:580px;gap:14px}
@keyframes rise{from{opacity:0;transform:translateY(22px)}}
.infobar{position:relative;z-index:2;margin:-70px 0 0;display:grid;grid-template-columns:repeat(3,1fr)}
.infobar>div{display:flex;gap:15px;align-items:center;padding:24px 28px;border-right:2px dashed #CDE0D3}.infobar>div:last-child{border:0}
.infobar .ic{width:52px;height:52px;border-radius:50%;background:var(--sunset);color:var(--g);display:grid;place-items:center;flex:none;border:2px solid var(--g)}
.infobar small{display:block;color:var(--mut);font-size:13.5px;font-weight:400;font-family:'Jua','Gowun Dodum','Apple SD Gothic Neo','Malgun Gothic',sans-serif!important}.infobar b{font-size:17px;font-weight:400;font-family:'Jua','Gowun Dodum','Apple SD Gothic Neo','Malgun Gothic',sans-serif!important}

/* ═══ 창문 카드 (초록 버스 창틀 + 노을색 그림자) ═══ */
:is(.info,.floor,.pass,.infobar,.st-key-mapbox,.st-key-resv,.st-key-opt,.st-key-cartbox,.st-key-lookup){background:linear-gradient(112deg,transparent 60%,rgba(156,203,234,.16) 60% 68%,transparent 68%),#fff;border:2px solid var(--g);border-radius:28px;box-shadow:var(--off) var(--off) 0 var(--ofs,rgba(247,184,133,.6)),0 28px 40px -30px rgba(7,38,28,.4)}
.info,.st-key-mapbox{--ofs:rgba(156,203,234,.7)}
.floor:nth-child(1){--ofs:rgba(156,203,234,.7)}.floor:nth-child(2){--ofs:rgba(243,166,184,.65)}
.st-key-resv{--ofs:rgba(243,166,184,.55)}.st-key-opt{--ofs:rgba(156,203,234,.65)}.st-key-cartbox{--ofs:rgba(247,184,133,.65)}.st-key-lookup{--ofs:rgba(156,203,234,.65)}
.info{padding:32px;height:100%;box-sizing:border-box}
.stApp .info h3{font-family:'Jua','Gowun Dodum','Apple SD Gothic Neo','Malgun Gothic',sans-serif!important;font-weight:400;font-size:26px;margin:0 0 18px}
.row{padding:15px 0;border-top:2px dashed #E3EAE0}.row small{display:block;color:var(--mut);font-weight:400;margin-bottom:4px;font-family:'Jua','Gowun Dodum','Apple SD Gothic Neo','Malgun Gothic',sans-serif!important}.row span{font-weight:400;line-height:1.6;font-family:'Jua','Gowun Dodum','Apple SD Gothic Neo','Malgun Gothic',sans-serif!important}
.row a{font-weight:400;color:var(--g)!important;font-family:'Jua','Gowun Dodum','Apple SD Gothic Neo','Malgun Gothic',sans-serif!important}
.chips{display:flex;flex-wrap:wrap;gap:8px;margin-top:8px}
.chip{border:2px solid var(--g);background:#fff;border-radius:99px;padding:6px 14px;font-weight:400;font-size:14px;color:var(--g);font-family:'Jua','Gowun Dodum','Apple SD Gothic Neo','Malgun Gothic',sans-serif!important}
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
.floor .lv{position:absolute;left:16px;top:16px;background:var(--led);color:var(--gd);font-family:'Jua','Gowun Dodum','Apple SD Gothic Neo','Malgun Gothic',sans-serif!important;font-size:18px;padding:5px 15px;border-radius:99px;border:2px solid var(--g)}
.floor .tx{padding:26px 28px 30px}.stApp .floor h3{font-family:'Jua','Gowun Dodum','Apple SD Gothic Neo','Malgun Gothic',sans-serif!important;font-weight:400;font-size:25px;margin:0 0 8px}.floor p{color:var(--mut);line-height:1.8;font-size:16px}
.rgroup{margin:48px 0 16px;display:flex;align-items:baseline;gap:12px;flex-wrap:wrap}.stApp .rgroup h3{font-family:'Jua','Gowun Dodum','Apple SD Gothic Neo','Malgun Gothic',sans-serif!important;font-weight:400;font-size:28px}.rgroup span{color:var(--mut);font-size:14.5px}
.rgrid{display:grid;grid-template-columns:repeat(auto-fill,minmax(220px,1fr));gap:14px}
.rc{position:relative;background:#fff;border:2px solid var(--line);border-left:8px solid var(--g2);border-radius:20px;padding:18px 18px 18px 16px;display:flex;flex-direction:column;gap:9px;transition:transform .15s,box-shadow .15s}
.rc:hover{transform:translateY(-3px);box-shadow:0 14px 24px -16px rgba(7,38,28,.5)}
.rc.e{border-left-color:#E58BA3}.rc.v{border-left-color:#EFA05F}.rc.s{border-left-color:#6FAEDC}
.rc b{font-size:18px;line-height:1.35}.rc small{color:var(--mut);font-weight:400;font-family:'Jua','Gowun Dodum','Apple SD Gothic Neo','Malgun Gothic',sans-serif!important}
.k{align-self:flex-start;font-size:12.5px;font-weight:400;padding:4px 11px;border-radius:99px;font-family:'Jua','Gowun Dodum','Apple SD Gothic Neo','Malgun Gothic',sans-serif!important}
.k.w{background:#E1F1E8;color:#146344}.k.e{background:#FBE4EA;color:#B03A5B}.k.v{background:#FFE8D2;color:#A5571B}.k.s{background:#DDEBFA;color:#1F5C99}
.st-key-cta{margin-top:112px;border-radius:34px;border:2px solid var(--g);padding:56px 52px!important;gap:0!important;position:relative;overflow:hidden;box-shadow:var(--off) var(--off) 0 rgba(243,166,184,.6);
background:radial-gradient(60% 140% at 100% 120%,rgba(247,184,133,.95),transparent 60%),radial-gradient(50% 120% at 80% 130%,rgba(243,166,184,.85),transparent 60%),radial-gradient(45% 100% at 100% -20%,rgba(156,203,234,.75),transparent 60%),linear-gradient(120deg,#07261C,#0E4A38 75%)}
.stApp .st-key-cta h2{font-family:'Jua','Gowun Dodum','Apple SD Gothic Neo','Malgun Gothic',sans-serif!important;font-weight:400;color:#fff;font-size:clamp(30px,3.8vw,44px);margin:0 0 12px}
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
.stApp .notice h2{font-family:'Jua','Gowun Dodum','Apple SD Gothic Neo','Malgun Gothic',sans-serif!important;font-weight:400;font-size:32px;color:#fff}.stApp .notice h4{margin:0 0 10px;font-size:17px;color:var(--led)}.notice p{color:#A9BFB3;line-height:1.85;font-size:15.5px}
.foot{padding:24px 0 34px;border-top:1px solid rgba(255,255,255,.12);color:#86A094;font-size:13.5px;display:flex;justify-content:space-between;gap:16px;flex-wrap:wrap}

/* ── 폼 카드 ── */
:is(.st-key-resv,.st-key-opt,.st-key-cartbox){padding:34px 34px 36px!important;gap:.85rem!important}
.form-h{display:flex;gap:15px;align-items:center;margin-bottom:6px}.form-h .ico{width:54px;height:54px;border-radius:50%;background:var(--mint);color:var(--g);display:grid;place-items:center;flex:none;border:2px solid var(--g)}
.stApp .form-h h3{font-family:'Jua','Gowun Dodum','Apple SD Gothic Neo','Malgun Gothic',sans-serif!important;font-weight:400;font-size:27px}.stApp .form-h p{margin:3px 0 0;color:var(--mut);font-size:14.5px}
.fs{display:flex;align-items:center;gap:12px;margin:22px 0 6px;font-weight:400;font-size:16.5px;font-family:'Jua','Gowun Dodum','Apple SD Gothic Neo','Malgun Gothic',sans-serif!important}
.fs em{font-style:normal;width:30px;height:30px;border-radius:50%;background:var(--g);color:#fff;display:grid;place-items:center;font-size:14px;box-shadow:0 0 0 4px rgba(14,74,56,.15);flex:none}
.fs:after{content:"";flex:1;height:3px;border-radius:3px;opacity:.35;background:repeating-linear-gradient(90deg,var(--g) 0 8px,transparent 8px 15px)}
[data-testid="stWidgetLabel"] p{font-weight:400;font-size:14.5px;color:var(--ink);font-family:'Jua','Gowun Dodum','Apple SD Gothic Neo','Malgun Gothic',sans-serif!important}
[data-baseweb="base-input"]{background:transparent!important}
[data-testid="stTextInputRootElement"],.stNumberInput [data-baseweb="input"],.stDateInput [data-baseweb="input"],[data-baseweb="select"]>div{border-radius:14px!important;border:2px solid var(--line)!important;background:#fff!important;min-height:52px}
[data-testid="stTextInputRootElement"] :is([data-baseweb="input"],[data-baseweb="base-input"],input){background:transparent!important;border:0!important}
[data-testid="stTextInputRootElement"]:focus-within,.stNumberInput [data-baseweb="input"]:focus-within,.stDateInput [data-baseweb="input"]:focus-within,[data-baseweb="select"]>div:focus-within{border-color:var(--g2)!important;box-shadow:0 0 0 4px rgba(23,105,79,.15)!important}
.stTextInput input,.stNumberInput input,.stDateInput input{font-size:16px;min-height:48px}
[data-testid="stAlert"]{border-radius:16px}
[data-testid="stCaptionContainer"]{color:var(--mut)}
[data-baseweb="tab"]{font-weight:400;font-size:16px;font-family:'Jua','Gowun Dodum','Apple SD Gothic Neo','Malgun Gothic',sans-serif!important}

/* ── 버튼: 꾹 눌리는 느낌 ── */
.stButton,.stButton>button{width:100%}
.stElementContainer:has(>.stButton){width:100%!important}
.stElementContainer:not(.st-key-page):has(>[data-testid="stRadio"]){width:100%!important}
[data-testid="stRadio"] label:is([data-baseweb="radio"],[data-testid="stRadioOption"]){display:flex;align-items:center;width:100%;box-sizing:border-box}
.stButton>button{min-height:56px;border-radius:17px;font-weight:400;font-size:17px;transition:transform .1s,box-shadow .1s,background .15s;font-family:'Jua','Gowun Dodum','Apple SD Gothic Neo','Malgun Gothic',sans-serif!important}
.stButton>button:active{transform:translateY(4px)}
.stButton>button:is([kind="primary"],[data-testid="stBaseButton-primary"]){background:var(--g);color:#fff;border:0;box-shadow:0 5px 0 var(--gd),0 18px 24px -16px rgba(7,38,28,.7)}
.stButton>button:is([kind="primary"],[data-testid="stBaseButton-primary"]):hover{background:var(--g2);color:#fff}
.stButton>button:is([kind="primary"],[data-testid="stBaseButton-primary"]):active{box-shadow:0 1px 0 var(--gd)}
.stButton>button:is([kind="secondary"],[data-testid="stBaseButton-secondary"]){background:#fff;color:var(--g);border:2px solid var(--g);box-shadow:0 4px 0 rgba(14,74,56,.22)}
.stButton>button:is([kind="secondary"],[data-testid="stBaseButton-secondary"]):hover{background:var(--mint);color:var(--g)}
.stButton>button:is([kind="secondary"],[data-testid="stBaseButton-secondary"]):active{box-shadow:0 1px 0 rgba(14,74,56,.22)}
.stButton>button p{font-weight:400;color:inherit!important;font-family:'Jua','Gowun Dodum','Apple SD Gothic Neo','Malgun Gothic',sans-serif!important}
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
.pass-lab{display:flex;align-items:center;gap:8px;font-weight:400;font-size:14.5px;color:var(--led);font-family:'Jua','Gowun Dodum','Apple SD Gothic Neo','Malgun Gothic',sans-serif!important}
.pass-lab svg.i{width:19px;height:19px;stroke-width:2.6}
.stApp .pass-title{margin:10px 0 4px;font-size:22px;font-weight:400;line-height:1.35;letter-spacing:-.02em;font-family:'Jua','Gowun Dodum','Apple SD Gothic Neo','Malgun Gothic',sans-serif!important}
.stApp .pass.ok .pass-title{font-size:34px;letter-spacing:.03em;font-variant-numeric:tabular-nums;font-family:'Jua','Gowun Dodum','Apple SD Gothic Neo','Malgun Gothic',sans-serif!important;font-weight:400;color:var(--led);text-shadow:0 0 14px rgba(255,194,75,.45)}
.pass-sub{font-size:15px;opacity:.92}
.pass-cut{position:relative;height:0;margin:0 22px;border-top:3px dashed #BFD3C6}
.pass-cut:before,.pass-cut:after{content:"";position:absolute;top:-15px;width:28px;height:28px;border-radius:50%;background:var(--bg);border:2px solid var(--g)}.pass-cut:before{left:-37px}.pass-cut:after{right:-37px}
.pass-body{padding:26px 30px 28px}
.pass dl{display:grid;grid-template-columns:auto 1fr;gap:10px 20px;margin:18px 0 0}.pass dt{color:var(--mut);font-size:14px}.pass dd{margin:0;font-weight:400;line-height:1.5;font-family:'Jua','Gowun Dodum','Apple SD Gothic Neo','Malgun Gothic',sans-serif!important}
.pass-note{margin-top:18px;padding-top:16px;border-top:2px dashed #DCE6DF;color:var(--mut);font-size:13.5px;line-height:1.7}
.tl{list-style:none;margin:0;padding:0}
.tl li{position:relative;display:flex;align-items:center;gap:14px;padding:0 0 18px 0;color:#97A79E;font-weight:400;font-family:'Jua','Gowun Dodum','Apple SD Gothic Neo','Malgun Gothic',sans-serif!important}.tl li:last-child{padding-bottom:0}
.tl li:before{content:"";position:absolute;left:6px;top:16px;bottom:-4px;width:3px;background:#DCE6DF}.tl li:last-child:before{display:none}
.tl li.on:before{background:var(--g2)}
.tl i{width:15px;height:15px;border-radius:50%;background:#fff;border:3px solid #C9D6CE;flex:none;position:relative;z-index:1}
.tl li.on{color:var(--ink)}.tl li.on i{border-color:var(--g2);background:var(--g2)}
.tl em{font-style:normal;margin-left:auto;font-size:12.5px;font-weight:400;color:var(--gd);background:var(--led);padding:3px 11px;border-radius:99px;font-family:'Jua','Gowun Dodum','Apple SD Gothic Neo','Malgun Gothic',sans-serif!important}
.tl .tm{font-variant-numeric:tabular-nums;font-weight:400;font-size:13px;color:var(--g2);min-width:44px;font-family:'Jua','Gowun Dodum','Apple SD Gothic Neo','Malgun Gothic',sans-serif!important}
.tl .nm{min-width:0;line-height:1.45;word-break:keep-all}
.tl li.gap .nm{color:var(--mut);font-weight:400;font-size:13px;font-family:'Jua','Gowun Dodum','Apple SD Gothic Neo','Malgun Gothic',sans-serif!important}
.warnchip{display:inline-block;margin-top:12px;padding:5px 12px;border-radius:99px;background:#FFE8D2;color:#A5571B;font-weight:400;font-size:13px;font-family:'Jua','Gowun Dodum','Apple SD Gothic Neo','Malgun Gothic',sans-serif!important}
.codewarn{display:flex;gap:12px;align-items:flex-start;margin:0 0 16px;padding:14px 16px;border-radius:18px;background:#FFF1E0;border:2px solid #F0A55C;color:#7A3F0E;line-height:1.6;font-size:14.5px}
.codewarn .cw-ic{flex:none;width:26px;height:26px;margin-top:1px;border-radius:50%;background:#E8741C;color:#fff;display:grid;place-items:center;font-size:16px;font-weight:400;font-style:normal;font-family:'Jua','Gowun Dodum','Apple SD Gothic Neo','Malgun Gothic',sans-serif!important}
.codewarn strong{display:block;font-size:15.5px;margin-bottom:2px;color:#6A3208}
.cancelask{margin:8px 0 10px;padding:12px 14px;border-radius:14px;background:#FFE8EC;color:#9A3A52;font-weight:400;font-size:14.5px;line-height:1.6;font-family:'Jua','Gowun Dodum','Apple SD Gothic Neo','Malgun Gothic',sans-serif!important}
.qrbox{margin:16px 0 10px;padding:20px 18px 16px;border-radius:24px;background:#fff;border:2px solid var(--g);box-shadow:6px 6px 0 rgba(156,203,234,.6);text-align:center}
.qrbox img{display:block;margin:0 auto;width:min(240px,72%);height:auto;image-rendering:pixelated}
.qrbox .qrcode{margin-top:10px;font-weight:400;letter-spacing:.04em;font-size:15px;color:var(--g);font-variant-numeric:tabular-nums;font-family:'Jua','Gowun Dodum','Apple SD Gothic Neo','Malgun Gothic',sans-serif!important}
.qrbox .qrhint{margin-top:4px;color:var(--mut);font-size:13px}
.notes{margin:20px 4px 0;padding:0;list-style:none;display:grid;gap:8px;color:var(--mut);font-size:14px;line-height:1.6}
.notes li{display:flex;gap:10px}.notes li:before{content:"";width:7px;height:7px;border-radius:50%;background:var(--sun);margin-top:9px;flex:none}
.ol{display:grid;gap:10px;margin:0}.ol div{display:flex;justify-content:space-between;gap:14px;font-weight:400;font-family:'Jua','Gowun Dodum','Apple SD Gothic Neo','Malgun Gothic',sans-serif!important}.ol small{display:block;color:var(--mut);font-weight:500}
.tot{display:flex;justify-content:space-between;align-items:baseline;margin-top:14px;padding-top:14px;border-top:2px dashed #CFDCD3;font-weight:400;font-family:'Jua','Gowun Dodum','Apple SD Gothic Neo','Malgun Gothic',sans-serif!important}.tot b{font-size:23px;color:var(--g)}

/* ── 음료 주문 ── */
.cup{width:58px;height:72px;flex:none}
:is([class*="st-key-mi_"],[class*="st-key-mis_"]){background:#fff;border:2px solid var(--line);border-radius:24px;padding:16px!important;gap:.6rem!important;transition:border-color .15s,box-shadow .15s,transform .15s}
:is([class*="st-key-mi_"]):hover{border-color:var(--g2);transform:translateY(-2px)}
[class*="st-key-mis_"]{border-color:var(--g);box-shadow:5px 5px 0 rgba(255,194,75,.75)}
.mc{display:flex;justify-content:space-between;align-items:flex-start;gap:8px}
.mc .tg{font-size:12px;font-weight:400;color:#B03A5B;background:#FBE4EA;border-radius:99px;padding:4px 10px;font-family:'Jua','Gowun Dodum','Apple SD Gothic Neo','Malgun Gothic',sans-serif!important}
.stApp .mc-n{font-weight:400;font-size:17.5px;margin-top:2px;font-family:'Jua','Gowun Dodum','Apple SD Gothic Neo','Malgun Gothic',sans-serif!important}.stApp .mc-d{color:var(--mut);font-size:13px;line-height:1.55;min-height:40px;margin:3px 0 8px}
.mc-p{font-weight:400;color:var(--g);font-size:16.5px;font-family:'Jua','Gowun Dodum','Apple SD Gothic Neo','Malgun Gothic',sans-serif!important}.mc-i{display:inline-block;margin-left:6px;font-size:12px;color:var(--mut);font-weight:400;font-family:'Jua','Gowun Dodum','Apple SD Gothic Neo','Malgun Gothic',sans-serif!important}
.cartbar{display:flex;align-items:center;gap:10px;background:#FFF6DC;border:2px dashed #E8C766;border-radius:16px;padding:10px 14px;margin:6px 0 4px;font-size:14.5px;color:var(--ink)}.cartbar .i{width:20px;height:20px;flex:none}.cartbar b{color:var(--g)}
.mc-p{margin-bottom:10px}
.mc.ph-on{display:block;position:relative}.mc .ph{display:block;width:100%;aspect-ratio:1/1;object-fit:contain;padding:8px;box-sizing:border-box;background:linear-gradient(160deg,#FFEFE2,#EAF5DD 70%);border-radius:16px;border:2px solid var(--line)}.mc.ph-on .tg{position:absolute;top:8px;right:8px;box-shadow:0 2px 6px rgba(0,0,0,.15)}
.emo{display:flex;align-items:center;justify-content:center;width:58px;height:72px;font-size:38px;flex:none}.emo.big{width:72px;height:90px;font-size:48px}
.opt-h .ph.big{width:96px;height:96px;aspect-ratio:auto;object-fit:contain;padding:5px;box-sizing:border-box;background:linear-gradient(160deg,#FFEFE2,#EAF5DD 70%);border-radius:18px;border:2px solid var(--line);flex:none}.mc-p.rg{font-size:14.5px}
.st-key-menu [data-testid="stHorizontalBlock"]{gap:14px}
.st-key-menu .stButton>button{min-height:44px;font-size:14.5px;border-radius:13px}
.st-key-opt{margin-top:26px}
.opt-h{display:flex;gap:18px;align-items:center}.opt-h .cup{width:72px;height:90px}.opt-h>div{flex:1}
.stApp .opt-h h3{font-family:'Jua','Gowun Dodum','Apple SD Gothic Neo','Malgun Gothic',sans-serif!important;font-weight:400;font-size:27px}.stApp .opt-h p{color:var(--mut);margin-top:4px;font-size:14.5px;line-height:1.55}.opt-h .pr{font-size:23px;font-weight:400;color:var(--g);white-space:nowrap;font-family:'Jua','Gowun Dodum','Apple SD Gothic Neo','Malgun Gothic',sans-serif!important}
.st-key-cartlines [data-testid="stHorizontalBlock"]{flex-wrap:nowrap!important;gap:10px!important;align-items:center}
.st-key-cartlines :is([data-testid="stColumn"],[data-testid="column"]){min-width:0!important}
.st-key-cartlines :is([data-testid="stColumn"],[data-testid="column"]):last-child{flex:0 0 70px!important;width:70px!important}
.st-key-cartlines .stButton>button{min-height:40px;font-size:13.5px;border-radius:11px;color:#9A3A52;border-color:#F1D3DB;box-shadow:none}
.cl{background:var(--mint);border-radius:16px;padding:12px 16px;display:flex;justify-content:space-between;gap:12px;font-weight:400;line-height:1.45;font-family:'Jua','Gowun Dodum','Apple SD Gothic Neo','Malgun Gothic',sans-serif!important}
.cl small{display:block;color:var(--mut);font-weight:400;font-family:'Jua','Gowun Dodum','Apple SD Gothic Neo','Malgun Gothic',sans-serif!important}
.empty{border:2px dashed #CBD9D0;border-radius:18px;padding:28px 18px;text-align:center;color:var(--mut);line-height:1.7;font-size:15px}
.ch{display:flex;align-items:center;gap:10px}.ch .ico{width:46px;height:46px;border-radius:50%;background:var(--mint);color:var(--g);display:grid;place-items:center;border:2px solid var(--g)}
.stApp .ch h3{font-family:'Jua','Gowun Dodum','Apple SD Gothic Neo','Malgun Gothic',sans-serif!important;font-weight:400;font-size:24px}.ch .n{margin-left:auto;background:var(--g);color:#fff;font-weight:400;font-size:13px;border-radius:99px;padding:4px 11px;font-family:'Jua','Gowun Dodum','Apple SD Gothic Neo','Malgun Gothic',sans-serif!important}

/* ── 분위기 장식 ── */
.stripe{height:32px;margin:80px 0 -40px;border-radius:99px;border:2px solid var(--gd);background:var(--hearts) repeat-x center/44px 30px,var(--g);box-shadow:0 12px 24px -16px rgba(7,38,28,.6)}
.st-key-map_campus{margin-bottom:20px}
:is(.st-key-b_campus,.st-key-map_campus) [role="radiogroup"]{display:grid!important;grid-template-columns:repeat(2,1fr)}
.st-key-lookup{padding:34px!important;gap:.85rem!important}
/* 좌석표 */
.bus{background:#fff;border:3px solid var(--g);border-radius:40px 40px 20px 20px;padding:16px 14px;max-width:330px;margin:4px auto 8px;box-shadow:5px 5px 0 rgba(156,203,234,.7)}
.bus-head{display:block;text-align:center;font-size:13px;font-family:'Jua','Gowun Dodum','Apple SD Gothic Neo','Malgun Gothic',sans-serif!important;color:var(--led);background:#08130F;border-radius:10px;padding:7px 10px;margin-bottom:14px;letter-spacing:.04em;text-shadow:0 0 8px rgba(255,194,75,.6)}
.srow{display:flex;gap:6px;justify-content:center;margin-bottom:6px}
.seat{width:46px;height:40px;border-radius:11px 11px 7px 7px;display:grid;place-items:center;font-size:12.5px;font-weight:400;font-family:'Jua','Gowun Dodum','Apple SD Gothic Neo','Malgun Gothic',sans-serif!important}
.seat.free{background:#fff;border:2px solid var(--g2);color:var(--g)}
.seat.taken{background:#E8EAE5;border:2px solid #E8EAE5;color:#A3ADA6;text-decoration:line-through}
.seat.sel{background:var(--led);border:2px solid var(--g);color:var(--gd);box-shadow:0 8px 16px -8px rgba(185,128,28,.9)}
.aisle{width:16px}
.legend{margin-top:12px;font-size:12px;color:var(--mut);text-align:center;font-weight:400;font-family:'Jua','Gowun Dodum','Apple SD Gothic Neo','Malgun Gothic',sans-serif!important}
.lg{display:inline-block;width:12px;height:12px;border-radius:4px;margin:0 5px 0 12px;vertical-align:-2px}
.lg.free{background:#fff;border:2px solid var(--g2)}.lg.sel{background:var(--led);border:2px solid var(--g)}.lg.taken{background:#E8EAE5}
[data-testid="stButtonGroup"] button{border-radius:13px!important;border:2px solid var(--line)!important;background:#fff!important;color:var(--ink)!important;font-weight:400;min-height:42px;font-family:'Jua','Gowun Dodum','Apple SD Gothic Neo','Malgun Gothic',sans-serif!important}
[data-testid="stButtonGroup"] button p{color:inherit!important}
[data-testid="stButtonGroup"] button[data-testid$="Active"]{background:var(--g)!important;border-color:var(--g)!important;color:#fff!important}

/* ── 태블릿 / 모바일 ── */
@media(max-width:980px){
.st-key-topnav label:is([data-baseweb="radio"],[data-testid="stRadioOption"]){padding:11px 16px!important;gap:8px}.st-key-topnav label:is([data-baseweb="radio"],[data-testid="stRadioOption"]) p{font-size:16px}
.brand small{display:none}
}
@media(max-width:860px){.split,.floors,.notice-in,.infobar{grid-template-columns:1fr}.split{gap:40px}.infobar>div{border-right:0;border-bottom:2px dashed #CDE0D3}.infobar>div:last-child{border:0}
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
.opt-h{flex-wrap:wrap}.opt-h .cup{width:56px;height:70px}.mc .emo{width:46px;height:58px;font-size:32px}.opt-h .emo.big{width:56px;height:70px;font-size:38px}.opt-h .ph.big{width:72px;height:72px}
.st-key-d_cat [role="radiogroup"]{grid-template-columns:repeat(2,1fr)!important}.st-key-d_cat label:is([data-baseweb="radio"],[data-testid="stRadioOption"]) p{white-space:normal!important}
.mc-p{margin-bottom:8px}.st-key-d_cat label:is([data-baseweb="radio"],[data-testid="stRadioOption"]) p{font-size:13px;white-space:nowrap}.st-key-d_cat [role="radiogroup"]{gap:6px}
}
@media(prefers-reduced-motion:reduce){html{scroll-behavior:auto}.stApp .hero-copy h1,.led-run,.led-lamp,.road-bus{animation:none}.road-bus{left:auto;right:12%}.stButton>button{transition:none}}
.stApp{font-synthesis:none}
.stApp [data-testid="stMarkdownContainer"] :is(h1,h2,h3,h4),.stApp :is(b,strong,button,button p,label,[data-testid="stWidgetLabel"] p,[role="tab"] p,th,summary){font-family:'Jua','Gowun Dodum','Apple SD Gothic Neo','Malgun Gothic',sans-serif!important;font-weight:400!important}
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


def has_opts(m: dict) -> bool:
    """고를 옵션이 있는 메뉴인가? (HOT 가능 · 디카페인 변경 가능)"""
    return (not m.get("food") and not m.get("ice_only")) or bool(m.get("decaf"))


def opt_label(m: dict) -> str:
    a = []
    if not m.get("food") and not m.get("ice_only"):
        a.append("HOT")
    if m.get("decaf"):
        a.append("디카페인")
    return "·".join(a) + " 고르기"


def pick(name: str):
    st.session_state["sel"] = name
    st.toast("아래 ‘옵션’ 창에서 골라 담아 주세요 ↓", icon="👇")


def add_to_cart(name: str, temp: str, shot: bool, qty: int):
    cart = st.session_state.setdefault("cart", [])
    for it in cart:
        if (it["n"], it["t"], it["s"]) == (name, temp, shot):
            it["q"] = min(it["q"] + qty, 20)
            break
    else:
        cart.append(dict(n=name, t=temp, s=shot, q=qty))
    st.toast(f"{name}을(를) 담았어요", icon="✅")


def quick_add(name: str):
    """카드에서 한 번에 담기 (음료는 ICE, 디카페인 없음으로 담고, 바꾸려면 '옵션 선택'을 쓰세요)"""
    m = ALL[name]
    temp = "" if m.get("food") else "ICE"
    add_to_cart(name, temp, False, 1)


def remove_item(i: int):
    cart = st.session_state.get("cart", [])
    if 0 <= i < len(cart):
        del cart[i]


def clear_cart():
    st.session_state["cart"] = []


def clear_key(key: str):
    st.session_state.pop(key, None)


def unit_price(it) -> int:
    return ALL[it["n"]]["price"] + (DECAF_PRICE if it["s"] else 0)


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


def visual(m: dict, uid: str, big: bool = False) -> str:
    """메뉴 그림: 사진이 있으면 사진, 디저트·샌드위치는 이모지, 나머지는 컵 그림"""
    if m.get("img"):
        b64 = menu_photo(m)
        if b64:
            return f'<img class="ph{" big" if big else ""}" src="{data_uri(b64)}" alt="{esc(m["name"])}">'
    if m.get("food") or (m.get("emoji") and not m.get("c")):
        return f'<span class="emo{" big" if big else ""}" aria-hidden="true">{m.get("emoji", "🍽️")}</span>'
    return cup(*m["c"], uid=uid)


def price_txt(m: dict) -> str:
    return f'{won(m["price"])} ~ {won(m["price_to"])}' if m.get("price_to") else won(m["price"])


def opt_txt(it) -> str:
    m = ALL[it["n"]]
    parts = [p for p in (it["t"], DECAF_LABEL if it["s"] else "") if p]
    parts.append(f'{it["q"]}개' if m.get("food") else f'{it["q"]}잔')
    return " · ".join(parts)


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
        t, d = "예약 QR(번호)을 꼭 저장하세요!", "QR이나 예약번호가 없으면 셔틀을 탈 수 없어요. 아래 ‘QR 이미지 저장하기’를 누르거나 화면을 캡처해 두세요."
    else:
        t, d = "주문 QR(번호)을 꼭 저장하세요!", "QR이나 주문번호가 없으면 음료를 받을 수 없어요. 아래 ‘QR 이미지 저장하기’를 누르거나 화면을 캡처해 두세요."
    return f'<div class="codewarn" role="alert"><b class="cw-ic">!</b><div><strong>{t}</strong>{d}</div></div>'


@st.cache_data(show_spinner=False)
def qr_png(code: str) -> bytes:
    buf = io.BytesIO()
    segno.make(code, error="m", micro=False).save(buf, kind="png", scale=10, border=3, dark="#07261C")
    return buf.getvalue()


def qr_block(code: str, hint: str):
    """예약·주문 번호를 QR 그림으로 보여 주고, 이미지로 저장할 수 있게 해요"""
    if segno is None:
        return
    png = qr_png(code)
    md(f'<div class="qrbox"><img src="data:image/png;base64,{base64.b64encode(png).decode()}" alt="{esc(code)} QR">'
       f'<div class="qrcode">{esc(code)}</div><div class="qrhint">{hint}</div></div>')
    st.download_button("QR 이미지 저장하기", png, f"{code}.png", "image/png", key=f"qrdl_{code}", type="primary")


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


def timeline(stops, board, alight, times=None) -> str:
    """내가 타는 구간(승차 → 하차)만 시간과 함께 보여줘요. 정류장이 많으면 가운데는 줄여서 보여줘요."""
    bi, ai = stops.index(board), stops.index(alight)

    def item(i):
        mark = "승차" if i == bi else "하차" if i == ai else ""
        tm = f'<span class="tm">{esc(times[i])}</span>' if times else ""
        return f'<li class="on"><i></i>{tm}<span class="nm">{esc(stops[i])}</span>{f"<em>{mark}</em>" if mark else ""}</li>'
    seg = list(range(bi, ai + 1))
    if len(seg) > 9:
        parts = ([item(i) for i in seg[:4]]
                 + [f'<li class="on gap"><i></i><span class="nm">중간 {len(seg) - 8}개 정류장 경유</span></li>']
                 + [item(i) for i in seg[-4:]])
    else:
        parts = [item(i) for i in seg]
    return f'<ul class="tl">{"".join(parts)}</ul>'


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
    groups = [("주중", "w", "평일에 운행하는 노선"), ("주말", "e", "금요일·일요일에 운행하는 노선"),
              ("셔틀", "s", "캠퍼스 사이를 오가는 셔틀")]
    out = [f'<div id="routes" class="sec">{nextstop(4, "운행 노선")}<h2>어디서든 학교까지</h2>'
           '<p class="lead">2026학년도 통학버스 노선이에요. 버스 예약 화면에서 캠퍼스를 고르면 그곳에서 이용할 수 있는 버스가 나와요.</p>']
    for k, c, desc in groups:
        rts = [rt for rt in BUS_ROUTES if rt["cls"] == k]
        if not rts:
            continue
        out.append(f'<div class="rgroup"><h3>{k}</h3><span>{desc}</span></div><div class="rgrid">')
        for rt in rts:
            n, first = len(rt["times"][0]), rt["times"][0][0]
            small = f'{rt["kind"]} · ' + (f"하루 {n}회" if n > 1 else f"{first} 출발")
            name = rt["title"].split(" · ", 1)[1]
            out.append(f'<div class="rc {c}"><span class="k {c}">{k}</span><b>{esc(name)}</b><small>{small}</small></div>')
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
    md('<div class="stripe"></div>')
    home_inside()
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
            rt = routes[kind][route]
            stops, times, days = rt["stops"], rt["times"], rt["days"]
            md('<div class="fs"><em>2</em>탑승 정보</div>')
            c1, c2 = st.columns(2)
            board = c1.selectbox("승차 정류장", rt["board"], key=f"board_{campus}_{route}")
            alight = c2.selectbox("하차 정류장", rt["alight_for"][board], key=f"alight_{campus}_{route}_{board}")
            bi, ai = stops.index(board), stops.index(alight)
            c3, c4 = st.columns(2)
            today = now_kst().date()
            day = c3.date_input("탑승일", value=today, min_value=today, max_value=today + timedelta(days=30),
                                format="YYYY-MM-DD", key="b_day")
            run = c4.selectbox("승차 시간", list(range(len(times[0]))), format_func=lambda j: times[bi][j], key=f"time_{campus}_{route}")
            time_ = times[0][run]                      # 같은 버스(회차)를 구분하는 기준 = 첫 정류장 출발 시간
            btime, atime = times[bi][run], times[ai][run]
            run_times = [t[run] for t in times]
            if days:
                st.caption(f"이 노선은 {days_text(days)}에만 운행해요.")
            if rt["note"]:
                st.caption(rt["note"])
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
                elif days and day.weekday() not in days:
                    st.error(f"{route}은(는) {days_text(days)}에만 운행해요. 탑승일을 바꿔 주세요.")
                elif not seat:
                    st.error("좌석을 선택해 주세요.")
                elif seat in taken_seats(campus, kind, route, day_s, time_):
                    st.error("방금 다른 분이 이 좌석을 예약했어요. 다른 좌석을 골라 주세요.")
                else:
                    code = new_code("reservations", "K-")
                    row = dict(code=code, created_at=now_kst().isoformat(timespec="seconds"), campus=campus, kind=kind,
                               route=route, ride_date=day_s, depart_time=time_, name=name.strip(),
                               board_stop=f"{btime} {board}", alight_stop=f"{atime} {alight}", seat=seat, status="예약")
                    try:
                        db_insert("reservations", row)
                    except Exception as e:
                        st.error(f"저장에 실패했어요. {explain(e)}")
                    else:
                        ss["ticket"] = dict(code=code, kind=kind, route=route, campus=campus, seat=seat, time=time_, day=day_s,
                                            when=f"{day.month}월 {day.day}일 ({WEEKDAY_KO[day.weekday()]}) {btime}",
                                            name=name.strip(), board=board, alight=alight, stops=stops, times=run_times)
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
                      timeline(t["stops"], t["board"], t["alight"], t.get("times"))
                      + f'<dl><dt>탑승</dt><dd>{esc(t["when"])}</dd><dt>좌석</dt><dd>{esc(t["seat"])}</dd><dt>이름</dt><dd>{esc(t["name"])}</dd></dl>',
                      ok=True, note="탑승할 때 학생증과 아래 QR(또는 예약번호)을 보여 주세요. 예약번호는 음료 주문·예약 조회 화면에서도 쓸 수 있어요. 취소는 아래 버튼이나 ‘예약 조회’ 탭에서 할 수 있어요.")
            qr_block(t["code"], "탑승할 때 이 QR을 보여 주세요")
            if bus_cancelable(t["day"], t["time"]):
                cancel_box("reservations", t["code"], "이 예약 취소하기", "bk", "ticket", "b_cancelled")
            st.button("다른 좌석 예약하기", key="b_new", on_click=clear_key, args=("ticket",))
        else:
            warn = ""
            if days and day.weekday() not in days:
                warn = '<span class="warnchip">이 날은 운행하지 않아요. 탑승일을 바꿔 주세요.</span>'
            pass_card("승차권 미리보기", esc(route), f"{day.month}월 {day.day}일 ({WEEKDAY_KO[day.weekday()]}) · {btime} 승차",
                      timeline(stops, board, alight, run_times)
                      + f'<dl><dt>캠퍼스</dt><dd>{esc(campus)}</dd><dt>구분</dt><dd>{esc(kind)}</dd><dt>좌석</dt><dd>{esc(seat) if seat else "선택 전"}</dd>'
                        f'<dt>이름</dt><dd>{esc(name.strip()) or "입력 전"}</dd></dl>{warn}')
            md("""<ul class="notes"><li>탑승일은 오늘부터 30일 뒤까지 고를 수 있어요.</li>
            <li>같은 시간·노선에서 이미 예약된 좌석은 회색으로 표시돼요.</li>
            <li>탑승할 때 학생증과 예약번호를 보여 주세요. 예약번호가 없으면 탈 수 없으니 꼭 저장해 두세요.</li><li>출발 10분 전까지 정류장에 도착해 주세요.</li></ul>""")


# ───────────────────────── 음료 주문 ─────────────────────────
def menu_card(i: int, m: dict, selected: bool):
    tag = f'<span class="tg">{m["tag"]}</span>' if m.get("tag") else ""
    ice = '<span class="mc-i">ICE 전용</span>' if m.get("ice_only") else ""
    photo = " ph-on" if menu_photo(m) else ""
    pcls = " rg" if m.get("price_to") else ""
    with st.container(key=f"{'mis' if selected else 'mi'}_{i}"):
        md(f'<div class="mc{photo}">{visual(m, f"m{i}")}{tag}</div>'
           f'<div class="mc-n">{m["name"]}</div><div class="mc-d">{m["desc"]}</div>'
           f'<div class="mc-p{pcls}">{price_txt(m)}{ice}</div>')
        if has_opts(m):
            st.button(f"{opt_label(m)} ✓" if selected else opt_label(m), key=f"pick_{i}", type="primary" if selected else "secondary",
                      on_click=pick, args=(m["name"],))
        st.button("＋ 바로 담기", key=f"quick_{i}", on_click=quick_add, args=(m["name"],))


def page_drink():
    pagehead("음료 주문", "버스를 기다리는 동안 먹고 마실 음료와 간식을 미리 주문해 두세요. 도착하면 1층 카운터에서 받아요.", "CAFE · 음료 주문")
    cart = st.session_state.setdefault("cart", [])
    st.session_state.setdefault("sel", ORDER[0])
    left, right = st.columns([1.5, 1], gap="large")

    with left:
        with st.container(key="menu"):
            cat = st.radio("종류", list(MENU), horizontal=True, key="d_cat", label_visibility="collapsed")
            if ALL[st.session_state["sel"]]["cat"] != cat:
                st.session_state["sel"] = MENU[cat][0]["name"]
            items = MENU[cat]
            n_cart = sum(c["q"] for c in cart)
            md(f'<div class="cartbar"><span class="ico">[[cart]]</span>'
               + (f'담은 메뉴 <b>{n_cart}개</b> · 합계 <b>{won(cart_total(cart))}</b>' if cart else '메뉴를 여러 개 담을 수 있어요. ‘＋ 바로 담기’를 누르세요.')
               + '</div>')
            for r in range(0, len(items), 3):
                cols = st.columns(3)
                for col, m in zip(cols, items[r:r + 3]):
                    with col:
                        menu_card(ORDER.index(m["name"]), m, m["name"] == st.session_state["sel"] and has_opts(m))
        sel = st.session_state["sel"]
        m = ALL[sel]
        if has_opts(m):
            with st.container(key="opt"):
                md(f'<div class="opt-h">{visual(m, "big", big=True)}<div><h3>{sel}</h3><p>{m["desc"]}</p></div><div class="pr">{price_txt(m)}</div></div>')
                c1, c2 = st.columns(2)
                if m.get("food"):
                    temp = ""
                    c1.markdown("&nbsp;", unsafe_allow_html=True)
                else:
                    temp_opts = ["ICE"] if m.get("ice_only") else ["ICE", "HOT"]
                    temp = c1.radio("온도", temp_opts, horizontal=True, key=f"t_{sel}")
                qty = int(c2.number_input("수량", 1, 10, 1, key="qty"))
                shot = False
                if m.get("decaf"):
                    shot = st.checkbox(f"{DECAF_LABEL} (+{won(DECAF_PRICE)})", key=f"shot_{sel}")
                if m.get("ice_only"):
                    st.caption("이 메뉴는 ICE로만 만들 수 있어요.")
                unit = m["price"] + (DECAF_PRICE if shot else 0)
                if m.get("price_to"):
                    st.caption("가격이 범위인 메뉴는 최저가로 먼저 계산해요. 정확한 금액은 카운터에서 확인해 드려요.")
                st.button(f"장바구니 담기 · {won(unit * qty)}", key="add", type="primary",
                          on_click=add_to_cart, args=(sel, temp, shot, qty))
        st.caption("메뉴와 가격은 매장 사정에 따라 달라질 수 있어요.")

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
                qr_block(order["code"], "음료를 받을 때 이 QR을 보여 주세요")
                cancel_box("orders", order["code"], "이 주문 취소하기", "dk", "d_ticket", "d_cancelled")
                st.button("새로 주문하기", key="d_new", on_click=clear_key, args=("d_ticket",))
            else:
                n_items = sum(c["q"] for c in cart)
                md(f'<div class="ch"><span class="ico">[[cart]]</span><h3>주문 내역</h3>{f"<span class=n>{n_items}개</span>" if n_items else ""}</div>')
                if not cart:
                    md('<div class="empty">아직 담은 메뉴가 없어요.<br>메뉴를 고르고 장바구니에 담아 주세요.</div>')
                else:
                    with st.container(key="cartlines"):
                        for i, it in enumerate(cart):
                            a, b = st.columns([5, 1], vertical_alignment="center")
                            opt = opt_txt(it)
                            a.markdown(squash(f'<div class="cl"><span>{esc(it["n"])}<small>{opt}</small></span><span>{won(unit_price(it) * it["q"])}</span></div>'),
                                       unsafe_allow_html=True)
                            b.button("삭제", key=f"rm_{i}", on_click=remove_item, args=(i,))
                    md(f'<div class="tot"><span>합계</span><b>{won(cart_total(cart))}</b></div>')
                    if any(ALL[c["n"]].get("price_to") for c in cart):
                        st.caption("※ 가격이 범위인 메뉴는 최저가로 계산했어요. 정확한 금액은 카운터에서 확인해요.")
                name = st.text_input("이름", placeholder="홍길동", max_chars=20, key="d_name")
                how = st.radio("수령 방법", PICKUP_HOW, horizontal=True, key="d_how")
                when = st.selectbox("수령 시간", PICKUP_WHEN, key="d_when")
                bus = st.text_input("버스 예약번호 (선택)", placeholder="K-7HQ3X", key="d_bus")
                if st.button("음료 주문하기", type="primary", key="d_order"):
                    if not cart:
                        st.error("먼저 메뉴를 담아 주세요.")
                    elif not name.strip():
                        st.error("이름을 입력해 주세요.")
                    else:
                        lines = [(it["n"], opt_txt(it)) for it in cart]
                        items_txt = ", ".join(f"{n}({q})" for n, q in lines)
                        total = cart_total(cart)
                        code = new_code("orders", "D-")
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
    table = "orders" if is_order(code) else "reservations"
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
        if row["status"] == "탑승완료":
            st.caption("이미 탑승 처리된 예약이에요.")
        if row["status"] == "예약":
            qr_block(row["code"], "탑승할 때 이 QR을 보여 주세요")
            when = datetime.fromisoformat(f'{row["ride_date"]} {row["depart_time"]}').replace(tzinfo=KST)
            if when > now_kst():
                cancel_box("reservations", row["code"], "예약 취소하기", "lc")
            else:
                st.caption("이미 지난 예약은 취소할 수 없어요.")
    else:
        pass_card(f"음료 주문 · {row['status']}", row["code"], esc(f"{row['pickup_how']} · {row['pickup_when']}"),
                  f'<div class="ol"><div><span>{esc(row["items"])}</span></div></div>'
                  f'<div class="tot"><span>합계</span><b>{won(int(row["total"] or 0))}</b></div>', ok=not done)
        if row["status"] == "수령완료":
            st.caption("이미 수령한 주문이에요.")
        if row["status"] == "접수":
            qr_block(row["code"], "음료를 받을 때 이 QR을 보여 주세요")
            cancel_box("orders", row["code"], "주문 취소하기", "lc")


def show_df(df):
    try:
        st.dataframe(df, width="stretch", hide_index=True)      # 최신 Streamlit
    except Exception:
        st.dataframe(df, use_container_width=True, hide_index=True)   # 구버전


def decode_qr(file) -> str:
    """카메라로 찍은 사진에서 QR 글자를 읽어요"""
    if cv2 is None:
        return ""
    try:
        img = Image.open(file).convert("RGB")
        det = cv2.QRCodeDetector()
        for scale in (1.0, 0.5):
            im = img if scale == 1.0 else img.resize((int(img.width * scale), int(img.height * scale)))
            txt, _, _ = det.detectAndDecode(cv2.cvtColor(np.array(im), cv2.COLOR_RGB2BGR))
            if txt:
                return txt.strip().upper()
    except Exception:
        pass
    return ""


def chk_done(table: str, code: str, status: str):
    try:
        db_update(table, code, status=status)
    except Exception as e:
        st.session_state["chk_msg"] = ("err", explain(e))
    else:
        st.session_state["chk_msg"] = ("ok", f"{code} · {status} 처리했어요.")
        st.session_state["chk_n"] = st.session_state.get("chk_n", 0) + 1   # 입력칸·카메라 비우기


def check_panel():
    """직원용: QR을 찍거나 번호를 넣으면 내용을 보여 주고, 탑승·수령 처리를 해요"""
    ss = st.session_state
    msg = ss.pop("chk_msg", None)
    if msg:
        (st.success if msg[0] == "ok" else st.error)(msg[1])
    n = ss.get("chk_n", 0)
    code = ""
    if cv2 is not None:
        shot = st.camera_input("QR을 카메라로 찍어 주세요", key=f"chk_cam_{n}")
        if shot:
            code = decode_qr(shot)
            if not code:
                st.warning("QR을 읽지 못했어요. 더 가까이, 밝은 곳에서 다시 찍거나 번호를 직접 입력해 주세요.")
    typed = st.text_input("번호 직접 입력 (QR이 안 읽힐 때)", placeholder="K-7HQ3X", key=f"chk_txt_{n}")
    code = code or typed.strip().upper()
    if not code:
        return
    table = "orders" if is_order(code) else "reservations"
    try:
        rows = db_select(table, code=code)
    except Exception as e:
        st.error(f"조회에 실패했어요. {explain(e)}")
        return
    if not rows:
        st.error(f"{code} 번호를 찾을 수 없어요.")
        return
    r = rows[0]
    if table == "reservations":
        st.markdown(f"**{r['name']}** · {r['campus']} {r['kind']} · {r['route']}  \n"
                    f"{r['ride_date']} {r['depart_time']} · 좌석 **{r['seat']}** · {r['board_stop']} → {r['alight_stop']}")
        ok_status, done_status, label = "예약", "탑승완료", "탑승 확인"
    else:
        st.markdown(f"**{r['name']}** · {r['items']}  \n합계 {won(int(r['total'] or 0))} · {r['pickup_how']} {r['pickup_when']}")
        ok_status, done_status, label = "접수", "수령완료", "수령 확인"
    if r["status"] == "취소":
        st.error("취소된 번호예요. 탑승·수령할 수 없어요.")
    elif r["status"] == done_status:
        st.warning("이미 사용된 번호예요. 같은 QR을 두 번 쓸 수 없어요.")
    elif r["status"] == ok_status:
        if table == "reservations" and r["ride_date"] != f"{now_kst():%Y-%m-%d}":
            st.warning(f"오늘 탑승일이 아니에요. (탑승일 {r['ride_date']})")
        st.button(label, key="chk_ok", type="primary", on_click=chk_done, args=(table, code, done_status))


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
        st.markdown("#### 탑승·수령 확인")
        check_panel()
        st.divider()
        try:
            res, orders = db_select("reservations"), db_select("orders")
        except Exception as e:
            st.error(f"데이터를 불러오지 못했어요. ({e})")
            return
        active = [r for r in res if r["status"] == "예약"]
        m = st.columns(4)
        m[0].metric("유효 예약", len(active))
        m[1].metric("오늘 탑승", sum(r["ride_date"] == f"{now_kst():%Y-%m-%d}" and r["status"] in ("예약", "탑승완료") for r in res))
        m[2].metric("접수 주문", sum(o["status"] == "접수" for o in orders))
        m[3].metric("탑승·수령 완료", sum(r["status"] == "탑승완료" for r in res) + sum(o["status"] == "수령완료" for o in orders))
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
                    db_update("orders" if is_order(target) else "reservations", target, status="취소")
                    st.rerun()
                except Exception as e:
                    st.error(explain(e))


def page_lookup():
    pagehead("예약 조회", "예약번호(K-…)나 주문번호(D-…)와 이름으로 확인하고, 필요하면 취소할 수 있어요.", "TICKET · 예약 조회")
    _, mid, _ = st.columns([1, 2.2, 1])
    with mid:
        with st.container(key="lookup"):
            code = st.text_input("예약번호 · 주문번호", placeholder="K-7HQ3X", key="l_code")
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

if "page" not in st.session_state:   # 새로 들어오면 항상 홈 (QR 주소 ?p=bus 같은 건 처음 한 번만 반영)
    _want = st.query_params.get("p") if QR_LINKS else None
    st.session_state["page"] = QUERY_TO_PAGE.get(_want, "홈") if _want else "홈"
    try:
        st.query_params.clear()           # 주소를 깨끗하게 → 새로고침하면 다시 홈
    except Exception:
        pass

md(CSS.replace("__EXT__", f"url(data:image/jpeg;base64,{img('exterior.jpg', 1920)})" if img("exterior.jpg", 1920)
               else "linear-gradient(135deg,#17694F,#9CCBEA 62%,#F3A6B8)").replace("__NAVICONS__", nav_icon_css()))
topnav()
page = st.session_state["page"]

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
