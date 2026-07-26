import requests
import datetime
import time
import pandas as pd

# ========== 天气相关配置 ==========
WTTR_URL = 'https://wttr.in'


def get_weather_wttr(city_name, date_str=None):
    """使用 wttr.in 获取指定城市的天气数据"""
    try:
        resp = requests.get(f"{WTTR_URL}/{city_name}", params={
            'format': 'j1'
        }, timeout=10)
        data = resp.json()
        current = data.get('current_condition', [{}])[0]

        temp_c = current.get('temp_C', '')
        humidity = current.get('humidity', '')
        precip_mm = current.get('precipMM', '')
        desc = current.get('weatherDesc', [{}])[0].get('value', '')
        wind_speed = current.get('windspeedKmph', '')

        return {
            '当日最高温': temp_c,
            '当日最低温': temp_c,
            '当日降水量': float(precip_mm) if precip_mm else 0
        }
    except Exception as e:
        print(f"  [警告] wttr.in 查询失败 ({city_name}): {e}")
    return None


def get_station_weather(station_name, date_str):
    """获取站点城市的天气数据，使用 wttr.in"""
    time.sleep(0.5)  # 请求间隔，避免触发限流

    # 提取城市名（去掉"站"、"东"、"西"、"南"、"北"等后缀）
    city_name = station_name.replace('站', '').replace('东', '').replace('西', '').replace('南', '').replace('北', '')
    
    weather = get_weather_wttr(city_name, date_str)
    if weather:
        print(f"  [天气] {station_name} -> wttr.in: {weather['当日最高温']}°C")
        return weather

    print(f"  [天气] {station_name} -> 无数据")
    return {
        '当日最高温': '',
        '当日最低温': '',
        '当日降水量': ''
    }

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

        # 获取该站点城市的天气
        arrival_date = result['到达日期'] or result['出发日期']
        if arrival_date:
            date_str = f"{arrival_date[:4]}-{arrival_date[4:6]}-{arrival_date[6:8]}" if len(arrival_date) == 8 else arrival_date
        else:
            date_str = format_date(date)

        weather = get_station_weather(station_name, date_str)
        result.update(weather)
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
df = pd.DataFrame(result_lists)
df.to_csv(f'dataset/{date}_{trainNumbers[0]}.csv', index=False)
