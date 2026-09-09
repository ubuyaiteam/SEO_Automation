import json
import time
import threading
import os, sys
import re

import gzip
import requests
from win10toast import ToastNotifier
from googleapiclient.errors import HttpError
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
import tkinter as tk
from tkinter import ttk, scrolledtext, messagebox
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from webdriver_manager.chrome import ChromeDriverManager
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from google.auth.transport.requests import Request
from selenium.common.exceptions import TimeoutException
from googleapiclient.discovery import build
import datetime
import ctypes
import tempfile
from app1 import get_gsc_data
import shutil
toaster = ToastNotifier()
def resource_path(filename):
    if hasattr(sys, '_MEIPASS'):
        return os.path.join(sys._MEIPASS, filename)
    return os.path.join(os.path.abspath("."), filename)


PIL_AVAILABLE = True
try:
    from PIL import Image, ImageTk
except Exception:
    PIL_AVAILABLE = False

SEPARATOR = "─" * 50

def mkdirr():
    output_dir = tempfile.mkdtemp()
    return output_dir



def show_notification(title, message):
    pass

# ----------------------------
# SELENIUM FUNCTIONS
# ----------------------------
def setup_driver(output_folder):
    options = Options()
    options.add_argument("--headless=new")
    options.add_argument("--disable-gpu")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-infobars")
    options.add_argument("--disable-extensions")
    options.add_argument("--window-size=1920,1080")
    options.add_argument("--blink-settings=imagesEnabled=false")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_argument("--disable-logging")
    options.add_argument("--log-level=3")
    options.add_argument("--silent")
    options.add_experimental_option("excludeSwitches", ["enable-automation", "enable-logging"])
    options.add_experimental_option("useAutomationExtension", False)
    # Configure download preferences
    output_dir = output_folder
    prefs = {
        "download.default_directory": output_dir,
        "download.prompt_for_download": False,
        "download.directory_upgrade": True,
        "safebrowsing.enabled": True
    }
    options.add_experimental_option("prefs", prefs)
    return webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=options)

def load_cookies_from_text(driver, cookie_text, log):
    driver.get("https://www.google.com/")
    time.sleep(2)
    try:
        cookies = json.loads(cookie_text)
    except json.JSONDecodeError:
        log("🔴 Cookie JSON format is invalid.", "error")
        return False

    for cookie in cookies:
        for key in ['sameSite', 'storeId', 'id', 'hostOnly']:
            cookie.pop(key, None)
        cookie['domain'] = '.google.com'
        try:
            driver.add_cookie(cookie)
        except Exception as e:
            log("🟡 Skipping invalid cookie entry.", "warn")
    time.sleep(3)
    log("🟢 Google cookies loaded successfully.", "success")
    return True

def extract_urls_from_sitemap(property, log):
    sitemap_url = f"https://www.{property}/sitemap.xml"
    """Fetch sitemap or sitemap index and return <loc> URLs."""
    try:
        print(f"Fetching sitemap from: {sitemap_url}")

        headers = {
            "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                           "AppleWebKit/537.36 (KHTML, like Gecko) "
                           "Chrome/120.0.0.0 Safari/537.36"),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Encoding": "gzip, deflate, br",
            "Accept-Language": "en-US,en;q=0.9",
        }

        resp = requests.get(sitemap_url, headers=headers, timeout=60)
        resp.raise_for_status()

        # handle gz
        if sitemap_url.endswith(".gz"):
            print("Detected GZipped sitemap. Decompressing…")
            xml_content = gzip.decompress(resp.content).decode("utf-8", errors="replace")
        else:
            # if server returned gz anyway
            content = resp.content
            try:
                xml_content = gzip.decompress(content).decode("utf-8")
            except Exception:
                xml_content = resp.text

        urls = re.findall(r"<loc>(.*?)</loc>", xml_content)
        print(f"Found {len(urls)} URLs in sitemap.")
        return urls
    except Exception as e:
        print(f"Error fetching sitemap: {e}")
        return []

def extract_sitemaps(driver, property, log):
    driver.get(f"https://search.google.com/search-console/sitemaps?resource_id=sc-domain:{property}")
    wait = WebDriverWait(driver, 80)
    js = """const list = document.querySelectorAll("div[role='option'][data-value='100']"); 
            if (list && list.length > 0){list[0].click(); return true;} return false;"""
    try:
        dropdown = wait.until(EC.element_to_be_clickable((By.CSS_SELECTOR, "div[role='listbox'][aria-label='Number of rows per page']")))
        dropdown.click()
        time.sleep(2)
        driver.execute_script(js)
        time.sleep(3)
        spans = wait.until(EC.presence_of_all_elements_located((By.CSS_SELECTOR, "td.RVEMNe")))
        log("✅ Sitemaps extracted successfully!", "success")
        return [span.text.strip() for span in spans if span.text.strip()]
    except Exception as e:
        log("🔴 Failed to extract sitemap list.")
        return []


def start_validation(driver, property, sitemaps, log):
    try:
        log("✅ Step 1: Validating Page Index Errors...")
        driver.get(f"https://search.google.com/search-console/index?resource_id=sc-domain:{property}")
        wait = WebDriverWait(driver, 80)
        try:
            WebDriverWait(driver, 10).until(EC.presence_of_all_elements_located((By.CLASS_NAME, "f1SRCe")))
            log("✅ No Page Indexing errors found.")
        except:
            rows = wait.until(EC.presence_of_all_elements_located((By.CSS_SELECTOR, "tr.nJ0sOc")))
            errors = {}
            restricted = ["Excluded by `noindex` tag", "Blocked by robots.txt","Blocked due to access forbidden (403)","Blocked due to other 4xx issue"]
            for row in rows:
                col = row.find_elements(By.TAG_NAME, "td")
                if "Failed" in col[2].text and col[0].text not in restricted:
                    errors[col[0].text] = "Failed"
                elif "Not Started" in col[2].text:
                    errors[col[0].text] = "Not Started"

            for key,value in errors.items():
                rows = wait.until(EC.presence_of_all_elements_located((By.CSS_SELECTOR, "tr.nJ0sOc")))
                for row in rows:
                    col = row.find_elements(By.TAG_NAME, "td")
                    if key in col[0].text and  value == "Failed":
                        driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", row)
                        time.sleep(1)
                        driver.execute_script("arguments[0].click();", row)
                        log("🖱️ Selected failed row.")
                        see_details_button = WebDriverWait(driver, 80).until(
                            EC.element_to_be_clickable((By.XPATH, "//span[text()='see details']/ancestor::div[@role='button']"))
                        )
                        see_details_button.click()
                        log("🖱️ Opening 'See Details' panel...")
                        start_validation_button = WebDriverWait(driver, 80).until(
                            EC.element_to_be_clickable((By.XPATH, "//span[text()='start new validation']/ancestor::div[@role='button']"))
                        )
                        start_validation_button.click()
                        log("🖱️ Starting new validation...")
                        time.sleep(1)
                        elem = driver.find_element(By.CSS_SELECTOR, "div.uW2Fw-IE5DDf[jsname='GGAcbc']")
                        driver.execute_script("arguments[0].click();", elem)
                        time.sleep(1)
                        driver.get(f"https://search.google.com/search-console/index?resource_id=sc-domain:{property}")
                        break

                    elif key in col[0].text and  value == "Not Started":
                        driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", row)
                        time.sleep(1)
                        driver.execute_script("arguments[0].click();", row)
                        log("🖱️ Selected 'Not Started' row.")
                        validate_fix_button = WebDriverWait(driver, 80).until(
                            EC.element_to_be_clickable((By.XPATH, "//span[text()='validate fix']/ancestor::div[@role='button']"))
                        )
                        validate_fix_button.click()
                        log("🖱️ Clicked 'Validate fix'.")
                        time.sleep(1)
                        elem = driver.find_element(By.CSS_SELECTOR, "div.uW2Fw-IE5DDf[jsname='GGAcbc']")
                        driver.execute_script("arguments[0].click();", elem)
                        time.sleep(1)
                        driver.get(f"https://search.google.com/search-console/index?resource_id=sc-domain:{property}")
                        break

            log("✅ All Page Indexing errors resolved.")

        log("✅ Step 2: Moving to Sitemap Validation Section...")
        total = len(sitemaps)
        if total == 0:
            log("❌ No sitemap entries found for inner validation.", "error")
            driver.quit()
            return False  # 🚩 Return False on empty sitemaps

        log(f"📄 Found {total} sitemaps.")
        for i, sitemap_url in enumerate(sitemaps, start=1):
            log(f"\n🔍 Checking sitemap ({i}/{total}): {sitemap_url}")
            driver.get(f"https://search.google.com/search-console/index?resource_id=sc-domain:{property}&pages=SITEMAP&sitemap={sitemap_url}")
            wait = WebDriverWait(driver, 30)
            try:
                rows = wait.until(EC.presence_of_all_elements_located((By.CSS_SELECTOR, "tr.nJ0sOc")))
                errors = {}
                restricted = ["Excluded by `noindex` tag", "Blocked by robots.txt","Blocked due to access forbidden (403)","Blocked due to other 4xx issue"]
                for row in rows:
                    col = row.find_elements(By.TAG_NAME, "td")
                    if "Failed" in col[2].text and col[0].text not in restricted:
                        errors[col[0].text] = "Failed"
                    elif "Not Started" in col[2].text:
                        errors[col[0].text] = "Not Started"

                for key,value in errors.items():
                    rows = wait.until(EC.presence_of_all_elements_located((By.CSS_SELECTOR, "tr.nJ0sOc")))
                    for row in rows:
                        col = row.find_elements(By.TAG_NAME, "td")
                        if key in col[0].text and  value == "Failed":
                            driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", row)
                            time.sleep(1)
                            driver.execute_script("arguments[0].click();", row)
                            log("🖱️ Selected failed row.")
                            see_details_button = WebDriverWait(driver, 80).until(
                            EC.element_to_be_clickable((By.XPATH, "//span[text()='see details']/ancestor::div[@role='button']"))
                            )
                            see_details_button.click()
                            log("🖱️ Opening 'See Details' panel...")
                            start_validation_button = WebDriverWait(driver, 80).until(
                                EC.element_to_be_clickable((By.XPATH, "//span[text()='start new validation']/ancestor::div[@role='button']"))
                            )
                            start_validation_button.click()
                            log("🖱️ Starting new validation...")
                            time.sleep(1)
                            elem = driver.find_element(By.CSS_SELECTOR, "div.uW2Fw-IE5DDf[jsname='GGAcbc']")
                            driver.execute_script("arguments[0].click();", elem)
                            time.sleep(1)
                            driver.get(f"https://search.google.com/search-console/index?resource_id=sc-domain:{property}&pages=SITEMAP&sitemap={sitemap_url}")
                            break

                        elif key in col[0].text and  value == "Not Started":
                            driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", row)
                            time.sleep(1)
                            driver.execute_script("arguments[0].click();", row)
                            log("🖱️ Selected 'Not Started' row.")
                            validate_fix_button = WebDriverWait(driver, 80).until(
                                EC.element_to_be_clickable((By.XPATH, "//span[text()='validate fix']/ancestor::div[@role='button']"))
                            )
                            validate_fix_button.click()
                            log("🖱️ Clicked 'Validate fix'.")
                            time.sleep(1)
                            elem = driver.find_element(By.CSS_SELECTOR, "div.uW2Fw-IE5DDf[jsname='GGAcbc']")
                            driver.execute_script("arguments[0].click();", elem)
                            time.sleep(1)
                            driver.get(f"https://search.google.com/search-console/index?resource_id=sc-domain:{property}&pages=SITEMAP&sitemap={sitemap_url}")
                            break
            except:
                WebDriverWait(driver, 8).until(EC.presence_of_all_elements_located((By.CLASS_NAME, "f1SRCe")))
                log("✅ No failed rows found — skipping sitemap.")
        
        return True  # 🟢 Success

    except Exception as e:
        log(f"❌ Error during validation: {e}", "error")
        return False  # 🚩 

def submit_all_sitemaps(property_name: str, sitemaps: list, choice: int, log=print):
    """
    Submit all sitemap URLs to Google Search Console.

    :param property_name: Domain only (e.g. "u-buy.tv")
    :param sitemaps: List of sitemap URLs
    :param choice: 1 = gschigh credentials, 2 = gsclow credentials
    :param log: Logger function (default = print)
    :return: True if no errors occurred, False otherwise
    """
    ok = True  # ✅ Track overall success

    # ✅ STEP 1 — Set correct JSON files based on radio button
    if choice == 1:
        CREDENTIALS_FILE = resource_path("credentials_gschigh.json")
        TOKEN_FILE = resource_path("token_gschigh.json")
        log("✅ Using: gschigh credentials")
    elif choice == 2:
        CREDENTIALS_FILE = resource_path("credentials_gsclow.json")
        TOKEN_FILE = resource_path("token_gsclow.json")
        log("✅ Using: gsclow credentials")
    else:
        log("❌ Invalid credential choice!")
        return False

    SCOPES = ["https://www.googleapis.com/auth/webmasters"]

    # ✅ STEP 2 — Build property for GSC
    site_url = f"sc-domain:{property_name}"
    log(f"🌍 Submitting for property: {site_url}")

    # ✅ STEP 3 - Authenticate to GSC
    def get_gsc_service():
        creds = None

        if os.path.exists(TOKEN_FILE):
            creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)

        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                try:
                    creds.refresh(Request())   # ✅ FIXED REFRESH
                except Exception:
                    creds = None

            if not creds:
                if not os.path.exists(CREDENTIALS_FILE):
                    raise FileNotFoundError(f"{CREDENTIALS_FILE} not found!")

                flow = InstalledAppFlow.from_client_secrets_file(CREDENTIALS_FILE, SCOPES)
                
                # Intercept the browser launch
                import webbrowser
                _old_get = webbrowser.get
                class AuthBrowser:
                    def open(self, url, new=0, autoraise=True):
                        root.clipboard_clear()
                        root.clipboard_append(url)
                        def display_alert():
                            try:
                                show_notification("Google Auth Needed", "Authorization URL copied! Please paste into your Chrome window.")
                            except Exception: pass
                            
                            # Force the popup onto the main active screen over all other apps
                            root.attributes('-topmost', True)
                            root.lift()
                            messagebox.showinfo(
                                "Google Authorization Required",
                                "The authorization URL has been COPIED to your clipboard!\n\n"
                                "Please PASTE it into the specific Chrome window/profile where you are logged in for this domain.",
                                parent=root
                            )
                            # Remove the 'always on top' lock once they click OK
                            root.attributes('-topmost', False)
                            
                        root.after(0, display_alert)
                        return True
                
                webbrowser.get = lambda using=None: AuthBrowser()
                try:
                    creds = flow.run_local_server(port=0)
                finally:
                    webbrowser.get = _old_get

                with open(TOKEN_FILE, "w") as token:
                    token.write(creds.to_json())

        return build("webmasters", "v3", credentials=creds, cache_discovery=False)
    
    # ✅ Create service
    try:
        service = get_gsc_service()
        log("✅ Google Search Console authorization successful.")
    except Exception as e:
        log(f"❌ GSC Auth error: {e}")
        return False

    # ✅ STEP 4 — Submit every sitemap
    def submit_sitemap(sitemap_url):
        nonlocal ok  # allow modifying the variable from outer scope
        try:
            service.sitemaps().submit(siteUrl=site_url, feedpath=sitemap_url).execute()
            log(f"✅ Successfully submitted: {sitemap_url}")
        except HttpError as e:
            status = getattr(e, "status_code", None) or (e.resp.status if hasattr(e, "resp") else "unknown")
            try:
                payload = json.loads(e.content.decode())
            except:
                payload = {"error": str(e)}
            log(f"❌ HTTP {status} | {payload}")
            ok = False  # 🚩 Error detected
        except Exception as e:
            log(f"❌ Unknown error: {e}")
            ok = False  # 🚩 Error detected

    # ✅ STEP 5 — Process sitemap list
    log(f"🔍 Found {len(sitemaps)} sitemaps to submit…")

    for sm in sitemaps:
        submit_sitemap(sm)

    # Clean up token file so next run/domain forces new auth
    if os.path.exists(TOKEN_FILE):
        try:
            os.remove(TOKEN_FILE)
            log(f"🗑️ Token file deleted: {os.path.basename(TOKEN_FILE)}")
        except Exception as e:
            log(f"⚠️ Failed to delete token file: {e}")

    return ok

def run_bing_process(property_val, bing_cookie_text, log, latestSitemap, output_folder):
    driver = None  # Initialize to None to prevent errors in 'finally' block

    # --- Helper Functions ---
    def overlay_present(driver):
        """Checks if Bing's dark overlay is blocking clicks."""
        try:
            overlays = driver.find_elements(By.CSS_SELECTOR, "div.ms-Overlay.ms-Overlay--dark")
            return any(o.is_displayed() for o in overlays)
        except:
            return False

    def is_error_500(driver):
        """Checks for Bing server errors."""
        try:
            return "Error500" in driver.current_url or "Error/Error500" in driver.current_url
        except:
            return False

    def safe_reload(driver):
        """Reloads the page and waits for it to stabilize."""
        log("🔄 Reloading page to clear errors/overlays...", "warning")
        try:
            driver.get(f"https://www.bing.com/webmasters/sitemaps?siteUrl=https://{property_val}/")
            
            # Wait for body
            WebDriverWait(driver, 20).until(EC.presence_of_element_located((By.TAG_NAME, "body")))
            
            # Wait for overlays to disappear
            try:
                WebDriverWait(driver, 10).until(
                    EC.invisibility_of_element_located((By.CSS_SELECTOR, "div.ms-Overlay"))
                )
            except:
                pass  # Proceed even if timeout, overlay might be gone
            
            time.sleep(2)  # Allow DOM to settle
        except Exception as e:
            log(f"⚠️ Reload failed: {e}", "warning")

    def robust_enter_text(driver, element, text):
        """
        Tries to enter text normally. If that fails or gets stuck, forces it via JavaScript.
        """
        # Attempt 1: Standard Clear and Type
        try:
            element.clear()
        except:
            pass 
        
        element.send_keys(text)
        time.sleep(0.5) 
        
        # Check if successful
        if element.get_attribute("value") == text:
            return True

        # Attempt 2: JS Injection (The Fix for "Stuck" keys)
        log(f"⚠️ Standard typing failed for {text}, trying JS injection...", "warning")
        try:
            driver.execute_script("""
                arguments[0].value = arguments[1];
                arguments[0].dispatchEvent(new Event('input', { bubbles: true }));
                arguments[0].dispatchEvent(new Event('change', { bubbles: true }));
                arguments[0].blur();
            """, element, text)
            time.sleep(0.5)
            return element.get_attribute("value") == text
        except Exception as e:
            log(f"❌ JS Injection failed: {e}", "error")
            return False

    # --- Main Logic ---
    ok = True
    try:
        log("🌐 Starting Bing automation...", "info")
        driver = setup_driver(output_folder) # Ensure this function is available
        sitemaps = latestSitemap
        
        if not sitemaps:
            log("❌ No sitemap URLs found.", "error")
            return False

        total = len(sitemaps)

        # 1. Open Bing Domain (Required to set cookies)
        driver.get("https://www.bing.com/")
        WebDriverWait(driver, 10).until(EC.presence_of_element_located((By.TAG_NAME, "body")))

        # 2. Load Cookies
        try:
            cookies = json.loads(bing_cookie_text.strip())
            for cookie in cookies:
                # Remove fields that often cause Selenium errors
                for key in ["sameSite", "storeId", "id", "hostOnly", "expirationDate"]: 
                    cookie.pop(key, None) 
                try:
                    driver.add_cookie(cookie)
                except:
                    pass
            log("✅ Bing cookies loaded.", "success")
        except Exception as e:
            log(f"❌ Failed to parse/load cookies: {e}", "error")
            return False

        # 3. Open Webmaster Tools
        driver.get(f"https://www.bing.com/webmasters/sitemaps?siteUrl=https://{property_val}/")

        # 4. Check Auth
        try:
            container = WebDriverWait(driver, 15).until(
                EC.presence_of_element_located((By.CLASS_NAME, "containerLayout"))
            )
            if "User is unauthorized" in container.text or "Sign In" in driver.title:
                log("❌ Cookies expired or login failed.", "error")
                return False
            log("✅ Logged in successfully!", "success")
        except Exception as e:
            log(f"❌ Login check failed (Page load issue): {e}", "error")
            return False

        # ---------- SUBMISSION LOOP ----------
        current_index = 0
        max_retries_per_item = 3
        current_item_retries = 0

        while current_index < total:
            sitemap = sitemaps[current_index]

            try:
                # 🚨 Loop Safety: Prevent infinite retries on one item
                if current_item_retries >= max_retries_per_item:
                    log(f"❌ Skipping {sitemap} after {max_retries_per_item} failed attempts.", "error")
                    current_index += 1
                    current_item_retries = 0
                    ok = False
                    continue

                # 🚨 Handle Bing crash page
                if is_error_500(driver):
                    log("🚨 Bing Error500 detected.", "warning")
                    safe_reload(driver)
                    current_item_retries += 1
                    continue 

                # 🚨 Handle overlay
                if overlay_present(driver):
                    safe_reload(driver)
                    current_item_retries += 1
                    continue

                # Open submit dialog
                try:
                    submit_btn = WebDriverWait(driver, 10).until(
                        EC.element_to_be_clickable((By.XPATH, "//button[.//span[text()='Submit sitemap']]"))
                    )
                    driver.execute_script("arguments[0].click();", submit_btn)
                except Exception:
                    # If button not found, we might need a reload
                    raise Exception("Submit button not found or not clickable")
                
                # Wait for Input Box
                input_box = WebDriverWait(driver, 10).until(
                    EC.visibility_of_element_located((By.XPATH, "//input[@aria-label='Sitemap URL']"))
                )
                
                # ✅ Enter Text Robustly (Fixes the stuck/incomplete typing)
                if not robust_enter_text(driver, input_box, sitemap):
                    raise Exception("Failed to verify sitemap URL in input box")

                # Submit Now
                submit_now = WebDriverWait(driver, 10).until(
                    EC.element_to_be_clickable((By.XPATH, "//button[.//span[text()='Submit']]"))
                )
                driver.execute_script("arguments[0].click();", submit_now)
                
                log(f"✅ ({current_index+1}/{total}) Submitted: {sitemap}", "success")

                # 🛑 CRITICAL: Wait for modal to disappear before next loop
                try:
                    WebDriverWait(driver, 5).until(
                        EC.invisibility_of_element_located((By.XPATH, "//div[@role='dialog']"))
                    )
                except:
                    pass 

                current_index += 1
                current_item_retries = 0 # Reset retry count on success
                time.sleep(3) # Small buffer

            except Exception as e:
                err_msg = str(e)
                # Recoverable UI issues
                if "element click intercepted" in err_msg or overlay_present(driver) or is_error_500(driver):
                    log(f"⚠️ UI Interference on {sitemap}, retrying...", "warning")
                    safe_reload(driver)
                    current_item_retries += 1
                    continue

                log(f"❌ Failed sitemap: {sitemap} → {e}", "error")
                ok = False 
                # If it's a critical error not fixed by reload, skip item
                current_index += 1 
                current_item_retries = 0

        if ok:
            log("🎉 All Bing sitemaps submitted successfully!", "success")

    except Exception as e:
        log(f"❌ Unexpected fatal error: {e}", "error")
        ok = False

    finally:
        if driver:
            try:
                driver.quit()
            except:
                pass

    return ok
# ----------------------------
# TKINTER GUI APP
# ----------------------------
def run_bot(property_entry, cookie_textbox, progressbar, logbox, start_btn, selected_option, bing_cookie_textbox,chk1, chk2, chk3, chk4):
    
    def clear_logbox():
        logbox.config(state=tk.NORMAL)
        logbox.delete("1.0", tk.END)
        logbox.config(state=tk.DISABLED)

    def log_header(log, title):
        log(SEPARATOR, "info")
        log(title, "header")
        log(SEPARATOR, "info")
    
    
    def log(msg, level="info"):
        timestamp = datetime.datetime.now().strftime("[%H:%M:%S] ")

        # choose tag
        tag = {
            "info": "info",
            "success": "success",
            "error": "error",
            "warn": "warn",
            "header": "header"
        }.get(level, "info")

        # Add timestamp + message
        root.after(0, lambda: logbox_write(timestamp, "time"))
        root.after(0, lambda: logbox_write(msg, tag))



    def logbox_write(msg, tag=None):
        logbox.config(state=tk.NORMAL)

        # 🔥 Auto-trim logs to keep UI fast
        lines = int(logbox.index("end-1c").split(".")[0])
        if lines > 3000:  
            logbox.delete("1.0", "500.0")   # delete top 500 lines

        logbox.insert(tk.END, msg + "\n", tag)
        logbox.see(tk.END)
        logbox.config(state=tk.DISABLED)



    def task():
        root.after(0, lambda: clear_logbox())
        start_time = time.time()
        # --- DYNAMIC WEIGHT SETUP (depends on how many tasks are active) ---
        # we'll compute weights so selected tasks share 100% evenly
        root.after(0, lambda: stat_indexed.config(text="--"))
        root.after(0, lambda: stat_non_indexed.config(text="--"))
        root.after(0, lambda: stat_today_total.config(text="--"))
        root.after(0, lambda: stat_today_avg.config(text="--"))
        root.after(0, lambda: stat_seven_total.config(text="--"))
        root.after(0, lambda: stat_seven_avg.config(text="--"))
        root.after(0, lambda: property_entry.config(state="disabled"))
        root.after(0, lambda: cookie_textbox.config(state="disabled"))
        root.after(0, lambda: bing_cookie_textbox.config(state="disabled"))

        bing_flag = chk1.get()
        gse_flag = chk2.get()
        gsc_submit_flag = chk3.get()
        inner_validation_flag = chk4.get()

        # prepare active tasks list and count
        active_flags = {
            "bing": bool(bing_flag),
            "gse": bool(gse_flag),
            "gsc_submit": bool(gsc_submit_flag),
            "inner_validation": bool(inner_validation_flag)
        }

        active_count = sum(1 for v in active_flags.values() if v)

        
        if active_count == 0:
            root.after(0, lambda: messagebox.showerror("Error", "Select at least one task!"))
            root.after(0, lambda: start_btn.config(state=tk.NORMAL))
            root.after(0, lambda: property_entry.config(state="normal"))
            root.after(0, lambda: cookie_textbox.config(state="normal"))
            root.after(0, lambda: bing_cookie_textbox.config(state="normal"))
            return

        
        root.after(0, lambda: progressbar.config(value=0))
        progressbar.config(maximum=100)

        
        per_task_weight = 100.0 / active_count
        task_weights = {}
        for k, active in active_flags.items():
            task_weights[k] = per_task_weight if active else 0.0

        total_progress = 0.0

        def add_progress(task_name, percent):
            """percent: 0..100 fraction of this task's weight to add"""
            nonlocal total_progress
            weight = task_weights.get(task_name, 0.0)
            increment = (percent / 100.0) * weight
            total_progress += increment
            
            if total_progress > 100:
                total_progress = 100.0
            root.after(0, lambda: progressbar.config(value=total_progress))

        def update_stats_safe(result):
            root.after(0, lambda: stat_indexed.config(text=result["indexed"]))
            root.after(0, lambda: stat_non_indexed.config(text=result["non_indexed"]))
            root.after(0, lambda: stat_today_total.config(text=result["today_total_requests"]))
            root.after(0, lambda: stat_today_avg.config(text=result["today_avg_response_ms"]))
            root.after(0, lambda: stat_seven_total.config(text=result["seven_days_total_requests"]))
            root.after(0, lambda: stat_seven_avg.config(text=result["seven_days_avg_response_ms"]))

        root.after(0, lambda: start_btn.config(state=tk.DISABLED))

        property_val = property_entry.get().strip()
        cookie_text = cookie_textbox.get("1.0", tk.END).strip()
        bing_cookie_text = bing_cookie_textbox.get("1.0", tk.END).strip()

        if not property_val:
            root.after(0, lambda: messagebox.showerror(
                "Error", "Please fill property!")
            )
            root.after(0, lambda: start_btn.config(state=tk.NORMAL))
            root.after(0, lambda: property_entry.config(state="normal"))
            root.after(0, lambda: cookie_textbox.config(state="normal"))
            root.after(0, lambda: bing_cookie_textbox.config(state="normal"))
            return

        log("🌐 Initializing browser environment...", "info")
        output_folder = mkdirr()
        log("🟢 Browser initialized successfully.", "success")
        latestSitemap = extract_urls_from_sitemap(property_val, log)
        log(f"📄 Found {len(latestSitemap)} URLs in sitemap.")
        b = g = v = True
        
        if active_flags["bing"]:
            add_progress("bing", 5)   
            b = run_bing_process(property_val, bing_cookie_text, log, latestSitemap, output_folder)
            add_progress("bing", 95)  

        
        driver = None
        if (active_flags["gse"] or active_flags["gsc_submit"] or active_flags["inner_validation"]):
            log("🌐 Setting up browser for Google Search Console — please wait...")
            driver = setup_driver(output_folder)
            if not load_cookies_from_text(driver, cookie_text, log):
                root.after(0, lambda: start_btn.config(state=tk.NORMAL))
                root.after(0, lambda: property_entry.config(state="normal"))
                root.after(0, lambda: cookie_textbox.config(state="normal"))
                root.after(0, lambda: bing_cookie_textbox.config(state="normal"))
                try:
                    driver.quit()
                except:
                    pass
                return

        
        if (active_flags["gse"]):
            log("📊 Fetching GSE data (Indexed, Non-indexed, and more)...", "info")
            add_progress("gse", 5)
            result = get_gsc_data(property_val, driver, output_folder)
            root.after(0, update_stats_safe, result)
            add_progress("gse", 95)

        # === GSC Submit ===
        if (active_flags["gsc_submit"]):
            add_progress("gsc_submit", 5)
            g = submit_all_sitemaps(property_val, latestSitemap, selected_option.get(), log)
            add_progress("gsc_submit", 95)

        # === Inner Validation ===
        if (active_flags["inner_validation"]):
            add_progress("inner_validation", 5)
            sitemaps = extract_sitemaps(driver, property_val, log)
            v = start_validation(driver, property_val, sitemaps, log)
            add_progress("inner_validation", 95)

        root.after(0, lambda: start_btn.config(state=tk.NORMAL))
        if(v and g):
            log("\n🎉 Process completed successfully!", "success")
        else:
            if(g):
                log("\n🎉 GSC Sitemap Resubmission completed successfully!", "success")
            else:
                log("\n❌ There is error in GSC Resubmission Process.", "error")
            if(v):
                log("\n🎉 GSC Inner Validation completed successfully!", "success")
            else:
                log("\n❌ GSC Inner Validation Process not completed.It may be 429 error or cookies expires in mid process.\n Try again later.", "error")
        end_time = time.time()
        total = end_time - start_time

        minutes = int(total // 60)
        seconds = int(total % 60)

        log(f"🕒 Total Duration: {minutes} min {seconds} sec", "success")

        try:
            if driver:
                driver.quit()
        except:
            pass

        root.after(
    0,
    lambda: show_notification(
        "GSC & Bing Automation Completed",
        f"All tasks for {property_val} have finished successfully!"
        )
    )

        shutil.rmtree(output_folder, ignore_errors=True)
        root.after(0, lambda: property_entry.config(state="normal"))
        root.after(0, lambda: cookie_textbox.config(state="normal"))
        root.after(0, lambda: bing_cookie_textbox.config(state="normal"))

    t = threading.Thread(target=task, daemon=True)
    t.start()


import tkinter as tk
from tkinter import ttk, scrolledtext
import webbrowser

# ----------------------------
# ROOT WINDOW SETUP
# ----------------------------
root = tk.Tk()
# Start invisible for fade-in
root.attributes("-alpha", 0.0)

# Safe icon loader
icon_path = resource_path("icon.ico")
try:
    if sys.platform.startswith("win"):
        root.iconbitmap(icon_path)
    else:
        icon_png = resource_path("icon.png")
        root.iconphoto(False, tk.PhotoImage(file=icon_png))
except Exception as e:
    print("Icon load error:", e)

root.title("UBUY SEO Automation Tool")

# Center Window
window_width, window_height = 950, 850
sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
x = int((sw - window_width) / 2)
y = int((sh - window_height) / 2)
root.geometry(f"{window_width}x{window_height}+{x}+{y}")
root.minsize(950, 700)

# ----------------------------
# THEME & ANIMATIONS
# ----------------------------
BG_COLOR = "#F9B11E"       # Ubuy Yellow
CARD_BG = "#FFFFFF"        # White Cards
ACCENT_COLOR = "#000000"   # Black Accents
TEXT_COLOR = "#333333"     # Dark Text
SUBTEXT_COLOR = "#666666"
CHECKMARK_COLOR = "#4CAF50" # Ubuy/Success Green

root.configure(bg=BG_COLOR)

style = ttk.Style(root)
style.theme_use("alt")


style.configure("TFrame", background=BG_COLOR)
style.configure("Card.TFrame", background=CARD_BG, relief="flat")


style.configure("Title.TLabel", 
                background=BG_COLOR, 
                foreground=ACCENT_COLOR, 
                font=("Segoe UI", 24, "bold"))

style.configure("TLabel", 
                background=CARD_BG, 
                foreground=TEXT_COLOR, 
                font=("Segoe UI", 11))

style.configure("Sub.TLabel", 
                background=CARD_BG, 
                foreground=SUBTEXT_COLOR, 
                font=("Segoe UI", 9))

style.configure("TLabelFrame", 
                background=CARD_BG, 
                borderwidth=2, 
                relief="groove")

style.configure("TLabelFrame.Label", 
                background=CARD_BG, 
                foreground=ACCENT_COLOR, 
                font=("Segoe UI", 12, "bold"))


style.configure("TCheckbutton", 
                background=CARD_BG, 
                foreground=TEXT_COLOR, 
                font=("Segoe UI", 11))
style.map("TCheckbutton", 
          background=[("active", CARD_BG)], 
          foreground=[("active", ACCENT_COLOR)],
          indicatorcolor=[("selected", CHECKMARK_COLOR)])

# Radiobutton Style
style.configure("TRadiobutton", 
                background=CARD_BG, 
                foreground=TEXT_COLOR, 
                font=("Segoe UI", 11))
style.map("TRadiobutton", 
          background=[("active", CARD_BG)], 
          foreground=[("active", ACCENT_COLOR)],
          indicatorcolor=[("selected", CHECKMARK_COLOR)])

# Custom Button Class for Hover & Pulse Effects
class PulsingButton(tk.Button):
    def __init__(self, master, text, command=None, **kwargs):
        super().__init__(master, text=text, command=command, **kwargs)
        self.default_bg = kwargs.get("bg", "#2E2E2E")
        self.hover_bg = "#444444" 
        self.pulse_bg = "#3A3A3A" # Mid-tone for pulse
        self.default_fg = "#FFFFFF"
        
        self.configure(
            bg=self.default_bg, 
            fg=self.default_fg, 
            font=("Segoe UI", 12, "bold"), 
            activebackground=self.hover_bg,
            activeforeground="#FFFFFF",
            relief="flat",
            bd=0,
            cursor="hand2",
            padx=20,
            pady=10
        )
        
        self.is_hovering = False
        self.bind("<Enter>", self.on_enter)
        self.bind("<Leave>", self.on_leave)
        
        self.pulse_step = 0
        self.after(100, self.pulse)

    def on_enter(self, e):
        self.is_hovering = True
        self.configure(bg=self.hover_bg)

    def on_leave(self, e):
        self.is_hovering = False
        self.configure(bg=self.default_bg)

    def pulse(self):
        if not self.is_hovering:
            # Simple 2-step pulse for performance
            if self.pulse_step == 0:
                self.configure(bg=self.pulse_bg)
                self.pulse_step = 1
            else:
                self.configure(bg=self.default_bg)
                self.pulse_step = 0
        
        # Pulse every 1.5 seconds roughly
        self.after(1200, self.pulse)

# ----------------------------
# HELPERS
# ----------------------------
def open_link():
    webbrowser.open_new_tab(
        "https://docs.google.com/spreadsheets/d/1fnfgAWfkMzpY4bXWvWdrS0hq5OmoQd_9Ks99y7XrFbo/edit"
    )

def fade_in(window, alpha=0.0):
    if alpha < 1.0:
        alpha += 0.04
        window.attributes("-alpha", alpha)
        window.after(20, fade_in, window, alpha)
    else:
        window.attributes("-alpha", 1.0)

def type_writer(label, text, index=0):
    """Effect: Type text character by character"""
    if index < len(text):
        label.config(text=label.cget("text") + text[index])
        # Randomize typing speed slightly for realism
        delay = 50 
        label.after(delay, type_writer, label, text, index + 1)




# ----------------------------
# GIF ANIMATION LOGIC
# ----------------------------
gif_frames = []
gif_duration = 100

def load_gif(path: str, label: tk.Label, target_height: int):
    """
    Load & resize GIF frames.
    """
    global gif_frames, gif_duration

    if not PIL_AVAILABLE:
        label.configure(text="[GIF]", fg=ACCENT_COLOR, bg=BG_COLOR)
        return

    gif_frames = []
    try:
        gif_image = Image.open(path)
        frame_duration = gif_image.info.get('duration', 100)
        gif_duration = frame_duration if isinstance(frame_duration, int) and frame_duration > 0 else 100

        frame_index = 0
        while True:
            try:
                gif_image.seek(frame_index)
                frame_image = gif_image.copy()
                
                # Make simple transparency mask if needed, but for now just resize
                ow, oh = frame_image.size
                if oh == 0: break
                new_w = max(1, int(target_height * (ow / oh)))
                resized = frame_image.resize((new_w, target_height), Image.Resampling.LANCZOS)
                
                frame_photo = ImageTk.PhotoImage(resized)
                gif_frames.append(frame_photo)
                frame_index += 1
            except EOFError:
                break

        if gif_frames:
            animate_frame(0, label)
        else:
            label.configure(text="[Err]", fg="red")
    except FileNotFoundError:
        label.configure(text="[Missing]", fg="red")
    except Exception as e:
        label.configure(text="[Err]", fg="red")

def animate_frame(frame_index: int, label: tk.Label):
    if not gif_frames: return
    frame = gif_frames[frame_index]
    label.configure(image=frame)
    label.image = frame
    next_idx = (frame_index + 1) % len(gif_frames)
    root.after(gif_duration, animate_frame, next_idx, label)


# ----------------------------
# MAIN CONTAINER (NO SCROLL)
# ----------------------------

# Main Container with Padding
container = ttk.Frame(root)
container.pack(fill="both", expand=True, padx=30, pady=10)

# --- HEADER SECTION ---
header_frame = tk.Frame(container, bg=BG_COLOR)
header_frame.pack(fill="x", pady=(0, 20))

# Logo/GIF
gif_label = tk.Label(header_frame, bg=BG_COLOR)
gif_label.pack(side="left", padx=(0, 15))

# Title Group
title_group = tk.Frame(header_frame, bg=BG_COLOR)
title_group.pack(side="left", fill="y", anchor="w")

# Use empty text initially for typing effect
lbl_main_title = tk.Label(title_group, text="", 
         font=("Segoe UI", 26, "bold"), bg=BG_COLOR, fg=ACCENT_COLOR)
lbl_main_title.pack(anchor="w")

tk.Label(title_group, text="UBUY Advanced Tool Suite", 
         font=("Segoe UI", 10), bg=BG_COLOR, fg=SUBTEXT_COLOR).pack(anchor="w")

# Report Link
btn_report = tk.Button(header_frame, text="View Report ↗", command=open_link,
                       bg="#333", fg="white", font=("Segoe UI", 10), relief="flat", padx=10, pady=5)
btn_report.pack(side="right", anchor="center")


# --- MAIN CONTENT GRID ---
grid_frame = ttk.Frame(container)
grid_frame.pack(fill="both", expand=True)
grid_frame.columnconfigure(0, weight=4)  # Left column slightly larger
grid_frame.columnconfigure(1, weight=3)  # Right column
grid_frame.rowconfigure(0, weight=1)     # Ensure row expands vertically

# === LEFT COLUMN: CONTROLS ===
left_col = ttk.Frame(grid_frame)
left_col.grid(row=0, column=0, sticky="nsew", padx=(0, 20))

# 1. Credentials Card
card_creds = ttk.LabelFrame(left_col, text=" 🔑 Authentication ", padding=10)
card_creds.pack(fill="x", pady=(0, 5))

ttk.Label(card_creds, text="Target Property Domain:").pack(anchor="w", pady=(0, 5))
property_entry = ttk.Entry(card_creds, font=("Segoe UI", 11))
property_entry.pack(fill="x", pady=(0, 15))

ttk.Label(card_creds, text="Select Service Account:").pack(anchor="w", pady=(0, 5))
selected_option = tk.IntVar(value=1)
style.configure("TRadiobutton", background=CARD_BG, foreground=TEXT_COLOR)
ttk.Radiobutton(card_creds, text="gschigh (gschigh@ubuy.com)", variable=selected_option, value=1).pack(anchor="w", pady=2)
ttk.Radiobutton(card_creds, text="gsclow (gsclow@ubuy.com)", variable=selected_option, value=2).pack(anchor="w", pady=2)

# 2. Tasks Card
card_tasks = ttk.LabelFrame(left_col, text=" ⚡ Automated Tasks ", padding=10)
card_tasks.pack(fill="x", pady=(0, 5))

chk_bing_resubmit = tk.BooleanVar()
chk_gse_fetch = tk.BooleanVar()
chk_gsc_resubmit = tk.BooleanVar()
chk_gsc_inner_validation = tk.BooleanVar()

ttk.Checkbutton(card_tasks, text="Auto-Submit to Bing Webmaster", variable=chk_bing_resubmit).pack(anchor="w", pady=3)
ttk.Checkbutton(card_tasks, text="Fetch Indexing Data (GSE)", variable=chk_gse_fetch).pack(anchor="w", pady=3)
ttk.Checkbutton(card_tasks, text="Submit Sitemaps to GSC", variable=chk_gsc_resubmit).pack(anchor="w", pady=3)
ttk.Checkbutton(card_tasks, text="Validate GSC Coverage Errors", variable=chk_gsc_inner_validation).pack(anchor="w", pady=3)

# 3. Cookies Card
card_cookies = ttk.LabelFrame(left_col, text=" 🍪 Session Cookies ", padding=10)
card_cookies.pack(fill="both", expand=True)

# Stacked view instead of tabs
lbl_g = tk.Label(card_cookies, text="Google Cookies:", bg=CARD_BG, fg=TEXT_COLOR, font=("Segoe UI", 10, "bold"))
lbl_g.pack(anchor="w", pady=(0, 2))
cookie_textbox = scrolledtext.ScrolledText(card_cookies, height=3, bg="#2b2b2b", fg="#eee", insertbackground="white", bd=0)
cookie_textbox.pack(fill="x", padx=5, pady=(0, 10))

lbl_b = tk.Label(card_cookies, text="Bing Cookies:", bg=CARD_BG, fg=TEXT_COLOR, font=("Segoe UI", 10, "bold"))
lbl_b.pack(anchor="w", pady=(0, 2))
bing_cookie_textbox = scrolledtext.ScrolledText(card_cookies, height=8, bg="#2b2b2b", fg="#eee", insertbackground="white", bd=0)
bing_cookie_textbox.pack(fill="both", expand=True, padx=5, pady=(0, 5))


# === RIGHT COLUMN: STATUS & ACTION ===
right_col = ttk.Frame(grid_frame)
right_col.grid(row=0, column=1, sticky="nsew")

# 1. Action Button
start_btn = PulsingButton(
    right_col, 
    text="START EXECUTION ►", 
    command=lambda: run_bot(
        property_entry,
        cookie_textbox,
        progressbar,
        logbox,
        start_btn,
        selected_option,
        bing_cookie_textbox,
        chk_bing_resubmit,
        chk_gse_fetch,
        chk_gsc_resubmit,
        chk_gsc_inner_validation,
    )
)
start_btn.pack(fill="x", pady=(0, 20))

# 2. Progress
progress_frame = tk.Frame(right_col, bg=BG_COLOR)
progress_frame.pack(fill="x", pady=(0, 20))
ttk.Label(progress_frame, text="Task Progress", background=BG_COLOR, foreground=TEXT_COLOR).pack(anchor="w", pady=(0,5))
progressbar = ttk.Progressbar(progress_frame, mode="determinate")
progressbar.pack(fill="x", ipady=5)

# 3. Live Stats (Custom Grid)
stats_card = tk.Frame(right_col, bg=CARD_BG, padx=15, pady=15)
stats_card.pack(fill="x", pady=(0, 20))
tk.Label(stats_card, text="Live Statistics", bg=CARD_BG, fg=SUBTEXT_COLOR, font=("Segoe UI", 10, "bold")).pack(anchor="w", pady=(0, 10))

stats_grid = tk.Frame(stats_card, bg=CARD_BG)
stats_grid.pack(fill="x")
stats_grid.columnconfigure((0,1,2), weight=1)

def make_stat_box(parent, label, row, col):
    f = tk.Frame(parent, bg=CARD_BG, pady=5)
    f.grid(row=row, column=col, sticky="nsew")
    tk.Label(f, text=label, bg=CARD_BG, fg=SUBTEXT_COLOR, font=("Segoe UI", 9)).pack(anchor="center")
    val = tk.Label(f, text="--", bg=CARD_BG, fg=ACCENT_COLOR, font=("Segoe UI", 14, "bold"))
    val.pack(anchor="center")
    return val

# Row 1
stat_indexed = make_stat_box(stats_grid, "Indexed", 0, 0)
stat_non_indexed = make_stat_box(stats_grid, "Non-Indexed", 0, 1)
stat_today_total = make_stat_box(stats_grid, "Today - Total Requests", 0, 2)

# Row 2
stat_today_avg = make_stat_box(stats_grid, "Today - Avg Response Time", 1, 0)
stat_seven_total = make_stat_box(stats_grid, "7 Days Ago - Total Requests", 1, 1)
stat_seven_avg = make_stat_box(stats_grid, "7 Days Ago - Avg Response Time", 1, 2)

# 4. Logs
log_label = tk.Label(right_col, text="System Log", bg=BG_COLOR, fg=TEXT_COLOR, font=("Segoe UI", 10, "bold"))
log_label.pack(anchor="w", pady=(0, 5))

logbox = scrolledtext.ScrolledText(
    right_col,
    height=10,
    state="disabled",
    bg="#000000",
    fg="#00e5ff",
    font=("Consolas", 9),
    bd=0,
    highlightthickness=1,
    highlightbackground="#333"
)
logbox.pack(fill="both", expand=True)

# Tag config
logbox.tag_config("time", foreground="#555")
logbox.tag_config("success", foreground="#4caf50")
logbox.tag_config("error", foreground="#f44336")
logbox.tag_config("warn", foreground="#ffca28")
logbox.tag_config("info", foreground="#29b6f6")
logbox.tag_config("header", foreground="#ffffff", background="#333")

# Initialize GIF
load_gif(resource_path("ubuy.gif"), gif_label, 50)

# Start Fade-in
root.after(100, lambda: fade_in(root))

# Start Typewriter
root.after(500, lambda: type_writer(lbl_main_title, "SEO Automation V4"))

root.mainloop()
