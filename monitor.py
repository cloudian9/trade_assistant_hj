# 盘中监测循环：行情快照、逐笔大单、放量告警
import asyncio
import time
from collections import defaultdict, deque
from datetime import datetime
from zoneinfo import ZoneInfo

import market
import store
import universe
from config import (
    ALERT_COOLDOWN_MINUTES,
    ANALYZE_SECONDS,
    FUND_FLOW_SECONDS,
    LARGE_TRADE_AMOUNT,
    POLL_SECONDS,
    SINGLE_LARGE_AMOUNT,
    SUSTAINED_AMOUNT,
    SUSTAINED_COUNT,
    SUSTAINED_WINDOW_MINUTES,
    VOLUME_SURGE_HIGH_RATIO,
    VOLUME_SURGE_RATIO,
)

TZ = ZoneInfo("Asia/Shanghai")

state: dict = {
    "status": "starting",
    "message": "正在启动监测",
    "market": "closed",
    "last_update": "",
    "last_analysis": "",
    "ready": False,
}

_etfs: list[dict] = []
_last_alert: dict[tuple[str, str], datetime] = {}
_volume_streak: dict[str, int] = defaultdict(int)
_tick_state: dict[str, dict] = {}
_tick_windows: dict[str, deque] = defaultdict(deque)


def now_local() -> datetime:
    return datetime.now(TZ)


def market_phase(current: datetime) -> str:
    if current.weekday() >= 5:
        return "closed"
    clock = current.strftime("%H:%M")
    if "09:25" <= clock <= "11:35" or "12:55" <= clock <= "15:05":
        return "open"
    return "closed"


def signal_window(current: datetime) -> bool:
    """避开集合竞价和尾盘瞬时放量，只在这两个区间触发告警"""
    if current.weekday() >= 5:
        return False
    clock = current.strftime("%H:%M")
    return "09:35" <= clock <= "11:25" or "13:05" <= clock <= "14:55"


def _fmt_money(value: float) -> str:
    if abs(value) >= 100_000_000:
        return f"{value / 100_000_000:.2f} 亿"
    return f"{value / 10_000:.0f} 万"


def _single_alert_amount(item: dict) -> float:
    """单笔告警门槛随 ETF 基线成交额调整，避免小品种被固定阈值淹没"""
    return max(SINGLE_LARGE_AMOUNT, item["baseline_amount"] * 0.005)


def _sustained_alert_amount(item: dict) -> float:
    """持续买入金额门槛取基线日成交额的 1%，并按品种流动性保底"""
    return max(SUSTAINED_AMOUNT, item["baseline_amount"] * 0.01)


def _can_alert(code: str, alert_type: str) -> bool:
    key = (code, alert_type)
    last = _last_alert.get(key)
    if last is None:
        last = store.last_alert_time(code, alert_type)
        if last is not None:
            _last_alert[key] = last
    if last is None:
        return True
    return (now_local().replace(tzinfo=None) - last).total_seconds() >= ALERT_COOLDOWN_MINUTES * 60


def _remember_alert(code: str, alert_type: str) -> None:
    _last_alert[(code, alert_type)] = now_local().replace(tzinfo=None)


def snapshot() -> dict:
    return {
        "status": state["status"],
        "message": state["message"],
        "market": state["market"],
        "last_update": state["last_update"],
        "last_analysis": state["last_analysis"],
        "ready": state["ready"],
        "etf_count": len(_etfs),
    }


async def bootstrap() -> None:
    state["status"] = "loading"
    state["message"] = "正在拉取 ETF 列表"

    def progress(message: str) -> None:
        state["message"] = message

    rows = await universe.build(progress)
    if not rows:
        raise RuntimeError("没有筛选到可监测的 ETF")
    store.replace_etfs(rows)
    global _etfs
    _etfs = store.list_etfs()
    state["ready"] = True
    state["status"] = "ready"
    state["message"] = f"已加载 {len(_etfs)} 只 ETF"
    await refresh_quotes()
    await refresh_fund_flow()
    await review_volume()


async def refresh_quotes() -> None:
    if not _etfs:
        return
    secids = [market.secid(item["code"], item["market"]) for item in _etfs]
    try:
        quotes = await market.fetch_quotes(secids)
    except Exception as exc:
        state["message"] = f"行情拉取失败：{exc.__class__.__name__}"
        return

    by_code = {quote["code"]: quote for quote in quotes}
    current = now_local()
    clock = current.strftime("%H:%M")
    for item in _etfs:
        quote = by_code.get(item["code"])
        if quote is None:
            continue
        fraction = universe.shape_fraction(item["shape"], clock) if market_phase(current) == "open" else 1.0
        expected = item["baseline_volume"] * fraction
        ratio = quote["volume"] / expected if expected > 0 else 0.0
        store.update_snapshot(
            item["code"],
            quote["price"],
            quote["change_pct"],
            quote["volume"],
            quote["amount"],
            ratio,
            quote["updated_at"],
        )
        item["price"] = quote["price"]
        item["change_pct"] = quote["change_pct"]
        item["volume"] = quote["volume"]
        item["amount"] = quote["amount"]
        item["volume_ratio"] = ratio
        item["updated_at"] = quote["updated_at"]
    state["last_update"] = current.strftime("%Y-%m-%d %H:%M:%S")


async def review_volume() -> None:
    """收盘后生成一次今日放量复盘，避免服务在非交易时段启动时信息流为空"""
    current = now_local()
    if current.weekday() >= 5 or current.strftime("%H:%M") < "15:05":
        return
    trade_date = current.strftime("%Y-%m-%d")
    for item in _etfs:
        if item["volume_ratio"] < VOLUME_SURGE_RATIO:
            continue
        if store.has_review_alert(item["code"], "volume_surge", trade_date):
            continue
        ratio = item["volume_ratio"]
        level = "high" if ratio >= VOLUME_SURGE_HIGH_RATIO else "warn"
        store.insert_alert(
            code=item["code"],
            name=item["name"],
            group_name=item["group_name"],
            alert_type="volume_surge",
            level=level,
            title=f"收盘复盘：成交量较基线放大 {ratio:.1f} 倍",
            detail=(
                f"今日累计成交 {item['volume']:,.0f} 手，"
                f"10 月 14 日前 20 日均量 {item['baseline_volume']:,.0f} 手，"
                f"成交额 {_fmt_money(item['amount'])}。"
            ),
            metrics={
                "volume": item["volume"],
                "baseline_volume": item["baseline_volume"],
                "ratio": round(ratio, 2),
                "amount": item["amount"],
            },
            source="review",
            trade_date=trade_date,
            minute="15:00",
        )


async def analyze_volume() -> None:
    current = now_local()
    if not signal_window(current):
        return
    clock = current.strftime("%H:%M")
    trade_date = current.strftime("%Y-%m-%d")
    for item in _etfs:
        fraction = universe.shape_fraction(item["shape"], clock)
        expected = item["baseline_volume"] * fraction
        if expected <= 0:
            continue
        ratio = item["volume"] / expected
        item["volume_ratio"] = ratio
        if ratio >= VOLUME_SURGE_RATIO:
            _volume_streak[item["code"]] += 1
        else:
            _volume_streak[item["code"]] = 0
            continue
        if _volume_streak[item["code"]] < 2 or not _can_alert(item["code"], "volume_surge"):
            continue
        level = "high" if ratio >= VOLUME_SURGE_HIGH_RATIO else "warn"
        store.insert_alert(
            code=item["code"],
            name=item["name"],
            group_name=item["group_name"],
            alert_type="volume_surge",
            level=level,
            title=f"盘中放量：累计成交量达到基线 {ratio:.1f} 倍",
            detail=(
                f"{clock} 累计成交 {item['volume']:,.0f} 手，"
                f"基线同期 {expected:,.0f} 手，成交额 {_fmt_money(item['amount'])}。"
            ),
            metrics={
                "volume": item["volume"],
                "expected_volume": expected,
                "ratio": round(ratio, 2),
                "amount": item["amount"],
            },
            source="live",
            trade_date=trade_date,
            minute=clock,
        )
        _remember_alert(item["code"], "volume_surge")
    state["last_analysis"] = current.strftime("%Y-%m-%d %H:%M:%S")


def _tick_cache(code: str) -> dict:
    cache = _tick_state.get(code)
    if cache is None:
        cache = {"pages": 0, "date": "", "last_seq": 0}
        _tick_state[code] = cache
    return cache


async def _poll_one_etf(item: dict) -> None:
    code = item["code"]
    cache = _tick_cache(code)
    pages, trade_date = await market.fetch_tick_page_count(code, item["market"])
    if pages <= 0 or not trade_date:
        return
    if trade_date != cache["date"]:
        cache["date"] = trade_date
        cache["last_seq"] = 0
        cache["pages"] = 0

    if cache["pages"] <= 0:
        # 首次进入只跟踪最新一页，不回补盘中已经发生的历史成交
        targets = [pages - 1]
    else:
        # 上一页可能仍在追加成交，重读；中间新增页一并补齐，单轮最多 5 页
        start = max(cache["pages"] - 1, pages - 5, 0)
        targets = list(range(start, pages))
    cache["pages"] = pages

    ticks = []
    for page in targets:
        page_ticks = await market.fetch_tick_page(code, item["market"], page)
        if page_ticks:
            ticks.extend(page_ticks)
    if not ticks:
        return
    ticks.sort(key=lambda tick: tick["seq"])

    current = now_local()
    cutoff = current.replace(tzinfo=None)
    window = _tick_windows[code]
    for tick in ticks:
        if tick["seq"] <= cache["last_seq"]:
            continue
        cache["last_seq"] = tick["seq"]
        if tick["amount"] < LARGE_TRADE_AMOUNT:
            continue
        inserted = store.insert_big_tick(
            code,
            cache["date"],
            tick["seq"],
            tick["time"],
            tick["price"],
            tick["volume"],
            tick["amount"],
            tick["side"],
        )
        if not inserted:
            continue
        try:
            tick_at = datetime.fromisoformat(f"{cache['date']} {tick['time']}")
        except ValueError:
            continue
        window.append((tick_at, tick["amount"], tick["side"], tick["price"], tick["volume"]))
        while window and (cutoff - window[0][0]).total_seconds() > SUSTAINED_WINDOW_MINUTES * 60:
            window.popleft()

        if tick["side"] != "B":
            continue
        single_threshold = _single_alert_amount(item)
        if tick["amount"] >= single_threshold and _can_alert(code, "single_large_buy"):
            store.insert_alert(
                code=code,
                name=item["name"],
                group_name=item["group_name"],
                alert_type="single_large_buy",
                level="high",
                title=f"单笔大额买入 {_fmt_money(tick['amount'])}",
                detail=(
                    f"{tick['time']} 成交价 {tick['price']:.3f}，"
                    f"成交量 {tick['volume']:,.0f} 股，成交额 {_fmt_money(tick['amount'])}。"
                ),
                metrics={
                    "amount": tick["amount"],
                    "price": tick["price"],
                    "volume": tick["volume"],
                    "time": tick["time"],
                },
                source="live",
                trade_date=cache["date"],
                minute=tick["time"][:5],
            )
            _remember_alert(code, "single_large_buy")

        buys = [entry for entry in window if entry[2] == "B"]
        total = sum(entry[1] for entry in buys)
        sustained_threshold = _sustained_alert_amount(item)
        if (
            len(buys) >= SUSTAINED_COUNT
            and total >= sustained_threshold
            and _can_alert(code, "sustained_large_buy")
        ):
            largest = max(entry[1] for entry in buys)
            store.insert_alert(
                code=code,
                name=item["name"],
                group_name=item["group_name"],
                alert_type="sustained_large_buy",
                level="high",
                title=f"{SUSTAINED_WINDOW_MINUTES} 分钟内持续大额买入",
                detail=(
                    f"共 {len(buys)} 笔主动买入，合计 {_fmt_money(total)}，"
                    f"最大单笔 {_fmt_money(largest)}，现价 {tick['price']:.3f}。"
                ),
                metrics={
                    "count": len(buys),
                    "total_amount": total,
                    "largest": largest,
                    "window_minutes": SUSTAINED_WINDOW_MINUTES,
                },
                source="live",
                trade_date=cache["date"],
                minute=tick["time"][:5],
            )
            _remember_alert(code, "sustained_large_buy")


async def poll_ticks() -> None:
    if not _etfs or not signal_window(now_local()):
        return
    await asyncio.gather(*[_poll_one_etf(item) for item in _etfs])


async def refresh_fund_flow() -> None:
    if not _etfs:
        return

    async def one(item: dict) -> None:
        flow = await market.fetch_fund_flow(market.secid(item["code"], item["market"]))
        if flow:
            store.update_fund_flow(
                item["code"],
                flow["main_net"],
                flow["large_net"],
                flow["super_net"],
            )
            item["main_net"] = flow["main_net"]
            item["large_net"] = flow["large_net"]
            item["super_net"] = flow["super_net"]

    await asyncio.gather(*(one(item) for item in _etfs))


async def start() -> None:
    try:
        await bootstrap()
    except Exception as exc:
        state["status"] = "error"
        state["message"] = f"初始化失败：{exc}"
        return

    last_quote = last_ticks = last_analysis = last_flow = 0.0
    while True:
        current = now_local()
        state["market"] = market_phase(current)
        monotonic_now = time.monotonic()
        if state["market"] == "open":
            if monotonic_now - last_quote >= POLL_SECONDS:
                await refresh_quotes()
                last_quote = monotonic_now
            if monotonic_now - last_ticks >= POLL_SECONDS:
                await poll_ticks()
                last_ticks = monotonic_now
            if monotonic_now - last_analysis >= ANALYZE_SECONDS:
                await analyze_volume()
                last_analysis = monotonic_now
            if monotonic_now - last_flow >= FUND_FLOW_SECONDS:
                await refresh_fund_flow()
                last_flow = monotonic_now
        elif monotonic_now - last_quote >= 300:
            await refresh_quotes()
            last_quote = monotonic_now
        await asyncio.sleep(5)
