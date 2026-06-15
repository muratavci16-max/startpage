import asyncio
from datetime import date, timedelta
from typing import Optional

import httpx
from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI(title="Startpage Market API")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET"],
    allow_headers=["*"],
)

DEFAULT_SYMBOLS = [
    "THYAO.IS", "GARAN.IS", "AKBNK.IS", "EREGL.IS",
    "ASELS.IS", "SISE.IS", "KCHOL.IS", "XU100.IS",
    "^GSPC", "^NDX", "^GDAXI", "CL=F",
]

# Yahoo Finance → Stooq symbol mapping
STOOQ_MAP = {
    "^GSPC":  "^spx",
    "^NDX":   "^ndq",
    "^GDAXI": "^dax",
    "^DJI":   "^dji",
    "^IXIC":  "^ixic",
    "^FTSE":  "^ukx",
    "^N225":  "^nkx",
    "CL=F":   "cl.f",
    "GC=F":   "gc.f",
    "SI=F":   "si.f",
    "BTC-USD": "btc.v",
    "ETH-USD": "eth.v",
}

STOOQ_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "text/html,*/*",
}


def to_stooq(sym: str) -> str:
    upper = sym.upper()
    if upper in STOOQ_MAP:
        return STOOQ_MAP[upper]
    if upper.endswith(".IS"):
        return sym.lower()
    # Plain US ticker: AAPL → aapl.us
    if not sym.startswith("^") and "." not in sym and "=" not in sym and "-" not in sym:
        return sym.lower() + ".us"
    return sym.lower()


async def fetch_stock_stooq(client: httpx.AsyncClient, sym: str):
    stooq_sym = to_stooq(sym)
    d_to   = date.today().strftime("%Y%m%d")
    d_from = (date.today() - timedelta(days=14)).strftime("%Y%m%d")
    url = f"https://stooq.com/q/d/l/?s={stooq_sym}&d1={d_from}&d2={d_to}&i=d"
    try:
        r = await client.get(url, timeout=12)
        lines = [l for l in r.text.strip().splitlines() if l and not l.lower().startswith("date")]
        if not lines:
            return None, None
        # Each row: Date,Open,High,Low,Close,Volume
        close = float(lines[-1].split(",")[4])
        prev  = float(lines[-2].split(",")[4]) if len(lines) > 1 else None
        chg   = (close - prev) / prev * 100 if prev else None
        return round(close, 2), (round(chg, 2) if chg is not None else None)
    except Exception as e:
        print(f"Stooq failed [{sym}→{stooq_sym}]: {e}")
        return None, None


async def fetch_stocks(symbols: list[str]) -> dict:
    async with httpx.AsyncClient(headers=STOOQ_HEADERS, follow_redirects=True) as client:
        tasks = [fetch_stock_stooq(client, sym) for sym in symbols]
        results = await asyncio.gather(*tasks)
    out = {}
    for sym, (price, chg) in zip(symbols, results):
        if price is not None:
            out[sym] = {"price": price, "change_pct": chg}
    return out


async def fetch_fx() -> dict:
    try:
        async with httpx.AsyncClient(timeout=8) as client:
            r = await client.get(
                "https://api.frankfurter.app/latest",
                params={"from": "USD", "to": "TRY,EUR,GBP"},
            )
            r.raise_for_status()
            rates = r.json().get("rates", {})
            t, e, g = rates.get("TRY"), rates.get("EUR"), rates.get("GBP")
            return {
                "usd_try": round(t, 4)       if t           else None,
                "eur_try": round(t / e, 4)    if t and e     else None,
                "gbp_try": round(t / g, 4)    if t and g     else None,
                "eur_usd": round(1 / e, 4)    if e           else None,
            }
    except Exception as ex:
        print(f"FX error: {ex}")
        return {}


async def fetch_crypto() -> dict:
    try:
        async with httpx.AsyncClient(timeout=8) as client:
            r = await client.get(
                "https://api.coingecko.com/api/v3/simple/price",
                params={
                    "ids": "bitcoin,ethereum,solana",
                    "vs_currencies": "usd",
                    "include_24hr_change": "true",
                },
            )
            r.raise_for_status()
            data = r.json()
        return {
            coin: {
                "price":     data[coin].get("usd"),
                "change24h": data[coin].get("usd_24h_change"),
            }
            for coin in ("bitcoin", "ethereum", "solana")
            if coin in data
        }
    except Exception as ex:
        print(f"Crypto error: {ex}")
        return {}


async def fetch_metals() -> dict:
    try:
        async with httpx.AsyncClient(timeout=8) as client:
            r = await client.get("https://api.metals.live/v1/spot")
            r.raise_for_status()
            arr = r.json()
        gold = silver = platinum = None
        for item in arr:
            if item.get("gold")     is not None: gold     = item["gold"]
            if item.get("silver")   is not None: silver   = item["silver"]
            if item.get("platinum") is not None: platinum = item["platinum"]
        return {"gold": gold, "silver": silver, "platinum": platinum}
    except Exception as ex:
        print(f"Metals error: {ex}")
        return {}


@app.get("/market")
async def market(symbols: Optional[str] = Query(default=None)):
    sym_list = (
        [s.strip() for s in symbols.split(",") if s.strip()]
        if symbols else DEFAULT_SYMBOLS
    )

    fx_task     = asyncio.create_task(fetch_fx())
    crypto_task = asyncio.create_task(fetch_crypto())
    metals_task = asyncio.create_task(fetch_metals())
    stocks      = await fetch_stocks(sym_list)

    return {
        "fx":     await fx_task,
        "crypto": await crypto_task,
        "metals": await metals_task,
        "stocks": stocks,
    }


@app.get("/health")
async def health():
    return {"status": "ok"}
