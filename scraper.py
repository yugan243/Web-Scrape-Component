import requests
from bs4 import BeautifulSoup
import json
import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed


# ─── CONFIG ────────────────────────────────────────────────────────────────
BASE_SITE = "https://www.laptop.lk"
CAT_PREFIX = BASE_SITE + "/index.php/product-category/"
HEADERS = {"User-Agent": "Mozilla/5.0"}

# ─── UTILITIES ─────────────────────────────────────────────────────────────
def fetch_soup(url):
    resp = requests.get(url, headers=HEADERS)
    resp.raise_for_status()
    return BeautifulSoup(resp.text, "html.parser")

# ─── 1. CATEGORY DISCOVERY ─────────────────────────────────────────────────
def get_all_category_links():
    """
    Scrape the homepage for real category links:
      https://www.laptop.lk/index.php/product-category/<slug>/
    """
    soup = fetch_soup(BASE_SITE)
    cats = []
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        # pick up ONLY sub-categories, not the bare root or external links
        if href.startswith(CAT_PREFIX) and href != CAT_PREFIX:
            name = a.get_text(strip=True)
            if name:
                cats.append({"name": name, "url": href})

    # dedupe by URL
    seen, unique = set(), []
    for c in cats:
        if c["url"] not in seen:
            seen.add(c["url"])
            unique.append(c)
    return unique

# ─── 2. PAGINATION & PRODUCT LINKS ─────────────────────────────────────────
def get_all_product_links(category_url):
    """
    Walks pages under a category, gracefully handling 404s
    and stopping when no more products exist.
    """
    urls = []
    page = 1

    while True:
        if page == 1:
            url = category_url
        else:
            url = category_url.rstrip("/") + f"/page/{page}/"

        # fetch without raising on 404
        resp = requests.get(url, headers=HEADERS)
        if resp.status_code == 404:
            break

        soup = BeautifulSoup(resp.content, "html.parser")
        products = soup.select("a.woocommerce-LoopProduct-link")

        # if no products on this page, we’ve hit the end
        if not products:
            break

        for p in products:
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

    # warranty: first string containing 'warranty'
    warranty = next((s for s in soup.stripped_strings if "warranty" in s.lower()),
                    "Warranty not found")

    # specs: table first, then fallback to #tab-specification text
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

    # ratings
    rate_tag = soup.select_one("div.star-rating")
    rating   = rate_tag["title"] if rate_tag and rate_tag.has_attr("title") else "No rating"

    # reviews placeholder
    reviews = "Reviews not supported"

    # product images
    imgs = [img["src"]
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
        print(" -", c["name"], c["url"])

    for cat in cats:
        print(f"\nScraping Category: {cat['name']}")
        prod_links = get_all_product_links(cat["url"])
        print(f"  → {len(prod_links)} products found")
        for i, url in enumerate(prod_links, 1):
            print(f"    [{i}/{len(prod_links)}] {url}")
            try:
                all_data.append(scrape_product_details(url, cat["name"]))
            except Exception as e:
                print(f"      ⚠️ Error on {url}: {e}")

    with open("laptoplk_products.json", "w", encoding="utf-8") as f:
        json.dump(all_data, f, ensure_ascii=False, indent=2)
    print("\n✅ Done — data written to laptoplk_products.json")




if __name__ == "__main__":
    main()
