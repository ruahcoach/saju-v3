# -*- coding: utf-8 -*-
from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime, date, time, timedelta, timezone
import re, math, calendar as cal_mod, os
from urllib.parse import urlencode
from urllib.request import urlopen
import xml.etree.ElementTree as ET
import streamlit as st
from zoneinfo import ZoneInfo
try:
    from korean_lunar_calendar import KoreanLunarCalendar
    HAS_LUNAR = True
except Exception:
    HAS_LUNAR = False

from korea_tz_history import wall_to_true_solar_time, describe_timezone_for_date, get_wall_clock_utc_offset

def get_kasi_key():
    try:
        val = st.secrets.get('KASI_KEY')
        if val: return val
    except Exception: pass
    return os.getenv('KASI_KEY')

LOCAL_TZ = ZoneInfo('Asia/Seoul')
DEFAULT_LONGITUDE = 126.9780  # 서울 기본값

city_options = {
    "서울": 126.9780,
    "부산": 129.0756,
    "대구": 128.6014,
    "인천": 126.7052,
    "광주": 126.8526,
    "대전": 127.3845,
    "울산": 129.3114,
    "제주": 126.5312,
}

def to_solar_time(dt_local, longitude=DEFAULT_LONGITUDE):
    """역사적 표준시 + 썸머타임 + 균시차 완전 반영"""
    result = wall_to_true_solar_time(dt_local, longitude, apply_eot=True)
    if result.tzinfo is None:
        result = result.replace(tzinfo=LOCAL_TZ)
    return result

def tz_label_for_date(d):
    """날짜에 해당하는 표준시 라벨 반환 (예: '東京 UTC+09:00' 또는 '서울+DST UTC+09:30')"""
    info = describe_timezone_for_date(d if isinstance(d, date) and not isinstance(d, datetime) else d.date() if hasattr(d, 'date') else d)
    label = info['standard']
    dst_str = '+DST' if info['dst_active'] else ''
    return f"{label}{dst_str} {info['utc_string']}"

def utc_to_wall_clock(dt_utc, target_date):
    """UTC datetime을 해당 날짜의 한국 역사적 법정시(벽시계)로 변환"""
    offset_min = get_wall_clock_utc_offset(target_date if isinstance(target_date, date) and not isinstance(target_date, datetime) else target_date.date() if hasattr(target_date, 'date') else target_date)
    wall = dt_utc + timedelta(minutes=offset_min)
    return wall.replace(tzinfo=LOCAL_TZ)

def calc_correction_detail(birth_date, longitude=DEFAULT_LONGITUDE):
    """보정값 상세 내역 계산 — UI 표시용"""
    info = describe_timezone_for_date(birth_date)
    std_meridian = info['meridian']
    # 경도 보정 (표준 자오선과 출생지 경도 차이)
    lon_corr = (longitude - std_meridian) * 4  # 1도 = 4분
    # DST 보정
    dst_corr = -info['dst_advance_min'] if info['dst_active'] else 0
    # 합계 (벽시계 → 사주 시각)
    total = lon_corr + dst_corr
    return {
        'standard': info['standard'],
        'utc_string': info['utc_string'],
        'dst_active': info['dst_active'],
        'dst_min': dst_corr,
        'std_meridian': std_meridian,
        'longitude': longitude,
        'lon_corr_min': round(lon_corr, 1),
        'total_min': round(total, 1),
    }

def render_correction_html(corr, eot_min=0):
    """보정값 상세 HTML 렌더링"""
    parts = []
    parts.append(f"<b>법정시</b>: {corr['standard']} ({corr['utc_string']})")
    if corr['dst_active']:
        parts.append(f"<b>써머타임</b>: 적용 중 ({corr['dst_min']:+.0f}분)")
    parts.append(f"<b>경도보정</b>: {corr['std_meridian']:.1f}°→{corr['longitude']:.1f}° ({corr['lon_corr_min']:+.1f}분)")
    if abs(eot_min) > 0.5:
        parts.append(f"<b>균시차</b>: {eot_min:+.1f}분")
    total = corr['total_min'] + eot_min
    parts.append(f"<b>합계 보정</b>: <span style='font-size:14px;font-weight:bold;color:#8b4513;'>{total:+.0f}분</span>")
    return '<div class="tz-info-box">' + '<br>'.join(parts) + '</div>'

def check_boundary_warning(dt_solar, jie24_solar, hour_branch_idx):
    """절입/시주 경계 경고 확인"""
    warnings = []
    # 절입 ±2시간 체크
    for name, t in jie24_solar.items():
        diff_min = abs((dt_solar - t).total_seconds()) / 60
        if diff_min <= 120:
            warnings.append(f"⚠️ 절입 경계: {name} 시각과 {diff_min:.0f}분 차이 — 월주가 달라질 수 있어 정밀검증 권장")
            break
    # 시주 경계 ±30분 체크
    mins = dt_solar.hour * 60 + dt_solar.minute
    si_boundaries = [23*60, 1*60, 3*60, 5*60, 7*60, 9*60, 11*60, 13*60, 15*60, 17*60, 19*60, 21*60]
    for sb in si_boundaries:
        diff = abs((mins - sb + 720) % 1440 - 720)
        if diff <= 30:
            warnings.append(f"⚠️ 시주 경계: 시주 전환 시각과 {diff}분 차이 — 시주가 달라질 수 있어 정밀검증 권장")
            break
    return warnings

def render_tst_compare_html(dt_wall, dt_tst, fp_wall, fp_tst):
    """벽시계 vs 진태양시 비교 HTML"""
    diff = (dt_tst - dt_wall).total_seconds() / 60
    html = '<div class="tst-compare">'
    html += f'<b>🔬 정밀검증 (진태양시 비교)</b><br>'
    html += f'벽시계: {dt_wall.strftime("%H:%M")} → 진태양시: {dt_tst.strftime("%H:%M")} (차이: {diff:+.0f}분)<br>'
    if fp_wall['hour'] != fp_tst['hour']:
        html += f'<span style="color:#c00;font-weight:bold;">⚠ 시주 차이: 벽시계={fp_wall["hour"]} / 진태양시={fp_tst["hour"]}</span><br>'
    else:
        html += f'시주 동일: {fp_wall["hour"]} ✅<br>'
    if fp_wall['month'] != fp_tst['month']:
        html += f'<span style="color:#c00;font-weight:bold;">⚠ 월주 차이: 벽시계={fp_wall["month"]} / 진태양시={fp_tst["month"]}</span>'
    else:
        html += f'월주 동일: {fp_wall["month"]} ✅'
    html += '</div>'
    return html

CHEONGAN = ['갑','을','병','정','무','기','경','신','임','계']
JIJI = ['자','축','인','묘','진','사','오','미','신','유','술','해']
HANJA_GAN = ['甲','乙','丙','丁','戊','己','庚','辛','壬','癸']
HANJA_JI = ['子','丑','寅','卯','辰','巳','午','未','申','酉','戌','亥']
MONTH_JI = ['인','묘','진','사','오','미','신','유','술','해','자','축']
JIE_TO_MONTH_JI = {'입춘':'인','경칩':'묘','청명':'진','입하':'사','망종':'오','소서':'미','입추':'신','백로':'유','한로':'술','입동':'해','대설':'자','소한':'축','(전년)대설':'자'}
MONTH_TO_2TERMS = {'인':('입춘','우수'),'묘':('경칩','춘분'),'진':('청명','곡우'),'사':('입하','소만'),'오':('망종','하지'),'미':('소서','대서'),'신':('입추','처서'),'유':('백로','추분'),'술':('한로','상강'),'해':('입동','소설'),'자':('대설','동지'),'축':('소한','대한')}
GAN_BG = {'갑':'#2ecc71','을':'#2ecc71','병':'#e74c3c','정':'#e74c3c','무':'#f1c40f','기':'#f1c40f','경':'#ffffff','신':'#ffffff','임':'#000000','계':'#000000'}
BR_BG = {'해':'#000000','자':'#000000','인':'#2ecc71','묘':'#2ecc71','사':'#e74c3c','오':'#e74c3c','신':'#ffffff','유':'#ffffff','진':'#f1c40f','술':'#f1c40f','축':'#f1c40f','미':'#f1c40f'}
def gan_fg(gan): bg=GAN_BG.get(gan,'#fff'); return '#000000' if bg in ('#ffffff','#f1c40f') else '#ffffff'
def br_fg(ji): bg=BR_BG.get(ji,'#fff'); return '#000000' if bg in ('#ffffff','#f1c40f') else '#ffffff'
STEM_ELEM = {'갑':'목','을':'목','병':'화','정':'화','무':'토','기':'토','경':'금','신':'금','임':'수','계':'수'}
STEM_YY = {'갑':'양','을':'음','병':'양','정':'음','무':'양','기':'음','경':'양','신':'음','임':'양','계':'음'}
BRANCH_MAIN = {'자':'계','축':'기','인':'갑','묘':'을','진':'무','사':'병','오':'정','미':'기','신':'경','유':'신','술':'무','해':'임'}
ELEM_PRODUCE = {'목':'화','화':'토','토':'금','금':'수','수':'목'}
ELEM_CONTROL = {'목':'토','화':'금','토':'수','금':'목','수':'화'}
ELEM_OVER_ME = {v:k for k,v in ELEM_CONTROL.items()}
ELEM_PROD_ME = {v:k for k,v in ELEM_PRODUCE.items()}
SAMHAP = {'화':{'인','오','술'},'목':{'해','묘','미'},'수':{'신','자','진'},'금':{'사','유','축'}}
MONTH_SAMHAP = {'인':'화','오':'화','술':'화','해':'목','묘':'목','미':'목','신':'수','자':'수','진':'수','사':'금','유':'금','축':'금'}
BRANCH_HIDDEN = {'자':['임','계'],'축':['계','신','기'],'인':['무','병','갑'],'묘':['갑','을'],'진':['을','계','무'],'사':['무','경','병'],'오':['병','기','정'],'미':['정','을','기'],'신':['무','임','경'],'유':['경','신'],'술':['신','정','무'],'해':['무','갑','임']}
NOTEARTH = {'갑','을','병','정','경','신','임','계'}
def stems_of_element(elem): return {'목':['갑','을'],'화':['병','정'],'토':['무','기'],'금':['경','신'],'수':['임','계']}[elem]
def stem_with_polarity(elem, parity): a,b=stems_of_element(elem); return a if parity=='양' else b
def is_yang_stem(gan): return gan in ['갑','병','무','경','임']
def ten_god_for_stem(day_stem, other_stem):
    d_e,d_p = STEM_ELEM[day_stem],STEM_YY[day_stem]
    o_e,o_p = STEM_ELEM[other_stem],STEM_YY[other_stem]
    if o_e==d_e: return '비견' if o_p==d_p else '겁재'
    if o_e==ELEM_PRODUCE[d_e]: return '식신' if o_p==d_p else '상관'
    if o_e==ELEM_CONTROL[d_e]: return '편재' if o_p==d_p else '정재'
    if o_e==ELEM_OVER_ME[d_e]: return '편관' if o_p==d_p else '정관'
    if o_e==ELEM_PROD_ME[d_e]: return '편인' if o_p==d_p else '정인'
    return '미정'
def ten_god_for_branch(day_stem, branch): return ten_god_for_stem(day_stem, BRANCH_MAIN[branch])
def six_for_stem(ds,s): return ten_god_for_stem(ds,s)
def six_for_branch(ds,b): return ten_god_for_branch(ds,b)
def all_hidden_stems(branches):
    s=set()
    for b in branches: s.update(BRANCH_HIDDEN.get(b,[]))
    return s
def is_first_half_by_terms(dt_solar, first_term_dt, mid_term_dt): return first_term_dt <= dt_solar < mid_term_dt

JIE_DEGREES = {'입춘':315,'경칩':345,'청명':15,'입하':45,'망종':75,'소서':105,'입추':135,'백로':165,'한로':195,'입동':225,'대설':255,'소한':285}
JIE_ORDER = ['입춘','경칩','청명','입하','망종','소서','입추','백로','한로','입동','대설','소한']
JIE24_DEGREES = {'입춘':315,'우수':330,'경칩':345,'춘분':0,'청명':15,'곡우':30,'입하':45,'소만':60,'망종':75,'하지':90,'소서':105,'대서':120,'입추':135,'처서':150,'백로':165,'추분':180,'한로':195,'상강':210,'입동':225,'소설':240,'대설':255,'동지':270,'소한':285,'대한':300}
JIE24_ORDER = ['입춘','우수','경칩','춘분','청명','곡우','입하','소만','망종','하지','소서','대서','입추','처서','백로','추분','한로','상강','입동','소설','대설','동지','소한','대한']
SIDU_START = {('갑','기'):'갑',('을','경'):'병',('병','신'):'무',('정','임'):'경',('무','계'):'임'}
def month_start_gan_idx(year_gan_idx): return ((year_gan_idx % 5) * 2 + 2) % 10
K_ANCHOR = 49

def jdn_0h_utc(y,m,d):
    if m<=2: y-=1; m+=12
    A=y//100; B=2-A+A//4
    return int(365.25*(y+4716))+int(30.6001*(m+1))+d+B-1524

def jd_from_utc(dt_utc):
    y=dt_utc.year; m=dt_utc.month
    d=dt_utc.day+(dt_utc.hour+dt_utc.minute/60+dt_utc.second/3600)/24
    if m<=2: y-=1; m+=12
    A=y//100; B=2-A+A//4
    return int(365.25*(y+4716))+int(30.6001*(m+1))+d+B-1524.5

def norm360(x): return x%360.0
def wrap180(x): return (x+180.0)%360.0-180.0

def delta_t_seconds(year):
    y = year
    if 2005 <= y <= 2050:
        t = y - 2000
        return 62.92 + 0.32217*t + 0.005589*t*t
    elif 1986 <= y < 2005:
        t = y - 2000
        return 63.86 + 0.3345*t - 0.060374*t*t \
               + 0.0017275*t**3 + 0.000651814*t**4 \
               + 0.00002373599*t**5
    else:
        t = (y - 2000)/100
        return 62.92 + 32.217*t + 55.89*t*t

def equation_of_time_minutes(dt_utc):
    doy = dt_utc.timetuple().tm_yday
    B = math.radians((360/365) * (doy - 81))
    return 9.87*math.sin(2*B) - 7.53*math.cos(B) - 1.5*math.sin(B)
    
try:
    import ephem as _ephem
    _HAS_EPHEM = True
except ImportError:
    _HAS_EPHEM = False

def solar_longitude_deg(dt_utc):
    """태양 황경(도) 계산 — ephem(VSOP87 완전판) 우선, 없으면 간이공식"""
    if _HAS_EPHEM:
        d = _ephem.Date(dt_utc)
        s = _ephem.Sun(d)
        eq = _ephem.Equatorial(s.ra, s.dec, epoch=d)
        ec = _ephem.Ecliptic(eq)
        return math.degrees(float(ec.lon)) % 360
    # 폴백: 간이 Meeus 공식
    dt_tt = dt_utc + timedelta(seconds=delta_t_seconds(dt_utc.year))
    JD = jd_from_utc(dt_tt)
    T = (JD - 2451545.0) / 36525.0
    L0 = norm360(280.46646 + 36000.76983*T + 0.0003032*T*T)
    M  = norm360(357.52911 + 35999.05029*T - 0.0001537*T*T)
    Mr = math.radians(M)
    C = ((1.914602 - 0.004817*T - 0.000014*T*T) * math.sin(Mr)
         + (0.019993 - 0.000101*T) * math.sin(2*Mr)
         + 0.000289 * math.sin(3*Mr))
    theta = L0 + C
    Omega = 125.04 - 1934.136*T
    lam = theta - 0.00569 - 0.00478 * math.sin(math.radians(Omega))
    return norm360(lam)

def find_longitude_time_utc(year, target_deg, approx_dt_local):
    """절기 시각을 UTC로 계산하여 반환 (천문 이벤트 시각)"""
    a=(approx_dt_local-timedelta(days=7)).astimezone(timezone.utc)
    b=(approx_dt_local+timedelta(days=7)).astimezone(timezone.utc)
    def f(dt_utc): return wrap180(solar_longitude_deg(dt_utc)-target_deg)
    scan,step=a,timedelta(hours=6); fa=f(scan); found=False
    while scan<b:
        scan2=scan+step; fb=f(scan2)
        if fa==0 or fb==0 or (fa<0 and fb>0) or (fa>0 and fb<0): a,b=scan,scan2; found=True; break
        scan,fa=scan2,fb
    if not found:
        a=(approx_dt_local-timedelta(days=1)).astimezone(timezone.utc)
        b=(approx_dt_local+timedelta(days=1)).astimezone(timezone.utc)
    for _ in range(100):
        mid=a+(b-a)/2; fm=f(mid); fa=f(a)
        if fm==0: a=b=mid; break
        if (fa<=0 and fm>=0) or (fa>=0 and fm<=0): b=mid
        else: a=mid
    return (a+(b-a)/2).replace(microsecond=0)  # UTC datetime 반환

def find_longitude_time_local(year, target_deg, approx_dt_local):
    """절기 시각을 벽시계(당시 법정시)로 변환하여 반환"""
    dt_utc = find_longitude_time_utc(year, target_deg, approx_dt_local)
    # UTC → 역사적 한국 법정시(벽시계)로 변환
    target_date = dt_utc.date()
    offset_min = get_wall_clock_utc_offset(target_date)
    mid_local = dt_utc + timedelta(minutes=offset_min)
    return mid_local.replace(tzinfo=LOCAL_TZ, microsecond=0)

def approx_guess_local(year):
    rough={'입춘':(2,4),'경칩':(3,6),'청명':(4,5),'입하':(5,6),'망종':(6,6),'소서':(7,7),'입추':(8,8),'백로':(9,8),'한로':(10,8),'입동':(11,7),'대설':(12,7),'소한':(1,6)}
    out={}
    for name,(m,d) in rough.items(): out[name]=datetime(year,m,d,9,0,tzinfo=LOCAL_TZ)
    out['(전년)대설']=datetime(year-1,12,7,9,0,tzinfo=LOCAL_TZ)
    return out

def approx_guess_local_24(year):
    rough={'입춘':(2,4),'우수':(2,19),'경칩':(3,6),'춘분':(3,21),'청명':(4,5),'곡우':(4,20),'입하':(5,6),'소만':(5,21),'망종':(6,6),'하지':(6,21),'소서':(7,7),'대서':(7,23),'입추':(8,8),'처서':(8,23),'백로':(9,8),'추분':(9,23),'한로':(10,8),'상강':(10,23),'입동':(11,7),'소설':(11,22),'대설':(12,7),'동지':(12,22),'소한':(1,6),'대한':(1,20)}
    out={}
    for name,(m,d) in rough.items(): out[name]=datetime(year,m,d,9,0,tzinfo=LOCAL_TZ)
    return out

def compute_jie_times_calc(year):
    """12절기 시각 계산 — 벽시계(당시 법정시) 반환"""
    guesses=approx_guess_local(year); terms={}
    for name in JIE_ORDER: terms[name]=find_longitude_time_local(year,JIE_DEGREES[name],guesses[name])
    terms['(전년)대설']=find_longitude_time_local(year-1,JIE_DEGREES['대설'],guesses['(전년)대설'])
    return terms

def compute_jie24_times_calc(year):
    """24절기 시각 계산 — 벽시계(당시 법정시) 반환"""
    guesses=approx_guess_local_24(year); out={}
    for name in JIE24_ORDER:
        deg=JIE24_DEGREES[name]; approx=guesses[name]; calc_year=approx.year
        out[name]=find_longitude_time_local(calc_year,deg,approx)
    return out

def pillar_day_by_2300(dt_solar):
    return (dt_solar+timedelta(days=1)).date() if (dt_solar.hour,dt_solar.minute)>=(23,0) else dt_solar.date()

def day_ganji_solar(dt_solar, k_anchor=K_ANCHOR):
    d=pillar_day_by_2300(dt_solar); idx60=(jdn_0h_utc(d.year,d.month,d.day)+k_anchor)%60
    cidx,jidx=idx60%10,idx60%12; return CHEONGAN[cidx]+JIJI[jidx],cidx,jidx

def hour_branch_idx_2300(dt_solar):
    mins = dt_solar.hour * 60 + dt_solar.minute
    off = (mins - (23 * 60)) % 1440
    return off // 120
def sidu_zi_start_gan(day_gan):
    for pair,start in SIDU_START.items():
        if day_gan in pair: return start
    raise ValueError('invalid day gan')

def four_pillars_from_solar(dt_solar, k_anchor=K_ANCHOR):
    jie12_wall = compute_jie_times_calc(dt_solar.year)

    # 사주 비교용: 절기도 진태양시로 변환
    if st.session_state.get('apply_solar', True):
        lon = st.session_state.get('longitude', DEFAULT_LONGITUDE)
        jie_solar = {}
        for k in jie12_wall:
            jie_solar[k] = to_solar_time(jie12_wall[k], lon)
    else:
        jie_solar = dict(jie12_wall)

    ipchun=jie_solar.get("입춘")
    y=dt_solar.year-1 if dt_solar<ipchun else dt_solar.year
    y_gidx=(y-4)%10; y_jidx=(y-4)%12
    year_pillar=CHEONGAN[y_gidx]+JIJI[y_jidx]
    order=list(jie_solar.items()); order.sort(key=lambda x:x[1])
    last='(전년)대설'
    for name,t in order:
        if dt_solar>=t: last=name
        else: break
    m_branch=JIE_TO_MONTH_JI[last]; m_bidx=MONTH_JI.index(m_branch)
    m_gidx=(month_start_gan_idx(y_gidx)+m_bidx)%10
    month_pillar=CHEONGAN[m_gidx]+m_branch
    day_pillar,d_cidx,d_jidx=day_ganji_solar(dt_solar,k_anchor)
    h_j_idx=hour_branch_idx_2300(dt_solar)
    zi_start=sidu_zi_start_gan(CHEONGAN[d_cidx])
    h_c_idx=(CHEONGAN.index(zi_start)+h_j_idx)%10
    hour_pillar=CHEONGAN[h_c_idx]+JIJI[h_j_idx]
    return {'year':year_pillar,'month':month_pillar,'day':day_pillar,'hour':hour_pillar,'y_gidx':y_gidx,'m_gidx':m_gidx,'m_bidx':m_bidx,'d_cidx':d_cidx}

def next_prev_jie(dt_solar, jie_solar_dict):
    items=[(n,t) for n,t in jie_solar_dict.items()]; items.sort(key=lambda x:x[1])
    prev_t=items[0][1]
    for _,t in items:
        if t>dt_solar: return prev_t,t
        prev_t=t
    return prev_t,prev_t

def round_half_up(x): return int(math.floor(x+0.5))

def dayun_start_age(dt_solar, jie12_solar, forward):
    prev_t,next_t=next_prev_jie(dt_solar,jie12_solar)
    delta_days=(next_t-dt_solar).total_seconds()/86400.0 if forward else (dt_solar-prev_t).total_seconds()/86400.0
    return max(0,round_half_up(delta_days/3.0))

def build_dayun_list(month_gidx, month_bidx, forward, start_age, count=10):
    dirv=1 if forward else -1; out=[]
    for i in range(1,count+1):
        g_i=(month_gidx+dirv*i)%10; b_i=(month_bidx+dirv*i)%12
        out.append({'start_age':start_age+(i-1)*10,'g_idx':g_i,'b_idx':b_i})
    return out

def calc_age_on(dob, now_dt):
    today=now_dt.date() if hasattr(now_dt,"date") else now_dt
    return today.year-dob.year-((today.month,today.day)<(dob.month,dob.day))

def lunar_to_solar(y,m,d,is_leap=False):
    if not HAS_LUNAR: raise RuntimeError('korean-lunar-calendar 미설치')
    c=KoreanLunarCalendar(); c.setLunarDate(y,m,d,is_leap); return date(c.solarYear,c.solarMonth,c.solarDay)

@dataclass
class Inputs:
    day_stem: str
    month_branch: str
    month_stem: str
    stems_visible: list
    branches_visible: list
    solar_dt: datetime
    first_term_dt: datetime
    mid_term_dt: datetime
    day_from_jieqi: int

def decide_geok(inp):
    ds=inp.day_stem; mb=inp.month_branch; ms=inp.month_stem
    stems=list(inp.stems_visible); branches=list(inp.branches_visible)
    ds_e=STEM_ELEM[ds]; ds_p=STEM_YY[ds]
    mb_main=BRANCH_MAIN[mb]; mb_e,mb_p=STEM_ELEM[mb_main],STEM_YY[mb_main]
    visible_set=set(stems); hidden_set=all_hidden_stems(branches); pool=visible_set|hidden_set
    if mb in {'자','오','묘','유','인','신','사','해'} and ds_e==mb_e:
        off_e=ELEM_OVER_ME[ds_e]
        jung_gwan=stem_with_polarity(off_e,'음' if ds_p=='양' else '양')
        pyeon_gwan=stem_with_polarity(off_e,ds_p)
        same_polarity=(ds_p==mb_p)
        any_jung_br=any(ten_god_for_branch(ds,b)=='정관' for b in branches)
        any_pyeon_br=any(ten_god_for_branch(ds,b)=='편관' for b in branches)
        if same_polarity:
            if (jung_gwan in visible_set) or any_jung_br:
                why=('정관 '+jung_gwan+' 천간 투간' if jung_gwan in visible_set else '지지 정관 존재')
                return '건록격',f'[특수] 월비+{why}->건록격'
            else: return '월비격','[특수] 월비, 정관 없음->월비격'
        else:
            if (pyeon_gwan in visible_set) or any_pyeon_br:
                why=('편관 '+pyeon_gwan+' 천간 투간' if pyeon_gwan in visible_set else '지지 편관 존재')
                return '양인격',f'[특수] 월겁+{why}->양인격'
            else: return '월겁격','[특수] 월겁, 편관 없음->월겁격'
    grp='자오묘유' if mb in {'자','오','묘','유'} else ('인신사해' if mb in {'인','신','사','해'} else '진술축미')
    if grp=='자오묘유':
        month_elem=STEM_ELEM[mb_main]
        same_elem_vis=[s for s in stems if STEM_ELEM.get(s)==month_elem]
        if same_elem_vis:
            pick=next((s for s in same_elem_vis if STEM_YY[s]!=ds_p),same_elem_vis[0])
            six=ten_god_for_stem(ds,pick); return f'{six}격',f'[자오묘유] {pick} 투간->{six}격'
        six=ten_god_for_stem(ds,mb_main); return f'{six}격',f'[자오묘유] 투간없음->체(본기 {mb_main}){six}격'
    if grp=='인신사해':
        rokji=mb_main; month_elem=STEM_ELEM[rokji]
        base_stems=set(stems_of_element(month_elem))
        base_vis=[s for s in inp.stems_visible if s in base_stems]
        if base_vis:
            pick=base_vis[0]
            if month_elem==STEM_ELEM[ds]:
                off_e=ELEM_OVER_ME[STEM_ELEM[ds]]
                jung_gwan=stem_with_polarity(off_e,'음' if STEM_YY[ds]=='양' else '양')
                pyeon_gwan=stem_with_polarity(off_e,STEM_YY[ds])
                if STEM_YY[pick]==STEM_YY[ds]:
                    if jung_gwan in inp.stems_visible: return '건록격',f'[인신사해] {pick}투간+정관{jung_gwan}->건록격'
                else:
                    if pyeon_gwan in inp.stems_visible: return '양인격',f'[인신사해] {pick}투간+편관{pyeon_gwan}->양인격'
            six=ten_god_for_stem(ds,pick); return f'{six}격',f'[인신사해] 록지{pick}투간->{six}격'
        tri_elem=MONTH_SAMHAP.get(mb,'')
        if tri_elem:
            tri_grp=SAMHAP[tri_elem]; others=set(tri_grp)-{mb}
            if others.issubset(set(inp.branches_visible)) and is_first_half_by_terms(inp.solar_dt,inp.first_term_dt,inp.mid_term_dt):
                tri_stems=stems_of_element(tri_elem)
                tri_vis=[s for s in tri_stems if s in inp.stems_visible]
                if tri_vis and tri_elem!=STEM_ELEM[ds]:
                    pick=tri_vis[0]; six=ten_god_for_stem(ds,pick)
                    return f'중기격({six})',f'[인신사해] 삼합+중기사령+{pick}투간->중기격'
        if ms: six=ten_god_for_stem(ds,ms); return f'{six}격',f'[인신사해] 록지투간없음->월간{ms}기준{six}격'
        six=ten_god_for_stem(ds,rokji); return f'{six}격',f'[인신사해] 폴백->본기({rokji}){six}격'
    if grp=='진술축미':
        h=BRANCH_HIDDEN.get(mb,[]); mb_main_l=BRANCH_MAIN[mb]; is_front12=(inp.day_from_jieqi<=11)
        tri_elem=MONTH_SAMHAP.get(mb,'')
        if tri_elem:
            tri_grp=SAMHAP[tri_elem]; others=set(tri_grp)-{mb}; partners=others&set(branches)
            if partners:
                if tri_elem==STEM_ELEM[ds]:
                    six=ten_god_for_stem(ds,mb_main_l); return f'{six}격',f'[진술축미] 반합{mb}+동일오행->체(본기){six}격'
                tri_stems=stems_of_element(tri_elem); tri_vis=[s for s in tri_stems if s in visible_set]
                mid_qi=h[1] if len(h)>=2 else (h[-1] if h else mb_main_l); mid_is_tri=(STEM_ELEM.get(mid_qi)==tri_elem)
                pick=tri_vis[0] if tri_vis else (mid_qi if mid_is_tri else stem_with_polarity(tri_elem,'음' if STEM_YY[ds]=='양' else '양'))
                six=ten_god_for_stem(ds,pick); return f'{six}격',f'[진술축미] 반합+{pick}기준{six}격'
        if is_front12:
            yeogi=h[0] if h else mb_main_l; y_elem=STEM_ELEM[yeogi]
            same_vis=[s for s in stems if STEM_ELEM.get(s)==y_elem]
            opp=[s for s in same_vis if STEM_YY[s]!=ds_p]
            pick=opp[0] if opp else (same_vis[0] if same_vis else yeogi)
            six=ten_god_for_stem(ds,pick); return f'{six}격',f'[진술축미] 절입후12일이내->여기사령({pick}){six}격'
        else:
            earth_vis=[s for s in ('무','기') if s in visible_set]
            opp=[s for s in earth_vis if STEM_YY[s]!=ds_p]
            pick=opp[0] if opp else (earth_vis[0] if earth_vis else mb_main_l)
            six=ten_god_for_stem(ds,pick); return f'{six}격',f'[진술축미] 절입13일이후->주왕토({pick}){six}격'
    six=ten_god_for_stem(ds,BRANCH_MAIN[mb]); return f'{six}격',f'[폴백]->체(본기{BRANCH_MAIN[mb]}){six}격'

def calc_wolun_accurate(year):
    jie12_prev=compute_jie_times_calc(year-1); jie12_this=compute_jie_times_calc(year); jie12_next=compute_jie_times_calc(year+1)
    jie24_prev=compute_jie24_times_calc(year-1); jie24_this=compute_jie24_times_calc(year); jie24_next=compute_jie24_times_calc(year+1)
    collected=[]
    for src_jie in [jie12_prev,jie12_this,jie12_next]:
        for jname in JIE_ORDER:
            if jname in src_jie:
                t = src_jie[jname]
                if t.year==year: collected.append((t,jname))
    collected.sort(key=lambda x:x[0])
    items=[]
    for t,jname in collected:
        t_calc = t + timedelta(seconds=1); fp=four_pillars_from_solar(t_calc)
        m_gan=fp['month'][0]; m_ji=fp['month'][1]
        t2_name=MONTH_TO_2TERMS[m_ji][1]; t2=None
        for src in [jie24_this,jie24_prev,jie24_next]:
            if t2_name in src:
                cand = src[t2_name]
                if cand>t: t2=cand; break
        jie_idx=JIE_ORDER.index(jname); next_jname=JIE_ORDER[(jie_idx+1)%12]; t_end=None
        for src in [jie12_this,jie12_next,jie12_prev]:
            if next_jname in src:
                nt = src[next_jname]
                if nt>t: t_end=nt; break
        items.append({'month':t.month,'gan':m_gan,'ji':m_ji,'t1':t,'t2':t2,'t_end':t_end})
    return items

def calc_ilun_strip(start_dt, end_dt, day_stem, k_anchor=K_ANCHOR):
    items=[]; cur=start_dt.replace(hour=12,minute=0,second=0,microsecond=0)
    if cur<start_dt: cur=cur+timedelta(days=1)
    while cur<end_dt:
        dj,dc,djidx=day_ganji_solar(cur,k_anchor); g,j=dj[0],dj[1]
        items.append({'date':cur.date(),'gan':g,'ji':j,'six':f'{six_for_stem(day_stem,g)}/{six_for_branch(day_stem,j)}'})
        cur=cur+timedelta(days=1)
    return items

# ── 사령(司令) 데이터 ──
SARYEONG = {
    "해": {"early_15": "갑", "late_15": "임"},
    "자": {"early_15": "임", "late_15": "계"},
    "축": {"early_15": "계", "late_15": "신"},
    "인": {"early_15": "병", "late_15": "갑"},
    "묘": {"early_15": "갑", "late_15": "을"},
    "진": {"early_15": "을", "late_15": "계"},
    "사": {"early_15": "경", "late_15": "병"},
    "오": {"early_15": "병", "late_15": "정"},
    "미": {"early_15": "을", "late_15": "정"},
    "신": {"early_15": "임", "late_15": "경"},
    "유": {"early_15": "경", "late_15": "신"},
    "술": {"early_15": "신", "late_15": "정"},
}

# ── 당령(當令) 데이터 ──
DANGRYEONG = [
    {"months":["자","축"],"period":"동지~입춘","heaven_mission":"계수","description":"깊이를 더하고, 내면을 정화하며, 감정과 지혜를 축적하는 사명을 받았습니다."},
    {"months":["인","묘"],"period":"입춘~춘분","heaven_mission":"갑목","description":"새로운 시작을 열고, 성장의 씨앗을 틔우는 개척의 사명을 받았습니다."},
    {"months":["묘","진"],"period":"춘분~입하","heaven_mission":"을목","description":"관계를 다듬고, 부드럽게 확장하며 조화를 이루는 사명을 받았습니다."},
    {"months":["사","오"],"period":"입하~하지","heaven_mission":"병화","description":"세상에 빛을 드러내고, 에너지를 외부로 확산하는 사명을 받았습니다."},
    {"months":["오","미"],"period":"하지~입추","heaven_mission":"정화","description":"따뜻함으로 사람을 연결하고, 관계 속에서 의미를 완성하는 사명을 받았습니다."},
    {"months":["신","유"],"period":"입추~추분","heaven_mission":"경금","description":"질서를 세우고, 불필요한 것을 정리하며 기준을 만드는 사명을 받았습니다."},
    {"months":["유","술"],"period":"추분~입동","heaven_mission":"신금","description":"정밀함과 통찰로 본질을 구분하고 다듬는 사명을 받았습니다."},
    {"months":["해","자"],"period":"입동~동지","heaven_mission":"임수","description":"포용과 흐름 속에서 세상을 연결하고 순환시키는 사명을 받았습니다."},
]

def get_saryeong_gan(month_branch, day_from_jieqi):
    sr = SARYEONG.get(month_branch)
    if not sr: return None, None
    if day_from_jieqi < 15:
        return sr["early_15"], "전반15일"
    else:
        return sr["late_15"], "후반15일"

def get_dangryeong(month_branch, dt_solar=None, jie24_solar=None):
    boundary_jie = {'오':'하지','묘':'춘분','유':'추분','자':'동지','해':'입동'}
    if month_branch in boundary_jie and dt_solar and jie24_solar:
        jie_name = boundary_jie[month_branch]
        jie_dt = jie24_solar.get(jie_name)
        if jie_dt:
            matched = [item for item in DANGRYEONG if month_branch in item['months']]
            if len(matched) >= 2:
                return matched[1] if dt_solar >= jie_dt else matched[0]
            elif matched:
                return matched[0]
    for item in DANGRYEONG:
        if month_branch in item['months']:
            return item
    return None

def get_nearby_jeolip(dt_ref):
    """dt_ref 근처의 절기를 벽시계(당시 법정시)로 반환"""
    year = dt_ref.year
    all_jeolip = []
    for y in [year-1, year, year+1]:
        jie24 = compute_jie24_times_calc(y)
        for name in JIE24_ORDER:
            if name in jie24:
                t = jie24[name]
                all_jeolip.append((name, t))
    all_jeolip.sort(key=lambda x: x[1])
    prev_item = None
    next_item = None
    for item in all_jeolip:
        if item[1] <= dt_ref:
            prev_item = item
        elif next_item is None and item[1] > dt_ref:
            next_item = item
    return prev_item, next_item

# ── 격(格) 카드 데이터 ──
GYEOK_CARDS = [
    {"slug":"geonrok","card_title":"체제건립 · 건록격","icon":"🏛️","one_liner":"세상을 더 나은 규칙과 교육으로 바꾸려는 '기반을 만드는 사람'","story":"당신은 혼란 속에서도 기준을 세우는 사람입니다. 무너진 질서를 그냥 두지 않고, 공부하고 정리하고 글과 말로 설득해요. 사람들이 안전해지려면 제도, 교육, 원칙이 필요하다고 믿기 때문에, 오늘도 조용히 초석을 다지고 있습니다. 당신이 추구하는 건 품격 있는 변화, 즉 흔들리지 않는 기반 위에서 세상을 바꾸는 일이에요.","strengths":["학습력과 정리력","말, 글로 설득하는 힘","윤리, 품격을 지키는 태도","장기전에서 버티는 꾸준함"],"growth_tips":["70% 준비되면 작은 실행부터","원칙을 말하기 전에 상대의 사정 한 문장 먼저","비판보다 대안으로 말하기"],"praise_keywords":["기반을 만든다","품격 있다","믿고 맡길 수 있다","정리력이 탁월하다","원칙 위의 따뜻함"],"keywords":["건록격","건록","월비격","월비"]},
    {"slug":"yangin","card_title":"체제수호 · 양인격","icon":"🛡️","one_liner":"약자를 지키기 위해 몸으로 책임지는 '방패형 리더'","story":"당신은 위험을 보면 먼저 몸이 움직이는 사람입니다. 불의와 부조리를 그냥 넘기지 못하고, 누군가 다치면 내가 대신 막고 싶어져요. 그래서 개인의 힘을 키우게 돕거나, 필요하면 팀을 만들어 함께 버텨냅니다. 당신이 추구하는 건 보호와 의리입니다.","strengths":["강한 책임감","약자 보호 본능","팀을 지키는 헌신","위기 대응력"],"growth_tips":["도와주기 전에 스스로 할 수 있는 1단계부터 요청하기","의리=침묵이 아니라 건강한 경계","휴식도 책임의 일부로 일정에 넣기"],"praise_keywords":["든든하다","의리가 있다","지켜준다","리더십이 있다","끝까지 책임진다"],"keywords":["양인격","양인","월겁격","월겁"]},
    {"slug":"sanggwan","card_title":"산업융합 · 상관격","icon":"🔧","one_liner":"규칙을 활용해 혁신을 만드는 '응용의 천재'","story":"당신은 정해진 틀만 따르면 답답해지는 사람입니다. 주변 환경을 빠르게 읽고, 있는 자원을 엮어서 새로운 방식을 만들어내요. 변화가 올 때 오히려 살아나고, 효율적인 길을 찾아내는 능력이 탁월합니다.","strengths":["임기응변과 적응력","아이디어를 현실로 바꾸는 응용력","혁신 추진력","효율 중심 사고"],"growth_tips":["아이디어는 한 장 요약+첫 실행까지","편법처럼 보일 땐 근거와 리스크를 먼저 공개","관계 갈등은 사실-감정-요청 순서로 말하기"],"praise_keywords":["센스 있다","응용력이 탁월하다","혁신적이다","문제 해결이 빠르다","길을 만든다"],"keywords":["상관격","상관"]},
    {"slug":"sikshin","card_title":"연구개발 · 식신격","icon":"🧪","one_liner":"실험과 성과로 말하는 '꾸준한 빌더'","story":"당신은 해보면 알지라는 태도로 성장하는 사람입니다. 연구하고 만들고 개선하면서 실력을 쌓고, 결과로 증명하고 싶어해요. 주관적인 평가보다 객관적인 지표와 성과를 선호하고, 자유로운 몰입 환경에서 빛납니다.","strengths":["몰입과 실행","책임감 있는 생산성","학습, 실험, 개선 루프","객관적 판단"],"growth_tips":["일의 우선순위를 효과/시간 2축으로 정하기","성과 공유는 과정 1줄 + 결과 1줄로 짧게","사람 이슈도 시스템 개선으로 다루기"],"praise_keywords":["생산적이다","뭐든 척척 한다","실력이 있다","꾸준하다","결과로 증명한다"],"keywords":["식신격","식신"]},
    {"slug":"jeongin","card_title":"교육행정 · 정인격","icon":"📚","one_liner":"지식과 기준으로 안정감을 주는 '정석형 멘토'","story":"당신은 정리된 지식에서 편안함을 얻는 사람입니다. 배운 것을 체계적으로 쌓고, 그 범위 안에서 정확히 해내는 데 강해요. 눈에 띄기보다 실속을 추구하고, 맡은 임무를 차분히 해결합니다.","strengths":["개념 정리/문서화","안정적인 수행력","기준을 지키는 신뢰","지식 전달 능력"],"growth_tips":["새로운 일은 작게 테스트로 안전하게 확장","기준을 말하기 전에 상대의 목표를 먼저 확인","정답보다 작동하는 해결책 1개를 먼저 제시"],"praise_keywords":["박학다식하다","정리 잘한다","일처리가 정확하다","믿음직하다","기준을 잡아준다"],"keywords":["정인격","정인"]},
    {"slug":"pyeonin","card_title":"기획전략 · 편인격","icon":"🌙","one_liner":"상상과 공감으로 방향을 만드는 '의미 설계자'","story":"당신은 남들이 못 보는 가능성을 먼저 느끼는 사람입니다. 상상력과 감정의 깊이가 아이디어를 만들고, 사람의 고통을 그냥 지나치지 못해요. 구체화만 붙으면 엄청난 기획이 됩니다.","strengths":["창의적 기획","공감 기반 아이디어","미래 지향적 사고","깊은 통찰(감정/서사)"],"growth_tips":["아이디어는 1)목표 2)대상 3)첫 행동으로 쪼개기","현실 검증 파트너 1명을 정해 체크받기","감정 표현 뒤엔 구체적 요청을 붙이기"],"praise_keywords":["창의적이다","따뜻하다","상상력이 무한하다","의미를 만든다","사람을 살린다"],"keywords":["편인격","편인"]},
    {"slug":"jeongjae","card_title":"실용경제 · 정재격","icon":"🧱","one_liner":"안정과 실속을 지키는 '현실 설계자'","story":"당신은 지속 가능함이 얼마나 중요한지 아는 사람입니다. 크게 흔들리지 않는 수입, 안정적인 시스템, 실용적인 선택을 선호해요. 내 사람에게는 책임감 있게 베풀지만, 신뢰가 쌓이기 전까지는 쉽게 마음을 열지 않습니다.","strengths":["현실 감각","지출/리스크 관리","지속 가능한 선택","책임 있는 보호 본능"],"growth_tips":["변화는 작은 실험으로만 도입","돈/시간은 가치 예산을 따로 배정","기회는 손실 한도를 정해두고 도전"],"praise_keywords":["한결같다","실속 있다","안정적이다","믿음직하다","관리 능력이 좋다"],"keywords":["정재격","정재"]},
    {"slug":"pyeonjae","card_title":"혁신경영 · 편재격","icon":"🌍","one_liner":"판을 넓혀 기회를 만드는 '확장형 사업가'","story":"당신은 한 자리에서만 머무르면 답답해지는 사람입니다. 사람들과 함께 움직이며 기회를 찾고, 새로운 분야를 개척하는 데 에너지가 생겨요. 대외 활동에서 빛나고, 파트너십으로 성장을 만들려 합니다.","strengths":["도전과 확장성","네트워킹/협업","기회 포착","대외 감각(브랜딩/시장)"],"growth_tips":["동시에 벌리는 프로젝트는 2개까지만","수익/가치/리스크 3줄로 의사결정","파트너십은 역할, 기대, 정산을 문서로"],"praise_keywords":["진취적이다","도전적이다","확장성이 있다","호탕하다","판을 키운다"],"keywords":["편재격","편재"]},
    {"slug":"jeonggwan","card_title":"원리운영 · 정관격","icon":"⚖️","one_liner":"규칙과 협동으로 조직을 살리는 '원칙형 운영자'","story":"당신은 조직이 굴러가려면 규칙과 시스템이 필요하다고 믿는 사람입니다. 원리원칙에서 안정감을 느끼고, 모두가 공정하게 움직이길 바래요. 행정, 운영, 제도 정비에 강하고, 조직의 신뢰를 지켜냅니다.","strengths":["원칙과 공정성","운영/행정 능력","책임감","협업 구조를 만드는 힘"],"growth_tips":["원칙 적용 전 예외 기준을 1개만 정해두기","사람 문제는 규정보다 합의부터","칭찬은 태도+영향까지 구체적으로"],"praise_keywords":["성실하다","믿고 맡길 수 있다","원칙적이다","조직을 살린다","공정하다"],"keywords":["정관격","정관"]},
    {"slug":"pyeongwan","card_title":"관리감독 · 편관격","icon":"🦅","one_liner":"기준을 세워 구분하고 단속하는 '감독형 리더'","story":"당신은 흐릿한 상태를 싫어하고, 분명한 기준을 세우는 사람입니다. 조직의 경쟁력은 관리와 감독에서 나온다고 믿고, 역할, 위계, 규율을 선명하게 잡아줘요. 남들이 놓치는 문제를 빠르게 찾아내는 감별력이 강합니다.","strengths":["문제 탐지/감별력","위기 관리","규율 수립","결단력"],"growth_tips":["지적 전에 기대 기준을 먼저 공유","사람을 단속하기보다 행동을 교정하기","강한 메시지 뒤엔 반드시 출구(대안) 제공"],"praise_keywords":["특출나다","안목이 좋다","감별사다","결단력 있다","위기를 잡는다"],"keywords":["편관격","편관","중기격"]},
]

def find_geok_card(geok_name):
    geok_clean = geok_name.replace('격','').strip()
    for card in GYEOK_CARDS:
        for kw in card["keywords"]:
            if kw in geok_name or kw in geok_clean:
                return card
    return None

MOBILE_CSS = """
<style>
:root{--bg:#ffffff;--bg2:#f5f5f0;--card:#e8e4d8;--acc:#8b6914;--text:#2c2416;--sub:#6b5a3e;--r:10px;--bdr:#c8b87a;}
*{box-sizing:border-box;}
html{font-size:16px;}
body,.stApp{background:var(--bg)!important;color:var(--text)!important;font-family:"Noto Serif KR","Malgun Gothic",serif;-webkit-text-size-adjust:100%;}
#MainMenu,footer,header{visibility:hidden;}
.block-container{padding:0.5rem!important;max-width:480px!important;margin:0 auto!important;}
.stTextInput input,.stNumberInput input{background:#fff!important;color:var(--text)!important;border:1px solid var(--bdr)!important;border-radius:8px!important;font-size:16px!important;}
.stRadio label{color:var(--text)!important;font-size:15px!important;}
.stSelectbox label,.stCheckbox label{font-size:15px!important;}
.stButton>button{background:linear-gradient(135deg,#c8b87a,#a0945e)!important;color:#fff!important;border:1px solid var(--acc)!important;border-radius:6px!important;width:100%!important;font-size:12px!important;font-weight:bold!important;padding:2px 0px!important;white-space:nowrap!important;overflow:hidden;min-height:0!important;height:24px!important;line-height:1!important;}
.page-hdr{background:linear-gradient(135deg,#c8b87a,#a0945e);border-bottom:2px solid var(--acc);padding:12px;text-align:center;font-size:20px;font-weight:bold;color:#fff;letter-spacing:4px;margin-bottom:12px;}
.saju-wrap{background:var(--bg2);border:1px solid var(--bdr);border-radius:var(--r);padding:8px 4px 4px;margin-bottom:6px;}
.saju-table{width:100%;border-collapse:separate;border-spacing:4px;table-layout:fixed;}
.saju-table th{font-size:13px;color:var(--sub);text-align:center;padding:4px 0;}
.saju-table .lb td{font-size:12px;color:var(--sub);text-align:center;padding:2px 0;}
.gcell,.jcell{text-align:center;padding:0;}
.gcell div,.jcell div{display:flex;align-items:center;justify-content:center;width:100%;height:48px;border-radius:8px;font-weight:900;font-size:26px;border:1px solid rgba(0,0,0,.15);margin:1px auto;}
.sec-title{font-size:15px;color:var(--acc);font-weight:bold;padding:6px 8px;border-left:3px solid var(--acc);margin:12px 0 8px;}
.geok-box{background:rgba(200,184,122,.2);border:1px solid var(--acc);border-radius:8px;padding:12px 14px;margin:8px 0;font-size:13px;color:var(--text);}
.geok-name{font-size:17px;font-weight:900;color:#8b4513;margin-bottom:4px;}
.geok-why{font-size:12px;color:var(--sub);line-height:1.5;}
.today-banner{background:linear-gradient(135deg,#f5f0e8,#ede0c4);border:1px solid var(--acc);border-radius:8px;padding:8px 12px;margin-bottom:8px;font-size:14px;color:var(--sub);text-align:center;}
.sel-info{background:var(--card);border:1px solid var(--acc);border-radius:8px;padding:8px 12px;margin-bottom:8px;font-size:14px;color:var(--text);text-align:center;}
.cal-wrap{background:var(--bg2);border:1px solid var(--bdr);border-radius:var(--r);overflow:hidden;margin-bottom:10px;}
.cal-header{background:#c8b87a;text-align:center;padding:10px;font-size:16px;color:#fff;font-weight:bold;}
.cal-table{width:100%;border-collapse:collapse;}
.cal-table th{background:#d4c48a;color:#5a3e0a;font-size:12px;text-align:center;padding:5px 2px;border:1px solid var(--bdr);}
.cal-table td{text-align:center;padding:3px 1px;border:1px solid var(--bdr);font-size:12px;color:var(--text);vertical-align:top;min-width:42px;height:80px;}
.cal-table td.empty{background:#f0ece4;}
.cal-table td .dn{font-size:15px;font-weight:bold;margin-bottom:1px;}
.cal-table td.today-cell{background:#ffe8a0;border:1px solid var(--acc);}
.cal-table td.sun .dn{color:#E53935;}
.cal-table td.sat .dn{color:#1565C0;}
.geok-card-front{background:linear-gradient(135deg,rgba(200,184,122,.25),rgba(160,148,94,.15));border:1px solid var(--acc);border-radius:12px;padding:14px 16px;margin:4px 0 2px;cursor:pointer;}
.geok-card-title{font-size:16px;font-weight:900;color:#8b4513;}
.geok-card-oneliner{font-size:13px;color:var(--sub);line-height:1.5;margin-top:4px;}
.geok-card-detail{background:#faf6ed;border:1px solid #d4b86a;border-radius:10px;padding:14px 16px;margin:4px 0 8px;font-size:14px;color:var(--text);line-height:1.7;}
.geok-tag{display:inline-block;background:#f0e8c8;color:#7a5a1a;border:1px solid #c8a84a;border-radius:20px;padding:3px 10px;font-size:12px;margin:2px;}
.ai-section{background:linear-gradient(135deg,#fff0f5,#ffe4ee);border:1px solid #f4a0c0;border-radius:12px;padding:14px;margin:12px 0 4px;}
.bottom-btns{display:flex;gap:8px;margin:14px 0 8px;}
.bottom-btn-saju{flex:1;background:linear-gradient(135deg,#c8b87a,#a0945e);border:none;border-radius:10px;padding:14px 6px;text-align:center;color:#fff;font-size:14px;font-weight:bold;text-decoration:none;display:block;}
.bottom-btn-ai{flex:1;background:linear-gradient(135deg,#f0c4dc,#e8a0c4);border:none;border-radius:10px;padding:14px 6px;text-align:center;color:#2c3e7a;font-size:14px;font-weight:bold;text-decoration:none;display:block;}
label{color:var(--text)!important;font-size:15px!important;}
div[data-testid='stHorizontalBlock']{gap:4px!important;}
div[data-testid='column']{padding:0 2px!important;}
.tz-info-box{background:#f8f4e8;border:1px solid #d4c48a;border-radius:8px;padding:10px 12px;margin:6px 0;font-size:12px;color:var(--sub);line-height:1.6;}
.tz-info-box b{color:var(--text);}
.boundary-warn{background:#fff3e0;border:1px solid #f0a030;border-radius:8px;padding:10px 12px;margin:6px 0;font-size:13px;color:#8b4500;line-height:1.5;}
.tst-compare{background:#f0f4ff;border:1px solid #90a0d0;border-radius:8px;padding:10px 12px;margin:6px 0;font-size:12px;color:#2a3060;line-height:1.6;}
</style>
"""

def hanja_gan(g): return HANJA_GAN[CHEONGAN.index(g)]
def hanja_ji(j): return HANJA_JI[JIJI.index(j)]

def gan_card_html(g, size=52, fsize=26):
    bg=GAN_BG.get(g,"#888"); fg=gan_fg(g); hj=hanja_gan(g)
    return f'<div style="width:{size}px;height:{size}px;border-radius:8px;background:{bg};color:{fg};display:flex;align-items:center;justify-content:center;font-size:{fsize}px;font-weight:900;border:1px solid rgba(0,0,0,.15);">{hj}</div>'

def ji_card_html(j, size=52, fsize=26):
    bg=BR_BG.get(j,"#888"); fg=br_fg(j); hj=hanja_ji(j)
    return f'<div style="width:{size}px;height:{size}px;border-radius:8px;background:{bg};color:{fg};display:flex;align-items:center;justify-content:center;font-size:{fsize}px;font-weight:900;border:1px solid rgba(0,0,0,.15);">{hj}</div>'

def render_saju_table(fp, ilgan):
    yg,yj=fp['year'][0],fp['year'][1]; mg,mj=fp['month'][0],fp['month'][1]
    dg,dj=fp['day'][0],fp['day'][1]; sg,sj=fp['hour'][0],fp['hour'][1]
    cols=[(sg,sj,'시주'),(dg,dj,'일주'),(mg,mj,'월주'),(yg,yj,'년주')]
    ss_g=[six_for_stem(ilgan,sg),'일간',six_for_stem(ilgan,mg),six_for_stem(ilgan,yg)]
    ss_j=[six_for_branch(ilgan,sj),six_for_branch(ilgan,dj),six_for_branch(ilgan,mj),six_for_branch(ilgan,yj)]
    html='<div class="saju-wrap"><table class="saju-table"><thead><tr>'
    for g,j,lbl in cols: html+=f'<th>{lbl}</th>'
    html+='</tr><tr class="lb">'
    for i,(g,j,_) in enumerate(cols): html+=f'<td>{ss_g[i]}</td>'
    html+='</tr></thead><tbody><tr>'
    for g,j,_ in cols: html+=f'<td class="gcell">{gan_card_html(g)}</td>'
    html+='</tr><tr>'
    for g,j,_ in cols: html+=f'<td class="jcell">{ji_card_html(j)}</td>'
    html+='</tr><tr class="lb">'
    for i,(_,j,__) in enumerate(cols): html+=f'<td>{ss_j[i]}</td>'
    html+='</tr></tbody></table></div>'
    return html

def render_geok_card_html(card, show_detail=False):
    if not card: return ''
    icon_title = f'{card["icon"]} {card["card_title"]}'
    front = (
        '<div class="geok-card-front">'
        f'<div class="geok-card-title">{icon_title}</div>'
        f'<div class="geok-card-oneliner">{card["one_liner"]}</div>'
        '<div style="font-size:10px;color:#a0845e;margin-top:6px;text-align:right;">▼ 상세보기 클릭</div>'
        '</div>'
    )
    if not show_detail:
        return front
    strengths_html = ''.join([f'<span class="geok-tag">✦ {s}</span>' for s in card["strengths"]])
    tips_html = ''.join([f'<li style="margin-bottom:4px;">{t}</li>' for t in card["growth_tips"]])
    praise_html = ''.join([f'<span class="geok-tag" style="background:#e8f8e8;color:#2a6a2a;border-color:#6ab46a;">✧ {p}</span>' for p in card["praise_keywords"]])
    detail = (
        '<div class="geok-card-detail">'
        f'<div style="font-size:15px;font-weight:900;color:#8b4513;margin-bottom:8px;">{icon_title}</div>'
        f'<div style="font-size:12px;margin-bottom:10px;line-height:1.7;color:#3a2a14;">{card["story"]}</div>'
        '<div style="font-size:12px;font-weight:bold;color:#8b6914;margin-bottom:4px;">💪 강점</div>'
        f'<div style="margin-bottom:10px;">{strengths_html}</div>'
        '<div style="font-size:12px;font-weight:bold;color:#8b6914;margin-bottom:4px;">🌱 성장 팁</div>'
        f'<ul style="margin:0 0 10px;padding-left:18px;font-size:11px;color:#2c2416;">{tips_html}</ul>'
        '<div style="font-size:12px;font-weight:bold;color:#2a6a2a;margin-bottom:4px;">🎉 칭찬 키워드</div>'
        f'<div>{praise_html}</div>'
        '</div>'
    )
    return detail

def render_daeun_card(age, g, j, ilgan, active, btn_key, dy_year=0):
    bg_g=GAN_BG.get(g,"#888"); tc_g=gan_fg(g)
    bg_j=BR_BG.get(j,"#888"); tc_j=br_fg(j)
    hj_g=hanja_gan(g); hj_j=hanja_ji(j)
    bdr='2px solid #8b6914' if active else '1px solid #c8b87a'
    bg_card='#d4c48a' if active else '#e8e4d8'
    six_g=six_for_stem(ilgan,g); six_j=six_for_branch(ilgan,j)
    st.markdown(
        f'<div style="text-align:center;font-size:10px;color:#6b5a3e;margin-bottom:1px">{age}세</div>'
        f'<div style="display:flex;flex-direction:column;align-items:center;border:{bdr};border-radius:10px;background:{bg_card};padding:3px 2px;">'
        f'<div style="font-size:9px;color:#5a3e0a;margin-bottom:1px;white-space:nowrap">{six_g}</div>'
        f'<div style="width:30px;height:30px;border-radius:5px;background:{bg_g};color:{tc_g};display:flex;align-items:center;justify-content:center;font-size:16px;font-weight:900;margin-bottom:1px">{hj_g}</div>'
        f'<div style="width:30px;height:30px;border-radius:5px;background:{bg_j};color:{tc_j};display:flex;align-items:center;justify-content:center;font-size:16px;font-weight:900;margin-bottom:1px">{hj_j}</div>'
        f'<div style="font-size:9px;color:#5a3e0a;white-space:nowrap">{six_j}</div>'
        '</div>',
        unsafe_allow_html=True
    )
    return st.button(f'{dy_year}', key=btn_key, use_container_width=True)

def main():
    st.set_page_config(page_title='이박사 만세력', layout='centered', page_icon='🔮', initial_sidebar_state='collapsed')
    st.markdown(MOBILE_CSS, unsafe_allow_html=True)
    st.markdown('<meta name="viewport" content="width=device-width, initial-scale=1.0, user-scalable=yes, maximum-scale=5.0">', unsafe_allow_html=True)
    st.markdown('<div class="page-hdr">만 세 력</div>', unsafe_allow_html=True)
    for key,val in [('page','input'),('saju_data',None),('sel_daeun',0),('sel_seun',0),('sel_wolun',0),('show_geok_detail',False),('show_saju_interp',False)]:
        if key not in st.session_state: st.session_state[key]=val
    if st.session_state.page=='input': page_input()
    elif st.session_state.page=='saju': page_saju()
    elif st.session_state.page=='wolun': page_wolun()
    elif st.session_state.page=='ilun': page_ilun()

def page_input():
    now=datetime.now(LOCAL_TZ)
    st.markdown('<div class="sec-title">📅 출생 정보 입력</div>', unsafe_allow_html=True)
    c1,c2=st.columns(2)
    with c1: gender=st.radio('성별',['남','여'],horizontal=True)
    with c2: cal_type=st.radio('달력',['양력','음력','음력윤달'],horizontal=True)
    city = st.selectbox("출생지", list(city_options.keys()))
    longitude = city_options[city]

    apply_solar = st.checkbox("진태양시(경도) 보정 적용", value=True)
    show_tst = st.checkbox("🔬 정밀검증 모드 (진태양시 비교)", value=False)
    
    birth_str=st.text_input('생년월일 (YYYYMMDD)',value=st.session_state.get('_birth_str','19840202'),max_chars=8)
    birth_time=st.text_input('출생시각 (HHMM, 모르면 0000)',value=st.session_state.get('_birth_time','0000'),max_chars=4)
    is_leap = (cal_type == '음력윤달')
    if st.button('🔮 사주 보기'):
        try:
            bs=re.sub(r'\D','',birth_str); bt=re.sub(r'\D','',birth_time)
            y=int(bs[:4]); m=int(bs[4:6]); d=int(bs[6:8])
            hh=int(bt[:2]) if len(bt)>=2 else 0
            mm_t=int(bt[2:4]) if len(bt)==4 else 0
            base_date=date(y,m,d)
            if cal_type in ('음력','음력윤달') and HAS_LUNAR: base_date=lunar_to_solar(y,m,d,is_leap)
            dt_local=datetime.combine(base_date,time(hh,mm_t)).replace(tzinfo=LOCAL_TZ)
            if apply_solar:
                dt_solar = to_solar_time(dt_local, longitude)
            else:
                dt_solar = dt_local

            fp=four_pillars_from_solar(dt_solar)
            ilgan=fp['day'][0]

            # ★ 벽시계 절기 (표시용)
            jie12_wall = compute_jie_times_calc(dt_solar.year)
            jie24_wall = compute_jie24_times_calc(dt_solar.year)

            # ★ 진태양시 절기 (계산용)
            if apply_solar:
                jie12_solar = {k: to_solar_time(v, longitude) for k, v in jie12_wall.items()}
                jie24_solar = {k: to_solar_time(v, longitude) for k, v in jie24_wall.items()}
            else:
                jie12_solar = dict(jie12_wall)
                jie24_solar = dict(jie24_wall)

            year_gan=fp['year'][0]
            forward=(is_yang_stem(year_gan)==(gender=='남'))
            start_age=dayun_start_age(dt_solar,jie12_solar,forward)
            daeun=build_dayun_list(fp['m_gidx'],fp['m_bidx'],forward,start_age)
            seun_start=base_date.year
            seun=[]
            for i in range(100):
                sy=seun_start+i; off=(sy-4)%60
                seun.append((sy,CHEONGAN[off%10],JIJI[off%12]))

            pair=MONTH_TO_2TERMS[fp['month'][1]]
            def nearest_t(name):
                cands=[(abs((t-dt_solar).total_seconds()),t) for n,t in jie24_solar.items() if n==name]
                if not cands: return dt_solar
                cands.sort(); return cands[0][1]
            t1=nearest_t(pair[0]); t2=nearest_t(pair[1])
            day_from_jieqi=int((dt_solar-t1).total_seconds()//86400)
            day_from_jieqi=max(0,min(29,day_from_jieqi))
            geok,why=decide_geok(Inputs(
                day_stem=fp['day'][0],month_branch=fp['month'][1],month_stem=fp['month'][0],
                stems_visible=[fp['year'][0],fp['month'][0],fp['day'][0],fp['hour'][0]],
                branches_visible=[fp['year'][1],fp['month'][1],fp['day'][1],fp['hour'][1]],
                solar_dt=dt_solar,first_term_dt=t1,mid_term_dt=t2,day_from_jieqi=day_from_jieqi
            ))
            age_now=calc_age_on(base_date,now)
            sel_du=0
            for idx,item in enumerate(daeun):
                if item['start_age']<=age_now: sel_du=idx
            sel_su=min(age_now, 99)
            st.session_state['_birth_str']=birth_str
            st.session_state['_birth_time']=birth_time

            # ★ 표준시 라벨
            tz_lbl = tz_label_for_date(base_date)

            # ★ 보정값 상세
            corr_detail = calc_correction_detail(base_date, longitude)
            eot_min = equation_of_time_minutes(dt_local.astimezone(timezone.utc)) if apply_solar else 0

            # ★ 경계 경고
            boundary_warns = check_boundary_warning(dt_solar, jie24_solar, hour_branch_idx_2300(dt_solar))

            # ★ 진태양시 비교용 (정밀검증 모드)
            fp_tst = None
            dt_tst = None
            if show_tst and apply_solar:
                dt_tst = dt_solar  # 이미 진태양시
                fp_tst = fp
                # 벽시계 기준 사주도 계산
                fp_wall = four_pillars_from_solar(dt_local)
            else:
                fp_wall = fp

            st.session_state.saju_data={
                'birth':(base_date.year,base_date.month,base_date.day,hh,mm_t),
                'dt_solar':dt_solar,'dt_local':dt_local,
                'gender':gender,'fp':fp,'daeun':daeun,
                'seun':seun,'seun_start':seun_start,'geok':geok,'why':why,
                't1':t1,'t2':t2,'day_from_jieqi':day_from_jieqi,
                'ilgan':ilgan,'start_age':start_age,'forward':forward,
                'jie24_solar':jie24_solar,
                'jie24_wall':jie24_wall,
                'longitude': longitude,
                'apply_solar': apply_solar,
                'tz_label': tz_lbl,
                'corr_detail': corr_detail,
                'eot_min': eot_min,
                'boundary_warns': boundary_warns,
                'show_tst': show_tst,
                'fp_wall': fp_wall,
                'fp_tst': fp_tst,
                'dt_tst': dt_tst,
            }
            st.session_state.sel_daeun=sel_du
            st.session_state.sel_seun=sel_su
            st.session_state.sel_wolun=now.month-1
            st.session_state.show_geok_detail=False
            st.session_state.page='saju'
            st.rerun()
        except Exception as e: st.error(f'입력 오류: {e}')

def page_saju():
    data=st.session_state.saju_data
    if not data or 'fp' not in data: st.session_state.page='input'; st.rerun(); return
    now=datetime.now(LOCAL_TZ)
    fp=data['fp']; ilgan=data['ilgan']
    daeun=data['daeun']; seun=data['seun']
    geok=data['geok']; why=data['why']
    sel_du=st.session_state.sel_daeun
    birth_year=data['birth'][0]

    if st.button('← 입력으로'):
        st.session_state.page='input'; st.rerun()

    longitude = data.get('longitude', DEFAULT_LONGITUDE)
    apply_solar = data.get('apply_solar', True)

    if apply_solar:
        now_solar = to_solar_time(now, longitude)
    else:
        now_solar = now
    today_fp=four_pillars_from_solar(now_solar)
    yg,yj=today_fp['year'][0],today_fp['year'][1]
    dg,dj=today_fp['day'][0],today_fp['day'][1]
    mg,mj=today_fp['month'][0],today_fp['month'][1]
    hj_yg=hanja_gan(yg); hj_yj=hanja_ji(yj)
    hj_mg=hanja_gan(mg); hj_mj=hanja_ji(mj)
    hj_dg=hanja_gan(dg); hj_dj=hanja_ji(dj)
    st.markdown(f'<div class="today-banner">오늘 {now.strftime("%Y.%m.%d")} · {hj_yg}{hj_yj}년 {hj_mg}{hj_mj}월 {hj_dg}{hj_dj}일</div>', unsafe_allow_html=True)
    b=data['birth']; birth_display=f'{b[0]}년 {b[1]}월 {b[2]}일 {b[3]:02d}:{b[4]:02d}'
    st.markdown(f'<div style="text-align:center;font-size:11px;color:#8b6914;margin:-4px 0 6px;padding:2px 0;">입력 생년월일시 · 서기 {birth_display}</div>', unsafe_allow_html=True)

    st.markdown(render_saju_table(fp,ilgan), unsafe_allow_html=True)

    # ★ 표준시 라벨 + 경도 보정 정보
    tz_lbl = data.get('tz_label', '')
    calc_info = f"🔎 기준: {tz_lbl} · 경도 {longitude:.2f}°"
    if apply_solar:
        calc_info += " · 진태양시 보정 적용"

    st.markdown(
        f'<div style="text-align:center;font-size:10px;color:#6b5a3e;margin:-6px 0 4px;">{calc_info}</div>',
        unsafe_allow_html=True
    )
    month_ji=fp['month'][1]
    day_from=data['day_from_jieqi']
    du_dir='순행' if data['forward'] else '역행'
    du_age=data['start_age']

    saryeong_gan, saryeong_period = get_saryeong_gan(month_ji, day_from)
    saryeong_six = ten_god_for_stem(ilgan, saryeong_gan) if saryeong_gan else ''
    _jie24_s = data.get('jie24_solar') or {}
    dangryeong_item = get_dangryeong(month_ji, data['dt_solar'], _jie24_s)

    # ★ 절입일: 벽시계(당시 법정시)로 표시
    birth_date = date(data['birth'][0], data['birth'][1], data['birth'][2])
    prev_jeolip, next_jeolip = get_nearby_jeolip(data['dt_solar'])
    prev_str = f"{prev_jeolip[0]} {prev_jeolip[1].strftime('%Y.%m.%d %H:%M')}" if prev_jeolip else '-'
    next_str = f"{next_jeolip[0]} {next_jeolip[1].strftime('%Y.%m.%d %H:%M')}" if next_jeolip else '-'

    dr_desc = dangryeong_item["description"] if dangryeong_item else ""
    dr_mission = dangryeong_item["heaven_mission"] if dangryeong_item else "-"
    dr_period = dangryeong_item["period"] if dangryeong_item else "-"

    geok_box_html = (
        '<div class="geok-box">'
        f'<div class="geok-name">格 {geok} &nbsp;&nbsp;<span style="font-size:11px;color:var(--sub);font-weight:normal;">{why}</span>'
        f'&nbsp;&nbsp;·&nbsp;&nbsp;<span style="font-size:11px;color:var(--sub);">대운 {du_age}세 {du_dir}</span>'
        '</div>'
        '<div class="geok-why" style="margin-top:6px;padding-top:6px;border-top:1px solid rgba(200,184,122,.4);">'
        f'<b>사령</b>: {saryeong_gan}({saryeong_six}) · {saryeong_period} · {month_ji}월 절입+{day_from}일'
        f'<br><b>당령</b>: {dr_mission} · {dr_period}<br>{dr_desc}'
        f'<br><b>절입일</b>({tz_lbl}): 이전 {prev_str} / 이후 {next_str}'
        '</div>'
        '</div>'
    )
    st.markdown(geok_box_html, unsafe_allow_html=True)

    # ★ ① 보정값 상세 표시
    corr = data.get('corr_detail')
    eot = data.get('eot_min', 0)
    if corr:
        st.markdown(render_correction_html(corr, eot), unsafe_allow_html=True)

    # ★ ② 경계 경고 표시
    warns = data.get('boundary_warns', [])
    if warns:
        warn_html = '<div class="boundary-warn">' + '<br>'.join(warns) + '</div>'
        st.markdown(warn_html, unsafe_allow_html=True)

    # ★ ③ 진태양시 비교 (정밀검증 모드)
    if data.get('show_tst') and data.get('fp_tst'):
        dt_local = data.get('dt_local')
        dt_tst = data.get('dt_tst')
        fp_wall = data.get('fp_wall', fp)
        fp_tst = data.get('fp_tst', fp)
        if dt_local and dt_tst:
            st.markdown(render_tst_compare_html(dt_local, dt_tst, fp_wall, fp_tst), unsafe_allow_html=True)

    daeun_rev=list(reversed(daeun))
    cols_du=st.columns(len(daeun))
    for ci,col in enumerate(cols_du):
        real_idx=len(daeun)-1-ci
        item=daeun_rev[ci]
        age=item['start_age']
        g=CHEONGAN[item['g_idx']]; j=MONTH_JI[item['b_idx']]
        dy_year=birth_year+age
        with col:
            clicked=render_daeun_card(age,g,j,ilgan,real_idx==sel_du,f"du_{real_idx}",dy_year)
            if clicked:
                st.session_state.sel_daeun=real_idx
                birth_y=data['birth'][0]
                du_start_age=item['start_age']
                new_seun=[]
                for i in range(100):
                    sy=birth_y+i; off=(sy-4)%60
                    new_seun.append((sy,CHEONGAN[off%10],JIJI[off%12]))
                st.session_state.saju_data['seun']=new_seun
                st.session_state.sel_seun=du_start_age
                st.session_state.page='saju'
                st.rerun()

    sel_su=st.session_state.sel_seun
    seun=data["seun"]
    du_item=daeun[sel_du]
    du_start=du_item['start_age']
    birth_y=data['birth'][0]
    if sel_du==0: seun_age_start=0
    else: seun_age_start=du_start
    seun_age_end=du_start+9
    seun_range=[]
    for age_i in range(seun_age_start, seun_age_end+1):
        if age_i < len(seun):
            sy,sg,sj=seun[age_i]
            seun_range.append((age_i,sy,sg,sj))
    seun_range_disp=list(reversed(seun_range))

    seun_html='<div style="overflow-x:auto;-webkit-overflow-scrolling:touch;padding:4px 0 2px;">'
    seun_html+='<div style="display:inline-flex;flex-wrap:nowrap;gap:2px;padding:0 2px;">'
    for age_i,sy,sg,sj in seun_range_disp:
        bg_g=GAN_BG.get(sg,"#888"); tc_g=gan_fg(sg)
        bg_j=BR_BG.get(sj,"#888"); tc_j=br_fg(sj)
        hj_sg=hanja_gan(sg); hj_sj=hanja_ji(sj)
        six_g=six_for_stem(ilgan,sg); six_j=six_for_branch(ilgan,sj)
        active=(age_i==sel_su)
        bdr='2px solid #8b6914' if active else '1px solid #c8b87a'
        bg_card='#d4c48a' if active else '#e8e4d8'
        display_age = age_i + 1
        seun_html+=(
            f'<div style="display:flex;flex-direction:column;align-items:center;min-width:38px;border:{bdr};border-radius:8px;background:{bg_card};padding:3px 2px 2px;">'
            f'<div style="font-size:9px;color:#6b5a3e;margin-bottom:1px;white-space:nowrap">{sy}</div>'
            f'<div style="font-size:9px;color:#5a3e0a;margin-bottom:1px;white-space:nowrap">{six_g}</div>'
            f'<div style="width:30px;height:30px;border-radius:5px;background:{bg_g};color:{tc_g};display:flex;align-items:center;justify-content:center;font-size:16px;font-weight:900;">{hj_sg}</div>'
            f'<div style="width:30px;height:30px;border-radius:5px;background:{bg_j};color:{tc_j};display:flex;align-items:center;justify-content:center;font-size:16px;font-weight:900;margin-top:1px;">{hj_sj}</div>'
            f'<div style="font-size:9px;color:#5a3e0a;margin-top:1px;white-space:nowrap">{six_j}</div>'
            '</div>'
        )
    seun_html+='</div></div>'
    st.markdown(seun_html, unsafe_allow_html=True)

    n_btn=len(seun_range_disp)
    if n_btn>0:
        cols_su=st.columns(n_btn)
        for ci,(age_i,sy,sg,sj) in enumerate(seun_range_disp):
            display_age = age_i + 1
            with cols_su[ci]:
                if st.button(f'{display_age}세', key=f'su_{age_i}', use_container_width=True):
                    st.session_state.sel_seun=age_i
                    st.session_state.sel_wolun=0
                    st.session_state.page='wolun'
                    st.rerun()

    gpt_url='https://chatgpt.com/g/g-68d90b2d8f448191b87fb7511fa8f80a-rua-myeongrisajusangdamsa'
    bottom_html = (
        '<div class="bottom-btns">'
        f'<a href="{gpt_url}" target="_blank" class="bottom-btn-ai">🤖 AI 챗봇 무료상담</a>'
        '</div>'
        '<div style="text-align:center;margin-top:6px;font-size:11px;">'
        '<a href="https://www.youtube.com/@psycologysalon" target="_blank" style="color:#8b6914;text-decoration:none;">🎥 2025 상담학박사 루아코치 유튜브</a>'
        '</div>'
    )
    st.markdown(bottom_html, unsafe_allow_html=True)
    show_interp = st.session_state.get('show_saju_interp', False)
    btn_label = '▲ 내 사주 해석 닫기' if show_interp else '📊 내 사주 해석 보기'
    if st.button(btn_label, key='show_saju_interp_btn', use_container_width=True):
        st.session_state['show_saju_interp'] = not show_interp
        st.rerun()
    if show_interp:
        geok_card2 = find_geok_card(geok)
        if geok_card2:
            st.markdown(render_geok_card_html(geok_card2, show_detail=True), unsafe_allow_html=True)

def page_wolun():
    data=st.session_state.saju_data
    if not data or 'fp' not in data: st.session_state.page='input'; st.rerun(); return
    now=datetime.now(LOCAL_TZ)
    ilgan=data['ilgan']
    seun=data["seun"]
    sel_su=st.session_state.sel_seun
    sy,sg,sj=seun[sel_su]
    if st.button('← 사주로'): st.session_state.page='saju'; st.rerun()
    hj_sg=hanja_gan(sg); hj_sj=hanja_ji(sj)
    display_age = sel_su + 1
    st.markdown(f'<div class="sel-info">{sy}년 {display_age}세 {hj_sg}{hj_sj} 월운 ({six_for_stem(ilgan,sg)}/{six_for_branch(ilgan,sj)})</div>', unsafe_allow_html=True)

    wolun=calc_wolun_accurate(sy)
    sel_wu=st.session_state.sel_wolun
    wolun_rev=list(reversed(wolun))
    MONTH_KR=['1월','2월','3월','4월','5월','6월','7월','8월','9월','10월','11월','12월']
    for row_start in [6,0]:
        row_items=wolun_rev[row_start:row_start+6]
        cols=st.columns(len(row_items))
        for ci,col in enumerate(cols):
            if ci>=len(row_items): break
            real_wu=11-(row_start+ci)
            wm=row_items[ci]["month"]
            wg=row_items[ci]["gan"]; wj=row_items[ci]["ji"]
            with col:
                active=(real_wu==sel_wu)
                bg_g=GAN_BG.get(wg,"#888"); tc_g=gan_fg(wg)
                bg_j=BR_BG.get(wj,"#888"); tc_j=br_fg(wj)
                hj_wg=hanja_gan(wg); hj_wj=hanja_ji(wj)
                bdr='2px solid #8b6914' if active else '1px solid #c8b87a'
                bg_card='#d4c48a' if active else '#e8e4d8'
                six_g=six_for_stem(ilgan,wg); six_j=six_for_branch(ilgan,wj)
                st.markdown(
                    f'<div style="text-align:center;font-size:10px;color:#6b5a3e;margin-bottom:1px">{MONTH_KR[wm-1]}</div>'
                    f'<div style="display:flex;flex-direction:column;align-items:center;border:{bdr};border-radius:10px;background:{bg_card};padding:2px 2px;">'
                    f'<div style="font-size:9px;color:#5a3e0a;margin-bottom:1px;white-space:nowrap">{six_g}</div>'
                    f'<div style="width:34px;height:34px;border-radius:6px;background:{bg_g};color:{tc_g};display:flex;align-items:center;justify-content:center;font-size:18px;font-weight:900;margin-bottom:1px">{hj_wg}</div>'
                    f'<div style="width:34px;height:34px;border-radius:6px;background:{bg_j};color:{tc_j};display:flex;align-items:center;justify-content:center;font-size:18px;font-weight:900;margin-bottom:1px">{hj_wj}</div>'
                    f'<div style="font-size:9px;color:#5a3e0a;white-space:nowrap">{six_j}</div>'
                    '</div>',
                    unsafe_allow_html=True
                )
                if st.button(f'{wm}월',key=f'wu_{real_wu}',use_container_width=True):
                    st.session_state.sel_wolun=real_wu
                    st.session_state.page='ilun'
                    st.rerun()

    gpt_url='https://chatgpt.com/g/g-68d90b2d8f448191b87fb7511fa8f80a-rua-myeongrisajusangdamsa'
    bottom_html = (
        '<div class="bottom-btns">'
        '<a href="https://open.kakao.com/o/sWJUYGDh" target="_blank" class="bottom-btn-saju" style="text-align:center;padding:12px 6px;text-decoration:none;">💬 이박사 오픈카카오톡</a>'
        f'<a href="{gpt_url}" target="_blank" class="bottom-btn-ai">🤖 AI 챗봇 무료상담</a>'
        '</div>'
        '<div style="text-align:center;margin-top:6px;font-size:11px;">'
        '<a href="https://www.youtube.com/@psycologysalon" target="_blank" style="color:#8b6914;text-decoration:none;">🎥 2025 상담학박사 루아코치 유튜브</a>'
        '</div>'
    )
    st.markdown(bottom_html, unsafe_allow_html=True)

def page_ilun():
    data=st.session_state.saju_data
    if not data or 'fp' not in data: st.session_state.page='input'; st.rerun(); return
    now=datetime.now(LOCAL_TZ)
    longitude = data.get('longitude', DEFAULT_LONGITUDE)
    apply_solar = data.get('apply_solar', True)
    ilgan=data['ilgan']
    seun=data["seun"]
    sel_su=st.session_state.sel_seun
    sy,sg,sj=seun[sel_su]
    sel_wu=st.session_state.sel_wolun
    wolun=calc_wolun_accurate(sy)
    wm_data=wolun[sel_wu]
    wm=wm_data["month"]; wg=wm_data["gan"]; wj=wm_data["ji"]
    if st.button('← 월운으로'): st.session_state.page='wolun'; st.rerun()
    hj_wg=hanja_gan(wg); hj_wj=hanja_ji(wj)
    hj_sg=hanja_gan(sg); hj_sj=hanja_ji(sj)
    display_age = sel_su + 1
    st.markdown(f'<div class="sel-info">{sy}년({display_age}세) {wm}월 ({hj_wg}{hj_wj}) 일운</div>', unsafe_allow_html=True)

    _,days_in_month=cal_mod.monthrange(sy,wm)
    first_weekday,_=cal_mod.monthrange(sy,wm)
    first_wd=(first_weekday+1)%7

    # ★ 절기: 벽시계(당시 법정시)로 표시 — to_solar_time 적용하지 않음
    jie24_this = compute_jie24_times_calc(sy)
    jie24_wall_ilun = jie24_this  # 이미 벽시계 시간

    # 이 달의 절기 목록 (날짜 -> 절기명,시각)
    month_jie_map={}
    for jname,jt in jie24_wall_ilun.items():
        if jt.year==sy and jt.month==wm:
            month_jie_map[jt.day]=(jname,jt)

    # ★ 절기 표시에 표준시 라벨 추가
    sample_date = date(sy, wm, 15)
    ilun_tz_lbl = tz_label_for_date(sample_date)

    # 이 달의 절기 2개 텍스트 (상단 표시용)
    month_terms_list=sorted(month_jie_map.items())
    month_terms_str=' / '.join([f"{v[0]} ({v[1].strftime('%d일 %H:%M')})" for k,v in month_terms_list])

    # 음력 변환
    def solar_to_lunar_str(y,m,d):
        if not HAS_LUNAR: return ''
        try:
            c=KoreanLunarCalendar()
            c.setSolarDate(y,m,d)
            lm=c.lunarMonth; ld=c.lunarDay; is_l=c.isIntercalation
            leap_str='윤' if is_l else ''
            return f'{leap_str}{lm}/{ld}'
        except: return ''
    day_items=[]
    for d in range(1, days_in_month+1):
        dt_local=datetime(sy,wm,d,12,0,tzinfo=LOCAL_TZ)

        if apply_solar:
            dt_solar = to_solar_time(dt_local, longitude)
        else:
            dt_solar = dt_local
        dj,dc,djidx=day_ganji_solar(dt_solar)
        g,j=dj[0],dj[1]
        sg_six=six_for_stem(ilgan,g); sj_six=six_for_branch(ilgan,j)
        lunar_str=solar_to_lunar_str(sy,wm,d)
        jie_info=month_jie_map.get(d,None)
        jie_str=jie_info[0] if jie_info else ''
        day_items.append({'day':d,'gan':g,'ji':j,'sg_six':sg_six,'sj_six':sj_six,'lunar':lunar_str,'jie':jie_str})

    html='<div class="cal-wrap">'
    html+=f'<div class="cal-header">{sy}년({hj_sg}{hj_sj}) {wm}월({hj_wg}{hj_wj})</div>'
    if month_terms_str:
        html+=f'<div style="background:#f5eed8;padding:4px 8px;font-size:11px;color:#7a5a1a;text-align:center;border-bottom:1px solid #c8b87a;">🌿 절기({ilun_tz_lbl}): {month_terms_str}</div>'
    html+='<table class="cal-table"><thead><tr>'
    for dn in ['일','월','화','수','목','금','토']: html+=f'<th>{dn}</th>'
    html+='</tr></thead><tbody><tr>'
    for _ in range(first_wd): html+='<td class="empty"></td>'
    col_pos=first_wd
    for item in day_items:
        if col_pos==7: html+='</tr><tr>'; col_pos=0
        d_num=item["day"]; dow=(first_wd+d_num-1)%7
        is_today=(sy==now.year and wm==now.month and d_num==now.day)
        cls='today-cell' if is_today else ''
        if dow==0: cls+=' sun'
        elif dow==6: cls+=' sat'
        hj_dg=hanja_gan(item["gan"]); hj_dj=hanja_ji(item["ji"])
        sg6=item["sg_six"]; sj6=item["sj_six"]
        lunar6=item.get("lunar",""); jie6=item.get("jie","")
        jie_html=f'<div style="font-size:8px;color:#b06000;font-weight:bold;">{jie6}</div>' if jie6 else ''
        lunar_html=f'<div style="font-size:8px;color:#5a5a8a;">{lunar6}</div>' if lunar6 else ''
        html+=f'<td class="{cls.strip()}">{jie_html}<div class="dn">{d_num}</div>{lunar_html}<div style="font-size:9px;color:#888;">{sg6}</div><div style="font-size:14px;font-weight:bold;">{hj_dg}</div><div style="font-size:14px;font-weight:bold;">{hj_dj}</div><div style="font-size:9px;color:#888;">{sj6}</div></td>'
        col_pos+=1
    while col_pos%7!=0 and col_pos>0: html+='<td class="empty"></td>'; col_pos+=1
    html+='</tr></tbody></table></div>'
    st.markdown(html,unsafe_allow_html=True)

    gpt_url='https://chatgpt.com/g/g-68d90b2d8f448191b87fb7511fa8f80a-rua-myeongrisajusangdamsa'
    bottom_html = (
        '<div class="bottom-btns">'
        '<div class="bottom-btn-saju" style="text-align:center;padding:12px 6px;">📊 내 사주 해석 보기</div>'
        f'<a href="{gpt_url}" target="_blank" class="bottom-btn-ai">🤖 AI 챗봇 무료상담</a>'
        '</div>'
    )
    st.markdown(bottom_html, unsafe_allow_html=True)

if __name__=='__main__':
    main()
