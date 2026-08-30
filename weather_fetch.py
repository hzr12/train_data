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
import os
import json
import requests
import time

GEO_URL = 'https://geocoding-api.open-meteo.com/v1/search'
ARCHIVE_URL = 'https://archive-api.open-meteo.com/v1/archive'

# 地理编码结果持久化（自动生成，非手写坐标表）：CI 第二次起零网络请求
_GEO_CACHE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'geo_cache.json')

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

def _load_geo_cache():
    try:
        with open(_GEO_CACHE_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return {}


def _save_geo_cache():
    try:
        with open(_GEO_CACHE_FILE, 'w', encoding='utf-8') as f:
            json.dump(_GEO_CACHE, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


# 进程内 + 持久化地理编码缓存，避免同一城市重复请求
_GEO_CACHE = _load_geo_cache()
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
    _save_geo_cache()
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
            result = {
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
            result = _reconcile(result)
            _ARCHIVE_CACHE[cache_key] = result
            return result
        except Exception as e:
            if attempt == retries - 1:
                print(f"  [失败] {station_name} {date_str}: {e}")
                return None
            time.sleep(1.5 * (attempt + 1))
    return None


def _build_weather(d, idx):
    """从 archive 的 daily 响应里取第 idx 天的天气 dict（idx 用于多坐标/区间批量的按位置索引）。"""
    code = int(d['weather_code'][idx])
    w = {
        '当日最高温': _to_float(d['temperature_2m_max'][idx]),
        '当日最低温': _to_float(d['temperature_2m_min'][idx]),
        '当日降水量': _to_float(d['precipitation_sum'][idx]),
        '当日降雨量': _to_float(d['rain_sum'][idx]),
        '当日降雪量': _to_float(d['snowfall_sum'][idx]),
        '降水小时数': _to_float(d['precipitation_hours'][idx]),
        '最大风速': _to_float(d['wind_speed_10m_max'][idx]),
        '最大阵风': _to_float(d['wind_gusts_10m_max'][idx]),
        '平均云量': _to_float(d['cloud_cover_mean'][idx]),
        '平均相对湿度': _to_float(d['relative_humidity_2m_mean'][idx]),
        '天气代码': code,
        '天气情况': WMO_WEATHER_CN.get(code, f'未知({code})'),
    }
    return _reconcile(w)


def _city_to_coord(city_name):
    """城市名 -> (lat, lon)，统一走带缓存的地理编码。"""
    return _geocode(city_name)


def fetch_day_weather(cities, date_str, retries=3):
    """一次性取回【多城市同一天】的天气，写入 _ARCHIVE_CACHE，使逐行查询命中缓存。

    用逗号拼接多组经纬度，单次 archive 请求完成（Open-Meteo 多坐标一次返回数组），
    把 CI 的天气请求从“城市数级”压到 1 次，规避 GitHub Runner 共享 IP 被限流。
    cities: 城市名列表（已去车站后缀，如 '郑州'/'武汉'）。
    """
    coords = []
    valid = []
    for c in cities:
        coord = _city_to_coord(c)
        if coord is None:
            continue
        coords.append(coord)
        valid.append(c)
    if not coords:
        return
    lats = ','.join(str(x[0]) for x in coords)
    lons = ','.join(str(x[1]) for x in coords)
    for attempt in range(retries):
        try:
            resp = requests.get(ARCHIVE_URL, params={
                'latitude': lats, 'longitude': lons,
                'start_date': date_str, 'end_date': date_str,
                'daily': ','.join(_DAILY_VARS),
                'timezone': 'Asia/Shanghai',
            }, timeout=45)
            resp.raise_for_status()
            data = resp.json()
            items = data if isinstance(data, list) else [data]
            for i, c in enumerate(valid):
                d = items[i]['daily']
                _ARCHIVE_CACHE[(c, date_str)] = _build_weather(d, 0)
            return
        except Exception as e:
            if attempt == retries - 1:
                print(f"  [失败] 批量天气 {date_str}: {e}")
                return
            time.sleep(2.0 * (attempt + 1))


def fetch_city_range(city_name, date_list, retries=3):
    """一次性取回【单城市多天】的天气，写入 _ARCHIVE_CACHE（用于离线全量回填）。

    对 date_list 取最小~最大日期发起一次区间 archive 请求，按天索引写缓存，
    把“城市×天数”次请求压到“城市数”次。
    """
    coord = _city_to_coord(city_name)
    if coord is None or not date_list:
        return
    ds = sorted(date_list)
    for attempt in range(retries):
        try:
            resp = requests.get(ARCHIVE_URL, params={
                'latitude': coord[0], 'longitude': coord[1],
                'start_date': ds[0], 'end_date': ds[-1],
                'daily': ','.join(_DAILY_VARS),
                'timezone': 'Asia/Shanghai',
            }, timeout=60)
            resp.raise_for_status()
            d = resp.json()['daily']
            times = d.get('time', [])
            for j, t in enumerate(times):
                if t in date_list:
                    _ARCHIVE_CACHE[(city_name, t)] = _build_weather(d, j)
            return
        except Exception as e:
            if attempt == retries - 1:
                print(f"  [失败] 区间天气 {city_name} {ds[0]}~{ds[-1]}: {e}")
                return
            time.sleep(2.0 * (attempt + 1))


# 天气代码语义分组（WMO）
_LIQUID_PRECIP = set(range(51, 68)) | {80, 81, 82}   # 毛毛雨/雨/冻雨/阵雨
_SNOW_PRECIP = {71, 73, 75, 77, 85, 86}              # 雪/阵雪


def _reconcile(w):
    """对齐“天气现象”与“降水量”的口径。

    Open-Meteo 的 daily.weather_code 是“当天出现最多的小时代码”（按现象统计），
    而 rain_sum 是各小时液态降水之和。微量/未完结日期会出现“显示毛毛雨但
    rain_sum=0.0”的矛盾。这里用 precipitation_sum 兜底，使降雨/降雪量不丢量。
    """
    code = w['天气代码']
    if code in _LIQUID_PRECIP and w['当日降雨量'] == 0.0 and w['当日降水量'] > 0.0:
        w['当日降雨量'] = max(w['当日降水量'] - w['当日降雪量'], 0.0)
    if code in _SNOW_PRECIP and w['当日降雪量'] == 0.0 and w['当日降水量'] > 0.0:
        w['当日降雪量'] = max(w['当日降水量'] - w['当日降雨量'], 0.0)
    return w


def empty_weather():
    """查询失败时的空天气占位（避免 KeyError）。"""
    return {c: '' for c in WEATHER_COLUMNS}
