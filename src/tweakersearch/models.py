from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class PriceAnalysis:
    """Resultaat van de Black Friday prijshistorie-analyse (jaargemiddelde excl. laatste 2 maanden)."""

    current_price: float  # Huidige echte consumentenprijs (incl. 21% btw)
    avg_year_excl_2m: float | None  # Jaargemiddelde dag -365 t/m dag -60 (vóór Black Friday prijsopdrijving)
    peak_last_2m: float | None  # Hoogste prijs in de laatste 60 dagen (laat zien of prijs is opgepompt)
    previous_price_30d: float | None  # Prijs 30 dagen geleden
    lowest_price_year: float | None  # Laagste prijs in de afgelopen 365 dagen
    highest_price_year: float | None  # Hoogste prijs in de afgelopen 365 dagen
    real_discount_vs_year_pct: float  # Echte korting % t.o.v. jaargemiddelde (excl. laatste 2 mnd)
    real_savings_vs_year_eur: float  # Echte besparing € t.o.v. jaargemiddelde (excl. laatste 2 mnd)
    fake_discount_vs_2m_peak_pct: float  # Schijnkorting % t.o.v. de piek van de laatste 2 maanden
    is_at_year_low: bool  # True als huidige prijs op/rond het laagste punt van het jaar ligt
    deal_verdict: str  # Eindoordeel (bijv. TOP DEAL, ECHTE DEAL, SCAM / NEPKORTING)
    deal_score: float  # Sorteerscore op basis van echte korting + jaarlaagterecord + winkelverificatie
    data_points_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ShopOfferVerification:
    """Resultaat van het daadwerkelijk doorklikken naar de externe webshop en live prijsverificatie (incl. btw)."""

    shop_name: str
    shop_url: str  # Schone particuliere URL (zonder zakelijke excl.-btw params of affiliate-tracking)
    tweakers_listed_price: float
    live_price_incl_vat: float
    ignored_price_excl_vat: float | None = None
    promo_cashback_note: str = ""
    verified: bool = False
    verification_note: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ProductDeal:
    """
    Universeel datamodel voor elk product/apparaat op Tweakers (of met bekende prijshistorie),
    inclusief specificaties, live winkelverificatie (incl. btw) en anti-scam prijshistorie-analyse.
    """

    product_id: str
    name: str
    category: str
    subcategory_or_type: str
    url: str
    tweakers_listed_price: float
    current_price: float  # De echte, op de webshop geverifieerde consumentenprijs (incl. 21% btw)
    shop_price_verified: bool
    verification_note: str
    cheapest_shop: str
    shop_url: str
    avg_year_excl_2m: float | None
    peak_last_2m: float | None
    previous_price_30d: float | None
    lowest_price_year: float | None
    real_discount_vs_year_pct: float
    real_savings_vs_year_eur: float
    fake_discount_vs_2m_peak_pct: float
    deal_verdict: str
    deal_score: float
    key_specs_summary: str = ""
    dimensions_str: str = ""
    rating: str = "-"
    ignored_price_excl_vat: float | None = None
    promo_cashback_note: str = ""
    specs: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
