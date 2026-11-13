from __future__ import annotations

import argparse
import csv
from datetime import date, timedelta
import html as html_lib
import http.cookiejar
import json
from pathlib import Path
import re
import statistics
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass
from typing import Any

USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64; rv:128.0) Gecko/20100101 Firefox/128.0"
)
CACHE_FILE = Path(".tweakers_verified_bagged_cache.json")


@dataclass
class VacuumDeal:
    product_id: str
    name: str
    category: str  # "Reguliere stofzuiger" of "Robotstofzuiger"
    vacuum_type: str  # bijv. "Slede", "Trommel", "Steel", "Robotstofzuiger"
    has_bag: bool  # True = Met stofzak (Zakloos: Nee)
    bag_capacity: str  # bijv. "4,5L" of "3,5L"
    action_radius: str  # bijv. "11m"
    height_mm: float | None
    height_cm: float | None
    station_height_mm: float | None
    station_height_cm: float | None
    dimensions_str: str
    tweakers_listed_price: float
    current_price: float  # De echte, op de webshop geverifieerde prijs!
    shop_price_verified: bool
    verification_note: str
    avg_year_excl_2m: float | None  # Jaargemiddelde (dag -365 t/m dag -60, vóór BF-prijsopdrijving)
    peak_last_2m: float | None  # Hoogste prijs in de laatste 2 maanden (laat zien of prijs is opgepompt)
    previous_price_30d: float | None  # Prijs 30 dagen geleden
    lowest_price_year: float | None
    real_discount_vs_year_pct: float  # Echte korting % t.o.v. jaargemiddelde (excl. laatste 2 mnd)
    real_savings_vs_year_eur: float  # Echte besparing € t.o.v. jaargemiddelde (excl. laatste 2 mnd)
    fake_discount_vs_2m_peak_pct: float  # Schijnkorting % t.o.v. de piek van de laatste 2 maanden
    deal_verdict: str
    deal_score: float
    cheapest_shop: str
    shop_url: str
    power_or_suction: str
    noise_level: str
    rating: str
    url: str


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def clean_consumer_shop_url(url: str) -> str:
    """
    Maakt van een (affiliate/redirect) winkel-URL een schone particuliere consumenten-URL (incl. btw).
    Verwijdert zakelijke parameters (excl. btw / b2b) en affiliate-tracking parameters.
    """
    if not url or "tweakers.net/pricewatch/clickout" in url:
        return url

    parsed = urllib.parse.urlsplit(url)
    netloc = parsed.netloc.lower()
    path = parsed.path

    # Zet eventuele zakelijke subdomeinen/paden terug naar de particuliere winkelpagina (incl. btw)
    if netloc.startswith("zakelijk."):
        netloc = "www." + netloc[len("zakelijk.") :]
    path = re.sub(r"^/zakelijk(/|$)", "/", path, flags=re.I)

    # Voor Amazon.nl: maak een schone /dp/<ASIN> consumenten-link zonder affiliate-ruis
    if "amazon.nl" in netloc:
        m_asin = re.search(r"/(?:dp|gp/product)/([A-Z0-9]{10})", path)
        if m_asin:
            return f"https://www.amazon.nl/dp/{m_asin.group(1)}"

    # Voor Coolblue, bol.com, MediaMarkt, Electro World, CoolSound, Art & Craft:
    # product-URL heeft geen query-parameters nodig (alle query-params zijn affiliate/tracking of btw-switches)
    if any(
        domain in netloc
        for domain in (
            "coolblue.nl",
            "bol.com",
            "mediamarkt.nl",
            "electroworld.nl",
            "coolsound.nl",
            "artencraft.com",
            "proshop.nl",
            "philips.nl",
        )
    ):
        return urllib.parse.urlunsplit((parsed.scheme or "https", netloc, path, "", ""))

    # Voor overige winkels: verwijder bekende tracking- en zakelijke btw-parameters
    blocked_prefixes = (
        "utm_",
        "aw_",
        "awc",
        "clickref",
        "ref",
        "cmt",
        "referrer",
        "channable",
        "gclid",
        "fbclid",
        "tduid",
        "tag",
        "linkcode",
        "vat",
        "btw",
        "b2b",
        "business",
        "zakelijk",
    )
    clean_qs = [
        (k, v)
        for k, v in urllib.parse.parse_qsl(parsed.query, keep_blank_values=False)
        if not any(k.lower().startswith(p) for p in blocked_prefixes)
    ]
    return urllib.parse.urlunsplit(
        (
            parsed.scheme or "https",
            netloc,
            path,
            urllib.parse.urlencode(clean_qs),
            "",
        )
    )


class TweakersClient:
    """HTTP-client die automatisch de Tweakers/DPG privacy-gate doorloopt én webshop-clickouts verifieert."""

    def __init__(self, min_interval_sec: float = 0.16) -> None:
        self.cookie_jar = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self.cookie_jar)
        )
        self.no_redirect_opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self.cookie_jar),
            _NoRedirectHandler(),
        )
        headers = [
            ("User-Agent", USER_AGENT),
            ("Accept-Language", "nl-NL,nl;q=0.9,en;q=0.8"),
        ]
        self.opener.addheaders = list(headers)
        self.no_redirect_opener.addheaders = list(headers)
        self._warmed_up = False
        self._lock = threading.Lock()
        self._last_req_ts = 0.0
        self._min_interval = min_interval_sec

    def warmup(self) -> None:
        if not self._warmed_up:
            self.fetch("https://tweakers.net/stofzuigers/vergelijken/")
            self._warmed_up = True

    def _throttle(self) -> None:
        with self._lock:
            now = time.monotonic()
            wait = self._min_interval - (now - self._last_req_ts)
            if wait > 0:
                time.sleep(wait)
            self._last_req_ts = time.monotonic()

    def fetch(self, url: str, timeout: int = 15, retries: int = 3) -> str:
        last_err: Exception | None = None
        for attempt in range(retries + 1):
            self._throttle()
            try:
                resp = self.opener.open(url, timeout=timeout)
                content = resp.read().decode("utf-8", errors="ignore")
                m = re.search(r"decodeURIComponent\('([^']+)'\)", content)
                if m and "privacygate-confirm" in m.group(1):
                    cb_url = urllib.parse.unquote(m.group(1))
                    resp = self.opener.open(cb_url, timeout=timeout)
                    content = resp.read().decode("utf-8", errors="ignore")
                return content
            except urllib.error.HTTPError as e:
                last_err = e
                if e.code == 429:
                    retry_after = float(e.headers.get("Retry-After", 4.0))
                    time.sleep(max(retry_after, 3.0 * (attempt + 1)))
                elif attempt < retries:
                    time.sleep(0.6 * (attempt + 1))
            except Exception as e:
                last_err = e
                if attempt < retries:
                    time.sleep(0.5 * (attempt + 1))
        raise RuntimeError(f"Kon {url} niet ophalen: {last_err}")

    def follow_clickout_and_fetch_shop(
        self, clickout_url: str, timeout: int = 14
    ) -> tuple[str, str]:
        """
        Klikt daadwerkelijk op de Tweakers clickout-link, vangt eerst de 302-redirect van Tweakers op
        en volgt daarna met curl alle redirects naar de particuliere (incl. btw) webshop-productpagina.
        """
        target_url = clickout_url
        for attempt in range(3):
            self._throttle()
            try:
                resp = self.no_redirect_opener.open(clickout_url, timeout=timeout)
                target_url = resp.geturl()
                break
            except urllib.error.HTTPError as e:
                if e.code in (301, 302, 303, 307, 308):
                    loc = e.headers.get("Location")
                    if loc:
                        target_url = urllib.parse.urljoin(clickout_url, loc)
                    break
                elif e.code == 429:
                    time.sleep(3.0 * (attempt + 1))
                else:
                    break
            except Exception:
                break

        # Pak eventuele geneste Tradedoubler url(...) direct uit
        m_td_url = re.search(r"url\((https?%3A%2F%2F[^)]+)\)", target_url, re.I)
        if m_td_url:
            target_url = urllib.parse.unquote(m_td_url.group(1))

        shop_url = target_url
        shop_html = ""

        # Volg de externe redirect en haal de winkelpagina op via curl (werkt ook voor bol.com HTTP/2 TLS)
        try:
            res = subprocess.run(
                [
                    "curl",
                    "--compressed",
                    "-sL",
                    "-m",
                    str(timeout),
                    "-w",
                    "\n__FINAL_URL__:%{url_effective}",
                    "-A",
                    USER_AGENT,
                    "-H",
                    "Accept: text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                    "-H",
                    "Accept-Language: nl-NL,nl;q=0.9,en;q=0.8",
                    target_url,
                ],
                capture_output=True,
                text=True,
                errors="ignore",
                timeout=timeout + 3,
            )
            out = res.stdout
            m_final = re.search(r"__FINAL_URL__:(\S+)\s*$", out)
            if m_final:
                resolved = m_final.group(1)
                if "myprivacy.dpgmedia" not in resolved:
                    shop_url = resolved
                out = out[: m_final.start()]
            shop_html = out
        except Exception:
            shop_html = ""

        # Check of de response een Tradedoubler / affiliate tussenpagina met embedded url(...) is
        m_td_html = re.search(r"url\((https?%3A%2F%2F[^)]+)\)", shop_html, re.I)
        if m_td_html:
            unwrapped = urllib.parse.unquote(m_td_html.group(1))
            shop_url = unwrapped
            try:
                res2 = subprocess.run(
                    [
                        "curl",
                        "--compressed",
                        "-sL",
                        "-m",
                        str(timeout),
                        "-w",
                        "\n__FINAL_URL__:%{url_effective}",
                        "-A",
                        USER_AGENT,
                        "-H",
                        "Accept-Language: nl-NL,nl;q=0.9,en;q=0.8",
                        unwrapped,
                    ],
                    capture_output=True,
                    text=True,
                    errors="ignore",
                    timeout=timeout + 3,
                )
                out2 = res2.stdout
                m_final2 = re.search(r"__FINAL_URL__:(\S+)\s*$", out2)
                if m_final2:
                    shop_url = m_final2.group(1)
                    out2 = out2[: m_final2.start()]
                if len(out2) > 500:
                    shop_html = out2
            except Exception:
                pass

        clean_url = clean_consumer_shop_url(shop_url)
        return clean_url, shop_html


def parse_price(price_str: str) -> float:
    """Zet een prijsstring (bijv. '€ 499,-', '499.95' of '€ 129,95') om naar een float."""
    if not price_str:
        return 0.0
    s = price_str.strip().replace(",-", ",00").replace(".-", ".00")
    s = re.sub(r"[^\d.,]", "", s)
    if not s:
        return 0.0
    if "," in s and "." in s:
        if s.rfind(",") > s.rfind("."):
            s = s.replace(".", "").replace(",", ".")
        else:
            s = s.replace(",", "")
    elif "," in s:
        s = s.replace(",", ".")
    try:
        return round(float(s), 2)
    except ValueError:
        return 0.0


def extract_live_price_from_shop_html(
    shop_html: str, expected_price: float
) -> tuple[float | None, float | None, str]:
    """
    Haalt de daadwerkelijke actuele PARTICULIERE consumentenprijs (INCL. 21% BTW) uit de HTML van de webshop
    (Coolblue, bol.com, Amazon.nl, MediaMarkt, Proshop, Electro World, CoolSound, Art & Craft, Philips, etc.).
    Negeert expliciet alle zakelijke prijzen (excludingVat / priceExcludingVat / excl. btw).
    Retourneert: (prijs_incl_btw, gedetecteerde_prijs_excl_btw, extra_promo_info)
    """
    if not shop_html or len(shop_html) < 500:
        return None, None, ""

    candidates_incl_vat: list[float] = []
    excluded_vat_prices: set[float] = set()
    promo_note = ""

    # 0. Detecteer en blokkeer expliciete zakelijke (excl. btw) prijzen (zoals op Coolblue: "excludingVat":197.52066)
    for m_excl in re.finditer(
        r'\\?"(?:excludingVat|priceExcludingVat|price_excl_vat|priceExVat)\\?"\s*:\s*\\?"?(\d+(?:[.,]\d+)?)\\?"?',
        shop_html,
    ):
        p_ex = parse_price(m_excl.group(1))
        if p_ex > 10:
            excluded_vat_prices.add(round(p_ex, 2))
            excluded_vat_prices.add(float(int(p_ex)))

    # Coolblue expliciete "salesPrice":{"includingVat":239,"excludingVat":197.52066}
    for m_cb in re.finditer(
        r'\\?"salesPrice\\?"\s*:\s*\{\s*\\?"includingVat\\?"\s*:\s*(\d+(?:[.,]\d+)?)\s*,\s*\\?"excludingVat\\?"\s*:\s*(\d+(?:[.,]\d+)?)',
        shop_html,
    ):
        p_in = parse_price(m_cb.group(1))
        p_ex = parse_price(m_cb.group(2))
        if p_ex > 10:
            excluded_vat_prices.add(round(p_ex, 2))
            excluded_vat_prices.add(float(int(p_ex)))
        if p_in > 15:
            candidates_incl_vat.append(p_in)

    # Check op eventuele cashback-promotielabels op de productpagina (bijv. Coolblue "promoLabel":{"label":"€ 40,- cashback"})
    m_promo = re.search(
        r'\\?"promoLabel\\?"\s*:\s*\{[^}]{0,200}?\\?"label\\?"\s*:\s*\\?"([^"\\]*cashback[^"\\]*)\\?"',
        shop_html,
        re.I,
    )
    if m_promo:
        promo_note = clean_html_text(m_promo.group(1))

    # 1. Amazon.nl specifieke hoofdprijs-blokken (corePriceDisplay / a-price, altijd incl. btw)
    if "amazon.nl" in shop_html or "a-price-whole" in shop_html:
        for m_amz in re.finditer(
            r'id="(?:corePriceDisplay_desktop_feature_div|corePrice_feature_div|apex_desktop)".*?(?:<span class="a-offscreen">\s*([^<]+)\s*</span>|class="a-price-whole">(\d+)<.*?class="a-price-fraction">(\d+)<)',
            shop_html,
            re.S,
        ):
            if m_amz.group(1):
                p = parse_price(m_amz.group(1))
                if p > 15 and p not in excluded_vat_prices:
                    return p, (min(excluded_vat_prices) if excluded_vat_prices else None), promo_note
            elif m_amz.group(2) and m_amz.group(3):
                p = parse_price(f"{m_amz.group(2)}.{m_amz.group(3)}")
                if p > 15 and p not in excluded_vat_prices:
                    return p, (min(excluded_vat_prices) if excluded_vat_prices else None), promo_note
        for m_wh in re.finditer(
            r'class="a-price-whole">(\d[\d.]*)<span[^>]*>[^<]*</span></span><span class="a-price-fraction">(\d+)</span>',
            shop_html,
        ):
            p = parse_price(f"{m_wh.group(1).replace('.', '')}.{m_wh.group(2)}")
            if p > 15 and p not in excluded_vat_prices:
                candidates_incl_vat.append(p)
        for m_off in re.finditer(
            r'<span class="a-offscreen">\s*(€\s*[\d.,]+|[\d.,]+\s*€)\s*</span>',
            shop_html,
        ):
            p = parse_price(m_off.group(1))
            if p > 15 and p not in excluded_vat_prices:
                candidates_incl_vat.append(p)

    # 2. Schema.org JSON-LD Offer prices (Coolblue, bol.com, MediaMarkt, Proshop, Electro World, Art & Craft, CoolSound)
    for m_offer in re.finditer(
        r'"@type"\s*:\s*"(?:Offer|AggregateOffer)"[^}]{0,600}?"(?:price|lowPrice)"\s*:\s*"?(\d+(?:[.,]\d+)?)"?',
        shop_html,
        re.S,
    ):
        p = parse_price(m_offer.group(1))
        if p > 15 and p not in excluded_vat_prices:
            candidates_incl_vat.append(p)

    for m_offer_esc in re.finditer(
        r'\\"@type\\"\s*:\s*\\"Offer\\"[^}]{0,600}?\\"price\\"\s*:\s*\\"?(\d+(?:[.,]\d+)?)\\"?',
        shop_html,
        re.S,
    ):
        p = parse_price(m_offer_esc.group(1))
        if p > 15 and p not in excluded_vat_prices:
            candidates_incl_vat.append(p)

    # 3. Meta tags (product:price:amount / og:price:amount / itemprop="price")
    for m_meta in re.finditer(
        r'(?:property="(?:product|og):price:amount"|itemprop="price")\s+content="(\d+(?:[.,]\d+)?)"|content="(\d+(?:[.,]\d+)?)"\s+(?:property="(?:product|og):price:amount"|itemprop="price")',
        shop_html,
        re.I,
    ):
        raw_p = m_meta.group(1) or m_meta.group(2)
        p = parse_price(raw_p)
        if p > 15 and p not in excluded_vat_prices:
            candidates_incl_vat.append(p)

    # 4. Algemene JSON-LD "price": "..." binnen Product (negeer alles wat op excludingVat lijkt)
    if not candidates_incl_vat:
        for m_p in re.finditer(
            r'"price"\s*:\s*"?(\d+(?:[.,]\d+)?)"?', shop_html
        ):
            p = parse_price(m_p.group(1))
            if 25.0 <= p <= 3000.0 and p not in excluded_vat_prices:
                candidates_incl_vat.append(p)

    if not candidates_incl_vat:
        return None, None, promo_note

    # Extra controle: als een kandidaat exact overeenkomt met expected_price / 1.21 (dus een excl.-btw bedrag is),
    # verwijder die kandidaat tenzij er geen incl.-btw prijs is
    filtered_incl = [
        p
        for p in candidates_incl_vat
        if not (
            expected_price > 0
            and abs(p * 1.21 - expected_price) <= 1.0
            and abs(p - expected_price) > 3.0
        )
    ]
    if filtered_incl:
        candidates_incl_vat = filtered_incl

    excl_detected = (
        round(expected_price / 1.21, 2)
        if any(abs(ex - round(expected_price / 1.21, 2)) <= 1.0 for ex in excluded_vat_prices)
        else (min(excluded_vat_prices) if excluded_vat_prices else None)
    )

    # Als de verwachte Tweakers-prijs (incl. btw, binnen € 1,00 marge) in de hoofd-kandidaten op de pagina staat, is hij exact bevestigd
    for c_price in candidates_incl_vat[:6]:
        if abs(c_price - expected_price) <= 1.0:
            return c_price, excl_detected, promo_note

    # Anders retourneren we de eerste gevonden hoofdproduct-prijs incl. btw op de pagina
    return candidates_incl_vat[0], excl_detected, promo_note


def parse_mm(val_str: str | None) -> float | None:
    if not val_str:
        return None
    m = re.search(r"(\d+(?:[.,]\d+)?)\s*mm", val_str, re.I)
    if not m:
        return None
    return float(m.group(1).replace(",", "."))


def clean_html_text(raw: str) -> str:
    text = re.sub(r"<[^>]+>", "", raw)
    text = html_lib.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def is_plausible_height_mm(height_mm: float | None, vacuum_type: str) -> bool:
    if height_mm is None:
        return False
    vtype_lower = vacuum_type.lower()
    if any(
        k in vtype_lower
        for k in (
            "slede",
            "trommel",
            "bouwstofzuiger",
            "matrasreiniger",
            "water",
        )
    ):
        return height_mm >= 120.0
    return height_mm >= 55.0


def load_deal_cache() -> dict[str, dict[str, Any]]:
    if CACHE_FILE.exists():
        try:
            return json.loads(CACHE_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def save_deal_cache(cache: dict[str, dict[str, Any]]) -> None:
    try:
        CACHE_FILE.write_text(
            json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except Exception:
        pass


def discover_candidates(
    client: TweakersClient,
    base_slug: str,
    height_filter_param: str,
    max_height_mm: float,
    bag_filter_param: str | None = None,
    bag_filter_value: str | None = None,
    min_rpd: float = 0.01,
    max_pages: int = 2,
) -> dict[str, dict[str, Any]]:
    candidates: dict[str, dict[str, Any]] = {}
    bag_query = (
        f"&{bag_filter_param}={bag_filter_value}"
        if bag_filter_param and bag_filter_value
        else ""
    )

    page = 1
    while page <= max_pages:
        url = (
            f"https://tweakers.net/{base_slug}/vergelijken/"
            f"?{height_filter_param}={max_height_mm:.1f}{bag_query}&minrpd={min_rpd}&page={page}"
        )
        html = client.fetch(url)
        items_found = _parse_vergelijken_items(html, candidates)
        if items_found == 0 or f"page={page + 1}" not in html:
            break
        page += 1

    deals_map: dict[str, dict[str, Any]] = {}
    for deal_page in range(1, 4):
        deal_url = f"https://tweakers.net/{base_slug}/deals/?page={deal_page}"
        deal_html = client.fetch(deal_url)
        count = _parse_deals_table(deal_html, deals_map)
        if count == 0 or f"page={deal_page + 1}" not in deal_html:
            break

    for pid, dinfo in deals_map.items():
        if pid in candidates:
            candidates[pid].update(
                {k: v for k, v in dinfo.items() if v is not None and v != ""}
            )

    return candidates


def _parse_vergelijken_items(
    html: str, out: dict[str, dict[str, Any]]
) -> int:
    ul_idx = html.find('<ul class="item-listing">')
    if ul_idx == -1:
        return 0
    listing_html = html[ul_idx:]
    li_blocks = re.findall(r"<li[^>]*>.*?</li>", listing_html, re.S)
    count = 0
    for li in li_blocks:
        m_data = re.search(r'data-productdata="([^"]+)"', li)
        if not m_data:
            continue
        try:
            pdata = json.loads(html_lib.unescape(m_data.group(1)))
            pid = str(pdata["id"])
            name = str(pdata["name"]).strip()
            url = str(pdata["url"]).strip()
        except Exception:
            continue

        m_price = re.search(r'<span class="amount">(.*?)</span>', li, re.S)
        curr_price = parse_price(m_price.group(1)) if m_price else 0.0

        m_badge = re.search(
            r'<span class="lowestPriceBadge">.*?<strong[^>]*>(.*?)</strong>',
            li,
            re.S,
        )
        badge_text = clean_html_text(m_badge.group(1)) if m_badge else ""

        m_spec = re.search(r'<p class="ellipsis specline">(.*?)</p>', li, re.S)
        specline = clean_html_text(m_spec.group(1)) if m_spec else ""

        m_score = re.search(r'<twk-star-rating data-score="([^"]+)"', li)
        rating = m_score.group(1) if m_score else ""

        out[pid] = {
            "product_id": pid,
            "name": name,
            "url": url,
            "current_price": curr_price,
            "before_price": None,
            "listed_discount_pct": _extract_pct(badge_text),
            "badge": badge_text,
            "specline": specline,
            "rating": rating,
        }
        count += 1
    return count


def _parse_deals_table(html: str, out: dict[str, dict[str, Any]]) -> int:
    rows = re.findall(r'<tr><td class="pwimage">.*?</tr>', html, re.S)
    count = 0
    for tr in rows:
        m_link = re.search(
            r'<a class="editionName" href="(https://tweakers\.net/pricewatch/(\d+)/[^"]+)"[^>]*>(.*?)</a>',
            tr,
            re.S,
        )
        if not m_link:
            continue
        url, pid, raw_name = m_link.group(1), m_link.group(2), m_link.group(3)
        name = clean_html_text(raw_name)

        m_before = re.search(r'<p class="beforePrice">.*?<s>(.*?)</s>', tr, re.S)
        before_price = parse_price(m_before.group(1)) if m_before else None

        m_price = re.search(r'<p class="price"><a[^>]*>(.*?)</a></p>', tr, re.S)
        curr_price = parse_price(m_price.group(1)) if m_price else 0.0

        m_disc = re.search(r'class="discount down"[^>]*>(.*?)</a>', tr, re.S)
        disc_pct = _extract_pct(m_disc.group(1)) if m_disc else 0.0

        m_badge = re.search(
            r'<span class="lowestPriceBadge"[^>]*>.*?<strong>(.*?)</strong>',
            tr,
            re.S,
        )
        badge_text = clean_html_text(m_badge.group(1)) if m_badge else ""

        m_spec = re.search(r'<p class="specline ellipsis">(.*?)</p>', tr, re.S)
        specline = clean_html_text(m_spec.group(1)) if m_spec else ""

        out[pid] = {
            "product_id": pid,
            "name": name,
            "url": url,
            "current_price": curr_price,
            "before_price": before_price,
            "listed_discount_pct": disc_pct,
            "badge": badge_text,
            "specline": specline,
        }
        count += 1
    return count


def _extract_pct(text: str) -> float:
    m = re.search(r"(\d+(?:[.,]\d+)?)\s*%", text)
    return float(m.group(1).replace(",", ".")) if m else 0.0


def extract_product_specs(html: str) -> dict[str, str]:
    specs: dict[str, str] = {}
    for m in re.finditer(
        r'<li class="(?:popular-)?spec-item">(.*?)</li>', html, re.S
    ):
        block = m.group(1)
        lbl = re.search(r'<span class="spec-label">(.*?)</span>', block, re.S)
        val = re.search(
            r'<span class="spec">(.*?)</span>\s*$', block.strip(), re.S
        )
        if lbl and val:
            k = clean_html_text(lbl.group(1))
            v_raw = val.group(1)
            if "fa-xmark" in v_raw:
                v = "Nee"
            elif "fa-check" in v_raw:
                v = "Ja"
            else:
                v = clean_html_text(v_raw)
            specs[k] = v
    return specs


def verify_shop_offers_on_page(
    client: TweakersClient, html_new_only: str, fallback_twk_price: float
) -> tuple[str, float, str, bool, str]:
    """
    Doorloopt de nieuwprijs-winkels op de Tweakers-productpagina, klikt daadwerkelijk door
    via de Tweakers clickout-link naar de externe webshop en controleert de echte prijs op de webshop zelf.
    Als winkel #1 een foute/verlopen prijs op Tweakers heeft (bijv. Proshop € 51,26 op Tweakers maar € 91,02 op de site),
    wordt automatisch ook de volgende winkel gecontroleerd en de goedkoopste echte prijs gekozen!
    """
    shop_Matches = list(
        re.finditer(
            r'<li[^>]*data-shop-id="[^"]+"[^>]*class="price-listing-item border-box-container"[^>]*>(.*?)</li>',
            html_new_only,
            re.S,
        )
    )
    if not shop_Matches:
        return ("Onbekend", fallback_twk_price, "", False, "Geen winkel-link")

    offers_checked: list[dict[str, Any]] = []
    discrepancy_notes: list[str] = []

    for idx, m in enumerate(shop_Matches[:3]):
        block = m.group(1)
        m_sname = re.search(
            r'<span class="shop-name">\s*<a[^>]*>(.*?)</a>', block, re.S
        )
        sname = clean_html_text(m_sname.group(1)) if m_sname else "Onbekend"

        m_sprice = re.search(
            r'<span class="shop-price">\s*<a[^>]*>(.*?)</a>', block, re.S
        )
        twk_shop_price = (
            parse_price(m_sprice.group(1))
            if m_sprice
            else fallback_twk_price
        )

        m_co = re.search(
            r'href="(https://tweakers\.net/pricewatch/clickout\?[^"]+)"', block
        )
        if not m_co:
            continue
        clickout_url = html_lib.unescape(m_co.group(1))

        shop_url, shop_html = client.follow_clickout_and_fetch_shop(clickout_url)
        live_price, excl_vat_price, promo_note = extract_live_price_from_shop_html(
            shop_html, twk_shop_price
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
                return (sname, live_price, shop_url, True, note)
            else:
                # De prijs op de webshop wijkt af van wat op Tweakers staat!
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
                }
            )
            # Als dit de eerste winkel was en niet afweek maar geen uitleesbare JSON-LD had, probeer nog 1 alternatief
            if idx == 0 and len(shop_Matches) == 1:
                break

    if offers_checked:
        # Kies het goedkoopste aanbod; geef voorkeur aan geverifieerde prijzen
        offers_checked.sort(key=lambda x: (x["price"], not x["verified"]))
        best = offers_checked[0]
        return (
            best["shop"],
            best["price"],
            best["url"],
            best["verified"],
            best["note"],
        )

    return ("Onbekend", fallback_twk_price, "", False, "Onbekend")


def enrich_and_verify_product(
    client: TweakersClient,
    cand: dict[str, Any],
    category_label: str,
    max_height_mm: float,
    bag_type: str = "bagged",
    max_station_height_mm: float | None = None,
    cache: dict[str, dict[str, Any]] | None = None,
) -> VacuumDeal | None:
    pid = str(cand["product_id"])
    url = cand["url"]

    if cache is not None and pid in cache:
        cached = cache[pid]
        h_mm = cached.get("height_mm")
        st_mm = cached.get("station_height_mm")
        vtype = str(cached.get("vacuum_type", ""))
        has_bag = bool(cached.get("has_bag", False))
        if bag_type == "bagged" and not has_bag:
            return None
        if bag_type == "bagless" and has_bag:
            return None
        if (
            h_mm is not None
            and float(h_mm) <= max_height_mm
            and is_plausible_height_mm(float(h_mm), vtype)
        ):
            if (
                max_station_height_mm is None
                or st_mm is None
                or float(st_mm) <= max_station_height_mm
            ):
                return VacuumDeal(**cached)
        return None

    try:
        html = client.fetch(url)
    except Exception:
        return None

    # 1. Alle specificaties uitlezen
    specs = extract_product_specs(html)

    zakloos_val = specs.get("Zakloos", "")
    has_bag = zakloos_val == "Nee"
    if bag_type == "bagged" and not has_bag:
        return None
    if bag_type == "bagless" and has_bag:
        return None

    height_mm = parse_mm(specs.get("Hoogte"))
    width_mm = parse_mm(specs.get("Breedte"))
    depth_mm = parse_mm(specs.get("Diepte"))
    station_height_mm = parse_mm(specs.get("Hoogte basisstation"))

    vacuum_type = (
        specs.get("Stofzuigermodel")
        or cand.get("specline", "").split(" met ")[0]
        or category_label
    )
    if category_label == "Robotstofzuiger" and "robot" not in vacuum_type.lower():
        if specs.get("Dweilrobot") and specs.get("Dweilrobot") != "Geen dweilfunctie":
            vacuum_type = "Robotstofzuiger met dweil"
        else:
            vacuum_type = "Robotstofzuiger"

    if (
        height_mm is None
        or height_mm > max_height_mm
        or not is_plausible_height_mm(height_mm, vacuum_type)
    ):
        return None

    if (
        max_station_height_mm is not None
        and station_height_mm is not None
        and station_height_mm > max_station_height_mm
    ):
        return None

    height_cm = round(height_mm / 10.0, 2)
    station_height_cm = (
        round(station_height_mm / 10.0, 2)
        if station_height_mm is not None
        else None
    )

    dims_parts = [f"H: {height_cm:g} cm"]
    if width_mm:
        dims_parts.append(f"B: {round(width_mm / 10.0, 1):g} cm")
    if depth_mm:
        dims_parts.append(f"D: {round(depth_mm / 10.0, 1):g} cm")
    if station_height_cm:
        dims_parts.append(f"Dock H: {station_height_cm:g} cm")
    dimensions_str = " × ".join(dims_parts)

    bag_capacity = (
        specs.get("Inhoud")
        or specs.get("Stofcapaciteit (basisstation)")
        or specs.get("Stofcapaciteit (stofzuiger)")
        or "-"
    )
    action_radius = specs.get("Actieradius") or specs.get("Snoerlengte") or "-"

    # 2. Verwijder 'Refurbished / Tweedekans'-blok en klik daadwerkelijk door naar de webshop!
    html_new_only = re.sub(
        r'<details class="refurbished-listing">.*?</details>',
        "",
        html,
        flags=re.S,
    )
    twk_listed_price = float(cand.get("current_price") or 0.0)
    (
        cheapest_shop,
        verified_price,
        shop_url,
        is_verified,
        verification_note,
    ) = verify_shop_offers_on_page(client, html_new_only, twk_listed_price)

    curr_price = verified_price if verified_price > 0 else twk_listed_price
    if curr_price <= 0:
        return None

    # 3. Jaargemiddelde (exclusief de laatste 2 maanden!) berekenen via Tweakers' AJAX price_chart
    avg_year_excl_2m: float | None = None
    peak_last_2m: float | None = None
    prev_30d: float | None = cand.get("before_price")
    low_year: float | None = None

    try:
        chart_raw = client.fetch(
            f"https://tweakers.net/ajax/price_chart/{pid}/nl/"
        )
        chart_data = json.loads(chart_raw)
        source = chart_data.get("dataset", {}).get("source", [])

        dated_prices: list[tuple[date, float]] = []
        for row in source:
            if (
                isinstance(row, list)
                and len(row) >= 2
                and row[0]
                and row[1] is not None
                and float(row[1]) > 0
            ):
                try:
                    d_obj = date.fromisoformat(str(row[0])[:10])
                    dated_prices.append((d_obj, float(row[1])))
                except Exception:
                    continue

        if dated_prices:
            latest_date = dated_prices[-1][0]
            cutoff_2m = latest_date - timedelta(days=60)
            cutoff_1y = latest_date - timedelta(days=365)

            year_all_prices = [
                p for d_obj, p in dated_prices if d_obj >= cutoff_1y
            ]
            if year_all_prices:
                low_year = round(min(year_all_prices), 2)

            # Het jaargemiddelde EXCLUSIEF de laatste 2 maanden (dag -365 t/m dag -60)
            year_excl_2m_prices = [
                p
                for d_obj, p in dated_prices
                if cutoff_1y <= d_obj <= cutoff_2m
            ]
            if year_excl_2m_prices:
                avg_year_excl_2m = round(
                    statistics.mean(year_excl_2m_prices), 2
                )
            else:
                # Als het product korter dan 60 dagen op de markt is, pak de eerste helft van de historie
                half = max(1, len(dated_prices) // 2)
                avg_year_excl_2m = round(
                    statistics.mean(p for _, p in dated_prices[:half]), 2
                )

            # Piekprijs in de laatste 2 maanden (om te zien hoeveel de prijs vlak voor Black Friday is opgepompt)
            last_2m_prices = [
                p for d_obj, p in dated_prices if d_obj > cutoff_2m
            ]
            if last_2m_prices:
                peak_last_2m = round(max(last_2m_prices), 2)

            if prev_30d is None:
                if len(dated_prices) >= 31:
                    prev_30d = round(dated_prices[-31][1], 2)
                else:
                    prev_30d = round(dated_prices[0][1], 2)
    except Exception:
        pass

    # Echte korting (%) en besparing (€) t.o.v. het JAARGEMIDDELDE (exclusief de laatste 2 maanden!)
    if avg_year_excl_2m and avg_year_excl_2m > 0:
        real_discount_vs_year_pct = round(
            ((avg_year_excl_2m - curr_price) / avg_year_excl_2m) * 100.0, 1
        )
        real_savings_vs_year_eur = round(avg_year_excl_2m - curr_price, 2)
    else:
        real_discount_vs_year_pct = 0.0
        real_savings_vs_year_eur = 0.0

    # Schijnkorting t.o.v. de 2-maanden piek (hoeveel korting winkels beweren te geven)
    if peak_last_2m and peak_last_2m > curr_price:
        fake_discount_vs_2m_peak_pct = round(
            ((peak_last_2m - curr_price) / peak_last_2m) * 100.0, 1
        )
    else:
        fake_discount_vs_2m_peak_pct = 0.0

    is_at_year_low = low_year is not None and curr_price <= low_year * 1.02

    # Beoordeling op basis van het JAARGEMIDDELDE (excl. laatste 2 maanden)
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

    # Sorteerscore puur gebaseerd op de ECHTE korting t.o.v. het jaargemiddelde (excl. 2m) + winkelverificatie
    deal_score = round(
        real_discount_vs_year_pct
        + (3.0 if is_at_year_low else 0.0)
        + (2.0 if is_verified else 0.0),
        1,
    )

    power_or_suction = (
        specs.get("Zuigkracht (Pa)")
        or specs.get("Vermogen (watt)")
        or specs.get("Zuigkracht")
        or "-"
    )
    noise_level = specs.get("Geluidssterkte (max.)", "-")

    rating_str = cand.get("rating") or "-"
    m_rev = re.search(
        r'<twk-star-rating data-score="([^"]+)"(?:\s+title="([^"]+)")?', html
    )
    if m_rev and m_rev.group(1) != "0 / 5":
        rating_str = m_rev.group(1)

    deal_obj = VacuumDeal(
        product_id=pid,
        name=cand["name"],
        category=category_label,
        vacuum_type=vacuum_type,
        has_bag=has_bag,
        bag_capacity=bag_capacity,
        action_radius=action_radius,
        height_mm=height_mm,
        height_cm=height_cm,
        station_height_mm=station_height_mm,
        station_height_cm=station_height_cm,
        dimensions_str=dimensions_str,
        tweakers_listed_price=twk_listed_price,
        current_price=curr_price,
        shop_price_verified=is_verified,
        verification_note=verification_note,
        avg_year_excl_2m=avg_year_excl_2m,
        peak_last_2m=peak_last_2m,
        previous_price_30d=prev_30d,
        lowest_price_year=low_year,
        real_discount_vs_year_pct=real_discount_vs_year_pct,
        real_savings_vs_year_eur=real_savings_vs_year_eur,
        fake_discount_vs_2m_peak_pct=fake_discount_vs_2m_peak_pct,
        deal_verdict=verdict,
        deal_score=deal_score,
        cheapest_shop=cheapest_shop,
        shop_url=shop_url,
        power_or_suction=power_or_suction,
        noise_level=noise_level,
        rating=rating_str,
        url=url,
    )
    if cache is not None:
        cache[pid] = asdict(deal_obj)
    return deal_obj


def find_tweakers_vacuum_deals(
    max_height_cm: float = 25.0,
    min_discount_pct: float = 0.0,
    category: str = "all",
    bag_type: str = "bagged",
    max_station_height_cm: float | None = None,
    max_workers: int = 4,
    use_cache: bool = True,
) -> list[VacuumDeal]:
    max_height_mm = max_height_cm * 10.0
    max_station_mm = (
        max_station_height_cm * 10.0
        if max_station_height_cm is not None
        else None
    )

    bag_val = (
        "6403"
        if bag_type == "bagged"
        else ("6402" if bag_type == "bagless" else None)
    )
    bag_desc = (
        "met stofzak"
        if bag_type == "bagged"
        else ("zakloos" if bag_type == "bagless" else "met & zonder zak")
    )

    cache = load_deal_cache() if use_cache else {}
    client = TweakersClient(min_interval_sec=0.14)
    print("🌐 Verbinden met Tweakers Pricewatch & Deals...")
    client.warmup()

    tasks_meta: list[tuple[dict[str, Any], str]] = []

    if category in ("all", "regular"):
        print(
            f"🔍 Reguliere stofzuigers ({bag_desc}) zoeken met hoogte <= {max_height_cm:g} cm ({max_height_mm:g} mm)..."
        )
        reg_cands = discover_candidates(
            client,
            base_slug="stofzuigers",
            height_filter_param="a6545",
            max_height_mm=max_height_mm,
            bag_filter_param="d2139" if bag_val else None,
            bag_filter_value=bag_val,
            min_rpd=0.05,
            max_pages=2,
        )
        print(f"   ↳ {len(reg_cands)} kandidaat-deals gevonden.")
        for c in reg_cands.values():
            tasks_meta.append((c, "Reguliere stofzuiger"))

    if category in ("all", "robot"):
        print(
            f"🤖 Robotstofzuigers ({bag_desc} in basisstation) zoeken met hoogte <= {max_height_cm:g} cm ({max_height_mm:g} mm)..."
        )
        rob_cands = discover_candidates(
            client,
            base_slug="robotstofzuigers",
            height_filter_param="a9864",
            max_height_mm=max_height_mm,
            bag_filter_param="d9810" if bag_val else None,
            bag_filter_value=bag_val,
            min_rpd=0.15,
            max_pages=1,
        )
        sorted_rob = sorted(
            rob_cands.values(),
            key=lambda x: float(x.get("listed_discount_pct") or 0.0),
            reverse=True,
        )[:18]
        print(
            f"   ↳ {len(sorted_rob)} beste robotstofzuiger-kandidaten geselecteerd."
        )
        for c in sorted_rob:
            tasks_meta.append((c, "Robotstofzuiger"))

    print(
        f"📊 Voor {len(tasks_meta)} producten: doorklikken naar de webshop voor live prijsverificatie + jaargemiddelde (excl. laatste 2 mnd) berekenen..."
    )

    verified_deals: list[VacuumDeal] = []
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        future_map = {
            pool.submit(
                enrich_and_verify_product,
                client,
                cand,
                cat_label,
                max_height_mm,
                bag_type,
                max_station_mm,
                cache,
            ): cand["name"]
            for cand, cat_label in tasks_meta
        }
        for fut in as_completed(future_map):
            deal = fut.result()
            if deal is not None:
                verified_deals.append(deal)

    if use_cache:
        save_deal_cache(cache)

    verified_deals.sort(
        key=lambda d: (
            d.deal_score,
            d.real_discount_vs_year_pct,
            d.real_savings_vs_year_eur,
        ),
        reverse=True,
    )
    return verified_deals


def _fmt_eur(val: float | None) -> str:
    if val is None:
        return "-"
    return (
        f"€ {val:,.2f}"
        .replace(",", "X")
        .replace(".", ",")
        .replace("X", ".")
        .replace(",00", ",-")
    )


def format_deals_table(deals: list[VacuumDeal], limit: int = 25) -> str:
    if not deals:
        return "Geen deals gevonden die aan alle criteria voldoen."

    headers = [
        "#",
        "Product",
        "Zak",
        "Hoogte",
        "Echte Prijs",
        "Jaargem (excl 2m)",
        "Piek (2m)",
        "Echte Korting vs Jaar",
        "Winkel (Live Check)",
        "Oordeel (vs Jaargem. excl. 2m)",
    ]
    rows: list[list[str]] = []
    for idx, d in enumerate(deals[:limit], start=1):
        h_str = f"{d.height_cm:g} cm"
        if d.station_height_cm:
            h_str += f" (dock {d.station_height_cm:g}cm)"
        sign = "-" if d.real_discount_vs_year_pct >= 0 else "+"
        sav_sign = "-" if d.real_savings_vs_year_eur >= 0 else "+"
        real_disc_str = f"{sign}{abs(d.real_discount_vs_year_pct):g}% ({sav_sign}€{abs(d.real_savings_vs_year_eur):g})"
        shop_chk = f"{'✅' if d.shop_price_verified else '🔗'} {d.cheapest_shop[:15]}"
        if abs(d.current_price - d.tweakers_listed_price) > 1.50:
            shop_chk += f" (Twk: €{d.tweakers_listed_price:g})"
        rows.append(
            [
                str(idx),
                d.name[:32] + ("…" if len(d.name) > 32 else ""),
                d.bag_capacity[:6],
                h_str,
                _fmt_eur(d.current_price),
                _fmt_eur(d.avg_year_excl_2m),
                _fmt_eur(d.peak_last_2m),
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


def export_results(
    deals: list[VacuumDeal],
    csv_path: str = "tweakers_stofzuiger_deals.csv",
    md_path: str = "tweakers_stofzuiger_deals.md",
    json_path: str = "tweakers_stofzuiger_deals.json",
) -> None:
    fieldnames = [
        "product_id",
        "category",
        "name",
        "vacuum_type",
        "has_bag",
        "bag_capacity",
        "action_radius",
        "height_cm",
        "height_mm",
        "station_height_cm",
        "dimensions_str",
        "current_price",
        "tweakers_listed_price",
        "shop_price_verified",
        "verification_note",
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
        "power_or_suction",
        "noise_level",
        "rating",
        "url",
    ]
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for d in deals:
            writer.writerow(asdict(d))

    with open(json_path, "w", encoding="utf-8") as f:
        json.dump([asdict(d) for d in deals], f, ensure_ascii=False, indent=2)

    reg_deals = [d for d in deals if d.category == "Reguliere stofzuiger"]
    rob_deals = [d for d in deals if d.category == "Robotstofzuiger"]

    md_lines = [
        "# Tweakers Black Friday Deals: Stofzuigers met Zak (Hoogte <= 25 cm)",
        "",
        "- **100% Particuliere Consumentenprijzen (incl. 21% btw)**: Zakelijke prijzen (`excl. btw`, zoals `excludingVat: 197.52` bij Coolblue op een prijs van `€ 239,- incl. btw`) én zakelijke/affiliate tracking-parameters worden expliciet uitgefilterd.",
        "- **Echte Korting-berekening**: Vergeleken met het **jaargemiddelde (dag -365 t/m dag -60)**, dus **exclusief de laatste 2 maanden** waarin winkels prijzen vaak kunstmatig verhogen vóór Black Friday.",
        "- **Live Winkel-verificatie**: Elke prijs is gecontroleerd door daadwerkelijk op de Tweakers clickout-link te klikken en de consumentenprijs (incl. btw) op de winkelpagina zelf uit te lezen.",
        "",
    ]

    for section_title, subset in [
        (
            "1. Reguliere Stofzuigers met Zak (Slede / Trommel, <= 25 cm)",
            reg_deals,
        ),
        (
            "2. Robotstofzuigers met Stofzak in Basisstation (Robot <= 25 cm)",
            rob_deals,
        ),
    ]:
        if not subset:
            continue
        md_lines.append(f"## {section_title}")
        md_lines.append("")
        md_lines.append(
            "| # | Product (Tweakers) | Zak | Afmetingen | Echte Prijs (Winkel) | Jaargem. (excl. 2m) | Piek laatste 2m | Echte Korting vs Jaar | Winkel & Verificatie | Oordeel |"
        )
        md_lines.append(
            "|---|--------------------|-----|------------|----------------------|---------------------|-----------------|-----------------------|----------------------|---------|"
        )
        for idx, d in enumerate(subset, start=1):
            sign = "-" if d.real_discount_vs_year_pct >= 0 else "+"
            sav_sign = "-" if d.real_savings_vs_year_eur >= 0 else "+"
            shop_link = (
                f"[{d.cheapest_shop}]({d.shop_url})"
                if d.shop_url
                else d.cheapest_shop
            )
            md_lines.append(
                f"| {idx} | [{d.name}]({d.url}) | {d.bag_capacity} | {d.dimensions_str} "
                f"| **{_fmt_eur(d.current_price)}** | {_fmt_eur(d.avg_year_excl_2m)} | {_fmt_eur(d.peak_last_2m)} "
                f"| **{sign}{abs(d.real_discount_vs_year_pct):g}%** ({sav_sign}€ {abs(d.real_savings_vs_year_eur):g}) "
                f"| {shop_link} ({d.verification_note}) | {d.deal_verdict} |"
            )
        md_lines.append("")

    with open(md_path, "w", encoding="utf-8") as f:
        f.write("\n".join(md_lines))


def cli_main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Zoek en verifieer de beste Tweakers Black Friday stofzuiger-deals met stofzak en maximale hoogte."
    )
    parser.add_argument(
        "--max-height",
        type=float,
        default=25.0,
        help="Maximale hoogte van de stofzuiger in cm (standaard: 25.0)",
    )
    parser.add_argument(
        "--bag-type",
        choices=["bagged", "bagless", "all"],
        default="bagged",
        help="Filter op stofzak: bagged (met zak, standaard), bagless (zakloos) of all",
    )
    parser.add_argument(
        "--max-station-height",
        type=float,
        default=None,
        help="Optioneel: maximale hoogte van het basisstation (voor robotstofzuigers) in cm",
    )
    parser.add_argument(
        "--min-discount",
        type=float,
        default=0.0,
        help="Minimale korting t.o.v. jaargemiddelde in procenten (standaard: 0.0 zodat ook ontmaskerde nepkortingen zichtbaar zijn)",
    )
    parser.add_argument(
        "--category",
        choices=["all", "regular", "robot"],
        default="all",
        help="Welke categorie stofzuigers te doorzoeken: all, regular of robot (standaard: all)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=25,
        help="Aantal resultaten per categorie in de tabelweergave (standaard: 25)",
    )
    parser.add_argument(
        "--no-cache",
        action="store_true",
        help="Negeer lokale cache en haal alle product- en winkelpagina's opnieuw op",
    )
    args = parser.parse_args(argv)

    deals = find_tweakers_vacuum_deals(
        max_height_cm=args.max_height,
        min_discount_pct=args.min_discount,
        category=args.category,
        bag_type=args.bag_type,
        max_station_height_cm=args.max_station_height,
        use_cache=not args.no_cache,
    )

    reg_deals = [d for d in deals if d.category == "Reguliere stofzuiger"]
    rob_deals = [d for d in deals if d.category == "Robotstofzuiger"]

    bag_label = (
        "MET STOFZAK"
        if args.bag_type == "bagged"
        else ("ZAKLOOS" if args.bag_type == "bagless" else "ALLE")
    )

    if reg_deals:
        print(
            f"\n🏆 1. REGULIERE STOFZUIGER DEALS {bag_label} (Hoogte <= {args.max_height:g} cm) — KORTING VS JAARGEMIDDELDE (EXCL. LAATSTE 2 MND)"
        )
        print("=" * 145)
        print(format_deals_table(reg_deals, limit=args.limit))

    if rob_deals:
        print(
            f"\n🤖 2. ROBOTSTOFZUIGER DEALS {bag_label} IN BASISSTATION (Hoogte robot <= {args.max_height:g} cm) — KORTING VS JAARGEMIDDELDE (EXCL. 2 MND)"
        )
        print("=" * 145)
        print(format_deals_table(rob_deals, limit=args.limit))

    export_results(deals)
    print(
        "\n💾 Volledige geverifieerde resultaten opgeslagen in:\n"
        "   • tweakers_stofzuiger_deals.csv\n"
        "   • tweakers_stofzuiger_deals.md\n"
        "   • tweakers_stofzuiger_deals.json"
    )
    return 0


if __name__ == "__main__":
    sys.exit(cli_main())
