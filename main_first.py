import requests
import datetime
import time
import re
import pandas as pd

from weather_fetch import fetch_station_weather, fetch_day_weather, empty_weather

trainNumbers = ['G339']
result_lists = []
headers = {
    'Host': 'sharyou.moefactory.com',
    'Origin': 'https://sharyou.moefactory.com',
    'Referer': 'https://sharyou.moefactory.com',
    'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36 Edg/150.0.0.0'
}
url = 'https://sharyou.moefactory.com/api/trainNumber/query'
time_url = 'https://sharyou.moefactory.com/api/trainDetails/queryTrainDelayDetails'
summary_url = 'https://sharyou.moefactory.com/api/stationSummary/query'
def get_raw_date(i=1):
    target_date = datetime.date.today() - datetime.timedelta(days=i)
    return int(target_date.strftime("%Y%m%d"))
def format_date(date):
    date_str = str(date)
    date_obj = datetime.datetime.strptime(date_str, "%Y%m%d") #将YYYYMMDD格式转换为YYYY-MM-DD
    formatted_date = date_obj.strftime("%Y-%m-%d")
    return formatted_date

def get_result_data(num_json_data, time_json_data):
    for i in range(num_json_data):
        station_name = time_json_data['data'][i]['stationName']
        if i == 0:
            result = {
                '车次ID': time_json_data['data'][i]['trainNumber'],
                '车站名': station_name,
                '到达日期': time_json_data['data'][i]['departureDate'],
                '到达时间': time_json_data['data'][i]['departureTime'],
                '出发日期': time_json_data['data'][i]['departureDate'],
                '出发时间': time_json_data['data'][i]['departureTime'],
                '延误分钟': time_json_data['data'][i]['delayMinutes'],
                '距离': time_json_data['data'][i]['distance']
            }
        else:
            result = {
                '车次ID': time_json_data['data'][i]['trainNumber'],
                '车站名': station_name,
                '到达日期': time_json_data['data'][i]['arrivalDate'],
                '到达时间': time_json_data['data'][i]['arrivalTime'],
                '出发日期' : time_json_data['data'][i]['departureDate'],
                '出发时间' : time_json_data['data'][i]['departureTime'],
                '延误分钟' : time_json_data['data'][i]['delayMinutes'],
                '距离': time_json_data['data'][i]['distance']
            }

        # 天气延迟到循环结束后按“城市+日期”批量预取（见文末），避免逐行重复请求
        result_lists.append(result)

def get_data_json(url, headers, data):
    response = requests.post(url=url, headers=headers,data=data)
    json_data = response.json()
    return json_data

def get_telegramCode(stationName):
    time.sleep(0.1)
    code_data = {
        'isFuzzy' : 'false',
        'stationName' : stationName
    }
    telegramCode_data = get_data_json(url=summary_url,headers=headers,data=code_data)
    telegramCode = telegramCode_data['data'][0]['telegramCode']
    return telegramCode
 
date = get_raw_date(2)
for trainNumber in trainNumbers:
    get_data = {
        'date': date,
        'TrainNumber': trainNumber
    }
    get_json_data = get_data_json(url=url, headers=headers, data=get_data)
    start_station = get_json_data['data']['data'][0]['beginStationName']
    end_station = get_json_data['data']['data'][0]['endStationName']
    start_telegramCode = get_telegramCode(start_station)
    end_telegramCode = get_telegramCode(end_station)
    time_data = {
        'date': date,
        'TrainNumber': trainNumber,
        'fromStationTelegramCode' : start_telegramCode,
        'toStationTelegramCode' : end_telegramCode
    }
    time_json_data = get_data_json(url=time_url, headers=headers, data=time_data)
    num_json_data=len(time_json_data['data'])
    formatted_date = format_date(date)
    get_result_data(num_json_data, time_json_data)

# 收集所有 (城市, 日期) 组合，按日期批量预取天气（单次 archive 请求覆盖多城市），
# 再回填到各行。天气网络请求从“城市数级”压到“日期数级”(通常 1 次)。
from collections import defaultdict
pairs_by_date = defaultdict(set)
base_date = format_date(date)
for r in result_lists:
    if not r:
        continue
    st = r.get('车站名')
    if not st:
        continue
    arrival = r.get('到达日期') or r.get('出发日期')
    if arrival and len(str(arrival)) == 8:
        d = f"{str(arrival)[:4]}-{str(arrival)[4:6]}-{str(arrival)[6:8]}"
    else:
        d = arrival if arrival else base_date
    city = re.sub(r'[东西南北]?站', '', str(st).strip() + '站')
    pairs_by_date[d].add(city)

for d, cities in pairs_by_date.items():
    fetch_day_weather(list(cities), d)

# 回填天气到每行（命中缓存，几乎零耗时）
for r in result_lists:
    if not r:
        continue
    st = r.get('车站名')
    if not st:
        continue
    arrival = r.get('到达日期') or r.get('出发日期')
    if arrival and len(str(arrival)) == 8:
        d = f"{str(arrival)[:4]}-{str(arrival)[4:6]}-{str(arrival)[6:8]}"
    else:
        d = arrival if arrival else base_date
    w = fetch_station_weather(st, d) or empty_weather()
    r.update(w)

df = pd.DataFrame(result_lists)
df.to_csv(f'dataset/{date}_{trainNumbers[0]}.csv', index=False)
