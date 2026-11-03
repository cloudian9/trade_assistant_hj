# ETF 交易监测服务入口
import asyncio
from contextlib import asynccontextmanager
from datetime import datetime
from zoneinfo import ZoneInfo

import uvicorn
from fastapi import FastAPI, HTTPException, Query
from fastapi.staticfiles import StaticFiles

import market
import monitor
import store
from config import BASELINE_DAYS, BASELINE_END, BASE_DIR, PORT, VOLUME_SURGE_RATIO

TZ = ZoneInfo("Asia/Shanghai")


@asynccontextmanager
async def lifespan(_: FastAPI):
    store.init_db()
    task = asyncio.create_task(monitor.start())
    yield
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    await market.close()


app = FastAPI(title="ETF 交易监测助手", lifespan=lifespan)


def _public_etf(item: dict) -> dict:
    return {key: value for key, value in item.items() if key != "shape"}


@app.get("/api/dashboard")
def dashboard(
    group: str = Query(default=""),
    alert_type: str = Query(default=""),
):
    etfs = [_public_etf(item) for item in store.list_etfs()]
    alerts = store.list_alerts(group, alert_type, 100)
    trade_date = datetime.now(TZ).strftime("%Y-%m-%d")
    surge = [item for item in etfs if item["volume_ratio"] >= VOLUME_SURGE_RATIO]
    ranking = sorted(etfs, key=lambda item: item["volume_ratio"], reverse=True)[:8]
    return {
        "status": monitor.snapshot(),
        "summary": {
            "monitored": len(etfs),
            "surge_count": len(surge),
            "alert_count": store.count_alerts(trade_date),
            "big_tick_count": store.count_big_ticks(trade_date),
        },
        "baseline": {
            "end": BASELINE_END,
            "days": BASELINE_DAYS,
            "label": "10 月 14 日前 20 日均量",
        },
        "trade_date": trade_date,
        "ranking": ranking,
        "etfs": etfs,
        "alerts": alerts,
    }


@app.get("/api/alerts")
def alerts(
    group: str = Query(default=""),
    alert_type: str = Query(default=""),
    limit: int = Query(default=100, ge=1, le=200),
):
    return store.list_alerts(group, alert_type, limit)


@app.post("/api/alerts/{alert_id}/read")
def read_alert(alert_id: int):
    if not store.mark_alert_read(alert_id):
        raise HTTPException(status_code=404, detail="告警不存在")
    return {"ok": True}


@app.get("/api/health")
def health():
    return monitor.snapshot()


app.mount("/", StaticFiles(directory=BASE_DIR / "static", html=True), name="static")


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=PORT)
