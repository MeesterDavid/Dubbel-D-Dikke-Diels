from __future__ import annotations

import csv
from datetime import date, timedelta
import json
from pathlib import Path
import statistics
from typing import Any, Iterable

from tweakersearch.models import PriceAnalysis


def parse_price_history_points(
    raw_history: str | Path | Iterable[Any] | dict[str, Any],
) -> list[tuple[date, float]]:
    """
    Zet prijshistorie uit allerlei bronnen om naar een gesorteerde lijst van (datum, prijs):
    - Tweakers AJAX price_chart JSON ({"dataset": {"source": [["2026-01-01", 249.0, 269.0], ...]}})
    - JSON-bestand of JSON-string met [{"date": "2026-01-01", "price": 249.0}, ...] of [["2026-01-01", 249.0], ...]
    - CSV-bestand met kolommen (date/datum, price/prijs)
    - Lijst van getallen (bijv. [250, 255, 248, 299]): krijgt automatisch dagelijkse datums terug in de tijd
    """
    if isinstance(raw_history, Path) or (
        isinstance(raw_history, str)
        and len(raw_history) < 260
        and Path(raw_history).exists()
    ):
        path = Path(raw_history)
        content = path.read_text(encoding="utf-8", errors="ignore").strip()
        if path.suffix.lower() == ".csv":
            return _parse_csv_history(content)
        return parse_price_history_points(content)

    if isinstance(raw_history, str):
        s = raw_history.strip()
        if s.startswith("{") or s.startswith("["):
            try:
                data = json.loads(s)
                return parse_price_history_points(data)
            except Exception:
                pass
        # Komma-gescheiden reeks prijzen (bijv. "249, 255, 260, 299")
        nums: list[float] = []
        for part in s.split(","):
            part_clean = part.strip().replace("€", "").strip()
            if not part_clean:
                continue
            try:
                nums.append(float(part_clean))
            except ValueError:
                pass
        if nums:
            return _prices_Without_dates_to_dated(nums)
        return []

    if isinstance(raw_history, dict):
        # Tweakers chart format: {"dataset": {"source": [...]}}
        if "dataset" in raw_history and isinstance(
            raw_history["dataset"], dict
        ):
            source = raw_history["dataset"].get("source", [])
            return parse_price_history_points(source)
        if "history" in raw_history:
            return parse_price_history_points(raw_history["history"])
        if "prices" in raw_history:
            return parse_price_history_points(raw_history["prices"])
        # Dict van {"YYYY-MM-DD": price}
        dated_from_map: list[tuple[date, float]] = []
        for k, v in raw_history.items():
            try:
                d_obj = date.fromisoformat(str(k)[:10])
                p_val = float(v)
                if p_val > 0:
                    dated_from_map.append((d_obj, p_val))
            except Exception:
                continue
        dated_from_map.sort(key=lambda x: x[0])
        return dated_from_map

    if isinstance(raw_history, Iterable):
        items = list(raw_history)
        if not items:
            return []
        if all(isinstance(x, (int, float)) for x in items):
            return _prices_Without_dates_to_dated([float(x) for x in items])

        dated: list[tuple[date, float]] = []
        for row in items:
            if isinstance(row, (list, tuple)) and len(row) >= 2:
                try:
                    d_obj = (
                        row[0]
                        if isinstance(row[0], date)
                        else date.fromisoformat(str(row[0])[:10])
                    )
                    p_val = float(row[1])
                    if p_val > 0:
                        dated.append((d_obj, p_val))
                except Exception:
                    continue
            elif isinstance(row, dict):
                d_raw = (
                    row.get("date")
                    or row.get("datum")
                    or row.get("day")
                    or row.get("timestamp")
                )
                p_raw = (
                    row.get("price")
                    or row.get("prijs")
                    or row.get("min_price")
                    or row.get("value")
                )
                if d_raw is not None and p_raw is not None:
                    try:
                        d_obj = date.fromisoformat(str(d_raw)[:10])
                        p_val = float(p_raw)
                        if p_val > 0:
                            dated.append((d_obj, p_val))
                    except Exception:
                        continue
        dated.sort(key=lambda x: x[0])
        return dated

    return []


def _parse_csv_history(csv_text: str) -> list[tuple[date, float]]:
    lines = [ln for ln in csv_text.splitlines() if ln.strip()]
    if not lines:
        return []
    reader = csv.reader(lines)
    dated: list[tuple[date, float]] = []
    for row in reader:
        if len(row) < 2:
            continue
        try:
            d_obj = date.fromisoformat(row[0].strip()[:10])
            p_val = float(row[1].strip().replace(",", "."))
            if p_val > 0:
                dated.append((d_obj, p_val))
        except Exception:
            continue
    dated.sort(key=lambda x: x[0])
    return dated


def _prices_Without_dates_to_dated(
    prices: list[float],
) -> list[tuple[date, float]]:
    """
    Als een gebruiker een lijst van historische prijzen zonder datums opgeeft:
    verdeel deze gelijkmatig over het afgelopen jaar (365 dagen) tot vandaag.
    """
    clean = [p for p in prices if p > 0]
    if not clean:
        return []
    today = date.today()
    n = len(clean)
    if n == 1:
        return [(today - timedelta(days=120), clean[0])]
    step = max(1, 365 // max(1, n - 1))
    return [
        (today - timedelta(days=(n - 1 - idx) * step), val)
        for idx, val in enumerate(clean)
    ]


def analyze_price_history(
    current_price: float,
    dated_prices: list[tuple[date, float]],
    fallback_before_price: float | None = None,
    is_shop_verified: bool = True,
    lookback_days: int = 365,
    exclude_recent_days: int = 60,
) -> PriceAnalysis:
    """
    Berekent de echte korting t.o.v. het jaargemiddelde EXCLUSIEF de laatste 2 maanden (standaard 60 dagen),
    zodat Black Friday prijsopdrijving ('scam-kortingen') direct wordt ontmaskerd.
    """
    avg_year_excl_2m: float | None = None
    peak_last_2m: float | None = None
    prev_30d: float | None = fallback_before_price
    low_year: float | None = None
    high_year: float | None = None

    if dated_prices:
        dated_sorted = sorted(dated_prices, key=lambda x: x[0])
        latest_date = dated_sorted[-1][0]
        cutoff_recent = latest_date - timedelta(days=exclude_recent_days)
        cutoff_year = latest_date - timedelta(days=lookback_days)

        year_all_prices = [
            p for d_obj, p in dated_sorted if d_obj >= cutoff_year and p > 0
        ]
        if year_all_prices:
            low_year = round(min(year_all_prices), 2)
            high_year = round(max(year_all_prices), 2)

        # Jaargemiddelde EXCLUSIEF de laatste 2 maanden (dag -365 t/m dag -60)
        year_excl_2m_prices = [
            p
            for d_obj, p in dated_sorted
            if cutoff_year <= d_obj <= cutoff_recent and p > 0
        ]
        if year_excl_2m_prices:
            avg_year_excl_2m = round(statistics.mean(year_excl_2m_prices), 2)
        else:
            # Als een product korter dan 60 dagen op de markt is, gebruik de eerste helft van de historie
            half = max(1, len(dated_sorted) // 2)
            avg_year_excl_2m = round(
                statistics.mean(p for _, p in dated_sorted[:half]), 2
            )

        # Piekprijs in de laatste 2 maanden (om te zien of de prijs vlak voor Black Friday is opgepompt)
        last_2m_prices = [
            p for d_obj, p in dated_sorted if d_obj > cutoff_recent and p > 0
        ]
        if last_2m_prices:
            peak_last_2m = round(max(last_2m_prices), 2)

        if prev_30d is None:
            if len(dated_sorted) >= 31:
                prev_30d = round(dated_sorted[-31][1], 2)
            else:
                prev_30d = round(dated_sorted[0][1], 2)
    elif fallback_before_price and fallback_before_price > 0:
        avg_year_excl_2m = round(fallback_before_price, 2)
        peak_last_2m = round(fallback_before_price, 2)
        low_year = round(min(current_price, fallback_before_price), 2)
        high_year = round(max(current_price, fallback_before_price), 2)

    if avg_year_excl_2m and avg_year_excl_2m > 0:
        real_discount_vs_year_pct = round(
            ((avg_year_excl_2m - current_price) / avg_year_excl_2m) * 100.0, 1
        )
        real_savings_vs_year_eur = round(avg_year_excl_2m - current_price, 2)
    else:
        real_discount_vs_year_pct = 0.0
        real_savings_vs_year_eur = 0.0

    if peak_last_2m and peak_last_2m > current_price:
        fake_discount_vs_2m_peak_pct = round(
            ((peak_last_2m - current_price) / peak_last_2m) * 100.0, 1
        )
    else:
        fake_discount_vs_2m_peak_pct = 0.0

    is_at_year_low = low_year is not None and current_price <= low_year * 1.02

    if real_discount_vs_year_pct <= 2.0 and fake_discount_vs_2m_peak_pct >= 10.0:
        verdict = "🚨 SCAM / NEPKORTING (Opgepompt!)"
    elif real_discount_vs_year_pct < 0:
        verdict = "❌ DUURDER DAN JAARGEMIDDELDE"
    elif real_discount_vs_year_pct >= 15.0 and is_at_year_low:
        verdict = "🔥 TOP DEAL (Ver onder jaargem.)"
    elif real_discount_vs_year_pct >= 10.0:
        verdict = "✅ ECHTE DEAL (Onder jaargem.)"
    elif real_discount_vs_year_pct >= 5.0:
        verdict = "👍 GOEDE KORTING (Onder jaargem.)"
    else:
        verdict = "🆗 MINIMALE KORTING (<5% vs jaar)"

    deal_score = round(
        real_discount_vs_year_pct
        + (3.0 if is_at_year_low else 0.0)
        + (2.0 if is_shop_verified else 0.0),
        1,
    )

    return PriceAnalysis(
        current_price=current_price,
        avg_year_excl_2m=avg_year_excl_2m,
        peak_last_2m=peak_last_2m,
        previous_price_30d=prev_30d,
        lowest_price_year=low_year,
        highest_price_year=high_year,
        real_discount_vs_year_pct=real_discount_vs_year_pct,
        real_savings_vs_year_eur=real_savings_vs_year_eur,
        fake_discount_vs_2m_peak_pct=fake_discount_vs_2m_peak_pct,
        is_at_year_low=is_at_year_low,
        deal_verdict=verdict,
        deal_score=deal_score,
        data_points_count=len(dated_prices),
    )
