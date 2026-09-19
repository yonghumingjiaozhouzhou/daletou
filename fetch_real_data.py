# -*- coding: utf-8 -*-
"""
抓取大乐透真实开奖历史数据（近 N 期）并可选导入本地数据库。
数据源：中国竞彩网官方开奖历史接口（webapi.sporttery.cn，gameNo=85 大乐透）。
用法：
  python fetch_real_data.py            # 仅抓取并保存 CSV + 打印统计
  python fetch_real_data.py --import   # 抓取并导入数据库（替换示例数据）
"""
from __future__ import annotations

import sys
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))
import daletou_core as core

HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"),
    "Accept-Language": "zh-CN,zh;q=0.9",
    "Referer": "https://www.sporttery.cn/",
}
API_URL = ("https://webapi.sporttery.cn/gateway/lottery/getHistoryPageListV1.qry"
           "?gameNo=85&provinceId=0&pageSize={ps}&isVerify=1&pageNo={pn}")
CSV_OUT = core.DATA_DIR / "real_dlt_history.csv"


def fetch_official(pages: int, page_size: int = 30) -> list[tuple]:
    """竞彩网官方接口：返回 [(期号, 日期, [前区5], [后区2]), ...]（时间倒序）。"""
    rows: list[tuple] = []
    for pn in range(1, pages + 1):
        url = API_URL.format(ps=page_size, pn=pn)
        r = requests.get(url, headers=HEADERS, timeout=40)
        r.raise_for_status()
        j = r.json()
        lst = j["value"]["list"]
        for it in lst:
            issue = str(it["lotteryDrawNum"]).strip()
            date = str(it["lotteryDrawTime"])[:10]
            nums = [int(x) for x in str(it["lotteryDrawResult"]).split()]
            if len(nums) != 7:
                continue
            rows.append((issue, date, nums[:5], nums[5:]))
        if len(lst) < page_size:
            break
    return rows


def validate_rows(rows: list[tuple]) -> tuple[list[tuple], list[str]]:
    """校验号码范围与去重，返回 (有效行, 错误信息)。"""
    seen: set[str] = set()
    good: list[tuple] = []
    errors: list[str] = []
    for issue, date, front, back in rows:
        ok, msg = core.validate_draw(issue, front, back)
        if not ok:
            errors.append(f"期号 {issue}: {msg}")
            continue
        if issue in seen:
            errors.append(f"期号 {issue} 重复")
            continue
        seen.add(issue)
        good.append((issue, date, front, back))
    return good, errors


def save_csv(rows: list[tuple], path: Path) -> None:
    lines = ["期号,开奖日期,前区号码,后区号码"]
    for issue, date, front, back in rows:
        f = " ".join(f"{n:02d}" for n in front)
        b = " ".join(f"{n:02d}" for n in back)
        lines.append(f'{issue},{date},"{f}","{b}"')
    path.write_text("\n".join(lines) + "\n", encoding="utf-8-sig")


def main() -> None:
    do_import = "--import" in sys.argv
    target = 300
    pages = (target + 29) // 30  # 每页 30 期
    rows = fetch_official(pages)
    if len(rows) < 50:
        print(f"官方接口抓取失败或数据不足（{len(rows)} 期），请检查网络。")
        sys.exit(1)

    good, errors = validate_rows(rows)
    # 按期号升序并取最新 target 期
    good.sort(key=lambda x: core.issue_key(x[0]))
    latest = good[-target:]
    print(f"抓取 {len(rows)} 期 → 校验有效 {len(good)} 期，错误 {len(errors)} 条（示例: {errors[:3]}）")
    print(f"覆盖区间：{latest[0][0]}（{latest[0][1]}）~ {latest[-1][0]}（{latest[-1][1]}），共 {len(latest)} 期")
    print("最新 3 期样例：")
    for issue, date, front, back in latest[-3:]:
        print(f"  {issue} {date} 前区 {' '.join(f'{n:02d}' for n in front)} 后区 {' '.join(f'{n:02d}' for n in back)}")

    core.ensure_dirs()
    save_csv(latest, CSV_OUT)
    print(f"CSV 已保存：{CSV_OUT}")

    if do_import:
        core.init_db()
        conn = core.get_conn()
        conn.execute("DELETE FROM draws")
        conn.commit()
        inserted = 0
        for issue, date, front, back in latest:
            ok, msg = core.insert_draw(issue, date, front, back, source="import", conn=conn)
            if ok:
                inserted += 1
            else:
                print(f"  导入失败 {issue}: {msg}")
        conn.close()
        print(f"数据库已替换为真实数据：{inserted} 期（示例数据已清除）")


if __name__ == "__main__":
    main()
