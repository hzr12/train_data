"""给已有的列车延误 CSV 补充真实历史天气数据。

- 读取 datasets/ 与 dataset/ 下所有 CSV；
- 对每一行有“车站名 + 到达日期”的记录，按其到达日期查询 Open-Meteo 历史天气；
- 用 12 个天气列覆盖/补全（旧 CSV 仅有 3 个天气列且为错误值）；
- 列顺序统一为：基础 8 列 + 12 个天气列（20 列，与新采集代码一致）。

天气获取逻辑见 weather_fetch.py（动态地理编码，无需经纬度表）。
"""
import os
import glob
import pandas as pd

from weather_fetch import fetch_station_weather, empty_weather, WEATHER_COLUMNS

BASE_COLUMNS = ['车次ID', '车站名', '到达日期', '到达时间',
                '出发日期', '出发时间', '延误分钟', '距离']
DATA_DIRS = ['datasets', 'dataset']


def _norm_date(v):
    """把日期整理成 YYYY-MM-DD；无法识别返回 ''。"""
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return ''
    s = str(v).strip().replace('/', '-')
    if len(s) == 8 and s.isdigit():
        s = f"{s[:4]}-{s[4:6]}-{s[6:8]}"
    return s


def backfill_file(path):
    df = pd.read_csv(path)
    # 确保 12 个天气列存在（旧文件只有 3 个）；统一为 object 类型以便混合赋值
    for c in WEATHER_COLUMNS:
        if c not in df.columns:
            df[c] = ''
        else:
            df[c] = df[c].astype(object)

    updated = 0
    weather_rows = []  # 每行的天气 dict（与 df 行序对应）
    for idx, row in df.iterrows():
        station = row.get('车站名')
        if station is None or pd.isna(station) or str(station).strip() == '':
            weather_rows.append(None)  # 空白分隔行
            continue
        station = str(station).strip()
        date_str = _norm_date(row.get('到达日期')) or _norm_date(row.get('出发日期'))
        if not date_str:
            weather_rows.append(None)
            continue
        # 全量重填：每行都按到达日期重新查询真实历史天气，覆盖旧的错误/假数据
        w = fetch_station_weather(station, date_str) or empty_weather()
        weather_rows.append(w)
        updated += 1

    # 用 object 类型的 Series 整体覆盖天气列，规避 pyarrow string 列无法赋数值的问题
    for c in WEATHER_COLUMNS:
        df[c] = pd.Series(
            [(wr.get(c, '') if wr is not None else '') for wr in weather_rows],
            dtype=object, index=df.index,
        )

    # 统一列顺序：基础 8 列 + 天气 12 列
    df = df[BASE_COLUMNS + WEATHER_COLUMNS]
    df.to_csv(path, index=False, encoding='utf-8')
    return updated


def main():
    total = 0
    for d in DATA_DIRS:
        if not os.path.isdir(d):
            continue
        for path in sorted(glob.glob(os.path.join(d, '*.csv'))):
            n = backfill_file(path)
            total += n
            print(f"  {path}: 更新 {n} 行")
    print(f"完成，共更新 {total} 行天气数据。")


if __name__ == '__main__':
    main()
