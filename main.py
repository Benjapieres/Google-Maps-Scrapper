import logging
from typing import List, Optional
from playwright.sync_api import sync_playwright, Page
from dataclasses import dataclass, asdict
from abc import ABC, abstractmethod
import pandas as pd
import argparse
import platform
import time
import os

# ============================================================================
# Open/Closed Principle (SOLID) - Browser Configuration Strategy
# ============================================================================
# Este patrón permite agregar nuevas plataformas sin modificar el código existente


class BrowserConfig(ABC):
    """Abstract base class for browser configuration strategies."""

    @abstractmethod
    def get_browser_args(self) -> dict:
        """Retorna los argumentos para lanzar el navegador."""
        pass

    @abstractmethod
    def get_search_url(self, query: str) -> str:
        """Retorna la URL de búsqueda."""
        pass

    @abstractmethod
    def get_cookie_button_text(self) -> str:
        """Retorna el texto del botón de cookies según el idioma."""
        pass


class WindowsBrowserConfig(BrowserConfig):
    """Configuración original para Windows."""

    CHROME_PATH = r"C:\Program Files\Google\Chrome\Application\chrome.exe"

    def get_browser_args(self) -> dict:
        return {"executable_path": self.CHROME_PATH, "headless": False}

    def get_search_url(self, query: str) -> str:
        # Original: navegación por input
        return "https://www.google.com/maps/@32.9817464,70.1930781,3.67z?"

    def get_cookie_button_text(self) -> str:
        return "Accept all"  # Inglés


class LinuxBrowserConfig(BrowserConfig):
    """Configuración optimizada para Linux/Fedora."""

    BROWSER_ARGS = [
        "--no-sandbox",
        "--disable-setuid-sandbox",
        "--disable-dev-shm-usage",
        "--disable-blink-features=AutomationControlled",
    ]

    USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"

    def get_browser_args(self) -> dict:
        return {
            "headless": False,
            "args": self.BROWSER_ARGS + [f"--user-agent={self.USER_AGENT}"],
        }

    def get_search_url(self, query: str) -> str:
        # Optimizado: navegación directa a URL de búsqueda
        encoded = query.replace(" ", "+")
        return f"https://www.google.com/maps/search/{encoded}"

    def get_cookie_button_text(self) -> str:
        return "Aceptar todo"  # Español


class MacBrowserConfig(BrowserConfig):
    """Configuración para macOS."""

    def get_browser_args(self) -> dict:
        return {"headless": False}

    def get_search_url(self, query: str) -> str:
        encoded = query.replace(" ", "+")
        return f"https://www.google.com/maps/search/{encoded}"

    def get_cookie_button_text(self) -> str:
        return "Accept all"


def get_browser_config() -> BrowserConfig:
    """Factory: retorna la configuración apropiada según el sistema operativo."""
    system = platform.system()
    if system == "Windows":
        return WindowsBrowserConfig()
    elif system == "Linux":
        return LinuxBrowserConfig()
    elif system == "Darwin":
        return MacBrowserConfig()
    else:
        # Default: usar Linux config como fallback
        return LinuxBrowserConfig()


# ============================================================================
# Modelos de Datos
# ============================================================================


@dataclass
class Place:
    name: str = ""
    address: str = ""
    website: str = ""
    phone_number: str = ""
    reviews_count: Optional[int] = None
    reviews_average: Optional[float] = None
    store_shopping: str = "No"
    in_store_pickup: str = "No"
    store_delivery: str = "No"
    place_type: str = ""
    opens_at: str = ""
    introduction: str = ""


def setup_logging():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(levelname)s - %(message)s",
    )


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
    address_xpath = (
        '//button[@data-item-id="address"]//div[contains(@class, "fontBodyMedium")]'
    )
    website_xpath = (
        '//a[@data-item-id="authority"]//div[contains(@class, "fontBodyMedium")]'
    )
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
            temp = (
                reviews_count_raw.replace("\xa0", "")
                .replace("(", "")
                .replace(")", "")
                .replace(",", "")
            )
            place.reviews_count = int(temp)
        except Exception as e:
            logging.warning(f"Failed to parse reviews count: {e}")
    # Reviews Average
    reviews_avg_raw = extract_text(page, reviews_average_xpath)
    if reviews_avg_raw:
        try:
            temp = reviews_avg_raw.replace(" ", "").replace(",", ".")
            place.reviews_average = float(temp)
        except Exception as e:
            logging.warning(f"Failed to parse reviews average: {e}")
    # Store Info
    for idx, info_xpath in enumerate([info1, info2, info3]):
        info_raw = extract_text(page, info_xpath)
        if info_raw:
            temp = info_raw.split("·")
            if len(temp) > 1:
                check = temp[1].replace("\n", "").lower()
                if "shop" in check:
                    place.store_shopping = "Yes"
                if "pickup" in check:
                    place.in_store_pickup = "Yes"
                if "delivery" in check:
                    place.store_delivery = "Yes"
    # Opens At
    opens_at_raw = extract_text(page, opens_at_xpath)
    if opens_at_raw:
        opens = opens_at_raw.split("⋅")
        if len(opens) > 1:
            place.opens_at = opens[1].replace("\u202f", "")
        else:
            place.opens_at = opens_at_raw.replace("\u202f", "")
    else:
        opens_at2_raw = extract_text(page, opens_at_xpath2)
        if opens_at2_raw:
            opens = opens_at2_raw.split("⋅")
            if len(opens) > 1:
                place.opens_at = opens[1].replace("\u202f", "")
            else:
                place.opens_at = opens_at2_raw.replace("\u202f", "")
    return place


def scrape_places(search_for: str, total: int) -> List[Place]:
    setup_logging()

    # Open/Closed Principle: Obtener configuración según plataforma (Strategy Pattern)
    config = get_browser_config()
    browser_args = config.get_browser_args()
    cookie_button_text = config.get_cookie_button_text()
    search_url = config.get_search_url(search_for)

    places: List[Place] = []
    with sync_playwright() as p:
        browser = p.chromium.launch(**browser_args)

        page = browser.new_page()
        page.set_default_timeout(90000)

        try:
            # Navegación directa usando la abstracción (Open/Closed Principle)
            page.goto(search_url, timeout=90000)
            page.wait_for_load_state("networkidle", timeout=60000)
            page.wait_for_timeout(4000)

            # Manejo automático del popup de cookies (usa abstracción)
            try:
                accept_btn = page.locator(f'button:has-text("{cookie_button_text}")')
                if accept_btn.count() > 0:
                    accept_btn.first.click(timeout=5000)
                    logging.info("✅ Popup de cookies aceptado automáticamente")
                    page.wait_for_timeout(2000)
            except:
                pass  # No había popup o ya estaba aceptado

            # Scroll para cargar resultados
            previously_counted = 0
            while True:
                page.mouse.wheel(0, 8000)
                page.wait_for_timeout(2500)
                found = page.locator(
                    '//a[contains(@href, "https://www.google.com/maps/place")]'
                ).count()
                logging.info(f"Currently Found: {found}")
                if found >= total:
                    break
                if found == previously_counted:
                    logging.info("✅ Llegamos al final de los resultados disponibles")
                    break
                previously_counted = found

            listings = page.locator(
                '//a[contains(@href, "https://www.google.com/maps/place")]'
            ).all()[:total]
            listings = [listing.locator("xpath=..") for listing in listings]
            logging.info(f"Total Found: {len(listings)}")

            for idx, listing in enumerate(listings):
                try:
                    listing.click()
                    page.wait_for_selector(
                        '//div[@class="TIHn2 "]//h1[@class="DUwDvf lfPIob"]',
                        timeout=15000,
                    )
                    time.sleep(2)
                    place = extract_place(page)
                    if place.name:
                        places.append(place)
                        logging.info(f"✅ Extraído: {place.name}")
                    else:
                        logging.warning(f"No name found for listing {idx + 1}")
                except Exception as e:
                    logging.warning(f"Failed to extract listing {idx + 1}: {e}")

        finally:
            browser.close()

    return places


def save_places_to_csv(
    places: List[Place], output_path: str = "result.csv", append: bool = False
):
    df = pd.DataFrame([asdict(place) for place in places])
    if not df.empty:
        for column in df.columns:
            if df[column].nunique() == 1:
                df.drop(column, axis=1, inplace=True)
        file_exists = os.path.isfile(output_path)
        mode = "a" if append else "w"
        header = not (append and file_exists)
        df.to_csv(output_path, index=False, mode=mode, header=header)
        logging.info(f"Saved {len(df)} places to {output_path} (append={append})")
    else:
        logging.warning("No data to save. DataFrame is empty.")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("-s", "--search", type=str, help="Search query for Google Maps")
    parser.add_argument(
        "-t", "--total", type=int, help="Total number of results to scrape"
    )
    parser.add_argument(
        "-o", "--output", type=str, default="result.csv", help="Output CSV file path"
    )
    parser.add_argument(
        "--append",
        action="store_true",
        help="Append results to the output file instead of overwriting",
    )
    args = parser.parse_args()
    search_for = args.search or "turkish stores in toronto Canada"
    total = args.total or 1
    output_path = args.output
    append = args.append
    places = scrape_places(search_for, total)
    save_places_to_csv(places, output_path, append=append)


if __name__ == "__main__":
    main()
