import asyncio
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

METALS_SYMBOLS = [("GC=F", "gold"), ("SI=F", "silver"), ("PL=F", "platinum")]

YF_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "*/*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://finance.yahoo.com/",
}

_yf_cookies: dict = {}


@app.on_event("startup")
async def _warmup():
    """Pre-fetch Yahoo Finance session cookies once at startup."""
    await _init_yf_session()


async def _init_yf_session():
    global _yf_cookies
    try:
        async with httpx.AsyncClient(
            headers=YF_HEADERS, follow_redirects=True, timeout=15
        ) as client:
            await client.get("https://finance.yahoo.com/")
            _yf_cookies = dict(client.cookies)
            print(f"Yahoo session OK ({len(_yf_cookies)} cookies)")
    except Exception as e:
        print(f"Yahoo session init error: {e}")


async def _yf_chart(client: httpx.AsyncClient, sym: str):
    """Fetch latest price + change via Yahoo Finance v8/chart."""
    try:
        r = await client.get(
            f"https://query2.finance.yahoo.com/v8/finance/chart/{sym}",
            params={"interval": "1d", "range": "5d"},
            timeout=12,
        )
        if r.status_code != 200:
            print(f"Yahoo chart {sym}: HTTP {r.status_code}")
            return None, None
        data = r.json()
        result = data.get("chart", {}).get("result")
        if not result:
            print(f"Yahoo chart {sym}: empty result")
            return None, None
        meta  = result[0]["meta"]
        price = meta.get("regularMarketPrice")
        prev  = meta.get("chartPreviousClose")
        if price is None:
            return None, None
        chg = (price - prev) / prev * 100 if prev else None
        return round(price, 2), (round(chg, 2) if chg is not None else None)
    except Exception as e:
        print(f"Yahoo chart error [{sym}]: {e}")
        return None, None


async def fetch_stocks(symbols: list[str]) -> dict:
    if not _yf_cookies:
        await _init_yf_session()

    async with httpx.AsyncClient(
        headers=YF_HEADERS,
        cookies=_yf_cookies,
        follow_redirects=True,
        timeout=20,
    ) as client:
        tasks = [_yf_chart(client, sym) for sym in symbols]
        results = await asyncio.gather(*tasks)

    return {
        sym: {"price": price, "change_pct": chg}
        for sym, (price, chg) in zip(symbols, results)
        if price is not None
    }


# ── FX ────────────────────────────────────────────────────

async def fetch_fx() -> dict:
    try:
        async with httpx.AsyncClient(timeout=10, follow_redirects=True) as client:
            r = await client.get(
                "https://api.frankfurter.dev/v1/latest",
                params={"from": "USD", "to": "TRY,EUR,GBP"},
            )
            r.raise_for_status()
            rates = r.json().get("rates", {})
            t, e, g = rates.get("TRY"), rates.get("EUR"), rates.get("GBP")
            return {
                "usd_try": round(t, 4)       if t       else None,
                "eur_try": round(t / e, 4)    if t and e else None,
                "gbp_try": round(t / g, 4)    if t and g else None,
                "eur_usd": round(1 / e, 4)    if e       else None,
            }
    except Exception as ex:
        print(f"FX error: {ex}")
        return {}


# ── CRYPTO (CoinCap — free, no auth, no geo-block) ───────

async def fetch_crypto() -> dict:
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(
                "https://api.coincap.io/v2/assets",
                params={"ids": "bitcoin,ethereum,solana", "limit": "3"},
            )
            r.raise_for_status()
        return {
            item["id"]: {
                "price":     float(item["priceUsd"]),
                "change24h": float(item["changePercent24Hr"]),
            }
            for item in r.json().get("data", [])
            if item.get("priceUsd") and item.get("changePercent24Hr")
        }
    except Exception as ex:
        print(f"Crypto error: {ex}")
        return {}


# ── MARKET ────────────────────────────────────────────────

@app.get("/market")
async def market(symbols: Optional[str] = Query(default=None)):
    user_syms = (
        [s.strip() for s in symbols.split(",") if s.strip()]
        if symbols else DEFAULT_SYMBOLS
    )

    # Single Yahoo call for stocks + metals futures
    metal_syms = [s for s, _ in METALS_SYMBOLS]
    all_syms   = list(dict.fromkeys(user_syms + metal_syms))

    fx_task     = asyncio.create_task(fetch_fx())
    crypto_task = asyncio.create_task(fetch_crypto())
    all_quotes  = await fetch_stocks(all_syms)

    metals = {}
    for yf_sym, key in METALS_SYMBOLS:
        if yf_sym in all_quotes:
            metals[key] = all_quotes.pop(yf_sym)["price"]

    stocks = {k: v for k, v in all_quotes.items() if k in user_syms}

    return {
        "fx":     await fx_task,
        "crypto": await crypto_task,
        "metals": metals,
        "stocks": stocks,
    }


@app.get("/health")
async def health():
    return {"status": "ok"}
