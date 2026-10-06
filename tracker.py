"""Deal Tracker — Amazon, Flipkart, Croma, Reliance"""
import json, os, re, time, random, urllib.parse, urllib.request
from datetime import datetime
from pathlib import Path

try:
    import cloudscraper
except ImportError:
    os.system("pip install cloudscraper")
    import cloudscraper

WATCHLIST = Path("watchlist.json")
STATE_FILE = Path("tracker_state.json")
TG_TOKEN = os.environ.get("TG_TOKEN", "")
TG_CHAT = os.environ.get("TG_CHAT", "")

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Linux; Android 13; Pixel 7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Mobile Safari/537.36",
]

SESSION = cloudscraper.create_scraper(
    browser={"browser": "chrome", "platform": "windows", "mobile": False},
    delay=3,
)


def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


def num(t):
    if not t:
        return None
    m = re.search(r"([0-9][0-9,]*\.?[0-9]*)", str(t).replace(",", ""))
    if not m:
        return None
    try:
        return float(m.group(1))
    except ValueError:
        return None


def fetch(url, timeout=25):
    try:
        headers = {
            "User-Agent": random.choice(USER_AGENTS),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-IN,en;q=0.9",
        }
        r = SESSION.get(url, headers=headers, timeout=timeout)
        if r.status_code == 200:
            return r.text
        log(f"[!] HTTP {r.status_code} for {url[:60]}")
    except Exception as e:
        log(f"[!] fetch error: {str(e)[:70]}")
    return None


OUT_MARKERS = ("out of stock", "sold out", "currently unavailable",
               "temporarily out of stock", "notify me when available",
               "coming soon", "back in stock soon")


def detect_site(url):
    u = url.lower()
    if "amazon." in u:
        return "amazon"
    if "flipkart." in u:
        return "flipkart"
    if "croma." in u:
        return "croma"
    if "reliancedigital." in u:
        return "reliance"
    return None


def parse_croma(html):
    soup = __import__("bs4").BeautifulSoup(html, "html.parser")
    title = ""
    h1 = soup.select_one("h1")
    if h1:
        title = h1.get_text(" ", strip=True)
    price = None
    m = re.search(r'"@type"\s*:\s*"Product".*?"price"\s*:\s*"?([\d.]+)"?',
                  html, re.DOTALL)
    if m:
        price = num(m.group(1))
    if not price:
        m = re.search(r'"price"\s*:\s*"?([\d.]+)"?', html)
        if m:
            price = num(m.group(1))
    blob = html.lower()
    in_stock = not any(m in blob for m in OUT_MARKERS) and price is not None
    return title, price, in_stock


def parse_reliance(html):
    soup = __import__("bs4").BeautifulSoup(html, "html.parser")
    title = ""
    h1 = soup.select_one("h1")
    if h1:
        title = h1.get_text(" ", strip=True)
    price = None
    m = re.search(r'"@type"\s*:\s*"Product".*?"price"\s*:\s*"?([\d.]+)"?',
                  html, re.DOTALL)
    if m:
        price = num(m.group(1))
    blob = html.lower()
    in_stock = not any(m in blob for m in OUT_MARKERS) and price is not None
    return title, price, in_stock


def parse_amazon(html):
    soup = __import__("bs4").BeautifulSoup(html, "html.parser")
    title = ""
    for sel in ("#productTitle", "h1 span", "h1"):
        el = soup.select_one(sel)
        if el:
            t = el.get_text(" ", strip=True)
            if len(t) > len(title):
                title = t
    price = None
    for sel in ("span.a-price-whole", "span.a-offscreen", ".a-price .a-offscreen"):
        el = soup.select_one(sel)
        if el:
            price = num(el.get_text())
            if price:
                break
    blob = html.lower()
    in_stock = not any(m in blob for m in OUT_MARKERS) and price is not None
    return title, price, in_stock


def parse_flipkart(html):
    soup = __import__("bs4").BeautifulSoup(html, "html.parser")
    title = ""
    for sel in ("span.VU-ZEz", "span.B_NuCI", "h1 span", "h1"):
        el = soup.select_one(sel)
        if el:
            t = el.get_text(" ", strip=True)
            if len(t) > len(title):
                title = t
    price = None
    for sel in ("div.Nx9bqj", "div._30jeq3", "div._16Jk6d"):
        el = soup.select_one(sel)
        if el:
            price = num(el.get_text())
            if price:
                break
    blob = html.lower()
    in_stock = not any(m in blob for m in OUT_MARKERS) and price is not None
    return title, price, in_stock


PARSERS = {
    "croma": parse_croma,
    "reliance": parse_reliance,
    "amazon": parse_amazon,
    "flipkart": parse_flipkart,
}


def send_tg(text):
    if not TG_TOKEN or not TG_CHAT:
        log("[!] TG secrets missing")
        return
    try:
        data = urllib.parse.urlencode({
            "chat_id": TG_CHAT,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": "true",
        }).encode()
        req = urllib.request.Request(
            f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage", data=data)
        urllib.request.urlopen(req, timeout=15)
        log(f"[+] TG sent")
    except Exception as e:
        log(f"[!] TG err: {str(e)[:60]}")


def check_product(product, state):
    name = product["name"]
    url = product["url"]
    target = product.get("target_price", 0)

    site = detect_site(url)
    if not site:
        log(f"[!] unknown site: {url[:60]}")
        return

    parser = PARSERS[site]
    log(f"[*] {name}  ({site})")

    html = fetch(url)
    if not html:
        return

    title, price, in_stock = parser(html)
    if not price:
        log(f"    price nahi mila")
        return

    log(f"    ₹{price:,.0f}  stock={in_stock}")

    prev = state.get(url, {})
    prev_price = prev.get("price")
    prev_stock = prev.get("in_stock", False)

    # Alerts
    if in_stock and not prev_stock and prev:
        msg = f"🔔 <b>{name}</b>\n✅ Back in stock!\nPrice: ₹{price:,.0f}\n{url}"
        send_tg(msg)

    if prev_price and price < prev_price:
        save = prev_price - price
        msg = (f"🔔 <b>{name}</b>\n📉 Down ₹{save:,.0f}\n"
               f"Was: ₹{prev_price:,.0f}\nNow: ₹{price:,.0f}\n{url}")
        send_tg(msg)

    if not in_stock and prev_stock:
        msg = f"🔔 <b>{name}</b>\n🔴 Sold out\nWas: ₹{prev_price or 0:,.0f}\n{url}"
        send_tg(msg)

    if target and price <= target:
        msg = (f"🎯 <b>{name}</b>\nTarget hit! (≤ ₹{target:,.0f})\n"
               f"Now: ₹{price:,.0f}\n{url}")
        send_tg(msg)

    state[url] = {
        "name": name,
        "price": price,
        "in_stock": in_stock,
        "checked_at": datetime.now().isoformat(),
    }


def main():
    log("=" * 50)
    log("Deal Tracker run")

    if not WATCHLIST.exists():
        log("[!] watchlist.json nahi mila")
        return

    try:
        products = json.loads(WATCHLIST.read_text())
    except Exception as e:
        log(f"[!] watchlist parse error: {e}")
        return

    state = {}
    if STATE_FILE.exists():
        try:
            state = json.loads(STATE_FILE.read_text())
        except Exception:
            state = {}

    log(f"Products: {len(products)}")

    for p in products:
        try:
            check_product(p, state)
        except Exception as e:
            log(f"[!] {p.get('name','?')}: {str(e)[:80]}")
        time.sleep(random.uniform(1.5, 3))

    STATE_FILE.write_text(json.dumps(state, indent=2))
    log("Done.")


if __name__ == "__main__":
    main()
