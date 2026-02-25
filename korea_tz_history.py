# -*- coding: utf-8 -*-
"""
korea_tz_history.py
═══════════════════════════════════════════════════════════════
한국 역사적 표준시 변경 + 썸머타임(DST) 완전 반영 모듈

📌 참조: 동양 삼국의 표준시 설정 기준
   大韓民國 (서울) : 東經 127.5度 — 경기도 가평 지역
   日本 (東京)     : 東經 135度   — 고베 서쪽 20km
   中國 (北京)     : 東經 120度   — 산동반도 지역
   ◎ 15度마다 1시간의 差異

📌 우리나라 표준시 변경 상황 (완전 반영)
   ┌──────────────────────────────────────────────────────┐
   │ 시기            │ 기준      │ 표준자오선 │ UTC 오프셋 │
   ├──────────────────────────────────────────────────────┤
   │ ~1897.12.31     │ 北京      │ 120.0°E   │ +08:00    │
   │ 1898.01.01~     │ 서울(한성) │ 127.5°E   │ +08:30    │
   │ 1910.04.01~     │ 東京      │ 135.0°E   │ +09:00    │
   │ 1954.03.21~     │ 서울      │ 127.5°E   │ +08:30    │
   │ 1961.08.10~     │ 東京      │ 135.0°E   │ +09:00    │
   └──────────────────────────────────────────────────────┘

📌 썸머타임(DST) 시행 기록 — 양력 기준 (시작일~종료일)
   1948: 06/01~09/12  (23→24시 시작, 24→23시 종료)
   1949: 04/03~09/10
   1950: 04/01~09/09
   1951: 05/06~09/08
   1954: 03/21~05/05  (서울 표준시 전환과 동시, 0→1시)
   1955: 05/05~09/09  (0→1시 시작, 1→0시 종료)
   1956: 05/20~09/30
   1957: 05/05~09/22
   1958: 05/04~09/21
   1959: 05/03~09/20
   1960: 05/01~09/18
   1987: 05/10~10/11  (02→03시 시작, 03→02시 종료)
   1988: 05/08~10/09

═══════════════════════════════════════════════════════════════
"""
from __future__ import annotations
from datetime import datetime, date, time, timedelta, timezone
from dataclasses import dataclass
import math

# ===================================================================
# 1. 표준시 기간 정의 (standard_meridian in degrees East)
# ===================================================================
@dataclass(frozen=True)
class StandardTimePeriod:
    """하나의 표준시 기간을 나타낸다."""
    start: date          # 이 표준시가 적용되기 시작하는 날(포함)
    end: date            # 이 표준시가 끝나는 날(포함)
    meridian: float      # 표준자오선 경도(°E)
    utc_offset_min: int  # UTC 오프셋(분)  예: +09:00 → 540
    label: str           # 설명

# 날짜 경계는 '자정 0시' 기준 전환으로 간주
_PERIODS: list[StandardTimePeriod] = [
    StandardTimePeriod(
        start=date(1, 1, 1),         # 아주 오래 전
        end=date(1897, 12, 31),
        meridian=120.0,
        utc_offset_min=480,          # UTC+8:00
        label="북경(北京) 표준시"
    ),
    StandardTimePeriod(
        start=date(1898, 1, 1),
        end=date(1910, 3, 31),
        meridian=127.5,
        utc_offset_min=510,          # UTC+8:30
        label="한성(서울) 표준시"
    ),
    StandardTimePeriod(
        start=date(1910, 4, 1),
        end=date(1954, 3, 20),
        meridian=135.0,
        utc_offset_min=540,          # UTC+9:00
        label="동경(東京) 표준시"
    ),
    StandardTimePeriod(
        start=date(1954, 3, 21),
        end=date(1961, 8, 9),
        meridian=127.5,
        utc_offset_min=510,          # UTC+8:30
        label="서울 표준시 (복원)"
    ),
    StandardTimePeriod(
        start=date(1961, 8, 10),
        end=date(9999, 12, 31),      # 현재까지
        meridian=135.0,
        utc_offset_min=540,          # UTC+9:00
        label="동경(東京) 표준시 (현행)"
    ),
]


# ===================================================================
# 2. 썸머타임(DST) 기록 — 양력 기준
# ===================================================================
@dataclass(frozen=True)
class DSTRecord:
    """하나의 서머타임 시행 기록."""
    year: int
    start: date    # DST 시작일 (이 날부터 DST)
    end: date      # DST 종료일 (이 날까지 DST — 종료일 자정에 해제)
    advance_min: int = 60   # 보통 +60분

# ※ 시작·종료 시각 세부:
#   1948~1951: 23시→24시(始), 24시→23시(終)
#   1955~1960:  0시→ 1시(始),  1시→ 0시(終)
#   1987~1988:  2시→ 3시(始),  3시→ 2시(終)
# → 날짜 단위로는 start_date 0시부터 DST, end_date+1일 0시에 해제로 근사
#   (시주 판단에서 ±1시간 경계는 별도로 정밀 처리 가능)

_DST_RECORDS: list[DSTRecord] = [
    # ── 동경 표준시(UTC+9) 하 서머타임 ──
    DSTRecord(1948, date(1948, 6,  1), date(1948, 9, 12)),
    DSTRecord(1949, date(1949, 4,  3), date(1949, 9, 10)),
    DSTRecord(1950, date(1950, 4,  1), date(1950, 9,  9)),
    DSTRecord(1951, date(1951, 5,  6), date(1951, 9,  8)),

    # ── 서울 표준시(UTC+8:30) 하 서머타임 ──
    DSTRecord(1954, date(1954, 3, 21), date(1954, 5,  5)),
    DSTRecord(1955, date(1955, 5,  5), date(1955, 9,  9)),
    DSTRecord(1956, date(1956, 5, 20), date(1956, 9, 30)),
    DSTRecord(1957, date(1957, 5,  5), date(1957, 9, 22)),
    DSTRecord(1958, date(1958, 5,  4), date(1958, 9, 21)),
    DSTRecord(1959, date(1959, 5,  3), date(1959, 9, 20)),
    DSTRecord(1960, date(1960, 5,  1), date(1960, 9, 18)),

    # ── 동경 표준시(UTC+9) 하 서머타임 ──
    DSTRecord(1987, date(1987, 5, 10), date(1987, 10, 11)),
    DSTRecord(1988, date(1988, 5,  8), date(1988, 10,  9)),
]


# ===================================================================
# 3. 조회 함수
# ===================================================================
def get_standard_period(d: date) -> StandardTimePeriod:
    """주어진 날짜에 적용되는 표준시 기간을 반환."""
    for p in _PERIODS:
        if p.start <= d <= p.end:
            return p
    # fallback: 현행
    return _PERIODS[-1]


def get_dst_record(d: date) -> DSTRecord | None:
    """주어진 날짜에 적용 중인 DST 기록을 반환 (없으면 None)."""
    for r in _DST_RECORDS:
        if r.start <= d <= r.end:
            return r
    return None


def is_dst_active(d: date) -> bool:
    """해당 날짜에 서머타임이 적용 중인지 여부."""
    return get_dst_record(d) is not None


def get_wall_clock_utc_offset(d: date) -> int:
    """
    해당 날짜의 벽시계 UTC 오프셋(분).
    표준시 오프셋 + DST 보정.
    """
    p = get_standard_period(d)
    offset = p.utc_offset_min
    dst = get_dst_record(d)
    if dst:
        offset += dst.advance_min
    return offset


def get_standard_meridian(d: date) -> float:
    """해당 날짜의 표준자오선 경도(°E)."""
    return get_standard_period(d).meridian


# ===================================================================
# 4. 균시차(Equation of Time) — 진태양시 보정
# ===================================================================
def equation_of_time_minutes(dt_utc: datetime) -> float:
    """
    균시차(EoT)를 분 단위로 반환.
    평균태양시 → 진태양시 변환에 사용.
    양수 → 진태양이 평균태양보다 앞섬.
    """
    doy = dt_utc.timetuple().tm_yday
    B = math.radians((360.0 / 365.0) * (doy - 81))
    return 9.87 * math.sin(2 * B) - 7.53 * math.cos(B) - 1.5 * math.sin(B)


# ===================================================================
# 5. 핵심 변환 함수: 벽시계 → 진태양시
# ===================================================================
def wall_to_true_solar_time(
    dt_wall: datetime,
    longitude: float = 127.0,
    apply_eot: bool = True,
) -> datetime:
    """
    벽시계 시각(timezone-aware 또는 naive+한국으로 간주)을
    출생지 경도에서의 **진태양시**(True Solar Time)로 변환.

    Parameters
    ----------
    dt_wall : datetime
        벽시계(시계에 표시된) 시각. KST/해외 등 어떤 timezone이든 가능.
        timezone-naive이면 한국 벽시계로 간주.
    longitude : float
        출생지 경도(°E). 기본값 127.0 (서울).
    apply_eot : bool
        균시차(Equation of Time) 보정 적용 여부.
        True → 진태양시, False → 평균태양시(지방평균시).

    Returns
    -------
    datetime
        진태양시 (timezone-naive, 순수 태양 위치 기반 시각).

    공식
    ----
    1) UTC = 벽시계 − (표준시오프셋 + DST보정)
    2) 지방평균시(LMT) = UTC + (경도 / 15) × 60분
    3) 진태양시(TST) = LMT + 균시차(EoT)
    """
    d = dt_wall.date() if hasattr(dt_wall, 'date') else dt_wall

    # --- 1) 벽시계 → UTC ---
    if dt_wall.tzinfo is not None:
        # timezone-aware: 직접 UTC 변환
        dt_utc = dt_wall.astimezone(timezone.utc)
    else:
        # timezone-naive → 해당 날짜의 한국 벽시계로 간주
        wall_offset_min = get_wall_clock_utc_offset(d)
        tz_wall = timezone(timedelta(minutes=wall_offset_min))
        dt_utc = dt_wall.replace(tzinfo=tz_wall).astimezone(timezone.utc)

    # --- 2) UTC → 지방평균시(LMT) ---
    lmt_offset_min = longitude * 4.0   # 경도 1도 = 4분
    dt_lmt = dt_utc + timedelta(minutes=lmt_offset_min)

    # --- 3) LMT → 진태양시(TST) ---
    if apply_eot:
        eot = equation_of_time_minutes(dt_utc)
        dt_tst = dt_lmt + timedelta(minutes=eot)
    else:
        dt_tst = dt_lmt

    # timezone 정보 제거 (순수 태양시)
    return dt_tst.replace(tzinfo=None, microsecond=0)


def wall_to_true_solar_time_historical(
    year: int, month: int, day: int,
    hour: int, minute: int,
    longitude: float = 127.0,
    apply_eot: bool = True,
) -> datetime:
    """
    역사적 날짜+시각을 직접 받아서 진태양시로 변환.
    (timezone 없는 '벽시계' 입력 전용)

    해당 날짜의 한국 표준시+DST 상태를 자동 판별.
    """
    d = date(year, month, day)
    wall_offset = get_wall_clock_utc_offset(d)
    tz = timezone(timedelta(minutes=wall_offset))
    dt_wall = datetime(year, month, day, hour, minute, tzinfo=tz)
    return wall_to_true_solar_time(dt_wall, longitude, apply_eot)


# ===================================================================
# 6. 정보 조회 유틸리티
# ===================================================================
def describe_timezone_for_date(d: date) -> dict:
    """
    특정 날짜의 표준시/DST 상태를 사전으로 반환.
    UI 표시용.
    """
    p = get_standard_period(d)
    dst = get_dst_record(d)
    total_offset = p.utc_offset_min + (dst.advance_min if dst else 0)
    sign = "+" if total_offset >= 0 else "-"
    hh, mm = divmod(abs(total_offset), 60)
    utc_str = f"UTC{sign}{hh:02d}:{mm:02d}"

    return {
        "date": d.isoformat(),
        "standard": p.label,
        "meridian": p.meridian,
        "base_offset_min": p.utc_offset_min,
        "dst_active": dst is not None,
        "dst_advance_min": dst.advance_min if dst else 0,
        "total_offset_min": total_offset,
        "utc_string": utc_str,
    }


def correction_minutes_for_saju(
    d: date,
    longitude: float = 127.0,
) -> float:
    """
    사주 계산 시 벽시계에서 빼야 할 보정값(분).

    벽시계 12:00 기준으로 진태양시를 구할 때:
      진태양시 = 12:00 − correction_minutes

    양수 → 벽시계가 태양시보다 빠름 (시계가 앞섬)
    음수 → 벽시계가 태양시보다 느림 (시계가 뒤짐)
    """
    p = get_standard_period(d)
    dst = get_dst_record(d)
    dst_min = dst.advance_min if dst else 0

    # 보정 = (표준자오선 − 출생지경도) × 4 + DST보정
    correction = (p.meridian - longitude) * 4.0 + dst_min
    return correction


# ===================================================================
# 7. 검증: 첨부 표와 대조
# ===================================================================
def _verify_table():
    """
    사용자 제공 표와 동일한 결과가 나오는지 검증.
    서울(127°E) 기준, 벽시계 12시 → 사주 시각
    """
    test_cases = [
        # (date, expected_saju_approx, description)
        (date(1895, 6, 15), "12:28", "북경 표준시 (120°E)"),
        (date(1897, 6, 15), "12:28", "북경 표준시 (120°E)"),  # 아직 120°
        (date(1900, 6, 15), "11:58", "한성 표준시 (127.5°E)"),
        (date(1920, 6, 15), "11:28", "동경 표준시 (135°E)"),
        (date(1948, 7, 15), "10:28", "동경+DST"),
        (date(1952, 6, 15), "11:28", "동경 (DST 없음)"),
        (date(1955, 6, 15), "10:58", "서울+DST"),
        (date(1957, 3, 15), "11:58", "서울 (DST 전)"),
        (date(1965, 6, 15), "11:28", "동경 (현행)"),
        (date(1987, 7, 15), "10:28", "동경+DST"),
        (date(1989, 6, 15), "11:28", "동경 (현행)"),
        (date(2024, 6, 15), "11:28", "동경 (현행)"),
    ]
    print("=" * 70)
    print("검증: 벽시계 12:00 (서울 127°E) → 진태양시 (EoT 제외)")
    print("=" * 70)
    all_pass = True
    for d, expected, desc in test_cases:
        # EoT 제외 계산 (표의 값은 EoT 미반영 근사)
        corr = correction_minutes_for_saju(d, 127.0)
        solar_min = 12 * 60 - corr
        h, m = divmod(int(solar_min), 60)
        result = f"{h:02d}:{m:02d}"
        status = "✓" if result == expected else "✗"
        if result != expected:
            all_pass = False
        info = describe_timezone_for_date(d)
        print(f"  {status} {d} ({desc}): 12:00 → {result} (기대: {expected}) "
              f"[{info['standard']}, {info['utc_string']}, DST={info['dst_active']}]")
    print("=" * 70)
    print(f"검증 결과: {'모두 통과 ✓' if all_pass else '일부 실패 ✗'}")
    return all_pass


if __name__ == "__main__":
    _verify_table()

    print()
    print("=" * 70)
    print("예시: 1950년 7월 15일 09:00 서울 출생")
    print("=" * 70)
    d = date(1950, 7, 15)
    info = describe_timezone_for_date(d)
    print(f"  표준시: {info['standard']}")
    print(f"  기준 자오선: {info['meridian']}°E")
    print(f"  UTC 오프셋: {info['utc_string']}")
    print(f"  DST: {'적용' if info['dst_active'] else '미적용'}")

    tst = wall_to_true_solar_time_historical(1950, 7, 15, 9, 0, longitude=127.0, apply_eot=False)
    print(f"  벽시계 09:00 → 진태양시(EoT 제외): {tst.strftime('%H:%M')}")

    tst_eot = wall_to_true_solar_time_historical(1950, 7, 15, 9, 0, longitude=127.0, apply_eot=True)
    print(f"  벽시계 09:00 → 진태양시(EoT 포함): {tst_eot.strftime('%H:%M')}")
