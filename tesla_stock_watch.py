#!/usr/bin/env python3
"""
Tesla UK inventory watcher - new Model Y stock near HU3.

Polls Tesla's inventory API (the same one the inventory page loads in the
background) and sends a push notification to your phone via ntfy.sh whenever
a car appears that it hasn't seen before.

Setup:
    pip install curl_cffi requests
    1. Install the free "ntfy" app on your phone (iOS / Android).
    2. In the app, subscribe to a topic - pick something hard to guess,
       e.g. greebo-tesla-my-8k2f9 - and put the same name in NTFY_TOPIC below.
    3. Run:  python tesla_stock_watch.py
       Leave it running (or see the notes at the bottom for running it on a schedule).
"""

import json
import os
import random
import sys
import time
from datetime import datetime

# ----------------------------------------------------------------------------
# Settings
# ----------------------------------------------------------------------------
NTFY_TOPIC = "greebo-tesla-my-CHANGE-ME"   # <- change to your own topic name
CHECK_EVERY_MINUTES = 5                     # don't go much lower; Tesla rate-limits
MODEL = "my"                                # Model Y
TRIM_CODE = "PRAWD"                         # from your link (CATEGORY=PRAWD)
ZIP = "HU3"
LAT, LNG = 53.7398, -0.3600                 # approx. centre of HU3
RANGE = 0                                   # 0 = all of GB, as on your link
SEEN_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "seen_cars.json")
PAGE_URL = ("https://www.tesla.com/en_GB/inventory/new/my"
            f"?arrangeby=plh&zip={ZIP}&range={RANGE}&CATEGORY={TRIM_CODE}")
# ----------------------------------------------------------------------------

API_URL = "https://www.tesla.com/inventory/api/v4/inventory-results"

CONFIG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tesla_watch_config.json")


def pause_before_exit():
    # Keeps the window open when the script is started by double-clicking.
    if "--once" not in sys.argv:
        try:
            input("\nPress Enter to close...")
        except EOFError:
            pass


def ensure_packages():
    """Install curl_cffi / requests automatically if they're missing."""
    import importlib, subprocess
    missing = [p for p in ("curl_cffi", "requests") if importlib.util.find_spec(p) is None]
    if missing:
        print(f"Installing missing packages: {', '.join(missing)} ...")
        subprocess.check_call([sys.executable, "-m", "pip", "install", *missing])


try:
    import importlib.util
    ensure_packages()
except Exception as e:
    print(f"Couldn't install packages automatically ({e}).")
    print("Open Command Prompt and run:  pip install curl_cffi requests")
    pause_before_exit()
    sys.exit(1)

try:
    # curl_cffi mimics a real Chrome browser, which gets past Tesla's bot
    # protection far more reliably than plain requests.
    from curl_cffi import requests as http
    HTTP_KW = {"impersonate": "chrome"}
except ImportError:
    import requests as http
    HTTP_KW = {}
    print("Tip: `pip install curl_cffi` - plain requests often gets blocked by Tesla.")

import requests as plain_requests  # used for ntfy


def get_topic():
    """Use the topic set at the top of the file, or ask once and remember it."""
    global NTFY_TOPIC
    if os.environ.get("NTFY_TOPIC"):          # set as a secret on GitHub
        NTFY_TOPIC = os.environ["NTFY_TOPIC"].strip()
        return
    if "CHANGE-ME" not in NTFY_TOPIC:
        return
    if os.environ.get("GITHUB_ACTIONS"):
        raise RuntimeError("NTFY_TOPIC secret isn't set in the GitHub repo settings.")
    try:
        with open(CONFIG_FILE) as f:
            NTFY_TOPIC = json.load(f)["ntfy_topic"]
            return
    except Exception:
        pass
    print("First run setup")
    print("In the ntfy app on your phone, tap + and subscribe to a topic name")
    print("that's hard to guess (e.g. greebo-tesla-my-8k2f9).")
    topic = ""
    while not topic:
        topic = input("Type that same topic name here and press Enter: ").strip().replace(" ", "-")
    NTFY_TOPIC = topic
    with open(CONFIG_FILE, "w") as f:
        json.dump({"ntfy_topic": topic}, f)
    print(f"Saved. Alerts will go to ntfy topic '{topic}'.\n")


def build_query(offset=0):
    return {
        "query": {
            "model": MODEL,
            "condition": "new",
            "options": {"TRIM": [TRIM_CODE]},
            "arrangeby": "Price",
            "order": "asc",
            "market": "GB",
            "language": "en",
            "super_region": "europe",
            "lng": LNG,
            "lat": LAT,
            "zip": ZIP,
            "range": RANGE,
            "region": "GB",
        },
        "offset": offset,
        "count": 50,
        "outsideOffset": 0,
        "outsideSearch": False,
    }


def fetch_inventory():
    """Return a list of car dicts currently listed."""
    cars, offset = [], 0
    headers = {
        "Accept": "application/json",
        "Accept-Language": "en-GB,en;q=0.9",
        "Referer": PAGE_URL,
    }
    # Behave like a browser: open the inventory page first so Tesla hands
    # over its cookies, then ask the stock API using the same session.
    session = http.Session(**HTTP_KW) if HTTP_KW else http.Session()
    page = session.get(PAGE_URL, timeout=30,
                       headers={"Accept-Language": "en-GB,en;q=0.9"})
    print(f"Inventory page: HTTP {page.status_code}")
    time.sleep(random.uniform(2, 4))

    while True:
        params = {"query": json.dumps(build_query(offset), separators=(",", ":"))}
        r = session.get(API_URL, params=params, headers=headers, timeout=30)
        if r.status_code != 200:
            raise RuntimeError(f"Tesla returned HTTP {r.status_code} "
                               f"(inventory page gave {page.status_code})")
        data = r.json()
        results = data.get("results", [])
        # When nothing matches exactly, Tesla returns {"exact": [], "approximate": [...]}
        if isinstance(results, dict):
            results = results.get("exact", [])
        cars.extend(results)
        total = int(data.get("total_matches_found", 0) or 0)
        offset += 50
        if not results or offset >= total:
            break
    # Belt and braces: keep only cars matching the trim code from your link.
    return [c for c in cars if TRIM_CODE in json.dumps(c)]


def describe(car):
    def first(v):
        return v[0] if isinstance(v, list) and v else v
    price = car.get("InventoryPrice") or car.get("Price") or car.get("PurchasePrice")
    price_txt = f"£{int(price):,}" if price else "price n/a"
    paint = first(car.get("PAINT")) or ""
    interior = first(car.get("INTERIOR")) or ""
    wheels = first(car.get("WHEELS")) or ""
    trim = car.get("TrimName") or first(car.get("TRIM")) or TRIM_CODE
    year = car.get("Year", "")
    demo = " (ex-demo)" if car.get("IsDemo") else ""
    bits = ", ".join(b for b in [paint, interior, wheels] if b)
    return f"{year} Model Y {trim}{demo} - {price_txt}\n{bits}".strip()


def car_link(car):
    vin = car.get("VIN")
    return f"https://www.tesla.com/en_GB/my/order/{vin}" if vin else PAGE_URL


def notify(title, message, url=PAGE_URL, priority="high"):
    print(f"\n[{datetime.now():%H:%M}] {title}\n{message}\n")
    try:
        plain_requests.post(
            f"https://ntfy.sh/{NTFY_TOPIC}",
            data=message.encode("utf-8"),
            headers={"Title": title, "Priority": priority, "Click": url, "Tags": "car"},
            timeout=15,
        )
    except Exception as e:
        print(f"  (couldn't send push notification: {e})")


def load_seen():
    try:
        with open(SEEN_FILE) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def save_seen(seen):
    with open(SEEN_FILE, "w") as f:
        json.dump(seen, f, indent=2, sort_keys=True)


STATUS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "status.json")


def load_failures():
    try:
        with open(STATUS_FILE) as f:
            return int(json.load(f).get("consecutive_failures", 0))
    except Exception:
        return 0


def save_failures(n):
    with open(STATUS_FILE, "w") as f:
        json.dump({"consecutive_failures": n}, f)


def check_once(seen, first_run):
    cars = fetch_inventory()
    current = {c.get("VIN"): c for c in cars if c.get("VIN")}

    if first_run:
        notify("Tesla watcher running",
               f"Watching Model Y {TRIM_CODE} stock. {len(current)} car(s) listed right now - "
               "you'll be pinged when new ones appear.", priority="default")
        return {vin: describe(c) for vin, c in current.items()}

    new_vins = [v for v in current if v not in seen]
    for vin in new_vins:
        notify("New Model Y in stock!", describe(current[vin]), url=car_link(current[vin]))

    if not new_vins:
        print(f"[{datetime.now():%H:%M}] No new stock ({len(current)} listed).")

    # Remember everything seen (including sold cars) so a car that briefly
    # drops off and reappears doesn't trigger a duplicate alert.
    seen.update({vin: describe(c) for vin, c in current.items()})
    return seen


def main():
    get_topic()
    once = "--once" in sys.argv   # for GitHub Actions / Task Scheduler / cron
    if not once:
        print(f"Watching Model Y {TRIM_CODE} stock near {ZIP}, checking every "
              f"{CHECK_EVERY_MINUTES} min. Leave this window open (minimise it is fine).\n")

    seen = load_seen()
    failures = load_failures()

    while True:
        try:
            seen = check_once(seen or {}, first_run=seen is None)
            save_seen(seen)
            failures = 0
        except Exception as e:
            failures += 1
            print(f"[{datetime.now():%H:%M}] Check failed: {e}")
            if failures == 3:
                notify("Tesla watcher having trouble",
                       f"3 checks in a row failed ({e}). It'll keep retrying.", priority="default")
        save_failures(failures)
        if once:
            break
        # A little randomness so it doesn't hit Tesla on an exact beat.
        time.sleep(CHECK_EVERY_MINUTES * 60 + random.randint(0, 60))


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nStopped.")
    except Exception:
        import traceback
        print("\nSomething went wrong:\n")
        traceback.print_exc()
        print("\nCopy the text above and send it to Claude.")
    pause_before_exit()

# ----------------------------------------------------------------------------
# Running it without keeping a window open
#   Windows: Task Scheduler -> create a task that runs
#            `python C:\path\to\tesla_stock_watch.py --once` every 5 minutes.
#   Mac/Linux: `crontab -e` and add
#            */5 * * * * /usr/bin/python3 /path/to/tesla_stock_watch.py --once
# ----------------------------------------------------------------------------
