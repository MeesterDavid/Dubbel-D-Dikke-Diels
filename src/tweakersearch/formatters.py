from __future__ import annotations

import csv
from dataclasses import asdict
import json
from typing import Sequence

from tweakersearch.categories import list_categories_by_group
from tweakersearch.models import ProductDeal


def fmt_eur(val: float | None) -> str:
    if val is None:
        return "-"
    return (
        f"€ {val:,.2f}"
        .replace(",", "X")
        .replace(".", ",")
        .replace("X", ".")
        .replace(",00", ",-")
    )


def format_cli_table(deals: Sequence[ProductDeal], limit: int = 25) -> str:
    """Universele CLI-tabel voor elk type apparatuur op Tweakers."""
    if not deals:
        return "Geen deals gevonden die aan alle criteria voldoen."

    headers = [
        "#",
        "Product",
        "Categorie / Specs",
        "Prijs (incl btw)",
        "Jaargem (excl 2m)",
        "Piek (2m)",
        "Echte Korting vs Jaar",
        "Winkel (Live Check)",
        "Oordeel (vs Jaargem. excl. 2m)",
    ]
    rows: list[list[str]] = []
    for idx, d in enumerate(deals[:limit], start=1):
        sign = "-" if d.real_discount_vs_year_pct >= 0 else "+"
        sav_sign = "-" if d.real_savings_vs_year_eur >= 0 else "+"
        real_disc_str = (
            f"{sign}{abs(d.real_discount_vs_year_pct):g}% "
            f"({sav_sign}€{abs(d.real_savings_vs_year_eur):g})"
        )
        shop_chk = (
            f"{'✅' if d.shop_price_verified else '🔗'} {d.cheapest_shop[:15]}"
        )
        if (
            d.tweakers_listed_price > 0
            and abs(d.current_price - d.tweakers_listed_price) > 1.50
        ):
            shop_chk += f" (Twk: €{d.tweakers_listed_price:g})"
        spec_col = d.key_specs_summary or d.dimensions_str or d.subcategory_or_type
        rows.append(
            [
                str(idx),
                d.name[:33] + ("…" if len(d.name) > 33 else ""),
                spec_col[:26] + ("…" if len(spec_col) > 26 else ""),
                fmt_eur(d.current_price),
                fmt_eur(d.avg_year_excl_2m),
                fmt_eur(d.peak_last_2m),
                real_disc_str,
                shop_chk,
                d.deal_verdict,
            ]
        )

    col_widths = [
        max(len(headers[c]), *(len(r[c]) for r in rows))
        for c in range(len(headers))
    ]

    def fmt_row(r: list[str]) -> str:
        return " | ".join(
            r[c].ljust(col_widths[c]) for c in range(len(headers))
        )

    sep = "-+-".join("-" * w for w in col_widths)
    lines = [fmt_row(headers), sep]
    for r in rows:
        lines.append(fmt_row(r))
    return "\n".join(lines)


def format_telegram_deals_list(
    deals: Sequence[ProductDeal], title: str, limit: int = 8
) -> str:
    """
    Formatteert een lijst van deals als een compact, overzichtelijk bericht voor Telegram / chat-bots.
    """
    if not deals:
        return f"🔍 *{title}*\nGeen producten gevonden die aan alle filters voldoen."

    lines = [
        f"🎯 *{title}*",
        "_Korting berekend t.o.v. jaargemiddelde (dag -365 t/m -60, excl. laatste 2 mnd) • Prijzen incl. 21% btw_",
        "",
    ]
    for idx, d in enumerate(deals[:limit], start=1):
        sign = "-" if d.real_discount_vs_year_pct >= 0 else "+"
        sav_sign = "-" if d.real_savings_vs_year_eur >= 0 else "+"
        spec_info = (
            f" • _{d.key_specs_summary}_" if d.key_specs_summary else ""
        )
        cashback_info = (
            f" 🎁 `{d.promo_cashback_note}`" if d.promo_cashback_note else ""
        )
        shop_link = (
            f"[{d.cheapest_shop}]({d.shop_url})"
            if d.shop_url
            else d.cheapest_shop
        )
        lines.append(
            f"*{idx}. {d.name}*{spec_info}\n"
            f"   💰 *{fmt_eur(d.current_price)}* incl. btw bij {shop_link}{cashback_info}\n"
            f"   📉 *{sign}{abs(d.real_discount_vs_year_pct):g}%* ({sav_sign}{fmt_eur(abs(d.real_savings_vs_year_eur))}) vs jaargem. {fmt_eur(d.avg_year_excl_2m)} "
            f"_(2m-piek: {fmt_eur(d.peak_last_2m)})_\n"
            f"   🏷️ {d.deal_verdict} | [Tweakers]({d.url})"
        )
        lines.append("")
    return "\n".join(lines).strip()


def format_telegram_product_card(deal: ProductDeal) -> str:
    """
    Formatteert een gedetailleerde single-product 'Black Friday Deal & Scam Check' kaart
    voor Telegram of CLI.
    """
    sign = "-" if deal.real_discount_vs_year_pct >= 0 else "+"
    sav_sign = "-" if deal.real_savings_vs_year_eur >= 0 else "+"
    shop_link = (
        f"[{deal.cheapest_shop}]({deal.shop_url})"
        if deal.shop_url
        else deal.cheapest_shop
    )
    lines = [
        f"🔎 *Black Friday Deal & Prijshistorie Check*",
        f"📦 *{deal.name}* ({deal.category})",
        "",
        f"🏷️ *Oordeel:* {deal.deal_verdict}",
        f"💰 *Echte Prijs (incl. 21% btw):* *{fmt_eur(deal.current_price)}* bij {shop_link}",
        f"🔍 *Winkel-check:* {deal.verification_note}",
    ]
    if deal.ignored_price_excl_vat:
        lines.append(
            f"🚫 *Zakelijke prijs genegeerd:* {fmt_eur(deal.ignored_price_excl_vat)} excl. btw"
        )
    if deal.promo_cashback_note:
        lines.append(f"🎁 *Extra actie op winkelpagina:* {deal.promo_cashback_note}")

    lines.extend(
        [
            "",
            "📊 *Prijshistorie-analyse (Anti-Scam):*",
            f"• *Jaargemiddelde (excl. laatste 2 mnd):* {fmt_eur(deal.avg_year_excl_2m)}",
            f"• *Echte korting vs jaargemiddelde:* *{sign}{abs(deal.real_discount_vs_year_pct):g}%* ({sav_sign}{fmt_eur(abs(deal.real_savings_vs_year_eur))})",
            f"• *Piekprijs laatste 2 maanden:* {fmt_eur(deal.peak_last_2m)} "
            f"_(schijnkorting vs piek: -{deal.fake_discount_vs_2m_peak_pct:g}%)_",
            f"• *Laagste prijs afgelopen jaar:* {fmt_eur(deal.lowest_price_year)}",
        ]
    )
    if deal.dimensions_str:
        lines.append(f"📏 *Afmetingen:* {deal.dimensions_str}")
    if deal.key_specs_summary:
        lines.append(f"⚙️ *Belangrijkste specs:* {deal.key_specs_summary}")
    if deal.url:
        lines.append(f"🔗 *Tweakers Pricewatch:* {deal.url}")
    if deal.shop_url:
        lines.append(f"🛒 *Directe Particuliere Winkel-link:* {deal.shop_url}")

    return "\n".join(lines)


def format_categories_overview() -> str:
    grouped = list_categories_by_group()
    lines = ["📚 *Ondersteunde Tweakers Categorieën (selectie):*", ""]
    for grp, cats in grouped.items():
        items = ", ".join(f"`{c.slug}` ({c.label})" for c in cats)
        lines.append(f"• *{grp}:* {items}")
    lines.append("")
    lines.append(
        "💡 _Tip: Elke andere Tweakers-categorie of zoekterm werkt ook automatisch!_"
    )
    return "\n".join(lines)


def export_product_deals(
    deals: Sequence[ProductDeal],
    title: str = "Tweakers Black Friday Deals",
    csv_path: str = "tweakers_deals_export.csv",
    md_path: str = "tweakers_deals_export.md",
    json_path: str = "tweakers_deals_export.json",
) -> None:
    fieldnames = [
        "product_id",
        "category",
        "subcategory_or_type",
        "name",
        "key_specs_summary",
        "dimensions_str",
        "current_price",
        "tweakers_listed_price",
        "shop_price_verified",
        "verification_note",
        "ignored_price_excl_vat",
        "promo_cashback_note",
        "avg_year_excl_2m",
        "peak_last_2m",
        "previous_price_30d",
        "lowest_price_year",
        "real_discount_vs_year_pct",
        "real_savings_vs_year_eur",
        "fake_discount_vs_2m_peak_pct",
        "deal_verdict",
        "deal_score",
        "cheapest_shop",
        "shop_url",
        "rating",
        "url",
    ]
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for d in deals:
            writer.writerow(d.to_dict())

    with open(json_path, "w", encoding="utf-8") as f:
        json.dump([d.to_dict() for d in deals], f, ensure_ascii=False, indent=2)

    md_lines = [
        f"# {title}",
        "",
        "- **100% Particuliere Consumentenprijzen (incl. 21% btw)**: Zakelijke prijzen (`excl. btw`) en zakelijke/affiliate tracking-parameters zijn uitgefilterd.",
        "- **Echte Korting-berekening**: Vergeleken met het **jaargemiddelde (dag -365 t/m dag -60)**, dus **exclusief de laatste 2 maanden** waarin winkels prijzen vaak kunstmatig verhogen vóór Black Friday.",
        "- **Live Winkel-verificatie**: Elke prijs is gecontroleerd door daadwerkelijk op de Tweakers clickout-link te klikken en de consumentenprijs (incl. btw) op de winkelpagina zelf uit te lezen.",
        "",
        "| # | Product (Tweakers) | Categorie / Specs | Afmetingen | Echte Prijs (incl. btw) | Jaargem. (excl. 2m) | Piek laatste 2m | Echte Korting vs Jaar | Winkel & Verificatie | Oordeel |",
        "|---|--------------------|-------------------|------------|-------------------------|---------------------|-----------------|-----------------------|----------------------|---------|",
    ]
    for idx, d in enumerate(deals, start=1):
        sign = "-" if d.real_discount_vs_year_pct >= 0 else "+"
        sav_sign = "-" if d.real_savings_vs_year_eur >= 0 else "+"
        shop_link = (
            f"[{d.cheapest_shop}]({d.shop_url})"
            if d.shop_url
            else d.cheapest_shop
        )
        spec_col = d.key_specs_summary or d.subcategory_or_type or "-"
        dims_col = d.dimensions_str or "-"
        md_lines.append(
            f"| {idx} | [{d.name}]({d.url}) | {spec_col} | {dims_col} "
            f"| **{fmt_eur(d.current_price)}** | {fmt_eur(d.avg_year_excl_2m)} | {fmt_eur(d.peak_last_2m)} "
            f"| **{sign}{abs(d.real_discount_vs_year_pct):g}%** ({sav_sign}{fmt_eur(abs(d.real_savings_vs_year_eur))}) "
            f"| {shop_link} ({d.verification_note}) | {d.deal_verdict} |"
        )
    md_lines.append("")

    with open(md_path, "w", encoding="utf-8") as f:
        f.write("\n".join(md_lines))
