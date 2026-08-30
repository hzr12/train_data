"""抓取全国省/直辖市 + 其下辖市的经纬度，保存为 city_coords.json。

数据源：DataV GeoAtlas（阿里云），对中国地名覆盖率与准确度都远优于 Open-Meteo 地理编码，
且自带坐标，可作为 weather_fetch 的首选地理编码表，使 CI 完全不请求地理编码 API。

- center 字段为 [经度, 纬度]，这里统一转换为 [纬度, 经度] 存储以匹配 Open-Meteo 入参；
- 每个地名同时写入“原名”与“去掉末尾‘市’的别名”（如 郑州市->郑州），
  因为 weather_fetch 由车站名提取出的城市名不带“市”字。
"""
import os
import time
import json
import requests

BASE = 'https://geo.datav.aliyun.com/areas_v3/bound'
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'city_coords.json')

coords = {}  # 名称 -> [纬度, 经度]


def _add(name, center):
    if not name:
        return
    if not center or len(center) < 2:
        return
    lat, lon = center[1], center[0]
    entry = [round(lat, 5), round(lon, 5)]
    if name not in coords:
        coords[name] = entry
    # 去末尾“市”的别名
    if name.endswith('市'):
        short = name[:-1]
        if short and short not in coords:
            coords[short] = entry


def _fetch(adcode):
    for attempt in range(3):
        try:
            r = requests.get(f'{BASE}/{adcode}_full.json', timeout=30)
            r.raise_for_status()
            return r.json()
        except Exception as e:
            if attempt == 2:
                print(f'  [失败] 拉取 {adcode}: {e}')
                return None
            time.sleep(1.0 * (attempt + 1))
    return None


def main():
    # 1) 全国省级（含直辖市、省、自治区）
    cn = _fetch('100000')
    if not cn:
        print('无法获取全国数据')
        return
    for f in cn.get('features', []):
        p = f.get('properties', {})
        _add(p.get('name'), p.get('center') or p.get('centroid'))
        # 2) 该省级下辖市（地级市 / 直辖市的区 / 自治州等）
        adcode = p.get('adcode')
        sub = _fetch(adcode)
        if sub:
            for cf in sub.get('features', []):
                cp = cf.get('properties', {})
                _add(cp.get('name'), cp.get('center') or cp.get('centroid'))
        time.sleep(0.03)

    with open(OUT, 'w', encoding='utf-8') as fp:
        json.dump(coords, fp, ensure_ascii=False, indent=2)
    print(f'已保存 {len(coords)} 条地名坐标到 {OUT}')


if __name__ == '__main__':
    main()
