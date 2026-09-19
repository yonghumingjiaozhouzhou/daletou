# -*- coding: utf-8 -*-
"""
大乐透历史数据统计与号码生成工具 —— Streamlit 界面。
运行方式：streamlit run daletou_app.py
"""
from __future__ import annotations

import re

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from datetime import datetime
from pathlib import Path

import daletou_core as core
import data_updater

import streamlit.components.v1 as components

_TREND_COMP_DIR = Path(__file__).resolve().parent / "components" / "trend_pick"
_trend_pick_comp = components.declare_component(
    "trend_pick", path=str(_TREND_COMP_DIR))

ZONE_OPTS = ["不限制", "0", "1", "2", "3", "4", "5"]
EXCEL_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
LEGEND_HTML = (
    '<span style="color:#e74c3c">■</span> 热号（出现最多前1/3）　'
    '<span style="color:#95a5a6">■</span> 温号（中间1/3）　'
    '<span style="color:#3498db">■</span> 冷号（出现最少后1/3）'
)

st.set_page_config(page_title="大乐透历史数据统计与号码生成", layout="wide")

st.markdown(
    """<style>
    .stMainBlockContainer, .block-container { max-width: 1500px; margin: 0 auto; }
    </style>""",
    unsafe_allow_html=True)


def save_prefs_cb() -> None:
    """把所有侧边栏参数写入本地偏好文件（打开页面时自动加载）。"""
    def g(key: str, default):
        return st.session_state.get(key, default)

    prefs = {
        "stat_mode": "all" if st.session_state.get("stat_all", False)
                      else str(st.session_state.get("stat_n", 50)),
        "strategy": str(g("strategy", "纯随机")),
        "hot_count": int(g("hot_count", 2)),
        "warm_count": int(g("warm_count", 1)),
        "cold_count": int(g("cold_count", 2)),
        "odd": str(g("odd", "不限制")),
        "zone1": str(g("zone1", "不限制")),
        "zone2": str(g("zone2", "不限制")),
        "zone3": str(g("zone3", "不限制")),
        "groups": int(g("groups", 5)),
    }
    core.save_prefs(prefs)


def freq_bar(freq_df: pd.DataFrame, height: int = 360) -> go.Figure:
    """频次柱状图（按号码顺序展示，热/温/冷配色）。"""
    cls = core.classify_hot_warm_cold(freq_df).sort_values("number")
    colors = cls["category"].map(core.COLORS)
    ymax = max(int(cls["count"].max()), 1)
    fig = go.Figure(go.Bar(
        x=cls["number"], y=cls["count"], marker_color=colors,
        text=cls["count"], textposition="outside", cliponaxis=False))
    fig.update_layout(
        height=height, showlegend=False,
        margin=dict(t=30, b=10, l=10, r=10),
        xaxis=dict(dtick=1, title="号码"),
        yaxis=dict(range=[0, ymax * 1.18]))
    return fig


def odd_pie(draws: pd.DataFrame) -> go.Figure:
    od = core.odd_even_dist(draws)
    fig = go.Figure(go.Pie(
        labels=[f"{k}奇{5 - k}偶" for k in range(6)],
        values=[od[k] for k in range(6)], hole=0.42))
    fig.update_layout(height=360, margin=dict(t=30, b=10, l=10, r=10))
    return fig


def zone_bar(draws: pd.DataFrame) -> go.Figure:
    zd = core.zone_dist(draws)
    fig = go.Figure()
    colors = ["#4c78a8", "#f58518", "#54a24b"]
    for i, (zname, counts) in enumerate(zd.items()):
        fig.add_trace(go.Bar(x=[str(c) for c in range(6)], y=counts,
                             name=zname, marker_color=colors[i],
                             text=counts, textposition="outside"))
    fig.update_layout(
        barmode="group", height=360,
        margin=dict(t=30, b=10, l=10, r=10),
        xaxis_title="该区出号个数", yaxis_title="期数")
    return fig


# ---------------------------- 各 Tab 渲染 ----------------------------
def render_tab1(draws: pd.DataFrame) -> None:
    if draws.empty:
        st.info("数据库暂无开奖记录，请在左侧「数据管理」导入或手动录入。")
        return
    disp = core.draws_display_df(draws)
    disp["_key"] = disp["期号"].map(core.issue_key)
    disp = disp.sort_values("_key", ascending=False).drop(columns="_key")
    q = st.text_input("按期号检索（支持模糊匹配，如输入 260 或 26096）")
    if q.strip():
        disp = disp[disp["期号"].astype(str).str.contains(q.strip())]
    st.dataframe(disp, width="stretch", hide_index=True, height=560)
    buf = core.to_excel_bytes({"开奖记录": disp})
    st.download_button("导出当前列表 Excel", data=buf,
                       file_name="大乐透开奖记录.xlsx", mime=EXCEL_MIME)


def render_tab2(win: pd.DataFrame, stat_all: bool) -> None:
    if win.empty:
        st.info("统计范围内暂无数据，请在左侧导入或录入开奖记录。")
        return
    label = "全部历史" if stat_all else f"近 {len(win)} 期"
    st.caption(f"统计范围：{label}（共 {len(win)} 期）")

    m = core.summary_metrics(win)
    c = st.columns(5)
    c[0].metric("平均和值", f"{m['avg_sum']:.1f}")
    c[1].metric("连号出现率", f"{m['consec_rate']:.1%}")
    c[2].metric("最长连号", f"{m['max_run']} 连")
    c[3].metric("前区重号概率", f"{m['front_repeat']:.1%}")
    c[4].metric("后区重号概率", f"{m['back_repeat']:.1%}")

    st.subheader("前区号码出现频次（1-35）")
    st.plotly_chart(freq_bar(core.front_freq(win)), width="stretch", key="t2_front_freq")
    st.markdown(LEGEND_HTML, unsafe_allow_html=True)

    c1, c2 = st.columns(2)
    with c1:
        st.subheader("后区号码出现频次（1-12）")
        st.plotly_chart(freq_bar(core.back_freq(win)), width="stretch", key="t2_back_freq")
    with c2:
        st.subheader("前区奇偶组合占比")
        st.plotly_chart(odd_pie(win), width="stretch", key="t2_odd_pie")

    st.subheader("三区出号数量分布")
    st.plotly_chart(zone_bar(win), width="stretch", key="t2_zone")


def _record_generated(draws: pd.DataFrame, bet_type: str, items: list[dict]) -> None:
    """把本次生成结果写入历史记录表（供「生成历史记录」页展示与开奖对照）。"""
    if not items:
        return
    gen_issue = str(draws.iloc[-1]["issue"]) if not draws.empty else ""
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    for it in items:
        core.add_history(
            now, gen_issue, bet_type,
            list(it.get("front", [])), list(it.get("back", [])),
            list(it.get("f_dan", [])), list(it.get("b_dan", [])),
            int(it.get("notes", 1)), float(it.get("amount", 2.0)))


def _ball_html(num: int, hit: bool, color: str, dan: bool = False) -> str:
    n2 = f"{num:02d}"
    _dan_bd = "border:2px solid #111;" if dan else ""
    if hit:
        return (f'<span style="display:inline-block;min-width:24px;height:24px;line-height:20px;'
                f'border-radius:50%;background:{color};color:#fff;text-align:center;'
                f'font-size:12px;font-weight:800;margin:1px;padding:0 3px;{_dan_bd}">{n2}</span>')
    if dan:
        return (f'<span style="display:inline-block;min-width:24px;height:24px;line-height:20px;'
                f'border-radius:50%;background:#fff;color:{color};text-align:center;'
                f'font-size:12px;font-weight:800;margin:1px;padding:0 3px;'
                f'border:2px solid {color};">{n2}</span>')
    return (f'<span style="display:inline-block;min-width:24px;height:24px;line-height:24px;'
            f'border-radius:50%;background:#eef0f2;color:#333;text-align:center;'
            f'font-size:12px;margin:1px;padding:0 3px">{n2}</span>')


def _plus_html() -> str:
    return '<span style="font-weight:800;color:#222;margin:0 3px;font-size:14px">+</span>'


def _history_row_html(h: pd.Series, e: dict) -> str:
    front_nums = core.parse_nums_str(h["front"])
    back_nums = core.parse_nums_str(h["back"])
    f_dan = core.parse_nums_str(h["f_dan"])
    b_dan = core.parse_nums_str(h["b_dan"])
    fh, bh = set(e["hit_front_set"]), set(e["hit_back_set"])
    opened = e["status"] in ("中奖", "未中奖")
    is_dt = h["bet_type"] == "胆拖"
    if is_dt and f_dan:
        fballs = ("".join(_ball_html(n, n in fh, "#e74c3c", dan=True) for n in f_dan)
                  + _plus_html()
                  + "".join(_ball_html(n, n in fh, "#e74c3c") for n in front_nums))
    else:
        fballs = "".join(_ball_html(n, n in fh, "#e74c3c") for n in front_nums)
    if is_dt and b_dan:
        bballs = ("".join(_ball_html(n, n in bh, "#3498db", dan=True) for n in b_dan)
                  + _plus_html()
                  + "".join(_ball_html(n, n in bh, "#3498db") for n in back_nums))
    else:
        bballs = "".join(_ball_html(n, n in bh, "#3498db") for n in back_nums)
    note_txt = f"{h['notes']:,}注"
    if opened:
        issue_txt = f'{h["gen_issue"]} → {e["check_issue"]}'
        if e["status"] == "中奖":
            status_html = '<span style="color:#27ae60;font-weight:600">中奖</span>'
            prize_html = core.levels_text(e["levels"])
            amt_html = core.prize_amount_text(e["levels"], e["fixed_amount"])
        else:
            status_html = '<span style="color:#7f8c8d">未中奖</span>'
            prize_html, amt_html = "-", "-"
    else:
        issue_txt = f'{h["gen_issue"]} → 待开奖'
        status_html = '<span style="color:#95a5a6">待开奖</span>'
        prize_html, amt_html = "-", "-"
    hit_txt = (f'前区 {e["hit_front"]}/{len(front_nums)}　'
               f'后区 {e["hit_back"]}/{len(back_nums)}') if opened else "-"
    extra = ""
    td = 'style="padding:7px 8px;border:1px solid #e3e6ea;vertical-align:middle"'
    prize_cell = ""
    if e["status"] == "中奖":
        prize_cell = f'<div style="font-size:11px;color:#666">{prize_html}</div>'
    return (
        f'<tr>'
        f'<td {td}>{h["gen_time"]}</td>'
        f'<td {td}>{issue_txt}</td>'
        f'<td {td}>{h["bet_type"]}<div style="font-size:11px;color:#888">{note_txt}</div></td>'
        f'<td {td}>{fballs}{extra}</td>'
        f'<td {td}>{bballs}</td>'
        f'<td {td}>{hit_txt}</td>'
        f'<td {td}>{status_html}{prize_cell}</td>'
        f'<td {td}>{amt_html}</td>'
        f'</tr>')


def _trend_html(issues, weekdays, rows, miss_rows, stats, max_num: int, color: str) -> str:
    """走势图表格（缩小美化）：期次/星期/号码矩阵 + 统计行。
    开出号码以圆圈标注，未开出格填该号码当期连续未出现期数。"""
    css = (
        "<style>"
        ".trend-wrap { overflow-x: auto; margin-bottom: 8px; }"
        ".trend { border-collapse: collapse; font-size: 11px; white-space: nowrap; }"
        ".trend th, .trend td { border: 1px solid #e6e9ef; min-width: 21px; height: 21px;"
        " text-align: center; padding: 0 1px; }"
        ".trend th { background: #eef1f6; color: #444; font-weight: 600;"
        " position: sticky; top: 0; z-index: 2; }"
        ".trend tbody tr:nth-child(even) td { background: #fbfbfd; }"
        ".trend .t-issue { min-width: 48px !important; background: #f4f6fa; font-weight: 600; color: #333; }"
        ".trend .t-week { min-width: 32px !important; color: #777; }"
        ".trend .t-stat { background: #f7f8fa !important; font-weight: 600; color: #333; }"
        ".trend .t-miss { color: #b9bdc7; font-size: 10px; }"
        "</style>"
    )
    head = ('<tr><th class="t-issue">期次</th><th class="t-week">星期</th>'
            + "".join(f"<th>{i:02d}</th>" for i in range(1, max_num + 1)) + "</tr>")
    body = []
    for idx, iss in enumerate(issues):
        s = rows[idx]
        mrow = miss_rows[idx]
        row = [f'<td class="t-issue">{iss}</td><td class="t-week">{weekdays[idx]}</td>']
        for num in range(1, max_num + 1):
            if num in s:
                row.append(f'<td><span style="display:inline-block;width:16px;height:16px;'
                           f'line-height:14px;border-radius:50%;border:1.5px solid {color};'
                           f'color:{color};font-weight:700;">{num:02d}</span></td>')
            else:
                row.append(f'<td class="t-miss">{mrow[num]}</td>')
        body.append("<tr>" + "".join(row) + "</tr>")
    for key, label in [("count", "出现次数"), ("avg_miss", "平均遗漏"),
                       ("max_miss", "最大遗漏"), ("max_run", "最大连出")]:
        row = [f'<td class="t-stat">{label}</td><td class="t-stat"></td>']
        for num in range(1, max_num + 1):
            v = stats[num][key]
            row.append(f'<td class="t-stat">{v:.1f}</td>' if key == 'avg_miss'
                       else f'<td class="t-stat">{v}</td>')
        body.append("<tr>" + "".join(row) + "</tr>")
    return (f'{css}<div class="trend-wrap"><table class="trend">'
            f"<thead>{head}</thead><tbody>{''.join(body)}</tbody></table></div>")


_TREND_SELECT_CSS = (
    "<style>"
    ".stDataFrame { --gdg-accent-color: rgba(255,224,224,0.85); --gdg-accent-fg: #333;"
    " --gdg-font-size: 10px; --gdg-header-font-size: 10px;"
    " --gdg-cell-horizontal-padding: 1px; --gdg-cell-vertical-padding: 1px;"
    " --gdg-border-color: #aab2bd; --gdg-horizontal-border-color: #c7ccd4;"
    " --gdg-bg-group-header: #e9edf3; }"
    "</style>"
)


_TREND_COLS = (["期次", "星期"]
               + [f"{i:02d}" for i in range(1, 36)]
               + [f"B{i:02d}" for i in range(1, 13)])


def _trend_col_loc(cols, zone, num):
    """位置定位：MultiIndex列（区间,号码），兼容普通列名前{num:02d}/后{num:02d}。"""
    if isinstance(cols, pd.MultiIndex):
        return cols.get_loc((zone, f"{num:02d}"))
    if zone == "后区":
        return cols.get_loc(f"B{num:02d}")
    return cols.get_loc(f"{num:02d}")


def _trend_zone(num):
    """前区号码所属区间名。"""
    return "一区" if num <= 12 else "二区" if num <= 24 else "三区"


def _trend_matrix_df(issues, weekdays, front_rows, front_miss, back_rows, back_miss) -> pd.DataFrame:
    """走势图矩阵 DataFrame：期次/星期 + 前区 1-35 + 后区 1-12。
    开出号码显示两位号，未开出显示连续未出期数。
    数值右对齐填充至 3 字符，在左对齐渲染下使号码视觉居中。"""
    rows = []
    for idx, iss in enumerate(issues):
        r = {"期次": str(iss), "星期": weekdays[idx]}
        for num in range(1, 36):
            r[f"{num:02d}"] = f"{num:02d}".rjust(2) if num in front_rows[idx] else f"{front_miss[idx][num]:>2}"
        for num in range(1, 13):
            r[f"B{num:02d}"] = f"{num:02d}".rjust(2) if num in back_rows[idx] else f"{back_miss[idx][num]:>2}"
        rows.append(r)
    _df = pd.DataFrame(rows)
    _df.columns = _TREND_COLS
    return _df


def _trend_style_all(df: pd.DataFrame, n_data: int, front_rows, back_rows,
                    sel_f, sel_b, sel_rows=None) -> pd.DataFrame:
    """走势图整体样式：全格数字居中；
    数据行开奖号浅红/浅蓝底加粗；
    数据行被选中整行淡红（无其他作用）；
    预选行选中格号码套实心圆
    （前区红色 / 后区蓝色，白字居中）。"""
    sel_rows = set(sel_rows or [])
    styles = pd.DataFrame("text-align:center;", index=df.index, columns=df.columns)
    for _z, _n in [("一区", 12), ("二区", 24), ("三区", 35), ("后区", 12)]:
        _bc = _trend_col_loc(styles.columns, _z, _n)
        for _rr in range(len(styles)):
            styles.iat[_rr, _bc] += "background-color:#d9dce2;"
    for idx in range(n_data):
        _fr = set(front_rows[idx])
        for _zone, _a, _b in [("一区", 1, 12), ("二区", 13, 24), ("三区", 25, 35)]:
            if not any(_a <= n <= _b for n in _fr):
                for n in range(_a, _b + 1):
                    styles.iat[idx, _trend_col_loc(styles.columns, _zone, n)] += (
                        "background-color:#f2e9ff;")
        for n in _fr:
            styles.iat[idx, _trend_col_loc(styles.columns, _trend_zone(n), n)] = (
                "text-align:center;background-color:#fdecec;color:#b03a2e;font-weight:700")
        for n in back_rows[idx]:
            styles.iat[idx, _trend_col_loc(styles.columns, "后区", n)] = (
                "text-align:center;background-color:#e8f2fb;color:#1f5f8b;font-weight:700")
    for r in sel_rows:
        if 0 <= r < n_data:
            for c in styles.columns:
                styles.iat[r, styles.columns.get_loc(c)] = (
                    "text-align:center;background-color:#ffe8e8;color:#a33")
    for i in range(5):
        r = n_data + i
        for n in sel_f[i]:
            styles.iat[r, _trend_col_loc(styles.columns, _trend_zone(n), n)] = (
                "text-align:center;background-color:#e74c3c;color:#fff;font-weight:700")
        for n in sel_b[i]:
            styles.iat[r, _trend_col_loc(styles.columns, "后区", n)] = (
                "text-align:center;background-color:#3498db;color:#fff;font-weight:700")
    for rr in range(n_data + 5, n_data + 9):
        if rr < len(styles):
            for c in styles.columns:
                styles.iat[rr, styles.columns.get_loc(c)] = (
                    "text-align:center;background-color:#f7f8fa;color:#333;font-weight:600")
    return styles


def _trend_stat_rows(front_stats, back_stats) -> list:
    """统计行（出现次数/平均遗漏/最大遗漏/最大连出），
    前区 1-35 + 后区 1-12 合并一行。"""
    rows = []
    for key, label in [("count", "出现次数"), ("avg_miss", "平均遗漏"),
                       ("max_miss", "最大遗漏"), ("max_run", "最大连出")]:
        r = {"期次": label, "星期": "—"}
        def _fmt(v):
            return f"{v:>4.1f}" if key == "avg_miss" else f"{v:>2}"
        for num in range(1, 36):
            r[f"{num:02d}"] = _fmt(front_stats[num][key])
        for num in range(1, 13):
            r[f"B{num:02d}"] = _fmt(back_stats[num][key])
        rows.append(r)
    return rows


def _parse_pick(pk):
    """解析预选格点击参数 f{行0-4}-{前区号} / b{行0-4}-{后区号}。"""
    _raw = str(pk or "").split("#", 1)[0]
    m = re.fullmatch(r"([fb])([0-4])-(\d{1,2})", _raw)
    if not m:
        return None
    z, row, num = m.group(1), int(m.group(2)), int(m.group(3))
    if z == "f" and not (1 <= num <= 35):
        return None
    if z == "b" and not (1 <= num <= 12):
        return None
    return z, row, num


def _trend_html_table(data, sel_f, sel_b, full=True) -> str:
    """走势图（CSS Grid）：区间分组表头、正方形号码格、数字居中、
    开出号红/蓝底、断区淡紫、区间加粗黑线分隔、预选格点击实心圆、统计行并入。"""
    issues = data["issues"]
    weekdays = data["weekdays"]
    fr = data["front_rows"]
    br = data["back_rows"]
    fm = data["front_miss"]
    bm = data["back_miss"]
    fs = data["front_stats"]
    bs = data["back_stats"]
    n = len(issues)
    sep_nums = {12: "s12", 24: "s24", 35: "s35"}
    css = (
        "<style>"
        ".tg-wrap { margin: 4px 0 8px; border-top: 1px solid #cfd4dc; border-left: 1px solid #cfd4dc; }"
        ".tg-wrap.hide { max-height: 400px; overflow-y: auto; }"
        ".tg { display: grid; width: 100%; box-sizing: border-box;"
        " grid-template-columns: 44px 38px repeat(47, minmax(0, 1fr)); background: #fff; }"
        ".tg .c { aspect-ratio: 1 / 1; min-width: 0; padding: 0; box-sizing: border-box;"
        " display: flex; align-items: center; justify-content: center;"
        " border-right: 1px solid #e6e9ef; border-bottom: 1px solid #e6e9ef;"
        " font-family: Consolas, 'Courier New', monospace; line-height: 1;"
        " font-size: clamp(8px, 1.05vw, 11px); white-space: nowrap; overflow: hidden; }"
        ".tg .qc { aspect-ratio: auto; font-size: clamp(8px, 0.95vw, 11px); }"
        ".tg .wk { aspect-ratio: auto; font-size: clamp(8px, 0.95vw, 11px); }"
        ".tg .h { background: #f3f5f9; font-weight: 600; }"
        ".tg .zone { aspect-ratio: auto; height: clamp(15px, 2vw, 22px);"
        " background: #e9edf3; font-weight: 700; font-size: clamp(8px, 1vw, 11px); }"
        ".tg .pk { border-color: #f2c4c4; }"
        ".tg .pk1 { border-top: 2px solid #c0392b; }"
        ".tg .zs { border-right: 2px solid #111; }"
        ".tg .s12, .tg .s24, .tg .s35 { border-right: 2px solid #111; }"
        ".tg .red { background: #fdecec; color: #b03a2e; font-weight: 700; }"
        ".tg .blue { background: #e8f2fb; color: #1f5f8b; font-weight: 700; }"
        ".tg .brk { background: #f2e9ff; }"
        ".tg .stat { background: #f7f8fa; color: #333; font-weight: 600; }"
        "a.c { text-decoration: none; color: #333; cursor: pointer; }"
        "a.c:hover { background: #eef2f7; }"
        ".tg .dot { width: 84%; height: 84%; border-radius: 50%; display: flex;"
        " align-items: center; justify-content: center; }"
        ".tg .dotr { background: #e74c3c; color: #fff; font-weight: 700; }"
        ".tg .dotb { background: #3498db; color: #fff; font-weight: 700; }"
        "</style>"
    )
    h = [css, '<div class="tg-wrap%s"><div class="tg">' % (" hide" if not full else "")]
    h.append('<div class="c qc h" style="grid-row:span 2">期次</div>')
    h.append('<div class="c wk h" style="grid-row:span 2">星期</div>')
    h.append('<div class="c zone zs" style="grid-column:span 12">一区</div>')
    h.append('<div class="c zone zs" style="grid-column:span 12">二区</div>')
    h.append('<div class="c zone zs" style="grid-column:span 11">三区</div>')
    h.append('<div class="c zone" style="grid-column:span 12">后区</div>')
    for num in range(1, 36):
        sep = " " + sep_nums[num] if num in sep_nums else ""
        h.append(f'<div class="c h nh{sep}">{num:02d}</div>')
    for num in range(1, 13):
        h.append(f'<div class="c h nh">B{num:02d}</div>')

    def sep_cls(num):
        return " " + sep_nums[num] if num in sep_nums else ""

    for idx in range(n):
        fset = set(fr[idx])
        bset = set(br[idx])
        h.append(f'<div class="c qc">{issues[idx]}</div>')
        h.append(f'<div class="c wk">{weekdays[idx]}</div>')
        for _a, _b in [(1, 12), (13, 24), (25, 35)]:
            _broke = not any(_a <= x <= _b for x in fset)
            for num in range(_a, _b + 1):
                _sep = sep_cls(num)
                if num in fset:
                    h.append(f'<div class="c red{_sep}">{num:02d}</div>')
                elif _broke:
                    h.append(f'<div class="c brk{_sep}">{fm[idx][num]}</div>')
                else:
                    h.append(f'<div class="c{_sep}">{fm[idx][num]}</div>')
        for num in range(1, 13):
            if num in bset:
                h.append(f'<div class="c blue">{num:02d}</div>')
            else:
                h.append(f'<div class="c">{bm[idx][num]}</div>')
    for i in range(5):
        sf = set(sel_f[i])
        sb = set(sel_b[i])
        _pk1 = ' pk1' if i == 0 else ''
        h.append(f'<div class="c qc pk{_pk1}">预选 {i + 1}</div>')
        h.append(f'<div class="c wk pk{_pk1}">—</div>')
        for num in range(1, 36):
            _sep = sep_cls(num)
            if num in sf:
                h.append(f'<a class="c pk{_pk1}{_sep}" data-pk="f{i}-{num}">'
                         f'<span class="dot dotr">{num:02d}</span></a>')
            else:
                h.append(f'<a class="c pk{_pk1}{_sep}" data-pk="f{i}-{num}">{num:02d}</a>')
        for num in range(1, 13):
            if num in sb:
                h.append(f'<a class="c pk{_pk1}" data-pk="b{i}-{num}">'
                         f'<span class="dot dotb">{num:02d}</span></a>')
            else:
                h.append(f'<a class="c pk{_pk1}" data-pk="b{i}-{num}">{num:02d}</a>')
    for key, label in [("count", "出现次数"), ("avg_miss", "平均遗漏"),
                       ("max_miss", "最大遗漏"), ("max_run", "最大连出")]:
        h.append(f'<div class="c qc stat">{label}</div>')
        h.append('<div class="c wk stat">—</div>')
        for num in range(1, 36):
            _v = fs[num][key]
            _t = f"{_v:.1f}" if key == "avg_miss" else str(_v)
            h.append(f'<div class="c stat{sep_cls(num)}">{_t}</div>')
        for num in range(1, 13):
            _v = bs[num][key]
            _t = f"{_v:.1f}" if key == "avg_miss" else str(_v)
            h.append(f'<div class="c stat">{_t}</div>')
    h.append("</div></div>")
    return "".join(h)


def _trend_stats_html(front_stats, back_stats) -> str:
    """走势图统计行（合并前后区同类数据一行、无前缀）。"""
    css = (
        "<style>"
        ".trend-wrap { overflow-x: auto; margin-bottom: 8px; }"
        ".trend { border-collapse: collapse; font-size: 10px; white-space: nowrap; }"
        ".trend th, .trend td { border: 1px solid #e6e9ef; min-width: 19px; height: 19px;"
        " text-align: center; padding: 0; }"
        ".trend th { background: #eef1f6; color: #444; font-weight: 600;"
        " position: sticky; top: 0; z-index: 2; }"
        ".trend tbody tr:nth-child(even) td { background: #fbfbfd; }"
        ".trend .t-issue { min-width: 44px !important; background: #f4f6fa; font-weight: 600; color: #333; }"
        ".trend .t-week { min-width: 28px !important; color: #777; }"
        ".trend .t-hf { background: #fdecec; color: #b03a2e; }"
        ".trend .t-hb { background: #e8f2fb; color: #1f5f8b; }"
        ".trend .t-stat { background: #f7f8fa !important; font-weight: 600; color: #333; }"
        ".trend .t-miss { color: #b9bdc7; font-size: 9px; }"
        "</style>"
    )
    head = ('<tr><th class="t-issue">期次</th><th class="t-week">星期</th>'
            + "".join(f'<th class="t-hf">{i:02d}</th>' for i in range(1, 36))
            + "".join(f'<th class="t-hb">{i:02d}</th>' for i in range(1, 13)) + "</tr>")
    sbody = []
    for key, label in [("count", "出现次数"), ("avg_miss", "平均遗漏"),
                       ("max_miss", "最大遗漏"), ("max_run", "最大连出")]:
        row = [f'<td class="t-stat">{label}</td><td class="t-stat"></td>']
        for num in range(1, 36):
            _v = front_stats[num][key]
            row.append(f'<td class="t-stat">{_v:.1f}</td>' if key == 'avg_miss'
                       else f'<td class="t-stat">{_v}</td>')
        for num in range(1, 13):
            _v = back_stats[num][key]
            row.append(f'<td class="t-stat">{_v:.1f}</td>' if key == 'avg_miss'
                       else f'<td class="t-stat">{_v}</td>')
        sbody.append("<tr>" + "".join(row) + "</tr>")
    return (f'{css}<div class="trend-wrap"><table class="trend">'
            f"<thead>{head}</thead><tbody>{''.join(sbody)}</tbody></table></div>")


def _tz_heat_color(v, vmax) -> str:
    """命中次数→颜色：0 无填充，越高越深红，vmax 为最深。"""
    if v <= 0 or vmax <= 0:
        return ""
    t = v / vmax
    r = int(255 - (255 - 192) * t)
    g = int(228 - (228 - 57) * t)
    b = int(228 - (228 - 43) * t)
    return f"{r},{g},{b}"


def _tz_grid_html(cells, col_labels) -> str:
    """5 行 × 9 列（横9竖5）表格：每组一行，前区 6 列 + 后区 3 列。
    cells: 5x9，每格 (text, ring, bg_rgb, fg)；ring: "" / "red" / "blue" 实心圆标注。"""
    css = (
        "<style>"
        ".tz-wrap { overflow-x: auto; margin: 6px 0; }"
        ".tz { border-collapse: collapse; font-size: 13px; white-space: nowrap; }"
        ".tz th, .tz td { border: 1px solid #d8dce3; min-width: 56px; height: 32px;"
        " text-align: center; padding: 2px 6px; font-weight: 600; }"
        ".tz th { background: #eef1f6; color: #333; }"
        ".tz .rowhead { background: #f4f6fa; color: #666; font-size: 12px; min-width: 48px; }"
        ".tz .h-front { background: #fdecec; color: #b03a2e; }"
        ".tz .h-back { background: #e8f2fb; color: #1f5f8b; }"
        ".tz .ring { display: inline-block; width: 24px; height: 24px; line-height: 22px;"
        " border-radius: 50%; color: #fff; font-weight: 700; }"
        ".tz .ring-red { background: #e74c3c; }"
        ".tz .ring-blue { background: #3498db; }"
        "</style>"
    )
    head = ("<tr><th></th>"
            + "".join(f'<th class="h-front">{c}</th>' for c in col_labels[:6])
            + "".join(f'<th class="h-back">{c}</th>' for c in col_labels[6:])
            + "</tr>")
    body = []
    for r in range(5):
        row = [f'<td class="rowhead">第 {r + 1} 组</td>']
        for c in range(9):
            text, ring, bg, fg = cells[r][c]
            style = f' style="background:rgb({bg});color:{fg};"' if bg else ""
            if ring:
                cls = "ring-red" if ring == "red" else "ring-blue"
                row.append(f'<td{style}><span class="ring {cls}">{text}</span></td>')
            else:
                row.append(f"<td{style}>{text}</td>")
        body.append("<tr>" + "".join(row) + "</tr>")
    return f'{css}<div class="tz-wrap"><table class="tz"><thead>{head}</thead><tbody>{"".join(body)}</tbody></table></div>'


def render_tz90(draws: pd.DataFrame) -> None:
    """90注定位：一键随机生成 5 组 6+3，独立历史与命中位置统计。"""
    st.caption("90注定位：一次生成 5 组 6+3（前区 6 个 + 后区 3 个，每组 18 注），"
               "共 90 注 / 180 元，以横9竖5表格展示。可点击“模拟开奖”"
               "生成当期模拟中奖号码，5 组号码按它标注命中"
               "（前区红实心圆 / 后区蓝实心圆）；点击“确认号码”"
               "才写入 90注定位历史（单独记录，不计入生成历史记录）。"
               "总表统计有记录以来各命中位置次数，越高颜色越深，深红为最多。")
    sim_c1, sim_c2 = st.columns(2)
    if sim_c1.button("模拟开奖（生成当期模拟中奖号码）", width="stretch"):
        st.session_state["tz_sim"] = core.simulate_draw()
        st.session_state.pop("_tz_sim_cleared", None)
    if sim_c2.button("清除模拟开奖", width="stretch"):
        st.session_state.pop("tz_sim", None)
    sim = st.session_state.get("tz_sim")
    if sim:
        st.success(f"模拟中奖号码：前区 {' '.join(f'{n:02d}' for n in sim[0])}"
                   f"　后区 {' '.join(f'{n:02d}' for n in sim[1])}")
    else:
        st.caption("点击“模拟开奖”生成当期模拟中奖号码，"
                   "本次生成的 5 组号码命中情况将按它标注。")

    st.markdown("**手动选择模拟中奖号码（覆盖自动模拟）**")
    mpf = st.pills("前区（选 5 个）", options=list(range(1, 36)), selection_mode="multi",
                   key="tz_man_f", format_func=lambda x: f"{x:02d}")
    mpb = st.pills("后区（选 2 个）", options=list(range(1, 13)), selection_mode="multi",
                   key="tz_man_b", format_func=lambda x: f"{x:02d}")
    if st.button("设为模拟中奖号码", width="stretch"):
        f = sorted({int(x) for x in mpf})
        b = sorted({int(x) for x in mpb})
        if len(f) != 5 or len(b) != 2:
            st.error(f"前区需恰好 5 个（当前 {len(f)} 个）、"
                     f"后区需恰好 2 个（当前 {len(b)} 个）")
        else:
            st.session_state["tz_sim"] = (f, b)
            st.session_state["_sim_manual"] = True
    if st.session_state.pop("_sim_manual", False):
        st.success("已手动设置模拟中奖号码")

    tz_n = st.slider("同时生成组数（每组 5 组 6+3，即 90 注）",
                     1, 10, 1, key="tz_n_batches")
    if st.button("生成号码", type="primary", width="stretch"):
        batches = [core.generate_90(5) for _ in range(tz_n)]
        st.session_state["tz_batches"] = batches
        st.session_state["tz_pending"] = True
        st.session_state.pop("_tz_confirmed", None)

    st.subheader("本次生成（横9竖5：每组一行，前区 6 列 + 后区 3 列）")
    batches = st.session_state.get("tz_batches")
    if not batches:
        st.info("点击上方按钮生成号码。")
    else:
        sim = st.session_state.get("tz_sim")
        heads = [f"前{i + 1}" for i in range(6)] + [f"后{i + 1}" for i in range(3)]
        for bi, groups in enumerate(batches, 1):
            if len(batches) > 1:
                st.markdown(f"**第 {bi} 批（{len(groups)} 组 / {len(groups) * 18:,} 注）**")
            cells = []
            for gi in range(5):
                front, back = groups[gi]
                row_cells = []
                for j in range(9):
                    num = front[j] if j < 6 else back[j - 6]
                    ring = ""
                    if sim:
                        if j < 6 and num in sim[0]:
                            ring = "red"
                        elif j >= 6 and num in sim[1]:
                            ring = "blue"
                    row_cells.append((f"{num:02d}", ring, "", ""))
                cells.append(row_cells)
            st.markdown(_tz_grid_html(cells, heads), unsafe_allow_html=True)
        if sim:
            st.caption("红色实心圆 = 前区命中模拟中奖号码；"
                       "蓝色实心圆 = 后区命中模拟中奖号码。")
        else:
            st.caption("生成后点击“模拟开奖”可查看命中情况。")
        total_notes = sum(len(g) * 18 for g in batches)
        m1, m2 = st.columns(2)
        m1.metric("本次组数", f"{len(batches)} 组")
        m2.metric("总注数 / 金额", f"{total_notes:,} 注 / {total_notes * 2:,.0f} 元")
        bc1, bc2 = st.columns(2)
        if bc1.button("再来一组（重新生成）", width="stretch"):
            n = st.session_state.get("tz_n_batches", 1)
            st.session_state["tz_batches"] = [core.generate_90(5) for _ in range(n)]
            st.session_state["tz_pending"] = True
            st.session_state.pop("_tz_confirmed", None)
        if st.session_state.get("tz_pending"):
            if bc2.button("确认号码（写入90注定位历史）", type="primary",
                          width="stretch"):
                latest = str(draws["issue"].iloc[-1]) if not draws.empty else ""
                next_iss = core.next_issue(latest) if latest else ""
                t = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                for groups in batches:
                    core.add_tz_history(t, next_iss, groups)
                st.session_state["tz_pending"] = False
                st.session_state["_tz_confirmed"] = True
            if st.session_state.pop("_tz_confirmed", False):
                st.success("号码已确认，写入 90 注定位历史（不计入生成历史记录）。")

    st.subheader("90注定位历史记录（按区：每次确认的 5 组为一个区）")
    hist = core.load_tz_history()
    if hist.empty:
        st.caption("暂无历史生成记录。")
    else:
        batches = core.tz_batches(hist)
        colors = ["#e74c3c", "#2980b9", "#27ae60", "#e67e22", "#8e44ad",
                  "#16a085", "#c0392b", "#2c3e50", "#d35400", "#7f8c8d"]
        sim_rep_f = {}
        sim_rep_b = {}
        sim_cur = st.session_state.get("tz_sim")
        if sim_cur:
            sf, sb = sim_cur
            issue_zones = {}
            for batch in batches:
                k = str(batch["gen_issue"])
                issue_zones.setdefault(k, []).append(batch)
            for k, zs in issue_zones.items():
                if len(zs) < 2:
                    continue
                cf = {}
                cb = {}
                for batch in zs:
                    hf = set()
                    hb = set()
                    for rec in batch["rows"]:
                        hf |= set(core.parse_numbers(rec["front"])) & set(sf)
                        hb |= set(core.parse_numbers(rec["back"])) & set(sb)
                    for n in hf:
                        cf[n] = cf.get(n, 0) + 1
                    for n in hb:
                        cb[n] = cb.get(n, 0) + 1
                sim_rep_f[k] = {n for n, c in cf.items() if c >= 2}
                sim_rep_b[k] = {n for n, c in cb.items() if c >= 2}
            st.caption("模拟对照：同期（相同期号）的区中，"
                       "若多个区命中模拟中奖号码中的相同号码，"
                       "该号码在历史记录中红色加粗标出。")
        st.caption("对应期数已开奖的区，命中号码以 ● 实心圆圈出"
                   "（前区红色 / 后区蓝色）；同期多个区命中模拟号码中相同号码时，"
                   "该号码红色加粗标出。")
        for bi, batch in enumerate(batches, 1):
            color = colors[(bi - 1) % len(colors)]
            b_rows = []
            tot_hit = 0
            opened = False
            cmp_issue = ""
            rep_f = sim_rep_f.get(str(batch["gen_issue"]), set())
            rep_b = sim_rep_b.get(str(batch["gen_issue"]), set())
            for rec in sorted(batch["rows"], key=lambda x: int(x["group_no"])):
                ev = core.tz_evaluate(rec, draws)
                front = core.parse_numbers(rec["front"])
                back = core.parse_numbers(rec["back"])
                f_html = " ".join(
                    (f'<b style="color:#e74c3c">●{n:02d}</b>' if n in ev["hit_front"] else
                     (f'<b style="color:#e74c3c">{n:02d}</b>' if n in rep_f else f"{n:02d}"))
                    for n in front)
                b_html = " ".join(
                    (f'<b style="color:#3498db">●{n:02d}</b>' if n in ev["hit_back"] else
                     (f'<b style="color:#e74c3c">{n:02d}</b>' if n in rep_b else f"{n:02d}"))
                    for n in back)
                tot_hit += ev["hit_total"]
                if ev["ok"]:
                    opened = True
                    cmp_issue = ev["issue"]
                b_rows.append(f'第 {int(rec["group_no"])} 组：前区 {f_html}　后区 {b_html}')
            status = f'已开奖（对照期 {cmp_issue}）命中 {tot_hit}' if opened else "待开奖"
            gen_time = str(batch["gen_time"])
            gen_issue = str(batch["gen_issue"]) or "—"
            sim_info = ""
            if st.session_state.get("tz_sim"):
                sf, sb = st.session_state["tz_sim"]
                hits_f = set()
                hits_b = set()
                for rec in batch["rows"]:
                    hits_f |= set(core.parse_numbers(rec["front"])) & set(sf)
                    hits_b |= set(core.parse_numbers(rec["back"])) & set(sb)
                sim_info = (f" ｜ 模拟对照：前区命中 {len(hits_f)} / "
                            f"后区命中 {len(hits_b)}")
            card = (
                f'<div style="border:2px solid {color};border-radius:8px;padding:8px 12px;margin:8px 0;'
                f'background:#fff">'
                f'<div style="display:flex;justify-content:space-between;flex-wrap:wrap;gap:4px;'
                f'font-weight:600;color:#333">'
                f'<span><span style="color:{color}">■</span> 区 {bi}（5 组 / 90 注）</span>'
                f'<span style="color:#666;font-weight:400">生成 {gen_time} ｜ 期号 {gen_issue} ｜ {status}'
                f'<span style="color:{color}">{sim_info}</span></span>'
                f'</div>'
                f'<div style="font-size:13px;line-height:1.9;margin-top:4px">'
                + "<br>".join(b_rows) +
                f'</div></div>'
            )
            c1, c2 = st.columns([6, 1])
            with c1:
                st.markdown(card, unsafe_allow_html=True)
            with c2:
                if st.button(f"删除区{bi}", key=f"tz_del_batch_{bi}", width="stretch"):
                    st.session_state["_confirm_tz_del_batch"] = list(batch["ids"])
                    st.session_state["_confirm_tz_del_label"] = f"区 {bi}"
        if st.session_state.get("_confirm_tz_del_batch"):
            ids = st.session_state["_confirm_tz_del_batch"]
            st.warning(f"将删除 {st.session_state['_confirm_tz_del_label']}"
                       f"（{len(ids)} 条记录），此操作不可撤销。")
            cw1, cw2 = st.columns(2)
            if cw1.button("确认删除", width="stretch"):
                core.delete_tz_history(ids)
                st.session_state.pop("_confirm_tz_del_batch", None)
                st.session_state.pop("_confirm_tz_del_label", None)
                st.session_state["_tz_deleted"] = True
            if cw2.button("取消", width="stretch"):
                st.session_state.pop("_confirm_tz_del_batch", None)
                st.session_state.pop("_confirm_tz_del_label", None)
        if st.session_state.pop("_tz_deleted", False):
            st.success("已删除所选区记录")
        hist_df = pd.DataFrame([
            {"生成时间": r["gen_time"], "期号": r["gen_issue"] or "—",
             "组号": int(r["group_no"]),
             "前区": " ".join(f"{n:02d}" for n in core.parse_numbers(r["front"])),
             "后区": " ".join(f"{n:02d}" for n in core.parse_numbers(r["back"]))}
            for _, r in hist.iterrows()])
        cc1, cc2 = st.columns(2)
        xlsx = core.to_excel_bytes({"九十注定位历史": hist_df})
        cc1.download_button("导出历史 Excel", xlsx,
                            file_name="90zhu_dingwei_history.xlsx", mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                            width="stretch")
        if cc2.button("清空 90注定位历史", width="stretch"):
            st.session_state["_confirm_tz_clear"] = True
        if st.session_state.pop("_confirm_tz_clear", False):
            st.warning("将删除全部 90注定位历史记录，此操作不可撤销。")
            cw1, cw2 = st.columns(2)
            if cw1.button("确认清空", width="stretch"):
                core.clear_tz_history()
                st.session_state.pop("tz_batches", None)
                st.session_state["_tz_cleared"] = True
            if cw2.button("取消", width="stretch"):
                st.session_state["_confirm_tz_clear"] = False
        if st.session_state.pop("_tz_cleared", False):
            st.success("90注定位历史已清空")

    st.subheader("命中位置统计总表（横9竖5）")
    if hist.empty:
        st.caption("暂无命中统计。")
    else:
        stats = core.tz_position_stats(hist, draws)
        vmax = max(stats.values()) if stats else 0
        heads = [f"前{i + 1}" for i in range(6)] + [f"后{i + 1}" for i in range(3)]
        cells = []
        for gi in range(5):
            row_cells = []
            for j in range(9):
                v = stats[(gi, j)]
                bg = _tz_heat_color(v, vmax)
                row_cells.append((v if v else "", "", bg, "#fff" if bg else "#555"))
            cells.append(row_cells)
        st.markdown(_tz_grid_html(cells, heads), unsafe_allow_html=True)
        st.caption("格内数字为该位置累计命中次数；颜色越深表示次数越多，深红为最多；白色为从未命中。")


def render_trend(draws: pd.DataFrame) -> None:
    """号码走势图：前后区合并一张表 + 预选行选号单。"""
    st.caption("走势图：每期一行，前区开出浅红底加粗、后区浅蓝底加粗；"
               "未开出格填连续未出期数；"
               "表格末尾 5 行为预选行（每列对应号码），"
               "点击格即选中对应号码（前区红圆 / 后区蓝圆），再点取消；"
               "末尾副出现次数/平均遗漏/最大遗漏/最大连出统计行。")
    max_n = max(len(draws), 1)
    _c1, _c2 = st.columns([3, 1])
    with _c1:
        trend_n = st.slider("走势图期数", 10, 100, min(30, max_n), key="trend_n")
    with _c2:
        st.toggle("完整展示走势图", key="trend_full_show", value=True,
                  help="开启后走势图按所选期数全部行完整展开、不折叠不滚动；关闭后表格限高 400px 内部滚动。")
    trend_n = min(trend_n, max_n)
    data = core.trend_data(draws, trend_n)
    st.subheader(f"前后区合并走势图（最近 {trend_n} 期：前区 1-35 + 后区 1-12）")
    # 预选格点击由自定义组件无刷新回传（值形如 "f0-1#3"，# 后为点击序号，保证同格连点可 toggle）
    _click = st.session_state.get("trend_pick_grid")
    if _click and st.session_state.get("_trend_pk_done") != _click:
        st.session_state["_trend_pk_done"] = _click
        _parsed = _parse_pick(str(_click).split("#", 1)[0])
        if _parsed is not None:
            _z, _row, _num = _parsed
            _key = f"pick_sel_{_z}_{_row}"
            _cur = list(st.session_state.get(_key, []))
            if _num in _cur:
                _cur.remove(_num)
            else:
                _cur.append(_num)
            st.session_state[_key] = sorted(_cur)
        st.rerun()
    st.caption("区间：一区 01-12｜二区 13-24｜三区 25-35｜后区 B01-B12，区间之间以加粗黑线分隔。"
               "走势图完整展示、不折叠不滑动；号码格为正方形、数字居中，开出号红/蓝底、断区淡紫。"
               "直接点击末尾 5 行预选行中的号码格即可选中，号码被实心圆圈住"
               "（前区红色 / 后区蓝色），再点取消；同一行可多选、互不影响，"
               "选号后在下方「选号单」查看注数金额并导出号码组合。")
    _sel_f = [st.session_state.get(f"pick_sel_f_{i}", []) for i in range(5)]
    _sel_b = [st.session_state.get(f"pick_sel_b_{i}", []) for i in range(5)]
    _trend_full = bool(st.session_state.get("trend_full_show", True))
    _trend_pick_comp(
        html=_trend_html_table(data, _sel_f, _sel_b, full=_trend_full),
        full=_trend_full, default=None, key="trend_pick_grid")

    st.caption("口径：平均遗漏 =（统计期数 - 出现次数）÷ 出现次数；"
               "最大遗漏含统计区间首尾的连续未开出期数；"
               "统计行数据与数据库实际开奖记录一致。")

    st.subheader("选号单")
    front_all = set()
    back_all = set()
    for i in range(5):
        front_all |= set(st.session_state.get(f"pick_sel_f_{i}", []))
        back_all |= set(st.session_state.get(f"pick_sel_b_{i}", []))
    if front_all or back_all:
        st.markdown("前区选号：" + (" ".join(f"{n:02d}" for n in sorted(front_all)) or "—"))
        st.markdown("后区选号：" + (" ".join(f"{n:02d}" for n in sorted(back_all)) or "—"))
    res = core.pick_combinations(front_all, back_all)
    if not res["ok"]:
        st.warning(res["reason"])
    else:
        m1, m2, m3 = st.columns(3)
        m1.metric("选号单注数", f"{res['notes']:,} 注")
        m2.metric("选号单金额", f"{res['amount']:,.2f} 元")
        m3.metric("展开组合", f"{len(res['combos']):,} 注"
                  + ("（已达上限截断）" if res["truncated"] else ""))
        if res["truncated"]:
            st.caption(f"组合总数 {res['notes']:,} 注超过导出上限，仅展开前 {len(res['combos']):,} 注。")
        rows = [{"序号": i + 1, "前区": " ".join(f"{n:02d}" for n in c["front"]),
                 "后区": " ".join(f"{n:02d}" for n in c["back"])}
                for i, c in enumerate(res["combos"])]
        text = "\n".join(
            "第{}注: 前区 {}  后区 {}".format(i + 1, r["前区"], r["后区"])
            for i, r in enumerate(rows))
        st.caption("点击下方按钮即可导出选号组合，或保存到「生成历史记录」参与开奖对照。")
        c1, c2, c3 = st.columns(3)
        c1.download_button("导出选号组合 Excel", data=core.to_excel_bytes({"选号组合": pd.DataFrame(rows)}),
                           file_name="走势图选号组合.xlsx", mime=EXCEL_MIME, width="stretch")
        c2.download_button("复制选号组合文本", data=text.encode("utf-8"),
                           file_name="走势图选号组合.txt", mime="text/plain", width="stretch")
        _sig = (",".join(f"{n:02d}" for n in sorted(front_all)),
                ",".join(f"{n:02d}" for n in sorted(back_all)))
        if st.session_state.get("pick_saved_sig") == _sig:
            c3.success("已保存到历史")
        elif c3.button("保存到生成历史记录", width="stretch"):
            _bet = "单式" if len(front_all) == 5 and len(back_all) == 2 else "复式"
            _item = {"front": sorted(front_all), "back": sorted(back_all),
                     "notes": int(res["notes"]), "amount": float(res["amount"])}
            _record_generated(draws, _bet, [_item])
            st.session_state["pick_saved_sig"] = _sig
            st.success(f"已按{_bet}保存到「生成历史记录」（{_item["notes"]:,} 注，"
                       f"{_item["amount"]:,.2f} 元），可在该页查看开奖对照。")


def render_history(draws: pd.DataFrame) -> None:
    hist = core.load_history()
    if hist.empty:
        st.info("暂无生成历史记录。在「号码组合生成器」中生成号码后会自动记录。")
        return
    evals = [core.evaluate_history(h, draws) for _, h in hist.iterrows()]
    opened = sum(1 for e in evals if e["status"] in ("中奖", "未中奖"))
    won = sum(1 for e in evals if e["status"] == "中奖")
    total_fixed = sum(e["fixed_amount"] for e in evals)
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("历史生成记录", f"{len(hist)} 条")
    c2.metric("已开奖对照", f"{opened} 条")
    c3.metric("中奖记录", f"{won} 条")
    c4.metric("固定奖合计", f"{total_fixed:,.2f} 元")
    if any(e["status"] == "中奖" and any(lv in ("一等奖", "二等奖") for lv in e["levels"]) for e in evals):
        st.caption("注：一、二等奖为浮动奖金，需以官方公布为准，此处只统计固定奖金额。")
    st.caption("命中号码以实心圆标出：前区红 ●、后区蓝 ●。对照期为生成后最新一期的开奖结果（未开奖则显示待开奖）。")

    filt = st.radio("筛选", ["全部", "仅已开奖", "仅待开奖", "仅中奖"], horizontal=True, key="hist_filter")
    rows = []
    for idx in range(len(hist)):
        h = hist.iloc[idx]
        e = evals[idx]
        if filt == "仅已开奖" and e["status"] not in ("中奖", "未中奖"):
            continue
        if filt == "仅待开奖" and e["status"] != "待开奖":
            continue
        if filt == "仅中奖" and e["status"] != "中奖":
            continue
        rows.append(_history_row_html(h, e))
    if rows:
        st.markdown(
            '<div style="overflow-x:auto"><table style="border-collapse:collapse;width:100%;font-size:13px">'
            '<thead><tr style="background:#f5f6fa;color:#333;font-weight:600">'
            '<th style="padding:7px 8px;border:1px solid #e3e6ea">生成时间</th>'
            '<th style="padding:7px 8px;border:1px solid #e3e6ea">对照期数</th>'
            '<th style="padding:7px 8px;border:1px solid #e3e6ea">类型</th>'
            '<th style="padding:7px 8px;border:1px solid #e3e6ea">前区号码</th>'
            '<th style="padding:7px 8px;border:1px solid #e3e6ea">后区号码</th>'
            '<th style="padding:7px 8px;border:1px solid #e3e6ea">命中</th>'
            '<th style="padding:7px 8px;border:1px solid #e3e6ea">中奖</th>'
            '<th style="padding:7px 8px;border:1px solid #e3e6ea">中奖金额</th>'
            '</tr></thead><tbody>' + "".join(rows) + '</tbody></table></div>',
            unsafe_allow_html=True)
    else:
        st.info("当前筛选条件下没有记录。")

    export_rows = []
    for idx in range(len(hist)):
        h = hist.iloc[idx]
        e = evals[idx]
        if h["bet_type"] == "胆拖":
            _f = (f"胆 {h['f_dan']} + 拖 {h['front']}") if core.parse_nums_str(h["f_dan"]) else h["front"]
            _b = (f"胆 {h['b_dan']} + 拖 {h['back']}") if core.parse_nums_str(h["b_dan"]) else h["back"]
        else:
            _f, _b = h["front"], h["back"]
        export_rows.append({
            "生成时间": h["gen_time"], "生成时最新期": h["gen_issue"],
            "对照期": e["check_issue"] or "", "类型": h["bet_type"],
            "前区": _f, "后区": _b,
            "前区命中": e["hit_front"], "后区命中": e["hit_back"],
            "状态": e["status"], "中奖等级": core.levels_text(e["levels"]),
            "固定奖金(元)": e["fixed_amount"],
        })
    c1, c2 = st.columns(2)
    c1.download_button("导出历史记录 Excel", data=core.to_excel_bytes(
        {"生成历史": pd.DataFrame(export_rows)}),
        file_name="大乐透生成历史.xlsx", mime=EXCEL_MIME, width="stretch")
    if c2.button("清空历史记录", width="stretch"):
        st.session_state["_confirm_hist_clear"] = True
    if st.session_state.get("_confirm_hist_clear"):
        st.warning("将删除全部生成历史记录，此操作不可撤销。")
        cc1, cc2 = st.columns(2)
        if cc1.button("确认清空", width="stretch"):
            core.clear_history()
            st.session_state["_confirm_hist_clear"] = False
            st.session_state["_hist_cleared"] = True
        if cc2.button("取消", width="stretch"):
            st.session_state["_confirm_hist_clear"] = False
    if st.session_state.pop("_hist_cleared", False):
        st.success("历史记录已清空")


def render_tab3(draws: pd.DataFrame, win: pd.DataFrame, prefs: dict) -> None:
    st.caption("生成规则取左侧「号码生成参考」；单式模式自动校验奇偶 / 三区约束（不满足重新生成），"
               "复式 / 胆拖模式的约束作用于号码集合整体。")
    strategy_opts = ["纯随机", "热号优先", "温号优先", "冷号优先", "冷热温混合"]
    strat_default = prefs.get("strategy", "纯随机")
    if strat_default == "冷热混合":
        strat_default = "冷热温混合"
    if strat_default not in strategy_opts:
        strat_default = "纯随机"
    hot_count, warm_count, cold_count = 0, 0, 0
    odd_sel, zone_sels = "不限制", ["不限制", "不限制", "不限制"]
    n_groups = int(prefs.get("groups", 5))

    c_param, c_result = st.columns([1, 1], gap="large")

    with c_param:
        st.subheader("号码生成参考")
        strategy = st.radio("号码策略", strategy_opts,
                            index=strategy_opts.index(strat_default),
                            horizontal=True, key="strategy", on_change=save_prefs_cb)
        err = None
        if strategy == "冷热温混合":
            hc, wc, cc = st.columns(3)
            hot_count = hc.slider("热号数", 0, 5, int(prefs.get("hot_count", 2)),
                                  key="hot_count", on_change=save_prefs_cb)
            warm_count = wc.slider("温号数", 0, 5, int(prefs.get("warm_count", 1)),
                                   key="warm_count", on_change=save_prefs_cb)
            cold_count = cc.slider("冷号数", 0, 5, int(prefs.get("cold_count", 1)),
                                   key="cold_count", on_change=save_prefs_cb)
            if hot_count + warm_count + cold_count > 5:
                err = "冷热温混合时，热号数 + 温号数 + 冷号数不能超过 5"
        bet_type = st.radio("投注类型", ["单式", "复式", "胆拖"], horizontal=True, key="bet_type")
        odd_sel = st.selectbox("前区奇数个数约束", ZONE_OPTS,
                               index=ZONE_OPTS.index(prefs.get("odd", "不限制")
                                                     if prefs.get("odd", "不限制") in ZONE_OPTS else "不限制"),
                               key="odd", on_change=save_prefs_cb)
        st.caption("三区区间约束（一区 1-12 / 二区 13-24 / 三区 25-35）")
        zc1, zc2, zc3 = st.columns(3)
        zone1 = zc1.selectbox("一区", ZONE_OPTS, key="zone1",
                              index=ZONE_OPTS.index(prefs.get("zone1", "不限制")
                                                    if prefs.get("zone1", "不限制") in ZONE_OPTS else "不限制"),
                              on_change=save_prefs_cb)
        zone2 = zc2.selectbox("二区", ZONE_OPTS, key="zone2",
                              index=ZONE_OPTS.index(prefs.get("zone2", "不限制")
                                                    if prefs.get("zone2", "不限制") in ZONE_OPTS else "不限制"),
                              on_change=save_prefs_cb)
        zone3 = zc3.selectbox("三区", ZONE_OPTS, key="zone3",
                              index=ZONE_OPTS.index(prefs.get("zone3", "不限制")
                                                    if prefs.get("zone3", "不限制") in ZONE_OPTS else "不限制"),
                              on_change=save_prefs_cb)
        zone_sels = [zone1, zone2, zone3]
        vals = [int(v) for v in zone_sels if v != "不限制"]
        if err is None and len(vals) == 3 and sum(vals) != 5:
            err = f"三区出号数量总和必须等于 5，当前为 {sum(vals)}"
        if err:
            st.error(err)

        f_opts = {"front_n": 8, "back_n": 4}
        d_opts = {"f_dan": 2, "f_tuo": 8, "b_dan": 1, "b_tuo": 4}
        if bet_type == "复式":
            c1, c2 = st.columns(2)
            f_opts["front_n"] = c1.slider("前区选号个数（6-18）", 6, 18, 8)
            f_opts["back_n"] = c2.slider("后区选号个数（3-12）", 3, 12, 4)
            notes_per = core.nCr(f_opts["front_n"], 5) * core.nCr(f_opts["back_n"], 2)
            st.caption(f"注数 = C({f_opts['front_n']},5) × C({f_opts['back_n']},2) = "
                       f"{notes_per:,} 注 / 张，{notes_per * 2.0:,.2f} 元 / 张")
        elif bet_type == "胆拖":
            c1, c2, c3 = st.columns(3)
            d_opts["f_dan"] = c1.slider("前区胆码（0-4，0=无胆全拖）", 0, 4, 2)
            min_tuo = max(5 - d_opts["f_dan"], 1)
            d_opts["f_tuo"] = c2.slider("前区拖码", min_tuo, 35 - d_opts["f_dan"], max(min_tuo, 8))
            d_opts["b_dan"] = c3.radio("后区胆码", [0, 1], index=1, horizontal=True,
                                       format_func=lambda x: "有胆(1个)" if x else "无胆(全拖)")
            c4, c5 = st.columns(2)
            d_opts["b_tuo"] = c4.slider("后区拖码", 2, 12 - d_opts["b_dan"], max(2, 4))
            notes_per = (core.nCr(d_opts["f_tuo"], 5 - d_opts["f_dan"])
                         * core.nCr(d_opts["b_tuo"], 2 - d_opts["b_dan"]))
            st.caption(f"注数 = C({d_opts['f_tuo']},{5 - d_opts['f_dan']}) × "
                       f"C({d_opts['b_tuo']},{2 - d_opts['b_dan']}) = {notes_per:,} 注 / 张，"
                       f"{notes_per * 2.0:,.2f} 元 / 张")
        n_groups = st.slider("生成数量（注 / 张）", 1, 20, int(prefs.get("groups", 5)),
                             key="groups", on_change=save_prefs_cb)

        if st.button("生成号码", type="primary", width="stretch", disabled=bool(err)):
            try:
                base = win if not win.empty else draws
                st.session_state.pop("convert_result", None)
                odd_target = None if odd_sel == "不限制" else int(odd_sel)
                zone_target = {i: (None if v == "不限制" else int(v)) for i, v in enumerate(zone_sels)}
                if bet_type == "单式":
                    groups, failed = core.generate_groups(base, strategy, hot_count, cold_count,
                                                          warm_count, odd_target, zone_target, n_groups)
                    st.session_state["gen_groups"] = groups
                    st.session_state["gen_failed"] = failed
                    st.session_state.pop("gen_fushi", None)
                    st.session_state.pop("gen_dantuo", None)
                elif bet_type == "复式":
                    out = []
                    for _ in range(n_groups):
                        g = core.generate_fushi(base, strategy, f_opts["front_n"], f_opts["back_n"],
                                                odd_target, zone_target)
                        if g:
                            out.append(g)
                    st.session_state["gen_fushi"] = out
                    st.session_state["gen_failed"] = n_groups - len(out)
                    st.session_state.pop("gen_groups", None)
                    st.session_state.pop("gen_dantuo", None)
                else:
                    out = []
                    for _ in range(n_groups):
                        g = core.generate_dantuo(base, strategy, d_opts["f_dan"], d_opts["f_tuo"],
                                                 d_opts["b_dan"], d_opts["b_tuo"], odd_target, zone_target)
                        if g:
                            out.append(g)
                    st.session_state["gen_dantuo"] = out
                    st.session_state["gen_failed"] = n_groups - len(out)
                    st.session_state.pop("gen_groups", None)
                    st.session_state.pop("gen_fushi", None)
                st.session_state["gen_pending"] = True
                st.session_state["gen_pending_type"] = bet_type
                st.session_state.pop("gen_error", None)
            except ValueError as e:
                st.session_state["gen_groups"] = None
                st.session_state["gen_error"] = str(e)

    with c_result:
        if st.session_state.get("gen_error"):
            st.error(st.session_state["gen_error"])
            return

        if bet_type == "单式":
            groups = st.session_state.get("gen_groups")
            if groups:
                if st.session_state.get("gen_failed", 0):
                    st.warning(f"有 {st.session_state['gen_failed']} 注因约束过于严格未能生成（已自动跳过）。")
                rows = [{
                    "注号": i,
                    "前区": g["front_str"],
                    "后区": g["back_str"],
                    "奇偶": f"{g['odd']}奇{5 - g['odd']}偶",
                    "三区": "/".join(map(str, g["zones"])),
                    "前区和值": g["sum"],
                } for i, g in enumerate(groups, 1)]
                st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True)
                m1, m2 = st.columns(2)
                m1.metric("总注数", f"{len(groups)} 注")
                m2.metric("总金额", f"{len(groups) * 2.0:,.2f} 元（2 元/注）")
                text = "\n".join(
                    f"第{i}注: 前区 {g['front_str']}  后区 {g['back_str']}"
                    for i, g in enumerate(groups, 1))
                st.caption("点击下方代码框右上角按钮即可一键复制全部号码")
                st.code(text)
                col1, col2 = st.columns(2)
                col1.download_button("导出号码组合 Excel", data=core.to_excel_bytes({"号码组合": pd.DataFrame(rows)}),
                                     file_name="大乐透号码组合.xlsx", mime=EXCEL_MIME, width="stretch")
                col2.download_button("复制号码文本文件", data=text.encode("utf-8"),
                                     file_name="大乐透号码组合.txt", mime="text/plain", width="stretch")
                if len(groups) >= 2:
                    st.divider()
                    st.subheader("单式转复式 / 胆拖")
                    st.caption("把已生成的注数合并去重，转化成一张复式或胆拖票（注数与金额自动重新计算）。")
                    cc1, cc2 = st.columns(2)
                    if cc1.button("转化为复式", width="stretch"):
                        try:
                            st.session_state["convert_result"] = ("复式", core.single_to_fushi(groups))
                        except ValueError as e:
                            st.session_state["convert_result"] = ("error", str(e))
                    with cc2.expander("转化为胆拖（按出现频次取胆）"):
                        cf_dan = st.slider("前区胆码个数（0-4，0=无胆全拖）", 0, 4, 2, key="conv_f_dan")
                        cb_dan = st.radio("后区胆码", [0, 1], index=1, horizontal=True, key="conv_b_dan",
                                          format_func=lambda x: "有胆(1个)" if x else "无胆(全拖)")
                        if st.button("转化为胆拖", width="stretch"):
                            try:
                                st.session_state["convert_result"] = (
                                    "胆拖", core.single_to_dantuo(groups, cf_dan, cb_dan))
                            except ValueError as e:
                                st.session_state["convert_result"] = ("error", str(e))
            else:
                _gen_hint(draws)
        elif bet_type == "复式":
            fs = st.session_state.get("gen_fushi")
            if fs:
                if st.session_state.get("gen_failed", 0):
                    st.warning(f"有 {st.session_state['gen_failed']} 张因约束过于严格未能生成（已自动跳过）。")
                rows = [{
                    "序号": i, "前区": g["front_str"], "后区": g["back_str"],
                    "注数": f"{g['notes']:,}", "金额(元)": f"{g['amount']:,.2f}",
                } for i, g in enumerate(fs, 1)]
                st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True)
                m1, m2 = st.columns(2)
                m1.metric("总注数", f"{sum(g['notes'] for g in fs):,} 注")
                m2.metric("总金额", f"{sum(g['amount'] for g in fs):,.2f} 元")
                text = "\n".join(
                    f"第{i}张: 前区 {g['front_str']}  后区 {g['back_str']}"
                    f"  （{g['notes']:,}注 {g['amount']:,.2f}元）"
                    for i, g in enumerate(fs, 1))
                st.caption("点击下方代码框右上角按钮即可一键复制全部号码")
                st.code(text)
                col1, col2 = st.columns(2)
                col1.download_button("导出复式 Excel", data=core.to_excel_bytes({"复式号码": pd.DataFrame(rows)}),
                                     file_name="大乐透复式.xlsx", mime=EXCEL_MIME, width="stretch")
                col2.download_button("复制号码文本文件", data=text.encode("utf-8"),
                                     file_name="大乐透复式.txt", mime="text/plain", width="stretch")
            else:
                _gen_hint(draws)
        else:  # 胆拖
            ds = st.session_state.get("gen_dantuo")
            if ds:
                if st.session_state.get("gen_failed", 0):
                    st.warning(f"有 {st.session_state['gen_failed']} 张因约束过于严格未能生成（已自动跳过）。")
                rows = [{
                    "序号": i, "前区胆码": g["f_dan_str"], "前区拖码": g["f_tuo_str"],
                    "后区胆码": g["b_dan_str"], "后区拖码": g["b_tuo_str"],
                    "注数": f"{g['notes']:,}", "金额(元)": f"{g['amount']:,.2f}",
                } for i, g in enumerate(ds, 1)]
                st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True)
                m1, m2 = st.columns(2)
                m1.metric("总注数", f"{sum(g['notes'] for g in ds):,} 注")
                m2.metric("总金额", f"{sum(g['amount'] for g in ds):,.2f} 元")
                text = "\n".join(
                    f"第{i}张: 前区胆 {g['f_dan_str']} 拖 {g['f_tuo_str']}"
                    f" | 后区胆 {g['b_dan_str']} 拖 {g['b_tuo_str']}"
                    f"  （{g['notes']:,}注 {g['amount']:,.2f}元）"
                    for i, g in enumerate(ds, 1))
                st.caption("点击下方代码框右上角按钮即可一键复制全部号码")
                st.code(text)
                col1, col2 = st.columns(2)
                col1.download_button("导出胆拖 Excel", data=core.to_excel_bytes({"胆拖号码": pd.DataFrame(rows)}),
                                     file_name="大乐透胆拖.xlsx", mime=EXCEL_MIME, width="stretch")
                col2.download_button("复制号码文本文件", data=text.encode("utf-8"),
                                     file_name="大乐透胆拖.txt", mime="text/plain", width="stretch")
            else:
                _gen_hint(draws)

        conv = st.session_state.get("convert_result")
        if conv:
            st.divider()
            if conv[0] == "error":
                st.error(conv[1])
            elif conv[0] == "复式":
                r = conv[1]
                st.success(f"已转化为复式：前区 {len(r['front'])} 个、后区 {len(r['back'])} 个")
                st.code(f"前区：{r['front_str']}\n后区：{r['back_str']}\n"
                        f"注数：{r['notes']:,} 注　金额：{r['amount']:,.2f} 元")
                st.download_button("导出复式票 Excel", data=core.to_excel_bytes(
                    {"复式票": pd.DataFrame([{"前区": r["front_str"], "后区": r["back_str"],
                                              "注数": r["notes"], "金额(元)": r["amount"]}])}),
                    file_name="大乐透转化复式.xlsx", mime=EXCEL_MIME, width="stretch")
            else:
                r = conv[1]
                st.success("已转化为胆拖")
                st.code(f"前区胆码：{r['f_dan_str']}\n前区拖码：{r['f_tuo_str']}\n"
                        f"后区胆码：{r['b_dan_str']}\n后区拖码：{r['b_tuo_str']}\n"
                        f"注数：{r['notes']:,} 注　金额：{r['amount']:,.2f} 元")
                st.download_button("导出胆拖票 Excel", data=core.to_excel_bytes(
                    {"胆拖票": pd.DataFrame([{"前区胆码": r["f_dan_str"], "前区拖码": r["f_tuo_str"],
                                              "后区胆码": r["b_dan_str"], "后区拖码": r["b_tuo_str"],
                                              "注数": r["notes"], "金额(元)": r["amount"]}])}),
                    file_name="大乐透转化胆拖.xlsx", mime=EXCEL_MIME, width="stretch")

        if st.session_state.get("gen_pending"):
            st.divider()
            st.caption("生成后需点击“确认号码”才会写入历史记录。")
            if st.button("确认号码（写入历史）", type="primary", width="stretch"):
                bt = st.session_state.get("gen_pending_type", "单式")
                if bt == "单式":
                    _record_generated(draws, "单式", st.session_state.get("gen_groups", []))
                elif bt == "复式":
                    _record_generated(draws, "复式", st.session_state.get("gen_fushi", []))
                else:
                    _record_generated(draws, "胆拖", st.session_state.get("gen_dantuo", []))
                st.session_state["gen_pending"] = False
                st.session_state["_gen_confirmed"] = True
            if st.session_state.pop("_gen_confirmed", False):
                st.success("号码已确认并写入生成历史记录")



def _gen_hint(draws: pd.DataFrame) -> None:
    if draws.empty:
        st.info("当前无历史数据，将按纯随机方式生成；导入数据后可启用热号 / 冷号策略。")
    else:
        st.caption("点击「生成号码」开始。注：本工具结果仅供娱乐，开奖完全随机。")


def render_tab5(win: pd.DataFrame, stat_all: bool) -> None:
    """后区分析与走势。"""
    if win.empty:
        st.info("统计范围内暂无数据。")
        return
    label = "全部历史" if stat_all else f"近 {len(win)} 期"
    st.caption(f"统计范围：{label}（共 {len(win)} 期）")

    m = core.back_summary(win)
    c = st.columns(4)
    c[0].metric("平均后区和值", f"{m['avg_sum']:.1f}")
    c[1].metric("后区重号概率", f"{m['repeat']:.1%}")
    c[2].metric("后区奇数占比", f"{m['odd_rate']:.1%}")
    c[3].metric("后区大号(7-12)占比", f"{m['big_rate']:.1%}")

    st.subheader("后区号码出现频次（1-12）")
    st.plotly_chart(freq_bar(core.back_freq(win)), width="stretch", key="t5_back_freq")
    st.markdown(LEGEND_HTML, unsafe_allow_html=True)

    c1, c2 = st.columns(2)
    with c1:
        st.subheader("后区奇偶分布")
        od = core.back_odd_dist(win)
        fig = go.Figure(go.Pie(labels=[f"{k}奇{2 - k}偶" for k in range(3)],
                               values=[od[k] for k in range(3)], hole=0.42))
        fig.update_layout(height=320, margin=dict(t=30, b=10, l=10, r=10))
        st.plotly_chart(fig, width="stretch", key="t5_odd_pie")
    with c2:
        st.subheader("后区大小分布（小 1-6 / 大 7-12）")
        sd = core.back_size_dist(win)
        fig = go.Figure(go.Pie(labels=[f"{k}大{2 - k}小" for k in range(3)],
                               values=[sd[k] for k in range(3)], hole=0.42))
        fig.update_layout(height=320, margin=dict(t=30, b=10, l=10, r=10))
        st.plotly_chart(fig, width="stretch", key="t5_size_pie")

    st.subheader("后区两码组合频次（热频 / 低频）")
    from itertools import combinations as _comb
    _pair_map = {pair: cnt for pair, cnt in core.back_pair_freq(win)}
    for _a, _b in _comb(range(1, 13), 2):
        _pair_map.setdefault((_a, _b), 0)
    _pair_desc = sorted(_pair_map.items(), key=lambda kv: (-kv[1], kv[0]))
    _pair_hot = _pair_desc[:10]
    # 低频组合：优先取实际开出过（次数>=1）中频次最低的，不足再补 0 次组合，保证蓝柱可见、更直观
    _pair_positive = sorted(((k, v) for k, v in _pair_map.items() if v >= 1),
                            key=lambda kv: (kv[1], kv[0]))
    _pair_zero = sorted(((k, v) for k, v in _pair_map.items() if v == 0),
                        key=lambda kv: kv[0])
    _pair_cold = (_pair_positive[:10] + _pair_zero)[:10]

    def _pair_fig(items, color, title):
        labels = [f"{a:02d}-{b:02d}" for (a, b), _ in items]
        counts = [c for _, c in items]
        fig = go.Figure(go.Bar(x=counts[::-1], y=labels[::-1], orientation="h",
                               marker_color=color, text=counts[::-1], textposition="outside",
                               cliponaxis=False))
        fig.update_layout(title=title, title_font_size=14, height=380,
                          xaxis_title="出现次数", yaxis_type="category",
                          margin=dict(t=44, b=10, l=10, r=26))
        return fig

    _pc1, _pc2 = st.columns(2)
    _pc1.plotly_chart(_pair_fig(_pair_hot, "#e74c3c", "热频组合 Top10"),
                      width="stretch", key="t5_pair_hot")
    _pc2.plotly_chart(_pair_fig(_pair_cold, "#3498db", "低频组合 Bottom10"),
                      width="stretch", key="t5_pair_cold")
    st.caption("共 66 种两码组合：红色为出现次数最多的热频搭配；蓝色为低频搭配（优先展示实际开出过但次数最少的组合，"
               "不足处补入从未开出、记 0 次的组合）。仅作历史统计，不代表未来走势。")

    st.subheader("后区号码遗漏值（距上次开出间隔期数）")
    om_df = core.back_omission_table(win)
    colors = om_df["冷热"].map(core.COLORS)
    fig = go.Figure(go.Bar(x=om_df["号码"], y=om_df["遗漏期数"], marker_color=colors,
                           text=om_df["遗漏期数"], textposition="outside", cliponaxis=False))
    fig.update_layout(height=320, xaxis=dict(dtick=1), yaxis_title="遗漏期数",
                      margin=dict(t=30, b=10, l=10, r=10))
    st.plotly_chart(fig, width="stretch", key="t5_omission")
    st.markdown(LEGEND_HTML, unsafe_allow_html=True)
    om_show = om_df.copy()
    om_show["冷热"] = om_show["冷热"].map({"hot": "热", "warm": "温", "cold": "冷"})
    st.dataframe(om_show, width="stretch", hide_index=True)

    st.subheader("后区号码走势")
    n_show = st.slider("显示最近期数", 10, min(len(win), 100), min(50, len(win)), step=5)
    tw = win.tail(n_show)
    y1 = [core.back_list(tw.iloc[i])[0] for i in range(len(tw))]
    y2 = [core.back_list(tw.iloc[i])[1] for i in range(len(tw))]
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=list(tw["issue"]), y=y1, mode="lines+markers",
                             name="后区第1码", line=dict(color="#e74c3c", width=2),
                             marker=dict(size=7)))
    fig.add_trace(go.Scatter(x=list(tw["issue"]), y=y2, mode="lines+markers",
                             name="后区第2码", line=dict(color="#3498db", width=2),
                             marker=dict(size=7)))
    fig.update_layout(height=400, xaxis_title="期号",
                      yaxis=dict(tickmode="linear", dtick=1, range=[0.5, 12.5]),
                      margin=dict(t=30, b=10, l=10, r=10))
    st.plotly_chart(fig, width="stretch", key="t5_trend")


def render_tab4(win: pd.DataFrame) -> None:
    if win.empty:
        st.info("统计范围内暂无数据。")
        return
    st.subheader("和值分布")
    sums = core.front_sums(win)
    fig = go.Figure(go.Histogram(x=sums, nbinsx=12, marker_color="#4c78a8"))
    fig.update_layout(height=320, xaxis_title="前区和值", yaxis_title="期数",
                      margin=dict(t=30, b=10, l=10, r=10))
    st.plotly_chart(fig, width="stretch", key="t4_sum_hist")

    st.subheader("连号统计（按每期最大连号归类）")
    cons, max_len = core.consecutive_stats(win)
    fig = go.Figure(go.Bar(
        x=list(cons.keys()), y=list(cons.values()),
        marker_color=["#95a5a6", "#f39c12", "#e67e22", "#c0392b"],
        text=list(cons.values()), textposition="outside"))
    fig.update_layout(height=320, yaxis_title="期数", margin=dict(t=30, b=10, l=10, r=10))
    st.plotly_chart(fig, width="stretch", key="t4_consec")
    st.caption(f"全局最长连号：{max_len} 连")

    st.subheader("上期重号统计（每期与上一期重复号码个数）")
    fr, bk, pairs = core.repeat_stats(win)
    c1, c2 = st.columns(2)
    fig1 = go.Figure(go.Bar(
        x=[str(i) for i in range(6)], y=[fr[i] for i in range(6)],
        text=[fr[i] for i in range(6)], textposition="outside"))
    fig1.update_layout(title="前区", height=300, margin=dict(t=50, b=10, l=10, r=10))
    c1.plotly_chart(fig1, width="stretch", key="t4_rep_front")
    fig2 = go.Figure(go.Bar(
        x=[str(i) for i in range(3)], y=[bk[i] for i in range(3)],
        text=[bk[i] for i in range(3)], textposition="outside"))
    fig2.update_layout(title="后区", height=300, margin=dict(t=50, b=10, l=10, r=10))
    c2.plotly_chart(fig2, width="stretch", key="t4_rep_back")

    st.subheader("号码遗漏值统计（距离上次开出间隔期数）")
    om_df = core.front_omission_table(win)
    colors = om_df["冷热"].map(core.COLORS)
    fig = go.Figure(go.Bar(
        x=om_df["号码"], y=om_df["遗漏期数"], marker_color=colors,
        text=om_df["遗漏期数"], textposition="outside", cliponaxis=False))
    fig.update_layout(
        height=380, margin=dict(t=30, b=10, l=10, r=10),
        xaxis=dict(dtick=1), yaxis_title="遗漏期数")
    st.plotly_chart(fig, width="stretch", key="t4_omission")
    st.markdown(LEGEND_HTML, unsafe_allow_html=True)
    om_show = om_df.copy()
    om_show["冷热"] = om_show["冷热"].map({"hot": "热", "warm": "温", "cold": "冷"})
    st.dataframe(om_show, width="stretch", hide_index=True)

    buf = core.build_stats_report_excel(win)
    st.download_button("导出统计报表 Excel（多工作表）", data=buf,
                       file_name="大乐透统计报表.xlsx", mime=EXCEL_MIME, width="stretch")


# ---------------------------- 主流程 ----------------------------
def main() -> None:
    core.ensure_demo_once()
    draws = core.load_draws()
    total = len(draws)
    prefs = core.load_prefs()

    # ---------------- 侧边栏 ----------------
    with st.sidebar:
        st.header("全局参数")
        if "import_msg" in st.session_state:
            res = st.session_state.pop("import_msg")
            if res.get("errors") and res.get("inserted") == 0:
                st.error("导入失败：" + "；".join(res["errors"][:3]))
            else:
                st.success(f"导入完成：共 {res['total']} 行，新增 {res['inserted']} 条，"
                           f"跳过重复 {res['skipped']} 条"
                           + (f"，另有 {len(res['errors'])} 行错误" if res["errors"] else ""))
                if res["errors"]:
                    with st.expander("查看错误明细"):
                        st.write("\n".join(res["errors"][:30]))

        with st.expander("数据管理", expanded=False):
            uploaded = st.file_uploader("导入历史开奖数据（CSV / Excel）",
                                        type=["csv", "xlsx"], key="history_upload")
            if uploaded is not None and st.session_state.get("_upload_done") != uploaded.file_id:
                res = core.import_draws(uploaded.getvalue(), uploaded.name)
                st.session_state["import_msg"] = res
                st.session_state["_upload_done"] = uploaded.file_id
                st.session_state["_need_reload"] = True

            with st.form("manual_add"):
                st.caption("手动录入最新一期开奖")
                m_issue = st.text_input("期号（如 26096）")
                m_date = st.text_input("开奖日期（如 2026-09-14，可留空）")
                m_front = st.text_input("前区 5 码（空格或逗号分隔）")
                m_back = st.text_input("后区 2 码（空格或逗号分隔）")
                m_submit = st.form_submit_button("录入该期")
            if m_submit:
                front = core.parse_numbers(m_front)
                back = core.parse_numbers(m_back)
                if not m_issue.strip():
                    st.error("请填写期号")
                elif len(front) != 5 or len(back) != 2:
                    st.error(f"号码数量不对：前区 {len(front)} 个（需 5 个）、后区 {len(back)} 个（需 2 个）")
                else:
                    ok, msg = core.insert_draw(m_issue, m_date, front, back)
                    if ok:
                        st.success(f"期号 {m_issue.strip()} 已入库")
                        st.session_state["_need_reload"] = True
                    else:
                        st.error(msg)

            if st.button("清空数据库", width="stretch"):
                st.session_state["_confirm_clear"] = True
            if st.session_state.get("_confirm_clear"):
                st.warning("将删除全部开奖记录（含示例数据），此操作不可撤销。")
                c1, c2 = st.columns(2)
                if c1.button("确认清空", width="stretch"):
                    core.clear_db()
                    st.session_state["_confirm_clear"] = False
                    st.session_state["_need_reload"] = True
                    st.session_state["clear_msg"] = True
                if c2.button("取消", width="stretch"):
                    st.session_state["_confirm_clear"] = False
            if st.session_state.pop("clear_msg", False):
                st.success("数据库已清空")

            st.download_button("导出开奖记录 Excel", data=core.to_excel_bytes(
                {"开奖记录": core.draws_display_df(core.load_draws())}),
                file_name="大乐透开奖记录.xlsx", mime=EXCEL_MIME, width="stretch")
            st.download_button("下载导入模板 CSV", data=core.template_csv_bytes(),
                               file_name="大乐透导入模板.csv", mime="text/csv", width="stretch")

        auto_upd = st.checkbox("自动更新开奖数据", value=True, key="auto_update",
                               help="每次打开页面自动检查官方最新开奖，有新增自动入库")
        if st.button("立即更新开奖数据", width="stretch"):
            st.session_state["_do_update"] = True
        _today = datetime.now().strftime("%Y-%m-%d")
        if auto_upd and st.session_state.get("_data_checked") != _today:
            st.session_state["_data_checked"] = _today
            st.session_state["_do_update"] = True
        if st.session_state.pop("_do_update", False):
            with st.spinner("正在从官方接口检查并更新开奖数据..."):
                try:
                    _res = data_updater.update_database()
                    if _res["inserted"]:
                        st.success(f"已自动更新 {_res['inserted']} 期，"
                                   f"最新期号 {_res['latest']}（{_res['checked']}）")
                    else:
                        st.info(f"数据已是最新，最新期号 {_res['latest']}"
                                f"（{_res['checked']}）")
                    st.session_state["_need_reload"] = True
                except Exception as _e:
                    st.error(f"数据更新失败：{_e}，请检查网络后重试")

        st.subheader("统计期数范围")
        if total <= 10:
            st.caption(f"当前仅 {total} 期数据，自动统计全部历史。")
            stat_all = True
            stat_n = total
        else:
            stat_all = st.checkbox("统计全部历史数据", value=(prefs.get("stat_mode") == "all"),
                                   key="stat_all", on_change=save_prefs_cb)
            stat_n = total
            if not stat_all:
                default_n = int(prefs.get("stat_mode", 50)) if str(prefs.get("stat_mode", "50")).isdigit() else 50
                default_n = min(max(default_n, 10), total)
                stat_n = st.slider("统计最近期数", 10, total, default_n, step=5,
                                   key="stat_n", on_change=save_prefs_cb)


        st.divider()
        st.caption("【本工具仅历史数据统计娱乐，开奖完全随机，无法预测】")

    # 数据管理操作后重载
    if st.session_state.pop("_need_reload", False):
        draws = core.load_draws()
        total = len(draws)

    # ---------------- 主内容区 ----------------
    st.title("大乐透历史数据统计与号码生成")
    if total and bool((draws["source"] == "demo").any()):
        st.info("当前数据为随机生成的示例数据（非真实开奖记录）。可在左侧「数据管理」导入真实数据，或清空后手动录入。")

    PAGES = ["开奖历史数据", "号码统计分析", "号码组合生成器",
             "高级统计", "后区分析与走势", "生成历史记录",
             "号码走势图", "90注定位"]
    page = st.session_state.get("page", PAGES[0])
    if page not in PAGES:
        page = PAGES[0]
        st.session_state["page"] = page

    # 顶部功能导航：一行 4 个，多余的放下一行
    nav_r1 = st.columns(4)
    nav_r2 = st.columns(4)
    for i, label in enumerate(PAGES):
        col = nav_r1[i] if i < 4 else nav_r2[i - 4]
        if col.button(label, key=f"nav_{i}",
                      type="primary" if label == page else "secondary",
                      width="stretch"):
            st.session_state["page"] = label
            page = label
    st.divider()

    win = core.select_window(draws, None if stat_all else stat_n)

    if page == PAGES[0]:
        render_tab1(draws)
    elif page == PAGES[1]:
        render_tab2(win, stat_all)
    elif page == PAGES[2]:
        render_tab3(draws, win, prefs)
    elif page == PAGES[3]:
        render_tab4(win)
    elif page == PAGES[4]:
        render_tab5(win, stat_all)
    elif page == PAGES[5]:
        render_history(draws)
    elif page == PAGES[6]:
        render_trend(draws)
    else:
        render_tz90(draws)


if __name__ == "__main__":
    main()
