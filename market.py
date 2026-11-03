# 东方财富 / 腾讯公开行情接口封装
import asyncio
import json
import re
from datetime import datetime, timezone

import httpx

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Referer": "https://quote.eastmoney.com/",
}

_ETF_LIST_URL = "https://push2.eastmoney.com/api/qt/clist/get"
_DAILY_URL = "https://push2his.eastmoney.com/api/qt/stock/kline/get"
_TRENDS_URL = "https://push2his.eastmoney.com/api/qt/stock/trends2/get"
_QUOTE_URL = "https://push2.eastmoney.com/api/qt/ulist.np/get"
_FLOW_URL = "https://push2.eastmoney.com/api/qt/stock/fflow/kline/get"
_TICK_URL = "https://stock.gtimg.cn/data/index.php"

_client: httpx.AsyncClient | None = None
_semaphore = asyncio.Semaphore(5)


def _get_client() -> httpx.AsyncClient:
    global _client
    if _client is None:
        _client = httpx.AsyncClient(
            timeout=httpx.Timeout(15.0, connect=10.0),
            headers=HEADERS,
            limits=httpx.Limits(max_connections=12, max_keepalive_connections=6),
        )
    return _client


async def close() -> None:
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None


async def _get_text(url: str, params: dict | None = None, retries: int = 2) -> str:
    client = _get_client()
    last_error: Exception | None = None
    for attempt in range(retries + 1):
        try:
            async with _semaphore:
                response = await client.get(url, params=params)
            response.raise_for_status()
            return response.text
        except Exception as exc:  # 公开接口偶发超时，重试后仍失败则交给上层跳过
            last_error = exc
            if attempt < retries:
                await asyncio.sleep(0.6 * (attempt + 1))
    raise last_error


async def _get_json(url: str, params: dict | None = None) -> dict:
    text = await _get_text(url, params)
    return json.loads(text)


def secid(code: str, market: int) -> str:
    return f"{market}.{code}"


def tick_symbol(code: str, market: int) -> str:
    return ("sh" if market == 1 else "sz") + code


def _num(value, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


async def fetch_etf_list() -> list[dict]:
    """拉取沪深 ETF 全量列表，按今日成交额降序"""
    items: list[dict] = []
    for page in range(1, 20):
        payload = await _get_json(
            _ETF_LIST_URL,
            {
                "pn": page,
                "pz": 100,
                "po": 1,
                "np": 1,
                "fltt": 2,
                "invt": 2,
                "fid": "f6",
                "fs": "b:MK0021,b:MK0022,b:MK0023,b:MK0024",
                "fields": "f12,f14,f13,f6,f2",
            },
        )
        diff = (payload.get("data") or {}).get("diff") or []
        for row in diff:
            code = str(row.get("f12") or "").strip()
            name = str(row.get("f14") or "").strip()
            market = int(row.get("f13") or 0)
            if not code or not name:
                continue
            items.append(
                {
                    "code": code,
                    "name": name,
                    "market": market,
                    "amount": _num(row.get("f6")),
                    "price": _num(row.get("f2")),
                }
            )
        if len(diff) < 100:
            break
    return items


async def fetch_daily_bars(secid_value: str, end_date: str, limit: int) -> list[dict]:
    payload = await _get_json(
        _DAILY_URL,
        {
            "secid": secid_value,
            "klt": 101,
            "fqt": 1,
            "lmt": limit,
            "end": end_date.replace("-", ""),
            "fields1": "f1,f2,f3,f4,f5,f6",
            "fields2": "f51,f52,f53,f54,f55,f56,f57",
        },
    )
    rows = (payload.get("data") or {}).get("klines") or []
    bars = []
    for row in rows:
        parts = row.split(",")
        if len(parts) < 7:
            continue
        bars.append(
            {
                "date": parts[0],
                "volume": _num(parts[5]),
                "amount": _num(parts[6]),
            }
        )
    return bars


async def fetch_trends(secid_value: str, ndays: int = 5) -> list[dict]:
    """分时数据，返回每个点的日期、时间和成交量（手）"""
    payload = await _get_json(
        _TRENDS_URL,
        {
            "secid": secid_value,
            "ndays": ndays,
            "iscr": 0,
            "fields1": "f1,f2,f3,f4,f5,f6,f7,f8,f9,f10,f11,f12,f13",
            "fields2": "f51,f52,f53,f54,f55,f56,f57,f58",
        },
    )
    rows = (payload.get("data") or {}).get("trends") or []
    points = []
    for row in rows:
        parts = row.split(",")
        if len(parts) < 6:
            continue
        stamp = parts[0]
        try:
            day, clock = stamp.split(" ")
        except ValueError:
            continue
        points.append({"date": day, "time": clock, "volume": _num(parts[5])})
    return points


async def fetch_quotes(secids: list[str]) -> list[dict]:
    quotes: list[dict] = []
    for start in range(0, len(secids), 50):
        chunk = secids[start : start + 50]
        payload = await _get_json(
            _QUOTE_URL,
            {
                "secids": ",".join(chunk),
                "fltt": 2,
                "fields": "f2,f3,f5,f6,f12,f14,f124",
            },
        )
        diff = (payload.get("data") or {}).get("diff") or []
        for row in diff:
            stamp = int(_num(row.get("f124")))
            updated = (
                datetime.fromtimestamp(stamp, timezone.utc)
                .astimezone()
                .strftime("%Y-%m-%d %H:%M:%S")
                if stamp
                else ""
            )
            quotes.append(
                {
                    "code": str(row.get("f12") or ""),
                    "name": str(row.get("f14") or ""),
                    "price": _num(row.get("f2")),
                    "change_pct": _num(row.get("f3")),
                    "volume": _num(row.get("f5")),
                    "amount": _num(row.get("f6")),
                    "updated_at": updated,
                }
            )
    return quotes


async def fetch_fund_flow(secid_value: str) -> dict | None:
    payload = await _get_json(
        _FLOW_URL,
        {
            "secid": secid_value,
            "klt": 1,
            "lmt": 0,
            "fields1": "f1,f2,f3,f7",
            "fields2": "f51,f52,f53,f54,f55,f56",
        },
    )
    rows = (payload.get("data") or {}).get("klines") or []
    if not rows:
        return None
    parts = rows[-1].split(",")
    if len(parts) < 6:
        return None
    return {
        "minute": parts[0],
        "main_net": _num(parts[1]),
        "small_net": _num(parts[2]),
        "medium_net": _num(parts[3]),
        "large_net": _num(parts[4]),
        "super_net": _num(parts[5]),
    }


async def fetch_tick_page_count(code: str, market: int) -> tuple[int, str]:
    """返回逐笔页数与交易日"""
    symbol = tick_symbol(code, market)
    text = await _get_text(
        _TICK_URL, {"appn": "detail", "action": "count", "c": symbol}
    )
    match = re.search(r'\[(\d{8}),"([^"]*)"\]', text)
    if not match:
        return 0, ""
    date_text = match.group(1)
    ranges = [item for item in match.group(2).split("|") if item]
    return len(ranges), f"{date_text[:4]}-{date_text[4:6]}-{date_text[6:8]}"


async def fetch_tick_page(code: str, market: int, page: int) -> list[dict]:
    symbol = tick_symbol(code, market)
    text = await _get_text(
        _TICK_URL,
        {"appn": "detail", "action": "data", "c": symbol, "p": page},
    )
    match = re.search(r'=\[\d+,"([^"]*)"\]', text)
    if not match:
        return []
    ticks = []
    for record in match.group(1).split("|"):
        parts = record.split("/")
        if len(parts) < 7:
            continue
        try:
            seq = int(parts[0])
        except ValueError:
            continue
        ticks.append(
            {
                "seq": seq,
                "time": parts[1],
                "price": _num(parts[2]),
                "volume": _num(parts[4]),
                "amount": _num(parts[5]),
                "side": parts[6].strip().upper(),
            }
        )
    return ticks
