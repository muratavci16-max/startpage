---
title: Startpage Market API
emoji: 📈
colorFrom: orange
colorTo: red
sdk: docker
pinned: false
---

# Startpage Market API

Single endpoint that returns FX, crypto, metals and stock data — used by the startpage project.

## Endpoint

`GET /market`

```json
{
  "fx":     { "usd_try": 38.5, "eur_try": 41.2, "gbp_try": 48.1, "eur_usd": 1.071 },
  "crypto": { "bitcoin": { "price": 65000, "change24h": 1.5 }, ... },
  "metals": { "gold": 2350, "silver": 27.5, "platinum": 980 },
  "stocks": { "THYAO.IS": { "price": 123.4, "change_pct": 1.2 }, ... }
}
```

`GET /health` → `{"status": "ok"}`
