"""天气数据采集模块（不依赖硬编码的车站经纬度表）。

方案：
  1. 用 Open-Meteo 免费「地理编码 API」把车站城市名动态转成经纬度；
  2. 用 Open-Meteo 免费「历史天气档案 API」查询该经纬度的真实历史天气。

相比原 wttr.in 方案修复了三个关键问题：
  1. 支持历史日期回溯（date_str 真正生效）；旧代码忽略日期参数，
     拿到的是采集当天的实时天气，对历史数据构成“未来信息泄漏”。
  2. 当日最高/最低温是真实的当日极值；旧代码两者相同（都存了实时温度）。
  3. 新增天气代码、降雨量、降雪量、风速、云量、湿度等特征，并给出中文天气情况。

两个 API 均免费、无需 API Key，且无需维护车站经纬度表。
"""
import re
import requests
import time

GEO_URL = 'https://geocoding-api.open-meteo.com/v1/search'
ARCHIVE_URL = 'https://archive-api.open-meteo.com/v1/archive'

# WMO 天气代码 -> 中文天气情况
WMO_WEATHER_CN = {
    0: '晴', 1: '晴间多云', 2: '多云', 3: '阴',
    45: '雾', 48: '雾凇',
    51: '小毛毛雨', 53: '毛毛雨', 55: '浓毛毛雨',
    56: '冻毛毛雨', 57: '浓冻毛毛雨',
    61: '小雨', 63: '中雨', 65: '大雨',
    66: '冻雨', 67: '强冻雨',
    71: '小雪', 73: '中雪', 75: '大雪', 77: '米雪',
    80: '阵雨', 81: '强阵雨', 82: '暴雨',
    85: '阵雪', 86: '强阵雪',
    95: '雷暴', 96: '雷暴伴冰雹', 99: '强雷暴伴冰雹',
}

# 输出列顺序（旧 CSV 的列名保留在前，便于下游兼容）
WEATHER_COLUMNS = [
    '当日最高温', '当日最低温', '当日降水量', '当日降雨量', '当日降雪量',
    '降水小时数', '最大风速', '最大阵风', '平均云量', '平均相对湿度',
    '天气代码', '天气情况',
]

_DAILY_VARS = [
    'weather_code', 'temperature_2m_max', 'temperature_2m_min',
    'precipitation_sum', 'rain_sum', 'snowfall_sum', 'precipitation_hours',
    'wind_speed_10m_max', 'wind_gusts_10m_max', 'cloud_cover_mean',
    'relative_humidity_2m_mean',
]

# 进程内地理编码缓存，避免同一城市重复请求
_GEO_CACHE = {}
# 进程内历史天气缓存，键为 (城市名, 日期)，避免同一城市同日期重复请求
_ARCHIVE_CACHE = {}


def _to_float(v):
    try:
        if v is None:
            return 0.0
        return float(v)
    except (TypeError, ValueError):
        return 0.0


# Open-Meteo 地理编码对部分中国城市的中文名查不到（即使加“市”也查不到），
# 但用拼音可查到。这里仅对“中文与加市都失败”的城市做拼音兜底（name->pinyin 提示，非经纬度表）。
PINYIN_FALLBACK = {
    '商丘': 'Shangqiu', '驻马店': 'Zhumadian', '汉中': 'Hanzhong',
    '运城': 'Yuncheng', '淮南': 'Huainan', '湖州': 'Huzhou',
    '晋中': 'Jinzhong', '连云港': 'Lianyungang', '宿迁': 'Suqian',
    '咸宁': 'Xianning', '百色': 'Baise', '河池': 'Hechi',
}


def _geocode_query(name):
    """用单个查询词请求地理编码，命中返回 (lat, lon)，否则 None。

    仅在网络/限流异常时重试；明确“查无结果”直接返回 None（重试无意义）。
    """
    for attempt in range(3):
        try:
            resp = requests.get(GEO_URL, params={
                'name': name,
                'count': 1,
                'language': 'zh',
            }, timeout=15)
            resp.raise_for_status()
            results = resp.json().get('results') or []
            if results:
                r = results[0]
                return (float(r['latitude']), float(r['longitude']))
            return None
        except Exception:
            if attempt < 2:
                time.sleep(1.0 * (attempt + 1))
    return None


def _geocode(city_name):
    """城市名 -> (lat, lon)，带进程内缓存；找不到返回 None。

    查询回退链：原名 -> 原名+“市” -> 拼音（PINYIN_FALLBACK）。
    """
    if city_name in _GEO_CACHE:
        return _GEO_CACHE[city_name]
    candidates = [city_name, city_name + '市']
    py = PINYIN_FALLBACK.get(city_name)
    if py:
        candidates.append(py)
    coord = None
    for cand in candidates:
        coord = _geocode_query(cand)
        if coord is not None:
            break
    if coord is None:
        print(f"  [警告] 地理编码未找到: {city_name}")
    _GEO_CACHE[city_name] = coord
    return coord


def fetch_station_weather(station_name, date_str, retries=3):
    """获取指定车站、指定（到达）日期的真实历史天气。

    station_name: 车站名，如 '北京西' / '郑州东' / '武汉'
    date_str: 'YYYY-MM-DD'，应使用列车到达该站的实际日期（跨天车次尤其重要）
    返回含 WEATHER_COLUMNS 键的 dict；查询失败返回 None。
    """
    # 与原逻辑一致：补上“站”字再用正则提取城市名（去掉方位词 + 站）
    city_name = re.sub(r'[东西南北]?站', '', station_name + '站')
    # 同城市同日期直接返回缓存
    cache_key = (city_name, date_str)
    if cache_key in _ARCHIVE_CACHE:
        return _ARCHIVE_CACHE[cache_key]
    coord = _geocode(city_name)
    if coord is None:
        return None
    lat, lon = coord
    for attempt in range(retries):
        try:
            resp = requests.get(ARCHIVE_URL, params={
                'latitude': lat,
                'longitude': lon,
                'start_date': date_str,
                'end_date': date_str,
                'daily': ','.join(_DAILY_VARS),
                'timezone': 'Asia/Shanghai',   # daily 聚合变量要求显式 timezone
            }, timeout=20)
            resp.raise_for_status()
            d = resp.json()['daily']
            code = int(d['weather_code'][0])
            time.sleep(0.12)   # 限速保护
            return {
                '当日最高温': _to_float(d['temperature_2m_max'][0]),
                '当日最低温': _to_float(d['temperature_2m_min'][0]),
                '当日降水量': _to_float(d['precipitation_sum'][0]),
                '当日降雨量': _to_float(d['rain_sum'][0]),
                '当日降雪量': _to_float(d['snowfall_sum'][0]),
                '降水小时数': _to_float(d['precipitation_hours'][0]),
                '最大风速': _to_float(d['wind_speed_10m_max'][0]),
                '最大阵风': _to_float(d['wind_gusts_10m_max'][0]),
                '平均云量': _to_float(d['cloud_cover_mean'][0]),
                '平均相对湿度': _to_float(d['relative_humidity_2m_mean'][0]),
                '天气代码': code,
                '天气情况': WMO_WEATHER_CN.get(code, f'未知({code})'),
            }
            _ARCHIVE_CACHE[cache_key] = result
            return result
        except Exception as e:
            if attempt == retries - 1:
                print(f"  [失败] {station_name} {date_str}: {e}")
                return None
            time.sleep(1.5 * (attempt + 1))
    return None


def empty_weather():
    """查询失败时的空天气占位（避免 KeyError）。"""
    return {c: '' for c in WEATHER_COLUMNS}
