#!/usr/bin/env python3
"""
sharaf_sync.py  -  real Sharaf catalogue data -> SHIRO chatbot

Usage:
    python3 sharaf_sync.py --html shiro_v2.html --out shiro_v2.html
    python3 sharaf_sync.py --json-only            # only writes products.json
    python3 sharaf_sync.py --from-json products.json --html shiro_v2.html --out shiro_v2.html

No third-party packages needed (standard library only).
After a run, open  unmatched.txt  to see products that were dropped because no
category rule matched. If a product lands in the wrong category, fix the RULES below.
"""
import argparse, html as htmlmod, json, re, sys, time, urllib.request, urllib.error
from datetime import date

BASE = "https://www.sharafstore.com/en/shop/category/"
MAX_PER_CAT = 40     # products kept per category (in-stock first). Raise it if you want more.
MAX_PAGES = 40       # safety stop per source
DELAY = 0.7          # seconds between requests (be polite to the shop)

# ---- source category page  ->  ordered rules [(shiro_category, regex or None)] -------------
# First matching rule wins. None = catch-all. Products that match nothing are dropped.
BAG = r"\b(bag|backpack|sleeve|briefcase|carry case|laptop case)\b"
LAPTOP = (r"laptop|notebook|chromebook|ideapad|thinkpad|vivobook|zenbook|inspiron|latitude|legion|"
          r"victus|pavilion|nitro|predator|katana|loq|thinkbook|macbook|swift|aspire")
COMPUTER_RULES = [
    ("bag", BAG), ("macbook", r"macbook"), ("laptop", LAPTOP),
    ("monitor", r"monitor|display screen"), ("keyboard", r"keyboard"),
    ("mouse", r"\bmouse\b|\bmice\b"), ("printer", r"printer|toner|ink ?cartridge|ink tank"),
    ("modem", r"modem|router|mesh|extender|access point|wi-?fi"),
    ("storage", r"\bssd\b|\bhdd\b|hard (disk|drive)|flash (drive|disk)|usb (drive|stick)|memory card|sd card|portable storage|external (drive|disk)|pendrive"),
]
CONSOLE_RULES = [
    ("playstation", r"playstation|\bps ?[45]\b|dualsense|dualshock"),
    ("nintendo", r"nintendo|switch|joy-?con"),
    ("xbox", r"xbox"),
] + COMPUTER_RULES
BEAUTY = r"hair|shav|trimmer|epilat|straighten|curler|clipper|beard|toothbrush|facial|skin|lash|nail|beauty|face"
KITCHEN = (r"blender|mixer|coffee|kettle|toaster|fry|cooker|oven|grill|juicer|chopper|kitchen|rice|steam|"
           r"espresso|\bpot\b|\bpan\b|microwave|dishwasher|fridge|refrigerator|food|ice|waffle|sandwich|tea")

SOURCES = {
    "mobile-accessories-smart-phone-5":       [("phone", None)],
    "mobile-accessories-mobile-accessories-10": [
        ("powerbank", r"power ?bank"),
        ("adapter", r"adapt[eo]r|charger|charging (head|brick|station)|wall (plug|charger)|\bplug\b"),
        ("cable", r"cable|cord|lightning|usb-?c to|type-?c to"),
    ],
    "mobile-accessories-smart-watches-9":     [("applewatch", r"apple watch"), ("androidwatch", None)],
    "mobile-accessories-tablet-4":            [("ipad", r"ipad"), ("tabletandroid", None)],
    "headphones-speaker-headphone-16":        [("headphone", None)],
    "headphones-speaker-sound-23":            [("speaker", None)],
    "television-television-30":               [("tv", None)],
    "television-tv-device-32":                [("projector", r"project"), ("mediaplayer", None)],
    "television-tv-accessories-35":           [("projector", r"project"),
                                               ("tvcable", r"cable|socket|plug|hdmi|extension|mount|bracket|adapter|splitter|antenna|remote")],
    "games-computer-laptop-41":               COMPUTER_RULES + [("laptop", None)],
    "games-computer-macbook-45":              [("macbook", None)],
    "games-computer-console-accessories-46":  CONSOLE_RULES,
    "small-home-appliances-vacuum-cleaners-70": [("vacuum", None)],
    "small-home-appliances-small-appliances-75": [("massage", r"massag"), ("beauty", BEAUTY), ("kitchen", None)],
    "small-home-appliances-home-86":          [("massage", r"massag"), ("beauty", BEAUTY), ("kitchen", KITCHEN), ("home", None)],
    "small-home-appliances-robot-vacuum-cleaners-140": [("robot", None)],
    "white-goods-kitchen-appliance-136":      [("kitchen", None)],
    "white-goods-laundry-appliance-137":      [("home", None)],
    "air-conditioning-air-conditioner-92":    [("ac", None)],
    "air-conditioning-fan-94":                [("ac", None)],
    "air-conditioning-heater-95":             [("ac", None)],
    "air-conditioning-air-care-devices-98":   [("ac", None)],
    "hobby-and-fitness-scooter-bicycle-103":  [("scooter", None)],
    "hobby-and-fitness-sports-equipment-106": [("massage", r"massag")],
    "hobby-and-fitness-toys-107":             [("toy", None)],
    "hobby-and-fitness-pet-accessories-109":  [("pet", None)],
    "hobby-and-fitness-others-110":           [("massage", r"massag"), ("beauty", BEAUTY)],
    "hobby-and-fitness-stanley-thermos-111":  [("mug", None)],
    "hobby-and-fitness-meta-glasses-186":     [("meta", None)],
}

UA = {"User-Agent": "Mozilla/5.0 (compatible; SHIRO-catalog-sync/1.0)", "Accept-Language": "en"}


def fetch(url, tries=3):
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=30) as r:
                return r.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            if e.code in (404, 410):
                return None
            err = e
        except Exception as e:  # network hiccup
            err = e
        time.sleep(1.5 * (i + 1))
    print("  ! failed:", url, err, file=sys.stderr)
    return None


HREF = re.compile(r'href="(?:https://www\.sharafstore\.com)?/en/shop/([^"?#/]+-(\d+))(?:\?[^"]*)?"')
TAG = re.compile(r"<[^>]+>")


def parse_page(page):
    """-> list of dict(id, slug, name, price, stock, new)"""
    first = {}
    for m in HREF.finditer(page):
        first.setdefault(m.group(2), (m.start(), m.group(1)))
    order = sorted(first.items(), key=lambda kv: kv[1][0])
    out = []
    for n, (pid, (pos, slug)) in enumerate(order):
        end = order[n + 1][1][0] if n + 1 < len(order) else pos + 6000
        win = page[pos:min(end, pos + 6000)]
        # name: title attribute of an <a> pointing to this product
        name = None
        for am in re.finditer(r"<a\b[^>]*>", win):
            tag = am.group(0)
            if slug in tag:
                tm = re.search(r'title="([^"]+)"', tag)
                if tm and tm.group(1).strip():
                    name = htmlmod.unescape(tm.group(1)).strip()
                    break
        if not name:
            name = slug.rsplit("-", 1)[0].replace("-", " ").title()
        text = htmlmod.unescape(TAG.sub(" ", win))
        pm = re.search(r"([\d.]+)\s*TRY", text) or re.search(r"(\d[\d,]*\.\d{2})\s*₺", text)
        if not pm:
            continue
        price = float(pm.group(1).replace(",", ""))
        out.append({
            "id": int(pid), "slug": slug, "name": re.sub(r"\s+", " ", name),
            "price": int(price) if price == int(price) else round(price, 2),
            "stock": 0 if "OUT OF STOCK" in text.upper() else 1,
            "new": 1 if re.search(r"\bNEW\b", text) else 0,
        })
    return out


def scrape_source(src):
    seen, items = set(), []
    for p in range(1, MAX_PAGES + 1):
        url = BASE + src + ("" if p == 1 else f"/page/{p}")
        page = fetch(url)
        time.sleep(DELAY)
        if not page:
            break
        got = [x for x in parse_page(page) if x["id"] not in seen]
        if not got:
            break
        for x in got:
            seen.add(x["id"])
        items += got
        print(f"  {src} p{p}: +{len(got)}")
    return items


def build():
    cats, unmatched, seen_ids = {}, [], set()
    for src, rules in SOURCES.items():
        print("->", src)
        for it in scrape_source(src):
            if it["id"] in seen_ids:
                continue
            for cat, rx in rules:
                if rx is None or re.search(rx, it["name"], re.I):
                    cats.setdefault(cat, []).append(it)
                    seen_ids.add(it["id"])
                    break
            else:
                unmatched.append(f'{src}\t{it["name"]}')
    items = {}
    for cat, lst in cats.items():
        lst.sort(key=lambda x: -x["stock"])               # in stock first, site order kept inside
        items[cat] = [[x["id"], x["name"], x["price"], x["stock"], x["new"], x["slug"]]
                      for x in lst[:MAX_PER_CAT]]
    open("unmatched.txt", "w", encoding="utf-8").write("\n".join(unmatched))
    counts = {k: len(v) for k, v in items.items()}
    print("category counts:", counts, "| unmatched:", len(unmatched))
    return {"updated": date.today().isoformat(), "items": items}


def inject(html_path, out_path, data):
    s = open(html_path, encoding="utf-8").read()
    blob = json.dumps(data, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    new, n = re.subn(r'(<script id="shiro-data" type="application/json">).*?(</script>)',
                     lambda m: m.group(1) + blob + m.group(2), s, count=1, flags=re.S)
    if not n:
        sys.exit('no <script id="shiro-data"> block found in the html')
    open(out_path, "w", encoding="utf-8").write(new)
    print("written:", out_path)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--html"); ap.add_argument("--out")
    ap.add_argument("--json-only", action="store_true")
    ap.add_argument("--from-json")
    a = ap.parse_args()
    data = json.load(open(a.from_json, encoding="utf-8")) if a.from_json else build()
    if not a.from_json:
        json.dump(data, open("products.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    if not a.json_only:
        if not (a.html and a.out):
            sys.exit("give --html and --out (or use --json-only)")
        inject(a.html, a.out, data)
