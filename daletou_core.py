# -*- coding: utf-8 -*-
"""
大乐透历史数据统计与号码生成工具 —— 核心逻辑模块（与 Streamlit UI 解耦，便于测试）。
所有数据统计、号码生成、导入导出逻辑均在本模块，UI 只做展示与交互。

玩法规则：
  前区：从 1-35 中选 5 个号码
  后区：从 1-12 中选 2 个号码
"""
from __future__ import annotations

import json
import os
import random
import re
import sqlite3
from collections import Counter
from datetime import date, timedelta
from io import BytesIO, StringIO
from math import comb
from pathlib import Path

import pandas as pd

# ---------------------------- 常量 ----------------------------
FRONT_MIN, FRONT_MAX, FRONT_COUNT = 1, 35, 5
BACK_MIN, BACK_MAX, BACK_COUNT = 1, 12, 2
ZONES = [(1, 12, "一区(1-12)"), (13, 24, "二区(13-24)"), (25, 35, "三区(25-35)")]
# 可视化配色：热号红 / 温号灰 / 冷号蓝
COLORS = {"hot": "#e74c3c", "warm": "#95a5a6", "cold": "#3498db"}
DEMO_ROWS = 150

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
DB_FILE = DATA_DIR / "lottery.db"
PREFS_FILE = DATA_DIR / "preferences.json"
SEED_FLAG = DATA_DIR / ".demo_seeded"

DEFAULT_PREFS = {
    "stat_mode": "50",   # "all" 或数字字符串
    "strategy": "纯随机",
    "hot_count": 2,
    "cold_count": 2,
    "odd": "不限制",
    "zone1": "不限制",
    "zone2": "不限制",
    "zone3": "不限制",
    "groups": 5,
}


# ---------------------------- 基础工具 ----------------------------
def active_db_path() -> Path:
    """当前数据库路径：环境变量 DALETOU_DB 优先（测试隔离用），否则用正式库。"""
    env = os.environ.get("DALETOU_DB")
    return Path(env) if env else DB_FILE


def active_data_dir() -> Path:
    env = os.environ.get("DALETOU_DB")
    return Path(env).parent if env else DATA_DIR


def ensure_dirs() -> None:
    active_data_dir().mkdir(parents=True, exist_ok=True)
    DATA_DIR.mkdir(parents=True, exist_ok=True)


def _init_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """CREATE TABLE IF NOT EXISTS draws(
            issue     TEXT PRIMARY KEY,
            draw_date TEXT,
            f1 INTEGER, f2 INTEGER, f3 INTEGER, f4 INTEGER, f5 INTEGER,
            b1 INTEGER, b2 INTEGER,
            source TEXT DEFAULT 'manual'
        )"""
    )
    conn.execute(
        """CREATE TABLE IF NOT EXISTS gen_history(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            gen_time TEXT NOT NULL,
            gen_issue TEXT,
            bet_type TEXT NOT NULL,
            front TEXT NOT NULL,
            back TEXT NOT NULL,
            f_dan TEXT DEFAULT '',
            b_dan TEXT DEFAULT '',
            notes INTEGER DEFAULT 1,
            amount REAL DEFAULT 2.0
        )"""
    )
    conn.execute(
        """CREATE TABLE IF NOT EXISTS tz_history(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            gen_time TEXT NOT NULL,
            gen_issue TEXT,
            group_no INTEGER NOT NULL,
            front TEXT NOT NULL,
            back TEXT NOT NULL
        )"""
    )
    conn.commit()


def get_conn() -> sqlite3.Connection:
    ensure_dirs()
    conn = sqlite3.connect(active_db_path(), check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL")
    _init_schema(conn)
    return conn


def init_db(conn: sqlite3.Connection | None = None) -> None:
    own = conn is None
    conn = conn or get_conn()
    _init_schema(conn)
    if own:
        conn.close()


def issue_key(issue) -> int | str:
    """期号排序键：纯数字按期号数值排，否则按字符串排。"""
    s = str(issue).strip()
    return int(s) if s.isdigit() else s


def next_issue(issue) -> str:
    """下一期号：纯数字期号 +1；其他形式原样返回。"""
    s = str(issue).strip()
    return str(int(s) + 1) if s.isdigit() else s


def parse_numbers(text) -> list[int]:
    """从文本中提取所有整数，兼容空格 / 逗号 / 顿号等分隔。"""
    if text is None:
        return []
    return [int(x) for x in re.split(r"[^\d]+", str(text).strip()) if x != ""]


def _to_int(v) -> int | None:
    """将单元格值转为 int，兼容 '01'、'1.0'、float(1.0) 等写法。"""
    if v is None:
        return None
    s = str(v).strip()
    if s == "":
        return None
    try:
        return int(s)
    except ValueError:
        try:
            return int(float(s))
        except (ValueError, TypeError):
            return None


def front_list(row) -> list[int]:
    return [int(row[f"f{i}"]) for i in range(1, FRONT_COUNT + 1)]


def back_list(row) -> list[int]:
    return [int(row[f"b{i}"]) for i in range(1, BACK_COUNT + 1)]


def fmt_front(row) -> str:
    return " ".join(f"{n:02d}" for n in front_list(row))


def fmt_back(row) -> str:
    return " ".join(f"{n:02d}" for n in back_list(row))


# ---------------------------- 数据读写 ----------------------------
def load_draws(conn: sqlite3.Connection | None = None) -> pd.DataFrame:
    """读取全部开奖记录，按期号升序排列。"""
    own = conn is None
    conn = conn or get_conn()
    df = pd.read_sql_query(
        "SELECT issue, draw_date, f1,f2,f3,f4,f5,b1,b2, source FROM draws", conn
    )
    if own:
        conn.close()
    if df.empty:
        return df
    df["issue"] = df["issue"].astype(str)
    df["sort_key"] = df["issue"].apply(issue_key)
    df = df.sort_values("sort_key").reset_index(drop=True)
    return df


def validate_draw(issue, front, back) -> tuple[bool, str]:
    """校验单条记录合法性。返回 (ok, 错误信息)。"""
    if str(issue).strip() == "":
        return False, "期号不能为空"
    if len(front) != FRONT_COUNT:
        return False, f"前区需 {FRONT_COUNT} 个号码，当前 {len(front)} 个"
    if len(back) != BACK_COUNT:
        return False, f"后区需 {BACK_COUNT} 个号码，当前 {len(back)} 个"
    for n in front:
        if not (FRONT_MIN <= int(n) <= FRONT_MAX):
            return False, f"前区号码 {n} 超出范围 {FRONT_MIN}-{FRONT_MAX}"
    for n in back:
        if not (BACK_MIN <= int(n) <= BACK_MAX):
            return False, f"后区号码 {n} 超出范围 {BACK_MIN}-{BACK_MAX}"
    return True, ""


def insert_draw(issue, draw_date, front, back, source: str = "manual",
                conn: sqlite3.Connection | None = None) -> tuple[bool, str]:
    """插入一条开奖记录；期号重复时拒绝。返回 (ok, 消息)。"""
    ok, msg = validate_draw(issue, front, back)
    if not ok:
        return False, msg
    own = conn is None
    conn = conn or get_conn()
    front_s = sorted(int(n) for n in front)
    back_s = sorted(int(n) for n in back)
    cur = conn.execute(
        "INSERT OR IGNORE INTO draws(issue, draw_date, f1,f2,f3,f4,f5,b1,b2,source) "
        "VALUES(?,?,?,?,?,?,?,?,?,?)",
        (str(issue).strip(), str(draw_date).strip(), *front_s, *back_s, source),
    )
    conn.commit()
    if own:
        conn.close()
    if cur.rowcount == 0:
        return False, f"期号 {issue} 已存在，未重复入库"
    return True, "ok"


def clear_db(conn: sqlite3.Connection | None = None) -> None:
    own = conn is None
    conn = conn or get_conn()
    conn.execute("DELETE FROM draws")
    conn.commit()
    if own:
        conn.close()


# ---------------------------- 示例数据 ----------------------------
def _demo_rows(n: int = DEMO_ROWS) -> list[tuple]:
    """生成 n 条随机示例开奖（约每周一/三/六，往回推），期号 26001 起。"""
    rng = random.Random(20260916)
    dates: list[date] = []
    d = date(2026, 9, 14)  # 周一
    while len(dates) < n:
        if d.weekday() in (0, 2, 5):  # Mon / Wed / Sat
            dates.append(d)
        d -= timedelta(days=1)
    dates.reverse()
    rows = []
    for i, dd in enumerate(dates, 1):
        front = sorted(rng.sample(range(FRONT_MIN, FRONT_MAX + 1), FRONT_COUNT))
        back = sorted(rng.sample(range(BACK_MIN, BACK_MAX + 1), BACK_COUNT))
        rows.append((f"26{i:03d}", dd.isoformat(), *front, *back))
    return rows


def ensure_demo_once(conn: sqlite3.Connection | None = None) -> bool:
    """首次运行且数据库为空时写入示例数据（只执行一次，清库后不再自动生成）。"""
    ensure_dirs()
    if (active_data_dir() / SEED_FLAG.name).exists():
        return False
    own = conn is None
    conn = conn or get_conn()
    cnt = conn.execute("SELECT COUNT(*) FROM draws").fetchone()[0]
    seeded = False
    if cnt == 0:
        rows = _demo_rows()
        conn.executemany(
            "INSERT INTO draws(issue, draw_date, f1,f2,f3,f4,f5,b1,b2,source) "
            "VALUES(?,?,?,?,?,?,?,?,?,?)",
            [(*r, "demo") for r in rows],
        )
        seeded = True
    conn.commit()
    (active_data_dir() / SEED_FLAG.name).write_text("1", encoding="utf-8")
    if own:
        conn.close()
    return seeded


# ---------------------------- 导入 / 模板 ----------------------------
def _find_col(df: pd.DataFrame, aliases: list[str], pattern: str | None = None):
    """按别名（忽略大小写与首尾空格）或正则查找列名，返回原列名或 None。"""
    low_map = {str(c).strip().lower(): c for c in df.columns}
    for a in aliases:
        if a.lower() in low_map:
            return low_map[a.lower()]
    if pattern:
        rx = re.compile(pattern, re.IGNORECASE)
        for c in df.columns:
            if rx.match(str(c).strip()):
                return c
    return None


def _row_front_back(row: pd.Series, front_str_col, back_str_col,
                    front_num_cols, back_num_cols, combined_col):
    """从一行数据中解析出 (front, back)；解析失败返回 (None, None)。"""
    front: list[int] | None = None
    back: list[int] | None = None
    if front_str_col and back_str_col:
        f = parse_numbers(row.get(front_str_col, ""))
        b = parse_numbers(row.get(back_str_col, ""))
        if f and b:
            front, back = f, b
    if front is None and front_num_cols:
        f = [_to_int(row.get(c)) for c in front_num_cols]
        if all(v is not None for v in f):
            front = [int(v) for v in f]
        b = [_to_int(row.get(c)) for c in back_num_cols]
        if all(v is not None for v in b):
            back = [int(v) for v in b]
    if front is None and combined_col:
        nums = parse_numbers(row.get(combined_col, ""))
        if len(nums) == FRONT_COUNT + BACK_COUNT:
            front, back = nums[:FRONT_COUNT], nums[FRONT_COUNT:]
    return front, back


def import_draws(file_bytes: bytes, filename: str) -> dict:
    """从 CSV / Excel 导入开奖记录。
    自动校验：重复期号跳过、号码范围非法报错。
    返回 {total, inserted, skipped, errors:[...]}。
    """
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    try:
        if ext in ("xlsx", "xls"):
            df = pd.read_excel(BytesIO(file_bytes), dtype=str)
        else:
            df = pd.read_csv(BytesIO(file_bytes), dtype=str)
    except Exception as e:  # noqa: BLE001
        return {"total": 0, "inserted": 0, "skipped": 0, "errors": [f"文件解析失败：{e}"]}
    df = df.fillna("")
    df.columns = [str(c).strip() for c in df.columns]
    if df.empty:
        return {"total": 0, "inserted": 0, "skipped": 0, "errors": ["文件为空"]}

    issue_col = _find_col(df, ["期号", "开奖期号", "期次", "issue", "期", "序号"])
    date_col = _find_col(df, ["开奖日期", "开奖时间", "日期", "draw_date", "date"])
    front_str_col = _find_col(df, ["前区号码", "前区", "前区号", "前区开奖号码", "front_numbers", "front"])
    back_str_col = _find_col(df, ["后区号码", "后区", "后区号", "后区开奖号码", "back_numbers", "back"])
    combined_col = _find_col(df, ["开奖号码", "开奖号", "号码", "numbers", "开奖号码(7个)"])
    front_num_cols = None
    back_num_cols = None
    if front_str_col is None:
        c1 = _find_col(df, [], r"^前区\s*[1-5]$")
        c2 = _find_col(df, [], r"^(f|front)\s*[1-5]$")
        fc = c1 or c2
        if fc is not None:
            front_num_cols = [fc]
            for i in range(2, FRONT_COUNT + 1):
                c1 = _find_col(df, [], rf"^前区\s*{i}$")
                c2 = _find_col(df, [], rf"^(f|front)\s*{i}$")
                front_num_cols.append(c1 or c2)
            c1 = _find_col(df, [], r"^后区\s*[1-2]$")
            c2 = _find_col(df, [], r"^(b|back)\s*[1-2]$")
            bc = c1 or c2
            if bc is not None:
                back_num_cols = [bc]
                for i in range(2, BACK_COUNT + 1):
                    c1 = _find_col(df, [], rf"^后区\s*{i}$")
                    c2 = _find_col(df, [], rf"^(b|back)\s*{i}$")
                    back_num_cols.append(c1 or c2)
    if issue_col is None:
        return {"total": len(df), "inserted": 0, "skipped": 0,
                "errors": ["未找到期号列（支持的列名：期号 / issue / 序号 等）"]}

    conn = get_conn()
    existing = set(pd.read_sql_query("SELECT issue FROM draws", conn)["issue"].astype(str))
    inserted, skipped = 0, 0
    errors: list[str] = []
    seen: set[str] = set()
    for idx, row in df.iterrows():
        issue = str(row.get(issue_col, "")).strip()
        if issue == "":
            errors.append(f"第{idx + 2}行：期号为空")
            continue
        if issue in existing or issue in seen:
            skipped += 1
            continue
        ddate = str(row.get(date_col, "")) if date_col else ""
        front, back = _row_front_back(row, front_str_col, back_str_col,
                                      front_num_cols, back_num_cols, combined_col)
        if front is None or back is None:
            errors.append(f"第{idx + 2}行：无法解析号码列（前区5个 + 后区2个）")
            continue
        ok, msg = validate_draw(issue, front, back)
        if not ok:
            errors.append(f"第{idx + 2}行：{msg}")
            continue
        insert_draw(issue, ddate, front, back, source="import", conn=conn)
        existing.add(issue)
        seen.add(issue)
        inserted += 1
    conn.close()
    if len(errors) > 30:
        errors = errors[:30] + [f"…另有 {len(errors) - 30} 条错误"]
    return {"total": len(df), "inserted": inserted, "skipped": skipped, "errors": errors}


def template_csv_bytes() -> bytes:
    """导入模板（UTF-8 with BOM，Excel 可直接打开）。"""
    buf = StringIO()
    buf.write("期号,开奖日期,前区号码,后区号码\n")
    buf.write('90001,2026-09-14,"01 05 12 23 34","03 08"\n')
    buf.write('90002,2026-09-12,"02 07 15 28 33","01 11"\n')
    return buf.getvalue().encode("utf-8-sig")


# ---------------------------- 统计计算 ----------------------------
def select_window(draws: pd.DataFrame, n: int | None = None) -> pd.DataFrame:
    """按期号升序取最新 n 期；n=None 或超出范围则取全部。"""
    if n is None or len(draws) <= n:
        return draws.reset_index(drop=True)
    return draws.tail(n).reset_index(drop=True)


def front_freq(draws: pd.DataFrame) -> pd.DataFrame:
    """前区 1-35 出现频次。列：number, count。"""
    nums = []
    for i in range(len(draws)):
        nums.extend(front_list(draws.iloc[i]))
    if not nums:
        return pd.DataFrame({"number": range(FRONT_MIN, FRONT_MAX + 1), "count": 0})
    s = pd.Series(nums)
    return (s.value_counts()
             .reindex(range(FRONT_MIN, FRONT_MAX + 1), fill_value=0)
             .rename_axis("number").reset_index(name="count"))


def back_freq(draws: pd.DataFrame) -> pd.DataFrame:
    """后区 1-12 出现频次。列：number, count。"""
    nums = []
    for i in range(len(draws)):
        nums.extend(back_list(draws.iloc[i]))
    if not nums:
        return pd.DataFrame({"number": range(BACK_MIN, BACK_MAX + 1), "count": 0})
    s = pd.Series(nums)
    return (s.value_counts()
             .reindex(range(BACK_MIN, BACK_MAX + 1), fill_value=0)
             .rename_axis("number").reset_index(name="count"))


def classify_hot_warm_cold(freq_df: pd.DataFrame) -> pd.DataFrame:
    """按频次三等分：前 1/3 热号、中 1/3 温号、后 1/3 冷号。返回带 category 列的副本。"""
    df = freq_df.sort_values(["count", "number"], ascending=[False, True]).reset_index(drop=True)
    n = len(df)
    df["category"] = "warm"
    if n <= 2:
        df["category"] = "warm"
        return df
    hot_n = max(1, round(n / 3))
    cold_n = max(1, round(n / 3))
    df.loc[: hot_n - 1, "category"] = "hot"
    df.loc[n - cold_n:, "category"] = "cold"
    return df


def odd_even_dist(draws: pd.DataFrame) -> dict[int, int]:
    """前区奇数个数分布：{0..5: 期数}。"""
    counts = {k: 0 for k in range(6)}
    for i in range(len(draws)):
        odd = sum(1 for n in front_list(draws.iloc[i]) if n % 2 == 1)
        counts[odd] += 1
    return counts


def zone_counts(front: list[int]) -> list[int]:
    """前区三区出号数量 [一区, 二区, 三区]。"""
    return [sum(1 for n in front if lo <= n <= hi) for lo, hi, _ in ZONES]


def zone_dist(draws: pd.DataFrame) -> dict[str, list[int]]:
    """三区出号数量分布：{区名: [出0个的期数, 出1个的期数, ...]}。"""
    data = {zname: [0] * 6 for _, _, zname in ZONES}
    for i in range(len(draws)):
        zc = zone_counts(front_list(draws.iloc[i]))
        for j, (_, _, zname) in enumerate(ZONES):
            data[zname][zc[j]] += 1
    return data


def front_sums(draws: pd.DataFrame) -> list[int]:
    return [sum(front_list(draws.iloc[i])) for i in range(len(draws))]


def max_run(nums: list[int]) -> int:
    """一组号码中的最大连号长度（无连号时为 1）。"""
    s = sorted(set(nums))
    if not s:
        return 1
    best = cur = 1
    for a, b in zip(s, s[1:]):
        cur = cur + 1 if b == a + 1 else 1
        best = max(best, cur)
    return best


def consecutive_stats(draws: pd.DataFrame) -> tuple[dict[str, int], int]:
    """连号统计：按每期最大连号归类（无连号 / 二连号 / 三连号 / 四连号及以上），返回统计与全局最长连号。"""
    stats = {"无连号": 0, "二连号": 0, "三连号": 0, "四连号及以上": 0}
    max_len = 0
    for i in range(len(draws)):
        m = max_run(front_list(draws.iloc[i]))
        max_len = max(max_len, m)
        if m == 1:
            stats["无连号"] += 1
        elif m == 2:
            stats["二连号"] += 1
        elif m == 3:
            stats["三连号"] += 1
        else:
            stats["四连号及以上"] += 1
    return stats, max_len


def repeat_stats(draws: pd.DataFrame) -> tuple[dict[int, int], dict[int, int], int]:
    """上期重号统计：每期与上一期的重复号码个数分布。
    返回 (前区分布{0..5}, 后区分布{0..2}, 相邻期数对数量)。
    """
    front_cnt = {k: 0 for k in range(6)}
    back_cnt = {k: 0 for k in range(3)}
    pairs = max(0, len(draws) - 1)
    for i in range(1, len(draws)):
        pf, cf = set(front_list(draws.iloc[i - 1])), set(front_list(draws.iloc[i]))
        front_cnt[len(pf & cf)] += 1
        pb, cb = set(back_list(draws.iloc[i - 1])), set(back_list(draws.iloc[i]))
        back_cnt[len(pb & cb)] += 1
    return front_cnt, back_cnt, pairs


def omission_stats(draws: pd.DataFrame) -> dict[int, int]:
    """遗漏值：每个前区号码距离上次开出的间隔期数（最近一期开出为 0；从未开出记为全部期数）。"""
    n = len(draws)
    last: dict[int, int] = {}
    for i in range(n):
        for num in front_list(draws.iloc[i]):
            last[num] = i
    return {num: (n - 1 - last[num]) if num in last else n
            for num in range(FRONT_MIN, FRONT_MAX + 1)}


def summary_metrics(draws: pd.DataFrame) -> dict:
    """汇总指标：平均和值 / 连号出现率 / 最长连号 / 前区重号概率 / 后区重号概率。"""
    total = len(draws)
    sums = front_sums(draws)
    avg = float(pd.Series(sums).mean()) if sums else 0.0
    cons, max_len = consecutive_stats(draws)
    rate = (cons["二连号"] + cons["三连号"] + cons["四连号及以上"]) / total if total else 0.0
    fr, bk, pairs = repeat_stats(draws)
    f_rep = sum(k * v for k, v in fr.items()) / (FRONT_COUNT * pairs) if pairs else 0.0
    b_rep = sum(k * v for k, v in bk.items()) / (BACK_COUNT * pairs) if pairs else 0.0
    return {
        "avg_sum": avg,
        "consec_rate": rate,
        "max_run": max_len,
        "front_repeat": f_rep,
        "back_repeat": b_rep,
    }


def sum_hist_df(draws: pd.DataFrame, bins: int = 12) -> pd.DataFrame:
    """和值分布（用于导出报表）。列：和值区间, 期数。"""
    sums = front_sums(draws)
    if not sums:
        return pd.DataFrame(columns=["和值区间", "期数"])
    s = pd.Series(sums)
    lo, hi = int(s.min()), int(s.max())
    b = bins if lo != hi else 1
    binned = pd.cut(s, bins=b)
    cnt = binned.value_counts().sort_index()
    return pd.DataFrame({"和值区间": [str(i) for i in cnt.index], "期数": cnt.values})


# ---------------------------- 号码生成 ----------------------------
def _weighted_sample(rng: random.Random, population: list, k: int, weights: list[float]) -> list | None:
    """按权重不放回抽取 k 个。"""
    pop, w = list(population), list(weights)
    chosen = []
    for _ in range(k):
        total = sum(w)
        if total <= 0:
            return None
        r = rng.random() * total
        acc = 0.0
        picked = None
        for i, wi in enumerate(w):
            acc += wi
            if r <= acc:
                picked = i
                break
        if picked is None:
            picked = len(w) - 1
        chosen.append(pop.pop(picked))
        w.pop(picked)
    return chosen


def _hot_warm_cold_sets(freq: dict[int, int]) -> tuple[set, set, set]:
    """按频次三等分返回 (热号集, 温号集, 冷号集)。"""
    df = classify_hot_warm_cold(pd.DataFrame({"number": list(freq), "count": list(freq.values())}))
    return (set(df[df["category"] == "hot"]["number"]),
            set(df[df["category"] == "warm"]["number"]),
            set(df[df["category"] == "cold"]["number"]))


def _build_weights(freq: dict[int, int], strategy: str) -> dict[int, float]:
    if strategy == "热号优先":
        return {num: (c + 1) ** 2 for num, c in freq.items()}
    if strategy == "冷号优先":
        return {num: 1.0 / (c + 1) for num, c in freq.items()}
    if strategy == "温号优先":
        warm = _hot_warm_cold_sets(freq)[1]
        return {num: ((c + 1) ** 2 if num in warm else 1.0) for num, c in freq.items()}
    if strategy in ("冷热混合", "冷热温混合"):
        hot, warm, cold = _hot_warm_cold_sets(freq)
        out: dict[int, float] = {}
        for num, c in freq.items():
            if num in hot:
                out[num] = (c + 1) ** 2
            elif num in warm:
                out[num] = c + 1
            elif num in cold:
                out[num] = 1.0 / (c + 1)
            else:
                out[num] = 1.0
        return out
    return {num: 1.0 for num in freq}  # 纯随机


def generate_group(draws: pd.DataFrame, strategy: str, hot_count: int, cold_count: int,
                   warm_count: int, odd_target: int | None, zone_target: dict[int, int | None],
                   rng: random.Random, max_attempts: int = 3000) -> dict | None:
    """生成一组满足约束的号码；重试 max_attempts 次仍不满足返回 None。"""
    freq_df = front_freq(draws)
    freq = freq_df.set_index("number")["count"].to_dict()
    cls = classify_hot_warm_cold(freq_df)
    hot_set = set(cls[cls["category"] == "hot"]["number"])
    warm_set = set(cls[cls["category"] == "warm"]["number"])
    cold_set = set(cls[cls["category"] == "cold"]["number"])
    all_nums = list(range(FRONT_MIN, FRONT_MAX + 1))

    for _ in range(max_attempts):
        if strategy in ("冷热混合", "冷热温混合"):
            picks: list[int] = []
            if hot_count:
                hs = sorted(hot_set)
                h = _weighted_sample(rng, hs, hot_count, [(freq[n] + 1) ** 2 for n in hs])
                picks.extend(h or [])
            if warm_count:
                ws = sorted(warm_set)
                w = _weighted_sample(rng, ws, warm_count, [(freq[n] + 1) ** 2 for n in ws])
                picks.extend(w or [])
            if cold_count:
                cs = sorted(cold_set)
                c = _weighted_sample(rng, cs, cold_count, [1.0 / (freq[n] + 1) for n in cs])
                picks.extend(c or [])
            need = FRONT_COUNT - len(picks)
            if need > 0:
                pool = [n for n in all_nums if n not in picks]
                picks.extend(rng.sample(pool, need))
            front = picks
        else:
            weights = _build_weights(freq, strategy)
            front = _weighted_sample(rng, all_nums, FRONT_COUNT, [weights[n] for n in all_nums])
            if front is None:
                return None

        if odd_target is not None and sum(1 for n in front if n % 2 == 1) != odd_target:
            continue
        if zone_target:
            zc = zone_counts(front)
            ok_zone = True
            for i, t in zone_target.items():
                if t is not None and zc[i] != t:
                    ok_zone = False
                    break
            if not ok_zone:
                continue
        back = sorted(rng.sample(range(BACK_MIN, BACK_MAX + 1), BACK_COUNT))
        front = sorted(front)
        return {
            "front": front,
            "back": back,
            "odd": sum(1 for n in front if n % 2 == 1),
            "zones": zone_counts(front),
            "sum": sum(front),
            "front_str": " ".join(f"{n:02d}" for n in front),
            "back_str": " ".join(f"{n:02d}" for n in back),
        }
    return None


def generate_groups(draws: pd.DataFrame, strategy: str, hot_count: int, cold_count: int,
                    warm_count: int, odd_target: int | None, zone_target: dict[int, int | None],
                    n_groups: int, rng: random.Random | None = None) -> tuple[list[dict], int]:
    """批量生成号码组合。返回 (groups, 失败组数)。参数不合法时抛 ValueError。"""
    if strategy in ("冷热混合", "冷热温混合") and hot_count + cold_count + warm_count > FRONT_COUNT:
        raise ValueError(f"混合策略时，热号数 + 温号数 + 冷号数不能超过 {FRONT_COUNT}")
    if odd_target is not None and not (0 <= odd_target <= FRONT_COUNT):
        raise ValueError("前区奇数个数需在 0-5 之间")
    if zone_target:
        vals = [v for v in zone_target.values() if v is not None]
        if len(vals) == 3 and sum(vals) != FRONT_COUNT:
            raise ValueError(f"三区出号数量总和必须等于 {FRONT_COUNT}，当前为 {sum(vals)}")
    rng = rng or random.Random()
    groups: list[dict] = []
    failed = 0
    for _ in range(max(1, n_groups)):
        g = generate_group(draws, strategy, hot_count, cold_count, warm_count,
                           odd_target, zone_target, rng)
        if g is None:
            failed += 1
            continue
        groups.append(g)
    return groups, failed


# ---------------------------- 复式 / 胆拖 / 单式转化 ----------------------------
def nCr(n: int, k: int) -> int:
    """组合数 C(n, k)。"""
    return comb(n, k) if 0 <= k <= n else 0


def _pool_weights(draws: pd.DataFrame, strategy: str, zone: str) -> tuple[dict, list[float]]:
    """返回 (频次映射, 按号码升序的权重列表)。zone='front'/'back'。"""
    if zone == "front":
        freq = front_freq(draws).set_index("number")["count"].to_dict()
        w = _build_weights(freq, strategy)
        return freq, [w[n] for n in range(FRONT_MIN, FRONT_MAX + 1)]
    freq = back_freq(draws).set_index("number")["count"].to_dict()
    w = _build_weights(freq, strategy)
    return freq, [w[n] for n in range(BACK_MIN, BACK_MAX + 1)]


def _check_pool_constraints(pool: list[int], odd_target: int | None,
                            zone_target: dict[int, int | None]) -> bool:
    """复式/胆拖模式下，奇偶与三区约束作用于号码集合整体。"""
    if odd_target is not None and sum(1 for n in pool if n % 2 == 1) != odd_target:
        return False
    if zone_target:
        zc = zone_counts(pool)
        for i, t in zone_target.items():
            if t is not None and zc[i] != t:
                return False
    return True


def generate_fushi(draws: pd.DataFrame, strategy: str, front_n: int, back_n: int,
                   odd_target: int | None = None, zone_target: dict[int, int | None] | None = None,
                   rng: random.Random | None = None, max_attempts: int = 3000) -> dict | None:
    """生成一张复式：前区选 front_n(6-18)，后区选 back_n(3-12)。
    注数 = C(front_n,5) × C(back_n,2)；金额 = 注数 × 2 元。约束作用于号码集合整体。
    """
    if not (6 <= front_n <= 18):
        raise ValueError("前区复式需选 6-18 个号码")
    if not (3 <= back_n <= 12):
        raise ValueError("后区复式需选 3-12 个号码")
    _, wf = _pool_weights(draws, strategy, "front")
    _, wb = _pool_weights(draws, strategy, "back")
    rng = rng or random.Random()
    all_f = list(range(FRONT_MIN, FRONT_MAX + 1))
    all_b = list(range(BACK_MIN, BACK_MAX + 1))
    for _ in range(max_attempts):
        front = _weighted_sample(rng, all_f, front_n, wf)
        if front is None or not _check_pool_constraints(front, odd_target, zone_target):
            continue
        back = _weighted_sample(rng, all_b, back_n, wb)
        if back is None:
            continue
        notes = nCr(front_n, FRONT_COUNT) * nCr(back_n, BACK_COUNT)
        return {
            "front": sorted(front), "back": sorted(back),
            "front_str": " ".join(f"{n:02d}" for n in sorted(front)),
            "back_str": " ".join(f"{n:02d}" for n in sorted(back)),
            "notes": notes, "amount": notes * 2.0,
        }
    return None


def generate_dantuo(draws: pd.DataFrame, strategy: str, f_dan: int, f_tuo: int,
                    b_dan: int, b_tuo: int, odd_target: int | None = None,
                    zone_target: dict[int, int | None] | None = None,
                    rng: random.Random | None = None, max_attempts: int = 3000) -> dict | None:
    """生成一张胆拖：前区胆 f_dan(0-4，0=无胆全拖) + 拖 f_tuo；后区胆 b_dan(0/1) + 拖 b_tuo。
    注数 = C(f_tuo, 5-f_dan) × C(b_tuo, 2-b_dan)；金额 = 注数 × 2 元。
    """
    if not (0 <= f_dan <= 4):
        raise ValueError("前区胆码需 0-4 个")
    if f_dan + f_tuo > FRONT_MAX:
        raise ValueError("前区胆码+拖码不能超过 35")
    if f_tuo < FRONT_COUNT - f_dan:
        raise ValueError(f"前区拖码至少 {FRONT_COUNT - f_dan} 个")
    if f_dan > 0 and f_dan + f_tuo < 6:
        raise ValueError("前区胆码+拖码总数至少 6 个")
    if b_dan not in (0, 1):
        raise ValueError("后区胆码需 0 或 1 个")
    if b_dan == 1 and b_tuo < 2:
        raise ValueError("后区有胆码时拖码至少 2 个")
    if b_dan == 0 and b_tuo < 2:
        raise ValueError("后区无胆（全拖）时至少选 2 个拖码")
    if b_dan + b_tuo > BACK_MAX:
        raise ValueError("后区胆码+拖码不能超过 12")
    _, wf = _pool_weights(draws, strategy, "front")
    _, wb = _pool_weights(draws, strategy, "back")
    rng = rng or random.Random()
    all_f = list(range(FRONT_MIN, FRONT_MAX + 1))
    all_b = list(range(BACK_MIN, BACK_MAX + 1))
    for _ in range(max_attempts):
        dan = _weighted_sample(rng, all_f, f_dan, wf)
        if dan is None:
            return None
        tuo_pool = [n for n in all_f if n not in dan]
        tuo = _weighted_sample(rng, tuo_pool, f_tuo, [wf[n - FRONT_MIN] for n in tuo_pool])
        if tuo is None or not _check_pool_constraints(dan + tuo, odd_target, zone_target):
            continue
        if b_dan == 1:
            bd = _weighted_sample(rng, all_b, 1, wb)
            if bd is None:
                continue
            btuo_pool = [n for n in all_b if n not in bd]
            btuo = _weighted_sample(rng, btuo_pool, b_tuo, [wb[n - BACK_MIN] for n in btuo_pool])
            if btuo is None:
                continue
        else:
            bd, btuo = [], _weighted_sample(rng, all_b, b_tuo, wb)
            if btuo is None:
                continue
        notes = nCr(f_tuo, FRONT_COUNT - f_dan) * nCr(b_tuo, BACK_COUNT - b_dan)
        return {
            "f_dan": sorted(dan), "f_tuo": sorted(tuo),
            "b_dan": sorted(bd), "b_tuo": sorted(btuo),
            "f_dan_str": " ".join(f"{n:02d}" for n in sorted(dan)),
            "f_tuo_str": " ".join(f"{n:02d}" for n in sorted(tuo)),
            "b_dan_str": " ".join(f"{n:02d}" for n in sorted(bd)) or "无",
            "b_tuo_str": " ".join(f"{n:02d}" for n in sorted(btuo)),
            "notes": notes, "amount": notes * 2.0,
        }
    return None


def single_to_fushi(groups: list[dict]) -> dict:
    """把已生成的 2 注以上单式合并去重，转化为一张复式。"""
    front = sorted({n for g in groups for n in g["front"]})
    back = sorted({n for g in groups for n in g["back"]})
    notes = nCr(len(front), FRONT_COUNT) * nCr(len(back), BACK_COUNT)
    return {
        "front": front, "back": back,
        "front_str": " ".join(f"{n:02d}" for n in front),
        "back_str": " ".join(f"{n:02d}" for n in back),
        "notes": notes, "amount": notes * 2.0,
    }


def single_to_dantuo(groups: list[dict], f_dan_count: int = 2, b_dan: int = 1) -> dict:
    """把已生成的 2 注以上单式转化为胆拖：按出现频次取前区胆码，其余为拖码；后区取最高频 1 码作胆（可全拖）。"""
    if len(groups) < 2:
        raise ValueError("至少需要 2 注单式才能转化")
    if not (0 <= f_dan_count <= 4):
        raise ValueError("前区胆码需 0-4 个")
    fc = Counter(n for g in groups for n in g["front"])
    bc = Counter(n for g in groups for n in g["back"])
    f_dan = sorted(n for n, _ in sorted(fc.items(), key=lambda kv: (-kv[1], kv[0]))[:f_dan_count])
    f_tuo = sorted(set(fc) - set(f_dan))
    if b_dan:
        top = sorted(bc.items(), key=lambda kv: (-kv[1], kv[0]))[0][0]
        b_dan_l = [top]
        b_tuo = sorted(set(bc) - set(b_dan_l))
    else:
        b_dan_l = []
        b_tuo = sorted(bc)
    if len(f_tuo) < FRONT_COUNT - len(f_dan):
        raise ValueError(f"合并去重后前区拖码不足 {FRONT_COUNT - len(f_dan)} 个")
    if f_dan and len(f_dan) + len(f_tuo) < 6:
        raise ValueError("合并去重后前区号码不足 6 个，无法组成胆拖（请生成更多注数）")
    if b_dan and len(b_tuo) < 2:
        raise ValueError("合并去重后后区拖码不足 2 个")
    if not b_dan and len(b_tuo) < 2:
        raise ValueError("合并去重后后区号码不足 2 个（无胆全拖至少 2 个）")
    notes = nCr(len(f_tuo), FRONT_COUNT - len(f_dan)) * nCr(len(b_tuo), BACK_COUNT - len(b_dan_l))
    return {
        "f_dan": f_dan, "f_tuo": f_tuo, "b_dan": b_dan_l, "b_tuo": b_tuo,
        "f_dan_str": " ".join(f"{n:02d}" for n in f_dan),
        "f_tuo_str": " ".join(f"{n:02d}" for n in f_tuo),
        "b_dan_str": " ".join(f"{n:02d}" for n in b_dan_l) or "无",
        "b_tuo_str": " ".join(f"{n:02d}" for n in b_tuo),
        "notes": notes, "amount": notes * 2.0,
    }


def ticket_amount_text(notes: int, copies: int = 1) -> str:
    """金额文本：如「2,640 注 × 2 元 = 5,280.00 元」。"""
    total = notes * copies * 2.0
    base = f"{notes:,} 注 × 2 元 = {notes * 2.0:,.2f} 元"
    if copies > 1:
        base += f"，共 {copies} 张，总金额 {total:,.2f} 元"
    return base


# ---------------------------- 后区分析与走势 ----------------------------
def back_odd_dist(draws: pd.DataFrame) -> dict[int, int]:
    """后区奇数个数分布：{0,1,2: 期数}。"""
    counts = {k: 0 for k in range(3)}
    for i in range(len(draws)):
        counts[sum(1 for n in back_list(draws.iloc[i]) if n % 2 == 1)] += 1
    return counts


def back_size_dist(draws: pd.DataFrame) -> dict[int, int]:
    """后区大小分布（小号 1-6 / 大号 7-12）个数分布：{0,1,2: 期数}。"""
    counts = {k: 0 for k in range(3)}
    for i in range(len(draws)):
        counts[sum(1 for n in back_list(draws.iloc[i]) if n >= 7)] += 1
    return counts


def back_pair_freq(draws: pd.DataFrame) -> list[tuple[tuple[int, int], int]]:
    """后区两码组合频次（按期数降序、组合升序）。"""
    c: Counter = Counter()
    for i in range(len(draws)):
        b = sorted(back_list(draws.iloc[i]))
        c[(b[0], b[1])] += 1
    return sorted(c.items(), key=lambda kv: (-kv[1], kv[0]))


def back_summary(draws: pd.DataFrame) -> dict:
    """后区汇总：平均和值 / 重号概率 / 奇数占比 / 大号占比。"""
    total = len(draws)
    sums = [sum(back_list(draws.iloc[i])) for i in range(total)]
    avg = float(pd.Series(sums).mean()) if sums else 0.0
    _, bk, pairs = repeat_stats(draws)
    rep = sum(k * v for k, v in bk.items()) / (BACK_COUNT * pairs) if pairs else 0.0
    nums = [n for i in range(total) for n in back_list(draws.iloc[i])]
    total_n = len(nums)
    odd_rate = sum(1 for n in nums if n % 2 == 1) / total_n if total_n else 0.0
    big_rate = sum(1 for n in nums if n >= 7) / total_n if total_n else 0.0
    return {"avg_sum": avg, "repeat": rep, "odd_rate": odd_rate, "big_rate": big_rate}


def back_omission_stats(draws: pd.DataFrame) -> dict[int, int]:
    """后区遗漏值：距离上次开出间隔期数（从未开出记为全部期数）。"""
    n = len(draws)
    last: dict[int, int] = {}
    for i in range(n):
        for num in back_list(draws.iloc[i]):
            last[num] = i
    return {num: (n - 1 - last[num]) if num in last else n
            for num in range(BACK_MIN, BACK_MAX + 1)}


def back_omission_table(draws: pd.DataFrame) -> pd.DataFrame:
    """后区遗漏值表：号码 / 出现次数 / 遗漏期数 / 冷热。"""
    freq_df = back_freq(draws)
    cls = classify_hot_warm_cold(freq_df).set_index("number")
    om = back_omission_stats(draws)
    rows = [{
        "号码": n,
        "出现次数": int(cls.loc[n, "count"]),
        "遗漏期数": om[n],
        "冷热": cls.loc[n, "category"],
    } for n in range(BACK_MIN, BACK_MAX + 1)]
    return pd.DataFrame(rows)


# ---------------------------- 中奖判定 / 生成历史 ----------------------------
def prize_level(a: int, b: int) -> tuple[str, float]:
    """大乐透中奖等级判定（基本投注）。返回 (等级, 固定奖金或 0)。"""
    if a == 5 and b == 2:
        return "一等奖", 0.0
    if a == 5 and b == 1:
        return "二等奖", 0.0
    if a == 5 and b == 0:
        return "三等奖", 10000.0
    if a == 4 and b == 2:
        return "四等奖", 3000.0
    if a == 4 and b == 1:
        return "五等奖", 300.0
    if a == 3 and b == 2:
        return "六等奖", 200.0
    if a == 4 and b == 0:
        return "七等奖", 100.0
    if (a == 3 and b == 1) or (a == 2 and b == 2):
        return "八等奖", 15.0
    if (a == 3 and b == 0) or (a == 1 and b == 2) or (a == 2 and b == 1) or (a == 0 and b == 2):
        return "九等奖", 5.0
    return "", 0.0


def single_prize(front: list[int], back: list[int],
                 draw_front: list[int], draw_back: list[int]) -> tuple[int, int, dict, float]:
    """单式一注的中奖判定。返回 (前区命中, 后区命中, {等级:注数}, 固定奖金)。"""
    fs = set(front) & set(draw_front)
    bs = set(back) & set(draw_back)
    a, b = len(fs), len(bs)
    lv, fixed = prize_level(a, b)
    return a, b, ({lv: 1} if lv else {}), fixed


def fushi_prize(front_pool: list[int], back_pool: list[int],
                draw_front: list[int], draw_back: list[int]) -> tuple[int, int, dict, float]:
    """复式投注中奖判定：枚举所有 C(前区,5)×C(后区,2) 组合的命中分布。
    返回 (前区集合命中数, 后区集合命中数, {等级:注数}, 固定奖金合计)。
    """
    fs = set(front_pool) & set(draw_front)
    bs = set(back_pool) & set(draw_back)
    a, na = len(fs), len(front_pool) - len(fs)
    b, nb = len(bs), len(back_pool) - len(bs)
    levels: dict[str, int] = {}
    total = 0.0
    for i in range(max(0, FRONT_COUNT - na), min(a, FRONT_COUNT) + 1):
        for j in range(max(0, BACK_COUNT - nb), min(b, BACK_COUNT) + 1):
            cnt = nCr(a, i) * nCr(na, FRONT_COUNT - i) * nCr(b, j) * nCr(nb, BACK_COUNT - j)
            if not cnt:
                continue
            lv, fixed = prize_level(i, j)
            if lv:
                levels[lv] = levels.get(lv, 0) + cnt
                total += fixed * cnt
    return a, b, levels, total


def dantuo_prize(f_dan: list[int], f_tuo: list[int], b_dan: list[int], b_tuo: list[int],
                 draw_front: list[int], draw_back: list[int]) -> tuple[int, int, dict, float]:
    """胆拖投注中奖判定：胆码固定进入每注。
    返回 (胆+拖整体前区命中数, 整体后区命中数, {等级:注数}, 固定奖金合计)。
    """
    da = len(set(f_dan) & set(draw_front))
    ta = len(set(f_tuo) & set(draw_front))
    nta = len(f_tuo) - ta
    db = len(set(b_dan) & set(draw_back))
    tb = len(set(b_tuo) & set(draw_back))
    ntb = len(b_tuo) - tb
    need_f = FRONT_COUNT - len(f_dan)
    need_b = BACK_COUNT - len(b_dan)
    levels: dict[str, int] = {}
    total = 0.0
    for i in range(max(0, need_f - nta), min(ta, need_f) + 1):
        for j in range(max(0, need_b - ntb), min(tb, need_b) + 1):
            cnt = nCr(ta, i) * nCr(nta, need_f - i) * nCr(tb, j) * nCr(ntb, need_b - j)
            if not cnt:
                continue
            lv, fixed = prize_level(da + i, db + j)
            if lv:
                levels[lv] = levels.get(lv, 0) + cnt
                total += fixed * cnt
    return da + ta, db + tb, levels, total


def nums_str(nums) -> str:
    return " ".join(f"{int(n):02d}" for n in nums)


def parse_nums_str(s: str) -> list[int]:
    return parse_numbers(s or "")


def levels_text(levels: dict[str, int]) -> str:
    return "、".join(f"{lv}{n}注" for lv, n in levels.items())


def prize_amount_text(levels: dict[str, int], fixed_amount: float) -> str:
    if not levels:
        return "0 元"
    has_float = any(lv in ("一等奖", "二等奖") for lv in levels)
    s = f"固定奖 {fixed_amount:,.2f} 元"
    if has_float:
        s += "，含浮动奖（以官方公布为准）"
    return s


def add_history(gen_time: str, gen_issue: str, bet_type: str, front: list[int], back: list[int],
                f_dan: list[int] | None = None, b_dan: list[int] | None = None,
                notes: int = 1, amount: float = 2.0,
                conn: sqlite3.Connection | None = None) -> None:
    own = conn is None
    conn = conn or get_conn()
    conn.execute(
        "INSERT INTO gen_history(gen_time, gen_issue, bet_type, front, back, f_dan, b_dan, notes, amount) "
        "VALUES(?,?,?,?,?,?,?,?,?)",
        (gen_time, str(gen_issue), bet_type, nums_str(front), nums_str(back),
         nums_str(f_dan or []), nums_str(b_dan or []), int(notes), float(amount)))
    conn.commit()
    if own:
        conn.close()


def load_history(conn: sqlite3.Connection | None = None) -> pd.DataFrame:
    own = conn is None
    conn = conn or get_conn()
    df = pd.read_sql_query(
        "SELECT id, gen_time, gen_issue, bet_type, front, back, f_dan, b_dan, notes, amount "
        "FROM gen_history ORDER BY id DESC", conn)
    if own:
        conn.close()
    return df


def clear_history(conn: sqlite3.Connection | None = None) -> None:
    own = conn is None
    conn = conn or get_conn()
    conn.execute("DELETE FROM gen_history")
    conn.commit()
    if own:
        conn.close()


def evaluate_history(row: pd.Series, draws: pd.DataFrame) -> dict:
    """对一条生成历史记录计算开奖对照结果。
    对照期 = 数据库中比 gen_issue（生成时最新期）更早出现的第一期，即用户投注的那一期。
    返回 dict(check_issue, hit_front, hit_back, hit_front_set, hit_back_set,
              levels, fixed_amount, status)。
    """
    result = {"check_issue": None, "hit_front": 0, "hit_back": 0,
              "hit_front_set": [], "hit_back_set": [],
              "levels": {}, "fixed_amount": 0.0, "status": "待开奖"}
    gen_issue = row.get("gen_issue")
    if not gen_issue:
        result["status"] = "无对照期"
        return result
    cand = draws[draws["sort_key"] > issue_key(gen_issue)]
    if cand.empty:
        return result
    draw_row = cand.iloc[0]
    df_, db_ = front_list(draw_row), back_list(draw_row)
    result["check_issue"] = draw_row["issue"]
    bt = row["bet_type"]
    if bt == "胆拖":
        f_dan = parse_nums_str(row["f_dan"])
        f_tuo = parse_nums_str(row["front"])
        b_dan = parse_nums_str(row["b_dan"])
        b_tuo = parse_nums_str(row["back"])
        hit_a, hit_b, levels, total = dantuo_prize(f_dan, f_tuo, b_dan, b_tuo, df_, db_)
        fs = set(f_dan) | set(f_tuo)
        bs = set(b_dan) | set(b_tuo)
    elif bt == "复式":
        hit_a, hit_b, levels, total = fushi_prize(
            parse_nums_str(row["front"]), parse_nums_str(row["back"]), df_, db_)
        fs = set(parse_nums_str(row["front"]))
        bs = set(parse_nums_str(row["back"]))
    else:
        hit_a, hit_b, levels, total = single_prize(
            parse_nums_str(row["front"]), parse_nums_str(row["back"]), df_, db_)
        fs = set(parse_nums_str(row["front"]))
        bs = set(parse_nums_str(row["back"]))
    result.update(
        hit_front=hit_a, hit_back=hit_b,
        hit_front_set=sorted(fs & set(df_)),
        hit_back_set=sorted(bs & set(db_)),
        levels=levels, fixed_amount=total,
        status="中奖" if levels else "未中奖")
    return result


# ---------------------------- 展示表 / 导出 ----------------------------
def simulate_draw(rng: random.Random | None = None) -> tuple:
    """模拟开奖号码：前区 5 个（1-35）+ 后区 2 个（1-12）。"""
    rng = rng or random.Random()
    return (sorted(rng.sample(range(1, 36), 5)), sorted(rng.sample(range(1, 13), 2)))


def generate_90(groups: int = 5, rng: random.Random | None = None) -> list:
    """90注定位：一次随机生成 groups 组 6+3 号码（前区 6 个 1-35，后区 3 个 1-12）。"""
    rng = rng or random.Random()
    out = []
    for _ in range(groups):
        front = sorted(rng.sample(range(1, 36), 6))
        back = sorted(rng.sample(range(1, 13), 3))
        out.append((front, back))
    return out


def add_tz_history(gen_time: str, gen_issue, groups: list,
                   conn: sqlite3.Connection | None = None) -> None:
    """将生成的 5 组 6+3 写入 tz_history。"""
    own = conn is None
    conn = conn or get_conn()
    for i, (front, back) in enumerate(groups):
        conn.execute(
            "INSERT INTO tz_history(gen_time, gen_issue, group_no, front, back) VALUES(?,?,?,?,?)",
            (gen_time, str(gen_issue) if gen_issue else "", i + 1,
             nums_str(front), nums_str(back)),
        )
    conn.commit()
    if own:
        conn.close()


def load_tz_history(conn: sqlite3.Connection | None = None) -> pd.DataFrame:
    """读取 90注定位历史，最新在前。"""
    own = conn is None
    conn = conn or get_conn()
    df = pd.read_sql_query("SELECT id, gen_time, gen_issue, group_no, front, back FROM tz_history"
                           " ORDER BY id DESC", conn)
    if own:
        conn.close()
    return df


def tz_batches(hist: pd.DataFrame) -> list:
    """按区划分 90注定位历史：每次确认（group_no 从 1 开始）的所有组为一个区。
    返回 [{gen_time, gen_issue, rows, ids}]，按 id 升序排列。"""
    batches = []
    cur = None
    for _, r in hist.sort_values("id").iterrows():
        if int(r["group_no"]) == 1:
            if cur is not None:
                batches.append(cur)
            cur = {"gen_time": r["gen_time"], "gen_issue": r["gen_issue"], "rows": [], "ids": []}
        cur["rows"].append(r)
        cur["ids"].append(int(r["id"]))
    if cur is not None:
        batches.append(cur)
    return batches


def delete_tz_history(ids, conn: sqlite3.Connection | None = None) -> int:
    """按 id 删除 90注定位历史记录，返回删除条数。"""
    if not ids:
        return 0
    own = conn is None
    conn = conn or get_conn()
    cur = conn.execute(
        "DELETE FROM tz_history WHERE id IN ({})".format(",".join("?" * len(ids))),
        list(ids))
    conn.commit()
    if own:
        conn.close()
    return cur.rowcount


def clear_tz_history(conn: sqlite3.Connection | None = None) -> None:
    own = conn is None
    conn = conn or get_conn()
    conn.execute("DELETE FROM tz_history")
    conn.commit()
    if own:
        conn.close()


def tz_evaluate(row, draws: pd.DataFrame) -> dict:
    """单组命中判定：对照期 = 生成期后第一期。
    返回 hit_front/hit_back（命中号码）、hit_pos（行位 0-8）、hit_total。"""
    issue = str(row["gen_issue"]).strip()
    if not issue:
        return {"ok": False, "issue": "", "hit_front": [], "hit_back": [], "hit_pos": [], "hit_total": 0}
    cand = draws[draws["sort_key"] == issue_key(issue)]
    if cand.empty:
        return {"ok": False, "issue": "", "hit_front": [], "hit_back": [], "hit_pos": [], "hit_total": 0}
    cmp_issue = cand.iloc[0]["issue"]
    cmp_front = set(front_list(cand.iloc[0]))
    cmp_back = set(back_list(cand.iloc[0]))
    front = parse_numbers(row["front"])
    back = parse_numbers(row["back"])
    hf = [n for n in front if n in cmp_front]
    hb = [n for n in back if n in cmp_back]
    pos = [front.index(n) for n in hf] + [6 + back.index(n) for n in hb]
    return {"ok": True, "issue": str(cmp_issue), "hit_front": hf, "hit_back": hb,
            "hit_pos": pos, "hit_total": len(pos)}


def tz_position_stats(history: pd.DataFrame, draws: pd.DataFrame) -> dict:
    """有记录以来命中位置累计：(column, row) -> 次数。"""
    stats = {(c, r): 0 for c in range(5) for r in range(9)}
    if history.empty or draws.empty:
        return stats
    for _, row in history.iterrows():
        ev = tz_evaluate(row, draws)
        if not ev["ok"]:
            continue
        col = int(row["group_no"]) - 1
        if 0 <= col < 5:
            for pos in ev["hit_pos"]:
                stats[(col, pos)] += 1
    return stats


def draws_display_df(draws: pd.DataFrame) -> pd.DataFrame:
    """开奖记录展示表：期号 / 日期 / 前区 / 后区 / 和值 / 奇偶 / 三区。"""
    rows = []
    for i in range(len(draws)):
        r = draws.iloc[i]
        front = front_list(r)
        rows.append({
            "期号": r["issue"],
            "开奖日期": r["draw_date"],
            "前区": fmt_front(r),
            "后区": fmt_back(r),
            "前区和值": sum(front),
            "前区奇偶": f"{sum(1 for n in front if n % 2 == 1)}奇{sum(1 for n in front if n % 2 == 0)}偶",
            "三区分布": "/".join(str(c) for c in zone_counts(front)),
        })
    return pd.DataFrame(rows, columns=["期号", "开奖日期", "前区", "后区", "前区和值", "前区奇偶", "三区分布"])


def _trend_miss(rows: list, n: int, max_num: int = 35) -> list:
    """每期每个号码的连续未出现期数（当期开出=0，
    窗口内从未开出则从第 1 期起算）。"""
    miss = []
    last = {}
    for i in range(n):
        row = rows[i]
        mrow = {}
        for num in range(1, max_num + 1):
            if num in row:
                mrow[num] = 0
                last[num] = i
            else:
                mrow[num] = i - last.get(num, -1)
        miss.append(mrow)
    return miss


def _trend_stats(rows: list, n: int, max_num: int = 35) -> dict:
    """每个号码统计：出现次数 / 平均遗漏 / 最大遗漏 / 最大连出。
    口径：平均遗漏=(n-次数)/次数；最大遗漏=窗口内最长连续未开出期数（含首尾）；
    最大连出=最长连续开出期数。"""
    stats = {}
    for num in range(1, max_num + 1):
        pos = [i for i, s in enumerate(rows) if num in s]
        cnt = len(pos)
        if cnt == 0:
            stats[num] = {"count": 0, "avg_miss": float(n), "max_miss": n, "max_run": 0}
            continue
        gaps = [pos[0]] + [pos[i] - pos[i - 1] - 1 for i in range(1, cnt)] + [n - 1 - pos[-1]]
        max_run = 1
        cur = 1
        for i in range(1, cnt):
            cur = cur + 1 if pos[i] == pos[i - 1] + 1 else 1
            max_run = max(max_run, cur)
        stats[num] = {
            "count": cnt,
            "avg_miss": round((n - cnt) / cnt, 1),
            "max_miss": max(gaps),
            "max_run": max_run,
        }
    return stats


def trend_data(draws: pd.DataFrame, n: int) -> dict:
    """走势图数据：最近 n 期（升序）的期次/星期/每期号码集合与各号码统计。"""
    d = draws.tail(n).reset_index(drop=True)
    issues = [str(r["issue"]) for _, r in d.iterrows()]
    weekdays = []
    for _, r in d.iterrows():
        dt = pd.to_datetime(str(r["draw_date"]), errors="coerce")
        weekdays.append("星期" + "一二三四五六日"[dt.weekday()] if not pd.isna(dt) else "")
    front_rows = [set(front_list(r)) for _, r in d.iterrows()]
    back_rows = [set(back_list(r)) for _, r in d.iterrows()]
    return {
        "issues": issues,
        "weekdays": weekdays,
        "front_rows": front_rows,
        "back_rows": back_rows,
        "front_miss": _trend_miss(front_rows, n, 35),
        "back_miss": _trend_miss(back_rows, n, 12),
        "front_stats": _trend_stats(front_rows, n, 35),
        "back_stats": _trend_stats(back_rows, n, 12),
    }


def pick_combinations(front_picks, back_picks, cap: int = 5000) -> dict:
    """预选号选号单：前区任取 5、后区任取 2 生成全部单式组合。
    返回 {ok, reason, notes, amount, combos, truncated}；combos 每项 {front, back}。"""
    from itertools import combinations
    f = sorted({int(x) for x in front_picks})
    b = sorted({int(x) for x in back_picks})
    if len(f) < 5 or len(b) < 2:
        return {"ok": False,
                "reason": f"前区需至少 5 个（当前 {len(f)} 个），后区需至少 2 个（当前 {len(b)} 个）",
                "notes": 0, "amount": 0.0, "combos": [], "truncated": False}
    notes = comb(len(f), 5) * comb(len(b), 2)
    combos = []
    truncated = False
    for fc in combinations(f, 5):
        for bc in combinations(b, 2):
            if len(combos) >= cap:
                truncated = True
                break
            combos.append({"front": fc, "back": bc})
        if truncated:
            break
    return {"ok": True, "reason": "", "notes": notes, "amount": notes * 2.0,
            "combos": combos, "truncated": truncated}


def front_omission_table(draws: pd.DataFrame) -> pd.DataFrame:
    """遗漏值表：号码 / 出现次数 / 遗漏期数 / 冷热。"""
    freq_df = front_freq(draws)
    cls = classify_hot_warm_cold(freq_df).set_index("number")
    om = omission_stats(draws)
    rows = [{
        "号码": n,
        "出现次数": int(cls.loc[n, "count"]),
        "遗漏期数": om[n],
        "冷热": cls.loc[n, "category"],
    } for n in range(FRONT_MIN, FRONT_MAX + 1)]
    return pd.DataFrame(rows)


def to_excel_bytes(sheets: dict[str, pd.DataFrame]) -> BytesIO:
    """多表导出 Excel（每个 sheet 不超过 31 字符）。"""
    buf = BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        for name, df in sheets.items():
            df.to_excel(writer, sheet_name=str(name)[:31], index=False)
    buf.seek(0)
    return buf


def build_stats_report_excel(draws: pd.DataFrame) -> BytesIO:
    """高级统计报表（多 sheet Excel）。"""
    freq_f = front_freq(draws)
    cls = classify_hot_warm_cold(freq_f).sort_values("number")
    freq_b = back_freq(draws)
    od = odd_even_dist(draws)
    zd = zone_dist(draws)
    cons, _ = consecutive_stats(draws)
    fr, bk, _ = repeat_stats(draws)
    sheets = {
        "前区频次": pd.DataFrame({
            "号码": cls["number"], "出现次数": cls["count"], "冷热": cls["category"]}),
        "后区频次": pd.DataFrame({
            "号码": freq_b["number"], "出现次数": freq_b["count"]}),
        "奇偶分布": pd.DataFrame({
            "前区奇数个数": list(range(6)),
            "期数": [od[k] for k in range(6)]}),
        "三区分布": pd.DataFrame({
            "三区": [zn for _, _, zn in ZONES],
            **{f"出{c}个": [zd[zn][c] for _, _, zn in ZONES] for c in range(6)}}),
        "和值分布": sum_hist_df(draws),
        "连号统计": pd.DataFrame({
            "类型": list(cons.keys()),
            "期数": list(cons.values())}),
        "重号统计": pd.DataFrame({
            "重复个数": list(range(6)),
            "前区期数": [fr[k] for k in range(6)],
            "后区期数": [bk[k] if k <= 2 else "" for k in range(6)]}),
        "遗漏值": front_omission_table(draws),
    }
    return to_excel_bytes(sheets)


# ---------------------------- 偏好缓存 ----------------------------
def load_prefs() -> dict:
    ensure_dirs()
    pf = active_data_dir() / PREFS_FILE.name
    if pf.exists():
        try:
            data = json.loads(pf.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return {**DEFAULT_PREFS, **data}
        except Exception:  # noqa: BLE001
            pass
    return dict(DEFAULT_PREFS)


def save_prefs(prefs: dict) -> None:
    ensure_dirs()
    (active_data_dir() / PREFS_FILE.name).write_text(json.dumps(prefs, ensure_ascii=False, indent=2), encoding="utf-8")
