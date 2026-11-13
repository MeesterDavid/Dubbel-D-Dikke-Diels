from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
import html as html_lib
import json
from pathlib import Path
import re
from typing import Any
import urllib.parse

from tweakersearch.analyzer import (
    analyze_price_history,
    parse_price_history_points,
)
from tweakersearch.categories import CategoryConfig, resolve_category_config
from tweakersearch.models import ProductDeal
from tweakersearch.scraper import (
    TweakersClient,
    _extract_pct,
    _parse_deals_table,
    _parse_vergelijken_items,
    clean_consumer_shop_url,
    clean_html_text,
    extract_live_price_from_shop_html,
    extract_product_specs,
    is_plausible_height_mm,
    parse_mm,
    parse_price,
)

UNIVERSAL_CACHE_FILE = Path(".tweakers_universal_deals_cache.json")


def _summarize_key_specs(specs: dict[str, str], fallback_specline: str = "") -> str:
    """Maakt een korte, leesbare samenvatting van de belangrijkste specificaties voor elk apparaattype."""
    priority_keys = (
        # Beeld & Monitoren / TV
        "Schermdiagonaal",
        "Resolutie",
        "Paneeltype",
        "Refreshrate",
        # Videokaarten / CPU / Laptop / Opslag
        "Videochip",
        "Geheugengrootte",
        "Processor",
        "Werkgeheugen",
        "Opslagcapaciteit",
        # Stofzuigers & Huishouden
        "Stofzuigermodel",
        "Zakloos",
        "Inhoud",
        "Stofcapaciteit (basisstation)",
        "Vulgewicht",
        "Energieklasse",
        "Geluidssterkte (max.)",
        # Audio
        "Type koptelefoon",
        "Noise-cancelling",
    )
    parts: list[str] = []
    for k in priority_keys:
        val = specs.get(k)
        if not val:
            continue
        if k == "Zakloos":
            parts.append("Met zak" if val == "Nee" else "Zakloos")
        elif k in ("Inhoud", "Stofcapaciteit (basisstation)"):
            parts.append(f"Inhoud: {val}")
        elif k == "Noise-cancelling":
            if val.lower() not in ("nee", "geen"):
                parts.append(f"ANC: {val}")
        elif k in ("Werkgeheugen", "Geheugengrootte"):
            parts.append(f"RAM/VRAM: {val}")
        elif k == "Opslagcapaciteit":
            parts.append(f"Opslag: {val}")
        else:
            parts.append(val)
        if len(parts) >= 4:
            break

    if parts:
        return " • ".join(parts)
    return fallback_specline[:65] if fallback_specline else ""


def _build_dimensions_str(specs: dict[str, str]) -> str:
    h_mm = parse_mm(specs.get("Hoogte"))
    w_mm = parse_mm(specs.get("Breedte"))
    d_mm = parse_mm(specs.get("Diepte") or specs.get("Lengte"))
    st_mm = parse_mm(specs.get("Hoogte basisstation"))
    parts: list[str] = []
    if h_mm:
        parts.append(f"H: {round(h_mm / 10.0, 2):g} cm")
    if w_mm:
        parts.append(f"B: {round(w_mm / 10.0, 1):g} cm")
    if d_mm:
        parts.append(f"D: {round(d_mm / 10.0, 1):g} cm")
    if st_mm:
        parts.append(f"Dock H: {round(st_mm / 10.0, 1):g} cm")
    return " × ".join(parts)


class TweakersDealService:
    """
    Herbruikbare kernservice voor zowel CLI als Telegram/chat-bots:
    - Werkt voor élke Tweakers-categorie (videokaarten, tv's, monitoren, laptops, stofzuigers, etc.)
    - Werkt voor vrije zoekopdrachten op Tweakers
    - Werkt voor individuele Tweakers-productpagina's of product-ID's
    - Werkt voor externe/bekende prijshistorie (JSON, CSV of lijst van prijzen)
    - Klikt altijd door naar de winkelpagina om de echte consumentenprijs (incl. 21% btw, nooit excl. btw) te verifiëren
    """

    def __init__(
        self,
        min_interval_sec: float = 0.16,
        max_workers: int = 4,
        use_cache: bool = True,
    ) -> None:
        self.client = TweakersClient(min_interval_sec=min_interval_sec)
        self.max_workers = max_workers
        self.use_cache = use_cache
        self._cache: dict[str, dict[str, Any]] = (
            self._load_cache() if use_cache else {}
        )

    def _load_cache(self) -> dict[str, dict[str, Any]]:
        if UNIVERSAL_CACHE_FILE.exists():
            try:
                return json.loads(
                    UNIVERSAL_CACHE_FILE.read_text(encoding="utf-8")
                )
            except Exception:
                pass
        return {}

    def _save_cache(self) -> None:
        if not self.use_cache:
            return
        try:
            UNIVERSAL_CACHE_FILE.write_text(
                json.dumps(self._cache, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception:
            pass

    def find_category_deals(
        self,
        category: str,
        query: str | None = None,
        max_price: float | None = None,
        min_discount_pct: float | None = None,
        max_height_cm: float | None = None,
        bag_type: str | None = None,
        spec_filters: dict[str, str] | None = None,
        max_candidates: int = 30,
        max_pages: int = 2,
    ) -> list[ProductDeal]:
        """
        Zoekt de beste deals binnen een willekeurige Tweakers-categorie, verifieert de prijzen
        live bij de webshops (incl. btw) en berekent de echte korting t.o.v. het jaargemiddelde (excl. laatste 2 mnd).
        """
        self.client.warmup()
        cat_cfg: CategoryConfig = resolve_category_config(category)

        # Als de categorie niet direct bekend was, check via Tweakers AJAX of er een exacte categorie-link is
        if cat_cfg.group == "Overig (Tweakers)":
            resolved_slug = self._discover_category_slug_via_ajax(category)
            if resolved_slug:
                cat_cfg = resolve_category_config(resolved_slug)

        extra_params: list[str] = []
        if query:
            extra_params.append(f"kw={urllib.parse.quote_plus(query)}")
        if max_height_cm is not None and "max_height_mm" in cat_cfg.url_filter_params:
            param_name = cat_cfg.url_filter_params["max_height_mm"]
            extra_params.append(f"{param_name}={max_height_cm * 10.0:.1f}")
        if bag_type in ("bagged", "bagless") and bag_type in cat_cfg.url_filter_params:
            extra_params.append(cat_cfg.url_filter_params[bag_type])

        query_suffix = ("&" + "&".join(extra_params)) if extra_params else ""
        candidates: dict[str, dict[str, Any]] = {}

        # 1. Haal producten op van /<slug>/vergelijken/ met kortingsfilter (minrpd)
        for page in range(1, max_pages + 1):
            url = (
                f"https://tweakers.net/{cat_cfg.slug}/vergelijken/"
                f"?minrpd={cat_cfg.default_min_rpd}{query_suffix}&page={page}"
            )
            try:
                html = self.client.fetch(url)
            except Exception:
                break
            found = _parse_vergelijken_items(html, candidates)
            if found == 0 or f"page={page + 1}" not in html:
                break

        # Als er met minrpd niets werd gevonden (bijv. specifieke zoekterm), probeer zonder minrpd
        if not candidates:
            url = f"https://tweakers.net/{cat_cfg.slug}/vergelijken/?page=1{query_suffix}"
            try:
                html = self.client.fetch(url)
                _parse_vergelijken_items(html, candidates)
            except Exception:
                pass

        # 2. Verrijk met gegevens van /<slug>/deals/
        deals_map: dict[str, dict[str, Any]] = {}
        for dpage in range(1, 3):
            try:
                dhtml = self.client.fetch(
                    f"https://tweakers.net/{cat_cfg.slug}/deals/?page={dpage}"
                )
                cnt = _parse_deals_table(dhtml, deals_map)
                if cnt == 0 or f"page={dpage + 1}" not in dhtml:
                    break
            except Exception:
                break

        for pid, dinfo in deals_map.items():
            if pid in candidates:
                candidates[pid].update(
                    {k: v for k, v in dinfo.items() if v is not None and v != ""}
                )
            elif not extra_params:
                candidates[pid] = dinfo

        # Filter alvast op max_price en query indien opgegeven
        cand_list = list(candidates.values())
        if query:
            q_tokens = [t.lower() for t in query.split() if t.strip()]
            cand_list = [
                c
                for c in cand_list
                if all(
                    tok in c["name"].lower()
                    or tok in c.get("specline", "").lower()
                    for tok in q_tokens
                )
            ]
        if max_price is not None:
            cand_list = [
                c
                for c in cand_list
                if 0 < float(c.get("current_price") or 0) <= max_price * 1.10
            ]

        # Sorteer kandidaten op vermelde korting zodat we de interessantste eerst verifiëren
        cand_list.sort(
            key=lambda x: float(x.get("listed_discount_pct") or 0.0),
            reverse=True,
        )
        selected = cand_list[:max_candidates]

        deals = self._verify_candidates_parallel(
            selected,
            category_label=cat_cfg.label,
            max_price=max_price,
            min_discount_pct=min_discount_pct,
            max_height_cm=max_height_cm,
            bag_type=bag_type,
            spec_filters=spec_filters,
        )
        self._save_cache()
        return deals

    def search_products(
        self,
        query: str,
        max_price: float | None = None,
        min_discount_pct: float | None = None,
        spec_filters: dict[str, str] | None = None,
        max_candidates: int = 15,
    ) -> list[ProductDeal]:
        """
        Zoekt op vrije tekst door heel Tweakers Pricewatch (bijv. 'RTX 5080', 'Sony WH-1000XM5', 'LG OLED C5', 'AEG 8000'),
        haalt bijbehorende uitvoeringen op, klikt door naar de winkels en berekent de echte korting vs jaargemiddelde.
        """
        self.client.warmup()
        q_enc = urllib.parse.quote_plus(query.strip())
        ajax_url = f"https://tweakers.net/ajax/zoeken/pricewatch/?keyword={q_enc}"
        raw = self.client.fetch(ajax_url)
        data = json.loads(raw)

        candidates: dict[str, dict[str, Any]] = {}

        # 1. Directe product-edities uit 'articles'
        for art in data.get("articles", []):
            link = str(art.get("link") or "")
            pid = str(art.get("id") or "")
            name = clean_html_text(str(art.get("name") or ""))
            if pid and "/pricewatch/" in link:
                candidates[pid] = {
                    "product_id": pid,
                    "name": name,
                    "url": link,
                    "current_price": 0.0,
                    "before_price": None,
                    "listed_discount_pct": 0.0,
                    "badge": "",
                    "specline": "",
                    "rating": "",
                }

        # 2. Product-families / categorieën uit 'entities'
        for ent in data.get("entities", []):
            etype = ent.get("type")
            link = str(ent.get("link") or "")
            if etype == "product" and "/pricewatch/" in link:
                m_id = re.search(r"/pricewatch/(\d+)/", link)
                if m_id:
                    pid = m_id.group(1)
                    candidates.setdefault(
                        pid,
                        {
                            "product_id": pid,
                            "name": clean_html_text(str(ent.get("name") or "")),
                            "url": link,
                            "current_price": 0.0,
                            "before_price": None,
                            "listed_discount_pct": 0.0,
                            "badge": "",
                            "specline": "",
                            "rating": "",
                        },
                    )
            elif etype in ("product", "series") and (
                link.endswith("/vergelijken/") or "/serie/" in link
            ):
                cmp_url = (
                    link
                    if link.endswith("/vergelijken/")
                    else link.rstrip("/") + "/vergelijken/"
                )
                try:
                    html = self.client.fetch(cmp_url)
                    _parse_vergelijken_items(html, candidates)
                except Exception:
                    pass
            elif etype == "category" and not candidates:
                # Als de zoekterm een categorie is, zoek binnen die categorie
                return self.find_category_deals(
                    category=link,
                    max_price=max_price,
                    min_discount_pct=min_discount_pct,
                    spec_filters=spec_filters,
                    max_candidates=max_candidates,
                )

        selected = list(candidates.values())[:max_candidates]
        deals = self._verify_candidates_parallel(
            selected,
            category_label=f"Zoekresultaat '{query}'",
            max_price=max_price,
            min_discount_pct=min_discount_pct,
            spec_filters=spec_filters,
        )
        self._save_cache()
        return deals

    def check_product(
        self, url_or_id: str, force_refresh: bool = False
    ) -> ProductDeal | None:
        """
        Controleert één specifiek product op basis van een Tweakers Pricewatch-URL of product-ID.
        Klikt door naar de winkelpagina voor de echte consumentenprijs (incl. 21% btw) en analyseert de prijshistorie.
        """
        self.client.warmup()
        cleaned = url_or_id.strip()
        m_id = re.search(r"(?:pricewatch/|^)(\d{4,10})(?:/|$)", cleaned)
        if not m_id:
            # Behandel als zoekopdracht en pak het beste resultaat
            results = self.search_products(cleaned, max_candidates=3)
            return results[0] if results else None

        pid = m_id.group(1)
        url = (
            cleaned
            if cleaned.startswith("https://tweakers.net/pricewatch/")
            else f"https://tweakers.net/pricewatch/{pid}/"
        )
        cand = {
            "product_id": pid,
            "name": f"Product #{pid}",
            "url": url,
            "current_price": 0.0,
            "before_price": None,
            "listed_discount_pct": 0.0,
            "badge": "",
            "specline": "",
            "rating": "",
        }
        deal = self._enrich_and_verify_single(
            cand,
            category_label="Tweakers Pricewatch",
            use_cache=self.use_cache and not force_refresh,
        )
        self._save_cache()
        return deal

    def check_custom_price_history(
        self,
        name: str,
        current_price: float | None,
        raw_history: str | Path | list[Any] | dict[str, Any],
        shop_url: str = "",
        shop_name: str = "Externe Webshop",
    ) -> ProductDeal:
        """
        Analyseert een product waarvan de prijshistorie bekend is (ook buiten Tweakers om!).
        Als er een `shop_url` wordt meegegeven, haalt hij bovendien live de actuele consumentenprijs (incl. btw)
        van die webshop op!
        """
        clean_url = clean_consumer_shop_url(shop_url) if shop_url else ""
        verified = False
        ignored_excl: float | None = None
        promo_note = ""
        verif_note = "Handmatig opgegeven prijs (incl. btw)"

        live_price = current_price or 0.0
        if clean_url:
            _, shop_html = self.client.follow_clickout_and_fetch_shop(clean_url)
            extracted, excl_vat, promo = extract_live_price_from_shop_html(
                shop_html, live_price
            )
            if extracted is not None:
                live_price = extracted
                ignored_excl = excl_vat
                promo_note = promo
                verified = True
                verif_note = f"✅ € {live_price:g} incl. btw live geverifieerd op {shop_name}"
                if ignored_excl:
                    verif_note += f" (excl. btw € {ignored_excl:g} genegeerd)"
                if promo_note:
                    verif_note += f" [Actie: {promo_note}]"

        dated_points = parse_price_history_points(raw_history)
        analysis = analyze_price_history(
            current_price=live_price,
            dated_prices=dated_points,
            is_shop_verified=verified,
        )

        return ProductDeal(
            product_id="custom",
            name=name,
            category="Bekende Prijshistorie Check",
            subcategory_or_type=f"{analysis.data_points_count} meetpunten",
            url="",
            tweakers_listed_price=current_price or live_price,
            current_price=live_price,
            shop_price_verified=verified,
            verification_note=verif_note,
            cheapest_shop=shop_name,
            shop_url=clean_url,
            avg_year_excl_2m=analysis.avg_year_excl_2m,
            peak_last_2m=analysis.peak_last_2m,
            previous_price_30d=analysis.previous_price_30d,
            lowest_price_year=analysis.lowest_price_year,
            real_discount_vs_year_pct=analysis.real_discount_vs_year_pct,
            real_savings_vs_year_eur=analysis.real_savings_vs_year_eur,
            fake_discount_vs_2m_peak_pct=analysis.fake_discount_vs_2m_peak_pct,
            deal_verdict=analysis.deal_verdict,
            deal_score=analysis.deal_score,
            key_specs_summary=f"Historie: {analysis.data_points_count} datapunten",
            ignored_price_excl_vat=ignored_excl,
            promo_cashback_note=promo_note,
        )

    def _discover_category_slug_via_ajax(self, keyword: str) -> str | None:
        try:
            q = urllib.parse.quote_plus(keyword.strip())
            raw = self.client.fetch(
                f"https://tweakers.net/ajax/zoeken/pricewatch/?keyword={q}"
            )
            data = json.loads(raw)
            for ent in data.get("entities", []):
                if ent.get("type") == "category" and ent.get("link"):
                    return str(ent["link"])
        except Exception:
            pass
        return None

    def _verify_candidates_parallel(
        self,
        candidates: list[dict[str, Any]],
        category_label: str,
        max_price: float | None = None,
        min_discount_pct: float | None = None,
        max_height_cm: float | None = None,
        bag_type: str | None = None,
        spec_filters: dict[str, str] | None = None,
    ) -> list[ProductDeal]:
        deals: list[ProductDeal] = []
        if not candidates:
            return deals

        with ThreadPoolExecutor(max_workers=self.max_workers) as pool:
            futures = {
                pool.submit(
                    self._enrich_and_verify_single,
                    cand,
                    category_label,
                    max_price,
                    min_discount_pct,
                    max_height_cm,
                    bag_type,
                    spec_filters,
                    self.use_cache,
                ): cand["product_id"]
                for cand in candidates
            }
            for fut in as_completed(futures):
                try:
                    res = fut.result()
                    if res is not None:
                        deals.append(res)
                except Exception:
                    continue

        deals.sort(
            key=lambda d: (
                d.deal_score,
                d.real_discount_vs_year_pct,
                d.real_savings_vs_year_eur,
            ),
            reverse=True,
        )
        return deals

    def _enrich_and_verify_single(
        self,
        cand: dict[str, Any],
        category_label: str,
        max_price: float | None = None,
        min_discount_pct: float | None = None,
        max_height_cm: float | None = None,
        bag_type: str | None = None,
        spec_filters: dict[str, str] | None = None,
        use_cache: bool = True,
    ) -> ProductDeal | None:
        pid = str(cand["product_id"])
        url = str(cand["url"])

        deal: ProductDeal | None = None
        if use_cache and pid in self._cache:
            try:
                deal = ProductDeal(**self._cache[pid])
            except Exception:
                deal = None

        if deal is None:
            try:
                html = self.client.fetch(url)
            except Exception:
                return None

            # Haal de echte productnaam en categorie uit de pagina als die nog niet bekend waren
            name = str(cand.get("name") or "").strip()
            if not name or name.startswith("Product #"):
                m_title = re.search(r"<h1[^>]*>(.*?)</h1>", html, re.S)
                if m_title:
                    name = clean_html_text(m_title.group(1))

            m_cat = re.search(
                r'<a href="https://tweakers\.net/([^/"]+)/"[^>]*class="[^"]*breadcrumb[^"]*"',
                html,
            )
            if m_cat and category_label == "Tweakers Pricewatch":
                category_label = m_cat.group(1).replace("-", " ").title()

            specs = extract_product_specs(html)
            dims_str = _build_dimensions_str(specs)
            key_specs = _summarize_key_specs(specs, cand.get("specline", ""))
            subcat = (
                specs.get("Stofzuigermodel")
                or specs.get("Type koptelefoon")
                or specs.get("Paneeltype")
                or specs.get("Videochip")
                or category_label
            )

            # Verwijder 'Refurbished / Tweedekans'-blok zodat alleen nieuwprijzen (incl. btw) overblijven
            html_new_only = re.sub(
                r'<details class="refurbished-listing">.*?</details>',
                "",
                html,
                flags=re.S,
            )
            twk_listed_price = float(cand.get("current_price") or 0.0)
            if twk_listed_price <= 0:
                m_first_p = re.search(
                    r'<span class="shop-price">\s*<a[^>]*>(.*?)</a>',
                    html_new_only,
                    re.S,
                )
                if m_first_p:
                    twk_listed_price = parse_price(m_first_p.group(1))

            (
                cheapest_shop,
                verified_price,
                shop_url,
                is_verified,
                verification_note,
                ignored_excl_vat,
                promo_cashback,
            ) = self._verify_shop_offers_detailed(
                html_new_only, twk_listed_price
            )

            curr_price = (
                verified_price if verified_price > 0 else twk_listed_price
            )
            if curr_price <= 0:
                return None

            # Haal de volledige prijshistorie op via Tweakers' AJAX price_chart endpoint
            dated_prices = []
            try:
                chart_raw = self.client.fetch(
                    f"https://tweakers.net/ajax/price_chart/{pid}/nl/"
                )
                dated_prices = parse_price_history_points(chart_raw)
            except Exception:
                pass

            analysis = analyze_price_history(
                current_price=curr_price,
                dated_prices=dated_prices,
                fallback_before_price=cand.get("before_price"),
                is_shop_verified=is_verified,
            )

            rating_str = cand.get("rating") or "-"
            m_rev = re.search(
                r'<twk-star-rating data-score="([^"]+)"', html
            )
            if m_rev and m_rev.group(1) != "0 / 5":
                rating_str = m_rev.group(1)

            deal = ProductDeal(
                product_id=pid,
                name=name,
                category=category_label,
                subcategory_or_type=subcat,
                url=url,
                tweakers_listed_price=twk_listed_price,
                current_price=curr_price,
                shop_price_verified=is_verified,
                verification_note=verification_note,
                cheapest_shop=cheapest_shop,
                shop_url=shop_url,
                avg_year_excl_2m=analysis.avg_year_excl_2m,
                peak_last_2m=analysis.peak_last_2m,
                previous_price_30d=analysis.previous_price_30d,
                lowest_price_year=analysis.lowest_price_year,
                real_discount_vs_year_pct=analysis.real_discount_vs_year_pct,
                real_savings_vs_year_eur=analysis.real_savings_vs_year_eur,
                fake_discount_vs_2m_peak_pct=analysis.fake_discount_vs_2m_peak_pct,
                deal_verdict=analysis.deal_verdict,
                deal_score=analysis.deal_score,
                key_specs_summary=key_specs,
                dimensions_str=dims_str,
                rating=rating_str,
                ignored_price_excl_vat=ignored_excl_vat,
                promo_cashback_note=promo_cashback,
                specs=specs,
            )
            if self.use_cache:
                self._cache[pid] = deal.to_dict()

        # Pas eventuele filters toe (hoogte, stofzak, max_price, min_discount, custom specs)
        if max_price is not None and deal.current_price > max_price:
            return None
        if (
            min_discount_pct is not None
            and deal.real_discount_vs_year_pct < min_discount_pct
        ):
            return None
        if max_height_cm is not None:
            h_mm = parse_mm(deal.specs.get("Hoogte"))
            if (
                h_mm is None
                or h_mm > max_height_cm * 10.0
                or not is_plausible_height_mm(h_mm, deal.subcategory_or_type)
            ):
                return None
        if bag_type == "bagged" and deal.specs.get("Zakloos") != "Nee":
            return None
        if bag_type == "bagless" and deal.specs.get("Zakloos") != "Ja":
            return None
        if spec_filters:
            for sk, sv in spec_filters.items():
                matched = False
                for actual_k, actual_v in deal.specs.items():
                    if sk.lower() in actual_k.lower() and sv.lower() in actual_v.lower():
                        matched = True
                        break
                if not matched:
                    return None

        return deal

    def _verify_shop_offers_detailed(
        self, html_new_only: str, fallback_twk_price: float
    ) -> tuple[str, float, str, bool, str, float | None, str]:
        shop_matches = list(
            re.finditer(
                r'<li[^>]*data-shop-id="[^"]+"[^>]*class="price-listing-item border-box-container"[^>]*>(.*?)</li>',
                html_new_only,
                re.S,
            )
        )
        if not shop_matches:
            return (
                "Onbekend",
                fallback_twk_price,
                "",
                False,
                "Geen winkel-link",
                None,
                "",
            )

        offers_checked: list[dict[str, Any]] = []
        discrepancy_notes: list[str] = []

        for idx, m in enumerate(shop_matches[:3]):
            block = m.group(1)
            m_sname = re.search(
                r'<span class="shop-name">\s*<a[^>]*>(.*?)</a>', block, re.S
            )
            sname = (
                clean_html_text(m_sname.group(1)) if m_sname else "Onbekend"
            )

            m_sprice = re.search(
                r'<span class="shop-price">\s*<a[^>]*>(.*?)</a>', block, re.S
            )
            twk_shop_price = (
                parse_price(m_sprice.group(1))
                if m_sprice
                else fallback_twk_price
            )

            m_co = re.search(
                r'href="(https://tweakers\.net/pricewatch/clickout\?[^"]+)"',
                block,
            )
            if not m_co:
                continue
            clickout_url = html_lib.unescape(m_co.group(1))

            shop_url, shop_html = self.client.follow_clickout_and_fetch_shop(
                clickout_url
            )
            live_price, excl_vat_price, promo_note = (
                extract_live_price_from_shop_html(shop_html, twk_shop_price)
            )

            if live_price is not None:
                diff = abs(live_price - twk_shop_price)
                vat_suffix = " incl. btw"
                if excl_vat_price is not None:
                    vat_suffix += f" (excl. btw € {excl_vat_price:g} genegeerd)"
                if promo_note:
                    vat_suffix += f" [Actie: {promo_note}]"

                if diff <= 1.50:
                    note = f"✅ € {live_price:g}{vat_suffix} op {sname}"
                    if discrepancy_notes:
                        note = f"{discrepancy_notes[0]} → Nu {note}"
                    return (
                        sname,
                        live_price,
                        shop_url,
                        True,
                        note,
                        excl_vat_price,
                        promo_note,
                    )
                else:
                    discrepancy_notes.append(
                        f"⚠️ {sname} toont € {live_price:g}{vat_suffix} op site (Tweakers: € {twk_shop_price:g})"
                    )
                    offers_checked.append(
                        {
                            "shop": sname,
                            "price": live_price,
                            "url": shop_url,
                            "verified": True,
                            "note": discrepancy_notes[-1],
                            "excl_vat": excl_vat_price,
                            "promo": promo_note,
                        }
                    )
            else:
                offers_checked.append(
                    {
                        "shop": sname,
                        "price": twk_shop_price,
                        "url": shop_url,
                        "verified": False,
                        "note": f"🔗 Link gevolgd naar {sname} (€ {twk_shop_price:g} incl. btw via Tweakers)",
                        "excl_vat": None,
                        "promo": "",
                    }
                )
                if idx == 0 and len(shop_matches) == 1:
                    break

        if offers_checked:
            offers_checked.sort(key=lambda x: (x["price"], not x["verified"]))
            best = offers_checked[0]
            return (
                best["shop"],
                best["price"],
                best["url"],
                best["verified"],
                best["note"],
                best["excl_vat"],
                best["promo"],
            )

        return (
            "Onbekend",
            fallback_twk_price,
            "",
            False,
            "Onbekend",
            None,
            "",
        )
