# 列车时刻表数据获取工具

这是一个自动化从指定网址获取列车时刻表数据的工具。

## 功能简介

该工具能够从网络端点全自动获取指定列车的时刻表数据，并以结构化格式存储。目前数据输出格式为CSV文件。

## 使用方法

1. 确保已安装所有依赖项：
   ```bash
   pip install -r requirements.txt
   ```

2. 运行主程序：
   ```bash
   python main.py
   ```

## 主要函数说明

- `get_raw_date(i=1)`：获取原始日期数据。
- `format_date(date)`：格式化日期数据。
- `get_result_data(num_json_data, time_json_data)`：从JSON数据中提取结果。
- `get_data_json(url, headers, data)`：发送请求并获取JSON格式的响应数据。

## 数据输出

程序运行后，列车时刻表数据将被保存在 `datasets/` 目录下的CSV文件中（如 `20250819.csv` 和 `20250820.csv`）。

## 注意事项

- 请确保网络连接正常，并确认目标URL可用。
- 如需调整数据获取逻辑，请修改 `main.py` 中的相关函数。

## 许可证

本项目采用 MIT 许可证。详情请查看项目根目录下的 LICENSE 文件。
