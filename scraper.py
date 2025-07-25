import requests, time, json, re
from lxml import html
from concurrent.futures import ThreadPoolExecutor, as_completed
from tqdm import tqdm

# ─── CONFIG ────────────────────────────────────────────────────
BASE_URL     = "https://www.laptop.lk"
CAT_PREFIX   = BASE_URL + "/index.php/product-category/"
HEADERS      = {"User-Agent": "Mozilla/5.0"}
MAX_WORKERS  = 32
TIMEOUT      = (5, 10)
RETRIES      = 2

session = requests.Session()
session.headers.update(HEADERS)

# ─── CATEGORY DISCOVERY ────────────────────────────────────────
def discover_categories():
    res = session.get(BASE_URL + "/shop", timeout=TIMEOUT)
    tree = html.fromstring(res.content)
    return list(set(
        a.get("href") for a in tree.xpath('//a[contains(@href,"/product-category/")]')
    ))

# ─── PAGINATION LOGIC ──────────────────────────────────────────
def get_total_pages(cat_url):
    res = session.get(cat_url, timeout=TIMEOUT)
    tree = html.fromstring(res.content)
    pages = tree.xpath('//a[@class="page-numbers"]/text()')
    return max(map(int, pages)) if pages else 1

def get_page_url(cat, page): return f"{cat.rstrip('/')}/page/{page}/"

# ─── PRODUCT LISTING ──────────────────────────────────────────
def get_product_links(page_url):
    res = session.get(page_url, timeout=TIMEOUT)
    tree = html.fromstring(res.content)
    return tree.xpath('//a[contains(@class,"woocommerce-LoopProduct-link")]/@href')

# ─── PRODUCT PARSING ───────────────────────────────────────────
def parse_product(url):
    try:
        res = session.get(url, timeout=TIMEOUT)
        tree = html.fromstring(res.content)

        def tx(path): return tree.xpath(path)[0].strip() if tree.xpath(path) else ""

        return {
            "title": tx('//h1/text()'),
            "price": tx('//p[@class="price"]//text()'),
            "description": tx('//div[contains(@class,"short-description")]//text()'),
            "specs": tree.xpath('//table//tr//text()'),
            "images": tree.xpath('//div[contains(@class,"product-gallery")]//img/@src'),
            "url": url,
            "timestamp": time.time()
        }
    except Exception as e:
        return {"url": url, "error": str(e)}

# ─── MAIN SCRAPER ──────────────────────────────────────────────
def scrape_all():
    all_products = []
    categories = discover_categories()
    listing_tasks, detail_tasks = [], []

    with ThreadPoolExecutor(MAX_WORKERS) as pool:
        for cat in categories:
            total = get_total_pages(cat)
            for page in range(1, total + 1):
                listing_tasks.append(pool.submit(get_product_links, get_page_url(cat, page)))

        all_urls = set()
        for task in tqdm(as_completed(listing_tasks), total=len(listing_tasks), desc="Listing URLs"):
            all_urls.update(task.result())

        for url in all_urls:
            detail_tasks.append(pool.submit(parse_product, url))

        for task in tqdm(as_completed(detail_tasks), total=len(detail_tasks), desc="Scraping details"):
            all_products.append(task.result())

    with open("laptoplk_products.json", "w", encoding="utf-8") as f:
        json.dump(all_products, f, indent=2, ensure_ascii=False)

scrape_all()
