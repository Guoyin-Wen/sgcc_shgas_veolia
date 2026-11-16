import json
import logging
import os
import threading
from datetime import datetime, timedelta

import requests
from const import *


class SensorUpdator:

    def __init__(self):
        HASS_URL = os.getenv("HASS_URL")
        HASS_TOKEN = os.getenv("HASS_TOKEN")
        self.base_url = HASS_URL[:-1] if HASS_URL.endswith("/") else HASS_URL
        self.token = HASS_TOKEN
        self._init_balance_notify()
        # 推送模式: MQTT(推荐, 经 broker+discovery 注册正规实体) / RESTFUL(HA rest 集成拉取
        # /sensors 快照) / LEGACY(REST API 直推, 历史遗留, 不推荐)
        self.push_mode = os.getenv("PUSH_MODE", "LEGACY").strip().upper()
        self._mqtt_client = None
        self._mqtt_published = set()
        self._snapshot_lock = threading.Lock()

    def _init_balance_notify(self):
        push_type = os.getenv("PUSH_TYPE", "None").lower()
        if push_type == "pushplus":
            from notify import PushplusNotify
            self.balance_notify = PushplusNotify()
        elif push_type == "urlpush":
            from notify import UrlPushNotify
            self.balance_notify = UrlPushNotify()
        else:
            self.balance_notify = None


    def update_one_userid(self, user_id: str, balance: float, last_daily_date: str, last_daily_usage: float, yearly_charge: float, yearly_usage: float, month_charge: float, month_usage: float, month_bill_date=None, tou_data: dict = None, enhanced_balance: dict = None, bill_tou_data: dict = None, notify=True):
        logging.info(f"[{user_id}] 开始更新 Home Assistant 传感器数据...")
        self._save_to_cache(user_id, balance, last_daily_date, last_daily_usage, yearly_charge, yearly_usage, month_charge, month_usage, month_bill_date, tou_data, enhanced_balance, bill_tou_data)
        postfix = f"_{user_id[-4:]}"
        if balance is not None:
            if notify and self.balance_notify is not None:
                self.balance_notify(user_id, balance)
            self.update_balance(postfix, balance, enhanced_balance)
        if last_daily_usage is not None:
            self.update_last_daily_usage(postfix, last_daily_date, last_daily_usage)
        if yearly_usage is not None:
            self.update_yearly_data(postfix, yearly_usage, usage=True)
        if yearly_charge is not None:
            self.update_yearly_data(postfix, yearly_charge)
        if month_usage is not None:
            self.update_month_data(postfix, month_usage, usage=True, bill_date=month_bill_date)
        if month_charge is not None:
            self.update_month_data(postfix, month_charge, bill_date=month_bill_date)

        # 分时电量传感器（优先用账单数据，更准确）
        self._update_tou_sensors(postfix, tou_data, bill_tou_data)

        # 每日用电量趋势：独立实体 + 逐日精确长期统计导入（供仪表板日趋势图）
        if tou_data and tou_data.get("daily"):
            try:
                self.update_daily_series(postfix, tou_data["daily"])
            except Exception as e:
                logging.warning(f"每日用电量趋势更新失败（不影响其他数据）: {e}")

        # 应交金额传感器
        if enhanced_balance and enhanced_balance.get("amount_due") is not None:
            self.update_prepay_balance(postfix, enhanced_balance["amount_due"])

        logging.info(f"[{user_id}] Home Assistant 传感器数据更新完成!")

    def _get_cache_file(self):
        from const import get_data_dir
        return os.path.join(get_data_dir(), 'sgcc_cache.json')

    def _save_to_cache(self, user_id, balance, last_daily_date, last_daily_usage, yearly_charge, yearly_usage, month_charge, month_usage, month_bill_date=None, tou_data=None, enhanced_balance=None, bill_tou_data=None):
        cache_file = self._get_cache_file()
        abs_cache_file = os.path.abspath(cache_file)
        data = {}
        try:
            if os.path.exists(cache_file):
                with open(cache_file, 'r') as f:
                    data = json.load(f)
        except Exception as e:
            logging.warning(f"加载缓存文件失败: {e}")

        cache_entry = {
            "balance": balance,
            "last_daily_date": last_daily_date,
            "last_daily_usage": last_daily_usage,
            "yearly_charge": yearly_charge,
            "yearly_usage": yearly_usage,
            "month_charge": month_charge,
            "month_usage": month_usage,
            "month_bill_date": month_bill_date,
            "timestamp": datetime.now().isoformat()
        }

        if tou_data:
            cache_entry["tou_data"] = tou_data
        if enhanced_balance:
            cache_entry["enhanced_balance"] = enhanced_balance
        if bill_tou_data:
            cache_entry["bill_tou_data"] = bill_tou_data

        data[user_id] = cache_entry

        try:
            with open(cache_file, 'w') as f:
                json.dump(data, f, indent=2)
            logging.debug(f"已保存数据到缓存文件: {abs_cache_file}")
        except Exception as e:
            logging.error(f"保存缓存文件失败 {abs_cache_file}: {e}")

    def republish(self):
        cache_file = self._get_cache_file()
        abs_cache_file = os.path.abspath(cache_file)
        if not os.path.exists(cache_file):
            logging.info(f"未找到缓存文件 {abs_cache_file}，跳过重新推送。")
            return False

        data = {}
        try:
            with open(cache_file, 'r') as f:
                data = json.load(f)
        except Exception as e:
            logging.error(f"加载缓存文件失败 {abs_cache_file}: {e}")
            return False

        # 检查缓存数据的日期是否与当前日期一致
        today_str = datetime.now().strftime("%Y-%m-%d")
        for user_id, values in data.items():
            cache_timestamp = values.get("timestamp", "")
            cache_date = cache_timestamp[:10] if cache_timestamp else ""
            if cache_date != today_str:
                logging.info(f"缓存数据日期({cache_date})与当前日期({today_str})不一致，需要从国家电网重新获取数据。")
                return False

        try:
            for user_id, values in data.items():
                logging.info(f"正在从缓存重新推送用户 {user_id} 的数据。")
                clean_values = {k: v for k, v in values.items() if k != 'timestamp'}
                self.update_one_userid(user_id, **clean_values, notify=False)
            return True
        except Exception as e:
            logging.error(f"重新推送数据失败: {e}")
            return False

    def get_sensor_state(self, sensor_name):
        headers = {
            "Content-Type": "application/json",
            "Authorization": "Bearer " + self.token,
        }
        url = self.base_url + API_PATH + sensor_name
        try:
            response = requests.get(url, headers=headers, timeout=10)
            if response.status_code == 200:
                return response.json()
            return None
        except Exception as e:
            logging.warning(f"获取传感器 {sensor_name} 状态失败: {e}")
            return None

    def should_update(self, sensor_name, new_state, check_attributes=None):
        current_state_obj = self.get_sensor_state(sensor_name)
        if not current_state_obj:
            return True

        # 检查状态
        try:
            current_state = current_state_obj.get('state')
            if current_state in ['unknown', 'unavailable', None]:
                return True

            curr_val = float(current_state)
            new_val = float(new_state)
            if abs(curr_val - new_val) > 0.001:
                return True
        except (ValueError, TypeError):
            # 如果无法作为浮点数比较，则假定不同
            return True

        # 如需则检查属性
        if check_attributes:
            curr_attrs = current_state_obj.get('attributes', {})
            for k, v in check_attributes.items():
                # 转换为字符串进行比较以避免类型不匹配
                if str(curr_attrs.get(k)) != str(v):
                    return True

        return False

    def update_last_daily_usage(self, postfix: str, last_daily_date: str, sensorState: float):
        sensorName = DAILY_USAGE_SENSOR_NAME + postfix

        if not self.should_update(sensorName, sensorState, {"data_date": last_daily_date}):
             logging.info(f"跳过 {sensorName} 的更新，状态相同。")
             return

        request_body = {
            "state": sensorState,
            "unique_id": sensorName,
            "attributes": {
                "data_date": last_daily_date,
                "unit_of_measurement": "kWh",
                "icon": "mdi:lightning-bolt",
                "device_class": "energy",
                "state_class": "measurement",
            },
        }

        self.send_url(sensorName, request_body)
        logging.info(f"Home Assistant 传感器 {sensorName} 状态已更新: {sensorState} kWh")

    def update_balance(self, postfix: str, sensorState: float, enhanced_balance: dict = None):
        sensorName = BALANCE_SENSOR_NAME + postfix

        if not self.should_update(sensorName, sensorState):
             logging.info(f"跳过 {sensorName} 的更新，状态相同。")
             return

        last_reset = datetime.now().strftime("%Y-%m-%d, %H:%M:%S")
        attributes = {
            "last_reset": last_reset,
            "unit_of_measurement": "CNY",
            "icon": "mdi:cash",
            "device_class": "monetary",
            "state_class": "total",
        }
        if enhanced_balance:
            if enhanced_balance.get("amount_due") is not None:
                attributes["amount_due"] = enhanced_balance["amount_due"]

        request_body = {
            "state": sensorState,
            "unique_id": sensorName,
            "attributes": attributes,
        }

        self.send_url(sensorName, request_body)
        logging.info(f"Home Assistant 传感器 {sensorName} 状态已更新: {sensorState} CNY")

    def update_month_data(self, postfix: str, sensorState: float, usage=False, bill_date=None):
        sensorName = (
            MONTH_USAGE_SENSOR_NAME + postfix
            if usage
            else MONTH_CHARGE_SENSOR_NAME + postfix
        )
        current_date = datetime.now()
        first_day_of_current_month = current_date.replace(day=1)
        last_day_of_previous_month = first_day_of_current_month - timedelta(days=1)
        last_reset = last_day_of_previous_month.strftime("%Y-%m")
        bill_period = bill_date or last_reset

        if not self.should_update(sensorName, sensorState, {"bill_period": bill_period}):
             logging.info(f"跳过 {sensorName} 的更新，状态相同。")
             return

        request_body = {
            "state": sensorState,
            "unique_id": sensorName,
            "attributes": {
                "bill_period": bill_period,
                "unit_of_measurement": "kWh" if usage else "CNY",
                "icon": "mdi:lightning-bolt" if usage else "mdi:cash",
                "device_class": "energy" if usage else "monetary",
                "state_class": "measurement",
            },
        }

        self.send_url(sensorName, request_body)
        logging.info(f"Home Assistant 传感器 {sensorName} 状态已更新: {sensorState} {'kWh' if usage else 'CNY'}")

    def update_yearly_data(self, postfix: str, sensorState: float, usage=False):
        sensorName = (
            YEARLY_USAGE_SENSOR_NAME + postfix
            if usage
            else YEARLY_CHARGE_SENSOR_NAME + postfix
        )
        if datetime.now().month == 1:
            last_year = datetime.now().year -1
            last_reset = datetime.now().replace(year=last_year).strftime("%Y")
        else:
            last_reset = datetime.now().strftime("%Y")

        if not self.should_update(sensorName, sensorState, {"last_reset": last_reset}):
             logging.info(f"跳过 {sensorName} 的更新，状态相同。")
             return

        request_body = {
            "state": sensorState,
            "unique_id": sensorName,
            "attributes": {
                "last_reset": last_reset,
                "unit_of_measurement": "kWh" if usage else "CNY",
                "icon": "mdi:lightning-bolt" if usage else "mdi:cash",
                "device_class": "energy" if usage else "monetary",
                "state_class": "total_increasing",
            },
        }
        self.send_url(sensorName, request_body)
        logging.info(f"Home Assistant 传感器 {sensorName} 状态已更新: {sensorState} {'kWh' if usage else 'CNY'}")

    def _update_tou_sensors(self, postfix: str, tou_data: dict = None, bill_tou_data: dict = None):
        """更新月度分时电量传感器（谷/平/峰/尖）。

        优先使用账单数据（bill_tou_data），更准确；
        兜底从每日数据汇总当月值。
        """
        current_date = datetime.now()
        first_day = current_date.replace(day=1)
        last_day_prev = first_day - timedelta(days=1)
        last_reset = last_day_prev.strftime("%Y-%m")

        tou_fields = [
            ("valley_usage", MONTH_VALLEY_SENSOR_NAME, "谷"),
            ("flat_usage", MONTH_FLAT_SENSOR_NAME, "平"),
            ("peak_usage", MONTH_PEAK_SENSOR_NAME, "峰"),
            ("tip_usage", MONTH_TIP_SENSOR_NAME, "尖"),
        ]

        # 优先：账单分时数据
        if bill_tou_data:
            tou_values = {
                "valley_usage": bill_tou_data.get("valley_usage"),
                "flat_usage": bill_tou_data.get("flat_usage"),
                "peak_usage": bill_tou_data.get("peak_usage"),
                "tip_usage": bill_tou_data.get("tip_usage"),
            }
        elif tou_data:
            # 兜底：从 daily 数据汇总当月分时
            daily_rows = tou_data.get("daily", [])
            if daily_rows:
                current_month_prefix = current_date.strftime("%Y-%m")
                tou_values = {
                    "valley_usage": sum(r.get("valley_usage", 0) or 0 for r in daily_rows if str(r.get("date", "")[:7]) == current_month_prefix),
                    "flat_usage": sum(r.get("flat_usage", 0) or 0 for r in daily_rows if str(r.get("date", "")[:7]) == current_month_prefix),
                    "peak_usage": sum(r.get("peak_usage", 0) or 0 for r in daily_rows if str(r.get("date", "")[:7]) == current_month_prefix),
                    "tip_usage": sum(r.get("tip_usage", 0) or 0 for r in daily_rows if str(r.get("date", "")[:7]) == current_month_prefix),
                }
            else:
                return
        else:
            return

        for field_key, sensor_base, label in tou_fields:
            value = tou_values.get(field_key, 0)
            if not value or value <= 0:
                continue
            sensorName = sensor_base + postfix
            if not self.should_update(sensorName, value, {"last_reset": last_reset}):
                logging.info(f"跳过 {sensorName} 的更新，状态相同。")
                continue
            request_body = {
                "state": value,
                "unique_id": sensorName,
                "attributes": {
                    "last_reset": last_reset,
                    "unit_of_measurement": "kWh",
                    "icon": "mdi:lightning-bolt",
                    "device_class": "energy",
                    "state_class": "measurement",
                    "friendly_name": f"月度{label}时电量",
                },
            }
            self.send_url(sensorName, request_body)
            logging.info(f"Home Assistant 传感器 {sensorName} 状态已更新: {value} kWh ({label})")

    def update_daily_series(self, postfix: str, daily_rows: list):
        """每日用电量趋势（二开新增）：
        1. MQTT 推独立实体（无 state_class → recorder 不生成自动统计）
        2. 把每天用量按天导入 HA recorder 长期统计（WS recorder/import_statistics），
           statistics-graph(period: day, stat_types: [mean]) 即可画出精确逐日柱状图。
        daily_rows: [{"date": "YYYY-MM-DD", "total_usage": x.x, 谷/平/峰/尖...}, ...]
        """
        rows = []
        for r in daily_rows or []:
            try:
                u = float(r.get("total_usage"))
                if r.get("date") and u >= 0:
                    rows.append(r)
            except (TypeError, ValueError):
                continue
        if not rows:
            return
        rows.sort(key=lambda r: r["date"])
        latest = rows[-1]
        sensorName = DAILY_ELE_SENSOR_NAME + postfix

        # 实体状态 = 最新一日用量；attributes 带分时明细
        # ⚠️ 故意不带 state_class：recorder 自动统计会把实体 state（最近一期日用量）按小时采样，
        # 在数据日期之后的每一天生成假"日用量"统计点，与手动导入的精确统计混在同一图上（2026-11-10 回滚 11-07 的加法）
        if not self.should_update(sensorName, float(latest["total_usage"]),
                                  {"data_date": latest["date"]}):
            logging.info(f"跳过 {sensorName} 的更新，状态相同。")
        else:
            request_body = {
                "state": float(latest["total_usage"]),
                "unique_id": sensorName,
                "attributes": {
                    "data_date": latest["date"],
                    "unit_of_measurement": "kWh",
                    "icon": "mdi:lightning-bolt",
                    "device_class": "energy",
                    "friendly_name": "每日用电量",
                    "valley_usage": latest.get("valley_usage"),
                    "flat_usage": latest.get("flat_usage"),
                    "peak_usage": latest.get("peak_usage"),
                    "tip_usage": latest.get("tip_usage"),
                },
            }
            self.send_url(sensorName, request_body)

        # 逐日长期统计导入（幂等：同 start 重复导入同值无害）
        # ⚠️ 2026-11-17 起改用外部统计 id（sgcc:xxx，非实体 ID）：HA 2026.11 对实体 ID 格式的
        # import_statistics 一律按 recorder 内部统计处理，实体会因"无 state_class 但有统计"反复告警
        statistic_id = "sgcc:daily_electricity_usage" + postfix
        # sum 列 = 自 sqlite 首条记录起的逐日累计（Energy 面板吃 sum 差值；必须单调连续，不能带年基数，
        # 否则查询范围首行会出现整条累计的假尖峰）。cum_usage 由 data_fetcher 从 sqlite 全表算出注入；
        # 缺失时只导 mean 不带 sum，宁可少一列也不导入会跳变的序列
        stats = []
        for r in rows:
            u = float(r["total_usage"])
            item = {
                "start": f"{r['date']}T00:00:00+08:00",
                "mean": u,
                "max": u,
            }
            if r.get("cum_usage") is not None:
                item["sum"] = float(r["cum_usage"])
                item["state"] = float(r["cum_usage"])
            stats.append(item)
        self._import_statistics_ws(statistic_id, stats)

    def _import_statistics_ws(self, statistic_id: str, stats: list):
        """经 HA WebSocket recorder/import_statistics 导入长期统计（二开新增）。
        优先 HASS_URL，失败回退内网 http://192.168.1.12；仅告警不抛错。"""
        import asyncio
        import re as _re

        base = self.base_url.rstrip("/")
        candidates = [_re.sub(r"^http", "ws", base) + "/api/websocket"]
        if "192.168.1.12" not in base:
            candidates.append("ws://192.168.1.12/api/websocket")

        payload = {
            "id": 1,
            "type": "recorder/import_statistics",
            "metadata": {
                "has_mean": True,
                # 是否带 sum 列按实际数据决定（Energy 面板依赖 sum 差值；缺 cum 时不硬标）
                "has_sum": any("sum" in s for s in stats),
                "statistic_id": statistic_id,
                "unit_of_measurement": "kWh",
                "unit_class": "energy",
                # source 必须等于 statistic_id 冒号前的域（外部统计 HA 校验规则）
                "source": statistic_id.split(":")[0],
                # mean_type=1(ARITHMETIC)：不填会有 deprecation 警告（2026.11 起强制）
                "mean_type": 1,
                "name": "每日用电量",
            },
            "stats": stats,
        }

        async def _run():
            import websockets
            last_err = None
            for url in candidates:
                try:
                    async with websockets.connect(url, open_timeout=10) as ws:
                        await ws.recv()  # auth_required
                        await ws.send(json.dumps(
                            {"type": "auth", "access_token": self.token}))
                        msg = json.loads(await ws.recv())
                        if msg.get("type") != "auth_ok":
                            raise RuntimeError(f"auth 失败: {msg.get('type')}")
                        await ws.send(json.dumps(payload))
                        r = json.loads(await ws.recv())
                        if not r.get("success"):
                            raise RuntimeError(f"导入被拒: {r.get('error')}")
                        return len(stats)
                except Exception as e:
                    last_err = e
                    logging.warning(f"统计导入 {url} 失败: {e}")
            raise last_err

        try:
            n = asyncio.run(_run())
            logging.info(f"已导入 {n} 天每日用电量统计到 {statistic_id}")
        except Exception as e:
            logging.warning(f"[{statistic_id}] 每日统计导入最终失败: {e}")

    def update_prepay_balance(self, postfix: str, sensorState: float):
        """更新预付费余额传感器"""
        sensorName = PREPAY_BALANCE_SENSOR_NAME + postfix
        if not self.should_update(sensorName, sensorState):
            logging.info(f"跳过 {sensorName} 的更新，状态相同。")
            return
        last_reset = datetime.now().strftime("%Y-%m-%d, %H:%M:%S")
        request_body = {
            "state": sensorState,
            "unique_id": sensorName,
            "attributes": {
                "last_reset": last_reset,
                "unit_of_measurement": "CNY",
                "icon": "mdi:cash-check",
                "device_class": "monetary",
                "state_class": "total",
                "friendly_name": "预付费余额",
            },
        }
        self.send_url(sensorName, request_body)
        logging.info(f"Home Assistant 传感器 {sensorName} 状态已更新: {sensorState} CNY")

    def send_url(self, sensorName, request_body):
        # 三种推送模式的统一汇聚点
        if self.push_mode == "MQTT":
            return self._send_mqtt(sensorName, request_body)
        if self.push_mode == "RESTFUL":
            return self._send_snapshot(sensorName, request_body)
        headers = {
            "Content-Type": "application/json",
            "Authorization": "Bearer " + self.token,
        }
        url = self.base_url + API_PATH + sensorName
        try:
            response = requests.post(url, json=request_body, headers=headers, timeout=15)
            logging.debug(
                f"Home Assistant REST API 调用，POST {url}。响应[{response.status_code}]: {response.content}"
            )
        except Exception as e:
            logging.error(f"Home Assistant REST API 调用失败，原因是 {e}")

    # ── MQTT 模式：discovery + retained state，由 HA 官方 MQTT 集成注册正规实体 ──
    def _mqtt(self):
        if self._mqtt_client is None:
            import paho.mqtt.client as mqtt
            client = mqtt.Client(client_id="sgcc_shgas_veolia")
            user = os.getenv("MQTT_USER")
            if user:
                client.username_pw_set(user, os.getenv("MQTT_PASSWORD", ""))
            client.connect(os.getenv("MQTT_HOST"), int(os.getenv("MQTT_PORT", "1883")), keepalive=60)
            client.loop_start()
            self._mqtt_client = client
        return self._mqtt_client

    def _send_mqtt(self, sensorName, request_body):
        try:
            client = self._mqtt()
            object_id = sensorName.split(".", 1)[1] if "." in sensorName else sensorName
            state_topic = f"home/sgcc_veolia/{object_id}/state"
            attrs_topic = f"home/sgcc_veolia/{object_id}/attributes"
            attrs = dict(request_body.get("attributes") or {})
            if sensorName not in self._mqtt_published:
                # 实体已全部在 HA 注册（unique_id 锁定 entity_id），首次注册后改 name
                # 只刷新 friendly_name、不动 entity_id，故 name 可安全用中文显示名
                base = object_id.rsplit("_", 1)[0] if object_id.rsplit("_", 1)[-1].isdigit() else object_id
                config = {
                    "name": self._CN_SENSOR_NAMES.get(base, object_id),
                    "unique_id": object_id,
                    "state_topic": state_topic,
                    "value_template": "{{ value_json.state }}",
                    "json_attributes_topic": attrs_topic,
                    "device": {
                        "identifiers": ["sgcc_shgas_veolia"],
                        "name": "sgcc_shgas_veolia",
                        "manufacturer": "sgcc_shgas_veolia",
                    },
                }
                for key in ("unit_of_measurement", "device_class", "state_class", "icon"):
                    if key in attrs:
                        config[key] = attrs.pop(key)
                attrs.pop("friendly_name", None)
                client.publish(f"homeassistant/sensor/sgcc_veolia/{object_id}/config",
                               json.dumps(config, ensure_ascii=False), retain=True, qos=1)
                self._mqtt_published.add(sensorName)
            client.publish(state_topic, json.dumps({"state": request_body.get("state")}, ensure_ascii=False),
                           retain=True, qos=1)
            if attrs:
                client.publish(attrs_topic, json.dumps(attrs, ensure_ascii=False), retain=True, qos=1)
            logging.info(f"MQTT 传感器 {sensorName} 状态已更新: {request_body.get('state')}")
        except Exception as e:
            logging.error(f"MQTT 发布失败（{sensorName}）: {e}")

    # ── RESTFUL 模式：写本地快照，由 state_server 暴露给 HA rest 集成拉取 ──
    def _send_snapshot(self, sensorName, request_body):
        try:
            path = os.path.join(get_data_dir(), "sensors_state.json")
            with self._snapshot_lock:
                data = {}
                if os.path.exists(path):
                    try:
                        with open(path) as f:
                            data = json.load(f)
                    except Exception:
                        data = {}
                data[sensorName] = {
                    "state": request_body.get("state"),
                    "attributes": request_body.get("attributes") or {},
                    "updated": datetime.now().isoformat(timespec="seconds"),
                }
                with open(path, "w") as f:
                    json.dump(data, f, ensure_ascii=False, indent=1)
            logging.info(f"快照传感器 {sensorName} 状态已更新: {request_body.get('state')}")
        except Exception as e:
            logging.error(f"快照写入失败（{sensorName}）: {e}")

    # 二开新增：MQTT discovery 中文显示名（键为去户号尾缀的 base 名；语义按 2026-11-06 核实：
    # month_* 实为上月已出账账单、last_electricity_usage 为昨日数据、水/气 last_* 为最近一期账单）
    _CN_SENSOR_NAMES = {
        "electricity_charge_balance": "电费余额",
        "prepay_balance": "预付费余额",
        "last_electricity_usage": "最新日用电量",
        "yearly_electricity_usage": "年度累计用电量",
        "yearly_electricity_charge": "年度累计电费",
        "month_electricity_usage": "上月用电量",
        "month_electricity_charge": "上月电费",
        "month_valley_usage": "上月谷时电量",
        "month_flat_usage": "上月平时电量",
        "month_peak_usage": "上月峰时电量",
        "month_tip_usage": "上月尖峰电量",
        "daily_electricity_usage": "每日用电量趋势",
        "last_water_usage_total": "上期总用水量",
        "last_water_usage_tap": "上期自来水用量",
        "last_water_usage_sewage": "上期污水量",
        "last_water_charge_total": "上期总水费",
        "last_water_charge_tap": "上期自来水费",
        "last_water_charge_sewage": "上期污水处理费",
        "water_meter_reading": "水表读数",
        "water_pay_status": "水费缴费状态",
        "gas_last_usage": "上期用气量",
        "gas_last_charge": "上期燃气费",
        "gas_yearly_usage": "燃气年用量",
        "gas_meter_reading": "燃气表读数",
        "gas_pay_status": "燃气缴费状态",
        "gas_next_read_date": "下次抄表日",
    }

    # 二开新增：水/气传感器的图标与友好名映射（不设置 icon 时 HA 默认显示小眼睛）
    _BILL_SENSOR_NAMES = {
        PDWLY_MONTHLY_USAGE_TOTAL_SENSOR_NAME: "威立雅本期总用量",
        PDWLY_MONTHLY_USAGE_WATER_SENSOR_NAME: "威立雅本期用水量",
        PDWLY_MONTHLY_USAGE_DIRTY_SENSOR_NAME: "威立雅本期污水量",
        PDWLY_MONTHLY_CHARGE_TOTAL_SENSOR_NAME: "威立雅本期总费用",
        PDWLY_MONTHLY_CHARGE_WATER_SENSOR_NAME: "威立雅本期水费",
        PDWLY_MONTHLY_CHARGE_DIRTY_SENSOR_NAME: "威立雅本期污水费",
        SHRQ_BALANCE_SENSOR_NAME: "燃气本期用量",
        SHRQ_MONTHLY_USAGE_SENSOR_NAME: "燃气本期费用",
        SHRQ_YEARLY_USAGE_SENSOR_NAME: "燃气年用量",
    }

    def _bill_sensor_meta(self, sensorName: str, sensorUnit: str) -> dict:
        # 命名历史交叉：SHRQ_BALANCE 实际承载用量(m³)、SHRQ_MONTHLY_USAGE 实际承载金额(CNY)，按单位判类型
        if sensorUnit == BALANCE_UNIT:
            icon, dc = "mdi:cash", "monetary"
        elif sensorName.startswith("sensor.gas_"):
            icon, dc = "mdi:meter-gas", "gas"
        else:
            icon, dc = "mdi:water", "water"
        base = sensorName.rsplit("_", 1)[0] if sensorName.rsplit("_", 1)[-1].isdigit() else sensorName
        return {"icon": icon, "device_class": dc, "state_class": "measurement",
                "friendly_name": self._BILL_SENSOR_NAMES.get(base, base)}

    # 二开新增：带完整 attributes 的传感器更新（浦东威立雅表底/缴费状态等）
    def update_attrs(self, sensorName: str, sensorState, attributes: dict):
        request_body = {
            "state": sensorState,
            "unique_id": sensorName,
            "attributes": attributes,
        }
        self.send_url(sensorName, request_body)
        logging.info(f"Home Assistant 传感器 {sensorName} 状态已更新: {sensorState}")

    # 二开新增：上海燃气 / 浦东威立雅传感器通用的更新入口
    def update(self, sensorName: str, present_date, sensorState: float, sensorUnit: str, month=False):
        """
        Update the sensor state
        :param sensorName: 此为id，不是name
        :param present_date: 主要用于确定最近一次用量所代表的日期
        :param sensorState: 传感器的状态
        :param sensorUnit: 传感器的单位
        """
        if month:
            last_updated = datetime.now().strftime("%Y-%m")
        else:
            last_updated = datetime.now().strftime("%Y-%m-%dT%H:%M:%S.%f%z")
        meta = self._bill_sensor_meta(sensorName, sensorUnit)
        if present_date:
            request_body = {
                "state": sensorState,
                "attributes": {
                    "present_date": present_date,
                    "unit_of_measurement": sensorUnit,
                    **meta
                }
            }
        else:
            request_body = {
                "state": sensorState,
                "unique_id": sensorName,
                "attributes": {
                    "unit_of_measurement": sensorUnit,
                    **meta
                }
            }
        url = self.base_url + API_PATH + sensorName  # /api/states/<entity_id>
        self.send_url(sensorName, request_body)
        logging.info(f"Homeassistant sensor {sensorName} state updated: {sensorState}{sensorUnit}")
