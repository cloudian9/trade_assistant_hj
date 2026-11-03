# 构建主流 ETF 清单与救市前 20 日均量基线
import asyncio
import math
import statistics
from datetime import datetime
from zoneinfo import ZoneInfo

import market
from config import (
    BASELINE_DAYS,
    BASELINE_END,
    CANDIDATE_MIN_AMOUNT,
    CANDIDATES_PER_GROUP,
    EXCLUDE_KEYWORDS,
    GROUPS,
    MAX_ETFS_PER_GROUP,
    MIN_BASELINE_AMOUNT,
)

TZ = ZoneInfo("Asia/Shanghai")


def _match_group(name: str) -> str | None:
    if any(word in name for word in EXCLUDE_KEYWORDS):
        return None
    for group_name, keywords in GROUPS.items():
        if any(word in name for word in keywords):
            return group_name
    return None


def _trading_minutes() -> list[str]:
    minutes = ["09:30"]
    for hour, start, end in ((9, 31, 59), (10, 0, 59), (11, 0, 30)):
        for minute in range(start, end + 1):
            minutes.append(f"{hour:02d}:{minute:02d}")
    minutes.append("13:01")
    for hour, start, end in ((13, 2, 59), (14, 0, 59), (15, 0, 0)):
        for minute in range(start, end + 1):
            minutes.append(f"{hour:02d}:{minute:02d}")
    return minutes


def _clock_minutes(clock: str) -> int:
    hour, minute = clock.split(":")
    return int(hour) * 60 + int(minute)


def _generic_shape() -> list[list]:
    """典型 A 股日内 U 形累计分布，避免尾盘集合竞价把盘中阈值抬得过高"""
    anchors = [
        ("09:30", 0.10),
        ("09:35", 0.22),
        ("10:00", 0.36),
        ("11:30", 0.50),
        ("13:30", 0.63),
        ("14:00", 0.74),
        ("14:30", 0.84),
        ("14:55", 0.94),
        ("15:00", 1.00),
    ]
    minute_keys = [(_clock_minutes(clock), value) for clock, value in anchors]
    shape = []
    for clock in _trading_minutes():
        value = _clock_minutes(clock)
        for index in range(1, len(minute_keys)):
            left_time, left_value = minute_keys[index - 1]
            right_time, right_value = minute_keys[index]
            if value <= right_time:
                span = right_time - left_time
                ratio = (value - left_time) / span if span else 1.0
                shape.append([clock, round(left_value + (right_value - left_value) * ratio, 6)])
                break
    shape[-1][1] = 1.0
    return shape


def _recent_shape(points: list[dict]) -> list[list]:
    """近几日分时成交量占比，跳过今天避免盘中数据失真"""
    today = datetime.now(TZ).strftime("%Y-%m-%d")
    by_day: dict[str, dict[str, float]] = {}
    for point in points:
        if point["date"] == today:
            continue
        day = point["date"]
        clock = point["time"][:5]
        by_day.setdefault(day, {})
        by_day[day][clock] = by_day[day].get(clock, 0.0) + point["volume"]

    if not by_day:
        return []

    keys = sorted({clock for day in by_day.values() for clock in day})
    average = {}
    for clock in keys:
        values = [day[clock] for day in by_day.values() if clock in day]
        if values:
            average[clock] = statistics.fmean(values)
    total = sum(average.values())
    if total <= 0:
        return []

    shape = []
    cumulative = 0.0
    for clock in keys:
        cumulative += average[clock]
        shape.append([clock, cumulative / total])
    shape[-1][1] = 1.0
    return shape


def build_shape(points: list[dict]) -> list[list]:
    """典型 U 形与近期真实分布各占一半，既保留品种差异又压制尾盘异常"""
    generic = _generic_shape()
    recent = _recent_shape(points)
    if not recent:
        return generic
    recent_map = dict(recent)
    values = [value for _, value in recent]
    indexes = sorted(clock for clock, _ in recent)
    shape = []
    for clock, generic_value in generic:
        if clock in recent_map:
            recent_value = recent_map[clock]
        else:
            position = 0
            while position < len(indexes) and indexes[position] <= clock:
                position += 1
            recent_value = values[position - 1] if position else 0.0
        shape.append([clock, 0.5 * recent_value + 0.5 * generic_value])
    shape[-1][1] = 1.0
    return shape


def shape_fraction(shape: list[list], clock: str) -> float:
    fraction = 0.0
    for time_text, value in shape:
        if time_text <= clock:
            fraction = value
        else:
            break
    return fraction


async def _baseline_for(item: dict) -> dict | None:
    try:
        bars = await market.fetch_daily_bars(
            market.secid(item["code"], item["market"]), BASELINE_END, BASELINE_DAYS + 5
        )
    except Exception:
        return None
    bars = bars[-BASELINE_DAYS:]
    if len(bars) < BASELINE_DAYS:
        return None
    item = dict(item)
    item["baseline_volume"] = statistics.fmean(bar["volume"] for bar in bars)
    item["baseline_amount"] = statistics.fmean(bar["amount"] for bar in bars)
    return item


async def _shape_for(item: dict) -> list[list]:
    try:
        points = await market.fetch_trends(market.secid(item["code"], item["market"]), 5)
        shape = build_shape(points)
    except Exception:
        shape = build_shape([])
    return shape


async def build(progress=None) -> list[dict]:
    if progress:
        progress("正在拉取 ETF 列表")
    all_etfs = await market.fetch_etf_list()

    grouped: dict[str, list[dict]] = {name: [] for name in GROUPS}
    for item in all_etfs:
        group_name = _match_group(item["name"])
        if group_name is None:
            continue
        grouped[group_name].append(item)

    candidates: list[dict] = []
    for group_name, items in grouped.items():
        items = [item for item in items if item["amount"] >= CANDIDATE_MIN_AMOUNT]
        items.sort(key=lambda item: item["amount"], reverse=True)
        for item in items[:CANDIDATES_PER_GROUP]:
            item = dict(item)
            item["group_name"] = group_name
            candidates.append(item)

    if progress:
        progress(f"正在计算 {BASELINE_END} 前 {BASELINE_DAYS} 日均量基线")
    enriched = await asyncio.gather(*[_baseline_for(item) for item in candidates])
    measured = [item for item in enriched if item is not None]

    selected: list[dict] = []
    for group_name in GROUPS:
        items = [item for item in measured if item["group_name"] == group_name]
        if not items:
            continue
        qualified = [
            item for item in items if item["baseline_amount"] >= MIN_BASELINE_AMOUNT
        ]
        if len(qualified) < 3:
            extra = sorted(
                [item for item in items if item not in qualified],
                key=lambda item: item["baseline_amount"],
                reverse=True,
            )
            qualified.extend(extra[: 3 - len(qualified)])
        qualified.sort(key=lambda item: item["baseline_amount"], reverse=True)
        selected.extend(qualified[:MAX_ETFS_PER_GROUP])

    if progress:
        progress(f"正在生成 {len(selected)} 只 ETF 的日内分布基线")
    shapes = await asyncio.gather(*[_shape_for(item) for item in selected])
    for item, shape in zip(selected, shapes):
        item["shape"] = shape
    return selected
