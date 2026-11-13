from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class CategoryConfig:
    slug: str
    label: str
    group: str
    aliases: tuple[str, ...] = ()
    default_min_rpd: float = 0.05
    # Bekende URL-filterparameters op Tweakers /<slug>/vergelijken/
    url_filter_params: dict[str, str] = field(default_factory=dict)


CATEGORIES: tuple[CategoryConfig, ...] = (
    # Huishouden & Keuken
    CategoryConfig(
        slug="stofzuigers",
        label="Stofzuigers",
        group="Huishouden",
        aliases=("stofzuiger", "vacuum", "vacuums", "sledestofzuiger", "steelstofzuiger"),
        default_min_rpd=0.05,
        url_filter_params={
            "max_height_mm": "a6545",
            "bagged": "d2139=6403",
            "bagless": "d2139=6402",
        },
    ),
    CategoryConfig(
        slug="robotstofzuigers",
        label="Robotstofzuigers",
        group="Huishouden",
        aliases=("robotstofzuiger", "robot", "robotvacuum", "dweilrobot"),
        default_min_rpd=0.12,
        url_filter_params={
            "max_height_mm": "a9864",
            "bagged": "d9810=6403",
            "bagless": "d9810=6402",
        },
    ),
    CategoryConfig(
        slug="wasmachines",
        label="Wasmachines",
        group="Huishouden",
        aliases=("wasmachine", "washingmachine"),
    ),
    CategoryConfig(
        slug="wasdrogers",
        label="Wasdrogers",
        group="Huishouden",
        aliases=("wasdroger", "droger", "warmtepompdroger"),
    ),
    CategoryConfig(
        slug="vaatwassers",
        label="Vaatwassers",
        group="Huishouden",
        aliases=("vaatwasser", "afwasmachine", "dishwasher"),
    ),
    CategoryConfig(
        slug="koelkasten",
        label="Koelkasten",
        group="Huishouden",
        aliases=("koelkast", "koelvriescombinatie", "fridge"),
    ),
    CategoryConfig(
        slug="vriezers",
        label="Vriezers",
        group="Huishouden",
        aliases=("vriezer", "diepvries"),
    ),
    CategoryConfig(
        slug="airfryers",
        label="Airfryers & Friteuses",
        group="Keuken",
        aliases=("airfryer", "friteuse", "heteluchtfriteuse"),
    ),
    CategoryConfig(
        slug="espressomachines",
        label="Espressomachines & Koffie",
        group="Keuken",
        aliases=("espressomachine", "koffiemachine", "koffiezetapparaat", "volautomaat", "koffie"),
    ),
    CategoryConfig(
        slug="magnetrons",
        label="Magnetrons & Combi-ovens",
        group="Keuken",
        aliases=("magnetron", "combimagnetron", "oven"),
    ),
    # Beeld & Geluid
    CategoryConfig(
        slug="televisies",
        label="Televisies",
        group="Beeld & Geluid",
        aliases=("tv", "tvs", "televisie", "oled", "qled"),
    ),
    CategoryConfig(
        slug="monitoren",
        label="Monitoren",
        group="Beeld & Geluid",
        aliases=("monitor", "monitors", "beeldscherm", "gamingmonitor"),
    ),
    CategoryConfig(
        slug="koptelefoons",
        label="Koptelefoons & Oordopjes",
        group="Beeld & Geluid",
        aliases=("koptelefoon", "headphone", "headphones", "oordopjes", "earbuds", "headset"),
    ),
    CategoryConfig(
        slug="soundbars",
        label="Soundbars",
        group="Beeld & Geluid",
        aliases=("soundbar", "thuisbioscoop"),
    ),
    CategoryConfig(
        slug="speakers",
        label="Speakers",
        group="Beeld & Geluid",
        aliases=("speaker", "luidspreker", "bluetoothspeaker"),
    ),
    # Computers & Componenten
    CategoryConfig(
        slug="laptops",
        label="Laptops",
        group="Computers & Tablets",
        aliases=("laptop", "notebook", "macbook", "chromebook"),
    ),
    CategoryConfig(
        slug="tablets",
        label="Tablets",
        group="Computers & Tablets",
        aliases=("tablet", "ipad"),
    ),
    CategoryConfig(
        slug="videokaarten",
        label="Videokaarten (GPU's)",
        group="Componenten",
        aliases=("videokaart", "gpu", "gpus", "grafischekaart", "rtx", "radeon"),
    ),
    CategoryConfig(
        slug="processors",
        label="Processors (CPU's)",
        group="Componenten",
        aliases=("processor", "cpu", "cpus", "ryzen", "core"),
    ),
    CategoryConfig(
        slug="moederborden",
        label="Moederborden",
        group="Componenten",
        aliases=("moederbord", "motherboard", "mainboard"),
    ),
    CategoryConfig(
        slug="geheugen-intern",
        label="Intern geheugen (RAM)",
        group="Componenten",
        aliases=("ram", "geheugen", "ddr4", "ddr5", "werkgeheugen"),
    ),
    CategoryConfig(
        slug="solid-state-drives",
        label="SSD's (Solid State Drives)",
        group="Componenten",
        aliases=("ssd", "ssds", "nvme", "m2"),
    ),
    CategoryConfig(
        slug="interne-harde-schijven",
        label="Harde schijven (HDD)",
        group="Componenten",
        aliases=("hdd", "hardeschijf"),
    ),
    CategoryConfig(
        slug="voedingen",
        label="PC Voedingen (PSU)",
        group="Componenten",
        aliases=("voeding", "psu"),
    ),
    CategoryConfig(
        slug="behuizingen",
        label="PC Behuizingen",
        group="Componenten",
        aliases=("behuizing", "pccase", "kast"),
    ),
    # Mobiel, Gaming & Randapparatuur
    CategoryConfig(
        slug="smartphones",
        label="Smartphones",
        group="Mobiel & Wearables",
        aliases=("smartphone", "telefoon", "mobiel", "iphone", "galaxy"),
    ),
    CategoryConfig(
        slug="smartwatches",
        label="Smartwatches & Sporthorloges",
        group="Mobiel & Wearables",
        aliases=("smartwatch", "sporthorloge", "horloge", "applewatch", "garmin"),
    ),
    CategoryConfig(
        slug="consoles",
        label="Gameconsoles",
        group="Gaming",
        aliases=("console", "gameconsole", "ps5", "playstation", "xbox", "switch"),
    ),
    CategoryConfig(
        slug="toetsenborden",
        label="Toetsenborden",
        group="Randapparatuur",
        aliases=("toetsenbord", "keyboard", "keyboards"),
    ),
    CategoryConfig(
        slug="muizen",
        label="Muizen",
        group="Randapparatuur",
        aliases=("muis", "mouse", "gamingmuis"),
    ),
    CategoryConfig(
        slug="printers",
        label="Printers & Scanners",
        group="Randapparatuur",
        aliases=("printer", "allinoneprinter", "laserprinter"),
    ),
    # Netwerk, Opslag & Smart Home
    CategoryConfig(
        slug="routers",
        label="Routers & Mesh-wifi",
        group="Netwerk & Smart Home",
        aliases=("router", "wifi", "mesh", "accesspoint"),
    ),
    CategoryConfig(
        slug="nas",
        label="NAS (Netwerkopslag)",
        group="Netwerk & Smart Home",
        aliases=("netwerkopslag", "synology", "qnap"),
    ),
    CategoryConfig(
        slug="beveiligingscameras",
        label="Beveiligingscamera's & Videodeurbellen",
        group="Netwerk & Smart Home",
        aliases=("beveiligingscamera", "ipcamera", "deurbel", "videodeurbel"),
    ),
    CategoryConfig(
        slug="thuisbatterijen",
        label="Thuisbatterijen & Powerstations",
        group="Energie",
        aliases=("thuisbatterij", "powerstation", "accu"),
    ),
)

_ALIAS_MAP: dict[str, CategoryConfig] = {}
for _cat in CATEGORIES:
    _ALIAS_MAP[_cat.slug.lower()] = _cat
    _ALIAS_MAP[_cat.label.lower()] = _cat
    for _al in _cat.aliases:
        _ALIAS_MAP[_al.lower().replace("-", "").replace(" ", "")] = _cat
        _ALIAS_MAP[_al.lower()] = _cat


def resolve_category_config(name_or_slug: str) -> CategoryConfig:
    """
    Zoekt een CategoryConfig op basis van naam, alias of Tweakers-slug.
    Als de categorie niet in de vaste lijst staat, maakt hij dynamisch een CategoryConfig aan
    op basis van de opgegeven Tweakers-slug (zodat élke Tweakers-categorie werkt!).
    """
    cleaned = name_or_slug.strip().lower().strip("/")
    if cleaned.startswith("https://tweakers.net/"):
        parts = [
            p
            for p in cleaned.replace("https://tweakers.net/", "").split("/")
            if p
        ]
        if parts:
            cleaned = parts[0]

    key_compact = cleaned.replace("-", "").replace(" ", "")
    if cleaned in _ALIAS_MAP:
        return _ALIAS_MAP[cleaned]
    if key_compact in _ALIAS_MAP:
        return _ALIAS_MAP[key_compact]

    slug = cleaned.replace(" ", "-")
    return CategoryConfig(
        slug=slug,
        label=slug.replace("-", " ").title(),
        group="Overig (Tweakers)",
        aliases=(slug,),
        default_min_rpd=0.05,
    )


def list_categories_by_group() -> dict[str, list[CategoryConfig]]:
    grouped: dict[str, list[CategoryConfig]] = {}
    for cat in CATEGORIES:
        grouped.setdefault(cat.group, []).append(cat)
    return grouped
