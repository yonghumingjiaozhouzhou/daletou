# -*- coding: utf-8 -*-
"""
自动更新大乐透开奖数据：抓取官方最新 60 期，与本地数据库增量合并入库。
数据源：中国竞彩网官方开奖历史接口（gameNo=85 大乐透）。
"""
from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import daletou_core as core
from fetch_real_data import fetch_official, validate_rows


def update_database() -> dict:
    """抓取官方最新 60 期并增量入库（跳过已存在期号）。

    返回 {"inserted": 新增期数, "latest": 官方最新期号, "checked": 检查时间}。
    网络失败或接口异常时抛出异常，由调用方提示。
    """
    core.ensure_dirs()
    conn = core.get_conn()
    try:
        existing = {str(r[0]) for r in conn.execute("SELECT issue FROM draws")}
    finally:
        conn.close()

    rows = fetch_official(2)  # 最新 2 页 = 最多 60 期
    good, _errors = validate_rows(rows)
    good.sort(key=lambda x: core.issue_key(x[0]))
    new_rows = [r for r in good if str(r[0]) not in existing]

    inserted = 0
    conn = core.get_conn()
    try:
        for issue, date, front, back in new_rows:
            ok, _ = core.insert_draw(issue, date, front, back, source="auto", conn=conn)
            if ok:
                inserted += 1
    finally:
        conn.close()

    latest = str(good[-1][0]) if good else ""
    return {
        "inserted": inserted,
        "latest": latest,
        "checked": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
