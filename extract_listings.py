import streamlit as st
import requests
from urllib.robotparser import RobotFileParser
from requests.adapters import HTTPAdapter, Retry
from bs4 import BeautifulSoup
import re
import time
import pandas as pd
from urllib.parse import urlparse

# Optional: Selenium (only if JS-heavy pages)
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from webdriver_manager.chrome import ChromeDriverManager


def get_session_with_retries(retries=3, backoff_factor=1.0, status_forcelist=(500, 502, 503, 504)):
    session = requests.Session()
    retry = Retry(
        total=retries,
        read=retries,
        connect=retries,
        backoff_factor=backoff_factor,
        status_forcelist=status_forcelist,
        raise_on_status=False
    )
    adapter = HTTPAdapter(max_retries=retry)
    session.mount('http://', adapter)
    session.mount('https://', adapter)
    return session


def analyze_robots_txt(site_url, session):
    robots_url = site_url.rstrip('/') + "/robots.txt"
    st.write(f"Fetching robots.txt from: {robots_url}")

    try:
        response = session.get(robots_url, timeout=5)
    except requests.RequestException as e:
        st.error(f"Error fetching robots.txt: {e}")
        return None, [], [], False

    if response.status_code != 200:
        st.warning(f"robots.txt not found (status code {response.status_code})")
        return None, [], [], False

    st.text_area("robots.txt content", response.text, height=200)

    rp = RobotFileParser()
    rp.set_url(robots_url)
    rp.read()

    user_agents = ['*', 'Googlebot', 'Bingbot']
    for ua in user_agents:
        can_fetch = rp.can_fetch(ua, site_url)
        st.write(f"User-agent '{ua}' can crawl homepage: {can_fetch}")

    sitemaps = [line.split(':', 1)[1].strip() for line in response.text.splitlines() if line.lower().startswith('sitemap:')]
    if sitemaps:
        st.write("Sitemap URLs found:")
        for sitemap in sitemaps:
            st.markdown(f"- [{sitemap}]({sitemap})")
    else:
        st.write("No Sitemap URLs found in robots.txt")

    crawl_delays = [line.split(':', 1)[1].strip() for line in response.text.splitlines() if line.lower().startswith('crawl-delay')]
    if crawl_delays:
        st.write("Crawl-delay directives found:")
        for delay in crawl_delays:
            st.write(f"- {delay}")
    else:
        st.write("No Crawl-delay directives found.")

    has_robots = True
    return response.text, sitemaps, crawl_delays, has_robots


def is_javascript_heavy(html_content):
    soup = BeautifulSoup(html_content, 'html.parser')
    scripts = soup.find_all('script')
    if len(scripts) > 10:
        st.warning("⚠️ Site appears to be JavaScript-heavy based on number of <script> tags.")
        return True
    if not soup.find_all(['div', 'p', 'span', 'table']):
        st.warning("⚠️ Page has minimal visible content. May require JavaScript rendering.")
        return True
    return False


def check_open_apis_and_feeds(base_url, session):
    st.write("🔎 Checking for open APIs or RSS feeds...")

    try:
        response = session.get(base_url, timeout=10)
        soup = BeautifulSoup(response.text, "html.parser")

        rss_links = soup.find_all("link", type="application/rss+xml")
        if rss_links:
            st.success("✅ RSS Feeds found:")
            for link in rss_links:
                st.write(f"- {link.get('href')}")
        else:
            st.warning("❌ No RSS feeds found on homepage.")

        api_endpoints = [a['href'] for a in soup.find_all('a', href=True) if '/api/' in a['href'].lower()]
        if api_endpoints:
            st.success("✅ API endpoints found in HTML:")
            for api in api_endpoints:
                st.write(f"- {api}")
        else:
            st.warning("❌ No obvious API endpoints found.")
    except Exception as e:
        st.error(f"Error while checking APIs/feeds: {e}")


def scrape_menu(base_url, session, use_selenium=False):
    rows = []

    def get_soup(url):
        if use_selenium:
            options = Options()
            options.add_argument('--headless=new')
            driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=options)
            driver.get(url)
            time.sleep(3)  # wait for JS to load
            html = driver.page_source
            driver.quit()
            return BeautifulSoup(html, "html.parser")
        else:
            try:
                response = session.get(url, timeout=10)
                if response.status_code != 200:
                    st.warning(f"Failed to load {url} - Status: {response.status_code}")
                    return None
                return BeautifulSoup(response.content, "html.parser")
            except requests.RequestException as e:
                st.error(f"Error fetching page: {e}")
                return None

    def scrape_page(url):
        soup = get_soup(url)
        if not soup:
            return

        panels = soup.find_all("div", class_="vc_tta-panel")
        st.write(f"Found {len(panels)} categories on page: {url}")

        for panel in panels:
            heading = panel.find("div", class_="vc_tta-panel-heading")
            title_span = heading.find("span", class_="vc_tta-title-text") if heading else None
            category_name = title_span.text.strip() if title_span else "Unnamed Category"

            body = panel.find("div", class_="vc_tta-panel-body")
            if not body:
                continue

            lines = [line.strip() for line in body.get_text(separator="\n").splitlines() if line.strip()]
            items = []
            current_item = {}

            price_pattern = re.compile(r"(Price\s*:\s*|)(\d+)\s*(L\.?E\.?)", re.IGNORECASE)
            price_only_pattern = re.compile(r"^(\d+)\s*(L\.?E\.?)\.?$", re.IGNORECASE)

            i = 0
            while i < len(lines):
                line = lines[i]
                price_match = price_pattern.search(line)
                price_only_match = price_only_pattern.match(line)

                if price_match or price_only_match:
                    price = price_match.group(2) if price_match else price_only_match.group(1)
                    if current_item:
                        current_item['price'] = price + " L.E"
                        items.append(current_item)
                        current_item = {}
                    i += 1
                    continue

                if 'name' in current_item and 'price' not in current_item:
                    current_item['description'] = current_item.get('description', '') + " " + line
                    i += 1
                    continue

                if line.lower() not in ["price", "l.e", "le"]:
                    current_item = {'name': line}
                i += 1

            if current_item and 'price' in current_item:
                items.append(current_item)

            for it in items:
                rows.append([
                    category_name,
                    it.get('name', ''),
                    it.get('description', ''),
                    it.get('price', '')
                ])

        # Handle pagination
        next_link = soup.find('a', class_='next')
        if next_link and next_link.get('href'):
            next_page_url = next_link['href']
            st.write(f"Following pagination to: {next_page_url}")
            scrape_page(next_page_url)

    scrape_page(base_url)

    return rows


def calculate_crawlability_score(has_robots, crawl_delays, sitemaps, js_heavy):
    """
    Simple heuristic score out of 100:
    - robots.txt presence: +25
    - crawl-delay: +15
    - sitemap: +30
    - not JS-heavy: +30
    """
    score = 0
    if has_robots:
        score += 25
    if crawl_delays:
        score += 15
    if sitemaps:
        score += 30
    if not js_heavy:
        score += 30
    return score


def recommend_crawling_tools(js_heavy, sitemaps):
    recommendations = []
    if js_heavy:
        recommendations.append("Use Selenium or Playwright for rendering JavaScript-heavy pages.")
        recommendations.append("Consider headless browsers or Puppeteer.")
    else:
        recommendations.append("Use Requests + BeautifulSoup for simple static pages.")
        recommendations.append("Scrapy is recommended for scalable crawling.")

    if sitemaps:
        recommendations.append("Utilize sitemap URLs to improve crawl efficiency.")

    return recommendations


def main():
    st.title("Menu Scraper & Analyzer with Crawlability and Sitemap")

    site_url = "https://bonappetit.com.eg"
    menu_url = f"{site_url}/menu-m/"

    session = get_session_with_retries()

    # Analyze robots.txt
    robots_txt, sitemaps, crawl_delays, has_robots = analyze_robots_txt(site_url, session)

    # Check APIs & feeds
    check_open_apis_and_feeds(site_url, session)

    # Check JS heaviness
    resp = session.get(menu_url)
    js_heavy = is_javascript_heavy(resp.text)

    # Scrape menu with or without Selenium
    menu_data = scrape_menu(menu_url, session, use_selenium=js_heavy)

    # Display extracted data
    if menu_data:
        df = pd.DataFrame(menu_data, columns=["Category", "Item Name", "Description", "Price"])
        st.subheader("Extracted Menu Items")
        st.dataframe(df)

        # Top extracted data summary
        st.subheader("Top Extracted Data Summary")

        # Top categories by number of items
        top_categories = df['Category'].value_counts().head(5)
        st.write("Top 5 Categories by number of items:")
        st.bar_chart(top_categories)

        # Top priced items (clean prices)
        def price_to_float(p):
            try:
                return float(re.search(r'\d+', p).group())
            except:
                return 0

        df['Price_num'] = df['Price'].apply(price_to_float)
        top_prices = df.sort_values('Price_num', ascending=False).head(5)[['Item Name', 'Price']]
        st.write("Top 5 Priced Items:")
        st.table(top_prices)

    else:
        st.warning("No menu data extracted.")

    # Crawlability score
    st.subheader("Crawlability Score")
    score = calculate_crawlability_score(has_robots, crawl_delays, sitemaps, js_heavy)
    st.progress(score / 100)
    st.write(f"Crawlability Score: {score}/100")

    # Recommendations for crawling tools
    st.subheader("Recommendations for Crawling Tools")
    recs = recommend_crawling_tools(js_heavy, sitemaps)
    for rec in recs:
        st.info(rec)

    # Visual Sitemap (if available)
    if sitemaps:
        st.subheader("Visual Sitemap URLs")
        for sitemap in sitemaps:
            st.markdown(f"[{sitemap}]({sitemap})")


if __name__ == "__main__":
    main()
