import json
import asyncio
import httpx
import re
import time
from selectolax.parser import HTMLParser 
from datetime import datetime
from typing import List, Dict, Optional, Set, Any
from tqdm.asyncio import tqdm

class AsyncLaptopLKScraper:
    def __init__(self, max_connections: int = 25, max_retries: int = 3):
        self.source_website = "laptop.lk"
        self.scrape_timestamp = datetime.now().isoformat()
        self.shop_phone = "+94 77 733 6464"
        self.shop_whatsapp = "+94 77 733 6464"
        self.max_retries = max_retries
        self.headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
        }
        self.semaphore = asyncio.Semaphore(max_connections)

    async def fetch_page(self, client: httpx.AsyncClient, url: str) -> Optional[str]:
        async with self.semaphore:
            for attempt in range(self.max_retries):
                try:
                    response = await client.get(url, headers=self.headers, timeout=30, follow_redirects=True)
                    response.raise_for_status()
                    return response.text
                except (httpx.RequestError, httpx.HTTPStatusError):
                    if attempt + 1 == self.max_retries: break
                    await asyncio.sleep(2 ** attempt)
        return None

    # --- MODIFIED: This function now uses the much faster selectolax parser ---
    def parse_product_data(self, html: str, url: str) -> Optional[Dict]:
        try:
            tree = HTMLParser(html)
            product_container = tree.css_first("div[id^=product-]")
            if not product_container: return None

            # Decompose is not available; we just select from the container
            
            title_node = product_container.css_first("h1.product_title")
            title = title_node.text(strip=True) if title_node else None

            product_id = product_container.id.split('-')[-1] if product_container.id else None
            
            desc_node = product_container.css_first("div#tab-description, div.woocommerce-tabs")
            description_html = desc_node.html if desc_node else None

            category_nodes = product_container.css("span.posted_in a")
            all_categories = [node.text(strip=True) for node in category_nodes]
            brand = next((cat for cat in all_categories if cat.lower() in ['hp', 'dell', 'apple', 'lenovo', 'asus', 'msi', 'acer', 'samsung']), None)
            category_path = [c for c in all_categories if c.lower() != (brand or '').lower()]

            image_nodes = product_container.css("div.woocommerce-product-gallery__image a")
            image_urls = [node.attributes.get('href') for node in image_nodes]

            price_curr_node = product_container.css_first("p.price ins .amount, span.electro-price ins .amount, p.price > .amount, span.electro-price > .amount")
            price_orig_node = product_container.css_first("p.price del .amount, span.electro-price del .amount")
            price_current = re.sub(r'[^\d.]', '', price_curr_node.text(strip=True)) if price_curr_node else "0"
            price_original = re.sub(r'[^\d.]', '', price_orig_node.text(strip=True)) if price_orig_node else None

            availability_text = "Out of Stock" if product_container.css_first("p.stock.out-of-stock") else "In Stock"

            warranty_text = None
            warranty_img = product_container.css_first("img[alt*='warranty' i]")
            if warranty_img and 'alt' in warranty_img.attributes:
                warranty_text = warranty_img.attributes['alt'].replace('Year-warranty', ' Year Warranty').replace('-', ' ')

            variants = [{"variant_id_native": product_id, "variant_title": "Default", "price_current": price_current, "price_original": price_original, "currency": "LKR", "availability_text": availability_text}]
            
            return {"product_id_native": product_id, "product_url": url, "product_title": title, "warranty": warranty_text,"description_html": description_html, "brand": brand, "category_path": category_path, "image_urls": image_urls, "variants": variants, "metadata": {"source_website": self.source_website, "shop_contact_phone": self.shop_phone,"shop_contact_whatsapp": self.shop_whatsapp, "scrape_timestamp": self.scrape_timestamp}}
        except Exception:
            return None

    def save_data(self, data: List[Dict[str, Any]], filename: str):
        output = {"extraction_info": {"total_products_extracted": len(data), "extraction_timestamp": self.scrape_timestamp}, "products": data}
        with open(filename, 'w', encoding='utf-8') as f:
            json.dump(output, f, indent=2, ensure_ascii=False)
        print(f"\n✅ Data successfully saved to {filename}")

# Helper function for the optimized pipeline
async def fetch_and_parse_product(client: httpx.AsyncClient, scraper: AsyncLaptopLKScraper, url: str) -> Optional[Dict]:
    html = await scraper.fetch_page(client, url)
    if html:
        return scraper.parse_product_data(html, url)
    return None

# The main function orchestrating the entire scrape
async def main() -> int:
    scraper = AsyncLaptopLKScraper()
    sitemap_index_url = "https://www.laptop.lk/sitemap_index.xml"

    async with httpx.AsyncClient(http2=True) as client: # Enabling HTTP/2 for potential speed up
        print(f"--- Fetching sitemap index: {sitemap_index_url} ---")
        index_xml = await scraper.fetch_page(client, sitemap_index_url)
        if not index_xml: return 0

        product_sitemap_urls = [node.text() for node in HTMLParser(index_xml).css('loc') if 'product-sitemap' in node.text()]
        
        sitemap_tasks = [scraper.fetch_page(client, url) for url in product_sitemap_urls]
        sitemap_xmls = await asyncio.gather(*sitemap_tasks)
        
        unique_product_urls = {loc.text() for xml in sitemap_xmls if xml for loc in HTMLParser(xml).css('url > loc')}
        product_urls_list = list(unique_product_urls)
        
        print(f"\nFound {len(product_urls_list)} unique product URLs to scrape.")
        if not product_urls_list: return 0

        print(f"--- Scraping {len(product_urls_list)} products ---")
        tasks = [fetch_and_parse_product(client, scraper, url) for url in product_urls_list]
        results = await tqdm.gather(*tasks, desc="Scraping Products")
        all_products_data = [item for item in results if item is not None]

    scraper.save_data(all_products_data, "..\Raw Data\laptop_lk_scrape.json")
    return len(all_products_data)


if __name__ == "__main__":
    start_time = time.time()
    
    # Use asyncio.run() to execute the main async function from a regular script
    product_count = asyncio.run(main())
    
    end_time = time.time()

    print("\n" + "="*50)
    print("🚀 SCRAPE PERFORMANCE SUMMARY 🚀")
    print("="*50)
    print(f"Total Products Scraped: {product_count}")
    print(f"Total Time Taken: {end_time - start_time:.2f} seconds")
    print("="*50)