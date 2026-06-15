import asyncio
import httpx
import yfinance as yf
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI(title="Startpage Market API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET"],
    allow_headers=["*"],
)

STOCK_SYMBOLS = [
    "THYAO.IS", "GARAN.IS", "AKBNK.IS", "EREGL.IS",
    "ASELS.IS", "SISE.IS", "KCHOL.IS", "XU100.IS",
    "^GSPC", "^NDX", "^GDAXI", "CL=F",
]


async def fetch_fx():
    try:
        async with httpx.AsyncClient(timeout=8) as client:
            r = await client.get(
                "https://api.frankfurter.app/latest",
                params={"from": "USD", "to": "TRY,EUR,GBP"},
            )
            r.raise_for_status()
            data = r.json()
            rates = data.get("rates", {})
            try_r = rates.get("TRY")
            eur_r = rates.get("EUR")
            gbp_r = rates.get("GBP")
            return {
                "usd_try":  round(try_r, 4) if try_r else None,
                "eur_try":  round(try_r / eur_r, 4) if try_r and eur_r else None,
                "gbp_try":  round(try_r / gbp_r, 4) if try_r and gbp_r else None,
                "eur_usd":  round(1 / eur_r, 4) if eur_r else None,
            }
    except Exception:
        return {}


async def fetch_crypto():
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
            result = {}
            for coin in ("bitcoin", "ethereum", "solana"):
                if coin in data:
                    result[coin] = {
                        "price": data[coin].get("usd"),
                        "change24h": data[coin].get("usd_24h_change"),
                    }
            return result
    except Exception:
        return {}


async def fetch_metals():
    try:
        async with httpx.AsyncClient(timeout=8) as client:
            r = await client.get("https://api.metals.live/v1/spot", timeout=8)
            r.raise_for_status()
            arr = r.json()
            gold = silver = platinum = None
            for item in arr:
                if item.get("gold") is not None:
                    gold = item["gold"]
                if item.get("silver") is not None:
                    silver = item["silver"]
                if item.get("platinum") is not None:
                    platinum = item["platinum"]
            return {"gold": gold, "silver": silver, "platinum": platinum}
    except Exception:
        return {}


def fetch_stocks_sync():
    try:
        tickers = yf.Tickers(" ".join(STOCK_SYMBOLS))
        result = {}
        for sym in STOCK_SYMBOLS:
            try:
                info = tickers.tickers[sym].fast_info
                price = getattr(info, "last_price", None)
                prev  = getattr(info, "previous_close", None)
                if price is None:
                    continue
                change_pct = ((price - prev) / prev * 100) if prev else None
                result[sym] = {
                    "price":     round(price, 2),
                    "change_pct": round(change_pct, 2) if change_pct is not None else None,
                }
            except Exception:
                continue
        return result
    except Exception:
        return {}


@app.get("/market")
async def market():
    fx_task     = asyncio.create_task(fetch_fx())
    crypto_task = asyncio.create_task(fetch_crypto())
    metals_task = asyncio.create_task(fetch_metals())

    stocks = await asyncio.get_event_loop().run_in_executor(None, fetch_stocks_sync)

    fx     = await fx_task
    crypto = await crypto_task
    metals = await metals_task

    return {
        "fx":     fx,
        "crypto": crypto,
        "metals": metals,
        "stocks": stocks,
    }


@app.get("/health")
async def health():
    return {"status": "ok"}
