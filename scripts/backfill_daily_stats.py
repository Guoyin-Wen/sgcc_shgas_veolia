#!/usr/bin/env python3
"""一次性回填（二开新增）：把 sqlite daily{user_id} 表的每日用电量导入 HA 长期统计，
并注册每日趋势实体。幂等，可重复执行。
用法: docker exec sgcc_shgas_veolia python3 /app/backfill_daily_stats.py
"""
import json
import logging
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
logging.basicConfig(level=logging.INFO, format="%(asctime)s  [%(levelname)-7s] %(message)s")

from sensor_updator import SensorUpdator  # noqa: E402
from const import get_data_dir  # noqa: E402


def main():
    data_dir = get_data_dir()
    db_path = os.path.join(data_dir, os.getenv("DB_NAME", "homeassistant.db"))

    # 户号优先取缓存文件，兜底扫 daily% 表名
    user_ids = []
    cache = os.path.join(data_dir, "sgcc_cache.json")
    if os.path.exists(cache):
        try:
            user_ids = list(json.load(open(cache)).keys())
        except Exception as e:
            logging.warning(f"读取缓存失败: {e}")
    if not user_ids:
        conn = sqlite3.connect(db_path)
        user_ids = [t[0][5:] for t in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'daily%'"
        ).fetchall()]
        conn.close()
    logging.info(f"发现户号: {user_ids}")

    updator = SensorUpdator()
    conn = sqlite3.connect(db_path)
    for uid in user_ids:
        try:
            rows = [{"date": d, "total_usage": u} for d, u in conn.execute(
                f"SELECT date, usage FROM daily{uid} ORDER BY date").fetchall()]
        except sqlite3.OperationalError as e:
            logging.warning(f"[{uid}] 无 daily 表: {e}")
            continue
        rng = f"{rows[0]['date']} ~ {rows[-1]['date']}" if rows else "无数据"
        logging.info(f"[{uid}] sqlite 每日数据 {len(rows)} 条: {rng}")
        if rows:
            updator.update_daily_series(f"_{uid[-4:]}", rows)
    conn.close()
    logging.info("回填完成")


if __name__ == "__main__":
    main()
