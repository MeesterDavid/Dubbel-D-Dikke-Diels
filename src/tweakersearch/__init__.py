from tweakersearch.analyzer import (
    analyze_price_history,
    parse_price_history_points,
)
from tweakersearch.bot import (
    BotCommandHandler,
    run_cli_repl,
    run_telegram_polling,
)
from tweakersearch.categories import CATEGORIES, resolve_category_config
from tweakersearch.cli import app_main
from tweakersearch.models import (
    PriceAnalysis,
    ProductDeal,
    ShopOfferVerification,
)
from tweakersearch.scraper import cli_main, find_tweakers_vacuum_deals
from tweakersearch.service import TweakersDealService


def main() -> None:
    raise SystemExit(app_main())


__all__ = [
    "main",
    "app_main",
    "cli_main",
    "find_tweakers_vacuum_deals",
    "TweakersDealService",
    "BotCommandHandler",
    "run_cli_repl",
    "run_telegram_polling",
    "analyze_price_history",
    "parse_price_history_points",
    "ProductDeal",
    "PriceAnalysis",
    "ShopOfferVerification",
    "CATEGORIES",
    "resolve_category_config",
]
