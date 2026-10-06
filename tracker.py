"""Deal Tracker — Amazon, Flipkart, Croma, Reliance + Telegram commands"""
import json, os, re, time, random, urllib.parse, urllib.request, urllib.error
from datetime import datetime
from pathlib import Path

try:
    import cloudscraper
except ImportError:
    os.system("pip install cloudscraper")
    import cloudscraper

try:
    from bs4 import BeautifulSoup
except ImportError:
    os.system("pip install beautifulsoup4")
    from bs4 import BeautifulSoup

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

OUT_MARKERS = ("out of stock", "sold out", "currently unavailable",
               "temporarily out of stock", "notify me when available",
               "coming soon", "back in stock soon")


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
        log(f"[!] HTTP {r.status_code} {url[:50]}")
    except Exception as e:
        log(f"[!] fetch: {str(e)[:60]}")
    return None


def detect_site(url):
    u = url.lower()
    if "amazon." in u: return "amazon"
    if "flipkart." in u: return "flipkart"
    if "croma." in u: return "croma"
    if "reliancedigital." in u: return "reliance"
    return None


def parse_croma(html):
    soup = BeautifulSoup(html, "html.parser")
    title = ""
    h1 = soup.select_one("h1")
    if h1: title = h1.get_text(" ", strip=True)
    price = None
    m = re.search(r'"@type"\s*:\s*"Product".*?"price"\s*:\s*"?([\d.]+)"?', html, re.DOTALL)
    if m: price = num(m.group(1))
    if not price:
        m = re.search(r'"price"\s*:\s*"?([\d.]+)"?', html)
        if m: price = num(m.group(1))
    blob = html.lower()
    in_stock = not any(m in blob for m in OUT_MARKERS) and price is not None
    return title, price, in_stock


def parse_reliance(html):
    soup = BeautifulSoup(html, "html.parser")
    title = ""
    h1 = soup.select_one("h1")
    if h1: title = h1.get_text(" ", strip=True)
    price = None
    m = re.search(r'"@type"\s*:\s*"Product".*?"price"\s*:\s*"?([\d.]+)"?', html, re.DOTALL)
    if m: price = num(m.group(1))
    blob = html.lower()
    in_stock = not any(m in blob for m in OUT_MARKERS) and price is not None
    return title, price, in_stock


def parse_amazon(html):
    soup = BeautifulSoup(html, "html.parser")
    title = ""
    for sel in ("#productTitle", "h1 span", "h1"):
        el = soup.select_one(sel)
        if el:
            t = el.get_text(" ", strip=True)
            if len(t) > len(title): title = t
    price = None
    for sel in ("span.a-price-whole", "span.a-offscreen", ".a-price .a-offscreen"):
        el = soup.select_one(sel)
        if el:
            price = num(el.get_text())
            if price: break
    blob = html.lower()
    in_stock = not any(m in blob for m in OUT_MARKERS) and price is not None
    return title, price, in_stock


def parse_flipkart(html):
    soup = BeautifulSoup(html, "html.parser")
    title = ""
    for sel in ("span.VU-ZEz", "span.B_NuCI", "h1 span", "h1"):
        el = soup.select_one(sel)
        if el:
            t = el.get_text(" ", strip=True)
            if len(t) > len(title): title = t
    price = None
    for sel in ("div.Nx9bqj", "div._30jeq3", "div._16Jk6d"):
        el = soup.select_one(sel)
        if el:
            price = num(el.get_text())
            if price: break
    blob = html.lower()
    in_stock = not any(m in blob for m in OUT_MARKERS) and price is not None
    return title, price, in_stock


PARSERS = {
    "croma": parse_croma,
    "reliance": parse_reliance,
    "amazon": parse_amazon,
    "flipkart": parse_flipkart,
}


# ═══════════════════════════════════════════════
#   TELEGRAM
# ═══════════════════════════════════════════════

def send_tg(text, chat_id=None):
    cid = chat_id or TG_CHAT
    if not TG_TOKEN or not cid:
        log("[!] TG secrets missing")
        return False

    def _post(payload):
        data = urllib.parse.urlencode(payload).encode()
        req = urllib.request.Request(
            f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage", data=data)
        try:
            r = urllib.request.urlopen(req, timeout=15)
            body = json.loads(r.read().decode())
            return body.get("ok", False), body
        except urllib.error.HTTPError as e:
            return False, e.read().decode()[:300]
        except Exception as e:
            return False, str(e)[:200]

    base = {"chat_id": cid, "text": text, "disable_web_page_preview": "true"}
    ok, body = _post({**base, "parse_mode": "HTML"})
    if ok:
        log("[+] TG sent")
        return True

    log(f"[!] TG HTML fail: {body}")
    ok2, body2 = _post(base)
    if ok2:
        log("[+] TG sent (plain fallback)")
        return True

    log(f"[!] TG plain fail: {body2}")
    return False


def tg_get_updates(offset):
    if not TG_TOKEN:
        return []
    try:
        url = f"https://api.telegram.org/bot{TG_TOKEN}/getUpdates"
        params = urllib.parse.urlencode({
            "offset": offset,
            "timeout": 1,
            "allowed_updates": json.dumps(["message"]),
        })
        r = urllib.request.urlopen(f"{url}?{params}", timeout=15)
        data = json.loads(r.read().decode())
        return data.get("result", []) if data.get("ok") else []
    except Exception as e:
        log(f"[!] getUpdates: {str(e)[:60]}")
        return []


# ═══════════════════════════════════════════════
#   STATE
# ═══════════════════════════════════════════════

def load_watchlist():
    if not WATCHLIST.exists():
        return []
    try:
        return json.loads(WATCHLIST.read_text())
    except Exception:
        return []


def save_watchlist(wl):
    WATCHLIST.write_text(json.dumps(wl, indent=2, ensure_ascii=False))


def load_state():
    if STATE_FILE.exists():
        try:
            raw = json.loads(STATE_FILE.read_text())
            return {
                "products": raw.get("products", {}),
                "last_update_id": raw.get("last_update_id", 0),
            }
        except Exception:
            pass
    return {"products": {}, "last_update_id": 0}


def save_state(state):
    clean = {
        "products": state.get("products", {}),
        "last_update_id": state.get("last_update_id", 0),
    }
    STATE_FILE.write_text(json.dumps(clean, indent=2))


# ═══════════════════════════════════════════════
#   COMMAND HANDLER
# ═══════════════════════════════════════════════

HELP_TEXT = """<b>Deal Tracker — Commands</b>

<b>Add product:</b>
<code>/add &lt;url&gt;</code>
<code>/add &lt;url&gt; &lt;target_price&gt;</code>

<b>Manage:</b>
<code>/list</code> — sari products dikhao
<code>/remove &lt;n&gt;</code> — n-th product hatao
<code>/check</code> — abhi check karo (next run pe)
<code>/help</code> — yeh list

<b>Supported sites:</b>
amazon.in, flipkart.com, croma.com, reliancedigital.in"""


def handle_command(text, chat_id, wl):
    parts = text.strip().split()
    if not parts:
        return False
    cmd = parts[0].lower()
    if "@" in cmd:
        cmd = cmd.split("@")[0]
    args = parts[1:]

    if cmd in ("/start", "/help"):
        send_tg(HELP_TEXT, chat_id)
        return False

    if cmd == "/add":
        if not args:
            send_tg("Usage: <code>/add &lt;url&gt; [target_price]</code>", chat_id)
            return False
        url = args[0]
        target = 0
        if len(args) >= 2:
            try:
                target = int(args[1].replace(",", ""))
            except ValueError:
                send_tg("Target price number nahi hai", chat_id)
                return False
        site = detect_site(url)
        if not site:
            send_tg("Sirf Amazon, Flipkart, Croma, Reliance supported", chat_id)
            return False
        if any(p.get("url") == url for p in wl):
            send_tg("Yeh URL pehle se watchlist mein hai", chat_id)
            return False

        send_tg("⏳ URL check kar raha...", chat_id)
        html = fetch(url)
        if not html:
            send_tg("❌ URL se response nahi mila", chat_id)
            return False
        parser = PARSERS[site]
        try:
            title, price, in_stock = parser(html)
        except Exception as e:
            send_tg(f"❌ Parse fail: {str(e)[:60]}", chat_id)
            return False
        if not price or not title:
            send_tg("❌ Product info nahi mila — URL valid product page hai?", chat_id)
            return False

        wl.append({
            "name": title[:80],
            "url": url,
            "target_price": target,
            "notify_on_stock": True,
        })
        save_watchlist(wl)
        stock = "🟢" if in_stock else "🔴"
        price_s = f"₹{price:,.0f}"
        target_s = f" | Target ₹{target:,}" if target else ""
        send_tg(f"✅ <b>Added</b>\n{title[:70]}\n{stock} {price_s}{target_s}\n\nTotal: {len(wl)}", chat_id)
        return True

    if cmd == "/list":
        if not wl:
            send_tg("Watchlist khaali hai", chat_id)
            return False
        lines = [f"<b>Watchlist ({len(wl)})</b>\n"]
        for i, p in enumerate(wl, 1):
            name = p.get("name", "?")[:50]
            target = p.get("target_price", 0)
            t = f" | ₹{target:,}" if target else ""
            lines.append(f"<b>{i}.</b> {name}{t}")
        send_tg("\n".join(lines), chat_id)
        return False

    if cmd == "/remove":
        if not args:
            send_tg("Usage: <code>/remove &lt;n&gt;</code>", chat_id)
            return False
        try:
            idx = int(args[0]) - 1
        except ValueError:
            send_tg("Number daal", chat_id)
            return False
        if idx < 0 or idx >= len(wl):
            send_tg(f"Index 1-{len(wl)} ke beech hona chahiye", chat_id)
            return False
        removed = wl.pop(idx)
        save_watchlist(wl)
        send_tg(f"🗑️ Removed: <b>{removed.get('name','?')[:60]}</b>\nTotal: {len(wl)}", chat_id)
        return True

    if cmd == "/check":
        send_tg("⏱️ Agle run pe check hoga (max 5 min)", chat_id)
        return False

    send_tg(f"Unknown: <code>{cmd}</code>\n<code>/help</code> dekh", chat_id)
    return False


def process_telegram(state):
    offset = state.get("last_update_id", 0) + 1
    updates = tg_get_updates(offset)
    if not updates:
        log("[tg] koi naya message nahi")
        return False

    wl = load_watchlist()
    changed = False
    for u in updates:
        state["last_update_id"] = max(state.get("last_update_id", 0), u.get("update_id", 0))
        changed = True
        msg = u.get("message") or {}
        text = msg.get("text", "")
        chat_id = msg.get("chat", {}).get("id")
        if text.startswith("/"):
            log(f"[tg] cmd: {text[:50]}")
            try:
                if handle_command(text, chat_id, wl):
                    changed = True
            except Exception as e:
                log(f"[!] cmd err: {e}")
                send_tg(f"Error: {str(e)[:80]}", chat_id)
    return changed


# ═══════════════════════════════════════════════
#   PRODUCT CHECK
# ═══════════════════════════════════════════════

def check_product(product, state):
    name = product["name"]
    url = product["url"]
    target = product.get("target_price", 0)

    site = detect_site(url)
    if not site:
        log(f"[!] unknown site: {url[:50]}")
        return

    parser = PARSERS[site]
    log(f"[*] {name[:50]}  ({site})")

    html = fetch(url)
    if not html:
        return

    try:
        title, price, in_stock = parser(html)
    except Exception as e:
        log(f"[!] parse: {str(e)[:60]}")
        return

    if not price:
        log(f"    price nahi mila")
        return

    log(f"    ₹{price:,.0f}  stock={in_stock}")

    prev = state.get("products", {}).get(url, {})
    prev_price = prev.get("price")
    prev_stock = prev.get("in_stock", False)

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

    if "products" not in state:
        state["products"] = {}
    state["products"][url] = {
        "name": name,
        "price": price,
        "in_stock": in_stock,
        "checked_at": datetime.now().isoformat(),
    }


# ═══════════════════════════════════════════════
#   MAIN
# ═══════════════════════════════════════════════

def main():
    log("=" * 50)
    log("Deal Tracker run")

    state = load_state()

    try:
        cmd_changed = process_telegram(state)
        if cmd_changed:
            save_state(state)
    except Exception as e:
        log(f"[!] tg process: {e}")

    wl = load_watchlist()
    if not wl:
        log("[!] watchlist khaali")
        save_state(state)
        return

    log(f"Products: {len(wl)}")
    for p in wl:
        try:
            check_product(p, state)
        except Exception as e:
            log(f"[!] {p.get('name','?')[:40]}: {str(e)[:60]}")
        time.sleep(random.uniform(1.5, 3))

    save_state(state)
    log("Done.")


if __name__ == "__main__":
    main()
