import httpx
from selectolax.parser import HTMLParser
import json
import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
from tqdm import tqdm
import time

# ─── CONFIG ────────────────────────────────────────────────────────────────
BASE_SITE = "https://www.laptop.lk"
CAT_PREFIX = BASE_SITE + "/index.php/product-category/"
HEADERS = {"User-Agent": "Mozilla/5.0"}
MAX_WORKERS = 10
REQUEST_TIMEOUT = 8  # seconds
MAX_RETRIES = 2  # on transient network errors

# ─── GLOBAL CLIENT ─────────────────────────────────────────────────────────
client = httpx.Client(timeout=REQUEST_TIMEOUT, headers=HEADERS)

# ─── UTILITIES ─────────────────────────────────────────────────────────────
def fetch_html(url):
    """
    Fetches the URL with retries and returns selectolax HTML tree.
    """
    for attempt in range(1, MAX_RETRIES + 2):
        try:
            resp = client.get(url)
            resp.raise_for_status()
            return HTMLParser(resp.text)
        except httpx.RequestError as e:
            if attempt <= MAX_RETRIES:
                time.sleep(1)
                continue
            print(f" Fetch failed [{e.__class__.__name__}] on {url}")
            return HTMLParser("")

# ─── 1. CATEGORY DISCOVERY ─────────────────────────────────────────────────
def get_all_category_links():
    tree = fetch_html(BASE_SITE)
    cats = []
    for a in tree.css("a"):
        href = (a.attributes.get("href") or "").strip()
        if href.startswith(CAT_PREFIX) and href != CAT_PREFIX:
            name = a.text(strip=True)
            if name:
                cats.append({"name": name, "url": href})
    seen = set()
    return [c for c in cats if not (c["url"] in seen or seen.add(c["url"]))]

# ─── 2. PAGINATION & PRODUCT LINKS ─────────────────────────────────────────
def get_all_product_links(category_url):
    urls = []
    page = 1
    while True:
        url = category_url if page == 1 else f"{category_url.rstrip('/')}/page/{page}/"
        try:
            resp = client.get(url)
        except httpx.RequestError:
            break
        if resp.status_code == 404:
            break

        tree = HTMLParser(resp.text)
        prods = tree.css("a.woocommerce-LoopProduct-link")
        if not prods:
            break

        for p in prods:
            href = p.attributes.get("href")
            if href:
                urls.append(href)
        page += 1
    return urls

# ─── 3. PRODUCT PARSER ──────────────────────────────────────────────────────
def scrape_product_details(prod_url, category):
    tree = fetch_html(prod_url)
    ts = datetime.datetime.now().isoformat()

    title = tree.css_first("h1.product_title.entry-title")
    price = tree.css_first("p.price bdi")
    desc = tree.css_first("div.woocommerce-product-details__short-description")
    warranty = next((s for s in tree.text().splitlines() if "warranty" in s.lower()), "Warranty not found")

    specs = {}
    tbl = tree.css_first("table.shop_attributes")
    if tbl:
        for tr in tbl.css("tr"):
            th = tr.css_first("th")
            td = tr.css_first("td")
            if th and td:
                specs[th.text(strip=True)] = td.text(strip=True)
    else:
        blk = tree.css_first("div#tab-specification")
        if blk:
            for line in blk.text().split("\n"):
                if ":" in line:
                    k, v = line.split(":", 1)
                    specs[k.strip()] = v.strip()
    if not specs:
        specs = "Specs not found"

    rate_tag = tree.css_first("div.star-rating")
    rating = rate_tag.attributes.get("title", "No rating") if rate_tag else "No rating"

    imgs = [img.attributes["src"] for img in tree.css("figure.woocommerce-product-gallery__wrapper img") if "src" in img.attributes]

    return {
        "timestamp": ts,
        "category": category,
        "title": title.text(strip=True) if title else "No title",
        "price": price.text(strip=True) if price else "No price",
        "warranty": warranty,
        "description": desc.text(strip=True) if desc else "No description",
        "specifications": specs,
        "reviews": "Reviews not supported",
        "ratings": rating,
        "image_urls": imgs
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

    with open("laptoplk_products.json", "w", encoding="utf-8") as f:
        json.dump(all_data, f, ensure_ascii=False, indent=2)

    print("\n✅ Done — data written to laptoplk_products.json")

if __name__ == "__main__":
    main()
