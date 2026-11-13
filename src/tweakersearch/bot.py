from __future__ import annotations

import json
import os
import re
import time
import urllib.parse
import urllib.request

from tweakersearch.formatters import (
    format_categories_overview,
    format_telegram_deals_list,
    format_telegram_product_card,
)
from tweakersearch.service import TweakersDealService


class BotCommandHandler:
    """
    Transport-onafhankelijke command-router voor Telegram, CLI-chat of webhooks.
    Geef een tekstbericht mee aan `handle_message(text)` en ontvang direct het geformatteerde antwoord.
    """

    def __init__(self, service: TweakersDealService | None = None) -> None:
        self.service = service or TweakersDealService()

    def handle_message(self, text: str) -> str:
        msg = (text or "").strip()
        if not msg:
            return self.help_text()

        # Als iemand direct een Tweakers Pricewatch-URL plakt (zonder commando), voer automatisch een check uit!
        m_url = re.search(
            r"(https?://tweakers\.net/pricewatch/\d+[^\s]*)", msg
        )
        if m_url and not msg.startswith("/"):
            return self._cmd_check(m_url.group(1))

        if msg.startswith("/"):
            parts = msg.split(maxsplit=1)
            cmd = parts[0].lower().split("@")[0]  # Strip eventuele @BotName in Telegram groepen
            arg = parts[1].strip() if len(parts) > 1 else ""

            if cmd in ("/start", "/help", "/hulp"):
                return self.help_text()
            if cmd in ("/categories", "/categorieen", "/cats"):
                return format_categories_overview()
            if cmd == "/deals":
                return self._cmd_deals(arg)
            if cmd in ("/search", "/zoek"):
                return self._cmd_search(arg)
            if cmd in ("/check", "/scam", "/scamcheck"):
                return self._cmd_check(arg)
            if cmd in ("/history", "/historie"):
                return self._cmd_history(arg)

            return (
                f"❓ Onbekend commando `{cmd}`.\n\n" + self.help_text()
            )

        # Vrije tekst zonder slash: behandel als zoekopdracht
        return self._cmd_search(msg)

    def help_text(self) -> str:
        return (
            "🤖 *Tweakers Black Friday Deal & Anti-Scam Bot*\n"
            "_Controleert élk apparaat op Tweakers: berekent echte korting t.o.v. het jaargemiddelde (excl. laatste 2 mnd) en klikt door naar de winkel voor de echte prijs incl. 21% btw._\n\n"
            "*Beschikbare commando's:*\n"
            "• `/deals <categorie> [zoekterm/filters]` — Beste deals in een categorie\n"
            "  _Voorbeelden:_\n"
            "  `  /deals videokaarten`\n"
            "  `  /deals televisies oled`\n"
            "  `  /deals monitoren max=400`\n"
            "  `  /deals stofzuigers zak <=25cm`\n"
            "• `/search <zoekterm>` — Zoek specifiek apparaat op Tweakers\n"
            "  _Voorbeeld:_ `/search sony wh-1000xm5` of `/search rtx 5080`\n"
            "• `/check <tweakers-url of id>` — Ontmasker nepkorting & check live winkelprijs\n"
            "  _Voorbeeld:_ `/check 2076152` of plak direct een Tweakers-link!\n"
            "• `/history <naam> | <huidige_prijs> | <prijzenreeks> [| <winkel_url>]` — Analyseer eigen/bekende prijshistorie\n"
            "  _Voorbeeld:_ `/history Dyson V15 | 499 | 520,530,515,525,649`\n"
            "• `/categories` — Toon alle populaire Tweakers-categorieën"
        )

    def _cmd_deals(self, arg: str) -> str:
        if not arg:
            return (
                "⚠️ Geef een categorie op, bijvoorbeeld:\n"
                "`/deals videokaarten`\n"
                "`/deals monitoren`\n"
                "`/deals stofzuigers zak <=25cm`\n"
                "Typ `/categories` voor een overzicht."
            )

        tokens = arg.split()
        category = tokens[0]
        rest = tokens[1:]

        max_price: float | None = None
        max_height_cm: float | None = None
        bag_type: str | None = None
        limit = 8
        query_words: list[str] = []
        spec_filters: dict[str, str] = {}

        for tok in rest:
            tl = tok.lower()
            m_maxp = re.match(r"^(?:max|max_price|prijs<=?)=(\d+(?:[.,]\d+)?)$", tl)
            m_height = re.match(r"^(?:<=?|h<=?|hoogte<=?)(\d+(?:[.,]\d+)?)\s*cm$", tl)
            m_limit = re.match(r"^top=(\d+)$", tl)
            if m_maxp:
                max_price = float(m_maxp.group(1).replace(",", "."))
            elif m_height:
                max_height_cm = float(m_height.group(1).replace(",", "."))
            elif m_limit:
                limit = max(1, min(25, int(m_limit.group(1))))
            elif tl in ("zak", "met-zak", "bagged"):
                bag_type = "bagged"
            elif tl in ("zakloos", "bagless"):
                bag_type = "bagless"
            elif "=" in tok:
                k, v = tok.split("=", 1)
                spec_filters[k.strip()] = v.strip()
            else:
                query_words.append(tok)

        query = " ".join(query_words) if query_words else None
        deals = self.service.find_category_deals(
            category=category,
            query=query,
            max_price=max_price,
            max_height_cm=max_height_cm,
            bag_type=bag_type,
            spec_filters=spec_filters or None,
            max_candidates=max(16, limit * 2),
        )
        title = f"Beste Tweakers Deals: {category.title()}"
        if query:
            title += f" ({query})"
        if bag_type == "bagged":
            title += " [Met zak]"
        if max_height_cm:
            title += f" [<= {max_height_cm:g} cm]"
        return format_telegram_deals_list(deals, title=title, limit=limit)

    def _cmd_search(self, arg: str) -> str:
        if not arg:
            return "⚠️ Geef een zoekterm op, bijv. `/search sony wh-1000xm5` of `/search aeg 8000`."
        deals = self.service.search_products(arg, max_candidates=10)
        return format_telegram_deals_list(
            deals, title=f"Zoekresultaten voor '{arg}'", limit=8
        )

    def _cmd_check(self, arg: str) -> str:
        if not arg:
            return "⚠️ Geef een Tweakers-link of product-ID op, bijv. `/check 2076152`."
        deal = self.service.check_product(arg)
        if not deal:
            return f"❌ Kon geen geldig product of prijs vinden voor: `{arg}`"
        return format_telegram_product_card(deal)

    def _cmd_history(self, arg: str) -> str:
        parts = [p.strip() for p in arg.split("|")]
        if len(parts) < 3:
            return (
                "⚠️ Gebruik het formaat:\n"
                "`/history <Productnaam> | <Huidige prijs> | <Historische prijzen of JSON/CSV> [| <Winkel-URL>]`\n"
                "Voorbeeld:\n"
                "`/history Sony OLED TV | 1299 | 1499,1450,1399,1420,1699`"
            )
        name = parts[0]
        try:
            curr_price = float(parts[1].replace("€", "").replace(",", ".").strip())
        except ValueError:
            curr_price = 0.0
        raw_hist = parts[2]
        shop_url = parts[3] if len(parts) >= 4 else ""
        deal = self.service.check_custom_price_history(
            name=name,
            current_price=curr_price,
            raw_history=raw_hist,
            shop_url=shop_url,
        )
        return format_telegram_product_card(deal)


def run_cli_repl(handler: BotCommandHandler | None = None) -> int:
    """Interactieve terminal-chat (REPL) om de bot lokaal te gebruiken of te testen."""
    bot = handler or BotCommandHandler()
    print("=" * 80)
    print("💬 TWEAKERSEARCH INTERACTIEVE BOT-MODUS (Typ 'exit' of 'quit' om te stoppen)")
    print("=" * 80)
    print(bot.help_text())
    print("-" * 80)

    while True:
        try:
            user_in = input("\nJij > ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nTot ziens!")
            break
        if user_in.lower() in ("exit", "quit", "/quit", "/exit"):
            print("Tot ziens!")
            break
        if not user_in:
            continue
        print("\n⏳ Bezig met ophalen & live winkelverificatie...\n")
        reply = bot.handle_message(user_in)
        print(reply)
    return 0


def run_telegram_polling(
    bot_token: str | None = None,
    handler: BotCommandHandler | None = None,
    poll_timeout: int = 25,
) -> int:
    """
    Start een echte Telegram Bot via long-polling (pure Python stdlib, geen extra packages nodig).
    Stel TELEGRAM_BOT_TOKEN in of geef `--telegram-token` mee.
    """
    token = bot_token or os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        print(
            "❌ Geen Telegram Bot Token gevonden. Geef --telegram-token <TOKEN> mee "
            "of zet de omgevingsvariabele TELEGRAM_BOT_TOKEN."
        )
        return 1

    bot = handler or BotCommandHandler()
    base_api = f"https://api.telegram.org/bot{token}"
    offset = 0
    print("🤖 Telegram Bot gestart! Luistert naar berichten via long-polling...")

    while True:
        try:
            url = f"{base_api}/getUpdates?timeout={poll_timeout}&offset={offset}"
            with urllib.request.urlopen(url, timeout=poll_timeout + 10) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            for upd in data.get("result", []):
                offset = int(upd["update_id"]) + 1
                msg = upd.get("message") or upd.get("edited_message") or {}
                chat_id = msg.get("chat", {}).get("id")
                text = msg.get("text", "")
                if not chat_id or not text:
                    continue
                print(f"📩 [Chat {chat_id}] {text}")
                reply = bot.handle_message(text)
                _send_telegram_message(base_api, chat_id, reply)
        except KeyboardInterrupt:
            print("\n🛑 Telegram Bot gestopt.")
            break
        except Exception as e:
            print(f"⚠️ Telegram polling fout: {e}")
            time.sleep(3.0)
    return 0


def _send_telegram_message(
    base_api: str, chat_id: int | str, text: str
) -> None:
    payload = urllib.parse.urlencode(
        {
            "chat_id": str(chat_id),
            "text": text,
            "disable_web_page_preview": "true",
        }
    ).encode("utf-8")
    req = urllib.request.Request(f"{base_api}/sendMessage", data=payload)
    try:
        urllib.request.urlopen(req, timeout=15)
    except Exception as e:
        print(f"⚠️ Kon bericht niet verzenden naar {chat_id}: {e}")
