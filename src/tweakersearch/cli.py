from __future__ import annotations

import argparse
import sys

from tweakersearch.bot import (
    BotCommandHandler,
    run_cli_repl,
    run_telegram_polling,
)
from tweakersearch.formatters import (
    export_product_deals,
    format_categories_overview,
    format_cli_table,
    format_telegram_deals_list,
    format_telegram_product_card,
)
from tweakersearch.scraper import cli_main as legacy_vacuum_cli_main
from tweakersearch.service import TweakersDealService

SUBCOMMANDS = {"deals", "search", "check", "history", "categories", "bot", "vacuums"}


def _parse_spec_list(spec_args: list[str] | None) -> dict[str, str] | None:
    if not spec_args:
        return None
    out: dict[str, str] = {}
    for item in spec_args:
        if "=" in item:
            k, v = item.split("=", 1)
            out[k.strip()] = v.strip()
    return out or None


def app_main(argv: list[str] | None = None) -> int:
    args_list = list(sys.argv[1:] if argv is None else argv)

    # Backward compatibility: als er geen subcommando wordt gebruikt (of oude vlaggen zoals --max-height),
    # draai het vertrouwde stofzuiger-overzicht of toon help bij -h/--help.
    if not args_list:
        return legacy_vacuum_cli_main([])
    if args_list[0] not in SUBCOMMANDS and args_list[0] not in ("-h", "--help"):
        return legacy_vacuum_cli_main(args_list)

    parser = argparse.ArgumentParser(
        prog="tweakersearch",
        description=(
            "Universele Tweakers Black Friday Deal & Anti-Scam App (voor élk apparaat op Tweakers of met bekende prijshistorie). "
            "Berekent echte korting t.o.v. het jaargemiddelde (excl. laatste 2 maanden) en verifieert live de consumentenprijs (incl. 21% btw) op de webshop."
        ),
    )
    subparsers = parser.add_subparsers(dest="subcommand", required=True)

    # 1. Subcommand: deals
    p_deals = subparsers.add_parser(
        "deals",
        help="Zoek en verifieer de beste deals in élke Tweakers-categorie (bijv. videokaarten, televisies, monitoren, laptops, stofzuigers, airfryers).",
    )
    p_deals.add_argument(
        "category",
        help="Tweakers-categorie of alias (bijv. 'videokaarten', 'gpu', 'televisies', 'monitoren', 'laptops', 'stofzuigers', 'koptelefoons', 'ssd')",
    )
    p_deals.add_argument(
        "-q", "--query", default=None, help="Optionele zoekterm binnen de categorie (bijv. 'oled', 'rtx 5070', 'miele')"
    )
    p_deals.add_argument(
        "--max-price", type=float, default=None, help="Maximale prijs incl. btw in euro's"
    )
    p_deals.add_argument(
        "--min-discount",
        type=float,
        default=None,
        help="Minimale echte korting (%) t.o.v. het jaargemiddelde (excl. laatste 2 mnd)",
    )
    p_deals.add_argument(
        "--max-height",
        type=float,
        default=None,
        help="Maximale hoogte in cm (bijv. 25 voor stofzuigers)",
    )
    p_deals.add_argument(
        "--bag-type",
        choices=["bagged", "bagless"],
        default=None,
        help="Filter op stofzak (bagged = met zak, bagless = zakloos)",
    )
    p_deals.add_argument(
        "--spec",
        action="append",
        help="Filter op elke gewenste Tweakers-specificatie in het formaat 'Naam=Waarde' (kan meerdere keren worden opgegeven)",
    )
    p_deals.add_argument(
        "--limit", type=int, default=20, help="Aantal te tonen deals (standaard: 20)"
    )
    p_deals.add_argument(
        "--candidates",
        type=int,
        default=25,
        help="Maximaal aantal kandidaat-producten om live bij de winkels te verifiëren (standaard: 25)",
    )
    p_deals.add_argument(
        "--telegram",
        action="store_true",
        help="Toon de uitvoer in compact Telegram/chat-formaat in plaats van een brede tabel",
    )
    p_deals.add_argument(
        "--no-cache",
        action="store_true",
        help="Negeer lokale cache en haal alle pagina's live opnieuw op",
    )

    # 2. Subcommand: search
    p_search = subparsers.add_parser(
        "search",
        help="Zoek op vrije tekst naar elk apparaat op Tweakers (bijv. 'Sony WH-1000XM5', 'RTX 5080', 'LG OLED C5').",
    )
    p_search.add_argument("query", help="Zoekterm voor het product of apparaat")
    p_search.add_argument("--max-price", type=float, default=None)
    p_search.add_argument("--min-discount", type=float, default=None)
    p_search.add_argument("--spec", action="append")
    p_search.add_argument("--limit", type=int, default=15)
    p_search.add_argument("--telegram", action="store_true")
    p_search.add_argument("--no-cache", action="store_true")

    # 3. Subcommand: check
    p_check = subparsers.add_parser(
        "check",
        help="Controleer één specifiek Tweakers-product via URL of Pricewatch-ID (live winkelcheck incl. btw + anti-scam prijshistorie).",
    )
    p_check.add_argument(
        "target",
        help="Tweakers Pricewatch URL (bijv. https://tweakers.net/pricewatch/2076152/...) of product-ID (bijv. 2076152)",
    )
    p_check.add_argument("--no-cache", action="store_true")

    # 4. Subcommand: history
    p_hist = subparsers.add_parser(
        "history",
        help="Analyseer een willekeurig apparaat waarvan de prijshistorie bekend is (komma-lijst, JSON of CSV) + optionele live winkel-URL.",
    )
    p_hist.add_argument("name", help="Naam van het product/apparaat")
    p_hist.add_argument(
        "--price",
        type=float,
        required=True,
        help="Huidige prijs incl. btw (wordt overschreven door de echte winkelprijs als --shop-url is opgegeven)",
    )
    p_hist.add_argument(
        "--history",
        required=True,
        help="Pad naar JSON/CSV met prijshistorie OF komma-gescheiden historische prijzen (bijv. '249,255,248,252,299')",
    )
    p_hist.add_argument(
        "--shop-url",
        default="",
        help="Optionele winkel-URL om de actuele prijs (incl. btw) live te verifiëren",
    )
    p_hist.add_argument(
        "--shop-name", default="Webshop", help="Naam van de winkel"
    )

    # 5. Subcommand: categories
    subparsers.add_parser(
        "categories",
        help="Toon alle voorgedefinieerde Tweakers-categorieën en aliassen.",
    )

    # 6. Subcommand: bot
    p_bot = subparsers.add_parser(
        "bot",
        help="Start de interactieve CLI-chat (REPL), voer één bot-commando uit (--cmd) of start een echte Telegram Bot (--telegram-token).",
    )
    p_bot.add_argument(
        "--cmd",
        default=None,
        help="Voer direct één bot-commando uit (bijv. '/deals videokaarten' of '/check 2076152') en sluit af",
    )
    p_bot.add_argument(
        "--telegram-token",
        default=None,
        help="Telegram Bot Token om direct via Telegram long-polling te luisteren (of zet TELEGRAM_BOT_TOKEN)",
    )
    p_bot.add_argument("--no-cache", action="store_true")

    # 7. Subcommand: vacuums (volledige reguliere + robotstofzuiger rapportage)
    p_vac = subparsers.add_parser(
        "vacuums",
        help="Draai de complete stofzuigers-met-zak (<= 25 cm) zoekopdracht (regulier + robot).",
    )
    p_vac.add_argument("--max-height", type=float, default=25.0)
    p_vac.add_argument(
        "--bag-type", choices=["bagged", "bagless", "all"], default="bagged"
    )
    p_vac.add_argument("--no-cache", action="store_true")

    args = parser.parse_args(args_list)

    if args.subcommand == "categories":
        print(format_categories_overview())
        return 0

    if args.subcommand == "vacuums":
        vac_argv = [
            "--max-height",
            str(args.max_height),
            "--bag-type",
            args.bag_type,
        ]
        if args.no_cache:
            vac_argv.append("--no-cache")
        return legacy_vacuum_cli_main(vac_argv)

    use_cache = not getattr(args, "no_cache", False)
    service = TweakersDealService(use_cache=use_cache)

    if args.subcommand == "deals":
        print(
            f"🔍 Zoeken naar beste deals in categorie '{args.category}' "
            f"(live winkelverificatie incl. btw + jaargemiddelde excl. laatste 2 mnd)..."
        )
        deals = service.find_category_deals(
            category=args.category,
            query=args.query,
            max_price=args.max_price,
            min_discount_pct=args.min_discount,
            max_height_cm=args.max_height,
            bag_type=args.bag_type,
            spec_filters=_parse_spec_list(args.spec),
            max_candidates=args.candidates,
        )
        title = f"Tweakers Deals: {args.category.title()}"
        if args.telegram:
            print("\n" + format_telegram_deals_list(deals, title=title, limit=args.limit))
        else:
            print(f"\n🏆 {title.upper()} — KORTING VS JAARGEMIDDELDE (EXCL. LAATSTE 2 MND)")
            print("=" * 145)
            print(format_cli_table(deals, limit=args.limit))
        export_product_deals(deals, title=title)
        return 0

    if args.subcommand == "search":
        print(
            f"🔍 Zoeken op Tweakers naar '{args.query}' + live winkelverificatie (incl. btw)..."
        )
        deals = service.search_products(
            query=args.query,
            max_price=args.max_price,
            min_discount_pct=args.min_discount,
            spec_filters=_parse_spec_list(args.spec),
            max_candidates=args.limit,
        )
        title = f"Zoekresultaten: {args.query}"
        if args.telegram:
            print("\n" + format_telegram_deals_list(deals, title=title, limit=args.limit))
        else:
            print(f"\n🏆 {title.upper()} — KORTING VS JAARGEMIDDELDE (EXCL. LAATSTE 2 MND)")
            print("=" * 145)
            print(format_cli_table(deals, limit=args.limit))
        export_product_deals(deals, title=title)
        return 0

    if args.subcommand == "check":
        print(f"🔎 Product controleren & winkelprijs (incl. btw) verifiëren: {args.target} ...")
        deal = service.check_product(
            args.target, force_refresh=not use_cache
        )
        if not deal:
            print(f"❌ Kon geen product of prijs vinden voor: {args.target}")
            return 1
        print("\n" + format_telegram_product_card(deal))
        return 0

    if args.subcommand == "history":
        deal = service.check_custom_price_history(
            name=args.name,
            current_price=args.price,
            raw_history=args.history,
            shop_url=args.shop_url,
            shop_name=args.shop_name,
        )
        print("\n" + format_telegram_product_card(deal))
        return 0

    if args.subcommand == "bot":
        handler = BotCommandHandler(service=service)
        if args.cmd:
            print(handler.handle_message(args.cmd))
            return 0
        if args.telegram_token:
            return run_telegram_polling(
                bot_token=args.telegram_token, handler=handler
            )
        return run_cli_repl(handler=handler)

    return 0
