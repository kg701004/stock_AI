"""Matplotlib-backed charts embedded into Tkinter frames: radar, sector pie, risk gauge, factor heatmap.

Kept separate from the data/decision modules so a charting failure (e.g. a
missing font) can never affect scoring, storage, or risk calculations --
these functions only ever render what callers already computed elsewhere.
"""
from __future__ import annotations

import tkinter as tk
from math import pi
from typing import Mapping, Sequence

import matplotlib
matplotlib.use("TkAgg")
import matplotlib.pyplot as plt
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg

plt.rcParams["font.sans-serif"] = ["Microsoft JhengHei", "Microsoft JhengHei UI", "SimHei", "Arial Unicode MS"]
plt.rcParams["axes.unicode_minus"] = False

RISK_COLOR_LOW = "#1E8449"
RISK_COLOR_MID = "#B8860B"
RISK_COLOR_HIGH = "#C0392B"
FIGURE_FACE = "#FFFFFF"


def _embed(parent: tk.Misc, figure) -> FigureCanvasTkAgg:
    figure.patch.set_facecolor(FIGURE_FACE)
    for axis in figure.axes:
        axis.set_facecolor(FIGURE_FACE)
    canvas = FigureCanvasTkAgg(figure, master=parent)
    canvas.draw()
    canvas.get_tk_widget().pack(fill="both", expand=True)
    plt.close(figure)
    return canvas


def radar_chart(parent: tk.Misc, scores: Mapping[str, float], labels: Mapping[str, str]) -> FigureCanvasTkAgg:
    """One stock's per-factor scores (0-100) as a Simply-Wall-St-style radar/snowflake."""
    names = list(scores)
    values = [scores[name] for name in names] + [scores[names[0]]]
    angles = [i / len(names) * 2 * pi for i in range(len(names))] + [0]
    figure = plt.Figure(figsize=(4.6, 4.2), dpi=100)
    axis = figure.add_subplot(111, polar=True)
    axis.plot(angles, values, color="#0F6E56", linewidth=1.5)
    axis.fill(angles, values, color="#0F6E56", alpha=0.25)
    axis.set_xticks(angles[:-1])
    axis.set_xticklabels([labels.get(name, name) for name in names], fontsize=8)
    axis.set_ylim(0, 100)
    axis.set_yticks([25, 50, 75, 100])
    axis.set_yticklabels(["25", "50", "75", "100"], fontsize=6)
    figure.tight_layout()
    return _embed(parent, figure)


def sector_pie_chart(parent: tk.Misc, weights_pct: Mapping[str, float]) -> FigureCanvasTkAgg:
    """Portfolio sector concentration as a pie chart."""
    labels = list(weights_pct); values = [weights_pct[name] for name in labels]
    figure = plt.Figure(figsize=(4.2, 3.6), dpi=100)
    axis = figure.add_subplot(111)
    axis.pie(values, labels=labels, autopct="%1.1f%%", textprops={"fontsize": 8})
    axis.axis("equal")
    figure.tight_layout()
    return _embed(parent, figure)


def risk_gauge(parent: tk.Misc, score: float, title: str = "風險分數") -> FigureCanvasTkAgg:
    """A 0-100 risk score as a half-circle gauge, colour-coded green/amber/red."""
    score = max(0.0, min(100.0, score))
    color = RISK_COLOR_LOW if score < 30 else RISK_COLOR_MID if score < 60 else RISK_COLOR_HIGH
    figure = plt.Figure(figsize=(3.6, 2.2), dpi=100)
    axis = figure.add_subplot(111)
    axis.pie([score, 100 - score], radius=1, startangle=180, counterclock=False,
             colors=[color, "#D3D1C7"], wedgeprops={"width": 0.35})
    axis.set_title(f"{title}：{score:.0f}", fontsize=10)
    axis.axis("equal")
    figure.tight_layout()
    return _embed(parent, figure)


def price_chart(parent: tk.Misc, dated_closes: Sequence[tuple], symbol: str, window_label: str) -> FigureCanvasTkAgg:
    """Daily close-price line chart for one stock, already restricted by the
    caller (price_chart_data.load_recent_closes) to the chosen window."""
    if not dated_closes:
        raise ValueError("price_chart needs at least one bar")
    dates = [item.trading_date for item in dated_closes]
    closes = [item.close for item in dated_closes]
    figure = plt.Figure(figsize=(7.6, 4.0), dpi=100)
    axis = figure.add_subplot(111)
    axis.plot(dates, closes, color="#0F6E56", linewidth=1.3)
    axis.set_title(f"{symbol}　{window_label}（收盤價，除權息回溯調整）", fontsize=11)
    axis.set_ylabel("收盤價", fontsize=9)
    axis.tick_params(axis="x", labelrotation=30, labelsize=7)
    axis.tick_params(axis="y", labelsize=8)
    axis.grid(True, alpha=0.3)
    figure.tight_layout()
    return _embed(parent, figure)


def factor_heatmap(parent: tk.Misc, rows: Sequence[tuple[str, Mapping[str, float]]], labels: Mapping[str, str]) -> FigureCanvasTkAgg:
    """Multi-stock factor comparison grid, colour-coded weak (red) to strong (green)."""
    if not rows:
        raise ValueError("factor_heatmap needs at least one row")
    factor_names = list(rows[0][1])
    matrix = [[values[name] for name in factor_names] for _symbol, values in rows]
    figure = plt.Figure(figsize=(0.6 * len(factor_names) + 1.6, 0.35 * len(rows) + 1.2), dpi=100)
    axis = figure.add_subplot(111)
    image = axis.imshow(matrix, cmap="RdYlGn", vmin=0, vmax=100, aspect="auto")
    axis.set_xticks(range(len(factor_names))); axis.set_xticklabels([labels.get(name, name) for name in factor_names], fontsize=7, rotation=45, ha="right")
    axis.set_yticks(range(len(rows))); axis.set_yticklabels([symbol for symbol, _ in rows], fontsize=8)
    for row_index, (_symbol, values) in enumerate(rows):
        for col_index, name in enumerate(factor_names):
            axis.text(col_index, row_index, f"{values[name]:.0f}", ha="center", va="center", fontsize=7, color="#2C2C2A")
    figure.colorbar(image, ax=axis, fraction=0.046, pad=0.04)
    figure.tight_layout()
    return _embed(parent, figure)
