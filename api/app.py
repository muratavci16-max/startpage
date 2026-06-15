import asyncio
import json
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

METALS_SYMBOLS = ["GC=F", "SI=F", "PL=F"]   # Gold, Silver, Platinum futures

YF_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "*/*",
    "Accept-Language": "en-US,en;q=0.9",
    "Origin": "https://finance.yahoo.com",
    "Referer": "https://finance.yahoo.com/",
}

_crumb: Optional[str] = None
_yf_cookies: dict = {}


async def _refresh_crumb() -> bool:
    global _crumb, _yf_cookies
    try:
        async with httpx.AsyncClient(
            headers=YF_HEADERS, follow_redirects=True, timeout=15
        ) as client:
            await client.get("https://finance.yahoo.com/")
            r = await client.get("https://query2.finance.yahoo.com/v1/test/getcrumb")
            if r.status_code == 200 and r.text.strip():
                _crumb = r.text.strip()
                _yf_cookies = dict(client.cookies)
                print(f"Yahoo crumb OK: {_crumb[:8]}...")
                return True
            print(f"Crumb response: {r.status_code} — {r.text[:80]}")
    except Exception as e:
        print(f"Crumb refresh error: {e}")
    return False


async def _yahoo_quote(symbols: list[str]) -> dict:
    """Fetch regularMarketPrice + regularMarketChangePercent via Yahoo v7/quote."""
    global _crumb, _yf_cookies

    if not _crumb:
        await _refresh_crumb()
    if not _crumb:
        return {}

    sym_str = ",".join(symbols)
    url = (
        f"https://query1.finance.yahoo.com/v7/finance/quote"
        f"?symbols={sym_str}&crumb={_crumb}"
        f"&fields=regularMarketPrice,regularMarketChangePercent"
    )

    result = {}
    for attempt in range(2):
        try:
            async with httpx.AsyncClient(
                headers=YF_HEADERS,
                cookies=_yf_cookies,
                follow_redirects=True,
                timeout=15,
            ) as client:
                r = await client.get(url)
            if r.status_code == 401 and attempt == 0:
                print("Crumb expired — refreshing...")
                await _refresh_crumb()
                url = (
                    f"https://query1.finance.yahoo.com/v7/finance/quote"
                    f"?symbols={sym_str}&crumb={_crumb}"
                    f"&fields=regularMarketPrice,regularMarketChangePercent"
                )
                continue
            if r.status_code != 200:
                print(f"Yahoo quote {r.status_code}: {r.text[:120]}")
                break
            for item in r.json().get("quoteResponse", {}).get("result", []):
                sym   = item.get("symbol")
                price = item.get("regularMarketPrice")
                chg   = item.get("regularMarketChangePercent")
                if sym and price is not None:
                    result[sym] = {
                        "price":      round(price, 2),
                        "change_pct": round(chg, 2) if chg is not None else None,
                    }
            break
        except Exception as e:
            print(f"Yahoo quote error (attempt {attempt}): {e}")
            break

    return result


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
                "usd_try": round(t, 4)       if t           else None,
                "eur_try": round(t / e, 4)    if t and e     else None,
                "gbp_try": round(t / g, 4)    if t and g     else None,
                "eur_usd": round(1 / e, 4)    if e           else None,
            }
    except Exception as ex:
        print(f"FX error: {ex}")
        return {}


# ── CRYPTO (Binance — free, no rate limits) ───────────────

async def fetch_crypto() -> dict:
    try:
        syms_json = json.dumps(["BTCUSDT", "ETHUSDT", "SOLUSDT"])
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(
                "https://api.binance.com/api/v3/ticker/24hr",
                params={"symbols": syms_json},
            )
            r.raise_for_status()
        mapping = {"BTCUSDT": "bitcoin", "ETHUSDT": "ethereum", "SOLUSDT": "solana"}
        return {
            mapping[item["symbol"]]: {
                "price":     float(item["lastPrice"]),
                "change24h": float(item["priceChangePercent"]),
            }
            for item in r.json()
            if item["symbol"] in mapping
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

    # Fetch user stocks + metals futures in one Yahoo call
    all_syms = list(dict.fromkeys(user_syms + METALS_SYMBOLS))

    fx_task     = asyncio.create_task(fetch_fx())
    crypto_task = asyncio.create_task(fetch_crypto())
    all_quotes  = await _yahoo_quote(all_syms)

    # Split metals out
    metals = {}
    for yf_sym, key in [("GC=F", "gold"), ("SI=F", "silver"), ("PL=F", "platinum")]:
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
