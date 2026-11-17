# sgcc_shgas_veolia

**国家电网电费 + 上海燃气 + 浦东威立雅水费，三合一接入 Home Assistant。**

| 数据源 | 方式 | 说明 |
|---|---|---|
| 🔡 国网电费 | Playwright 自动化 [95598.cn](https://www.95598.cn/osgweb/login)，多模态大模型解算验证码 | 电量/电费/分时电量/余额，除南方电网省份外可用 |
| 🔥 上海燃气 | 直接调用官方 API，图形验证码本地识别 | 本期用量/费用、年用量、表读数、抄表日 |
| 💧 浦东威立雅水费 | 直接调用官方 API | 本期用量/费用（自来水/污水）、表读数、近 12 期账单 |

三个数据源中，**国网电是主数据源**；上海燃气、浦东威立雅不配置账号时自动跳过（仅用电时无需 `SHRQ_*` / `PDWLY_ID`）。燃气与水费数据源源自 [xudingjun3131/sgcc_shgas_veolia](https://github.com/xudingjun3131/sgcc_shgas_veolia)（该仓库已停止维护，本仓库独立演进）；电费部分每日自动同步上游 [ARC-MX/sgcc_electricity_new](https://github.com/ARC-MX/sgcc_electricity_new)。

## 功能特性

- **三种推送模式**（`PUSH_MODE`）：MQTT discovery（推荐）/ RESTFUL 拉取 / LEGACY 直推（兼容保留）
- **HA Energy 面板支持**：每日用电长期统计、燃气/水账单摊日统计序列，均通过 WebSocket `recorder/import_statistics` 导入，Energy 面板直接可选
- **每日用电量趋势实体** + 历史数据一次性回填工具
- **数据落地**：SQLite（默认）/ MySQL；水/气账单存本地表，支持缓存定时重发（防 HA 重启丢数据）
- **余额不足通知**：PushPlus / 自定义 URL
- Docker 一键部署（本地构建），支持 amd64 / arm64

## Home Assistant 实体总览

后缀规则：电实体后缀为 `_{户号后4位}`（如 `sensor.last_electricity_usage_1234`）；水/气在配置了**多个户号**时后缀 `_{完整户号}`，单户号无后缀。

### 国网电费

| 实体 | 说明 |
|---|---|
| `sensor.electricity_charge_balance_xxxx` | 电费余额（预付费账户显示余额，后付费显示上月应交电费），元 |
| `sensor.last_electricity_usage_xxxx` | 最新一天用电量，kWh（attributes 含数据日期） |
| `sensor.daily_electricity_usage_xxxx` | 每日用电量趋势，state 为最新一日用量，attributes 含分时明细 |
| `sensor.yearly_electricity_usage_xxxx` / `..._charge_xxxx` | 年度累计用电量 / 电费 |
| `sensor.month_electricity_usage_xxxx` / `..._charge_xxxx` | 上月用电量 / 上月电费 |
| `sensor.month_valley_usage_xxxx` / `..._flat_...` / `..._peak_...` / `..._tip_...` | 上月谷 / 平 / 峰 / 尖峰分时电量 |
| `sensor.prepay_balance_xxxx` | 预付费余额 / 应交金额，元 |

### 上海燃气

| 实体 | 说明 |
|---|---|
| `sensor.gas_last_usage` | 本期用量，m³ |
| `sensor.gas_last_charge` | 本期费用，元 |
| `sensor.gas_yearly_usage` | 年用量，m³ |
| `sensor.gas_meter_reading` | 燃气表读数（attributes 含档位量/档位金额/价格） |
| `sensor.gas_pay_status` | 缴费状态 |
| `sensor.gas_next_read_date` | 下次抄表日 |

### 浦东威立雅水费

| 实体 | 说明 |
|---|---|
| `sensor.last_water_usage_total` / `_tap` / `_sewage` | 本期用水量（总 / 自来水 / 污水），m³ |
| `sensor.last_water_charge_total` / `_tap` / `_sewage` | 本期费用（总 / 自来水 / 污水处理），元 |
| `sensor.water_meter_reading` | 本期水表读数（attributes 含上期读数、抄表日、下次抄表日、近 12 期账单） |
| `sensor.water_pay_status` | 本期缴费状态（attributes 含缴费时间、实收金额） |

### 外部统计序列（Energy 面板数据源，非实体）

| statistic_id | 说明 |
|---|---|
| `sgcc:daily_electricity_usage_{后4位}` | 每日用电量，含 mean / max / sum（逐日累计） |
| `sgcc:gas_usage_{户号}` | 燃气账单用量按天摊分，纯 sum 序列 |
| `sgcc:water_usage_{户号}` | 水费账单用量按天摊分，纯 sum 序列 |

MQTT discovery 模式下全部实体自动归组到 `sgcc_shgas_veolia` 设备，使用中文友好名，无需任何 YAML。

> 历史环境变量名 `SHRQ_*` / `PDWLY_*` 未随实体重命名（gas/water）而更改，配置时仍使用旧变量名。

## 快速开始

### 0）前置条件

1. **国网账户**：[注册](https://www.95598.cn/osgweb/login)并绑定电表，可手动查询电量。
2. **多模态 LLM API Key**（仅电费需要，用于自动解算国网登录验证码；燃气验证码为本地识别，水费无验证码）：

   默认使用硅基流动（SiliconFlow）的 `Qwen/Qwen3.5-35B-A3B`，也支持任意 OpenAI 兼容接口。

   - 注册 [硅基流动](https://cloud.siliconflow.cn/i/2JCjsjdB)（邀请码 `2JCjsjdB`，新用户赠 16 元代金券，充值一分钱后可用），在控制台创建 API Key。
   - 费用：`Qwen3.5-35B-A3B` 输入 ¥0.0004/K tokens，每次验证码解算约 500 token，个人使用几乎免费。
   - 可换其他多模态模型（`Qwen/Qwen2.5-VL-72B-Instruct` ¥0.004/K、`gpt-4o` ~¥0.015/K、`doubao-seed-2-0-pro` ~¥0.004/K）。
   - **注意：DeepSeek 全系列（V3/V4/R1）为纯文本模型，无法识别验证码图片。**

3. **HA 长期访问令牌**：HA 个人页 → 安全 → 长期访问令牌。
4. 上海燃气 / 浦东威立雅账号（按需，不用则不填）。

### 1）部署（本地构建）

```bash
git clone https://github.com/Guoyin-Wen/sgcc_shgas_veolia.git
cd sgcc_shgas_veolia
cp example.env .env
vim .env   # 按下节说明填写
docker compose up -d --build
```

> 弃坑上游遗留镜像 `xudingjun3131/sgcc_shgas_veolia:v1.0` 冻结在旧版，不含 MQTT discovery、长期统计等新功能，不建议使用。

### 2）`.env` 关键配置

完整清单见 [example.env](example.env)，以下为必改项：

```bash
## 国网登录（主数据源）
PHONE_NUMBER="138xxxxxxxx"
PASSWORD="xxxx"
LLM_API_KEY="sk-xxxxx"           # 电费必填，多模态模型
# LLM_BASE_URL / LLM_MODEL      # 默认硅基流动 Qwen，可换

## 上海燃气（按需，不填则跳过）
SHRQ_PHONE_NUMBER=177xxxxxxxx
SHRQ_PASSWORD=xxxxxxxx

## 浦东威立雅账号ID（按需，不填则跳过；多个用列表格式）
PDWLY_ID=['xxxxxxxxx','yyyyyyyyy']

## Home Assistant
HASS_URL="http://localhost:8123/"
HASS_TOKEN="eyxxxxx"

## 推送模式：MQTT / RESTFUL / LEGACY（详见下节）
PUSH_MODE=MQTT
```

其他常用项：`JOB_START_TIME`（每日执行时间，默认 07:00，每天跑两次间隔 12 小时，实际有 ±10 分钟随机延迟）、`DB_TYPE`（SQLITE/NONE/MYSQL）、`DAILY_FETCH_DAYS`（7 或 30）、`PUSH_TYPE`+`BALANCE`（余额不足通知）、`USER_NAMES`（户号友好名）、`DEBUG_MODE`（本机调试）。

### 3）验证

```bash
docker compose logs -f
```

运行成功的日志形如：

```text
[INFO] ---- 程序开始，当前仓库版本为 v1.8.1
[INFO] ---- 已输入用户名: 138xxxxxxxx
[INFO] ---- Cookie 有效，跳过登录
[INFO] ---- [640xxxxxxxxx] 电费余额: 27.66 元
[INFO] ---- [640xxxxxxxxx] 年度用电量: 1691 度, 年度电费: 758.57 元 (Vue)
[INFO] ---- 上海燃气：本期用量 45m³，本期费用 156.00 元
[INFO] ---- 浦东威立雅：本期用水量 12m³，本期费用 58.20 元
[INFO] ---- Home Assistant 传感器更新完成!
```

## 数据推送方式（PUSH_MODE）

| 模式 | 原理 | 重启 HA 后 | 长期统计/能源面板 | 推荐度 |
|---|---|---|---|---|
| `MQTT` | MQTT discovery 注册正规实体 | retained 消息秒恢复 | ✅ | **推荐** |
| `RESTFUL` | 本项目暴露只读端点，HA `rest` 集成定时拉取 | 启动即拉取 | ✅ | 推荐（不想装 broker 时） |
| `LEGACY` | REST API 直推（上游原始方式） | 实体变 unavailable，等待重发 | ❌ | 历史遗留，仅为兼容保留 |

### 方式一：MQTT（推荐）

实体经 HA 官方 MQTT 集成注册：有 unique_id、重启后从 retained 消息恢复、自动进入长期统计、可在 UI 中改名/改图标，且全部实体自动归组到 `sgcc_shgas_veolia` 设备下。

1. 部署 broker（任一台内网机器）：

```yaml
services:
  mosquitto:
    image: eclipse-mosquitto:2
    container_name: mosquitto
    restart: unless-stopped
    ports:
      - "1883:1883"
    volumes:
      - ./config:/mosquitto/config
      - ./data:/mosquitto/data
```

`config/mosquitto.conf`：

```ini
listener 1883
allow_anonymous false
password_file /mosquitto/config/passwd
persistence true
persistence_location /mosquitto/data/
```

生成账号：`docker run --rm -v ./config:/mosquitto/config eclipse-mosquitto:2 mosquitto_passwd -c /mosquitto/config/passwd <用户名>`

2. 本项目 `.env` 配置：

```bash
PUSH_MODE=MQTT
MQTT_HOST=<broker 地址>   # broker 与本项目同机且 host 网络时填 127.0.0.1
MQTT_PORT=1883
MQTT_USER=<用户名>
MQTT_PASSWORD=<密码>
```

3. HA 侧：设置 → 设备与服务 → 添加集成 → 搜索 **MQTT** → 填 broker 地址与账号。添加后设备及全部传感器自动出现，无需任何 YAML。

### 方式二：RESTFUL（免 broker）

本项目内置只读快照端点（`GET http://<本项目IP>:8300/sensors`，端口可用 `STATE_SERVER_PORT` 修改），HA 用官方 `rest` 集成定时拉取。实体由 RESTful platform 注册，同样支持重启恢复与长期统计。

`.env` 配置：

```bash
PUSH_MODE=RESTFUL
STATE_SERVER_PORT=8300
```

HA `configuration.yaml` 示例（每个需要的传感器一段，`scan_interval` 建议 900 秒以上——数据本身一天只更新两次）：

```yaml
sensor:
  - platform: rest
    resource: http://<本项目IP>:8300/sensors
    name: water_charge_total
    unique_id: water_charge_total
    unit_of_measurement: "CNY"
    device_class: monetary
    state_class: measurement
    value_template: "{{ value_json['sensor.last_water_charge_total'].state }}"
    json_attributes_path: "sensor.last_water_charge_total"
    json_attributes:
      - attributes
    scan_interval: 900
```

### 方式三：LEGACY（历史遗留，不推荐）

REST API 直推（上游原始方式），实体为"无主实体"——重启 HA 后 unavailable、不进长期统计、不能在 UI 中管理。仅因向后兼容保留；若必须使用并希望重启后实体可用，参考 [template/configuration.yaml](template/configuration.yaml) 中的 trigger template 镜像传感器样例。

## HA Energy 面板与历史回填

- **配置 → 仪表盘 → 能源**：
  - 家庭用电 → 选统计 `每日用电量 (sgcc:daily_electricity_usage_xxxx)`
  - 燃气 / 水 → 分别选 `sgcc:gas_usage_{户号}`、`sgcc:water_usage_{户号}`（账单用量按天摊分，末账期后按日均估算，新账单落地自动校正）
- **历史回填**（把 SQLite 中已有的每日用电历史导入 HA 长期统计，幂等可重复执行）：

```bash
docker exec sgcc_shgas_veolia python3 /app/backfill_daily_stats.py
```

## 数据存储与运维

- SQLite 文件默认 `homeassistant.db`（Docker 内 `/data/`，即 compose 挂载的仓库目录），`DB_TYPE=MYSQL` 可切换 MySQL。
- 每户号一套表：`daily{户号}`（每日用电）、`data{户号}`（电费快照）、`shrq_bill{户号}`（燃气账单）、`pdwly_bill{户号}`（水费账单）。
- 缓存重发：每 `CACHE_REPUBLISH_INTERVAL` 分钟（默认 60）把缓存数据重推 HA，防止 HA 重启后实体空窗；启动时若有有效缓存则跳过抓取以保护账号。
- 更新：

```bash
git pull
docker compose up -d --build
```

## 仪表盘参考

<p align="center">
<img src="assets/homeassistant.png" alt="Home Assistant 实体展示效果" width=1024 />
</p>

<p align="center">
<img src="assets/grafana_dashboard.jpg" alt="Grafana 水电燃气监控看板" width=1024 />
</p>

Grafana 模板：[水费_电费_燃气.json](assets/水费_电费_燃气-1730082076616.json)；HA 卡片可结合 [mini-graph-card](https://github.com/kalkih/mini-graph-card) 与 [mushroom](https://github.com/piitaya/lovelace-mushroom) 美化，示例：

```yaml
type: custom:mini-graph-card
entities:
  - entity: sensor.last_electricity_usage_xxxx
    name: 国网每日用电量
    aggregate_func: first
    show_state: true
    show_points: true
    icon: mdi:lightning-bolt-outline
  - entity: sensor.electricity_charge_balance_xxxx
    name: 电费余额
    aggregate_func: first
    show_state: true
    show_points: true
    color: "#e74c3c"
    icon: mdi:cash
    y_axis: secondary
group_by: date
hour24: true
hours_to_show: 240
lower_bound: 0
```

（`_xxxx` 替换为自己日志中显示的户号后 4 位。）

## 常见问题

- **验证码识别成功但登录失败**：国网每天登录次数有限，超限后当天无法再登录，属正常风控，次日再试。
- **只能查 7 天日用电量**：未开通智能缴费只能查 7 天（`DAILY_FETCH_DAYS=7`）；开通智能缴费可能使电费从后付费变预付费（欠费即停电），注意风险。
- **水/气数据没更新**：查看日志中「浦东威立雅水费数据刷新任务失败」/「上海燃气数据刷新任务失败」——两者失败互不影响，也不影响电费抓取。
- **余额通知**：`PUSH_TYPE=PUSHPLUS` 或 `URLPUSH`，配合 `BALANCE` 阈值与 `PUSHPLUS_TOKEN` / `PUSH_URL`。

## 与上游的关系

| 部分 | 来源 | 现状 |
|---|---|---|
| 国网电费 | [ARC-MX/sgcc_electricity_new](https://github.com/ARC-MX/sgcc_electricity_new) | 上游仍活跃，本仓库经 [GitHub Actions](.github/workflows/auto-merge-upstream.yml) 每日自动合并其 master |
| 上海燃气 + 浦东威立雅水费 | [xudingjun3131/sgcc_shgas_veolia](https://github.com/xudingjun3131/sgcc_shgas_veolia) | 上游已停止维护，本仓库独立演进 |

## 致谢

- [louisslee/sgcc_electricity](https://github.com/louisslee/sgcc_electricity) — 项目起源，国网电费接入的原始实现。
- [ARC-MX/sgcc_electricity_new](https://github.com/ARC-MX/sgcc_electricity_new) — 国网电费上游：LLM 验证码识别、Vue 状态注入取数、分时电量、Playwright 反检测等大量核心工作均来自该仓库及其社区贡献者。
- [xudingjun3131/sgcc_shgas_veolia](https://github.com/xudingjun3131/sgcc_shgas_veolia) — 上海燃气与浦东威立雅水费数据源的引入与最初实现。
- 以及原 README 中提及的所有贡献者（Ami8834671、DuanXDong、renxiaoyaoo 等）。

## License

[Apache-2.0](LICENSE)，沿用上游协议。
