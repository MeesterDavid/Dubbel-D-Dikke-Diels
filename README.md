# TweakerSearch — Universele Tweakers Black Friday Deal & Anti-Scam App

Een modulaire Python-applicatie (zonder externe dependencies, 100% Python 3.12 stdlib + `curl`) die **voor élk type apparatuur op Tweakers** (of elk product waarvan een prijshistorie bekend is) de **echte Black Friday deals** vindt en nepkortingen ontmaskert. Direct te draaien via de **CLI**, als **Python-service**, in een **interactieve terminal-chat (REPL)** of als **Telegram Bot**.

---

## Kernprincipes

1. **Anti-Scam Korting (Jaargemiddelde excl. laatste 2 maanden)**
   - Winkels verhogen vaak in de 60 dagen vóór Black Friday de prijzen om daarna met een hoge "van/voor"-korting te adverteren.
   - `tweakersearch` berekent de **echte korting** altijd t.o.v. het **jaargemiddelde van dag `-365` t/m dag `-60`** (`avg_year_excl_2m`) én toont de opgepompte piekprijs van de laatste 2 maanden (`peak_last_2m`).
2. **Live Winkelverificatie (100% Particulier / incl. 21% btw)**
   - Klikt daadwerkelijk door op de Tweakers `clickout`-links naar de webshops (`Coolblue`, `bol.com`, `Amazon.nl`, `MediaMarkt`, `Proshop`, `CoolSound`, `Electro World`, `Art & Craft`, `Galaxus`, etc.).
   - Negeert refurbished/tweedekans-aanbiedingen (`<details class="refurbished-listing">`).
   - Blokkeert expliciet **zakelijke prijzen (`excl. btw`)** zoals Coolblue's `excludingVat: 197.52` op een prijs van `€ 239,- incl. btw`.
   - Verwijdert alle affiliate-/trackingparameters en zakelijke parameters uit de winkel-URL's (`clean_consumer_shop_url`).

---

## Architectuur (Klaar voor CLI & Telegram/Chat-bots)

```text
src/tweakersearch/
├── __init__.py      # Publieke package-exports
├── models.py        # Dataclasses: ProductDeal, PriceAnalysis, ShopOfferVerification
├── analyzer.py      # Prijshistorie- & Anti-Scam engine (werkt voor Tweakers én eigen JSON/CSV/lijsten)
├── categories.py    # 35+ ingebouwde Tweakers-categorieën + aliassen + dynamische categorie-resolver
├── scraper.py       # TweakersClient, DPG privacy-gate handler, clickout-resolver & incl. btw prijs-extractor
├── service.py       # TweakersDealService (herbruikbare kernservice voor CLI, API's en bots)
├── formatters.py    # CLI-tabellen, Telegram-kaarten en CSV/Markdown/JSON exports
├── bot.py           # BotCommandHandler (/deals, /search, /check, /history, /categories) + REPL + Telegram polling
└── cli.py           # Subcommand CLI (deals, search, check, history, categories, bot, vacuums)
```

---

## 1. CLI Gebruik

Je kunt de app aanroepen met `.venv/bin/tweakersearch` of `.venv/bin/python main.py`:

### A. Beste deals zoeken in élke Tweakers-categorie (`deals`)
```bash
# Videokaarten (GPU's)
.venv/bin/tweakersearch deals videokaarten

# OLED Televisies tot € 1500,-
.venv/bin/tweakersearch deals televisies --query oled --max-price 1500

# Monitoren (in compact Telegram-formaat)
.venv/bin/tweakersearch deals monitoren --limit 10 --telegram

# Stofzuigers met zak en maximale hoogte van 25 cm
.venv/bin/tweakersearch deals stofzuigers --bag-type bagged --max-height 25

# Willekeurige andere categorieën (koptelefoons, ssd, laptops, airfryers, espressomachines, vaatwassers...)
.venv/bin/tweakersearch deals koptelefoons
.venv/bin/tweakersearch deals airfryers
```

### B. Specifiek apparaat zoeken op vrije tekst (`search`)
```bash
.venv/bin/tweakersearch search "Sony WH-1000XM5"
.venv/bin/tweakersearch search "RTX 5080"
.venv/bin/tweakersearch search "Miele Guard M1"
```

### C. Één specifieke Tweakers-URL of Product-ID checken (`check`)
```bash
.venv/bin/tweakersearch check https://tweakers.net/pricewatch/2076152/aeg-ab81a2dg.html
.venv/bin/tweakersearch check 2076152
```

### D. Eigen of externe prijshistorie analyseren (`history`)
Werkt voor élk product waarvan je een prijshistorie hebt (als komma-gescheiden lijst, JSON-bestand of CSV-bestand), inclusief optionele live prijsverificatie op een winkel-URL:
```bash
.venv/bin/tweakersearch history "Samsung OLED 65S95D" \
  --price 1799 \
  --history "2199,2149,2099,2050,1999,2049,2399,2499"

# Of met live winkel-check op Coolblue / bol / Amazon:
.venv/bin/tweakersearch history "AEG 8000 Animal" \
  --price 239 \
  --history "269,259,249,255,260,272" \
  --shop-url "https://www.coolblue.nl/product/946276/aeg-ab81a2dg-8000-animal.html" \
  --shop-name "Coolblue.nl"
```

### E. Overzicht van categorieën (`categories`)
```bash
.venv/bin/tweakersearch categories
```

---

## 2. Interactieve Bot / Telegram Integratie (`bot`)

De module `tweakersearch.bot` bevat `BotCommandHandler`, waarmee je direct een chat-bot hebt die commando's én geplakte Tweakers-links verwerkt:

### Ondersteunde Bot-commando's
- `/deals <categorie> [zoekterm/filters]`
  - `/deals videokaarten`
  - `/deals televisies oled max=1500`
  - `/deals stofzuigers zak <=25cm`
- `/search <zoekterm>` — bijv. `/search sony wh-1000xm5`
- `/check <tweakers-url of id>` — of plak simpelweg een `https://tweakers.net/pricewatch/...` link in de chat!
- `/history <naam> | <huidige_prijs> | <prijzenreeks> [| <winkel_url>]`
- `/categories` — lijst van ondersteunde categorieën
- `/help` — overzicht van alle commando's

### Lokaal testen in de terminal (Interactieve REPL of 1-shot commando)
```bash
# Voer één bot-commando direct uit:
.venv/bin/tweakersearch bot --cmd "/deals koptelefoons top=5"
.venv/bin/tweakersearch bot --cmd "/check 2076152"

# Start de interactieve chat-modus in je terminal:
.venv/bin/tweakersearch bot
```

### Direct koppelen aan een echte Telegram Bot
Er zijn geen externe libraries nodig; `run_telegram_polling` gebruikt de standaard Python `urllib` tegen de Telegram Bot API:
```bash
export TELEGRAM_BOT_TOKEN="123456789:ABCdefGHIjklMNOpqrSTUvwxYZ"
.venv/bin/tweakersearch bot
# Of direct via vlag:
.venv/bin/tweakersearch bot --telegram-token "123456789:ABCdef..."
```

### Integreren in je eigen Python-code / Discord / FastAPI / Webhook
```python
from tweakersearch import BotCommandHandler, TweakersDealService

service = TweakersDealService(use_cache=True)
bot = BotCommandHandler(service=service)

# 1. Verwerk elk inkomend chatbericht (Telegram / Discord / WhatsApp / CLI):
reply_markdown = bot.handle_message("/deals videokaarten max=800")
print(reply_markdown)

# 2. Of gebruik de gestructureerde Python objecten (ProductDeal):
deals = service.find_category_deals("monitoren", query="oled", max_price=900)
for d in deals:
    print(d.name, d.current_price, d.real_discount_vs_year_pct, d.shop_url)
```
