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
OPEN_HOURS = "평일 07:00 – 20:00"                              # ← 예시: 실제 운영 시간으로 수정

# 뀨 스탑은 글로벌캠퍼스(논산)에만 있어요. 아래 주소·좌표는 예시예요 → 실제 값으로 수정
CAMPUSES = {
    "글로벌캠퍼스": dict(city="논산", name="뀨 스탑 (건양대학교 글로벌캠퍼스)",
                     address="충남 논산시 대학로 121 건양대학교 글로벌캠퍼스 내",   # ← 예시
                     lat=36.18119, lon=127.10870, hours=OPEN_HOURS),               # 글로벌캠퍼스 뀨 스탑 자리 (Plus Code 8Q8954J5+FFG 에서 계산)
}
CAMPUS_LIST = list(CAMPUSES)                                     # 뀨 스탑(카페)이 있는 캠퍼스 → 홈의 위치·지도에 쓰여요
BUS_CAMPUSES = ["글로벌캠퍼스", "메디컬캠퍼스"]                   # 통학버스 승강장이 있는 캠퍼스 (대전 메디컬캠퍼스에는 카페는 없지만 버스 승강장은 있어요)
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
        # variants = 종류별 가격. ※ 종류별 가격은 메뉴판에 없어서 임시 예시예요 → 실제 가격에 맞게 고쳐 주세요
        dict(name="쿠키", price=2000, desc="수제 버터 · 초코칩 중에 골라요", emoji="🍪", food=True,
             variants=[("수제 버터", 2000), ("초코칩", 2500)]),
        dict(name="마카롱", price=2500, desc="크림이 듬뿍 든 뚱카롱 (뚱카롱 기준)", emoji="🧁", food=True,
             variants=[("기본 맛", 2500), ("특별 맛", 2800)]),
        dict(name="칩", price=2000, desc="바나나칩 · 섭칩 · 칩류 중에 골라요", emoji="🥔", food=True,
             variants=[("바나나칩", 2000), ("섭칩", 2500), ("칩류", 3000)]),
        dict(name="떡", price=2200, desc="찰떡 · 크림 찹쌀떡 등 중에 골라요", emoji="🍡", food=True,
             variants=[("찰떡", 2200), ("크림 찹쌀떡", 2800)]),
        dict(name="시리얼컵", price=3500, desc="우유 · 요거트 포함 중에 골라요", emoji="🥣", food=True,
             variants=[("우유 포함", 3500), ("요거트 포함", 4000)]),
        dict(name="아사이볼", price=5500, desc="과일과 그래놀라를 올린 아사이볼", emoji="🫐", food=True,
             variants=[("기본", 5500), ("토핑 추가", 6500)]),
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
BUS_PICKUP = "버스 탈 때 수령"
PICKUP_WHEN = ["지금", "10분 후", "20분 후", "30분 후", "1시간 후", BUS_PICKUP]
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


for _ms in MENU.values():
    for _m in _ms:
        if _m.get("variants"):
            _ps = [p for _, p in _m["variants"]]
            _m["price"] = min(_ps)
            if max(_ps) > min(_ps):
                _m["price_to"] = max(_ps)
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
            try:
                im.save(buf, "WEBP", quality=85, method=6)     # 투명 배경도 되고 용량이 훨씬 작아요
            except Exception:
                buf = io.BytesIO()
                im.save(buf, "PNG", optimize=True)
        else:
            im.save(buf, "JPEG", quality=82, optimize=True, progressive=True)
        raw = buf.getvalue()
    except Exception:
        pass
    return base64.b64encode(raw).decode()


def data_uri(b64: str) -> str:
    """base64 사진이 PNG면 png로, 아니면 jpeg로 표시해요"""
    kind = "png" if b64.startswith("iVBOR") else ("webp" if b64.startswith("UklGR") else "jpeg")
    return f"data:image/{kind};base64,{b64}"


def img(name: str, width: int = 1400) -> str:
    """사진을 줄여서 base64로 바꿔요. 파일이 없으면 빈 문자열(→ 그라데이션으로 대체)"""
    p = find_asset(name)
    if not p:
        return ""
    return _img(str(p), p.stat().st_mtime, width)


# 양말차 사진 비상용 사본: assets 폴더에서 사진을 못 찾아도 이 사진이 나와요
FALLBACK_IMGS = {"yangmatcha.png": "UklGRkBSAABXRUJQVlA4WAoAAAAQAAAAKwEAkAEAQUxQSBASAAAB/yckSPD/eGtEpO4TjiTJbZtdLJyh/z+YQQAVfI3o/wTg7EmA5AIeJN1LJNJVRTZDzwrudNIzYhdJAxh7pISQqCkxF9KGBSRpJiKLnFIFJj+S8nxCJbSBddOkAbzEbGSLoMJOC4tsi+BO2OXhP7FTwzJ2Dxz7zSwhErAKfBV334F0DoMkSnttzbEZjttGciQp/7BnttqcfUbEBPAleji33AGrN/OjOWIyAbDxFtt78Zk36PNQ6GzwDg1xMJLJJeYTxA5ueUnaK+1btlKg7Y9JE0u+fBql44XZKsaHvTxe5MNj4///1Nxon+/vnLE6itk0qNvX2rZt27Zt2zbKbNeK1lunqW3EaTPX+X8fzEzmSubk/O9H90bEBFiObdt1I2nfB0bXrC1oV9qm9qE9KAfaBq21ztKSKYmkJsSXAD408M8gGT36975pREwA//P//2tZB5qmcZ5NLyGwwHY7JEQy+xqyc6t3XggIEvuaNBNJFVN2lt39fdq92J2OAFJOFbH09A9SRIK+/iWHDX70g9O2vX21SNI+BMnAyIpl48vXLBzpLEuq47a8/uztF08kRT7B8st33Bfibve/27x5ozzbfv/HJSRNEy1YvXL+8bdbMcqML188vo+Eskg87oOP+HnLW273iDcfPQCk9EFK8MKXzMGTRLX0KQ9vHdINuJLQNJIu4Flvf+likrJI7/eEnXzp9gFsK3hWMhY9YBWCosUzn8nkBBIzlKA89gWP7iOLeEsrJTvZSWKGFUv6UEz0vGURlYRos1yx8vF3wsqdYNUZrmy7SmLGgomdVEe+B6pO9qckc9xqnDlS+WNXnpp2im03P+qFJJXs7zC988hbR+tDNyixf70JiwOz7CwjX6TEOSdI7F9xRHCAKqWJlnNFNmkPZn/bHLgRNyJniRhc0LvVBwBxAJlrfk8Zyg+x6i83PE8havbkzewllBnB/H+dfhOmbjXR87Q7d4HyQnz+L3e+5w67blDHM7/0n+d3oZwQgz85Yt28LUq142Ts742gjIDuoevi87vCtQMiJXq3RU7IPPJhJTUtmT9XcgaokEGt/re9pgvVFKBtF5GTn3o1iRp33HwhamjSNIUXvL7v+l3zL1jy8k5T6+bSWyKamAIkQJiPvJGDpPgZZUSEpCZVQDkIUajF4qe9rduhFEJ1l3TBIqZWR6ghSfDgH657x0rgkJc8bwUHT2njq576rKc+cM04QKgJRcUdX/W4Lrjsu2t/9JMfI8wNMM7pvF8vNm5avzlRVI1H1dJXHDNEgmD7D+nN8FQYz1+3Ye2JN6vpRLXmfVCFwKmQEg4LKkrY9OGTUaNRKt9LqxBTGsPthNR6G244q4dTSUOsitRTqtF46a24KZAUvXFASB5JWF230SBtWi3tJ0WIg7ACSHSnJgHiRuz2KQQweIi5Y3f2d7R65rtRyLzjro52SckMHHf/JfP+8xHmTPfjnz6nc68BNQgiDT8Gt0UiMXSHB93FwDcZ4ewbjgR6sWiYvqOsNkSCQ5/6wAmgitP/S3JFPu4+VAEETVMrVzCjiISOfviLlkCliNYXSHgq82hSB01UnnM7Yt8UUD7oyfddQEIlqfj31TJX8PjxFs2k0ny0LwqY/9wX3a4gSUz2VylwVdxuL01V9JP2QWLgJacbEsHkVJ55AXZFiaeSioYCZh+F7r/Rrgimlb9JtDyR+PttMg3V2kNMEww/+nhMML15xVIHjioYCImG6sI34KlMa043DvYxfNgLbQ6mwSMTprHGtReRJgk6WiQxw1cc66L2JE0nja7HNFZzIXhSYuE8ZlwNvL0wNR8Amib0uNvUYNCpKE3qu9dyrJnwsAenqDnRuXJlD8UUUfIpm8Zadfz3bKbsvNsyxExTz/O6Ta1L3P/UrTt+v4IIKeC4y1JqLFXn3ncRFcT2rVRiptaj7uOoNQUvvMm2N92TyQte9A8nGmpy5453Ey2k6qxKzLwq3tiD6sxweRdVgHZ/5svXH/WoxxyJFZIkB6c/HAHBv7eGZ+bg2RTUedL6f6IAUnD21cfMww6iet03lyBAXHc57XScvYCotdj5UzO1ESQhGumeky7b8teLCCAYu4X26vsEdZ44bUukqSAh0UytnQ/eACEmlV/BboO148s1p20X0YxT/OrJu6kAxGss0cYUJ1wU1LjilrNoyIZnf7eYVHDMdTZtdDHxGYo6K/nrTqVmRIoz770VUJQ/cou2aN16VGPBpZdLNGXrOd8OCB4/kVJb4NsE9S1WbBBhTjrtCXswf9ss0953HZFqTFrwR0Scre0P3VjGlRtor9LI22WGO/R9E+qq+ObzEv9o4fb4Yfd0UV/BY6qkUFm7v/jAS64J2hoefE4k1ZYYWeeKcP9qCLVHfsDRpr6DR+1NKVpmENNWecEDUI2Jr7tytMC0/cFDRI0RFzg1MLVJHL0yUePyvIp8rMbuQK1Hun0+SHGHEtWYEvcmFbnA7RYkajmmonXkGrLRyxejekpTeeGrcJELA/ekpjseMX5Nz8KujkVH4SALZfo7rToSK16voYrJDnJxYTK1HLqr3UcyRJCLR4yiWkp8YyUJCpGN9i13I6jl0DfXgMjJlF5FQS0nXbCBVJCTKc6bT9QTbGmlksz48737VUdKdFZhcrPa9WIVtSNYtqYDIj/8Y4qoGSWOPy7I0ZRufgZErQRj911JyhLbuz41n2JI3LukF5QlYHrvNEyoLiQ9iErka8X4YaCaYGLVkSJnJZa9uxPVgtx7O3LX+CNFqAaky5d3OXeIqnoqUQPRumo+GVx581w0+3T5aJmUP05+KTHrolUsksigyid3odnG685EHh8+OtvE4w2ZLMaXzTKxbcjnoXFmd8QY5dOeHbMrePrJssm+5gI8i8Tw+iSyWVffPKuCB1ZGPre+tUuz68Pus6mKH39fZhaLY8hmca/lDma3skmed2dmufm3c4lyiWYbbFRkkVGvrFlmLr0WZ5DkPaIGz19Pyh9x5VWVZ5+jdUoVzp3Etn8h6lBrt6jKnKiOeiiFayF47BFE3gCWeoZR6t8BKXNSz2D6vEsInDHGnuEUm75xLuFskbEaEGDrCb/cLucKdiwHxcEVp2+VM0Us7gcFE/yWXIFdOywQaS04V/b7oYHrQyJX6+3QJO6fUK5MFmhYzFORL9OdMbA6bMnWlqShoc6XJcawGv5HypOkEg0M8BrLEtN/Qj88kwLlSOLyOzQ8527OEksfnxPDu+tqctR6/QcGOPgLcn7A0xINjzi9JXK0PTDAiXP+65QjzSC52PFHKUdmShoerC+eGylDKhIDnHT5R5Azw0GFhgg4ZXN2wOaGYU5xy79I2bF6Qz9IBNeTn7sCDZM5NxXOC7HpjaH651aRm+8Z7usuITtfYgOVdOMZODeuGWyl68jOZrjg23PkhdHV2dcbnBOyzaq+xPJAVoqHB1xXcNrmRlVS490DKSeg29eXbPMtzgmjPNUXIya5cXdSfUGHMkJwS7i2Ch66u1A+gEpEbQeLZ+SE7RtcZ2MLsrIrqXFRfpMTonhHqi+Cd1g+wGadXGMFy7xYYtR48P0aZcQdgy6+acjJ16je1l1GGPcMu3b/pc8GaAeu1OcZYVoOHBQa5YJYbwdOXE5SnwlQrdCgweMUZYK4nzPsYj/DsqHtBg7bf5YNsD4NXeKRXDQaDR10vWXDFNOwiasaZYGMR4yhe1uSi2odWKyz4bhg8E1j+kzYrRzgrygLROfCE7lYz+iHDha6UA6IeZkYenF9RRbAlqThm97nQouHp4osNGZo8JT0gqQsuMdB41pGFtyh4ROLjjwscPGhzIPjEg1fz/0MxU+s93ho244s3BxcgHkezNcuGJ/kgLhrXYDvUQ4s98iFJRY/6DBcrGZJ4TMaXBSPX3OOnhMTzAPsUJOBh1u8LLD4HSvMAxmXGil+G3w0rrZEX3Rr5AKs4gfLDh9FMQ+fmNXIhd4e7lD4prvkA3aqif+GhJfxExvMjasMKJEbL7DgBRM3xNMpeOb42g1YXaPQwXHuhmzxKX3wDjvctP6e6C/2fsCDkiJnZjtPvlgYsb/ukB/1EsVudjL83K0I/hJPl0XsjApzw1pMQic7zvHToVnoYPvoCDDBAie2c0/MjNhvOk+gxEJXrJEf4kkEXsxWuHq7it3tceTKZobiBguS/BCHx8gZS1y1/l3gNGKGXOEdFjbY3/sCc5Li1t3Q+1JsCfyuxdm7GkVNtAdvqobAz4/eHJaRe3NAnohuFrlrJVxJ+6u4mQqcTUywoMk2U2/EPGxQjZE3xQnFTOxanBW3O8Le7v2530RNzLfewKaOGrw5JHnDHMVMPDDCWeOemCvRIG/gFQoZ6TjBXeMGC5ko39N7A+0hKWKwmCB/qgVBW+5w+Kki6IveH7FsUMwambyBQ4vFbILH+ykhNx4d0uj8FosYNJg7GA+gkG1wWMz3RsDtvPVpsibgYrPyqGe8RPGCdo78wTYLAi7aGR4bVcRgtXQJbmJWHpx6EbPrs8mlm97iJcYkXK4rU7hghnkkioJwKdHhdD2PF7YskE+7BeHumT16da6waEEzd8r0QMA3J8NjJcYkhavBaaM4EW1jisklsdgE7AqnxU2BwjX2qk9P82iJ7cIrjBaLFTQNcgrmBFtMpngtXmOxgnmJnIJLwl2d8buUResVya/jnlBbfIN5ZdopihQ6lTjePMXKVK1nyzJWiVc31ntljhXBnuxNXiFKUqREi+F4BQpUcIv8Ene9EWereoXjYnok1IfSM+h2seo6z8xqhSJVLZFfMJkSaPFU43gaLR5RnKBcIb8wakLdynBc3JIi9Yhzl1iYlHiD82+PBPr8zjex6VCctmvfYPVAmMV16Zuo36Io9bypTZ5hx0mcRKHkG1SkKCUWOG/cnE1BMmrv4GpDkJVOY++Meo9iBKtr5JtYLwjzZonzonpEMRLTDvnGqLsjzNc1zisxj1NxNPmGmGFRWmG4975PCpFR437P1x0xttMb/6CtYyS2ryOwe0ARgk2LvBObK0IsFgf8t/42SvUuAhRYhOAhAnC/NQWoZ9wnBeB6SYSNOYkAFg2Kj0YUyD+xuI8Qtrgigmn7KkJidoP804h7LD5QbiKAuDonBeh6QwTPfFlafIyvj0kBgKIgvrZ/QwjF4S4+YvIZigB2/ipCb+bE0HhPis6E30I0Axi3KDb2rntQNANx+RCd5FO6iWYAqynR9csJGmLaf4dCY7bdszEo8QpTZBKbemmM4kUTmyqeTTSGs33389AkNg5LjQF429qiaiwa/etnGHFUXPOCvxRuKLLup4yIZIprnrRWbib96NMLs1CQyus+nHBIZA8QDKztt4qQGlssGtgVTfUM0TBVUzE2BHTPLhyUEotHV8dEqZ8QTrGsCeqxw6KB7VsUk35HOJXoCOq+jwewjspWKCQWEVlhISlIEYGnC4uH+PJgCklJ6sMRrLzMVgPxZhpp1wkpNRBxGm4gwQvtaBypOLuXJir1/4nUNKw9HyGqBkJwx+VE0yg+X6iikQalcZNIKr5/DqKhBnEr0RxS8IWziKqpIK7bglMjsIOrP9qCRHMVk4aED3quktOJt8cSoRX7F68J+6BmKvvPDy0I4nv8/R9vI3wQS5I3P38AgvgqcXvKbyuUDk62xHmvXwBBQzb++JM/o0hyJxGw7qcnQIim3It00vf+MkGSPLEJdq/7xu9vIUSTTsHOH33hhIZELxdsJLjwtBP+BGGatoO9J3z8mwZL6jV0TgrYes6P1l4KYdPEAzjmHRu22thC9SRkhn312tcdV4KCxq4QjNztLeuOHAYqSTUjel0Ap+qHzzmmB4gQjV4Cupc84GHHzA/AFqDZJxBmQD/55sv/fg4QIgNVBNC3+Nj73WH5EFMmW0KaBYJemCU+7OtPv/3+y9sNUITIRaEEDI8ceo+jl88f6BZTpqk0SftFH+gDM57t1+349fjv88kRSEjkpaRkoJg3NjZ/+YLRBeMjQx1TTWuMZ2aY8ax9wKF7uiuX8/H75cMSYIQkclQIElP3DfX3jRy6dM7IQP/YcFdHWYr9eDydDtPrtn58M93vqoLnEyCRt9Ikm2mL/rGRrrKjZ3DkkL7B/q6ys+jo7LzQ4XA4HY+7TbsoqvXexP+bnpHIZiGEjWljlEWZWq2KGUsCYzwp04Wm8yQzrSZpGpv/cxQzNv/zf04DVlA4IApAAAAwAgGdASosAZEBPlEkj0WjoiESqTYQOAUEsrd+PkyL4AGVhu+9/sT7NpGeUfGn9/dzzlOn/Nn6bvMb74D4J+az9mP2X91X03f17/a+wV/Pv691tP9t/4fsM/yL+ff/X15f3M+DX+tf8n9w/gF/lf9k/8v5//IB6AH/d9g/+AdjH/Yvwx/Wb5G+OH6P8pv2o9Z/LP8D92/Z1yj9iWqD8q/J/8z/DemXhr+Zf0noKflH9M/4nzafVZ9p3+drf189h334+2/8vxGP+b1G+0fsDfzz+sf8D2N8QX1b2BP6B/gP9j/ivyc+lz/B/83+0/LH3kfn/+p/8/+o+Af+V/2D/j/3r8mPnO///u6/d72L/11Nwip//fU1WrkKUvwcwpEkaVnk9nOe/IXrtVye6vxRAGmn7tIUpwg0BMp/z75mcttrhSHDcKyGwum3/IPzhZuFrfHgni42XJOPEZkK/LgYxpJG3tr93PBhK7dLPzXQu3qb1QWZ7chSl7UXRLGeGIiC55FWUe0aM+mYo8mJY7bKmT/pjnJPEuoNVHeMt2JpgOrlWChC4cO7xRZQG/GcDzrU5p+MioWNkC1jcHD0pJQT2IGivu2R6L2cXR//zIoKVvefGnxSHX2F1u8ztVhIcV/TP/r6bAAd3GEQ4eA4xE7fJUqQhmtyPWxY1+TrMfgnEJkTbkAoZ6vIOqdG9eZ4P0oPbPspsWdyp4QiHpD8k90VZAKCoDX69tbFhBnrr+Jt7GwdwocR8clbFs2h6t8R70wWvazGGQahFH82W03IhmYPegTImF7Cr3LavzaphzPK4dRMF/aF+CRJkB1hP/TjY4dxHIRfEe9/UrtgXc81qoaU7dL+Key7EQMbbqcNPR9nscS3PziRYs7cyc+18muwzFm4DXUH98QBkHAnvoi6Fvit4p5D/p1Hpf15U5rvgZ8Ke1LNVi18T5bpiGK9Ei4+TGf0TQmWGC8RLXDInNtaDtZi+9drVHyMbwwAoESH9YUDlSvL3V4CcJ34iEpt3VB0lxGQ5xL557YwknNVbK88YMyI+mGRNHwXzzP4SQwZ9Ng1nxRgRuqzBU3JfH8bQXYMwstDFV72FgoEKxg2mk/8BiY67b55Zw3OO39yHPxoVM4qk3k/2HNn/2ru171hBlX4Y4f/+WnzQTHkAQryTud2YtKm53FlkqdPQP28UCl4O2wKy6eCOT1aN8qCFcy4ReZMx8k/JThR4Deh6DVazGTSbJG5jxZ4kMZPXNmm03fZr9wiimPUkDCuqTR4hdG5hIyHKJ9x2N+s+OWjcstZBqGVPeCGtwHFtr2B1ivC6Z+xvt56vnH7dRuXdp8gunTRSHALWbAIkum7jXsSzkXlGOZQqZIesGt19xP5LOxkUc5inIVdAvCz9hlxN9vlbijL65zMkcUmrnWNUTyLjefVNHyrXj4p3cPtz8rjReeaMD+zD8CaJZpBWokhtGV0NLq81NES3T7n+sKUokAf+QETQUf4ZwQx2HCbCX4w6Xq7+lzFizotC2b7EWzw/Wdfi8mY19DYoUpDtT6ikmRPhRmKAjAJ7sRzjYE7iFyEVy7a6sDihTFasRheBHHhLDV9nktGzaZ6DbKepx01jenQ683Cr5m3sNU18YDk/oSFYdQau7ggYOT4POJKjHiMcCdRXmFC9O9R+wDZGYJ6YBUu14OV8JC0wboE7OsJ76Ik6INv/PwxwvzeyVijLUL/2D2bBCiatebUqeBLGTTwShPGgA14DD9CjyORBp/kssVj/AYAxpUVtTb3S91loKcnGPhIa+DXWz/YX2iGeBrsFIttAkp+zRoK6Zfjmge2FbkiMXDPtOybsQkvlijD24/xW8ESoW2M+Z4MNffYxwdGylFuigF5QxPPwcuEFezZa9MOBEuopCrY9mhrxVblOZ2NjwVomm6G+yPnN/Jfjj18AIoFfnvE6wp1aSr5eyOBWv1aLsGKBiT0isR6TV0sF9Flz74+NHjWvVas7doTlWTAMjaF0QyyJwZA5eyMxpfQgf2bAWKJEBdpIIxpqiHCxdTmcKwZNUFFCvDz6PWagYT+DAsfgBwfJNKWjl+O4a6ovJx6khZnMXJBGPmIaDyDaNATuenN/Bo10dCp66ZuvKxnbEM7upMEyV577yNXNqMUAGwvjL7qT4nc+a33hgxEq2nhD2zH8JS+V/pip/9dhBKg44yzjmPiqC9XKWq8ePfl1HSyS6yCbeZiIvO6bkUtqCTSSmXRuBJn34RzVyTJ+5jkqcHMykBgleZrxtP47RaK9rXjMkfeYmd1Dba4QJow5a2DEWMVEuyk39h0hbTztH+oIECTZ0tkDobNzLn80Gub/Iic8dUH5VFzPDII8CuHPeZG1oqx1WRKlH/9xUve5r6YXErUgNW+ieTLRZn5mLmMgP+G0RWw4yQ+XoFkJnYt9lHQfY0cnLNhcApzgyPIsMa5S8sYD+N4Wlh5Qx62spUqDsC/IpZvpzCkSI4SB4s5QAs7UN1h7r6Ze04U3L/dZMbKRxkZUhIOKvH88vWch0NnykQnxg/sJQv31r/BTtfNj4MZm9kqYVWoyUUTaLNQgLal+DlwjYAhB5VYmS3jv+fznv6Pf6PdakuVssBgvcZWwerd3jq7mTyP9jAr77O0JqBp7fGOYUiSN7iqEAPRYlRDJhJJpZA0k3sQo2oMc7r+9YQv9vl6MLR82lTFh0Lpply2SIfPccuP2auugxfPnv/f64UiSQOB67jENZB2NpQKxM+bC3G1Ij4zSgZ9Id6Lycip//fU1WrkKUjAAP771+AAAAAav2RytdjuzTKfEfEha2l1W0f73Mrjc0H8JA3TS6G+/t+puVTiFP7NoJQ9/uF1+Ngy8IPOCZgMtdD+yff29PkJ0KOGDw/3IZB8Dm/owallho3QXB5DaQwgffRgcJdGevShMynHxJAreBHiONKespXAWcUKnykPkT2VSqWwv7T/yPNDaqwLSs9+Q+fQ0K0L0lf2CHErD9ZhJgn50QWrY56KgObUsGIxCY+iAx+7X00FHvwqYiZVzm2F6t0f07PE7O1zCO1JmZsNNSka+yMnw7AoYHDcrhM5PnsPXk2xM9GZ9geEAL3+ApwKEWN4zmMeYUg50YAQG701+DPv/5AFZYPAoGw17wQwJs6Zh3C10sKaNG8qTNXTi3Ns+wGQj0yn3+GZh1yRjFqsOgTWT0qU5h4gAKz64g8cNetWL056FUJ9M1e2wfl1bcc+sW93x6ERkBDVHNkW3k9ErKPpvfayvoWckwyluav8gvzL7vwgC38an5vuwF/JWeqmBMc5N5du9pa+deEKB91I3/Qa1GOzM+Zs8hz18yaTUvxlswWAVRwXQuZv5749vcDmq8mNHfkCrhKFaFirkuS5TXuIxIwbt91U5u+nUcIGKmvHt4pJZDEUjxW2FbsAIUz1w97YtP+Pyjyp5jgY/U8x+30ndslt1IxLZEquveCawLsmEnCP1j9Cv6fyoy9aZeo4TQeo7u/riSLBXZOX4YIB41yhOh6zLMJJekBAtnaed3osU1/Cd2HfgaxeJoMAnw0pqqc6B+c7WliV3tCHAIx6mDXsuxJoXgrk3dNpbtDbyrzPc6vMYuOitVQa1khXS12rBslndIeI4cRJr16hHY0r5Jo7hK2nJ0N+BLpnn1iNksMT6B5ukJRxSVLcJsKWjXYnS9Oi3z6AW7SbsfkLCfYn+mwdz9LKZ6Fz43OTPz/ESfuHz8ALMpJ0+CXY4/GaJjCK80GSYECTHxZgY+pbjTwPQZJi6Dhi5+cmTJZwRU3wAXhb8W0gVR669GmrTR1pAhjyZw/hUIuZBA3ZzELZd/Uz93G+6aBywBRcXTKHDYcpCKtqpzOAjqbzputZP6fTcHg7itggWBEzz1s+CnlntmjQAdvriCv12TYASHW7ZIv8eIXF/qblAtJPW0Uwu7WA11EUWzHOVJV2e0KxJDHUA/fJl8QWSv3da1/vHOVBS+KkxMl6CSbmxe+BqyoyP1YP3Mgg5uAOyPD9zBvEfcj8qRqEhI/zxHVJZIfr1Pps0R5+00abtvOfD/77IniA6x5eMmeom1hO+03jOhp5r+79Ou8+GtpP4TbcQxDga+89qAAeN9D/VDA49vxUE6tBjzNnBAhfCa46Z2JX2l5MoZxmFZf2j6QmUu4FfIpo//bvgyk884uzIqXZAOyCiK/NDOECUo+Pr0hPzMrTBIHkzgaiEwh3QcgDJx6Hl5RRn7pD5by85sa6lrpqaN5bp6GRJL3t5SEurgz69P6c4nmhbCV5A9h96Iiv/dROk5IF96wuWJs0hYnh5DJ/eZl7uskhX3+xf4bJLaIdMrZd9q/2WWqlAp/Ltzs9uvXq3NdqW20Zf2j4+7FYAienTNMviSXMPJ0GSDSAsFdrsWebBlhs3iMZ0/XWhn4cUBkw8ExJAJDG/ogSA/+WVZ9Pr/28CIjVLmOzUzg3KTPUzhpQbXkkD9hpxUIgO5f7OGUO2hTxpwuLt+tIflcayUKA8Xgct6exeMBh2CkuRf/lyzGIu7aXrncrtP1htIPoH2nVK27Y14MgUa1qbCRRIgj0sgNugYCLfKzEFFVoVs59jxBWnBcBRxrPCoTNUgpnVTTY7rF8o0CQXB1F2RfecXAM2/ByrYX5VOiYtTH2TNxzdxTs7gakeOgLn0uuhC3jW5XzHuREODcTnY0A4gGc99Da2JeYQKtQWhhx6EgMgrqYF0Y7oAtF/s7oio+/c9frEL/pB9PB+oD1U1zsItV+ONxZhdM/MFiHYntYcuE/Wa27ppCa+4wnlfLao37qV+Zw0fkxxemyi6NOK7jtsWU2qydOPsqcKElyyTFPG4oXXdPl3/Vvr3zQ1f+ODVLu4/iVwgeikt5w10JFU5ysXzh9Tcn+JE2Msd2voPaqa/LgFIGg4nYrKJ1V2Zwzt6lNCOQpo+9cNr0AlXoLFqZ7Yfu6O53t2o1bcKjf35st/7pr17KZVWrCFCVIAQbevD3cN+AejCPYDIZ6MvgqYfp0UoSIZP1kiTPQKs1dcFEXBKFa9h9HVCq7jBW71ys11GgcP26Rh3CO60WxGusd4taDjcfiJkUZ2JL52LD2QCXsn1BDJwk5lSMroydY98P+IuOWeZQIswsKIh0bsHMBo3xeaXqyalYXDXVowuGiwK/nnHPqfRBzL9npU4Xvvh4f4rerpggaKhCFKA/IVPPE2HAL3r4FivqzrAZH1ztA1rbr2buURD9cc6sixjEz/CkY44PbOn4FQdhth4mvhYR2Ap44KgIlcE1Dh7hrfmUL3Ywzd1FX4V1wShQeG5zimAVVJN1uh/W3ZMfMM1QG6BGRoRjpqYWMYhzkBF4klt/CPhgS8+jj+KSwk3nc0VIUeqZyA5Ts+S7kuaIYAyZK/+hL2IZQBnLkG818DSkPpfcqGYlWMQcUyr5XyOeIwtZBTlTHb8IHNAuKk53qQup9Sbp0/PSNgG+sOZfsmFBJe8Wy/8UOVPYqimm0PCSTy81uqNF5O50uTaTcE5uXfvhFl+Lq4pngOKqMFnqwI6kMzy1NsUmyrG2uhT8Wpjz/G+FVZLpieVZC6jRcI7t2h0JVA4ebu2RcxSd7ueyBjJOPvpBrnwt6UT0SvZ39cUPtI9H6/wiYAl40lLmtHFDo1q9YFSNovYGJn9e+vs1yCZpB4HelwzUD1kDScqaDNIcadREu2UdDyuDmVvba4TqzETht4zsnD9Q76sMJpUT3/5AO1ywrL5R0NEa6BUvQk7wjwtPppi56i7GWpeUTXF2GUXkDXUIH9t9ki/fUAjrfNXwoqZ7gNvlU87mBALh2CYPGMxJmqxrPcd4OV6fyo12zp2PAI1Rd97wl6jeQ+i1+e++dO4Hdla5BLmhR5fIJ6x9rwpHntYJ61kTxjZDdfyjVscXC8uOnksoLjqzZGzmLo1KWnTPBJIiEkpuicZCRQYQvwJMmUcwr0huaodzwT+fVYhxb1C20VguOgmgc6J914Uit9cexCJ5A5aytPveofager6PPmsdduU8ign4bIQYy16n8V4h9Z9nAchk/rICwdcU+V8wkJ7aFsh/O174gIo0pl0JRd7og6pFA0u3cGoyt9z3a8RxX9FqGVI6NmwAdtHljklv7gegF74DAm55QWk+H38aTAUXJsCF9WR5uNDqDSaeztEPhuyYYd6cpVWhncAH45QvmVqOX3UFwqArBzvL5Ro0meaXMVFO2iKWHaqyb+gmhGJwt5cctnqJi3jhiO73YqdDQ+/8w10TDPX+iPIzbSliKy7lKuaqqBWocG5Ub99dUTlKAAmfQrigeAaX9Ks5ZTOnHj9SJp9dUEbBUMs2GeH1B9bUGJ67QbdRIGsK7Ygb6U/DdZEQ0r/nrf4oHw9Jwj3aMnxX0i8LAvpgULx0QCGrb5/cBKF97M3WZZp7fwvWVBNfdWgJKnQSjzGtDxIjfYXZVwdmok1UkyZ3uBcVhxgODBmLIQfzODQFQtes5/99TKftBusxQHoi4vKWHCYWExRCJk2fmIWjAsS/u/ZAKNUdI/G+i34VDPrSgksmmMcHaYsGWSTp9rR34UBpntjKVpnZWxflq7R8gqvwVn/cjDJwV5BuG7eQrngG4HPdqjqvLuyuBDVqToeSV9rcMzpiOCjPVt2RYKIzU+OUrf9UAH4Na8vrd8Jc9EHpF+92XopLwSsYreXYYKQLhHwhDdCP0syVf6rQjkT2TC0pQpHVrexieOvFqvtFTYtfC1780Rap338XC16MA63aa2cA2n9ciyA/Bn+TSAnL9AiRiMAiJ+F0z8bd1dEIXQrjN7spfmTglaCHpuYydEwI/bMWJaD1Aj10M0B9EBRSX+sFRDE7XsgSWNeM8lI0WlOKHIDqB6oa7lQq4a8AsmP7jWMgb93FBgB/22zG7mPTsjHatjPrr7Et9nBgXI9t1/JNERdV4ztMlqIyK6u3Q52Pcis8j9BlTUalBFovKpXw/PE2g9D71B4FS8amoh8ZBXC5moCnnrjMyrGjxtE4hkrXKwc4bujhQAoNqVR6tjgdW/3RJ9WWD5jcCqwnp6kcDafOiitvHTWK/kqoiTN5P47GGXczovZCorMV+YMDCsLSA09AQAYrBdHwT1QxNVfzBf8l/7fJlL6QwhWdl6a+ABfIYigLdIdmnyJnVLDgpr65Jif2r4B/YGeJuN1+eG8leI2bHAh769gN3Ul28Ug7q77LHJo7xpC/HsCMa09CICiFYKbQajXiJ/tNN43P4gTcOWCKeJYzqqccwUjg2iVs9QpJeCpOoidDa/KLGosC9kGjmQvAf0eLV6F3D0Y/Nzdzg6EkxdgaUzgA41sySbUpOxgN050f0H03bKCD5t6v3fXNPAFQRXN+ocND9DRLNHSvMge+ZctvENPxQZ+NzkMGqy4vHDd6E+PGAzEmPMgmWtY+qhMQElI/fR3XBISBfeagO+8Om9i5w2bX8yQSuDbSmBAfG3h1J8xSJ5WRQ1WoXWrQ323dN4o08WY1hpzBT720wubs5KDRACryYL7S64ZjhAJDiTvL31lNaryfoWsxz9ljadTzzQbrSyMlhzZ1XMIVQoQ9J0sA5YVPlWdhod5f/Til2dvfCm9k/OM19F81f2Mi7/ctqknu0/xianA8CY7XJnWeyrHYlevZp0Oi6gB/lX/LMG/kNHRQqZHYIV0CL/jvoE6iUbHToSFp6/+fCLjnSfTt2GRfLDdtJa6hvK6+AGlQka5zWDtm/LRKRmRFAcc2QWzffX5IfAJ/889MjHmqkQwQKsmWy2i6HulADNj/VeBoKD4QTyodVkMC7kCBq4z42f1duHejN3SllCcziV81ezfLVh9XKY23pVYqylt1dvR263mNejXJXWYe3j1vhn3yjAgdSLOqDvkINOUG8gH3C5EOmOgNwOzqKC+Td9EtV0BChflYpTYEP7TMguzFd9NE8gnIWR6XVyxLx4NN3y5RhqmAC94PgVzraXUIKRJ1LVQBpWOxNkAUCyUQUCtu7TroMlYUVqblekTxEvfepHuCIWGnfy6E07Nd8+0F4LqmCFwrso3kdA9nUEdjUJMtKqHGFqg/FDccNKmkGz4ObnNIFohD3tfncdi4i4fcFHlhK0V7NffEBZcHF7f3TlwuVmQkBq8tWht0UmSA3XY0xiliPNIvcqq/PKwGSoLix+k2sNfF/zMm6QO0UNDX9c54LVOOQuk5mw8XAtToLOv3SPKwv1NyGh920PrKjEwLe/vBybYQuXj2aPOxJ/fOit6IdkWPt7c1ayF8d7VSLsV/JWiftKE6bz1nsMCMD9G5F23yzp1A+JpRIE054BZufYTKUnn58mhP+5kntnfZpXD9XQaLTMlFGtn4BKNmHLa47rKQm3rg8Q9TMzhvtl6vBXblc3zUZcUAuBWWYzaZ4c2d8r6CGX02DEH5/yasFmLJOlbqXX3POcP48KmA7EIIctzkOv3VVTO/WxaUYQxFfWe2Ea0Lc64DMZHw1tJJVN0WZT0fk5nu4jv2+qXfkPYjXzvsHzzLChwpJWlGYLurJ+yKaVW92ZxTJEmHAhClbMqjHZGZmskHXeRofi0gZIEMnnpRXY459nPibsIWodz5YBl8sSdgpymnWegIMuILNxKa+6uK390TCkLYgP3bnIFISW/3jH68kJ8XmUm36DnJW0RxnvN98GylgkMTbGUDnpAGWTUv1GP1oHZqy3tYg8Szy7J7EHGK8AYpOJY9IbhGeJXA46Wy+9cyKHPBKwYfpIujDJMrGNA185w9CPmcsB8e98LbhQwMcCwU8p8tUS+pwCA6Y+GVMXDLCLtjHvh9u0rUDzoSNCzPdFIfZ/RnE/Xv6BU1y8D8imyG0PfR8G8mgyPEgQr2lZjNk+eh557bJf+FFsHNKcb2lRvS2TIxMTEx/oA2aWC7za1WBNyRrz043LAU0arM5NM2USnYhGeEHI69UB2hkf73hSf+oSKj/wjWeTUcvAsq0aqlXH0ODfgsEXPzRYGpHL0kXtkZT0YEI1YYEJB8Wjvh3YahUU9Zp+y9b+/Oryt5QErX440a7XB6k9ccHsA0RvAnSMkjKU+ZnFfGOoUgx2u4nlvwGNib6C0yPCwU3w3Dlb+wxpXGNs/DfARPlpE+PNO2Kq8TK7P2pq06bYP8T9vgaNgMsLvnN0pZnWhm99UhNWEyh2IinVsem+dbGoQBTZEO2kLzy0kKmoLs4PDagEtAdND9rfiTCaXNAxNv/vWfIB2uYbpJki3N3GjHqrwqVQxAp6gknWv0GvdWY4JgrkdUIcgGXTM6kD+b9tWGUXM/u4dh5fGem2YPTtkNbh7P5e2fKVvi2/elxn6AQxhnLa6itfOvNA04SjrUWjlEzaRYYoIHLslbzEXo/IH9u6s8oA3kQ6lY+r8B53ZLLpeUFvOvqSwHAXbHma/5XmfO5fLje2/jO/8RwvzTMcnMpECKI7xtX0o0llXvy8tfVF1bQR7WIAOCfxnC/YCRNIO07bR6LqbSSS7q59ek2BXXXY2ANHtkwajjkPrvw8uulAEfPbm3repNmxh+Uh9lI3isQEoJ/NRc4WedzN6fKq3xj+P1VEoXcR+8W6YUSaiKk3FEbVknkJlsubXRsjhkCx2a2fpYXQ8MBrPeB0ib9ZgAzTdH4Xae70V5DIr8Yk9Gzw7ae8kmfQcfQzaU5BpbdBsYygYzPndDHTY2Z7RVLA3AkZU5HG2ZVJ2GRMq/mvX0UmtS27IZJ7NAdLqfuUmq4P34FrtDmwj5IDuBDzp0K/OEJ5Ne6MRD+NPs76scLUD3Fg5ldqhIYOuhGkJsgLZu9F4M0XnXvlzZKZjLdc9NRrKzhTB3rTnTOrZu2SQTyDBN/UI2RQpoQffIcB8iNTvIRZvRe2Bja7w8avd3RjxYdhWJypSmxt+Ea/k3ywTVx+YFbfTnyv3l4ma9IOqcFLIfXKKNgRn2+HqbP4MPsHRgcmK61h7e5AdGWvoSMr0nU3v2EPuH1laTC+YCjSUnYzX16FUEjLoyfQkIlsf8n3P6IfdVC1A1m6SQBoJgQesxiM10cRlp4PWFPs6ZL+SRARynSxWCUUXVAGBSo+3Em75YApnFH1utb85bCHIvnWXQDV06rVzOM9jbPbiBzLrOKbkIU4Lcxl3i+e0pTBDnHzLYEgQbRfPogezsnao1RqBJHu6IDC/zTwLHeeXJj89S8X4qQyAm/cMua8G/l6Ii552TGZgKZasS2V5npqrwLDVDeFRULijosPAKGJgbv9QfpRvvnzTeAkPW+1b3j3RnQBKz7WTRoOWPB12dO+gQT5tP1u9kfaw4ghJmd15GekHW5YlGPWIk97t49gK3ZBt/nwi48gO1pPixIEiOZ0Sd1qWLozM/MU6SduaeSfJx/HOrICMVUIGAxi7LS6h1A1PQrfovLuwJlcQObM9sHcxnPbFBM2jeyTQ809LGNt33NNAWycRIfXmwTlD4WMnRnzBk9NhoNEzdraK+UdQZGNbHFmQjmolaJLefmMV3F4db1eR41IYeYatFIRB6WrBLubsKMGrv95DR7OzjnquP0yNZ/jcI6EqO/8yLoA/J1hq1rrQts4Orfm56/WihPJC/80zx6HaGWsqQyfS3OBx3qHDtHZSf3Tfn9F3rFqH1zchnH2yVNPbgQnjhuLTHxTBFd0cnDaF7RMQSTLNyqEMwWq118dTzs1ypL0i+28sq9lV3YGDNSyctFmzBkkXa3uk1YRf0Fe+FEKMR0tfs16/HHqrhVmclH1kdg6XT5T/W2PlgFR7stTSJf6aKsSZMAW1deqJWGVNhOzjWtd3fk/7yh7Xhj5Bxg4cPwCn7fC3QXB6/pgxac5G1l/JzgruwvFENLIubj4sJnvKHrvkbE3C+ro+uW97Kxd4KTiY54XAcONbJX+BRsVtIMIan0NfT9R8UwaHwO0obtd4pvs9Y6o74PLYVLFMfpVg4jOPrbE30RJU4RI7Yzy9RexgBBhGVJALQLqTiZAhxm/twCFvBNJCx+OQmrAf0Aif5UN+vBAZ/u9jOHDUeeVvkOsuJMsEeJJC25RSsjounL0SHen3QQbxdgXOZfdOOdaddHfgkN85DceXYH+qmjw5tLKC4tSmdIq6eAVNVfjYZIGG4bqhmCYZ8j10aBeOaPTnGl23MeOuSUQY+E2MkZ1q84XT29Zw2+QtknlGFleLutpsl7EsfQJTpBhje4hThtns67stmzNRjsSW/57xMn+hw17MzzSgDB1UqBMNKjqV684G1+GkSSHIyvznPifc89tHCKIn0u9Anefv4453TD5DbYOVRQx93mVbuIDZfj2Dzf6NYzla9sGRQ/oMjJqoI9ETumCzsAW7seXaSuOrex5jxYVL2NTWpuVY+kTDs4BS2F/KTDMnGXNm91JrBRkVZUVvDuZjQRYgBZX9a2bzcCyyP+I9Goo7dTo1+ASFZREvyzarv/nWdgwjto/Tf7OExg+AomJMWg5BuCgxiRgQ6w+eeYe65vRqLa2RSf/7zfHsb9kuO72AswVDR41QYyfO7DOH8DkCeXzXI6pHV+PflTrjUDV2KUqMnPFAjmDQqZVaxF05QNWL3D4KNA6/pR0IhOHOUSgBD0i3JK5C1FzoYBIDOYdXRCfdc8SNvF+CkitJDnDbmS4V4FgoNHIe/DNYCSMLPKgociEQOTAaGbug/g2YyrnBjIFk07HBw1Xa9CA+EADYgrdq9tq0gk5lKs1A0FdKMh+NjIKEIxmRpunMG+FH3utWYj/HEA2kzQJRZ8CWJ6nhMgA5Pu9sq7+5GFkwGTgLhyNQTktwA42EWZxO+mN0QMFwPGrcqMDufYS8gKdO8TzIqbSkCp9PJ/4ejTTTXbJgXGto7hMN+T/U+nzpREnkPJiljUkIFdbGAUUpH69cw6j9Gpt9fXq8bC9bz+dqxcjMpOjE9jkzMQTOqXXiiOyz6H8ylWEzwiDCG9DAamFRQEDJkoA8QYFmy1qDU6Y1rUZL1zf6AKW7AXedbe56gtuiSCFR9m8wfDLXUmxcvv8zaPOeu81mLz/LGpLene3/4QD6O5nrFokTMPLiZzDoxKT4L8t+RuNbAtT8dyrbvxmjcizKxe6PnvEFRfrjtJlvaPwysABKyiA2tsKAdn/byX8bEuudgUkN54gy47DIuC6vwcfJp3jlJrZS7pn7Pbr5acQjXAKdx0gUL4UoPn0LenGf1N/aHrvAACgijAve1xhn9xxiJrWxTeCWDSipNWymHp7kHMKy+w5rh8dkuq+W3OUO6RhXCNAsnJ7rt6xC3MUp08+OBjr3FrknYbHaSc2ZGSN5rY/zyEBBSYuBANKBCkjAEMmmHodfWGyIX2Y55v09uDIjJznGqd5Dxjc2b5Yo3bZcPnD5Qnj1f1dih8j98G02TpB9vGN5fe1AVrRxGl4T+Oxngw0PoifJxHlI0+XOT9LuyJAPfM5ak+nkJiPil+s6ZJKo7Uay458gJQCgFBGYhyQno+QCWD1XoMHLV6j6uHYxl/CJjGvWE4j4vpiArz3B835LN7KQukU3PeyYLRVjkH6v9oHSQik8lHTXixG6o/Hr19aZyFxmg3vWbmcDiwdZ4hM+51B0BWHqM6tKkOWqj8+ojzq0Elp9OFAb84kHd8GSILX5oLIVwBj5VICYMor27fWghvOvtU+B8jJlg5DixIr5Hm3/6uvX/DpPXa0NOlh2RX+HY+0oGacXzGarCA+LKRCICla8UmVom8acEuiJpM/FQzfjZK7xI7eAsuO+maWjfVi/7c9q8Hwr510K+FvIjttmc1OF3Gqcx/p7GQhADv3UGNxKu3tZJb/h1//jMQ6DwHfvOrnh+YeEkCOn4KkgPocsZuswt2ApB5zYOUQ8DDKq7Hpp+smqdUuSUyxFZDGZUiQZTC3Nl1GX/wHRabSrSZ+8vXEv6uhEBXk+zvrtGEtVbwxC9j62oBG5xMiszaxehTUOGhcfO/wY3/a1WBZ6WHBMw42bg1xgUd8CgOBxUxrYMz+HJ97vpi2KYHiycdvqq1skjV9RBKTkkkcX3Q+prjACOL7hyr2JMQuHfiBfd9EZjwQaC5+prwmu2XKE2XLv3kvSXngAttBqMcXGTR6dwSeYpmT4W+IO0tKuyo12wU/6zK9NnoTSQHevo4WBM6ezDVw7GgoNUtjp/1CC+IsRqL8Nfv+GtVd0Mf4dRHBbupvjrm4+k9FexL3NWkUab24TidYpT4RM9d+7QG0y3RHpeUJ0kWPs1W0OG5hfd/HMONLrQ1XVsYBRKBJoxf2tEAzcqdKljuBwyeo1mTQeqielM0EUlmsITt4nYGR/qNtPEPm7gJVxmVxLwOW7DO6WN4Cgq/BGXtpUFc3itskVR7wSy7ampkcg/9yosH/GxU7Y2B98EhvPd97MTF2zmpuYFdISew1WAO0bDzGScmiGk8ImBSx7E9mbG5/QlypQA2V+mlj96lJe0OjlA7KQa4nWEiDSFs6b4rmabxBJ8L/F3JstyB884922xAioU+2AffIVlQVIMWS+y1HEK9V7SkSgZrPWPU2VTTS64QKmTymRKCnzZRdxpS3mdl6QPw0hQaSVshkyQDTILL+otFZbhZ0VGDTtQDlRCFxRjP9l9pj1/53+JcxuMIfubtWwRsb+hV+iOxqFfgyG5aHMZpqa3jISZbBLZI6Kr0OFFbGzh0C+6het9jaE7m6NUlZkofGhHc+NnHQWzigUFK1C3vUDo02iziQtfwD0h68nwkkmLyYobJytSG7ccuDKnh1Dmf+Pjh61c5szVuEbQKZIoEMFrx/97zyQv4nOPae4GdnEYYgG8xf5S6aqRmspVRhMfZZbxdx/+U9KSO42+Q9W+xaiaux0Yel+FoqvI2H0CXhDdtfWNi6PPmg2EzH7rRGS6TYHCME6qZEgxabj5dujvXm9FWUOzkfwcKUanwNjJwMpkUkci7iauF/ozYDs6zlTmDxIeXF+G7C60q2Rzg81XxO38lv0Pya51ChiHWaUrJXqVy5Gks6lCsKQAfltiw5wCr+4XGg1CS2+PgHQURBeFYrDw1UbGKR8Z6TteBnCsxPf5RcQpWErd5bHDLCT+3oDoFtpfbkR7RwnL0ptnPz/WcSgyh4VWtnnrrTI/Zr8qY4eFCcMqgk6y+QaNAipPEPpqGlUcupzV5e+8eCLfG7gRWM6hRKoGRAKGeswmpJnFkdXaIDyOkBN8SmAAGbd/Wfm1y6/rqrwHlnGEi50cRO/pQ2IixvjyCv4lWBg7n97Wy8wg2rNy+Cc38/vsqHTBBw1YBUVE/rUTauoE7uRMQ7vNXUxrEZCwxIdvzRWdYPfdudaPMrZeAkIkb3lMWFs3Lk8gP5Aygqd6uk6pxOSdBB7tQb3wNxrAwODQq8XtfpSLi8vv7GwyrgkC1z3hHG4iKU0FutGwkr7A1BN1lioY6vB+PAsv+cmWqLtSH04fqzWRq7Den2ElO4cY9aWNBjCqcFS070qVTQj5UnO0iCxgGxryWv0SYnDruWA2sd2TJNMsKLQJn5g/pWEnft5L1Ds4hlyJKXcFsrPL+AijLFaImpI7vC0b64012IXoq8flxvip+lRpEtdIxdUProOBLlZblbWr6AZvHEo8/4+qyvV3yHLVzKvcPd7x0cVW218U2G4HtQDlA69wv4+llUeK9CvTlGrrFIFbGv9d7sTWcEcwbRWrqrWT6cPbzvDlkElfSfEQExmeY/54VbmDzoOOxZQls3hCQFvk1Mq8vl+CqUcFsw/nk9dgydiMdAjwD9p/kszidrSwd0iiUtznqtNQc2Zropi89RH5IbOGHngcFBn0UtDo3q0FRo6pYxKEX+SCZhhY8i40RqKZ2aIcNslON/Djd7cQ+e9/1npLzVs9/Ptsz7/xvz493eyJqXXcr0pQHR3/6Y8PACycl1pw4YmjdXbBm5cMfZUzNi+VZUL8brMgAGTkVlZzLWDDhikZ/i+YXG4tRIEurm8kjepqfTLbPz62+yrW53WoH0EEUFQUYtDfnOVW9Ts0Fkse2qLpumnGlcC9JmqHqp8lejaCG/9WlNfITAZQRLF7srY0kj6yA5/x/9CQqFV3+/qnlg0/6EfUI+B3exGRXWnURQwptatk7A0JHdgh7Ooj8fYT/hrEuUReusxf18GyI49L/pGDNUxKRCDN5dafLqLrNHUXt/QFLK+h5XXYIaVCv4eKvthrdNwrRQELHgj7gf/eS72hoeDudCvjXLVTqllmt8E5LWiETrlZ0BRmifw860z3om0t/PtY5/Tj2VTZAjbEV41vUKVZn4tm3GZVhcA7ghGQA+VkYkNvouPdfSwKFY/I7knPq85foOkB6gOxOmgEXWn77PefqStCsM2k4Ue5apUc/kHomZQNGq0zhYzQ0oN2KuddRTWWrfhOIUH1BhC5m6BEp6Rr0Nqfd8qbeGOW53TjWWQ/2VSsAkXmBpE7gh4BhS9XUvsr6/lDzb/CRLqxpEVAZH8xWMY4bEb6uGDB+yPdLSrNiSGYHC+a4nH3KIP+H85kYHK0Etqir7e1Zqy5ftobxUboqHRBk0a0Qc8+5Lmpoxl7rMxoP5TIwbeMsWuGpOfobpx0CyX2fWhVbJF3e381SV+c0SxZi6q+3qEbxH3pdWkL0MVhKqFC6IW5ATfOVGCnr70Xeikv7Zh90XoY9R5DpYEKKCWjU/H6m4xzBiOgLqEDOnQDajj8BziK1OgNXZsMj6plfFBmMi0bRXHFwX+LFORPpxXO3rw4RZr3Fpw85NjF2r9C31dFkNAx45mEhjPuvFZNPoy2lDUgEES6t7WPT9Zb8YEScht5A8EF+IDIYYPO6eyX7LzBgXrK83IPzJduZ7d/k8epJalNbbjnc8lcvs9RCcGHjmYKiozkWikXNwTHMnkzoKji22cd8KS2Vv19a6Of0GYEuC+LRWn2dew99Uia2LUrFJ/8kra8vl0I0ltDF2FylElkTnDHstV/1oSw/2DdmJ/EGFCF9EFQGHtNe4uPk7iERF73NnrQVJZpNMn2eo6QUWlQE7kNcKUwgtxPMCh8d9nGctaiJLgPluEqYcfpR3bSKTY4NXwjYEs01h4vhKx/madnwdvcXB4kFbGyMSqXOkSBTgqfDsAL4Qy46dfpTxCVH4im+xDNdKY49/et5wXkHieZHuxy5HWrVP6kG8/PXD758+cVQRxLc5ZqmF7JKFdKQX/hFBD6hLYxWhUkB8mOgPxmzl6n7is4HPRi+kmClf/ot9NeEEAiJveQXbZARGwyCLmXWsSrel1vNpuMaa74GrN2ovX2YJe3HTFnhjhC+jx99QhE6HCFZ484nL0nVQ3C9U1KjsxwhPfhrp28/mTYS2xb9DC6nyYYXMzEsvEiMTqW+Bm3Y23bo75pyCgvClDEFk3GyzFenr2zIoyG5NfecKF1jH2jz8LW+Lof8zGpesZchy2+fu3LMlkIM7kAsU0SrMOxpi3j1pyvdyGGxRpzPot4NfmW7S2T4qY5mhILU2F/pP05uzG/j/ACM/6PLmzsLa/EANhHBIHFCUG3PaUeyVjAZ8cN/uLTJxoUbjDtP0jbjodcguAQVsU5DhbE438bttWLLxX+KOWCuHmUjyPhGl7eFU/MmNXJjoAxh23YoxMVpKKpemUowLb64J0WG5TC9FI55QGzicJCySNi4aWD+cq8iIdIh/OedJZroclchAtp/3Lzz+GyPAA2jLDNpJSgZuAuPwPyO3EXM33bMGnQd3FczeSH3daWaXoq7CPzlCTA81ISzuR0eWt29tIiSFh3p8uM9xCGOF+dDacC+kcW71xgEl0SkvRsR2gWcDwDKTG8HjZcPWMJ0vfWyd+JNaJglW48X44z8VXOoUpd7XLZ2ju0BbRrJVr2eG3QYbsG2CNiiJEV75B7saJWunWB1oHaHlBIr2arZkE8nk51mZ6IXsouL/NZWU4rwFZ6nYfsInsn5kFr8uZl79mTCpkWMw5l0eh4ciScxzAaNiRyP8pLqB+L8W59u9D1rgDAAJrb5S0igjK2OSqB9aQp8DCEMuCan6PMi7TKAyVM8QQBXzbygSPB0u2iNvIM5j7xRwu7VabsxkTPK0XxNJSPbhVHbz3ZXo3OWMIuGlrKd07JXRpfMqJeZheYE4Le35m2fjr9Uu0vy/kvLTBrHK4KUX5cxR2c6OCfjzVlE1xCrVMopmroIm801e56BzLY/hpUaJNiIyO1KINSOOENfzBhUongCsdDXamNDPa7u8RSzk7vm72zB/c22nC54qmBPLnnwxy7GgDjmn0tY7g5fGUyoMG6A8l+bbkPEwZb1jRC2+YP3hwPR7Xx1139LxdyF7FXzcpnO+dmZxyYFOAEmgH2dxsOlDwAqz5V6LyMq3cZ3k7hqf/i4i5GrTCvP6JrZ2cgZPKQO6H7itSczEBt/cCdz3Yk0U31q5czlwA90fARt2WvYZD4T2h89/GPXNYvWfPNpm2TfbVy54Pjfdh48I9aT+m5frxU4qj0JGZW0imWI0k3ajxWooTvGsB8V/p0uY94Lp1aKpZiCe0AgZh9LNcz9a3bvlULK/IfqNQ5/DTAp4vvy5qjE0TPu9xOt+HxqpKhlE4ND4aiU1FP+12vMdXG9SuvNhJxe2irpfibZYYeJ/AtIoVUvfgNyCll4ab8ZQhk8IMJCSKtwk5PfycAE7iS32+4p2KTQf2EtP0nCS2PqcijstB8Bztb+HEx3fXrXDvTtaoBbaIS9phSvBaVGZIoPbmHLTbtk3KbYP+yjRqKKtbwonyCmIdp7Mu6QatbbL2ZqXqdFCDPvyV82CqAOIk01NBM23XrFmIB3+jnF1KRU6kFl039yS93c1lKw9G2TlZrNX9BQd4k/8ouiskYmbwpdfQnRL1bF3TpHVz4xLY31wuhywXsqShZd2vlQjbsezTiQfegbBf2wnPfxhZ8VU7lsEzyWhIOSHGq88rjJXeGsNX2fwfeSXMWYIujHV/oFXdiNunHnkyV8Fo+y58K/xcd4JADKq/ocC5u1E7bgpwl2IDFegCajmWaUPfsOdEjS9Q6dcQbUEIfmdEOqGjlIGMUWxSJBE+gKcWu3WTp6PBrvaQZ86CFGYwOzT0sD7Bb1x0IYH5F00GgtMPNmGcLr86UA1cb46hTwmYz73e+ITiPQ2ltIgBewRAZHHHuDADJ4z9d3dn4XCalh1AREe+u/F+FbdYQJD2fP4D2dUeCjFVU9k8jYBcY5t9KpUfDkY0MeDCnFSbCAffLAETiLLaPxIAuI7Poz+bQRdoZ2xvuD2daHARMT3fSB+OzVIe+3WA57uRBUCYemDy9Tqce9FD8yMIkU4v8ikGBjvUQLhhiX92GU8V8gNnb2yj+LuE5Znxw6s9I/LNZYW8rCakyXSmqHv4hIr4DqVpypfKm7bEke6GR1rO/9CQF/ZvzfK1F4kknzwDvDHuzuCahLirdXcvks6s98iadsTxmkBXZmNEYQOJBbQNU7hlaAObAVl46bfd3H9tSvwU6vhMuli+S/9FJJdzBQwnkwGHyLisdFAkGc7tdc6+tsLl0uW+b8rr9FMD4dM7ojdE6MNwzA1HkPheFWWby6kUf1d60+Y9VPRVYRSBDHkhj7MbUbVAzWhddN+F8JPqO6WhZ+s2cz+/AQkwrwKWRWg//qUfIU+bAB6WdQ2IzslY8lO8ymOYF9EaJUpc/wpwckuLzMcw4eeeRvfnmwrvaeN1t51S4BtlWP1xakJzk1kj1ebPQL9T2MFCdbbCWDBhPx5biKo8mhAn7XXMPFOdLhWvZOAJZuj5gaAv9SDnkiPGVakUyf/KJrilYbavqqrmk3b/fv8pj5r4v4hdgmU7Tqfly/sqeRYdinpxEkBuY3SqWw1ul0WAoiiAefUpZuf0MtbbfnspSS2sj8S4woJpuy8CjBLxq9bZkRQYX8yF5jQTnd6uWuAbF0vH4DFnBw6QaEjDzwDu746N6Uz4xmXI3TFFY1DlJaarPg7+6iHGKwEJUeOe4Mjqr1+/6Ep3w5kUp7x7wDiXkwOFi3foXHM7u2QKgpDKNbZiHLpyzZA7wHkKIaDJaHHhIdDrOGqs1+85pSessPIE2G+H/ZXd9MNOSJ6JAVfQIhfueDXvFp8nwsyNIv5i/5JXPFddNjCiVVxaGgqY8cURXNdzw8E5gC5CwUkpiQB+xfoVEDqtKyHRnnBuSaLJzrz6DWiSwvOXRBnOw/EDHQXggVU4hY35Qx//wv3mTbxp543KIBbN+Iew+wdal6g/Juu6Lj71YP0fRk/XAf7cw4oPfGIBO9BQUcp0x8nfn2vEqdXkw+KDcVeWFu0x+XkR+Ts4rzG7weRj8DV+gicurhOMTf6+ItwUQTxaYCUxj0GKHt8CxT4jjZUE9J2aq8n7yV/KmbshZUbjOMc2634KcsJ4aJM9qXbwxon/ld3bGszwb2o5iwjMCb4K18bbv1iFWANj4PQLUUnVVIBJ77R0+a57yvQOTXGQhzITQrxTUqISlu9P7bboNBqG1Hrtl/UBIDlAH/SHfKDhybfT/3WeZY8/7Mc8LgvbHyB6kdZP8wPUlFCrbPSqET4MNxT/Ln/bzlt2/LzVYWogghRDOKZM6JlUYK0At9zoWtQp+dHMv7SaU5rE1L6UvnMT5H+BK2gb+rxFrlULv7iXD1LGvpk9s2oB1nqnaatTnto9918HOiHgUlYTPLLPhLNplmi1f4+tjkgr3jBx+MnMdF6jX9R76Tp2q8W65Rp07oOSE3Fd04Ibk1zM6FMjLOfGhs+YK9A/Q1hFNEDMNe9MaDmfDCX5yuga53RkSfGEKbzracbR5ARE/vsWwtegsff/JuB+c0iflXWSKZJ7vYI1Tf0ZKBG/s+UMv0jdarm88qYcDWyEj1qU8LHIRbDbvT5Q3y1n+krw85NmnRVIz+5PxGI/oClmfUnpbI0LNhUz7tN+scdKykGflv9tluw9TVvZTAlq2gmf1CRO1RaWNaQWBOeMHcjzpUO9Zx6SQ/EOoT9N36N4IFw0B3vXCG/G/DlAQErZ1m9eZDGW7yIsjBRK9fekbGrNTt+xJNGkOCzaOpyiPYDk3wp83Inwyr6sSbkkVoaifC7TNBeOOWiv6+S33fWtvWgbCNT474jbVs1UW5MDd6w1OPWtbpyG8tpbinaqPf5EAYTCxmNn9CQ4H0FaUYFEBPP0XRUn3Z4j51oUI+bRZbpVV/UYNrh05OvRfdgrK1hXX9MNK+jtmmHp0L2xICld5Izd2YKMe9uewoSnJMW7Ta5lS93znLGsFbNPf1Vaa3lTibuVTuVbCIhGWChTxnPub8ODKuT41t2vQNkXgoXAFJz7Ay6Z4E9mSfumhds373dzlQJkoU9RDHvTgWE7rtIg4cR2v8VadqWKVS55APJkrRgNMBtYTi/lbAB/PwdWYTzob5Uiv7MM+9egAeH6ucuO+eEXBc8smIgiGJe1vW3ZFhQjkU9O6LsS4ZO5PrHIHfswSpjcUdmu4XZxiMNWQeH+EQOLmXd9NTS29OTmmrfXRcdmghPk0iWB1HzNhTKFKVa+WWfU78AZnse+RkqkHsDaLkZN0ea/7u0Qc+0LpHB10VFO6vjvSgw+v+QoLHIU/fPWLgQOHuDZlF6R8zVWCbfUJtimCZEl/yoRlNiA+NNMe2QGrfDNId7tBGMd9AHmFrjWlsB6+vYOmkYBwumVEHZFLzV3L2Ur+I9CPwiE1QtSK+MboeWmMPcENlQx3snJP1XmWduXvvpWS/VQ5oUBaXS4ORJm+T1GnI/IqGfEPj6zp/F5rrvchINfzDIAVqGP34vwqH6nLSHsWRdkdd0SoUXG1rx5Ci+i0rAInjqSd0tE8XiETHND4CdaUS67+hJGHxLgACvOjB02ipSHwBdpxf5v0UPOrbbBEbLK/3cp1M11gHOZqtPv2Yhe9fguBq4/EtHwLpwTPQNJ+PhabN+nDSi0pIxur2hbAk6aVuZGxbMOKMoR9v8b4Ml8re2E6klKNCDf7j4UmA5xa6w5Mh2+zpA2Y0MQKze9Wy7vnoefwBR8X85WIuCZLKkqkkdwceLePM2IA9yvtFM9bVWrfvrRyGQgktQQSnViIBlh+so9/J9pa21UY2xtQgy1orzudE4kGriijr9DhmNcfydK8bnbo8gew09ohgz+5a1mTuqgW2FWl883EbZKX+BtGrkSXqqiorqaIWlblbd5+vDsqVXf7KS6KHcrIjH2sVWiCEvOrFXfEiLJiQKcAwoiQNY4teMtweeYqjBHk4DiFq78KH3fiO1XfwgRAf0xHfYW7bo3c/7/h3kzx9D3nCUO6OGAgNIcsvAR3E8XixW7VcwJQV4OgkK8nZVez0bMEGoySUrWjjCgrTncZRywsB5SwRUMX33wtHRLcEBWcx5apxfNW9KsoMpbFdYp17bvO1pfIPsIJuDQ2DiPtUg82gzFww8LkjxJPznxTaXX9xukQdEtAE5CCiJ/6r1Y30sEqYMgQaC6boVvqa/dptstxIi9K+I8Yty+fgQ8XG6Q9NVIOt3VQOoMvHZUp0OUQq6yvN+R/0wem3aERkhT0XkLLkzCPC40RY6y3e9ziPslwH/oVxipazBkXjSmRxB5rW5UoP+eyyF8zPI0hsw9oGnzI8TxU/K78UFrjnqGy/gYTVB8JYfGyp+LlTYyx30khJy+OL8k+WR8gUxdlUHfx6Ox7ZFJuqDJFV8fZV5cK2uJbpFPJRf9JR6khdazK4tNG/bXZx0sVGaP6T6QSFLF7OJLvsfA8fj/6JyAuELU8g6A37669/KBiGk/xvYMePFkNWfhr/ZgAAAB9KR3Y5BBRZTRwpL8yh74P10LN6fY7AKBelpXLUKICnGmyNAVklQBIylmNZz/cO8JTfH7avG6fmLToRBQDkTHM+Z0pRqRZXnfA/uymtu8KEDhh5JmkU5GCWiFAAnCTQF6bIRnZk6AbHtrPnfGEIAQ8TO5TTW/wT3EqvuydzHhlqPyfHLnRA85DcE5BoYIPz7aJYxWLdRv0S110AAkem8lCarJ3S2LVJr/S0x8bE/9spAQAAAAAAAAAAAA=="}


def menu_photo(m: dict) -> str:
    """메뉴 사진(base64). assets 폴더 → 비상용 사본 순서로 찾아요. 없으면 빈 문자열"""
    if not m.get("img"):
        return ""
    return img(m["img"], 480) or FALLBACK_IMGS.get(m["img"], "")


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
            if not db_select(table, _fresh=True, code=code):
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
        _select_cached.clear()
    else:
        with _LOCK:
            rows = _lread(t)
            if t == "reservations" and any(
                    x.get("status") == "예약" and all(str(x.get(k)) == str(row.get(k)) for k in ("campus", "kind", "route", "ride_date", "depart_time", "seat"))
                    for x in rows):
                raise DBError(409, "duplicate", "23505")      # 같은 좌석 중복 예약 막기
            _lwrite(t, rows + [{k: row.get(k, "") for k in FIELDS[t]}])
        _select_cached.clear()


def _in_list(v) -> str:
    return "in.(" + ",".join('"%s"' % str(x).replace('"', "") for x in v) + ")"


def _select_raw(t, eq: dict):
    cfg = _sb()
    if cfg:
        flt = {k: (_in_list(v) if isinstance(v, (list, tuple)) else f"eq.{v}") for k, v in eq.items()}
        params = {"select": "*", "order": "created_at.desc", **flt}
        r = requests.get(f"{cfg[0]}/rest/v1/{t}", headers=_hdr(cfg[1]), params=params, timeout=15)
        _check(r)
        return r.json()
    def ok(x, k, v):
        return str(x.get(k)) in [str(i) for i in v] if isinstance(v, (list, tuple)) else str(x.get(k)) == str(v)
    rows = [x for x in _lread(t) if all(ok(x, k, v) for k, v in eq.items())]
    return sorted(rows, key=lambda x: x.get("created_at", ""), reverse=True)


@st.cache_data(ttl=8, show_spinner=False)
def _select_cached(t: str, eq_items: tuple):
    """같은 조회를 8초 동안 기억해서 화면을 누를 때마다 저장소에 다시 묻지 않게 해요 (저장·취소하면 바로 비워요)"""
    return _select_raw(t, dict(eq_items))


def db_select(t, _fresh=False, **eq):
    if _fresh:        # 저장 직전 확인처럼 꼭 최신이어야 할 때
        return _select_raw(t, eq)
    key = tuple(sorted((k, tuple(v) if isinstance(v, (list, tuple)) else v) for k, v in eq.items()))
    return _select_cached(t, key)


def db_update(t, code, **vals):
    cfg = _sb()
    if cfg:
        r = requests.patch(f"{cfg[0]}/rest/v1/{t}", headers=_hdr(cfg[1]), params={"code": f"eq.{code}"}, json=vals, timeout=15)
        _check(r)
        _select_cached.clear()
    else:
        with _LOCK:
            rows = _lread(t)
            for x in rows:
                if x["code"] == code:
                    x.update(vals)
            _lwrite(t, rows)
        _select_cached.clear()


def taken_seats(campus, kind, route, day, time_, fresh=False):
    rt = next((x for x in BUS_ROUTES if x["title"] == route), None)
    # 두 캠퍼스 학생이 한 대를 같이 타는 노선이면 두 캠퍼스의 예약을 모두 합쳐서 좌석을 확인해요
    scope = sorted(rt["serves"] | {campus}) if rt and rt["shared"] else [campus]
    try:
        # 캠퍼스·상태를 한 번에 묶어서 1번만 조회해요 (예전엔 최대 4번)
        rows = db_select("reservations", _fresh=fresh, campus=scope, kind=kind, route=route, ride_date=day,
                         depart_time=time_, status=["예약", "탑승완료"])   # 이미 탄 사람의 좌석도 '예약됨'으로 보여요
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
def _frag(f):
    """Streamlit이 지원하면 그 화면 안쪽만 다시 그려요 (상단 메뉴·히어로·푸터는 그대로 → 훨씬 빨라요)"""
    deco = getattr(st, "fragment", None) or getattr(st, "experimental_fragment", None)
    return deco(f) if deco else f


def go(page: str):
    st.session_state["page"] = page


def has_opts(m: dict) -> bool:
    """고를 옵션이 있는 메뉴인가? (HOT 가능 · 디카페인 변경 가능)"""
    return (not m.get("food") and not m.get("ice_only")) or bool(m.get("decaf")) or bool(m.get("variants"))


def opt_label(m: dict) -> str:
    a = []
    if m.get("variants"):
        a.append("종류")
    if not m.get("food") and not m.get("ice_only"):
        a.append("HOT")
    if m.get("decaf"):
        a.append("디카페인")
    return "·".join(a) + " 고르기"


def pick(name: str):
    st.session_state["sel"] = name
    st.toast("아래 ‘옵션’ 창에서 골라 담아 주세요 ↓", icon="👇")


def add_to_cart(name: str, temp: str, shot: bool, qty: int, variant: str = ""):
    cart = st.session_state.setdefault("cart", [])
    for it in cart:
        if (it["n"], it["t"], it["s"], it.get("v", "")) == (name, temp, shot, variant):
            it["q"] = min(it["q"] + qty, 20)
            break
    else:
        cart.append(dict(n=name, t=temp, s=shot, q=qty, v=variant))
    st.toast(f"{name}을(를) 담았어요", icon="✅")


def add_from_panel(name: str, temp, shot: bool, qty: int, variant: str = ""):
    add_to_cart(name, temp, shot, qty, variant)
    st.session_state.pop(f"t_{name}", None)      # 다음에 담을 때도 ICE/HOT을 다시 고르게 비워요


def quick_add(name: str):
    """카드에서 한 번에 담기 (디저트·ICE 전용 음료만 해당)"""
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


def variant_price(m: dict, v: str) -> int:
    return dict(m.get("variants") or []).get(v, m["price"])


def unit_price(it) -> int:
    m = ALL[it["n"]]
    return variant_price(m, it.get("v", "")) + (DECAF_PRICE if it["s"] else 0)


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
    parts = [p for p in (it.get("v", ""), it["t"], DECAF_LABEL if it["s"] else "") if p]
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
    <div><span class="ic">[[pin]]</span><span><small>위치</small><b>글로벌캠퍼스 버스 승차장 옆</b></span></div>
    <div><span class="ic">[[clock]]</span><span><small>운영 시간</small><b>{esc(OPEN_HOURS)}</b></span></div>
    <div><span class="ic">[[coffee]]</span><span><small>음료</small><b>사전 주문 가능</b></span></div></div>""")


@_frag
def home_location():
    md(f"""<div id="location" class="sec">{nextstop(1, "정류장 위치")}<h2>뀨 스탑은 여기에 있어요</h2>
    <p class="lead">글로벌캠퍼스 버스 승차장 바로 옆에서 노란 불빛이 켜진 초록색 2층 건물을 찾아보세요.</p></div><div class="sp"></div>""")
    cname = CAMPUS_LIST[0] if len(CAMPUS_LIST) == 1 else st.radio("캠퍼스 위치", CAMPUS_LIST, horizontal=True, key="map_campus", label_visibility="collapsed")
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
@_frag
def page_bus():
    pagehead("버스 예약", "캠퍼스를 고르고, 원하는 좌석까지 미리 잡아 두세요. 이름만 입력하면 돼요.", "BUS · 좌석 예약")
    ss = st.session_state
    left, right = st.columns([1.2, 1], gap="large")
    with left:
        with st.container(key="resv"):
            md("""<div class="form-h"><span class="ico">[[ticket]]</span><div><h3>예약 정보 입력</h3><p>캠퍼스와 노선을 고르고 좌석을 선택해 주세요.</p></div></div>""")
            md('<div class="fs"><em>1</em>캠퍼스 · 운행 선택</div>')
            campus = st.radio("내 캠퍼스", BUS_CAMPUSES, horizontal=True, key="b_campus", label_visibility="collapsed")
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
                elif seat in taken_seats(campus, kind, route, day_s, time_, fresh=True):
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
        if not m.get("variants") and (m.get("food") or m.get("ice_only")):   # HOT/ICE를 골라야 하는 음료는 바로 담기 없음
            st.button("＋ 바로 담기", key=f"quick_{i}", on_click=quick_add, args=(m["name"],))


@_frag
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
                variant, vprice = "", m["price"]
                if m.get("variants"):
                    vmap = {f"{n} · {won(p)}": (n, p) for n, p in m["variants"]}
                    variant, vprice = vmap[st.radio("종류", list(vmap), horizontal=True, key=f"v_{sel}")]
                c1, c2 = st.columns(2)
                if m.get("food"):
                    temp = ""
                    c1.markdown("&nbsp;", unsafe_allow_html=True)
                else:
                    temp_opts = ["ICE"] if m.get("ice_only") else ["ICE", "HOT"]
                    temp = c1.radio("온도", temp_opts, horizontal=True, key=f"t_{sel}",
                                    index=0 if len(temp_opts) == 1 else None)   # ICE/HOT 둘 다 되는 메뉴는 직접 골라야 해요
                qty = int(c2.number_input("수량", 1, 10, 1, key="qty"))
                shot = False
                if m.get("decaf"):
                    shot = st.checkbox(f"{DECAF_LABEL} (+{won(DECAF_PRICE)})", key=f"shot_{sel}")
                if m.get("ice_only"):
                    st.caption("이 메뉴는 ICE로만 만들 수 있어요.")
                unit = vprice + (DECAF_PRICE if shot else 0)
                need_temp = temp is None
                st.button("ICE 또는 HOT을 먼저 골라 주세요" if need_temp else f"장바구니 담기 · {won(unit * qty)}", key="add",
                          type="primary", disabled=need_temp, on_click=add_from_panel, args=(sel, temp, shot, qty, variant))
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
                name = st.text_input("이름", placeholder="홍길동", max_chars=20, key="d_name")
                how = st.radio("수령 방법", PICKUP_HOW, horizontal=True, key="d_how")
                when = st.selectbox("수령 시간", PICKUP_WHEN, key="d_when")
                bus_need = when == BUS_PICKUP
                bus = st.text_input("버스 예약번호 (필수)" if bus_need else "버스 예약번호 (선택)", placeholder="K-7HQ3X", key="d_bus")
                if bus_need:
                    st.caption("버스 탈 시간에 맞춰 음료를 준비해 드려요. 예약번호를 꼭 입력해 주세요.")
                if st.button("음료 주문하기", type="primary", key="d_order"):
                    if not cart:
                        st.error("먼저 메뉴를 담아 주세요.")
                    elif not name.strip():
                        st.error("이름을 입력해 주세요.")
                    elif bus_need and not bus.strip():
                        st.error("‘버스 탈 때 수령’은 버스 예약번호가 있어야 해요. 예약번호를 입력해 주세요.")
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
            camp = st.selectbox("캠퍼스", ["전체"] + BUS_CAMPUSES + sorted({r["campus"] for r in res if r.get("campus")} - set(BUS_CAMPUSES)), key="adm_campus")
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


@_frag
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
            or _k.startswith(("route_", "time_", "board_", "alight_", "t_", "shot_", "v_")):
        st.session_state[_k] = st.session_state[_k]

if "page" not in st.session_state:   # 새로 들어오면 항상 홈 (QR 주소 ?p=bus 같은 건 처음 한 번만 반영)
    _want = st.query_params.get("p") if QR_LINKS else None
    st.session_state["page"] = QUERY_TO_PAGE.get(_want, "홈") if _want else "홈"
    try:
        st.query_params.clear()           # 주소를 깨끗하게 → 새로고침하면 다시 홈
    except Exception:
        pass

@st.cache_data(show_spinner=False)
def build_css(_stamp: float) -> str:
    ext = img("exterior.jpg", 1600)
    return CSS.replace("__EXT__", f"url(data:image/jpeg;base64,{ext})" if ext else "linear-gradient(135deg,#17694F,#9CCBEA 62%,#F3A6B8)").replace("__NAVICONS__", nav_icon_css())


_p = find_asset("exterior.jpg")
md(build_css(_p.stat().st_mtime if _p else 0.0))
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
