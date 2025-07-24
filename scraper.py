import requests
from bs4 import BeautifulSoup
import json
import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
from tqdm import tqdm
import time

# ─── CONFIG ────────────────────────────────────────────────────────────────
BASE_SITE  = "https://www.laptop.lk"
CAT_PREFIX = BASE_SITE + "/index.php/product-category/"
HEADERS    = {"User-Agent": "Mozilla/5.0"}
MAX_WORKERS = 10
REQUEST_TIMEOUT = 8   # seconds
MAX_RETRIES     = 2   # on transient network errors

# ─── GLOBAL SESSION ─────────────────────────────────────────────────────────
session = requests.Session()
session.headers.update(HEADERS)

# ─── UTILITIES ─────────────────────────────────────────────────────────────
def fetch_soup(url):
    """
    Fetches the URL with a timeout, retries once on error,
    and never raises—returns an empty soup on failure.
    """
    for attempt in range(1, MAX_RETRIES + 2):
        try:
            resp = session.get(url, timeout=REQUEST_TIMEOUT)
            resp.raise_for_status()
            return BeautifulSoup(resp.text, "lxml")
        except requests.RequestException as e:
            if attempt <= MAX_RETRIES:
                time.sleep(1)   # tiny back-off
                continue
            print(f" Fetch failed [{e.__class__.__name__}] on {url}")
            return BeautifulSoup("", "lxml")

# ─── 1. CATEGORY DISCOVERY ─────────────────────────────────────────────────
def get_all_category_links():
    soup = fetch_soup(BASE_SITE)
    cats = []
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if href.startswith(CAT_PREFIX) and href != CAT_PREFIX:
            name = a.get_text(strip=True)
            if name:
                cats.append({"name": name, "url": href})
    # dedupe
    seen = set()
    return [c for c in cats if not (c["url"] in seen or seen.add(c["url"]))]

# ─── 2. PAGINATION & PRODUCT LINKS ─────────────────────────────────────────
def get_all_product_links(category_url):
    urls = []
    page = 1
    while True:
        url = category_url if page == 1 else f"{category_url.rstrip('/')}/page/{page}/"
        # fetch but don’t raise on 404
        try:
            resp = session.get(url, timeout=REQUEST_TIMEOUT)
        except requests.RequestException:
            break
        if resp.status_code == 404:
            break

        soup = BeautifulSoup(resp.text, "lxml")
        prods = soup.select("a.woocommerce-LoopProduct-link")
        if not prods:
            break

        for p in prods:
            href = p.get("href")
            if href:
                urls.append(href)
        page += 1

    return urls

# ─── 3. PRODUCT PARSER ──────────────────────────────────────────────────────
def scrape_product_details(prod_url, category):
    soup = fetch_soup(prod_url)
    ts   = datetime.datetime.now().isoformat()

    title = soup.select_one("h1.product_title.entry-title")
    price = soup.select_one("p.price bdi")
    desc  = soup.select_one("div.woocommerce-product-details__short-description")
    warranty = next((s for s in soup.stripped_strings if "warranty" in s.lower()),
                    "Warranty not found")

    specs = {}
    tbl = soup.select_one("table.shop_attributes")
    if tbl:
        for tr in tbl.select("tr"):
            k = tr.select_one("th").get_text(strip=True)
            v = tr.select_one("td").get_text(strip=True)
            specs[k] = v
    else:
        blk = soup.select_one("div#tab-specification")
        if blk:
            for line in blk.get_text("\n").split("\n"):
                if ":" in line:
                    k, v = line.split(":", 1)
                    specs[k.strip()] = v.strip()
    if not specs:
        specs = "Specs not found"

    rate_tag = soup.select_one("div.star-rating")
    rating   = rate_tag["title"] if rate_tag and rate_tag.has_attr("title") else "No rating"
    reviews  = "Reviews not supported"
    imgs     = [img["src"]
                for img in soup.select("figure.woocommerce-product-gallery__wrapper img")
                if img.has_attr("src")]

    return {
        "timestamp":      ts,
        "category":       category,
        "title":          title.get_text(strip=True) if title else "No title",
        "price":          price.get_text(strip=True) if price else "No price",
        "warranty":       warranty,
        "description":    desc.get_text(strip=True)  if desc  else "No description",
        "specifications": specs,
        "reviews":        reviews,
        "ratings":        rating,
        "image_urls":     imgs
    }

# ─── 4. MAIN ────────────────────────────────────────────────────────────────
def main():
    all_data = []

    cats = get_all_category_links()
    print(f"Found {len(cats)} categories:")
    for c in cats:
        print(f" - {c['name']}")

    for cat in cats:
        print(f"\nCategory: {cat['name']}")
        prod_links = get_all_product_links(cat["url"])
        total = len(prod_links)
        print(f" → {total} products")

        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
            futures = {
                executor.submit(scrape_product_details, url, cat["name"]): url
                for url in prod_links
            }

            for future in tqdm(
                as_completed(futures),
                total=total,
                desc=cat["name"],
                unit="prod",
                ncols=80
            ):
                url = futures[future]
                try:
                    all_data.append(future.result())
                except Exception as e:
                    print(f"   ⚠️ Error on {url}: {e}")

    # dump to JSON
    with open("laptoplk_products.json", "w", encoding="utf-8") as f:
        json.dump(all_data, f, ensure_ascii=False, indent=2)

    print("\n✅ Done — data written to laptoplk_products.json")

if __name__ == "__main__":
    main()
