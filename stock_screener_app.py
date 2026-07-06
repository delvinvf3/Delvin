import datetime as dt
import json
import math
import os
import statistics
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


PORT = int(os.environ.get("PORT", "8765"))
HOST = os.environ.get("HOST") or ("0.0.0.0" if os.environ.get("PORT") else "127.0.0.1")
USER_AGENT = "Mozilla/5.0"


def fetch_json(url):
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=18) as response:
        return json.loads(response.read().decode("utf-8"))


def yahoo_search(query):
    url = "https://query2.finance.yahoo.com/v1/finance/search?" + urllib.parse.urlencode(
        {"q": query, "quotesCount": 8, "newsCount": 0}
    )
    data = fetch_json(url)
    results = []
    for item in data.get("quotes", []):
        if item.get("quoteType") != "EQUITY":
            continue
        results.append(
            {
                "symbol": item.get("symbol", ""),
                "name": item.get("longname") or item.get("shortname") or "",
                "exchange": item.get("exchDisp") or item.get("exchange") or "",
                "type": item.get("typeDisp") or "Equity",
            }
        )
    return results


def sma(values, window):
    output = []
    for idx in range(len(values)):
        if idx + 1 < window:
            output.append(None)
        else:
            chunk = values[idx + 1 - window : idx + 1]
            output.append(sum(chunk) / window)
    return output


def rolling_std(values, window):
    output = []
    for idx in range(len(values)):
        if idx + 1 < window:
            output.append(None)
        else:
            chunk = values[idx + 1 - window : idx + 1]
            mean = sum(chunk) / window
            output.append(math.sqrt(sum((value - mean) ** 2 for value in chunk) / window))
    return output


def ema(values, span):
    alpha = 2 / (span + 1)
    output = []
    previous = None
    for value in values:
        previous = value if previous is None else alpha * value + (1 - alpha) * previous
        output.append(previous)
    return output


def rsi(values, period=14):
    output = [None] * len(values)
    if len(values) <= period:
        return output
    gains = []
    losses = []
    for idx in range(1, period + 1):
        change = values[idx] - values[idx - 1]
        gains.append(max(change, 0))
        losses.append(abs(min(change, 0)))
    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period
    output[period] = 100 if avg_loss == 0 else 100 - (100 / (1 + avg_gain / avg_loss))
    for idx in range(period + 1, len(values)):
        change = values[idx] - values[idx - 1]
        gain = max(change, 0)
        loss = abs(min(change, 0))
        avg_gain = (avg_gain * (period - 1) + gain) / period
        avg_loss = (avg_loss * (period - 1) + loss) / period
        output[idx] = 100 if avg_loss == 0 else 100 - (100 / (1 + avg_gain / avg_loss))
    return output


def pct_change(start, end):
    if start in (None, 0) or end is None:
        return None
    return (end - start) / start * 100


FUNDAMENTAL_FIELDS = [
    "trailingMarketCap",
    "annualMarketCap",
    "annualBasicAverageShares",
    "annualTotalRevenue",
    "annualNetIncome",
    "annualOperatingExpense",
    "annualEBITDA",
    "annualGrossProfit",
    "annualFreeCashFlow",
    "annualTotalDebt",
    "annualStockholdersEquity",
    "annualDilutedAverageShares",
    "trailingPeRatio",
    "trailingPbRatio",
    "trailingEnterprisesValueEBITDARatio",
    "trailingEnterprisesValueRevenueRatio",
]


INDUSTRY_RULES = {
    "Technology / Semiconductors": {"pe": (20, 45), "pbv": (4, 25), "evebitda": (12, 30)},
    "Banks": {"pe": (5, 15), "pbv": (0.8, 2.5), "evebitda": None},
    "Property": {"pe": (6, 15), "pbv": (0.4, 1.5), "evebitda": (6, 14)},
    "Mining / Commodities": {"pe": (4, 12), "pbv": (0.6, 2.0), "evebitda": (3, 8)},
    "Consumer Goods": {"pe": (12, 28), "pbv": (1, 5), "evebitda": (8, 18)},
    "Infrastructure / Utilities": {"pe": (8, 20), "pbv": (0.7, 2.5), "evebitda": (7, 15)},
    "Other": {"pe": (10, 25), "pbv": (1, 4), "evebitda": (8, 16)},
}


def clamp(value, low=0, high=100):
    return max(low, min(high, value))


def raw_value(entry):
    if not entry:
        return None
    reported = entry.get("reportedValue", {})
    return reported.get("raw")


def fmt_value(entry):
    if not entry:
        return None
    reported = entry.get("reportedValue", {})
    return reported.get("fmt")


def latest_entry(items):
    if not items:
        return None
    return sorted(items, key=lambda item: item.get("asOfDate", ""))[-1]


def annual_values(items):
    values = []
    for item in sorted(items or [], key=lambda row: row.get("asOfDate", "")):
        value = raw_value(item)
        if value is not None:
            values.append({"date": item.get("asOfDate"), "value": value, "fmt": fmt_value(item)})
    return values


def cagr(values):
    if len(values) < 2:
        return None
    first = values[0]["value"]
    last = values[-1]["value"]
    years = max(1, len(values) - 1)
    if first <= 0 or last <= 0:
        return None
    return ((last / first) ** (1 / years) - 1) * 100


def positive_steps(values):
    if len(values) < 2:
        return 0
    return sum(1 for idx in range(1, len(values)) if values[idx]["value"] >= values[idx - 1]["value"])


def trend_score(values, higher_better=True, base=50, scale=2.0):
    if len(values) < 2:
        return None
    growth = cagr(values)
    consistency = positive_steps(values) / max(1, len(values) - 1)
    if growth is None:
        raw = base + (consistency - 0.5) * 30
    elif higher_better:
        raw = base + growth * scale + (consistency - 0.5) * 30
    else:
        raw = base - growth * scale + (1 - consistency - 0.5) * 30
    return round(clamp(raw))


def margin_series(revenue, net_income):
    by_date = {row["date"]: row["value"] for row in revenue}
    margins = []
    for row in net_income:
        revenue_value = by_date.get(row["date"])
        if revenue_value:
            margin = row["value"] / revenue_value * 100
            margins.append({"date": row["date"], "value": margin, "fmt": f"{margin:.1f}%"})
    return margins


def ratio_series(numerator, denominator):
    by_date = {row["date"]: row["value"] for row in denominator}
    ratios = []
    for row in numerator:
        denominator_value = by_date.get(row["date"])
        if denominator_value:
            ratio = row["value"] / denominator_value * 100
            ratios.append({"date": row["date"], "value": ratio, "fmt": f"{ratio:.1f}%"})
    return ratios


def margin_score(margins):
    if len(margins) < 1:
        return None
    latest = margins[-1]["value"]
    trend = trend_score(margins, higher_better=True, base=50, scale=1.2) if len(margins) > 1 else 50
    level = clamp(45 + latest * 1.2)
    return round(clamp(level * 0.55 + trend * 0.45))


def opex_score(opex_ratios):
    if len(opex_ratios) < 1:
        return None
    latest = opex_ratios[-1]["value"]
    trend = trend_score(opex_ratios, higher_better=False, base=55, scale=1.3) if len(opex_ratios) > 1 else 55
    level = clamp(100 - latest * 1.35)
    return round(clamp(level * 0.45 + trend * 0.55))


def valuation_score(value, fair_range):
    if value is None or fair_range is None or value <= 0:
        return None
    low, high = fair_range
    if low <= value <= high:
        return 82
    if value < low:
        if value < low * 0.45:
            return 55
        return 74
    over = (value - high) / high
    return round(clamp(78 - over * 70, 20, 78))


def market_cap_score(value, currency):
    if value is None:
        return None
    if currency == "IDR":
        if value >= 100_000_000_000_000:
            return 85
        if value >= 10_000_000_000_000:
            return 70
        return 50
    if value >= 10_000_000_000:
        return 85
    if value >= 1_000_000_000:
        return 70
    return 50


def weighted_score(items):
    available = [(score, weight) for score, weight in items if score is not None]
    if not available:
        return None
    total_weight = sum(weight for _, weight in available)
    return round(sum(score * weight for score, weight in available) / total_weight)


def latest_number(values, offset=1):
    if len(values) < offset:
        return None
    return values[-offset]


def pct_distance(value, reference):
    if value is None or reference in (None, 0):
        return None
    return (value - reference) / reference * 100


def recent_cross_above(fast, slow, lookback):
    start = max(1, len(fast) - lookback)
    for idx in range(start, len(fast)):
        if None in (fast[idx], slow[idx], fast[idx - 1], slow[idx - 1]):
            continue
        if fast[idx - 1] <= slow[idx - 1] and fast[idx] > slow[idx]:
            return True
    return False


def rising_over(values, lookback):
    if len(values) <= lookback:
        return False
    start = values[-lookback - 1]
    end = values[-1]
    if start is None or end is None:
        return False
    return end > start


def score_band(value, low_value, high_value, low_score=20, high_score=90):
    if value is None:
        return None
    if value <= low_value:
        return low_score
    if value >= high_value:
        return high_score
    ratio = (value - low_value) / (high_value - low_value)
    return round(low_score + ratio * (high_score - low_score))


def annualized_volatility(closes):
    sample = closes[-252:] if len(closes) >= 252 else closes
    if len(sample) < 30:
        return None
    returns = []
    for idx in range(1, len(sample)):
        if sample[idx - 1]:
            returns.append((sample[idx] - sample[idx - 1]) / sample[idx - 1])
    if len(returns) < 20:
        return None
    return statistics.stdev(returns) * math.sqrt(252) * 100


def max_drawdown_pct(closes):
    sample = closes[-252:] if len(closes) >= 252 else closes
    if not sample:
        return None
    peak = sample[0]
    worst = 0
    for value in sample:
        peak = max(peak, value)
        if peak:
            worst = min(worst, (value - peak) / peak * 100)
    return abs(worst)


def debt_risk_from_ratio(value, industry):
    if value is None:
        return None
    if industry == "Banks":
        return None
    if value <= 0.5:
        return 20
    if value <= 1:
        return 40
    if value <= 2:
        return 65
    if value <= 3:
        return 82
    return 95


def risk_level(score):
    if score is None:
        return "Unknown"
    if score <= 25:
        return "Low"
    if score <= 50:
        return "Medium"
    if score <= 75:
        return "High"
    return "Very High"


def suggested_allocation(score):
    if score is None:
        return "Research first"
    if score <= 25:
        return "10-15%"
    if score <= 50:
        return "5-10%"
    if score <= 75:
        return "2-5%"
    return "0-2% / watchlist only"


def concentration_risk(industry):
    if industry == "Technology / Semiconductors":
        return 70
    if industry == "Mining / Commodities":
        return 65
    if industry == "Property":
        return 58
    if industry == "Banks":
        return 45
    if industry == "Infrastructure / Utilities":
        return 42
    if industry == "Consumer Goods":
        return 38
    return 50


def calculate_risk(closes, high_52w, last_close, one_year, fundamentals, industry):
    inputs = fundamentals.get("riskInputs", {}) if isinstance(fundamentals, dict) else {}
    vol = annualized_volatility(closes)
    drawdown = max_drawdown_pct(closes)
    drawdown_from_high = pct_change(high_52w, last_close)
    drawdown_from_high_abs = abs(drawdown_from_high) if drawdown_from_high is not None else None
    valuation_risk = inputs.get("valuationRisk")
    debt_risk = inputs.get("debtRisk")
    profit_risk = inputs.get("profitRisk")
    growth_risk = inputs.get("growthRisk")
    concentration = concentration_risk(industry)
    items = [
        {
            "label": "Price volatility",
            "score": score_band(vol, 20, 60),
            "weight": 15,
            "metric": f"{vol:.1f}% annualized" if vol is not None else "Unavailable",
            "note": "Higher volatility means wider position-size and patience requirements.",
        },
        {
            "label": "Max drawdown",
            "score": score_band(drawdown, 15, 55),
            "weight": 15,
            "metric": f"{drawdown:.1f}%" if drawdown is not None else "Unavailable",
            "note": "Measures the biggest recent peak-to-trough fall.",
        },
        {
            "label": "Valuation risk",
            "score": valuation_risk,
            "weight": 20,
            "metric": inputs.get("valuationMetric") or "P/E, PBV, EV/EBITDA vs industry",
            "note": "High-quality companies can still be risky when expectations are too expensive.",
        },
        {
            "label": "Debt risk",
            "score": debt_risk,
            "weight": 15,
            "metric": inputs.get("debtMetric") or "Unavailable / not meaningful",
            "note": "For banks, debt/equity is less useful and should be replaced by banking ratios.",
        },
        {
            "label": "Profit risk",
            "score": profit_risk,
            "weight": 15,
            "metric": inputs.get("profitMetric") or "Margin trend",
            "note": "Looks for weak or deteriorating profitability.",
        },
        {
            "label": "Growth slowdown risk",
            "score": growth_risk,
            "weight": 10,
            "metric": inputs.get("growthMetric") or "Revenue trend",
            "note": "Strong stocks can rerate lower when growth decelerates.",
        },
        {
            "label": "Concentration / narrative risk",
            "score": concentration,
            "weight": 10,
            "metric": "Industry proxy + manual review needed",
            "note": "Watch customer concentration, dependence on one theme, and crowded expectations.",
        },
    ]
    score = weighted_score((item["score"], item["weight"]) for item in items)
    if score is not None and valuation_risk is not None and valuation_risk >= 80 and concentration >= 70:
        score = max(score, 58)
    red_flags = []
    if drawdown_from_high_abs is not None and drawdown_from_high_abs > 40:
        red_flags.append("Stock is down more than 40% from its 52-week high.")
    if one_year is not None and one_year > 100 and growth_risk is not None and growth_risk > 55:
        red_flags.append("Stock is up more than 100% in one year without equally strong growth support.")
    if valuation_risk is not None and valuation_risk >= 75:
        red_flags.append("Valuation risk is high versus the selected industry pattern.")
    if valuation_risk is not None and valuation_risk >= 80 and concentration >= 70:
        red_flags.append("High valuation plus narrative/concentration risk; require larger margin of safety.")
    if debt_risk is not None and debt_risk >= 75:
        red_flags.append("Debt risk is high; check debt/equity and interest coverage manually.")
    if profit_risk is not None and profit_risk >= 75:
        red_flags.append("Profitability risk is high; margins or earnings consistency need review.")
    if growth_risk is not None and growth_risk >= 75:
        red_flags.append("Growth slowdown risk is high.")
    if score_band(vol, 20, 60) is not None and score_band(vol, 20, 60) >= 80:
        red_flags.append("Price volatility is very high.")
    if not red_flags:
        red_flags.append("No major automatic red flag found. Still review qualitative risks manually.")
    return {
        "score": score,
        "level": risk_level(score),
        "suggestedAllocation": suggested_allocation(score),
        "penalty": round(score * 0.20) if score is not None else None,
        "items": items,
        "redFlags": red_flags,
        "drawdownFromHigh": drawdown_from_high,
    }


def fetch_fundamentals(symbol, industry):
    encoded = urllib.parse.quote(symbol)
    params = urllib.parse.urlencode(
        {
            "symbol": symbol,
            "type": ",".join(FUNDAMENTAL_FIELDS),
            "merge": "false",
            "period1": "1451606400",
            "period2": "1783296000",
        }
    )
    url = f"https://query1.finance.yahoo.com/ws/fundamentals-timeseries/v1/finance/timeseries/{encoded}?{params}"
    data = fetch_json(url)
    raw = {}
    for item in data.get("timeseries", {}).get("result", []):
        meta = item.get("meta")
        if not meta:
            continue
        field = (meta.get("type") or [""])[0]
        raw[field] = item.get(field, [])

    rules = INDUSTRY_RULES.get(industry) or INDUSTRY_RULES["Other"]
    revenue = annual_values(raw.get("annualTotalRevenue"))
    net_income = annual_values(raw.get("annualNetIncome"))
    opex = annual_values(raw.get("annualOperatingExpense"))
    ebitda = annual_values(raw.get("annualEBITDA"))
    free_cash_flow = annual_values(raw.get("annualFreeCashFlow"))
    total_debt = annual_values(raw.get("annualTotalDebt"))
    equity = annual_values(raw.get("annualStockholdersEquity"))
    diluted_shares = annual_values(raw.get("annualDilutedAverageShares"))
    market_cap_entry = latest_entry(raw.get("trailingMarketCap") or raw.get("annualMarketCap"))
    share_entry = latest_entry(raw.get("annualBasicAverageShares"))
    pe_entry = latest_entry(raw.get("trailingPeRatio"))
    pbv_entry = latest_entry(raw.get("trailingPbRatio"))
    evebitda_entry = latest_entry(raw.get("trailingEnterprisesValueEBITDARatio"))
    evrev_entry = latest_entry(raw.get("trailingEnterprisesValueRevenueRatio"))
    currency = (market_cap_entry or {}).get("currencyCode") or (latest_entry(raw.get("annualTotalRevenue")) or {}).get("currencyCode") or ""

    npm = margin_series(revenue, net_income)
    opex_ratio = ratio_series(opex, revenue)
    debt_equity = None
    if total_debt and equity and equity[-1]["value"]:
        debt_equity = total_debt[-1]["value"] / equity[-1]["value"]
    share_growth = cagr(diluted_shares)

    revenue_score = trend_score(revenue, higher_better=True, base=48, scale=2.4)
    npm_score = margin_score(npm)
    op_score = opex_score(opex_ratio)
    pe_score = valuation_score(raw_value(pe_entry), rules.get("pe"))
    pbv_score = valuation_score(raw_value(pbv_entry), rules.get("pbv"))
    eve_score = valuation_score(raw_value(evebitda_entry), rules.get("evebitda"))
    cap_score = market_cap_score(raw_value(market_cap_entry), currency)
    pe_raw = raw_value(pe_entry)
    pbv_raw = raw_value(pbv_entry)
    eve_raw = raw_value(evebitda_entry)
    debt_risk = debt_risk_from_ratio(debt_equity, industry)
    profit_risk = None if npm_score is None else round(clamp(100 - npm_score, 5, 95))
    growth_risk = None if revenue_score is None else round(clamp(100 - revenue_score, 5, 95))
    if free_cash_flow and free_cash_flow[-1]["value"] < 0:
        profit_risk = max(profit_risk or 50, 80)
    if share_growth is not None and share_growth > 5:
        growth_risk = max(growth_risk or 50, 70)
    score = weighted_score(
        [
            (revenue_score, 20),
            (npm_score, 18),
            (op_score, 14),
            (pe_score, 15),
            (pbv_score, 15),
            (eve_score, 12),
            (cap_score, 6),
        ]
    )
    valuation = weighted_score([(pe_score, 35), (pbv_score, 35), (eve_score, 30)])
    valuation_risk_candidates = []
    if valuation is not None:
        valuation_risk_candidates.append(round(clamp(100 - valuation, 5, 95)))
    if pe_raw is not None:
        valuation_risk_candidates.append(score_band(pe_raw, 18, 50, 20, 90))
    if pbv_raw is not None:
        valuation_risk_candidates.append(score_band(pbv_raw, 3, 18, 20, 90))
    if eve_raw is not None:
        valuation_risk_candidates.append(score_band(eve_raw, 10, 35, 20, 90))
    valuation_risk = max([value for value in valuation_risk_candidates if value is not None], default=None)

    def latest_metric(label, entry, score_value, note):
        return {
            "label": label,
            "value": fmt_value(entry) if entry else None,
            "raw": raw_value(entry) if entry else None,
            "asOf": entry.get("asOfDate") if entry else None,
            "score": score_value,
            "note": note,
        }

    annual_table = []
    by_date = {row["date"]: {"date": row["date"], "revenue": row.get("fmt")} for row in revenue}
    for row in net_income:
        by_date.setdefault(row["date"], {"date": row["date"]})["netIncome"] = row.get("fmt")
    for row in npm:
        by_date.setdefault(row["date"], {"date": row["date"]})["npm"] = row.get("fmt")
    for row in opex:
        by_date.setdefault(row["date"], {"date": row["date"]})["opex"] = row.get("fmt")
    for row in opex_ratio:
        by_date.setdefault(row["date"], {"date": row["date"]})["opexRatio"] = row.get("fmt")
    for row in ebitda:
        by_date.setdefault(row["date"], {"date": row["date"]})["ebitda"] = row.get("fmt")
    for key in sorted(by_date):
        annual_table.append(by_date[key])

    return {
        "score": score,
        "valuationScore": valuation,
        "industry": industry,
        "currency": currency,
        "marketCap": latest_metric("Market cap", market_cap_entry, cap_score, "Size/stability signal, not a buy signal by itself."),
        "shares": latest_metric("Basic average shares", share_entry, None, "Used to understand share count and dilution."),
        "pe": latest_metric("P/E", pe_entry, pe_score, f"Scored against {industry} pattern."),
        "pbv": latest_metric("PBV", pbv_entry, pbv_score, f"Scored against {industry} pattern."),
        "evEbitda": latest_metric("EV/EBITDA", evebitda_entry, eve_score, "Often unavailable or less meaningful for banks."),
        "evRevenue": latest_metric("EV/Revenue", evrev_entry, None, "Extra context for high-growth companies."),
        "riskInputs": {
            "valuationRisk": valuation_risk,
            "valuationMetric": f"P/E {fmt_value(pe_entry) if pe_entry else 'N/A'}, PBV {fmt_value(pbv_entry) if pbv_entry else 'N/A'}",
            "debtRisk": debt_risk,
            "debtMetric": f"Debt/equity {debt_equity:.2f}x" if debt_equity is not None else "Debt/equity unavailable",
            "profitRisk": profit_risk,
            "profitMetric": f"Latest NPM {npm[-1]['fmt']}" if npm else "NPM unavailable",
            "growthRisk": growth_risk,
            "growthMetric": f"Revenue CAGR {cagr(revenue):.1f}%" if cagr(revenue) is not None else "Revenue CAGR unavailable",
        },
        "trendScores": [
            {"label": "Revenue trend", "score": revenue_score, "note": f"CAGR {cagr(revenue):.1f}%" if cagr(revenue) is not None else "Not enough annual data."},
            {"label": "NPM trend", "score": npm_score, "note": f"Latest NPM {npm[-1]['fmt']}" if npm else "Not enough margin data."},
            {"label": "OPEX / revenue trend", "score": op_score, "note": f"Latest OPEX ratio {opex_ratio[-1]['fmt']}" if opex_ratio else "Not enough OPEX data."},
        ],
        "annualTable": annual_table[-5:],
    }


def analyze_symbol(symbol, industry="Other"):
    encoded = urllib.parse.quote(symbol)
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{encoded}?range=2y&interval=1d&events=history"
    data = fetch_json(url)
    result = data.get("chart", {}).get("result", [None])[0]
    if not result:
        raise ValueError("No price history returned for this symbol.")

    meta = result.get("meta", {})
    quote = result.get("indicators", {}).get("quote", [{}])[0]
    timestamps = result.get("timestamp", [])
    rows = []
    for idx, timestamp in enumerate(timestamps):
        close = quote.get("close", [])[idx]
        high = quote.get("high", [])[idx]
        low = quote.get("low", [])[idx]
        volume = quote.get("volume", [])[idx]
        if close is None or high is None or low is None:
            continue
        rows.append(
            {
                "date": dt.datetime.fromtimestamp(timestamp, tz=dt.timezone.utc).date().isoformat(),
                "close": float(close),
                "high": float(high),
                "low": float(low),
                "volume": int(volume or 0),
            }
        )
    if len(rows) < 60:
        raise ValueError("Not enough daily price history to calculate the screener.")

    closes = [row["close"] for row in rows]
    highs = [row["high"] for row in rows]
    lows = [row["low"] for row in rows]
    volumes = [row["volume"] for row in rows]
    ma20 = sma(closes, 20)
    ma50 = sma(closes, 50)
    ma100 = sma(closes, 100)
    ma200 = sma(closes, 200)
    volume50 = sma(volumes, 50)
    std20 = rolling_std(closes, 20)
    upper = [None if ma20[i] is None else ma20[i] + 2 * std20[i] for i in range(len(rows))]
    lower = [None if ma20[i] is None else ma20[i] - 2 * std20[i] for i in range(len(rows))]
    band_width = [
        None if ma20[i] in (None, 0) or upper[i] is None or lower[i] is None else (upper[i] - lower[i]) / ma20[i] * 100
        for i in range(len(rows))
    ]

    ema12 = ema(closes, 12)
    ema26 = ema(closes, 26)
    macd = [ema12[i] - ema26[i] for i in range(len(rows))]
    signal = ema(macd, 9)
    hist = [macd[i] - signal[i] for i in range(len(rows))]
    rsi14 = rsi(closes, 14)

    stoch_k = []
    for idx in range(len(rows)):
        if idx + 1 < 14:
            stoch_k.append(None)
        else:
            low14 = min(lows[idx + 1 - 14 : idx + 1])
            high14 = max(highs[idx + 1 - 14 : idx + 1])
            stoch_k.append(50 if high14 == low14 else (closes[idx] - low14) / (high14 - low14) * 100)
    stoch_d_raw = sma([value if value is not None else 50 for value in stoch_k], 3)
    stoch_d = [None if stoch_k[idx] is None else stoch_d_raw[idx] for idx in range(len(rows))]

    latest = len(rows) - 1
    last_close = closes[-1]
    last_252 = closes[-252:] if len(closes) >= 252 else closes
    high_52w = max(last_252)
    low_52w = min(last_252)
    one_month = pct_change(closes[-22], last_close) if len(closes) >= 22 else None
    six_month = pct_change(closes[-126], last_close) if len(closes) >= 126 else None
    one_year = pct_change(closes[-252], last_close) if len(closes) >= 252 else None
    ma20_now = latest_number(ma20)
    ma50_now = latest_number(ma50)
    ma100_now = latest_number(ma100)
    ma200_now = latest_number(ma200)
    upper_now = latest_number(upper)
    lower_now = latest_number(lower)
    vol50_now = latest_number(volume50)
    band_width_now = latest_number(band_width)
    band_width_prev = latest_number(band_width, 21)
    dist_ma20 = pct_distance(last_close, ma20_now)
    dist_ma50 = pct_distance(last_close, ma50_now)
    dist_ma100 = pct_distance(last_close, ma100_now)
    dist_ma200 = pct_distance(last_close, ma200_now)
    rsi_now = latest_number(rsi14)
    recent_high = max(closes[-126:]) if len(closes) >= 126 else high_52w
    pullback_from_high = pct_change(recent_high, last_close)
    prior_low_3m = min(lows[-84:-21]) if len(lows) >= 84 else min(lows[:-1] or lows)
    dist_prior_support = pct_distance(last_close, prior_low_3m)
    range_52w = high_52w - low_52w
    position_52w = 50 if range_52w == 0 else (last_close - low_52w) / range_52w * 100
    band_position = (
        None
        if upper_now is None or lower_now is None or upper_now == lower_now
        else (last_close - lower_now) / (upper_now - lower_now) * 100
    )
    long_trend_intact = ma200_now is not None and last_close >= ma200_now * 0.97
    long_trend_healthy = ma50_now is not None and ma200_now is not None and last_close > ma200_now and ma50_now > ma200_now
    trend_stack = ma20_now is not None and ma50_now is not None and ma100_now is not None and ma200_now is not None and last_close > ma20_now > ma50_now > ma100_now > ma200_now
    ma200_rising = rising_over(ma200, 63)
    macd_improving = macd[-1] > signal[-1] or (len(hist) > 6 and hist[-1] > hist[-3] > hist[-6])
    macd_weakening = macd[-1] < signal[-1] and len(hist) > 6 and hist[-1] < hist[-3] < hist[-6]
    stoch_cross = recent_cross_above(stoch_k, stoch_d, 5)
    stoch_turning_up = stoch_k[-1] is not None and stoch_d[-1] is not None and 20 <= stoch_k[-1] <= 65 and stoch_k[-1] > stoch_d[-1]
    rsi_good_zone = rsi_now is not None and 35 <= rsi_now <= 55
    rsi_healthy_momentum = rsi_now is not None and 50 < rsi_now <= 70
    rsi_hot = rsi_now is not None and rsi_now > 70
    rsi_panic = rsi_now is not None and rsi_now < 30
    positive_volume = vol50_now not in (None, 0) and volumes[-1] > vol50_now * 1.15 and last_close > closes[-2]
    selling_volume_panic = vol50_now not in (None, 0) and volumes[-1] > vol50_now * 1.8 and last_close < closes[-2]
    near_ma50_pullback = dist_ma50 is not None and -4 <= dist_ma50 <= 6
    near_ma100_pullback = dist_ma100 is not None and -4 <= dist_ma100 <= 7
    near_ma200_support = dist_ma200 is not None and -3 <= dist_ma200 <= 6
    near_prior_support = dist_prior_support is not None and -3 <= dist_prior_support <= 8
    support_zone = near_ma50_pullback or near_ma100_pullback or near_ma200_support or near_prior_support
    healthy_pullback = pullback_from_high is not None and -25 <= pullback_from_high <= -8 and long_trend_intact
    too_hot = rsi_hot or (dist_ma50 is not None and dist_ma50 > 15) or (position_52w > 92 and pullback_from_high is not None and pullback_from_high > -5)
    breakdown = (
        (ma200_now is not None and last_close < ma200_now * 0.95)
        or (near_prior_support is False and dist_prior_support is not None and dist_prior_support < -4)
        or (selling_volume_panic and macd_weakening)
    )
    controlled_pullback_volume = not selling_volume_panic

    score = 0
    signals = []

    def add(condition, points, label, category, positive_note, watch_note=None, negative=False):
        nonlocal score
        if condition:
            score += points
            signals.append(
                {
                    "label": label,
                    "category": category,
                    "status": "positive",
                    "points": points,
                    "note": positive_note,
                }
            )
        else:
            signals.append(
                {
                    "label": label,
                    "category": category,
                    "status": "negative" if negative else "watch",
                    "points": 0,
                    "note": watch_note or "Needs confirmation before treating this as a strong entry signal.",
                }
            )

    add(long_trend_intact, 14, "Long-term trend intact", "MA", "Price is above or close to the 200-day MA.", "Below the 200-day area; buy slowly or wait for stabilization.", negative=True)
    add(long_trend_healthy, 10, "Trend quality", "MA", "Price is above MA200 and MA50 is above MA200.", "Trend is not fully healthy yet; do not treat this as an aggressive entry.")
    add(ma200_rising, 8, "40-week / MA200 direction", "MA", "MA200 is rising, similar to a healthy 40-week trend.", "MA200 is flat or falling; long-term timing is weaker.")
    add(healthy_pullback, 14, "Cooling from high", "Pullback", "Price has corrected roughly 8-25% while the long-term trend remains intact.", "No useful pullback yet, or the pullback is becoming too deep.")
    add(support_zone, 14, "Near support zone", "Support", "Price is near MA50, MA100, MA200, or a prior defended low.", "Price is not near a clear support zone; avoid forcing an entry.")
    add(rsi_good_zone, 12, "RSI entry zone", "RSI", "RSI is in the 35-55 cooling zone preferred for long-term entries.", "RSI is not in the preferred entry zone.")
    add(rsi_healthy_momentum or rsi_good_zone, 6, "RSI context", "RSI", "RSI shows healthy momentum without obvious overheating.", "RSI is either too hot or too weak; context matters.")
    add(macd_improving, 8, "MACD confirmation", "MACD", "MACD is flattening, crossing up, or improving.", "MACD is not confirming recovery yet.")
    add(stoch_turning_up or stoch_cross, 4, "Short-term trigger", "Stochastic", "Stochastic is turning up from a usable area.", "Stochastic does not yet support the timing.")
    add(controlled_pullback_volume, 6, "Selling volume", "Volume", "Selling volume is not at panic level.", "Heavy selling volume suggests waiting for stabilization.", negative=True)
    add(not too_hot, 10, "Avoid chasing", "Risk", "The chart is not obviously euphoric or far above moving averages.", "Chart looks extended; wait or use only a small first tranche.", negative=True)
    add(not breakdown, 10, "Pullback vs breakdown", "Risk", "No major support breakdown is visible.", "Possible breakdown: below key support, weak momentum, or panic selling.", negative=True)

    score_cap = 100
    if breakdown:
        score_cap = min(score_cap, 35)
    if too_hot:
        score_cap = min(score_cap, 62)
    if ma200_now is not None and last_close < ma200_now:
        score_cap = min(score_cap, 45)
    if ma50_now is not None and ma200_now is not None and ma50_now < ma200_now:
        score_cap = min(score_cap, 60)
    if macd_weakening:
        score_cap = min(score_cap, 70)
    if position_52w < 35:
        score_cap = min(score_cap, 55)

    if score_cap < 100:
        signals.append(
            {
                "label": "Technical score cap",
                "category": "Risk",
                "status": "negative",
                "points": 0,
                "note": f"Score capped at {score_cap} because one or more major trend/risk filters failed.",
            }
        )

    if breakdown:
        technical_action = "Wait"
        tranche_plan = "Do not average down yet. Wait for support to stabilize and for fundamental damage to be ruled out."
    elif too_hot:
        technical_action = "Wait / small first tranche"
        tranche_plan = "If fundamentals are excellent, consider only a small starter position and reserve most cash for a pullback."
    elif long_trend_intact and support_zone and rsi_good_zone and healthy_pullback:
        technical_action = "Start buying in tranches"
        tranche_plan = "Consider 30% now, 20% near MA50/MA100, 30% near MA200 or major support, and 20% after recovery confirmation."
    elif long_trend_intact and (support_zone or rsi_good_zone):
        technical_action = "Accumulate slowly"
        tranche_plan = "Use smaller tranches. The setup is improving, but not all timing conditions are aligned."
    elif long_trend_healthy and not too_hot:
        technical_action = "Hold / watch for pullback"
        tranche_plan = "The trend is healthy, but the entry is not ideal. Wait for cooling toward MA50/MA100 or RSI 35-55."
    else:
        technical_action = "Research more"
        tranche_plan = "Let the chart form a clearer support zone before committing serious capital."

    action_caps = {
        "Start buying in tranches": 94,
        "Accumulate slowly": 82,
        "Hold / watch for pullback": 72,
        "Wait / small first tranche": 62,
        "Research more": 60,
        "Wait": 35,
    }
    score = round(clamp(min(score, score_cap, action_caps.get(technical_action, 100))))
    rsi_price_rows = []
    for idx in range(max(0, len(rows) - 180), len(rows)):
        if rsi14[idx] is None:
            continue
        rsi_price_rows.append({"date": rows[idx]["date"], "price": closes[idx], "rsi": rsi14[idx]})
    rsi_target = 46.8
    rsi_target_price = None
    if rsi_price_rows:
        nearest = min(rsi_price_rows, key=lambda row: abs(row["rsi"] - rsi_target))
        rsi_target_price = {
            "target": rsi_target,
            "date": nearest["date"],
            "price": nearest["price"],
            "rsi": nearest["rsi"],
            "difference": abs(nearest["rsi"] - rsi_target),
            "note": "Historical nearest match, not a prediction. RSI depends on the path of gains and losses.",
        }
    recent_rsi_entry_prices = [
        row for row in rsi_price_rows if 35 <= row["rsi"] <= 55
    ][-8:]
    display_start = max(0, len(rows) - 220)
    series = []
    for idx in range(display_start, len(rows)):
        series.append(
            {
                "date": rows[idx]["date"],
                "close": closes[idx],
                "ma20": ma20[idx],
                "ma50": ma50[idx],
                "ma100": ma100[idx],
                "ma200": ma200[idx],
                "upper": upper[idx],
                "lower": lower[idx],
                "macd": macd[idx],
                "signal": signal[idx],
                "hist": hist[idx],
                "stochK": stoch_k[idx],
                "stochD": stoch_d[idx],
                "rsi": rsi14[idx],
            }
        )

    try:
        fundamentals = fetch_fundamentals(symbol, industry)
    except Exception as exc:
        fundamentals = {"score": None, "error": str(exc), "industry": industry}
    risk = calculate_risk(closes, high_52w, last_close, one_year, fundamentals, industry)

    return {
        "symbol": meta.get("symbol", symbol),
        "name": meta.get("longName") or meta.get("shortName") or symbol,
        "exchange": meta.get("fullExchangeName") or meta.get("exchangeName") or "",
        "currency": meta.get("currency") or "",
        "price": last_close,
        "asOf": rows[-1]["date"],
        "high52w": high_52w,
        "low52w": low_52w,
        "oneMonth": one_month,
        "sixMonth": six_month,
        "oneYear": one_year,
        "technicalScore": score,
        "technicalAction": technical_action,
        "tranchePlan": tranche_plan,
        "rsiTargetPrice": rsi_target_price,
        "recentRsiEntryPrices": recent_rsi_entry_prices,
        "risk": risk,
        "fundamentals": fundamentals,
        "signals": signals,
        "latestIndicators": {
            "ma20": ma20[-1],
            "ma50": ma50[-1],
            "ma100": ma100[-1],
            "ma200": ma200[-1],
            "distMa20": dist_ma20,
            "distMa50": dist_ma50,
            "distMa100": dist_ma100,
            "distMa200": dist_ma200,
            "macd": macd[-1],
            "signal": signal[-1],
            "stochK": stoch_k[-1],
            "stochD": stoch_d[-1],
            "rsi": rsi_now,
            "pullbackFromHigh": pullback_from_high,
            "upper": upper[-1],
            "lower": lower[-1],
            "bandPosition": band_position,
            "position52w": position_52w,
            "volume50": vol50_now,
        },
        "series": series,
    }


HTML = r"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Delvin Stock Screener</title>
  <style>
    :root {
      --ink: #17212b;
      --muted: #607080;
      --line: #d8e0e4;
      --panel: #f7f9fa;
      --brand: #1f4e5f;
      --gold: #c98f2a;
      --green: #2f6f4e;
      --red: #9a3b3b;
      --white: #fff;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      color: var(--ink);
      background: #eef3f4;
    }
    header {
      background: var(--brand);
      color: var(--white);
      padding: 16px 24px;
      display: flex;
      justify-content: space-between;
      align-items: center;
      gap: 16px;
    }
    h1 { margin: 0; font-size: 20px; letter-spacing: 0; }
    header span { color: #d6e5e8; font-size: 13px; }
    main {
      display: grid;
      grid-template-columns: 360px 1fr;
      gap: 16px;
      padding: 16px;
      max-width: 1440px;
      margin: 0 auto;
    }
    section, .panel {
      background: var(--white);
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 16px;
    }
    .left { display: flex; flex-direction: column; gap: 12px; }
    .right { display: flex; flex-direction: column; gap: 12px; }
    label { display: block; font-size: 12px; color: var(--muted); margin-bottom: 6px; font-weight: 700; }
    input[type="text"], select, textarea {
      width: 100%;
      border: 1px solid var(--line);
      border-radius: 6px;
      padding: 10px 11px;
      font: inherit;
      background: white;
      color: var(--ink);
    }
    textarea { min-height: 74px; resize: vertical; }
    button {
      border: 0;
      border-radius: 6px;
      padding: 10px 12px;
      font-weight: 800;
      cursor: pointer;
      background: var(--brand);
      color: white;
    }
    button.secondary { background: #e8eef0; color: var(--brand); }
    button:disabled { opacity: 0.5; cursor: not-allowed; }
    .search-row { display: grid; grid-template-columns: 1fr 92px; gap: 8px; }
    .results { display: grid; gap: 6px; margin-top: 8px; }
    .result {
      border: 1px solid var(--line);
      background: var(--panel);
      border-radius: 6px;
      padding: 9px;
      cursor: pointer;
    }
    .result strong { display: block; font-size: 13px; }
    .result span { color: var(--muted); font-size: 12px; }
    .score-grid {
      display: grid;
      grid-template-columns: repeat(4, minmax(0, 1fr));
      gap: 8px;
    }
    .score-card {
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 10px;
      background: #fbfcfd;
      min-height: 74px;
    }
    .score-card small { color: var(--muted); display: block; margin-bottom: 6px; }
    .score-card strong { font-size: 24px; color: var(--brand); }
    .decision {
      display: flex;
      justify-content: space-between;
      align-items: center;
      gap: 14px;
      border-left: 5px solid var(--gold);
      background: #fff7e7;
      border-radius: 8px;
      padding: 14px 16px;
    }
    .decision b { font-size: 18px; color: var(--brand); }
    .tabs { display: flex; gap: 8px; flex-wrap: wrap; }
    .tab {
      background: #e8eef0;
      color: var(--brand);
      padding: 8px 10px;
      border-radius: 6px;
      font-size: 13px;
    }
    .tab.active { background: var(--brand); color: white; }
    canvas {
      width: 100%;
      height: 380px;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: #fbfcfd;
    }
    .metrics {
      display: grid;
      grid-template-columns: repeat(4, minmax(0, 1fr));
      gap: 8px;
    }
    .metric {
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 10px;
      background: var(--panel);
    }
    .metric small { color: var(--muted); display: block; }
    .metric strong { font-size: 16px; }
    .fund-grid {
      display: grid;
      grid-template-columns: repeat(3, minmax(0, 1fr));
      gap: 8px;
      margin-top: 10px;
    }
    .fund-item {
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 10px;
      background: #fbfcfd;
    }
    .fund-item small { display: block; color: var(--muted); }
    .fund-item strong { display: block; color: var(--brand); font-size: 18px; margin: 3px 0; }
    .score-pill {
      display: inline-block;
      border-radius: 999px;
      padding: 2px 8px;
      background: #e8eef0;
      color: var(--brand);
      font-size: 12px;
      font-weight: 800;
    }
    .data-table {
      width: 100%;
      border-collapse: collapse;
      margin-top: 10px;
      font-size: 12px;
    }
    .data-table th, .data-table td {
      border: 1px solid var(--line);
      padding: 7px;
      text-align: left;
      vertical-align: top;
    }
    .data-table th {
      background: var(--brand);
      color: white;
    }
    .form-grid {
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 14px;
    }
    .slider-row {
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 10px;
      background: #fbfcfd;
    }
    .slider-head { display: flex; justify-content: space-between; gap: 8px; }
    input[type="range"] { width: 100%; accent-color: var(--brand); }
    .checklist { display: grid; gap: 8px; }
    .checkline {
      display: flex;
      align-items: flex-start;
      gap: 8px;
      font-size: 13px;
      line-height: 1.35;
    }
    .signals { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 8px; }
    .signal {
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 8px;
      font-size: 12px;
      background: #fbfcfd;
    }
    .signal.positive { border-color: #b9d2c4; background: #eff8f2; }
    .signal.watch { border-color: #ead7b7; background: #fff8ea; }
    .signal.negative { border-color: #e0b6b6; background: #fff1f1; }
    .signal .score-pill { margin-left: 6px; }
    .timing-plan {
      border: 1px solid #b9d2c4;
      border-radius: 8px;
      padding: 12px;
      background: #f4faf6;
      margin-bottom: 10px;
    }
    .timing-plan strong { color: var(--brand); font-size: 20px; }
    .muted { color: var(--muted); font-size: 12px; }
    @media (max-width: 980px) {
      main { grid-template-columns: 1fr; }
      .score-grid, .metrics, .form-grid, .signals, .fund-grid { grid-template-columns: 1fr 1fr; }
    }
    @media (max-width: 620px) {
      .score-grid, .metrics, .form-grid, .signals, .fund-grid, .search-row { grid-template-columns: 1fr; }
      canvas { height: 320px; }
    }
  </style>
</head>
<body>
  <header>
    <div>
      <h1>Delvin Stock Screener</h1>
      <span>Fundamentals, technical timing, ethics, roadmap, industry, macro, and influence tracing</span>
    </div>
    <span id="status">Ready</span>
  </header>
  <main>
    <div class="left">
      <section>
        <label for="query">Stock name or ticker</label>
        <div class="search-row">
          <input id="query" type="text" placeholder="Example: NVIDIA, NVDA, BBRI.JK">
          <button id="analyzeBtn">Analyze</button>
        </div>
        <div class="results" id="results"></div>
        <p class="muted">Tip: for Indonesia stocks, Yahoo symbols often end with <b>.JK</b>, such as <b>BBRI.JK</b>.</p>
      </section>
      <section>
        <label>Moral filter</label>
        <div class="checklist" id="moralChecks">
          <label class="checkline"><input type="checkbox" data-risk="harmful"> Alcohol, tobacco, gambling, or addictive products</label>
          <label class="checkline"><input type="checkbox" data-risk="abuse"> Abuse, corruption, forced labor, or customer exploitation</label>
          <label class="checkline"><input type="checkbox" data-risk="environment"> Severe environmental harm without credible improvement plan</label>
          <label class="checkline"><input type="checkbox" data-risk="unclear"> Business model is unclear or conflicts with my values</label>
        </div>
      </section>
      <section>
        <label for="industry">Industry lens</label>
        <select id="industry">
          <option>Technology / Semiconductors</option>
          <option>Banks</option>
          <option>Property</option>
          <option>Mining / Commodities</option>
          <option>Consumer Goods</option>
          <option>Infrastructure / Utilities</option>
          <option>Other</option>
        </select>
        <p class="muted" id="industryHint"></p>
      </section>
    </div>
    <div class="right">
      <section class="decision">
        <div>
          <b id="decisionText">Research more</b>
          <div class="muted" id="decisionReason">Run a stock first, then complete the scoring sliders.</div>
        </div>
        <button class="secondary" id="resetBtn">Reset manual scores</button>
      </section>
      <div class="score-grid">
        <div class="score-card"><small>Risk-Adjusted</small><strong id="overallScore">--</strong></div>
        <div class="score-card"><small>Fundamentals</small><strong id="fundScore">--</strong></div>
        <div class="score-card"><small>Entry Timing</small><strong id="techScore">--</strong></div>
        <div class="score-card"><small>Risk Score</small><strong id="riskScore">--</strong></div>
        <div class="score-card"><small>Moral Filter</small><strong id="moralScore">Pass</strong></div>
      </div>
      <section>
        <div class="metrics" id="quoteMetrics"></div>
      </section>
      <section>
        <h2>Automatic Fundamentals</h2>
        <p class="muted" id="fundamentalSource">Run a stock to calculate market cap, revenue trend, NPM trend, OPEX trend, P/E, PBV, and EV/EBITDA where available.</p>
        <div class="fund-grid" id="fundamentalMetrics"></div>
        <div id="fundamentalTable"></div>
      </section>
      <section>
        <h2>Risk Analysis</h2>
        <p class="muted" id="riskSource">Risk is scored separately from upside. A good company can still require a smaller position if valuation, volatility, debt, or concentration risk is high.</p>
        <div class="fund-grid" id="riskMetrics"></div>
        <div id="riskFlags"></div>
      </section>
      <section>
        <div class="tabs">
          <button class="tab active" data-chart="trend">MA Trend</button>
          <button class="tab" data-chart="bollinger">Bollinger</button>
          <button class="tab" data-chart="rsi">RSI</button>
          <button class="tab" data-chart="macd">MACD</button>
          <button class="tab" data-chart="stochastic">Stochastic</button>
        </div>
        <div class="metrics technical-metrics" id="technicalMetrics"></div>
        <div id="rsiPriceTable"></div>
        <canvas id="chart" width="1100" height="460"></canvas>
        <div class="timing-plan" id="timingPlan"></div>
        <div class="signals" id="signals"></div>
      </section>
      <section>
        <h2>Qualitative Scoring</h2>
        <p class="muted">Automatic fundamentals handle the financial ratios. Use these sliders for judgment areas that still need your reading and conviction.</p>
        <div class="form-grid" id="sliders"></div>
      </section>
      <section>
        <h2>Influence Notes</h2>
        <div class="form-grid">
          <div>
            <label>Short-term drivers</label>
            <textarea id="shortDrivers" placeholder="Earnings surprise, news, dividend date, fund flow, technical breakout..."></textarea>
          </div>
          <div>
            <label>Long-term drivers</label>
            <textarea id="longDrivers" placeholder="Revenue growth, margins, competitive advantage, regulation, industry demand..."></textarea>
          </div>
        </div>
      </section>
    </div>
  </main>
  <script>
    const $ = (id) => document.getElementById(id);
    let current = null;
    let chartMode = "trend";

    const sliderDefs = [
      ["revenue", "Business quality conviction", "Qualitative"],
      ["profit", "Management execution", "Qualitative"],
      ["cashflow", "Cash conversion confidence", "Qualitative"],
      ["balance", "Balance sheet nuance", "Qualitative"],
      ["valuation", "Valuation judgment after auto ratios", "Valuation"],
      ["dividend", "Dividend / shareholder return quality", "Qualitative"],
      ["roadmap", "Vision, roadmap, execution", "Future"],
      ["industryScore", "Industry outlook and pattern fit", "Industry"],
      ["macro", "Macro and geopolitical risk", "Macro"],
      ["influence", "Influence tracing clarity", "Influence"],
    ];

    const industryHints = {
      "Technology / Semiconductors": "Watch revenue growth, gross margin, customer concentration, product roadmap, capex cycle, export limits, and valuation expectations.",
      "Banks": "Watch loan growth, NIM, CASA, NPL, cost of credit, CAR, PBV vs ROE, and dividend sustainability.",
      "Property": "Watch debt maturity, net gearing, pre-sales, land bank, project completion, cash collection, and interest rates.",
      "Mining / Commodities": "Watch commodity prices, cash cost, production volume, reserves, regulation, export policy, and cycle position.",
      "Consumer Goods": "Watch brand strength, distribution, volume growth, pricing power, inventory, and margins.",
      "Infrastructure / Utilities": "Watch contracts, regulation, debt, project life, cash yield, and tariff risk.",
      "Other": "Define the 3-5 metrics that actually drive profit in this industry before scoring."
    };

    function fmt(value, digits = 2) {
      if (value === null || value === undefined || Number.isNaN(value)) return "--";
      return Number(value).toLocaleString(undefined, { maximumFractionDigits: digits });
    }

    function pct(value) {
      if (value === null || value === undefined || Number.isNaN(value)) return "--";
      return `${value >= 0 ? "+" : ""}${fmt(value, 1)}%`;
    }

    function status(message) { $("status").textContent = message; }

    function buildSliders() {
      $("sliders").innerHTML = sliderDefs.map(([id, label, group]) => `
        <div class="slider-row">
          <div class="slider-head"><label for="${id}">${label}</label><b id="${id}Val">3</b></div>
          <input type="range" min="1" max="5" value="3" id="${id}">
          <div class="muted">${group}</div>
        </div>
      `).join("");
      sliderDefs.forEach(([id]) => {
        $(id).addEventListener("input", () => {
          $(`${id}Val`).textContent = $(id).value;
          updateScores();
        });
      });
    }

    function resetManual() {
      sliderDefs.forEach(([id]) => {
        $(id).value = 3;
        $(`${id}Val`).textContent = "3";
      });
      document.querySelectorAll("#moralChecks input").forEach(input => input.checked = false);
      $("shortDrivers").value = "";
      $("longDrivers").value = "";
      updateScores();
    }

    function selectedMoralFail() {
      return [...document.querySelectorAll("#moralChecks input")].some(input => input.checked);
    }

    function avg(ids) {
      return ids.reduce((sum, id) => sum + Number($(id).value), 0) / ids.length * 20;
    }

    function updateScores() {
      const moralFail = selectedMoralFail();
      const manualQuality = avg(["revenue", "profit", "cashflow", "balance", "dividend"]);
      const fundamentals = current?.fundamentals?.score ?? manualQuality;
      const valuation = current?.fundamentals?.valuationScore ?? Number($("valuation").value) * 20;
      const future = Number($("roadmap").value) * 20;
      const industry = Number($("industryScore").value) * 20;
      const macro = Number($("macro").value) * 20;
      const influence = Number($("influence").value) * 20;
      const technical = current ? current.technicalScore : 50;
      const risk = current?.risk?.score ?? 0;
      const rawScore = Math.round(
        fundamentals * 0.30 + technical * 0.18 + valuation * 0.10 + manualQuality * 0.10 + future * 0.10 +
        industry * 0.09 + macro * 0.08 + influence * 0.05 + (moralFail ? 0 : 100) * 0.00
      );
      const overall = Math.max(0, Math.round(rawScore - risk * 0.20));
      $("fundScore").textContent = current && fundamentals !== null && fundamentals !== undefined ? Math.round(fundamentals) : "--";
      $("techScore").textContent = current ? Math.round(technical) : "--";
      $("riskScore").textContent = current?.risk?.score !== null && current?.risk?.score !== undefined ? Math.round(current.risk.score) : "--";
      $("moralScore").textContent = moralFail ? "Fail" : "Pass";
      $("overallScore").textContent = current ? overall : "--";
      if (moralFail) {
        $("decisionText").textContent = "Avoid / exclude";
        $("decisionReason").textContent = "The moral filter failed. Your framework treats this as a stop sign.";
      } else if (!current) {
        $("decisionText").textContent = "Research more";
        $("decisionReason").textContent = "Run a stock first, then complete the scoring sliders.";
      } else if (current.technicalAction === "Wait") {
        $("decisionText").textContent = "Wait";
        $("decisionReason").textContent = "The chart suggests a possible breakdown or unstable support. Fundamentals should be checked before averaging down.";
      } else if (current.technicalAction === "Wait / small first tranche") {
        $("decisionText").textContent = overall >= 70 ? "Good company, hot chart" : "Wait";
        $("decisionReason").textContent = "Technical timing says not to chase. If fundamentals are excellent, use only a small starter tranche.";
      } else if (current.risk?.score >= 76) {
        $("decisionText").textContent = "Very high risk";
        $("decisionReason").textContent = `Risk level is ${current.risk.level}. Suggested max allocation: ${current.risk.suggestedAllocation}.`;
      } else if (current.risk?.score >= 51 && overall >= 65) {
        $("decisionText").textContent = "Good, but risky";
        $("decisionReason").textContent = `Risk level is ${current.risk.level}. Consider smaller allocation or dollar-cost averaging.`;
      } else if (overall >= 78) {
        $("decisionText").textContent = "Strong candidate";
        $("decisionReason").textContent = `${current.technicalAction || "Review timing"}. Review valuation, risk, and position sizing before acting.`;
      } else if (overall >= 62) {
        $("decisionText").textContent = "Watchlist";
        $("decisionReason").textContent = `${current.technicalAction || "Watch timing"}. Good enough to follow, but not fully convincing yet.`;
      } else {
        $("decisionText").textContent = "Research more / avoid";
        $("decisionReason").textContent = "The current score is not strong. Identify what would need to improve.";
      }
    }

    async function searchStocks() {
      const q = $("query").value.trim();
      if (!q) return;
      status("Searching...");
      const res = await fetch(`/api/search?q=${encodeURIComponent(q)}`);
      const data = await res.json();
      $("results").innerHTML = (data.results || []).map(item => `
        <div class="result" data-symbol="${item.symbol}">
          <strong>${item.symbol} - ${item.name || "Unknown"}</strong>
          <span>${item.exchange || ""} ${item.type || ""}</span>
        </div>
      `).join("");
      document.querySelectorAll(".result").forEach(node => {
        node.onclick = () => {
          $("query").value = node.dataset.symbol;
          $("results").innerHTML = "";
          analyze(node.dataset.symbol);
        };
      });
      status("Ready");
    }

    async function analyze(symbol) {
      const q = (symbol || $("query").value).trim();
      if (!q) return;
      status("Analyzing...");
      $("analyzeBtn").disabled = true;
      try {
        const industry = $("industry").value;
        const res = await fetch(`/api/analyze?symbol=${encodeURIComponent(q)}&industry=${encodeURIComponent(industry)}`);
        const data = await res.json();
        if (!res.ok) throw new Error(data.error || "Could not analyze symbol");
        current = data;
        renderMetrics();
        renderTechnicalMetrics();
        renderFundamentals();
        renderRisk();
        renderTimingPlan();
        renderSignals();
        drawChart();
        updateScores();
        status(`${data.symbol} loaded`);
      } catch (err) {
        status("Error");
        alert(err.message);
      } finally {
        $("analyzeBtn").disabled = false;
      }
    }

    function renderMetrics() {
      $("quoteMetrics").innerHTML = `
        <div class="metric"><small>Symbol</small><strong>${current.symbol}</strong><div class="muted">${current.exchange}</div></div>
        <div class="metric"><small>Price</small><strong>${fmt(current.price)}</strong><div class="muted">${current.currency} as of ${current.asOf}</div></div>
        <div class="metric"><small>52-week range</small><strong>${fmt(current.low52w)} - ${fmt(current.high52w)}</strong></div>
        <div class="metric"><small>Momentum</small><strong>${pct(current.oneYear)}</strong><div class="muted">1-year change</div></div>
        <div class="metric"><small>1 month</small><strong>${pct(current.oneMonth)}</strong></div>
        <div class="metric"><small>6 months</small><strong>${pct(current.sixMonth)}</strong></div>
        <div class="metric"><small>MA50 / MA100 / MA200</small><strong>${fmt(current.latestIndicators.ma50)} / ${fmt(current.latestIndicators.ma100)} / ${fmt(current.latestIndicators.ma200)}</strong></div>
        <div class="metric"><small>Distance from MA50</small><strong>${pct(current.latestIndicators.distMa50)}</strong><div class="muted">extension risk</div></div>
        <div class="metric"><small>Pullback from high</small><strong>${pct(current.latestIndicators.pullbackFromHigh)}</strong><div class="muted">recent 6-month high</div></div>
        <div class="metric"><small>RSI 14</small><strong>${fmt(current.latestIndicators.rsi, 1)}</strong><div class="muted">35-55 is preferred entry zone</div></div>
        <div class="metric"><small>52-week position</small><strong>${fmt(current.latestIndicators.position52w, 1)}%</strong><div class="muted">0% low, 100% high</div></div>
        <div class="metric"><small>Stochastic %K / %D</small><strong>${fmt(current.latestIndicators.stochK, 1)} / ${fmt(current.latestIndicators.stochD, 1)}</strong></div>
        <div class="metric"><small>Bollinger position</small><strong>${fmt(current.latestIndicators.bandPosition, 1)}%</strong><div class="muted">within current band</div></div>
      `;
    }

    function rsiLabel(value) {
      if (value === null || value === undefined || Number.isNaN(value)) return "Run analysis";
      if (value >= 70) return "Hot / avoid chasing";
      if (value >= 55) return "Healthy momentum";
      if (value >= 35) return "Preferred entry zone";
      if (value >= 30) return "Weak / watch support";
      return "Oversold or panic risk";
    }

    function renderTechnicalMetrics() {
      const i = current?.latestIndicators || {};
      const target = current?.rsiTargetPrice;
      $("technicalMetrics").innerHTML = `
        <div class="metric"><small>RSI 14 + current price</small><strong>${fmt(i.rsi, 1)} @ ${fmt(current?.price)}</strong><div class="muted">${rsiLabel(i.rsi)}</div></div>
        <div class="metric"><small>RSI entry zone</small><strong>35 - 55</strong><div class="muted">preferred long-term accumulation area</div></div>
        <div class="metric"><small>Price near RSI 46.8</small><strong>${target ? fmt(target.price) : "--"}</strong><div class="muted">${target ? `RSI ${fmt(target.rsi, 1)} on ${target.date}` : "Run analysis"}</div></div>
        <div class="metric"><small>MA50 / MA100 / MA200</small><strong>${fmt(i.ma50)} / ${fmt(i.ma100)} / ${fmt(i.ma200)}</strong><div class="muted">support and trend reference</div></div>
        <div class="metric"><small>Pullback from high</small><strong>${pct(i.pullbackFromHigh)}</strong><div class="muted">8-25% is often healthier than chasing</div></div>
      `;
      const rows = current?.recentRsiEntryPrices || [];
      $("rsiPriceTable").innerHTML = rows.length ? `
        <table class="data-table">
          <thead><tr><th>Date</th><th>Close price</th><th>RSI 14</th><th>Use</th></tr></thead>
          <tbody>${rows.map(row => `
            <tr>
              <td>${row.date}</td>
              <td>${fmt(row.price)}</td>
              <td>${fmt(row.rsi, 1)}</td>
              <td>${row.rsi >= 35 && row.rsi <= 55 ? "Entry zone reference" : "Context"}</td>
            </tr>
          `).join("")}</tbody>
        </table>
        <p class="muted">RSI-to-price rows are historical references. They show what price matched each RSI level recently, not a guaranteed future price.</p>
      ` : "";
    }

    function renderTimingPlan() {
      $("timingPlan").innerHTML = `
        <small class="muted">Long-term technical role: fundamentals decide what to buy; RSI and other technicals decide when and how aggressively.</small><br>
        <strong>${current.technicalAction || "Research more"}</strong>
        <div class="muted">${current.tranchePlan || ""}</div>
      `;
    }

    function renderFundamentals() {
      const f = current.fundamentals || {};
      if (f.error) {
        $("fundamentalSource").textContent = `Automatic fundamentals unavailable: ${f.error}`;
        $("fundamentalMetrics").innerHTML = "";
        $("fundamentalTable").innerHTML = "";
        return;
      }
      $("fundamentalSource").textContent = `Industry pattern: ${f.industry || $("industry").value}. Data comes from Yahoo Finance fundamentals-timeseries when available.`;
      const main = [f.marketCap, f.shares, f.pe, f.pbv, f.evEbitda, f.evRevenue].filter(Boolean);
      const trends = f.trendScores || [];
      $("fundamentalMetrics").innerHTML = [
        `<div class="fund-item"><small>Auto fundamentals score</small><strong>${f.score ?? "--"}</strong><span class="score-pill">overall</span></div>`,
        ...main.map(item => `
          <div class="fund-item">
            <small>${item.label}${item.asOf ? ` (${item.asOf})` : ""}</small>
            <strong>${item.value ?? "N/A"}</strong>
            <span class="score-pill">${item.score === null || item.score === undefined ? "context" : `score ${item.score}`}</span>
            <div class="muted">${item.note || ""}</div>
          </div>
        `),
        ...trends.map(item => `
          <div class="fund-item">
            <small>${item.label}</small>
            <strong>${item.score ?? "N/A"}</strong>
            <span class="score-pill">trend</span>
            <div class="muted">${item.note || ""}</div>
          </div>
        `)
      ].join("");
      const rows = f.annualTable || [];
      $("fundamentalTable").innerHTML = rows.length ? `
        <table class="data-table">
          <thead><tr><th>Year</th><th>Revenue</th><th>Net income</th><th>NPM</th><th>OPEX</th><th>OPEX / revenue</th><th>EBITDA</th></tr></thead>
          <tbody>${rows.map(row => `
            <tr>
              <td>${row.date || "--"}</td>
              <td>${row.revenue || "--"}</td>
              <td>${row.netIncome || "--"}</td>
              <td>${row.npm || "--"}</td>
              <td>${row.opex || "--"}</td>
              <td>${row.opexRatio || "--"}</td>
              <td>${row.ebitda || "--"}</td>
            </tr>
          `).join("")}</tbody>
        </table>
      ` : "";
    }

    function renderRisk() {
      const r = current.risk || {};
      if (!r || r.score === null || r.score === undefined) {
        $("riskSource").textContent = "Risk analysis unavailable for this stock.";
        $("riskMetrics").innerHTML = "";
        $("riskFlags").innerHTML = "";
        return;
      }
      $("riskSource").textContent = `Risk Score: ${Math.round(r.score)}/100 (${r.level}). 0 is low risk, 100 is high risk. Suggested max allocation: ${r.suggestedAllocation}.`;
      $("riskMetrics").innerHTML = [
        `<div class="fund-item"><small>Risk score</small><strong>${Math.round(r.score)}</strong><span class="score-pill">${r.level}</span><div class="muted">Penalty applied to final score: ${r.penalty ?? "--"} points</div></div>`,
        `<div class="fund-item"><small>Suggested max allocation</small><strong>${r.suggestedAllocation}</strong><span class="score-pill">position size</span><div class="muted">Higher risk means smaller position or slower DCA.</div></div>`,
        `<div class="fund-item"><small>Drawdown from 52-week high</small><strong>${pct(r.drawdownFromHigh)}</strong><span class="score-pill">risk context</span><div class="muted">Large drawdowns may be opportunity or breakdown.</div></div>`,
        ...(r.items || []).map(item => `
          <div class="fund-item">
            <small>${item.label}</small>
            <strong>${item.score === null || item.score === undefined ? "N/A" : Math.round(item.score)}</strong>
            <span class="score-pill">${item.weight}% weight</span>
            <div class="muted">${item.metric || ""}</div>
            <div class="muted">${item.note || ""}</div>
          </div>
        `)
      ].join("");
      const flags = r.redFlags || [];
      $("riskFlags").innerHTML = flags.length ? `
        <table class="data-table">
          <thead><tr><th>Risk flags</th></tr></thead>
          <tbody>${flags.map(flag => `<tr><td>${flag}</td></tr>`).join("")}</tbody>
        </table>
      ` : "";
    }

    function renderSignals() {
      $("signals").innerHTML = current.signals.map(signal => `
        <div class="signal ${signal.status}">
          <b>${signal.status === "positive" ? "Positive" : signal.status === "negative" ? "Caution" : "Watch"}</b>
          <span class="score-pill">${signal.category}</span><br>
          ${signal.label}
          <div class="muted">${signal.note || ""}</div>
        </div>
      `).join("");
    }

    function seriesFor(mode) {
      const s = current.series;
      if (mode === "trend") return [
        ["Close", s.map(x => x.close), "#1f4e5f"],
        ["MA20", s.map(x => x.ma20), "#c98f2a"],
        ["MA50", s.map(x => x.ma50), "#2f6f4e"],
        ["MA100", s.map(x => x.ma100), "#725f9c"],
        ["MA200", s.map(x => x.ma200), "#9a3b3b"]
      ];
      if (mode === "bollinger") return [
        ["Close", s.map(x => x.close), "#1f4e5f"],
        ["Upper", s.map(x => x.upper), "#9a3b3b"],
        ["Middle", s.map(x => x.ma20), "#c98f2a"],
        ["Lower", s.map(x => x.lower), "#2f6f4e"]
      ];
      if (mode === "macd") return [
        ["MACD", s.map(x => x.macd), "#1f4e5f"],
        ["Signal", s.map(x => x.signal), "#c98f2a"],
        ["Hist", s.map(x => x.hist), "#9a3b3b", "bar"]
      ];
      if (mode === "rsi") return [
        ["RSI", s.map(x => x.rsi), "#1f4e5f"]
      ];
      return [
        ["%K", s.map(x => x.stochK), "#1f4e5f"],
        ["%D", s.map(x => x.stochD), "#c98f2a"]
      ];
    }

    function drawChart() {
      if (!current) return;
      const canvas = $("chart");
      const ctx = canvas.getContext("2d");
      const w = canvas.width, h = canvas.height;
      ctx.clearRect(0, 0, w, h);
      ctx.fillStyle = "#fbfcfd";
      ctx.fillRect(0, 0, w, h);
      const left = 70, right = 24, top = 40, bottom = 48;
      const pw = w - left - right, ph = h - top - bottom;
      const sets = seriesFor(chartMode);
      let values = [];
      sets.forEach(set => values.push(...set[1].filter(v => v !== null && v !== undefined && !Number.isNaN(v))));
      if (chartMode === "stochastic") values.push(0, 20, 80, 100);
      if (chartMode === "rsi") values.push(0, 30, 35, 55, 70, 100);
      if (chartMode === "macd") values.push(0);
      let min = Math.min(...values), max = Math.max(...values);
      if (min === max) { min -= 1; max += 1; }
      const pad = (max - min) * 0.08;
      min -= pad; max += pad;
      const xAt = i => left + (i / Math.max(1, current.series.length - 1)) * pw;
      const yAt = v => top + (max - v) / (max - min) * ph;
      ctx.strokeStyle = "#d8e0e4";
      ctx.lineWidth = 1;
      ctx.strokeRect(left, top, pw, ph);
      ctx.font = "12px sans-serif";
      ctx.fillStyle = "#607080";
      for (let i = 0; i < 5; i++) {
        const value = min + (max - min) * i / 4;
        const y = yAt(value);
        ctx.strokeStyle = "#d8e0e4";
        ctx.beginPath(); ctx.moveTo(left, y); ctx.lineTo(left + pw, y); ctx.stroke();
        ctx.fillText(value.toFixed(1), 12, y + 4);
      }
      if (chartMode === "stochastic") {
        [20, 80].forEach(v => {
          ctx.strokeStyle = "#c98f2a";
          ctx.beginPath(); ctx.moveTo(left, yAt(v)); ctx.lineTo(left + pw, yAt(v)); ctx.stroke();
        });
      }
      if (chartMode === "rsi") {
        [30, 35, 55, 70].forEach(v => {
          ctx.strokeStyle = v === 35 || v === 55 ? "#2f6f4e" : "#c98f2a";
          ctx.beginPath(); ctx.moveTo(left, yAt(v)); ctx.lineTo(left + pw, yAt(v)); ctx.stroke();
        });
      }
      if (chartMode === "macd") {
        const zero = yAt(0);
        ctx.strokeStyle = "#607080";
        ctx.beginPath(); ctx.moveTo(left, zero); ctx.lineTo(left + pw, zero); ctx.stroke();
      }
      sets.forEach(([name, arr, color, type]) => {
        ctx.strokeStyle = color;
        ctx.fillStyle = color;
        ctx.lineWidth = 3;
        if (type === "bar") {
          const zero = yAt(0);
          const bw = Math.max(2, pw / arr.length * 0.55);
          arr.forEach((v, i) => {
            if (v === null || v === undefined) return;
            ctx.fillStyle = v >= 0 ? "#2f6f4e" : "#9a3b3b";
            const x = xAt(i) - bw / 2, y = yAt(v);
            ctx.fillRect(x, Math.min(y, zero), bw, Math.abs(y - zero));
          });
        } else {
          ctx.beginPath();
          let started = false;
          arr.forEach((v, i) => {
            if (v === null || v === undefined || Number.isNaN(v)) { started = false; return; }
            if (!started) { ctx.moveTo(xAt(i), yAt(v)); started = true; }
            else ctx.lineTo(xAt(i), yAt(v));
          });
          ctx.stroke();
        }
      });
      ctx.font = "13px sans-serif";
      let lx = left;
      sets.forEach(([name, arr, color]) => {
        ctx.fillStyle = color;
        ctx.fillRect(lx, 18, 18, 3);
        ctx.fillStyle = "#607080";
        ctx.fillText(name, lx + 24, 23);
        lx += 90;
      });
      ctx.fillStyle = "#607080";
      const first = current.series[0].date.slice(0, 7);
      const last = current.series[current.series.length - 1].date.slice(0, 7);
      ctx.fillText(first, left, h - 14);
      ctx.fillText(last, left + pw - 56, h - 14);
    }

    let searchTimer = null;
    $("query").addEventListener("input", () => {
      clearTimeout(searchTimer);
      searchTimer = setTimeout(searchStocks, 350);
    });
    $("analyzeBtn").addEventListener("click", () => analyze());
    $("resetBtn").addEventListener("click", resetManual);
    $("industry").addEventListener("change", () => {
      $("industryHint").textContent = industryHints[$("industry").value];
      if (current?.symbol) {
        analyze(current.symbol);
      } else {
        updateScores();
      }
    });
    document.querySelectorAll("#moralChecks input").forEach(input => input.addEventListener("change", updateScores));
    document.querySelectorAll(".tab").forEach(tab => {
      tab.onclick = () => {
        document.querySelectorAll(".tab").forEach(t => t.classList.remove("active"));
        tab.classList.add("active");
        chartMode = tab.dataset.chart;
        drawChart();
      };
    });
    buildSliders();
    $("industryHint").textContent = industryHints[$("industry").value];
    renderTechnicalMetrics();
    updateScores();
  </script>
</body>
</html>"""


class Handler(BaseHTTPRequestHandler):
    def send_json(self, status, payload):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        params = urllib.parse.parse_qs(parsed.query)
        try:
            if parsed.path == "/":
                body = HTML.encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            elif parsed.path == "/api/search":
                query = params.get("q", [""])[0].strip()
                if not query:
                    self.send_json(400, {"error": "Missing search query."})
                else:
                    self.send_json(200, {"results": yahoo_search(query)})
            elif parsed.path == "/api/analyze":
                symbol = params.get("symbol", [""])[0].strip()
                industry = params.get("industry", ["Other"])[0].strip() or "Other"
                if not symbol:
                    self.send_json(400, {"error": "Missing stock symbol."})
                else:
                    self.send_json(200, analyze_symbol(symbol, industry))
            elif parsed.path == "/health":
                self.send_json(200, {"status": "ok"})
            else:
                self.send_json(404, {"error": "Not found."})
        except Exception as exc:
            self.send_json(500, {"error": str(exc)})

    def log_message(self, format, *args):
        return


def main():
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    local_url = f"http://127.0.0.1:{PORT}"
    print(f"Stock screener running on {HOST}:{PORT}")
    print(f"Local URL: {local_url}")
    server.serve_forever()


if __name__ == "__main__":
    main()
