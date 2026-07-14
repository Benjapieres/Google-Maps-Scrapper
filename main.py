import logging
from typing import List, Optional, Set
from playwright.sync_api import sync_playwright, Page
from dataclasses import dataclass, asdict
from urllib.parse import urlparse
import pandas as pd
import argparse
import platform
import time
import re
import os

@dataclass
class Place:
    name: str = ""
    address: str = ""
    website: str = ""
    phone_number: str = ""
    email: str = ""
    reviews_count: Optional[int] = None
    reviews_average: Optional[float] = None
    store_shopping: str = "No"
    in_store_pickup: str = "No"
    store_delivery: str = "No"
    place_type: str = ""
    opens_at: str = ""
    introduction: str = ""

# Google lazy-loads the results panel, so a scroll that adds nothing is not
# proof the list ended — it usually means the next batch is still in flight.
# Only give up after this many flat rounds, waiting between each.
MAX_STAGNANT_SCROLLS = 3
SCROLL_SETTLE_MS = 1000

def setup_logging():
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(levelname)s - %(message)s',
    )

# Google separates the open/closed status from the hours with U+22C5 DOT
# OPERATOR in some locales and U+00B7 MIDDLE DOT in others. The two are
# visually identical, so they are spelled as escapes to keep them apart.
OPENS_AT_SEPARATORS = ("\u22c5", "\u00b7")

def parse_opens_at(raw: str) -> str:
    for separator in OPENS_AT_SEPARATORS:
        if separator in raw:
            raw = raw.split(separator, 1)[1]
            break
    return raw.replace("\u202f", "").strip()

def extract_text(page: Page, xpath: str) -> str:
    try:
        if page.locator(xpath).count() > 0:
            return page.locator(xpath).inner_text()
    except Exception as e:
        logging.warning(f"Failed to extract text for xpath {xpath}: {e}")
    return ""

def extract_place(page: Page) -> Place:
    # XPaths
    name_xpath = '//div[@class="TIHn2 "]//h1[@class="DUwDvf lfPIob"]'
    address_xpath = '//button[@data-item-id="address"]//div[contains(@class, "fontBodyMedium")]'
    website_xpath = '//a[@data-item-id="authority"]//div[contains(@class, "fontBodyMedium")]'
    phone_number_xpath = '//button[contains(@data-item-id, "phone:tel:")]//div[contains(@class, "fontBodyMedium")]'
    reviews_count_xpath = '//div[@class="TIHn2 "]//div[@class="fontBodyMedium dmRWX"]//div//span//span//span[@aria-label]'
    reviews_average_xpath = '//div[@class="TIHn2 "]//div[@class="fontBodyMedium dmRWX"]//div//span[@aria-hidden]'
    info1 = '//div[@class="LTs0Rc"][1]'
    info2 = '//div[@class="LTs0Rc"][2]'
    info3 = '//div[@class="LTs0Rc"][3]'
    opens_at_xpath = '//button[contains(@data-item-id, "oh")]//div[contains(@class, "fontBodyMedium")]'
    opens_at_xpath2 = '//div[@class="MkV9"]//span[@class="ZDu9vd"]//span[2]'
    place_type_xpath = '//div[@class="LBgpqf"]//button[@class="DkEaL "]'
    intro_xpath = '//div[@class="WeS02d fontBodyMedium"]//div[@class="PYvSYb "]'

    place = Place()
    place.name = extract_text(page, name_xpath)
    place.address = extract_text(page, address_xpath)
    place.website = extract_text(page, website_xpath)
    place.phone_number = extract_text(page, phone_number_xpath)
    place.place_type = extract_text(page, place_type_xpath)
    place.introduction = extract_text(page, intro_xpath) or "None Found"

    # Reviews Count
    reviews_count_raw = extract_text(page, reviews_count_xpath)
    if reviews_count_raw:
        try:
            temp = reviews_count_raw.replace('\xa0', '').replace('(','').replace(')','').replace(',','')
            place.reviews_count = int(temp)
        except Exception as e:
            logging.warning(f"Failed to parse reviews count: {e}")
    # Reviews Average
    reviews_avg_raw = extract_text(page, reviews_average_xpath)
    if reviews_avg_raw:
        try:
            temp = reviews_avg_raw.replace(' ','').replace(',','.')
            place.reviews_average = float(temp)
        except Exception as e:
            logging.warning(f"Failed to parse reviews average: {e}")
    # Store Info
    for idx, info_xpath in enumerate([info1, info2, info3]):
        info_raw = extract_text(page, info_xpath)
        if info_raw:
            temp = info_raw.split('·')
            if len(temp) > 1:
                check = temp[1].replace("\n", "").lower()
                if 'shop' in check:
                    place.store_shopping = "Yes"
                if 'pickup' in check:
                    place.in_store_pickup = "Yes"
                if 'delivery' in check:
                    place.store_delivery = "Yes"
    # Opens At
    opens_at_raw = extract_text(page, opens_at_xpath) or extract_text(page, opens_at_xpath2)
    if opens_at_raw:
        place.opens_at = parse_opens_at(opens_at_raw)
    return place

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")

# The email regex also matches asset filenames ("logo@2x.png") and the addresses
# baked into analytics, themes and site-builder scripts, none of which reach a
# human. Scanning raw HTML surfaces more of these than scanning text alone.
EMAIL_JUNK_SUFFIXES = (".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".ico", ".css", ".js")
EMAIL_JUNK_DOMAINS = ("sentry.io", "sentry-cdn.com", "wixpress.com", "example.com",
                      "example.org", "example.net", "domain.com", "yourdomain.com",
                      "email.com", "godaddy.com", "squarespace.com", "wordpress.org",
                      "wordpress.com", "w3.org", "schema.org", "googleapis.com",
                      "jquery.com", "github.com", "gravatar.com", "sentry.local",
                      "adobe.com", "cloudflare.com", "elementor.com", "wix.com")

SITE_TIMEOUT_MS = 12000
# domcontentloaded fires before client-rendered markup exists, and the address
# usually lives in a footer that renders late. Without this settle the same
# site yields an address on one run and nothing on the next.
SITE_SETTLE_MS = 5000
CONTACT_LINK_RE = re.compile(r"contact|contacto|contactenos|contactanos", re.I)

def launch_browser(p, headless: bool = False):
    if platform.system() == "Windows":
        browser_path = r"C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe"
        return p.chromium.launch(executable_path=browser_path, headless=headless)
    return p.chromium.launch(headless=headless)

def is_plausible_email(email: str) -> bool:
    email = email.lower()
    if email.endswith(EMAIL_JUNK_SUFFIXES):
        return False
    local, _, domain = email.partition("@")
    if any(junk in domain for junk in EMAIL_JUNK_DOMAINS):
        return False
    # "@2x"/"@3x" retina asset suffixes that survived the extension check.
    if re.fullmatch(r"\d+x", local):
        return False
    return True

def emails_on_page(page: Page) -> Set[str]:
    found: Set[str] = set()
    # mailto: links are the deliberate signal.
    try:
        hrefs = page.eval_on_selector_all(
            'a[href^="mailto:"]', "els => els.map(e => e.getAttribute('href') || '')"
        )
        for href in hrefs:
            address = href[len("mailto:"):].split("?")[0].strip()
            if EMAIL_RE.fullmatch(address):
                found.add(address)
    except Exception as e:
        logging.debug(f"mailto scan failed: {e}")
    # Real addresses often live in markup the user never sees as text — script
    # variables, data attributes, obfuscated spans — so scan the source too.
    for extract in (lambda: page.inner_text("body"), page.content):
        try:
            found.update(EMAIL_RE.findall(extract()))
        except Exception as e:
            logging.debug(f"page scan failed: {e}")
    return {email for email in found if is_plausible_email(email)}

def site_url(website: str) -> str:
    website = website.strip()
    if not website.startswith(("http://", "https://")):
        website = "https://" + website
    return website.rstrip("/")

def candidate_urls(base: str) -> List[str]:
    # Maps hands us a bare host. https can fail on a mismatched certificate
    # while plain http serves the same site, so keep it as a fallback.
    parsed = urlparse(base)
    candidates = [base]
    if not parsed.netloc.startswith("www."):
        candidates.append(f"{parsed.scheme}://www.{parsed.netloc}")
    if parsed.scheme == "https":
        candidates.append(f"http://{parsed.netloc}")
    return candidates

def pick_best_email(emails: Set[str], base: str) -> str:
    # An address on the company's own domain beats a personal gmail scraped
    # from the same page.
    host = urlparse(base).netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    on_domain = sorted(e for e in emails if e.lower().endswith("@" + host))
    return (on_domain or sorted(emails))[0]

def open_page(page: Page, url: str) -> bool:
    try:
        page.goto(url, timeout=SITE_TIMEOUT_MS, wait_until="domcontentloaded")
    except Exception as e:
        logging.debug(f"{url} unreachable: {type(e).__name__}")
        return False
    try:
        page.wait_for_load_state("networkidle", timeout=SITE_SETTLE_MS)
    except Exception:
        pass  # a chatty page never goes idle; scan whatever rendered by now
    return True

def contact_links(page: Page) -> List[str]:
    # Follow the site's own contact link instead of guessing paths: real sites
    # use /contacto/, contacto.html, contacto.php, /es/contact-us and worse.
    try:
        hrefs = page.eval_on_selector_all("a[href]", "els => els.map(e => e.href || '')")
    except Exception:
        return []
    seen: Set[str] = set()
    links: List[str] = []
    for href in hrefs:
        if href.startswith(("http://", "https://")) and CONTACT_LINK_RE.search(href):
            if href not in seen:
                seen.add(href)
                links.append(href)
    return links[:2]

def find_email_for_site(page: Page, website: str) -> str:
    base = site_url(website)
    for url in candidate_urls(base):
        if not open_page(page, url):
            continue
        emails = emails_on_page(page)
        if emails:
            return pick_best_email(emails, base)
        for link in contact_links(page):
            if open_page(page, link):
                emails = emails_on_page(page)
                if emails:
                    return pick_best_email(emails, base)
        return ""  # the site loaded and simply has no address to find
    return ""

def enrich_with_emails(places: List[Place]) -> None:
    targets = [place for place in places if place.website and not place.email]
    if not targets:
        logging.info("No websites to search for emails")
        return
    logging.info(f"Searching {len(targets)} websites for emails")
    with sync_playwright() as p:
        browser = launch_browser(p, headless=True)
        page = browser.new_page()
        try:
            for idx, place in enumerate(targets, 1):
                try:
                    place.email = find_email_for_site(page, place.website)
                except Exception as e:
                    logging.warning(f"Email lookup failed for {place.website}: {e}")
                status = place.email or "no email found"
                logging.info(f"[{idx}/{len(targets)}] {place.website} -> {status}")
        finally:
            browser.close()
    logging.info(f"Found emails for {sum(1 for p in places if p.email)} of {len(places)} places")

def has_contact(place: Place) -> bool:
    return bool(place.phone_number or place.email)

def scrape_places(search_for: str, total: int) -> List[Place]:
    setup_logging()
    places: List[Place] = []
    with sync_playwright() as p:
        browser = launch_browser(p, headless=False)
        page = browser.new_page()
        try:
            page.goto("https://www.google.com/maps/@32.9817464,70.1930781,3.67z?", timeout=60000)
            page.wait_for_timeout(1000)
            page.locator("//form[contains(@jsaction,'searchboxFormSubmit')]//input[@name='q']").fill(search_for)
            page.keyboard.press("Enter")
            page.wait_for_selector('//a[contains(@href, "https://www.google.com/maps/place")]')
            page.hover('//a[contains(@href, "https://www.google.com/maps/place")]')
            previously_counted = 0
            stagnant_rounds = 0
            while True:
                page.mouse.wheel(0, 10000)
                page.wait_for_selector('//a[contains(@href, "https://www.google.com/maps/place")]')
                found = page.locator('//a[contains(@href, "https://www.google.com/maps/place")]').count()
                if found >= total:
                    logging.info(f"Currently Found: {found}")
                    break
                if found == previously_counted:
                    stagnant_rounds += 1
                    if stagnant_rounds >= MAX_STAGNANT_SCROLLS:
                        logging.info(f"Arrived at all available: {found}")
                        break
                    # The count only settles once the lazy-loaded batch lands, so
                    # wait before treating a flat round as the end of the list.
                    page.wait_for_timeout(SCROLL_SETTLE_MS)
                else:
                    stagnant_rounds = 0
                    logging.info(f"Currently Found: {found}")
                previously_counted = found
            listings = page.locator('//a[contains(@href, "https://www.google.com/maps/place")]').all()[:total]
            listings = [listing.locator("xpath=..") for listing in listings]
            logging.info(f"Total Found: {len(listings)}")
            for idx, listing in enumerate(listings):
                try:
                    listing.click()
                    page.wait_for_selector('//div[@class="TIHn2 "]//h1[@class="DUwDvf lfPIob"]', timeout=10000)
                    time.sleep(1.5)  # Give time for details to load
                    place = extract_place(page)
                    if place.name:
                        places.append(place)
                    else:
                        logging.warning(f"No name found for listing {idx+1}, skipping.")
                except Exception as e:
                    logging.warning(f"Failed to extract listing {idx+1}: {e}")
        finally:
            browser.close()
    return places

def drop_uninformative_columns(df: pd.DataFrame) -> pd.DataFrame:
    # A single row makes every column look constant, so there is nothing to learn from it.
    if len(df) < 2:
        return df
    keep = [column for column in df.columns if df[column].nunique(dropna=False) > 1]
    return df[keep] if keep else df

def save_places_to_csv(places: List[Place], output_path: str = "result.csv", append: bool = False):
    df = pd.DataFrame([asdict(place) for place in places])
    if df.empty:
        logging.warning("No data to save. DataFrame is empty.")
        return

    if append and os.path.isfile(output_path):
        # Conform to the header already on disk; dropping columns here would
        # shift values into the wrong columns of the existing file.
        existing_columns = pd.read_csv(output_path, nrows=0).columns.tolist()
        dropped = [column for column in df.columns if column not in existing_columns]
        if dropped:
            logging.warning(f"Columns not in {output_path}, not appended: {', '.join(dropped)}")
        df = df.reindex(columns=existing_columns)
        df.to_csv(output_path, index=False, mode="a", header=False)
    else:
        df = drop_uninformative_columns(df)
        df.to_csv(output_path, index=False, mode="w", header=True)
    logging.info(f"Saved {len(df)} places to {output_path} (append={append})")

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("-s", "--search", type=str, help="Search query for Google Maps")
    parser.add_argument("-t", "--total", type=int, help="Total number of results to scrape")
    parser.add_argument("-o", "--output", type=str, default="result.csv", help="Output CSV file path")
    parser.add_argument("--append", action="store_true", help="Append results to the output file instead of overwriting")
    parser.add_argument("--emails", action="store_true", help="Visit each business website and extract an email address")
    parser.add_argument("--require-contact", action="store_true", help="Drop results that have neither a phone number nor an email")
    args = parser.parse_args()
    search_for = args.search or "turkish stores in toronto Canada"
    total = args.total or 1
    output_path = args.output
    append = args.append
    places = scrape_places(search_for, total)
    if args.emails:
        enrich_with_emails(places)
    if args.require_contact:
        before = len(places)
        places = [place for place in places if has_contact(place)]
        if before - len(places):
            logging.info(f"Dropped {before - len(places)} of {before} places with no phone and no email")
    save_places_to_csv(places, output_path, append=append)

if __name__ == "__main__":
    main()
