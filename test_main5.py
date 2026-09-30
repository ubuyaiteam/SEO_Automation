import csv
import json
import os
import re
import sys
import threading
import time
import pyotp

import urllib3
import urllib.request
import urllib.error
import subprocess

# Version Information
CURRENT_VERSION = "v9.7.1"

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
import datetime
import shutil
import tempfile
import tkinter as tk
from tkinter import filedialog, messagebox, scrolledtext, ttk

from google.oauth2 import service_account
import ssl
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait
from webdriver_manager.chrome import ChromeDriverManager
from win10toast import ToastNotifier

from app1 import get_gsc_data

# Local JSON Storage (AppData Migration)

# ==================================================
# SYSTEM UTILITIES
# ==================================================
def get_app_folder():
    """Get the absolute path to the app data folder."""
    appdata = os.getenv("APPDATA")
    if not appdata:
        return os.path.abspath(".")
    app_folder = os.path.join(appdata, "UbuySEOAutomation")
    os.makedirs(app_folder, exist_ok=True)
    return app_folder

APP_FOLDER = get_app_folder()

def get_data_path():
    """Get the absolute path to the data file in AppData and ensure directory exists."""
    new_path = os.path.join(APP_FOLDER, "service_accounts_data.json")
    old_path = "service_accounts_data.json"

    # 1. If it doesn't exist in AppData, try to copy it from PyInstaller's bundled resource folder
    if not os.path.exists(new_path):
        bundled_dir = getattr(sys, "_MEIPASS", os.path.abspath("."))
        bundled_path = os.path.join(bundled_dir, "service_accounts_data.json")
        if os.path.exists(bundled_path) and bundled_path != new_path:
            try:
                shutil.copy(bundled_path, new_path)
                print(f"Copied default data from bundled resources to {new_path}")
            except Exception as e:
                print(f"Failed to copy bundled data: {e}")

    # 2. Simple Migration: Move file if it exists in local folder but not in AppData
    if os.path.exists(old_path) and not os.path.exists(new_path):
        try:
            shutil.move(old_path, new_path)
            print(f"Migrated data from {old_path} to {new_path}")
        except Exception as e:
            print(f"Migration failed: {e}")

    return new_path


DATA_FILE = get_data_path()


def load_local_sa():
    """Load all service accounts from the local JSON file in AppData."""
    if not os.path.exists(DATA_FILE) or os.path.getsize(DATA_FILE) == 0:
        return {}
    try:
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print(f"Error loading local SA data: {e}")
        return {}


def save_local_sa(data):
    """Save all service accounts to the local JSON file in AppData."""
    try:
        with open(DATA_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=4)
        return True
    except Exception as e:
        print(f"Error saving local SA data: {e}")
        return False


# Initialize status
DB_STATUS = True
toaster = ToastNotifier()


def resource_path(filename):
    if hasattr(sys, "_MEIPASS"):
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
# SESSION MANAGEMENT
# ----------------------------


# ==================================================
# SESSION MANAGEMENT
# ==================================================
def get_session_dir(country, service):
    """Get persistent Chrome profile directory for a country/service combo.
    service = 'google' or 'bing'
    """
    base = os.path.join(APP_FOLDER, "sessions", country, service)
    os.makedirs(base, exist_ok=True)
    return base


def session_exists(country, service):
    """Check if a saved session profile exists for a country/service."""
    session_dir = get_session_dir(country, service)
    default_dir = os.path.join(session_dir, "Default")
    return os.path.isdir(default_dir)


def clear_session(country, service):
    """Delete a saved session profile."""
    session_dir = get_session_dir(country, service)
    if os.path.isdir(session_dir):
        shutil.rmtree(session_dir, ignore_errors=True)
        # Check if it was actually deleted
        default_dir = os.path.join(session_dir, "Default")
        if os.path.isdir(default_dir):
            return False
    return True

def get_real_profile_info(country):
    """
    Finds the real Chrome 'User Data' directory and the specific Profile name
    (e.g., 'Profile 131') for the given country.
    Returns: (user_data_dir, profile_name) or (None, None)
    """
    import os, json
    
    local_app_data = os.getenv("LOCALAPPDATA")
    if not local_app_data:
        return None, None

    real_user_data = os.path.join(local_app_data, "Google", "Chrome", "User Data")
    local_state_path = os.path.join(real_user_data, "Local State")
    
    if not os.path.exists(local_state_path):
        return None, None
        
    try:
        with open(local_state_path, "r", encoding="utf-8") as f:
            local_state = json.load(f)
            
        profiles = local_state.get("profile", {}).get("info_cache", {})
        target_profile_dir = None
        
        # 1st pass: exact match
        for profile_dir, info in profiles.items():
            name = info.get("name", "").strip().lower()
            if name == country.lower() or name == f"ubuy {country.lower()}":
                target_profile_dir = profile_dir
                break
                
        # 2nd pass: substring match
        if not target_profile_dir:
            for profile_dir, info in profiles.items():
                name = info.get("name", "").strip().lower()
                if country.lower() in name:
                    target_profile_dir = profile_dir
                    break
                    
        if target_profile_dir and os.path.exists(os.path.join(real_user_data, target_profile_dir)):
            return real_user_data, target_profile_dir
            
    except Exception:
        pass
        
    return None, None




def _build_chrome_options(user_data_dir, profile_name, headless, output_folder, with_profile=True):
    """Build Chrome options. Shared between profile-based and fresh-profile launches."""
    options = Options()

    if with_profile and user_data_dir and profile_name:
        options.add_argument(f"--user-data-dir={user_data_dir}")
        options.add_argument(f"--profile-directory={profile_name}")

    # Flags required when loading a real Chrome profile to prevent crashes
    options.add_argument("--no-first-run")
    options.add_argument("--no-default-browser-check")
    options.add_argument("--disable-sync")
    options.add_argument("--password-store=basic")  # Prevents OS keychain blocking startup
    options.add_argument("--disable-client-side-phishing-detection")
    options.add_argument("--disable-component-update")
    options.add_argument("--disable-translate")
    options.add_argument("--disable-features=TranslateUI,OptimizationHints")
    options.add_argument("--disable-hang-monitor")

    # Anti-detection
    user_agent = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36"
    options.add_argument(f"user-agent={user_agent}")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_experimental_option("excludeSwitches", ["enable-automation", "enable-logging"])
    options.add_experimental_option("useAutomationExtension", False)

    # Performance / stability
    options.add_argument("--disable-gpu")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-infobars")
    options.add_argument("--disable-extensions")
    options.add_argument("--disable-logging")
    options.add_argument("--log-level=3")
    options.add_argument("--silent")

    if headless:
        options.add_argument("--headless=new")
        options.add_argument("--blink-settings=imagesEnabled=false")
        options.add_argument("--window-size=1920,1080")
    else:
        options.add_argument("--start-maximized")

    if output_folder:
        prefs = {
            "download.default_directory": output_folder,
            "download.prompt_for_download": False,
            "download.directory_upgrade": True,
            "safebrowsing.enabled": True,
        }
        options.add_experimental_option("prefs", prefs)

    return options


def setup_driver_with_session(country, service, headless=False, output_folder=None):
    """
    Create a Chrome driver using a persistent session profile.
    This restores the original sessions located in AppData/Roaming/UbuySEOAutomation/sessions.
    It also cleans up any zombie lock files from previous crashes to prevent the devtoolsactiveport error.
    """
    session_dir = get_session_dir(country, service)
    
    # 🧹 CRITICAL FIX: Aggressively clean up any lingering lock files before launching.
    # If a previous run crashed, these files get left behind and cause the "devtoolsactiveport" error.
    locks_to_remove = ["SingletonLock", "SingletonSocket", "SingletonCookie", "DevToolsActivePort"]
    for lock in locks_to_remove:
        lock_path = os.path.join(session_dir, lock)
        if os.path.exists(lock_path):
            try:
                os.remove(lock_path)
                print(f"🧹 Cleaned up stale lock file: {lock}")
            except Exception as e:
                print(f"⚠️ Could not remove {lock}: {e}")
                
    chrome_service = Service()
    if sys.platform.startswith("win"):
        chrome_service.creationflags = 0x08000000

    print(f"🔄 Launching Chrome using persistent session for {country}...")
    try:
        # Build options using the dedicated session directory
        options = _build_chrome_options(session_dir, "Default", headless, output_folder, with_profile=True)
        driver = webdriver.Chrome(service=chrome_service, options=options)
        driver.set_page_load_timeout(60)
        _active_drivers.append(driver)
        return driver, None
    except Exception as e:
        print(f"❌ Failed to launch Chrome for {country}: {e}")
        return None, str(e)


def read_credentials(country):
    creds_file = "credentials.csv"
    if not os.path.exists(creds_file):
        return None
    try:
        with open(creds_file, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                if row.get("Country", "").strip().lower() == country.lower():
                    return row
    except Exception as e:
        print(f"Error reading credentials.csv: {e}")
    return None

def auto_login_google(driver, credentials):
    email = credentials.get("Email", "").strip()
    password = credentials.get("Password", "").strip()
    totp_secret = credentials.get("2FA Secret", "").strip()
    
    if not email or not password:
        return False
        
    try:
        # Enter Email
        try:
            email_input = WebDriverWait(driver, 10).until(EC.presence_of_element_located((By.XPATH, '//input[@type="email"]')))
            email_input.clear()
            email_input.send_keys(email)
            driver.find_element(By.XPATH, '//*[@id="identifierNext"]').click()
        except Exception:
            print("Could not find email input or next button.")
            return False
            
        # Enter Password
        try:
            time.sleep(2)
            pwd_input = WebDriverWait(driver, 10).until(EC.element_to_be_clickable((By.XPATH, '//input[@type="password"]')))
            pwd_input.clear()
            pwd_input.send_keys(password)
            driver.find_element(By.XPATH, '//*[@id="passwordNext"]').click()
        except Exception:
            print("Could not find password input or next button.")
            return False
            
        # Enter TOTP if provided
        if totp_secret:
            try:
                time.sleep(2)
                totp = pyotp.TOTP(totp_secret)
                code = totp.now()
                totp_input = WebDriverWait(driver, 10).until(EC.presence_of_element_located((By.XPATH, '//input[@id="totpPin"]')))
                totp_input.clear()
                totp_input.send_keys(code)
                driver.find_element(By.XPATH, '//*[@id="totpNext"]').click()
            except Exception:
                print("Could not automate 2FA. Please complete it manually.")
                
        return True
    except Exception as e:
        print(f"Auto-login failed: {e}")
        return False


def load_cookies_to_driver(driver, country, service):
    country_clean = country.strip().lower()
    service_clean = service.strip().lower()
    
    # 0. Look for temporary cookies from UI paste
    cookie_file = os.path.join(APP_FOLDER, f"temp_cookies_{country_clean}_{service_clean}.json")
    
    if not os.path.exists(cookie_file):
        # 1. Look for specific: cookies_guadeloupe_google.json
        cookie_file = os.path.join(APP_FOLDER, f"cookies_{country_clean}_{service_clean}.json")
        
        if not os.path.exists(cookie_file):
            return False

    try:
        with open(cookie_file, "r", encoding="utf-8") as f:
            cookies = json.load(f)
            
        added = 0
        for cookie in cookies:
            selenium_cookie = {
                'name': cookie.get('name'),
                'value': cookie.get('value'),
                'domain': cookie.get('domain'),
                'path': cookie.get('path', '/')
            }
            if 'secure' in cookie:
                selenium_cookie['secure'] = cookie.get('secure')
            if 'expirationDate' in cookie:
                selenium_cookie['expiry'] = int(cookie['expirationDate'])
                
            try:
                driver.add_cookie(selenium_cookie)
                added += 1
            except Exception:
                pass
        
        print(f"🍪 Loaded {added} cookies from {cookie_file}!")
        
        # Auto-cleanup for temp cookie files
        if "temp_cookies_" in cookie_file:
            try:
                os.remove(cookie_file)
                print(f"🧹 Cleaned up temporary cookie file: {cookie_file}")
            except Exception as e:
                print(f"⚠️ Failed to delete temp cookie file: {e}")
                
        return added > 0
    except Exception as e:
        print(f"❌ Failed to load cookies from {cookie_file}: {e}")
        return False


def open_cookie_paste_window(country, service):
    country_clean = country.strip().lower()
    service_clean = service.strip().lower()
    
    top = tk.Toplevel()
    top.title(f"Paste Cookies for {service.title()} ({country})")
    top.geometry("500x400")
    top.attributes("-topmost", True)
    
    lbl = tk.Label(top, text="Paste raw JSON cookies from EditThisCookie:")
    lbl.pack(pady=5)
    
    text_area = scrolledtext.ScrolledText(top, wrap=tk.WORD, height=15)
    text_area.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)
    
    def save_cookies():
        content = text_area.get("1.0", tk.END).strip()
        if not content:
            messagebox.showwarning("Empty", "Please paste the cookies JSON.")
            return
            
        try:
            cookies = json.loads(content)
            if not isinstance(cookies, list):
                raise ValueError("Cookies must be a JSON array.")
        except Exception as e:
            messagebox.showerror("Invalid JSON", f"The pasted text is not valid JSON:\n\n{e}")
            return
            
        cookie_file = os.path.join(APP_FOLDER, f"temp_cookies_{country_clean}_{service_clean}.json")
        try:
            with open(cookie_file, "w", encoding="utf-8") as f:
                json.dump(cookies, f, indent=4)
            messagebox.showinfo("Saved", f"Temporary cookies saved!\nNow click 'Login' to use them.")
            top.destroy()
        except Exception as e:
            messagebox.showerror("Error", f"Failed to save cookies file:\n{e}")
            
    btn_save = tk.Button(top, text="Save Cookies", command=save_cookies, bg="#4CAF50", fg="white", font=("Segoe UI", 10, "bold"))
    btn_save.pack(pady=10)



def open_login_browser(country, service):
    """Open a VISIBLE browser for manual login. Blocks until user clicks OK."""
    if service == "google":
        start_url = "https://accounts.google.com/"
        verify_text = "Then verify access at: https://search.google.com/search-console"
    else:
        start_url = "https://www.bing.com/webmasters/"
        verify_text = "Then verify access to the Bing Webmaster Tools dashboard"

    try:
        creds = read_credentials(country) if service == "google" else None
        
        if not creds:
            messagebox.showinfo(
                "Login Instructions",
                f"A browser window is about to open.\n\n"
                f"1. Please complete the login process in the browser.\n"
                f"2. {verify_text}\n"
                f"3. After you finish logging in, return to this app and click 'Yes' on the next popup to confirm.",
            )

        print("🔄 Setting up browser for GSC Validation...")
            
        driver, e = setup_driver_with_session(country, service, headless=False, output_folder=None)
        if not driver:
            print(f"❌ Failed to launch Chrome for {country}. Check terminal for errors.")
            messagebox.showerror("Login Error", f"Failed to launch Chrome for {country}: {e}")
            return

        driver.get(start_url)

        # Let the page load its initial domain cookies before injecting
        time.sleep(2)

        # TRY COOKIES FIRST
        if load_cookies_to_driver(driver, country, service):
            print(f"🍪 Successfully injected cookies for {service.title()} ({country})!")
            driver.refresh()
            time.sleep(3) # Give it time to finish loading the logged-in state
            
        elif creds and service == "google":
            print(f"🔑 Automating login for {country}...")
            auto_login_google(driver, creds)
            if creds.get("2FA Secret", "").strip():
                time.sleep(5) # Give it time to finish login
            else:
                # Need manual 2FA or Captcha
                messagebox.showinfo("Manual Action Required", "Please complete the 2FA challenge or Captcha in the browser, then click OK.")
        else:
            # Give the user a 30-second head start to interact with the browser before showing the confirmation popup
            time.sleep(30)

        success = messagebox.askyesno(
            f"{service.title()} Login \u2014 {country}",
            f"Did you successfully complete the login?",
        )

        safe_quit_driver(driver)

        if not success:
            clear_session(country, service)
            messagebox.showwarning(
                "Login Cancelled", f"The {service.title()} login was cancelled."
            )

    except Exception as e:
        messagebox.showerror("Login Error", f"Could not open browser: {e}")


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
    options.add_experimental_option(
        "excludeSwitches", ["enable-automation", "enable-logging"]
    )
    options.add_experimental_option("useAutomationExtension", False)
    # Configure download preferences
    output_dir = output_folder
    prefs = {
        "download.default_directory": output_dir,
        "download.prompt_for_download": False,
        "download.directory_upgrade": True,
        "safebrowsing.enabled": True,
    }
    options.add_experimental_option("prefs", prefs)
    driver = webdriver.Chrome(
        service=Service(ChromeDriverManager().install()), options=options
    )
    driver.set_page_load_timeout(60)
    _active_drivers.append(driver)
    return driver


def load_cookies_from_text(driver, cookie_text, log):
    if not cookie_text.strip():
        log(
            "🔴 Google cookies are empty! Please paste your Google Search Console JSON cookies into the 'Google Cookies' box.",
            "error",
        )
        return False
    driver.get("https://www.google.com/")
    time.sleep(2)
    try:
        cookies = json.loads(cookie_text.strip())
    except json.JSONDecodeError:
        log(
            "🔴 Google cookie JSON format is invalid. Please copy the entire JSON array from your cookie editor extension.",
            "error",
        )
        return False

    for cookie in cookies:
        for key in ["sameSite", "storeId", "id", "hostOnly"]:
            cookie.pop(key, None)
        cookie["domain"] = ".google.com"
        try:
            driver.add_cookie(cookie)
        except Exception as e:
            log("🟡 Skipping invalid cookie entry.", "warn")
    time.sleep(3)
    log("🟢 Google cookies loaded successfully.", "success")
    return True


def extract_urls_from_sitemap(property, log):
    """Fetch sitemap or sitemap index and return <loc> URLs via a headless Selenium browser to bypass Cloudflare."""
    sitemap_url = f"https://www.{property}/sitemap.xml"
    xml_content = None

    print(f"Fetching sitemap via headless Selenium: {sitemap_url}")
    try:
        options = Options()
        options.add_argument("--headless=new")
        options.add_argument("--disable-gpu")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--window-size=1920,1080")
        options.add_argument("--blink-settings=imagesEnabled=false")
        options.add_argument("--disable-blink-features=AutomationControlled")
        options.add_experimental_option("excludeSwitches", ["enable-automation"])
        options.add_experimental_option("useAutomationExtension", False)
        options.add_argument(
            "user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
        )

        service = Service(ChromeDriverManager().install())
        if sys.platform.startswith("win"):
            service.creationflags = 0x08000000

        temp_driver = webdriver.Chrome(service=service, options=options)
        temp_driver.set_page_load_timeout(60)
        temp_driver.execute_cdp_cmd(
            "Page.addScriptToEvaluateOnNewDocument",
            {
                "source": "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
            },
        )
        temp_driver.get(sitemap_url)
        time.sleep(5)  # Wait for page/sitemap to load
        xml_content = temp_driver.page_source
        safe_quit_driver(temp_driver)
        print("✅ Successfully fetched sitemap.")
    except Exception as sel_err:
        if _stop_requested: raise sel_err
        print(f"❌ Failed to fetch sitemap: {sel_err}")
        return []

    if xml_content:
        urls = re.findall(r"<loc>(.*?)</loc>", xml_content)
        print(f"Found {len(urls)} URLs in sitemap.")
        return urls
    return []


# ==================================================
# GOOGLE SEARCH CONSOLE LOGIC
# ==================================================
def filter_sitemaps(full_list, d_on, f_on, oth_on):
    if not (d_on or f_on or oth_on):
        return full_list  # no filter → return all
    result = []
    for sm in full_list:
        if d_on and "_d_" in sm:
            result.append(sm)
        elif f_on and "_f_" in sm:
            result.append(sm)
        elif oth_on and "_d_" not in sm and "_f_" not in sm:
            result.append(sm)
    return result


def extract_sitemaps(driver, property, log):
    driver.get(
        f"https://search.google.com/search-console/sitemaps?resource_id=sc-domain:{property}"
    )
    # Short initial wait for the page to settle
    time.sleep(5)

    # Step 1: Optionally expand pagination if the dropdown exists (soft check, max 5s)
    try:
        js_expand = """const list = document.querySelectorAll("div[role='option'][data-value='500']");
                if (list && list.length > 0){list[0].click(); return true;} return false;"""
        dropdown = WebDriverWait(driver, 5).until(
            EC.element_to_be_clickable(
                (By.CSS_SELECTOR, "div[role='listbox'][aria-label='Number of rows per page']")
            )
        )
        dropdown.click()
        time.sleep(2)
        driver.execute_script(js_expand)
        time.sleep(3)
    except Exception:
        # Pagination dropdown doesn't exist (small number of sitemaps) — skip gracefully
        pass

    # Step 2: Primary extraction via known CSS selector
    try:
        spans = WebDriverWait(driver, 15).until(
            EC.presence_of_all_elements_located((By.CSS_SELECTOR, "td.RVEMNe"))
        )
        results = [span.text.strip() for span in spans if span.text.strip()]
        if results:
            log(f"✅ Sitemaps extracted successfully! ({len(results)} found)", "success")
            return results
    except Exception:
        pass

    # Step 3: Fallback — scan all table cells for sitemap-like URLs
    try:
        all_tds = driver.find_elements(By.CSS_SELECTOR, "td")
        results = []
        for td in all_tds:
            txt = td.text.strip()
            if txt.startswith("http") and ("sitemap" in txt.lower() or ".xml" in txt.lower()):
                results.append(txt)
        if results:
            log(f"✅ Sitemaps extracted via fallback! ({len(results)} found)", "success")
            return results
    except Exception:
        pass

    # Step 4: Last resort — look for any anchor links pointing to XML files
    try:
        anchors = driver.find_elements(By.CSS_SELECTOR, "a[href*='.xml'], a[href*='sitemap']")
        results = [a.get_attribute("href") for a in anchors if a.get_attribute("href")]
        if results:
            log(f"✅ Sitemaps extracted via anchor fallback! ({len(results)} found)", "success")
            return results
    except Exception:
        pass

    log("⚠️ No sitemaps submitted to GSC page yet — using sitemap.xml fallback.", "warning")
    return []


def start_validation(driver, property, sitemaps, log):
    try:
        # Helper function to validate indexing errors on any target URL
        def validate_page_indexing_errors(target_url, label):
            """Returns 'RATE_LIMITED' if 429 detected and retries fail, None otherwise."""
            log(f"🔎 Scanning Page Indexing errors for: {label}...")

            wait_times = [
                30,
                10,
                10,
            ]  # Cooldown schedule in minutes: 1st=30m, 2nd=10m, 3rd=10m
            max_429_retries = len(wait_times)
            page_loaded_ok = False
            for attempt in range(max_429_retries + 1):
                try:
                    driver.get(target_url)
                    time.sleep(3)  # Allow page to fully render

                    # --- 429 Rate-Limit Detection with progressive wait ---
                    page_source = driver.page_source.lower()
                    if (
                        "429" in driver.title.lower()
                        or "too many requests" in page_source
                        or "rate limit" in page_source
                        or "quota" in page_source
                    ):
                        if attempt < max_429_retries:
                            wait_mins = wait_times[attempt]
                            log(
                                f"🛑 Rate limited (HTTP 429) detected on {label}. Waiting {wait_mins} minutes to recover...",
                                "warning",
                            )
                            # Keep browser alive during wait by pinging it every 2 minutes
                            total_seconds = wait_mins * 60
                            elapsed = 0
                            keepalive_interval = 120  # ping every 2 minutes
                            while elapsed < total_seconds:
                                sleep_chunk = min(
                                    keepalive_interval, total_seconds - elapsed
                                )
                                time.sleep(sleep_chunk)
                                elapsed += sleep_chunk
                                if elapsed < total_seconds:
                                    try:
                                        driver.execute_script(
                                            "return 1;"
                                        )  # Lightweight keepalive ping
                                    except Exception:
                                        log(
                                            "⚠️ Browser connection lost during wait. Attempting recovery...",
                                            "warning",
                                        )
                                        break
                            log(f"🔄 {wait_mins} minutes passed. Retrying...", "info")
                            continue
                        else:
                            log(
                                f"🛑 Rate limited (HTTP 429) detected on {label} after {max_429_retries} retries. Aborting entire validation process.",
                                "error",
                            )
                            return "RATE_LIMITED"

                    page_loaded_ok = True
                    break  # Success, exit retry loop

                except Exception as net_err:
                    if _stop_requested: raise net_err
                    log(f"❌ Failed to load {label}: {net_err}", "error")
                    return None

            if not page_loaded_ok:
                return None

            try:
                # Wait for the page table to fully load (wait for any table row to appear)
                try:
                    WebDriverWait(driver, 15).until(
                        EC.presence_of_element_located((By.CSS_SELECTOR, "table tr"))
                    )
                except Exception:
                    # No table found - check if it's a rate limit or genuinely no errors
                    current_page = driver.page_source.lower()
                    if (
                        "429" in driver.title.lower()
                        or "too many requests" in current_page
                        or "rate limit" in current_page
                    ):
                        log(
                            f"🛑 Rate limited (HTTP 429) detected on {label}. Aborting entire validation process.",
                            "error",
                        )
                        return "RATE_LIMITED"
                    log(f"✅ No Page Indexing errors found under: {label}")
                    return None

                # Use JavaScript to find all table rows that contain 'Failed' or 'Not Started'
                # in their Validation column — this is class-independent and robust against UI updates
                def get_error_rows():
                    all_rows = driver.find_elements(By.CSS_SELECTOR, "table tr")
                    error_map = {}
                    for row in all_rows:
                        cols = row.find_elements(By.TAG_NAME, "td")
                        if len(cols) < 3:
                            continue
                        reason_text = cols[0].text.strip()
                        validation_text = cols[2].text.strip() if len(cols) > 2 else ""

                        if not reason_text:
                            continue

                        reason_lower = reason_text.lower()
                        # Skip reasons that are excluded per business rules
                        if "robots.txt" in reason_lower or "noindex" in reason_lower:
                            log(f"⏭️ Skipping validation for excluded reason: {reason_text}")
                            continue

                        if "Failed" in validation_text:
                            error_map[reason_text] = ("Failed", row)
                        elif "Not Started" in validation_text:
                            error_map[reason_text] = ("Not Started", row)
                    return error_map

                errors = get_error_rows()

                if not errors:
                    log(f"✅ No Page Indexing errors found under: {label}")
                    return None

                log(f"⚠️ Found {len(errors)} error(s) under {label}: {list(errors.keys())}", "warning")

                for key, (value, _) in errors.items():
                    try:
                        # Re-fetch rows fresh to avoid stale element references
                        fresh_errors = get_error_rows()
                        if key not in fresh_errors:
                            continue
                        _, row = fresh_errors[key]

                        driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", row)
                        time.sleep(1)
                        driver.execute_script("arguments[0].click();", row)
                        log(f"🖱️ Selected row: {key} ({value})")

                        if value == "Failed":
                            # Click "See Details"
                            see_details_button = WebDriverWait(driver, 15).until(
                                EC.element_to_be_clickable((
                                    By.XPATH,
                                    "//*[translate(normalize-space(text()), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz')='see details']/ancestor-or-self::*[@role='button' or @role='link']",
                                ))
                            )
                            see_details_button.click()
                            log("🖱️ Opening 'See Details' panel...")

                            # Click "Start New Validation" or "Start Validation"
                            start_validation_button = WebDriverWait(driver, 15).until(
                                EC.element_to_be_clickable((
                                    By.XPATH,
                                    "//*[contains(translate(normalize-space(text()), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'start') and contains(translate(normalize-space(text()), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'validation')]/ancestor-or-self::*[@role='button']",
                                ))
                            )
                            start_validation_button.click()
                            log("🖱️ Clicked 'Start Validation'...")

                        elif value == "Not Started":
                            # Click "Validate Fix"
                            validate_fix_button = WebDriverWait(driver, 15).until(
                                EC.element_to_be_clickable((
                                    By.XPATH,
                                    "//*[translate(normalize-space(text()), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz')='validate fix']/ancestor-or-self::*[@role='button']",
                                ))
                            )
                            validate_fix_button.click()
                            log("🖱️ Clicked 'Validate fix'.")

                        time.sleep(1)

                        # Dismiss confirmation dialog (try multiple strategies)
                        try:
                            confirm_btn = WebDriverWait(driver, 5).until(
                                EC.element_to_be_clickable((
                                    By.XPATH,
                                    "//*[@role='button' and (contains(translate(normalize-space(text()), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'ok') or contains(translate(normalize-space(text()), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'confirm') or contains(translate(normalize-space(text()), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'done'))]",
                                ))
                            )
                            driver.execute_script("arguments[0].click();", confirm_btn)
                        except Exception:
                            pass  # Dialog may not always appear

                        time.sleep(1)
                        log(f"✅ Validation triggered for: {key}")
                        driver.get(target_url)
                        time.sleep(2)

                    except Exception as row_err:
                        if _stop_requested:
                            raise row_err
                        log(
                            f"⚠️ Could not trigger validation for '{key}': {type(row_err).__name__}: {row_err}",
                            "warning",
                        )
                        driver.get(target_url)
                        time.sleep(2)

                log(f"✅ All Page Indexing errors processed for: {label}")
            except Exception as e:
                log(
                    f"❌ Error during validation for {label}: {type(e).__name__}: {e}",
                    "error",
                )

        # --- Phase 1: Validate All Known Pages ---
        log("✅ Step 1A: Validating Page Index Errors (All Known Pages)...")
        known_pages_url = f"https://search.google.com/search-console/index?resource_id=sc-domain:{property}"
        result = validate_page_indexing_errors(known_pages_url, "All Known Pages")
        if result == "RATE_LIMITED":
            log(
                "🛑 Validation aborted due to Google rate limiting (429). Please try again later.",
                "error",
            )
            return False

        time.sleep(5)  # Throttle delay to prevent Google 429 rate limiting

        # --- Phase 2: Validate All Submitted Pages ---
        log("✅ Step 1B: Validating Page Index Errors (All Submitted Pages)...")
        submitted_pages_url = f"https://search.google.com/search-console/index?resource_id=sc-domain:{property}&pages=ALL_SUBMITTED_URLS"
        result = validate_page_indexing_errors(
            submitted_pages_url, "All Submitted Pages"
        )
        if result == "RATE_LIMITED":
            log(
                "🛑 Validation aborted due to Google rate limiting (429). Please try again later.",
                "error",
            )
            return False

        time.sleep(5)  # Throttle delay to prevent Google 429 rate limiting

        # --- Phase 2B: Validate Unsubmitted Pages Only ---
        log("✅ Step 1C: Validating Page Index Errors (Unsubmitted Pages Only)...")
        unsubmitted_pages_url = f"https://search.google.com/search-console/index?resource_id=sc-domain:{property}&pages=ALL_NON_SUBMITTED_URLS"
        result = validate_page_indexing_errors(
            unsubmitted_pages_url, "Unsubmitted Pages Only"
        )
        if result == "RATE_LIMITED":
            log(
                "🛑 Validation aborted due to Google rate limiting (429). Please try again later.",
                "error",
            )
            return False

        # --- Phase 3: Validate Sitemap Specific Sections ---
        log("✅ Step 2: Moving to Sitemap Validation Section...")
        total = len(sitemaps)
        if total == 0:
            log("❌ No sitemap entries found for inner validation.", "error")
            return False

        log(f"📄 Found {total} sitemaps.")
        skipped = 0
        for i, sitemap_url in enumerate(sitemaps, start=1):
            if _stop_requested:
                log("🛑 Validation stopped by user.", "warning")
                return False
            try:
                if i > 1:
                    time.sleep(3)  # Small delay between sitemaps to reduce 429 risk
                log(f"\n🔍 Checking sitemap ({i}/{total}): {sitemap_url}")
                sitemap_target_url = f"https://search.google.com/search-console/index?resource_id=sc-domain:{property}&pages=SITEMAP&sitemap={sitemap_url}"
                result = validate_page_indexing_errors(
                    sitemap_target_url, f"Sitemap {i} of {total}"
                )
                if result == "RATE_LIMITED":
                    log(
                        "🛑 Validation aborted due to Google rate limiting (429). Please try again later.",
                        "error",
                    )
                    return False
            except Exception as sitemap_err:
                if _stop_requested: raise sitemap_err
                skipped += 1
                log(
                    f"⚠️ Skipping sitemap {i}/{total} due to error: {sitemap_err}",
                    "warning",
                )
                continue
        if skipped:
            log(f"⚠️ {skipped}/{total} sitemaps were skipped due to errors.", "warning")

        return True  # 🟢 Success

    except Exception as e:
        log(f"❌ Error during validation: {e}", "error")
        return False  # 🚩


def submit_all_sitemaps(
    property_name: str,
    sitemaps: list,
    sa_info: dict,
    log=print,
    delete_stale=False,
    do_submit=True,
    full_sitemap_list=None,
):
    """
    Submit all sitemap URLs to Google Search Console using a Service Account JSON.
    """
    ok = True  # ✅ Track overall success

    # ✅ STEP 1 — Validate SA Info
    if not sa_info:
        log(
            "❌ No Service Account successfully loaded. Have you mapped one for this country?",
            "error",
        )
        return False
    else:
        log("✅ Using: Database Service Account JSON")

    SCOPES = ["https://www.googleapis.com/auth/webmasters"]

    # ✅ STEP 2 — Build property for GSC
    site_url = f"sc-domain:{property_name}"
    log(f"🌍 Submitting for property: {site_url}")

    # ✅ STEP 3 - Authenticate to GSC using Service Account Flow
    def get_gsc_service():
        creds = service_account.Credentials.from_service_account_info(
            sa_info, scopes=SCOPES
        )
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
            status = getattr(e, "status_code", None) or (
                e.resp.status if hasattr(e, "resp") else "unknown"
            )
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
    if do_submit:
        log(f"🔍 Found {len(sitemaps)} sitemaps to submit…")
        for sm in sitemaps:
            submit_sitemap(sm)
    else:
        log("ℹ️ Skipping sitemap submission on Google (disabled by user).")

    # ✅ STEP 6 — Remove OLD / stale sitemaps from GSC
    if not delete_stale:
        log("ℹ️ Skipping deletion of old/stale sitemaps on Google (disabled by user).")
        return ok

    log("🧹 Checking for old/stale sitemaps to remove…")
    try:
        # Build a normalized set of the newly submitted URLs for fast lookup
        sitemap_list_to_use = full_sitemap_list if full_sitemap_list is not None else sitemaps
        new_sitemap_set = set(s.strip().rstrip("/") for s in sitemap_list_to_use)

        # List all sitemaps currently registered in GSC
        response = service.sitemaps().list(siteUrl=site_url).execute()
        existing_sitemaps = response.get("sitemap", [])

        if not existing_sitemaps:
            log("ℹ️ No existing sitemaps found in GSC to clean up.")
        else:
            removed_count = 0
            for sitemap_entry in existing_sitemaps:
                registered_url = sitemap_entry.get("path", "").strip().rstrip("/")
                if not registered_url:
                    continue

                # Check if sitemap has errors (like Couldn't fetch)
                errors_count = int(sitemap_entry.get("errors", 0))
                is_failed = errors_count > 0
                
                if is_failed:
                    try:
                        service.sitemaps().delete(
                            siteUrl=site_url, feedpath=registered_url
                        ).execute()
                        log(f"🗑️ Removed failed sitemap (Errors: {errors_count}): {registered_url}", "warn")
                        removed_count += 1
                    except HttpError as he:
                        hs = getattr(he, "status_code", None) or (
                            he.resp.status if hasattr(he, "resp") else "unknown"
                        )
                        log(
                            f"⚠️ Could not remove {registered_url} (HTTP {hs}) - skipping.",
                            "warn",
                        )
                    except Exception as de:
                        if _stop_requested: raise de
                        log(f"⚠️ Could not remove {registered_url}: {de}", "warn")
                elif delete_stale and registered_url not in new_sitemap_set:
                    try:
                        service.sitemaps().delete(
                            siteUrl=site_url, feedpath=registered_url
                        ).execute()
                        log(f"🗑️ Removed stale sitemap: {registered_url}", "warn")
                        removed_count += 1
                    except HttpError as he:
                        hs = getattr(he, "status_code", None) or (
                            he.resp.status if hasattr(he, "resp") else "unknown"
                        )
                        log(
                            f"⚠️ Could not remove {registered_url} (HTTP {hs}) - skipping.",
                            "warn",
                        )
                    except Exception as de:
                        if _stop_requested: raise de
                        log(f"⚠️ Could not remove {registered_url}: {de}", "warn")

            if removed_count == 0:
                log("✅ No stale sitemaps found - GSC is already clean.")
            else:
                log(f"✅ Removed {removed_count} old sitemap(s) from GSC.", "success")

    except Exception as e:
        log(f"⚠️ Cleanup step failed (non-critical): {e}", "warn")

    # No Tokens to delete for Service Accounts
    return ok


# ==================================================
# BING WEBMASTER LOGIC
# ==================================================
def run_bing_process(
    property_val,
    country,
    log,
    latestSitemap,
    output_folder,
    do_submit=True,
    delete_stale=False,
    full_sitemap_list=None,
):
    driver = None  # Initialize to None to prevent errors in 'finally' block

    # --- Helper Functions ---
    def overlay_present(driver):
        """Checks if Bing's dark overlay is blocking clicks."""
        try:
            overlays = driver.find_elements(
                By.CSS_SELECTOR, "div.ms-Overlay.ms-Overlay--dark"
            )
            return any(o.is_displayed() for o in overlays)
        except:
            return False

    def is_error_500(driver):
        """Checks for Bing server errors."""
        try:
            return (
                "Error500" in driver.current_url
                or "Error/Error500" in driver.current_url
            )
        except:
            return False

    def safe_reload(driver):
        """Reloads the page and waits for it to stabilize."""
        log("🔄 Reloading page to clear errors/overlays...", "warning")
        try:
            driver.get(
                f"https://www.bing.com/webmasters/sitemaps?siteUrl=https://{property_val}/"
            )

            # Wait for body
            WebDriverWait(driver, 20).until(
                EC.presence_of_element_located((By.TAG_NAME, "body"))
            )

            # Wait for overlays to disappear
            try:
                WebDriverWait(driver, 10).until(
                    EC.invisibility_of_element_located(
                        (By.CSS_SELECTOR, "div.ms-Overlay")
                    )
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
            driver.execute_script(
                """
                arguments[0].value = arguments[1];
                arguments[0].dispatchEvent(new Event('input', { bubbles: true }));
                arguments[0].dispatchEvent(new Event('change', { bubbles: true }));
                arguments[0].blur();
            """,
                element,
                text,
            )
            time.sleep(0.5)
            return element.get_attribute("value") == text
        except Exception as e:
            log(f"❌ JS Injection failed: {e}", "error")
            return False

    def cleanup_bing_sitemaps(driver, property_val, latestSitemap, log, full_sitemap_list=None):
        """
        Check for old/stale or failed sitemaps on Bing Webmaster Tools and delete them.
        """
        log(
            "🧹 Checking for old/stale or failed sitemaps on Bing Webmaster Tools...",
            "info",
        )
        try:
            # 1. Ensure we are on the sitemaps page
            target_url = f"https://www.bing.com/webmasters/sitemaps?siteUrl=https://{property_val}/"
            if not driver.current_url.startswith(target_url):
                driver.get(target_url)

            wait = WebDriverWait(driver, 30)
            # Wait for table to load by checking for any row
            try:
                wait.until(
                    EC.presence_of_element_located(
                        (By.CSS_SELECTOR, "tr, div[role='row']")
                    )
                )
            except Exception as e:
                log(
                    f"ℹ️ No sitemaps found in Bing Webmaster table or page load timeout. Error: {str(e)}",
                    "info",
                )
                return

            time.sleep(3)

            # Helper to normalize URLs
            def normalize_url(url):
                if not url:
                    return ""
                u = url.strip().lower()
                u = re.sub(r"^https?://", "", u)
                u = re.sub(r"^www\.", "", u)
                u = u.rstrip("/")
                return u

            def extract_clean_url(cell_or_element):
                try:
                    href = cell_or_element.get_attribute("href") or ""
                    title = cell_or_element.get_attribute("title") or ""
                    inner_html = cell_or_element.get_attribute("innerHTML") or ""
                    txt = (
                        cell_or_element.text.strip()
                        .replace(" ", "")
                        .replace("\n", "")
                        .replace("\r", "")
                    )
                    for candidate in [href, title, inner_html, txt]:
                        if candidate:
                            url_match = re.search(
                                r"(https?://[^\s\"\'<>]+)",
                                candidate,
                                re.IGNORECASE,
                            )
                            if url_match:
                                return url_match.group(1)
                except:
                    pass
                return None

            sitemap_list_to_use = full_sitemap_list if full_sitemap_list is not None else latestSitemap
            new_sitemaps_normalized = {normalize_url(u) for u in sitemap_list_to_use}

            # 2. Try to expand to maximum rows per page (500)
            try:
                dropdown = None
                dropdown_locators = [
                    (
                        By.XPATH,
                        "//div[contains(text(), 'Rows per page')]/following-sibling::div[@role='combobox']",
                    ),
                    (
                        By.XPATH,
                        "//*[contains(text(), 'Rows per page')]/..//div[@role='combobox' or @role='listbox']",
                    ),
                    (
                        By.CSS_SELECTOR,
                        "div[role='listbox'][aria-label*='rows per page'], div[role='combobox'][aria-label*='Rows per page']",
                    ),
                    (
                        By.CSS_SELECTOR,
                        "div[aria-label*='rows per page'], div[aria-label*='Rows per page']",
                    ),
                    (
                        By.XPATH,
                        "//*[contains(text(), 'Rows per page')]/following::div[1]",
                    ),
                ]
                for by, locator in dropdown_locators:
                    try:
                        dropdown = driver.find_element(by, locator)
                        if dropdown.is_displayed():
                            break
                    except:
                        continue

                if not dropdown:
                    dropdown = wait.until(
                        EC.presence_of_element_located(
                            (
                                By.CSS_SELECTOR,
                                "div[role='listbox'][aria-label*='rows per page'], div[role='combobox'][aria-label*='Rows per page']",
                            )
                        )
                    )

                if dropdown:
                    driver.execute_script("arguments[0].click();", dropdown)
                    time.sleep(2)
                    clicked = driver.execute_script("""
                        var targets = ['500', '250', '100', '50', '25'];
                        var opts = document.querySelectorAll("button[role='option'], div[role='option'], span[class*='optionText']");
                        for (var t = 0; t < targets.length; t++) {
                            for (var i = 0; i < opts.length; i++) {
                                if (opts[i].textContent.trim() === targets[t]) {
                                    // if it's a span inside a button, click the parent button
                                    var el = opts[i];
                                    while (el && el.tagName !== 'BUTTON' && el.tagName !== 'DIV') {
                                        if (el.getAttribute('role') === 'option') break;
                                        el = el.parentElement;
                                    }
                                    if (el) {
                                        el.click();
                                        return targets[t];
                                    }
                                }
                            }
                        }
                        return null;
                    """)
                    if clicked:
                        log(
                            f"📋 Set Bing sitemap rows to {clicked} per page for full coverage.",
                            "info",
                        )
                time.sleep(3)
            except Exception as dropdown_err:
                if _stop_requested: raise dropdown_err
                log(
                    f"ℹ️ Could not expand Bing rows per page (non-critical): {dropdown_err}"
                )

            # Zoom out to ensure virtualized rows (like Fabric UI DetailsList) render all rows in the DOM
            try:
                driver.execute_script("document.body.style.zoom = '25%';")
                time.sleep(2)
            except:
                pass

            attempted_deletions = set()
            total_deleted = 0
            page_num = 1
            while True:
                # Find all rows currently listed
                rows = driver.find_elements(By.CSS_SELECTOR, "tr, div[role='row']")
                to_delete = []

                for row in rows:
                    try:
                        # Extract sitemap URL
                        cells = row.find_elements(
                            By.CSS_SELECTOR,
                            "td, div[role='gridcell'], div[class*='cell']",
                        )
                        if not cells:
                            continue

                        sitemap_url = None
                        url_cell_idx = -1

                        # Look for URL in cells
                        for idx, cell in enumerate(cells):
                            cleaned_val = extract_clean_url(cell)
                            if cleaned_val:
                                sitemap_url = cleaned_val
                                url_cell_idx = idx
                                break

                        # Deeper look if needed
                        if not sitemap_url:
                            for idx, cell in enumerate(cells):
                                links = cell.find_elements(By.TAG_NAME, "a")
                                for lnk in links:
                                    cleaned_val = extract_clean_url(lnk)
                                    if cleaned_val:
                                        sitemap_url = cleaned_val
                                        url_cell_idx = idx
                                        break
                                if sitemap_url:
                                    break

                        if not sitemap_url:
                            continue

                        # Extract status using whole row text for robustness
                        status_text = "Unknown"
                        try:
                            row_text = row.text.lower()
                            if sitemap_url:
                                row_text = row_text.replace(sitemap_url.lower(), "")
                                
                            if "success" in row_text:
                                status_text = "Success"
                            elif "could not fetch" in row_text or "couldn't fetch" in row_text:
                                status_text = "Could not fetch"
                            elif "error" in row_text:
                                status_text = "Error"
                            elif "failed" in row_text:
                                status_text = "Failed"
                            elif "processing" in row_text:
                                status_text = "Processing"
                            elif "pending" in row_text:
                                status_text = "Pending"
                        except Exception:
                            pass

                        # Decide if irrelevant
                        norm_url = normalize_url(sitemap_url)

                        is_failed = status_text in [
                            "Could not fetch",
                            "Error",
                            "Failed",
                        ]

                        is_stale = False
                        if delete_stale:
                            if "..." not in norm_url:
                                is_stale = norm_url not in new_sitemaps_normalized
                            else:
                                parts = norm_url.split("...")
                                matched_any = False
                                for valid_sitemap in new_sitemaps_normalized:
                                    last_idx = 0
                                    matches = True
                                    for part in parts:
                                        if not part: continue
                                        idx = valid_sitemap.find(part, last_idx)
                                        if idx == -1:
                                            matches = False
                                            break
                                        last_idx = idx + len(part)
                                    if matches:
                                        matched_any = True
                                        break
                                is_stale = not matched_any

                        if is_failed:
                            reason = f"Failed status ({status_text})"
                            if sitemap_url not in attempted_deletions:
                                to_delete.append((sitemap_url, reason))
                        elif is_stale:
                            reason = "Stale (not in current submission list)"
                            if sitemap_url not in attempted_deletions:
                                to_delete.append((sitemap_url, reason))
                                
                        # DEBUG
                        if "145" in sitemap_url or "error" in row.text.lower():
                            log(f"DEBUG ROW: url='{sitemap_url}', status='{status_text}', failed={is_failed}, stale={is_stale}")

                    except Exception as row_err:
                        if _stop_requested: raise row_err
                        continue

                if to_delete:
                    log(
                        f"🗑️ Found {len(to_delete)} irrelevant sitemap(s) on page {page_num} to delete.",
                        "info",
                    )
                    selected_count = 0

                    for url, reason in to_delete:
                        # Re-locate rows dynamically to avoid stale elements
                        fresh_rows = driver.find_elements(
                            By.CSS_SELECTOR, "tr, div[role='row']"
                        )
                        checkbox_clicked = False

                        for row in fresh_rows:
                            try:
                                cells = row.find_elements(
                                    By.CSS_SELECTOR,
                                    "td, div[role='gridcell'], div[class*='cell']",
                                )
                                if not cells:
                                    continue

                                sitemap_url = None
                                url_cell_idx = -1
                                for idx, cell in enumerate(cells):
                                    cleaned_val = extract_clean_url(cell)
                                    if cleaned_val:
                                        sitemap_url = cleaned_val
                                        url_cell_idx = idx
                                        break

                                if not sitemap_url:
                                    for idx, cell in enumerate(cells):
                                        links = cell.find_elements(By.TAG_NAME, "a")
                                        for lnk in links:
                                            cleaned_val = extract_clean_url(lnk)
                                            if cleaned_val:
                                                sitemap_url = cleaned_val
                                                url_cell_idx = idx
                                                break
                                        if sitemap_url:
                                            break

                                if sitemap_url and normalize_url(
                                    sitemap_url
                                ) == normalize_url(url):
                                    # Find checkbox
                                    checkbox_el = None
                                    try:
                                        checkbox_el = row.find_element(
                                            By.CSS_SELECTOR,
                                            "div[role='checkbox'], input[type='checkbox'], div[class*='ms-Check'], div[class*='Check'], span[class*='ms-Check']",
                                        )
                                    except:
                                        try:
                                            checkbox_el = cells[0].find_element(
                                                By.CSS_SELECTOR, "div, span, input"
                                            )
                                        except:
                                            pass

                                    is_checked = False
                                    if checkbox_el:
                                        aria_checked = checkbox_el.get_attribute(
                                            "aria-checked"
                                        )
                                        if aria_checked == "true":
                                            is_checked = True
                                        classes = (
                                            checkbox_el.get_attribute("class") or ""
                                        )
                                        if (
                                            "is-checked" in classes.lower()
                                            or "is-selected" in classes.lower()
                                        ):
                                            is_checked = True
                                        if (
                                            checkbox_el.tag_name == "input"
                                            and checkbox_el.is_selected()
                                        ):
                                            is_checked = True

                                    row_selected = row.get_attribute("aria-selected")
                                    if row_selected == "true":
                                        is_checked = True
                                    row_classes = row.get_attribute("class") or ""
                                    if "is-selected" in row_classes.lower():
                                        is_checked = True

                                    if not is_checked:
                                        if checkbox_el:
                                            driver.execute_script(
                                                "arguments[0].click();", checkbox_el
                                            )
                                        else:
                                            driver.execute_script(
                                                "arguments[0].click();", row
                                            )
                                        log(
                                            f"   ☑️ Checked for deletion ({reason}): {url}",
                                            "info",
                                        )
                                    else:
                                        log(
                                            f"   ☑️ Already checked for deletion ({reason}): {url}",
                                            "info",
                                        )

                                    checkbox_clicked = True
                                    selected_count += 1
                                    attempted_deletions.add(url)
                                    time.sleep(0.5)
                                    break
                            except:
                                continue

                        if not checkbox_clicked:
                            log(f"   ⚠️ Could not re-locate row for: {url}", "warning")

                    if selected_count > 0:
                        # Click the Delete button in the bottom bar
                        time.sleep(2)
                        delete_btn = None
                        locators = [
                            (
                                By.XPATH,
                                "//span[contains(@class, 'overlayButton') and @aria-label='Delete']",
                            ),
                            (
                                By.XPATH,
                                "//span[contains(@class, 'overlayButton')][.//span[text()='Delete']]",
                            ),
                            (
                                By.XPATH,
                                "//*[contains(@class, 'overlayButton') and contains(., 'Delete')]",
                            ),
                            (
                                By.XPATH,
                                "//span[@role='button' and @aria-label='Delete']",
                            ),
                            (By.XPATH, "//*[@aria-label='Delete' and @role='button']"),
                            (By.XPATH, "//button[.//span[text()='Delete']]"),
                            (By.XPATH, "//button[contains(., 'Delete')]"),
                            (
                                By.XPATH,
                                "//div[contains(@class, 'bar')]//button[contains(., 'Delete')]",
                            ),
                            (By.XPATH, "//*[text()='Delete']/ancestor::button"),
                            (By.CSS_SELECTOR, "button[title='Delete']"),
                            (By.CSS_SELECTOR, "button[aria-label='Delete']"),
                        ]
                        for by, locator in locators:
                            try:
                                btn = driver.find_element(by, locator)
                                if btn.is_displayed() and btn.is_enabled():
                                    delete_btn = btn
                                    break
                            except:
                                continue

                        if delete_btn:
                            log("🗑️ Clicking 'Delete' button in bottom bar...", "info")
                            driver.execute_script("arguments[0].click();", delete_btn)
                            time.sleep(2)

                            # Handle confirmation dialog
                            confirm_btn = None
                            dialog_locators = [
                                (
                                    By.XPATH,
                                    "//div[@role='dialog']//*[contains(@class, 'Button--primary') or contains(@class, 'primary')]//*[text()='Delete']/ancestor::button",
                                ),
                                (
                                    By.XPATH,
                                    "//div[@role='dialog']//*[text()='Delete']/ancestor::button",
                                ),
                                (
                                    By.XPATH,
                                    "//div[@role='dialog']//*[text()='Delete']/ancestor::*[@role='button']",
                                ),
                                (
                                    By.XPATH,
                                    "//div[@role='dialog']//*[contains(text(), 'Delete')]",
                                ),
                                (
                                    By.XPATH,
                                    "//div[@role='dialog']//button[.//span[text()='Delete']]",
                                ),
                                (
                                    By.XPATH,
                                    "//div[@role='dialog']//button[contains(., 'Delete')]",
                                ),
                                (
                                    By.XPATH,
                                    "//div[contains(@class, 'modal') or contains(@class, 'dialog')]//button[contains(., 'Delete')]",
                                ),
                                (
                                    By.XPATH,
                                    "//button[contains(@class, 'confirm') or contains(@class, 'primary')]//span[text()='Delete']",
                                ),
                                (By.XPATH, "//button[contains(., 'Delete')]"),
                            ]
                            for by, locator in dialog_locators:
                                try:
                                    elements = driver.find_elements(by, locator)
                                    for el in elements:
                                        if el.is_displayed() and el.is_enabled():
                                            confirm_btn = el
                                            break
                                    if confirm_btn:
                                        break
                                except:
                                    continue

                            if confirm_btn:
                                log("⚠️ Confirming deletion in dialog...", "info")
                                driver.execute_script(
                                    "arguments[0].click();", confirm_btn
                                )
                                log(
                                    f"✅ Successfully deleted {selected_count} sitemap(s) from Bing.",
                                    "success",
                                )
                                total_deleted += selected_count
                            else:
                                log(
                                    "⚠️ Could not find confirmation 'Delete' button in dialog, sending ENTER to active element...",
                                    "warning",
                                )
                                from selenium.webdriver.common.keys import Keys

                                try:
                                    driver.switch_to.active_element.send_keys(Keys.ENTER)
                                except Exception as e:
                                    log(f"⚠️ Fallback ENTER failed: {e}", "error")
                                
                                log(
                                    f"✅ Executed Enter key fallback to confirm deletion of {selected_count} sitemap(s).",
                                    "success",
                                )
                                total_deleted += selected_count

                            time.sleep(5)
                            try:
                                log(
                                    "🔄 Reloading Bing sitemaps page to update status...",
                                    "info",
                                )
                                driver.refresh()
                                WebDriverWait(driver, 10).until(
                                    EC.presence_of_element_located(
                                        (By.CSS_SELECTOR, "tr, div[role='row']")
                                    )
                                )
                                time.sleep(3)
                            except:
                                pass
                            # Restart from page 1 since DOM shifted/reloaded
                            page_num = 1
                            continue
                        else:
                            log("❌ Delete button not found in bottom bar.", "error")

                # Handle pagination if any
                try:
                    next_btn = driver.find_element(
                        By.XPATH, "//button[contains(@aria-label, 'ext') and contains(@aria-label, 'age') and not(@aria-disabled='true') and not(@disabled)]"
                    )
                    driver.execute_script("arguments[0].click();", next_btn)
                    time.sleep(3)
                    page_num += 1
                except:
                    # No more pages or next button disabled
                    break

            if total_deleted > 0:
                log(
                    f"✅ Bing sitemap cleanup completed successfully. Deleted {total_deleted} stale/failed sitemap(s).",
                    "success",
                )
            else:
                log("☑️ No stale sitemaps found - Bing is already clean.", "info")

        except Exception as cleanup_err:
            if _stop_requested: raise cleanup_err
            log(
                f"⚠️ Bing sitemap cleanup failed (non-critical): {cleanup_err}",
                "warning",
            )

    # --- Main Logic ---
    ok = True
    try:
        log("🌐 Starting Bing automation...", "info")
        driver, _ = setup_driver_with_session(
            country, "bing", headless=dev_headless_var.get(), output_folder=output_folder
        )
        sitemaps = latestSitemap

        if not sitemaps:
            log("❌ No sitemap URLs found.", "error")
            return False

        total = len(sitemaps)

        # Navigate directly to Webmaster Tools (session has login state)
        driver.get(
            f"https://www.bing.com/webmasters/sitemaps?siteUrl=https://{property_val}/"
        )

        # 4. Check Auth
        try:
            log("⏳ Verifying login status on Bing Webmaster Tools...", "info")
            login_success = False
            for attempt in range(25):
                curr_url = driver.current_url.lower()
                curr_title = driver.title.lower()

                # Check for redirection to Microsoft login/landing page (failure)
                if (
                    "login.live.com" in curr_url
                    or "login.microsoft" in curr_url
                    or "webmasters/about" in curr_url
                    or "sign in" in curr_title
                    or "login" in curr_title
                ):
                    log(
                        "❌ Cookies expired or login failed (Redirected to sign-in page).",
                        "error",
                    )
                    return False

                # Check for positive indicators
                try:
                    # Check if 'Submit sitemap' button is present and visible
                    submit_sitemap_btn = driver.find_elements(
                        By.XPATH, "//button[.//span[text()='Submit sitemap']]"
                    )
                    if submit_sitemap_btn and submit_sitemap_btn[0].is_displayed():
                        login_success = True
                        break
                except:
                    pass

                # Sitemaps list table elements, or the main sitemaps container
                try:
                    sitemaps_container = driver.find_elements(
                        By.CSS_SELECTOR,
                        "tr, div[role='row'], td.RVEMNe, [class*='containerLayout']",
                    )
                    if sitemaps_container:
                        login_success = True
                        break
                except:
                    pass

                time.sleep(1)

            if not login_success:
                # Check body text for explicit unauthorized message
                try:
                    body_text = driver.find_element(By.TAG_NAME, "body").text
                    if (
                        "User is unauthorized" in body_text
                        or "unauthorized" in body_text.lower()
                    ):
                        log(
                            "❌ Cookies expired or login failed (Unauthorized).",
                            "error",
                        )
                        return False
                except:
                    pass

                # Fallback: check if we are on the correct page URL
                if (
                    "webmasters/sitemaps" in driver.current_url
                    or "webmasters/sitemap" in driver.current_url
                ):
                    log(
                        "⚠️ Login container check timed out, but URL indicates sitemaps page. Proceeding...",
                        "warning",
                    )
                else:
                    raise Exception("Sitemaps page elements not detected.")

            log("✅ Logged in successfully!", "success")
        except Exception as e:
            log(f"❌ Login check failed (Page load issue): {e}", "error")
            return False

        # ---------- SUBMISSION LOOP ----------
        if do_submit:
            current_index = 0
            max_retries_per_item = 3
            current_item_retries = 0

            while current_index < total:
                sitemap = sitemaps[current_index]

                try:
                    # 🚨 Loop Safety: Prevent infinite retries on one item
                    if current_item_retries >= max_retries_per_item:
                        log(
                            f"❌ Skipping {sitemap} after {max_retries_per_item} failed attempts.",
                            "error",
                        )
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
                            EC.element_to_be_clickable(
                                (By.XPATH, "//button[contains(translate(., 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'submit sitemap')]")
                            )
                        )
                        driver.execute_script("arguments[0].click();", submit_btn)
                    except Exception:
                        # If button not found, we might need a reload
                        raise Exception("Submit button not found or not clickable")

                    # Wait for Input Box
                    input_box = WebDriverWait(driver, 10).until(
                        EC.visibility_of_element_located(
                            (By.XPATH, "//input[@aria-label='Sitemap URL']")
                        )
                    )

                    # ✅ Enter Text Robustly (Fixes the stuck/incomplete typing)
                    if not robust_enter_text(driver, input_box, sitemap):
                        raise Exception("Failed to verify sitemap URL in input box")

                    # Submit Now
                    submit_now = WebDriverWait(driver, 10).until(
                        EC.element_to_be_clickable(
                            (By.XPATH, "//button[contains(translate(., 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'submit') and not(contains(translate(., 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'sitemap'))]")
                        )
                    )
                    driver.execute_script("arguments[0].click();", submit_now)

                    # Wait to see if error appears or modal closes
                    import time as time_module
                    start_wait = time_module.time()
                    modal_closed = False
                    error_text = "Unknown error"
                    has_error = False
                    
                    while time_module.time() - start_wait < 20:
                        try:
                            # Grab ALL text inside the modal and check it
                            dialogs = driver.find_elements(By.XPATH, "//div[@role='dialog']")
                            if dialogs:
                                # Use textContent/innerText instead of .text for headless compatibility!
                                d_text = (dialogs[0].get_attribute('textContent') or dialogs[0].get_attribute('innerText') or dialogs[0].text).lower()
                                if "momentary" in d_text or "unexpected" in d_text or "try again" in d_text or "error occurred" in d_text:
                                    has_error = True
                                    error_text = "Bing Rate Limit/Server Error"
                                    break
                                
                            # If no error, verify if the submit button still exists in the DOM
                            # (Bypassing is_displayed() which is buggy in Headless Chrome)
                            _ = submit_now.get_attribute('class')
                        except:
                            # StaleElementReferenceException means the modal closed and destroyed the button!
                            modal_closed = True
                            break
                        time_module.sleep(0.5)

                    if has_error:
                        # Click Cancel to close the broken modal
                        try:
                            cancel_btn = driver.find_element(By.XPATH, "//button[.//span[text()='Cancel']]")
                            driver.execute_script("arguments[0].click();", cancel_btn)
                            time_module.sleep(1)
                        except:
                            pass
                        # We include "element click intercepted" in the exception string so the outer loop catches it, reloads the page, and triggers a retry!
                        raise Exception(f"element click intercepted: {error_text} (Triggering auto-retry)")
                    elif not modal_closed:
                        # Timed out waiting for modal to close or show error
                        try:
                            cancel_btn = driver.find_element(By.XPATH, "//button[.//span[text()='Cancel']]")
                            driver.execute_script("arguments[0].click();", cancel_btn)
                            time_module.sleep(1)
                        except:
                            pass
                        raise Exception("element click intercepted: Timed out waiting for Bing to respond (Triggering auto-retry)")

                    # If we reach here, modal closed successfully
                    log(
                        f"✅ ({current_index+1}/{total}) Submitted: {sitemap}",
                        "success",
                    )
                    current_index += 1
                    current_item_retries = 0  # Reset retry count on success
                    time.sleep(3)  # Small buffer

                except Exception as e:
                    err_msg = str(e)
                    # Recoverable UI issues
                    if (
                        "element click intercepted" in err_msg
                        or overlay_present(driver)
                        or is_error_500(driver)
                    ):
                        log(f"⚠️ UI Interference on {sitemap}, retrying...", "warning")
                        safe_reload(driver)
                        current_item_retries += 1
                        continue

                    log(f"❌ Failed sitemap: {sitemap} → {e}", "error")
                    ok = False
                    # If it's a critical error not fixed by reload, skip item
                    current_index += 1
                    current_item_retries = 0

        else:
            log("ℹ️ Skipping sitemap submission on Bing (disabled by user).", "info")

        # ---------- CLEANUP IRRELEVANT SITEMAPS ----------
        if delete_stale:
            try:
                cleanup_bing_sitemaps(driver, property_val, sitemaps, log, full_sitemap_list=full_sitemap_list)
            except Exception as cleanup_err:
                if _stop_requested: raise cleanup_err
                log(
                    f"⚠️ Bing sitemap cleanup failed (non-critical): {cleanup_err}",
                    "warning",
                )
        else:
            log(
                "ℹ️ Skipping deletion of old/stale sitemaps on Bing (disabled by user)."
            )

        if ok and do_submit:
            log("🎉 All Bing sitemaps submitted successfully!", "success")

    except Exception as e:
        log(f"❌ Unexpected fatal error: {e}", "error")
        ok = False

    finally:
        if driver:
            safe_quit_driver(driver)

    return ok


# ----------------------------
# TKINTER GUI APP
# ----------------------------
def check_gsc_sitemap_status_via_browser(
    driver, property_val, log, sa_info=None, delete_stale=False
):
    """
    Navigate to the GSC Sitemaps page via Selenium and read the REAL
    'Couldn't fetch' / 'Success' status directly from the UI table.
    Handles pagination to ensure ALL sitemaps are checked, not just the first page.
    If sa_info is provided, automatically removes all 'Couldn't Fetch' sitemaps via API.
    """
    try:
        log("📊 Reading actual sitemap status from Google Search Console…")
        driver.get(
            f"https://search.google.com/search-console/sitemaps?resource_id=sc-domain:{property_val}"
        )
        wait = WebDriverWait(driver, 30)

        # Wait for the sitemaps table to load
        wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "td.RVEMNe")))
        time.sleep(2)

        # ── Expand to 500 rows per page ──────────────────────────────────
        # Uses text-content matching (reliable across GSC versions)
        # This mirrors the same approach proven in extract_sitemaps()
        try:
            dropdown = wait.until(
                EC.element_to_be_clickable(
                    (
                        By.CSS_SELECTOR,
                        "div[role='listbox'][aria-label='Number of rows per page']",
                    )
                )
            )
            dropdown.click()
            time.sleep(2)  # wait for options to render

            # Click the largest available option (500 → 250 → 100) by text
            clicked = driver.execute_script("""
                var targets = ['500', '250', '100'];
                var opts = document.querySelectorAll("div[role='option']");
                for (var t = 0; t < targets.length; t++) {
                    for (var i = 0; i < opts.length; i++) {
                        if (opts[i].textContent.trim() === targets[t]) {
                            opts[i].click();
                            return targets[t];
                        }
                    }
                }
                return null;
            """)
            if clicked:
                log(f"📋 Set GSC sitemap rows to {clicked} per page for full coverage.")
            time.sleep(3)  # wait for table to re-render with more rows
        except Exception as dropdown_err:
            if _stop_requested: raise dropdown_err
            log(
                f"ℹ️ Could not expand rows per page: {dropdown_err} — reading visible rows only."
            )

        could_not_fetch = []
        success_list = []
        other_list = []

        def scrape_current_page():
            rows = driver.find_elements(By.CSS_SELECTOR, "tr")
            for row in rows:
                try:
                    url_cells = row.find_elements(By.CSS_SELECTOR, "td.RVEMNe")
                    if not url_cells:
                        continue
                    sitemap_url = url_cells[0].text.strip()
                    if not sitemap_url:
                        continue

                    status_spans = row.find_elements(By.CSS_SELECTOR, "span.Ncxbed")
                    if not status_spans:
                        continue
                    status_text = status_spans[0].text.strip()

                    if (
                        "fetch" in status_text.lower()
                        or "couldn" in status_text.lower()
                    ):
                        could_not_fetch.append(sitemap_url)
                    elif "success" in status_text.lower():
                        success_list.append(sitemap_url)
                    else:
                        other_list.append((sitemap_url, status_text))
                except:
                    continue

        # ── Scrape first (and usually only) page ────────────────────────
        scrape_current_page()

        # ── Handle pagination: click Next until disabled ─────────────────
        page_num = 1
        while True:
            try:
                next_btn = driver.find_element(
                    By.XPATH, "//button[@aria-label='Next page' and not(@disabled)]"
                )
                driver.execute_script("arguments[0].click();", next_btn)
                time.sleep(2)
                wait.until(
                    EC.presence_of_element_located((By.CSS_SELECTOR, "td.RVEMNe"))
                )
                page_num += 1
                scrape_current_page()
            except:
                break  # No more pages or next button disabled

        if page_num > 1:
            log(f"📄 Scanned {page_num} page(s) of sitemaps.")

        # ── Final Report ─────────────────────────────────────────────────
        total_checked = len(could_not_fetch) + len(success_list) + len(other_list)
        log(f"📊 Total sitemaps checked: {total_checked}")

        if could_not_fetch:
            log(
                f"⚠️ {len(could_not_fetch)} sitemap(s) have 'Couldn't Fetch' status in GSC:",
                "warn",
            )
            for url in could_not_fetch:
                log(f"   🔴 {url}", "error")
            log(
                "⚠️ These sitemaps could not be fetched by Google. Check server accessibility or Cloudflare rules.",
                "warn",
            )

            # ── Auto-remove 'Couldn't Fetch' sitemaps via GSC API ────────
            if sa_info and delete_stale:
                log(
                    f"🗑️ Auto-removing {len(could_not_fetch)} 'Couldn't Fetch' sitemap(s) from GSC…"
                )
                try:
                    from google.oauth2 import service_account as _sa
                    from googleapiclient.discovery import build as _build

                    _SCOPES = ["https://www.googleapis.com/auth/webmasters"]
                    _creds = _sa.Credentials.from_service_account_info(
                        sa_info, scopes=_SCOPES
                    )
                    _service = _build("webmasters", "v3", credentials=_creds)
                    _site_url = f"sc-domain:{property_val}"
                    removed_count = 0
                    for bad_url in could_not_fetch:
                        try:
                            _service.sitemaps().delete(
                                siteUrl=_site_url, feedpath=bad_url
                            ).execute()
                            log(f"   🗑️ Deleted: {bad_url}", "success")
                            removed_count += 1
                        except Exception as _del_err:
                            if _stop_requested: raise _del_err
                            log(f"   ⚠️ Could not delete {bad_url}: {_del_err}", "warn")
                    log(
                        f"✅ Removed {removed_count}/{len(could_not_fetch)} 'Couldn't Fetch' sitemap(s) from GSC.",
                        "success",
                    )
                except Exception as _api_err:
                    if _stop_requested: raise _api_err
                    log(f"⚠️ Auto-removal failed (non-critical): {_api_err}", "warn")
            elif not delete_stale:
                log(
                    "ℹ️ 'Delete old/stale sitemaps' is not checked — skipping auto-removal of failed sitemaps."
                )
            else:
                log(
                    "ℹ️ No service account available — skipping auto-removal of failed sitemaps."
                )
        else:
            log("✅ No 'Couldn't Fetch' errors found in GSC.", "success")

        if success_list:
            log(f"✅ {len(success_list)} sitemap(s) have 'Success' status.", "success")

        for url, st in other_list:
            log(f"ℹ️ [{st}] {url}")

    except Exception as e:
        log(f"⚠️ GSC browser status check failed (non-critical): {e}", "warn")


_is_running = False  # Guard flag to prevent double-click race condition
_stop_requested = False
_active_drivers = []

def safe_quit_driver(driver):
    try:
        driver.quit()
    except:
        pass
    try:
        if driver in _active_drivers:
            _active_drivers.remove(driver)
    except:
        pass


def download_and_install_update(url):
    def _download():
        try:
            update_window = tk.Toplevel(root)
            update_window.title("Downloading Update")
            update_window.geometry("300x150")
            tk.Label(update_window, text="Downloading update, please wait...", font=("Segoe UI", 10)).pack(pady=30)
            update_window.update()
            
            installer_path = os.path.join(os.environ["TEMP"], "SEO_Auto_Update.exe")
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
            
            with urllib.request.urlopen(url, context=ctx) as response, open(installer_path, 'wb') as out_file:
                shutil.copyfileobj(response, out_file)
            
            # Execute installer and exit current app
            subprocess.Popen([installer_path, "/SILENT"])
            os._exit(0)
        except Exception as e:
            messagebox.showerror("Update Error", f"Failed to download or install update: {e}")
            if 'update_window' in locals():
                update_window.destroy()
                
    threading.Thread(target=_download, daemon=True).start()

def check_for_updates():
    def _check():
        try:
            req = urllib.request.Request("https://api.github.com/repos/ubuyaiteam/SEO_Automation/releases/latest")
            # GitHub API requires a User-Agent header
            req.add_header("User-Agent", "SEO-Automation-App")
            
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
            
            response = urllib.request.urlopen(req, context=ctx)
            data = json.loads(response.read().decode())
            latest_version = data.get("tag_name", "")
            
            if latest_version and latest_version.lower() > CURRENT_VERSION.lower():
                # Found a new version!
                assets = data.get("assets", [])
                download_url = None
                for asset in assets:
                    if asset["name"].lower().endswith(".exe"):
                        download_url = asset["browser_download_url"]
                        break
                
                if download_url:
                    if messagebox.askyesno("Update Available", f"Version {latest_version} is available! Do you want to download and install it now?"):
                        download_and_install_update(download_url)
                else:
                    messagebox.showinfo("Update Available", f"Version {latest_version} is out, but no installer was found in the release assets.")
            else:
                messagebox.showinfo("Up to Date", f"You are running the latest version! ({CURRENT_VERSION})")
        except urllib.error.HTTPError as e:
            if e.code == 404:
                messagebox.showinfo("Up to Date", "No updates found yet, or the repository is private without an access token.")
            else:
                messagebox.showerror("Update Error", f"HTTP Error checking for updates: {e}")
        except Exception as e:
            messagebox.showerror("Update Error", f"Could not check for updates: {e}")
            
    threading.Thread(target=_check, daemon=True).start()


# ==================================================
# EXECUTION CONTROLLER
# ==================================================
def stop_bot():
    global _stop_requested, _active_drivers
    _stop_requested = True
    try:
        stop_btn.config(state="disabled")
    except:
        pass
    
    # Forcefully terminate all active browsers to instantly break wait loops
    for driver in list(_active_drivers):
        safe_quit_driver(driver)
    _active_drivers.clear()
    
    print("STOP requested. Active drivers forcefully terminated.")


def update_stats_safe(result):
    root.after(0, lambda: stat_indexed.config(text=result["indexed"]))
    root.after(0, lambda: stat_non_indexed.config(text=result["non_indexed"]))
    root.after(0, lambda: stat_today_total.config(text=result["today_total_requests"]))
    root.after(0, lambda: stat_today_avg.config(text=result["today_avg_response_ms"]))
    root.after(
        0, lambda: stat_seven_total.config(text=result["seven_days_total_requests"])
    )
    root.after(
        0, lambda: stat_seven_avg.config(text=result["seven_days_avg_response_ms"])
    )


def run_bot(
    get_selected_countries_func,
    progressbar,
    logbox,
    start_btn,
    stop_btn,
    sa_info,
    chk1,
    chk2,
    chk3,
    chk4,
    chk5,
    chk_d=None,
    chk_f=None,
    chk_others=None,
    chk_bing_d=None,
    chk_bing_f=None,
    chk_bing_others=None,
    chk_val_d=None,
    chk_val_f=None,
    chk_val_others=None,
):
    global _is_running, _stop_requested
    if _is_running:
        return
    _is_running = True
    _stop_requested = False

    selected_countries = get_selected_countries_func()
    if not selected_countries:
        messagebox.showwarning("Warning", "Please select at least one country.")
        _is_running = False
        return
        
    if len(selected_countries) > 15:
        messagebox.showerror("Limit Exceeded", "You can only select up to 15 countries per execution batch. Please reduce your selection.")
        _is_running = False
        return

    root.after(0, lambda: start_btn.config(state=tk.DISABLED))
    root.after(0, lambda: stop_btn.config(state=tk.NORMAL))
    root.after(0, lambda: logbox.config(state=tk.NORMAL))
    root.after(0, lambda: logbox.delete(1.0, tk.END))
    root.after(0, lambda: logbox.config(state=tk.DISABLED))
    def toggle_tasks_ui(state_val):
        def recurse(w):
            for child in w.winfo_children():
                if child.winfo_class() == 'TCheckbutton':
                    child.state(['disabled'] if state_val == 'disabled' else ['!disabled'])
                elif hasattr(child, 'winfo_children'):
                    recurse(child)
        recurse(card_tasks)

    root.after(0, lambda: toggle_tasks_ui("disabled"))

    def log(msg, level="info"):
        def write_log():
            import datetime
            timestamp = datetime.datetime.now().strftime("[%H:%M:%S] ")
            tag = {"info": "info", "success": "success", "error": "error", "warn": "warn", "header": "header"}.get(level, "info")
            logbox.config(state=tk.NORMAL)
            lines = int(logbox.index("end-1c").split(".")[0])
            if lines > 3000:
                logbox.delete("1.0", "500.0")
            logbox.insert(tk.END, timestamp + msg + "\n", tag)
            logbox.see(tk.END)
            logbox.config(state=tk.DISABLED)
        root.after(0, write_log)

    def task():
        global _is_running
        
        try:
            # Read flags
            active_flags = {
                "bing": chk1.get() if chk1 else False,
                "gse": chk2.get() if chk2 else False,
                "gsc_submit": chk3.get() if chk3 else False,
                "inner_validation": chk4.get() if chk4 else False,
                "delete_stale": chk5.get() if chk5 else False,
            }
            
            gsc_d_val = chk_d.get() if chk_d and hasattr(chk_d, "get") else False
            gsc_f_val = chk_f.get() if chk_f and hasattr(chk_f, "get") else False
            gsc_oth_val = chk_others.get() if chk_others and hasattr(chk_others, "get") else False
    
            bing_d_val = chk_bing_d.get() if chk_bing_d and hasattr(chk_bing_d, "get") else False
            bing_f_val = chk_bing_f.get() if chk_bing_f and hasattr(chk_bing_f, "get") else False
            bing_oth_val = chk_bing_others.get() if chk_bing_others and hasattr(chk_bing_others, "get") else False
    
            val_d_flag_val = chk_val_d.get() if chk_val_d and hasattr(chk_val_d, "get") else False
            val_f_flag_val = chk_val_f.get() if chk_val_f and hasattr(chk_val_f, "get") else False
            val_oth_flag_val = chk_val_others.get() if chk_val_others and hasattr(chk_val_others, "get") else False
    
            # Reset Stats UI
            root.after(0, lambda: stat_indexed.config(text="--"))
            root.after(0, lambda: stat_non_indexed.config(text="--"))
            root.after(0, lambda: stat_today_total.config(text="--"))
            root.after(0, lambda: stat_today_avg.config(text="--"))
            root.after(0, lambda: stat_seven_total.config(text="--"))
            root.after(0, lambda: stat_seven_avg.config(text="--"))
    
            active_count = sum(1 for v in active_flags.values() if v)
            if active_count == 0:
                pass 
            else:
                root.after(0, lambda: progressbar.config(value=0, maximum=100))
    
                per_task_weight = 100.0 / (active_count * len(selected_countries))
                task_weights = {k: per_task_weight if active else 0.0 for k, active in active_flags.items()}
    
                total_progress = [0.0]
                progress_lock = threading.Lock()
    
                def add_progress(task_name, percent):
                    with progress_lock:
                        weight = task_weights.get(task_name, 0.0)
                        increment = (percent / 100.0) * weight
                        total_progress[0] += increment
                        if total_progress[0] > 100:
                            total_progress[0] = 100.0
                        root.after(0, lambda val=total_progress[0]: progressbar.config(value=val))
    
                sa_data = load_local_sa()
    
                needs_parallel = active_flags["gsc_submit"] or active_flags["delete_stale"] or active_flags["gse"]
                needs_sequential = active_flags["bing"] or active_flags["inner_validation"] or active_flags["delete_stale"]
    
                def process_parallel(country):
                    if _stop_requested: return
                    log(f"\n{'='*50}\n🚀 [PARALLEL TASKS] {country.upper()}\n{'='*50}", "header")
                    
                    if country in COUNTRY_DOMAINS:
                        property_val = COUNTRY_DOMAINS[country]
                    else:
                        log(f"⚠️ Skipping {country}: No property domain configured.", "error")
                        return
                    
                    curr_sa_info = sa_data.get(country, None)
                    output_folder = mkdirr()
                    latestSitemap = extract_urls_from_sitemap(property_val, log)
                    
                    gsc_sitemaps = filter_sitemaps(latestSitemap, gsc_d_val, gsc_f_val, gsc_oth_val)
                    def gsc_log(msg, level="info"): log(f"[GOOGLE] {msg}", level)
                    
                    if active_flags["gsc_submit"] or active_flags["delete_stale"]:
                        add_progress("gsc_submit", 5)
                        add_progress("delete_stale", 5)
                        submit_all_sitemaps(
                            property_val, gsc_sitemaps, curr_sa_info, gsc_log,
                            delete_stale=active_flags["delete_stale"],
                            do_submit=active_flags["gsc_submit"],
                            full_sitemap_list=latestSitemap,
                        )
                        add_progress("gsc_submit", 95)
                        add_progress("delete_stale", 95)
                    
                    if active_flags["gse"]:
                        gsc_log("📊 Fetching GSE data...")
                        gsc_driver, _ = setup_driver_with_session(country, "google", headless=dev_headless_var.get(), output_folder=output_folder)
                        if gsc_driver:
                            try:
                                add_progress("gse", 5)
                                result = get_gsc_data(property_val, gsc_driver, output_folder)
                                root.after(0, update_stats_safe, result)
                                add_progress("gse", 95)
                            finally:
                                try: safe_quit_driver(gsc_driver)
                                except: pass
                    
                    import shutil
                    shutil.rmtree(output_folder, ignore_errors=True)
    
                def process_sequential(country):
                    if _stop_requested: return
                    log(f"\n{'='*50}\n🔄 [SEQUENTIAL TASKS] {country.upper()}\n{'='*50}", "header")
                    
                    if country in COUNTRY_DOMAINS:
                        property_val = COUNTRY_DOMAINS[country]
                    else:
                        return
                    
                    output_folder = mkdirr()
                    latestSitemap = extract_urls_from_sitemap(property_val, log)
                    
                    if active_flags["bing"] or active_flags["delete_stale"]:
                        def bing_log(msg, level="info"): log(f"[BING] {msg}", level)
                        bing_sitemaps = filter_sitemaps(latestSitemap, bing_d_val, bing_f_val, bing_oth_val)
                        if active_flags["bing"]:
                            add_progress("bing", 5)
                        run_bing_process(
                            property_val, country, bing_log, bing_sitemaps, output_folder,
                            do_submit=active_flags["bing"], delete_stale=active_flags["delete_stale"], full_sitemap_list=latestSitemap
                        )
                        if active_flags["bing"]:
                            add_progress("bing", 95)
    
                    if active_flags["inner_validation"]:
                        def gsc_log(msg, level="info"): log(f"[GOOGLE] {msg}", level)
                        gsc_log("🌍 Setting up browser for GSC Validation...")
                        gsc_driver, _ = setup_driver_with_session(country, "google", headless=dev_headless_var.get(), output_folder=output_folder)
                        if gsc_driver:
                            try:
                                add_progress("inner_validation", 5)
                                sitemaps_for_validation = extract_sitemaps(gsc_driver, property_val, gsc_log)
                                
                                # Fallback: if GSC page has no submitted sitemaps, use the sitemap.xml URLs
                                if not sitemaps_for_validation:
                                    if latestSitemap:
                                        gsc_log(f"⚠️ No sitemaps found on GSC page. Falling back to {len(latestSitemap)} URLs from sitemap.xml.", "warning")
                                        sitemaps_for_validation = latestSitemap
                                    else:
                                        gsc_log("❌ No sitemaps available from GSC or sitemap.xml. Skipping sitemap validation.", "error")
                                
                                filtered_validation = filter_sitemaps(sitemaps_for_validation, val_d_flag_val, val_f_flag_val, val_oth_flag_val)
                                start_validation(gsc_driver, property_val, filtered_validation, gsc_log)
                                add_progress("inner_validation", 95)
                            finally:
                                try: safe_quit_driver(gsc_driver)
                                except: pass
                    
                    import shutil
                    shutil.rmtree(output_folder, ignore_errors=True)
    
                import time
                start_time = time.time()
    
                if needs_parallel:
                    import concurrent.futures
                    log("🚀 PHASE 1: Running fast API/Fetch tasks in PARALLEL mode...", "success")
                    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as batch_executor:
                        batch_futures = []
                        for country in selected_countries:
                            if _stop_requested: break
                            batch_futures.append(batch_executor.submit(process_parallel, country))
                        for future in concurrent.futures.as_completed(batch_futures):
                            if _stop_requested: break
                            try:
                                future.result()
                            except Exception as e:
                                if not _stop_requested:
                                    log(f"⚠️ Error in Parallel Phase: {e}", "error")
    
                if needs_sequential:
                    log("🔄 PHASE 2: Running heavy browser tasks in SEQUENTIAL mode...", "info")
                    for country in selected_countries:
                        if _stop_requested: break
                        try:
                            process_sequential(country)
                        except Exception as e:
                            if not _stop_requested:
                                log(f"⚠️ Error in Sequential Phase for {country}: {e}", "error")
                
                end_time = time.time()
                total = end_time - start_time
                minutes = int(total // 60)
                seconds = int(total % 60)
                log(f"\n🎉 BATCH COMPLETE. Total Duration: {minutes} min {seconds} sec", "success")
    
        except Exception as _e:
            if not _stop_requested:
                print(f"Unhandled Thread Exception: {_e}")
        finally:
        # Finally block after loop finishes or aborts
            root.after(0, lambda: start_btn.config(state=tk.NORMAL))
            root.after(0, lambda: stop_btn.config(state=tk.DISABLED))
            root.after(0, lambda: toggle_tasks_ui("normal"))

            global _is_running
            _is_running = False
            if not _stop_requested:
                root.after(0, lambda: show_notification("SEO Automation Finished", "The scheduled task has completed."))

    t = threading.Thread(target=task, daemon=True)
    t.start()


import tkinter as tk
import webbrowser
from tkinter import scrolledtext, ttk

# ----------------------------
# ROOT WINDOW SETUP
# ----------------------------


# ==================================================
# MAIN GUI INITIALIZATION
# ==================================================
root = tk.Tk()
dev_headless_var = tk.BooleanVar(value=True)  # Default to Headless mode

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

root.title(f"UBUY SEO Automation Tool {CURRENT_VERSION.upper()}(beta)")

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
BG_COLOR = "#F9B11E"  # Ubuy Yellow
CARD_BG = "#FFFFFF"  # White Cards
ACCENT_COLOR = "#000000"  # Black Accents
TEXT_COLOR = "#333333"  # Dark Text
SUBTEXT_COLOR = "#666666"
CHECKMARK_COLOR = "#4CAF50"  # Ubuy/Success Green

root.configure(bg=BG_COLOR)

style = ttk.Style(root)
style.theme_use("alt")


style.configure("TFrame", background=BG_COLOR)
style.configure("Card.TFrame", background=CARD_BG, relief="flat")


style.configure(
    "Title.TLabel",
    background=BG_COLOR,
    foreground=ACCENT_COLOR,
    font=("Segoe UI", 24, "bold"),
)

style.configure(
    "TLabel", background=CARD_BG, foreground=TEXT_COLOR, font=("Segoe UI", 11)
)

style.configure(
    "Sub.TLabel", background=CARD_BG, foreground=SUBTEXT_COLOR, font=("Segoe UI", 9)
)

style.configure("TLabelFrame", background=CARD_BG, borderwidth=2, relief="groove")

style.configure(
    "TLabelFrame.Label",
    background=CARD_BG,
    foreground=ACCENT_COLOR,
    font=("Segoe UI", 12, "bold"),
)


style.configure(
    "TCheckbutton", background=CARD_BG, foreground=TEXT_COLOR, font=("Segoe UI", 11)
)
style.map(
    "TCheckbutton",
    background=[("active", CARD_BG)],
    foreground=[("active", ACCENT_COLOR)],
    indicatorcolor=[("selected", CHECKMARK_COLOR)],
)

# Radiobutton Style
style.configure(
    "TRadiobutton", background=CARD_BG, foreground=TEXT_COLOR, font=("Segoe UI", 11)
)
style.map(
    "TRadiobutton",
    background=[("active", CARD_BG)],
    foreground=[("active", ACCENT_COLOR)],
    indicatorcolor=[("selected", CHECKMARK_COLOR)],
)


# Custom Button Class for Hover & Pulse Effects


# ==================================================
# GUI COMPONENTS & ANIMATIONS
# ==================================================
class PulsingButton(tk.Button):
    def __init__(self, master, text, command=None, **kwargs):
        super().__init__(master, text=text, command=command, **kwargs)
        self.default_bg = kwargs.get("bg", "#2E2E2E")
        self.hover_bg = "#444444"
        self.pulse_bg = "#3A3A3A"  # Mid-tone for pulse
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
            pady=10,
        )

        self.is_hovering = False
        self.bind("<Enter>", self.on_enter)
        self.bind("<Leave>", self.on_leave)

        self.pulse_step = 0
        self.after(100, self.pulse)

    def on_enter(self, e):
        if self["state"] == tk.DISABLED:
            return
        self.is_hovering = True
        self.configure(bg=self.hover_bg)

    def on_leave(self, e):
        if self["state"] == tk.DISABLED:
            return
        self.is_hovering = False
        self.configure(bg=self.default_bg)

    def pulse(self):
        if self["state"] == tk.DISABLED:
            self.configure(bg="#555555")
        elif not self.is_hovering:
            # Simple 2-step pulse for performance
            if self.pulse_step == 0:
                self.configure(bg=self.pulse_bg)
                self.pulse_step = 1
            else:
                self.configure(bg=self.default_bg)
                self.pulse_step = 0

        # Pulse every 1.2 seconds roughly
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
        frame_duration = gif_image.info.get("duration", 100)
        gif_duration = (
            frame_duration
            if isinstance(frame_duration, int) and frame_duration > 0
            else 100
        )

        frame_index = 0
        while True:
            try:
                gif_image.seek(frame_index)
                frame_image = gif_image.copy()

                # Make simple transparency mask if needed, but for now just resize
                ow, oh = frame_image.size
                if oh == 0:
                    break
                new_w = max(1, int(target_height * (ow / oh)))
                resized = frame_image.resize(
                    (new_w, target_height), Image.Resampling.LANCZOS
                )

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
    if not gif_frames:
        return
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
lbl_main_title = tk.Label(
    title_group, text="", font=("Segoe UI", 26, "bold"), bg=BG_COLOR, fg=ACCENT_COLOR
)
lbl_main_title.pack(anchor="w")



tk.Label(
    title_group,
    text=f"Version {CURRENT_VERSION} | Advanced Tool Suite",
    font=("Segoe UI", 10),
    bg=BG_COLOR,
    fg=SUBTEXT_COLOR,
).pack(anchor="w")

# Report Link
btn_report = tk.Button(
    header_frame,
    text="View Report ↗",
    command=open_link,
    bg="#333",
    fg="white",
    font=("Segoe UI", 10),
    relief="flat",
    padx=10,
    pady=5,
)
btn_report.pack(side="right", padx=15)

# Check for Updates Button
btn_update = tk.Button(
    header_frame,
    text="Check for Updates 🔄",
    command=check_for_updates,
    bg="#29b6f6",
    fg="white",
    font=("Segoe UI", 10, "bold"),
    relief="flat",
    padx=10,
    pady=5,
    cursor="hand2",
)
btn_update.pack(side="right", padx=10)


# --- MAIN CONTENT GRID ---
grid_frame = ttk.Frame(container)
grid_frame.pack(fill="both", expand=True)
grid_frame.columnconfigure(0, weight=4)  # Left column slightly larger
grid_frame.columnconfigure(1, weight=3)  # Right column
grid_frame.rowconfigure(0, weight=1)  # Ensure row expands vertically

# === LEFT COLUMN: CONTROLS ===
left_col = ttk.Frame(grid_frame)
left_col.grid(row=0, column=0, sticky="nsew", padx=(0, 20))

# 1. Credentials Card
card_creds = ttk.LabelFrame(left_col, text=" 🔑 Authentication ", padding=10)
card_creds.pack(fill="x", pady=(0, 5))



ttk.Label(card_creds, text="Select Country:").pack(anchor="w", pady=(0, 2))

countries_list = sorted(
    [
        "Italy",
        "Georgia",
        "Colombia",
        "Algeria",
        "Albania",
        "Réunion",
        "Greenland",
        "French Polynesia",
        "Bhutan",
        "Aruba",
        "Wallis and Futuna Islands",
        "Antigua and Barbuda",
        "Oman",
        "Denmark",
        "Norway",
        "Sri Lanka",
        "Poland",
        "Armenia",
        "Zambia",
        "Kyrgyzstan",
        "Libya",
        "Montserrat",
        "Saint Kitts and Nevis",
        "Sierra Leone",
        "Kenya",
        "Bulgaria",
        "Serbia",
        "Rwanda",
        "Cote d'Ivoire",
        "Togo",
        "Honduras",
        "Jamaica",
        "Micronesia",
        "Mongolia",
        "Turkmenistan",
        "Benin",
        "Japan",
        "Finland",
        "Switzerland",
        "Philippines",
        "Thailand",
        "Myanmar",
        "Bolivia",
        "Comoros",
        "Falkland Islands",
        "Grenada",
        "Guinea",
        "Curacao",
        "Croatia",
        "Pakistan",
        "Saudi",
        "UK",
        "Netherlands",
        "Nepal",
        "Azerbaijan",
        "Guinea-Bissau",
        "Tonga",
        "Western Samoa",
        "Anguilla",
        "Mali",
        "Angola",
        "Austria",
        "Latvia",
        "Moldova",
        "Paraguay",
        "New Caledonia",
        "Martinique",
        "Nicaragua",
        "Vanuatu",
        "Central African Republic",
        "Cook Islands",
        "Gabon",
        "Czech Republic",
        "Uganda",
        "Iceland",
        "Mexico",
        "Kuwait",
        "Mozambique",
        "Ethiopia",
        "Guyana",
        "Haiti",
        "Isle of Man",
        "Kiribati",
        "Laos",
        "Romania",
        "Taiwan",
        "Kazakhstan",
        "New Zealand",
        "Ireland",
        "Cambodia",
        "Madagascar",
        "Dominican Republic",
        "Equatorial Guinea",
        "Guernsey",
        "Djibouti",
        "Liechtenstein",
        "Sweden",
        "Lebanon",
        "Ghana",
        "Morocco",
        "Chile",
        "Cameroon",
        "Senegal",
        "Faroe Islands",
        "Burkina Faso",
        "Burundi",
        "Cape Verde",
        "Cayman Islands",
        "Belgium",
        "Jordan",
        "Spain",
        "Hong Kong",
        "Mauritius",
        "Slovakia",
        "Saint Lucia",
        "Saint Pierre and Miquelon",
        "Saint Vincent",
        "San Marino",
        "Sint Maarten",
        "Uzbekistan",
        "Seychelles",
        "Ecuador",
        "Vietnam",
        "Bahrain",
        "Argentina",
        "Costa Rica",
        "Luxembourg",
        "Mauritania",
        "Barbados",
        "Bermuda",
        "Dominica",
        "Malaysia",
        "Hungary",
        "Portugal",
        "Qatar",
        "France",
        "Trinidad and Tobago",
        "Tajikistan",
        "Puerto Rico",
        "Belize",
        "Jersey",
        "Aland Islands",
        "India",
        "Australia",
        "UAE",
        "South Africa",
        "Canada",
        "North Macedonia",
        "Malta",
        "Palau",
        "Palestine",
        "Chad",
        "Solomon Islands",
        "Nigeria",
        "Indonesia",
        "Egypt",
        "Brazil",
        "Germany",
        "Kosovo",
        "Lithuania",
        "Suriname",
        "Nauru",
        "Republic of the Congo",
        "Saint Helena",
        "Tuvalu",
        "Estonia",
        "Singapore",
        "Iraq",
        "Bosnia and Herzegovina",
        "Cyprus",
        "Fiji",
        "Monaco",
        "French Guiana",
        "Brunei",
        "Montenegro",
        "Timor-Leste",
        "Malawi",
        "Peru",
        "Turkey",
        "Slovenia",
        "Botswana",
        "Namibia",
        "Zimbabwe",
        "Guadeloupe",
        "Niger",
        "Lesotho",
        "The Bahamas",
        "The Gambia",
        "Turks and Caicos",
        "Greece",
        "South Korea",
        "Bangladesh",
        "El Salvador",
        "Uruguay",
        "Tanzania",
        "Panama",
        "Reunion",
        "Guatemala",
        "Tunisia",
        "Maldives",
        "Macao",
    ]
)

country_frame = tk.Frame(card_creds, bg=CARD_BG)
country_frame.pack(fill="x", pady=(0, 5))



# Multiple Country Listbox
tk.Label(country_frame, text="Countries:", bg=CARD_BG, fg=TEXT_COLOR).pack(side="left")
country_listbox_frame = tk.Frame(country_frame, bg=CARD_BG)
country_listbox_frame.pack(side="left", padx=(5, 10))

search_var = tk.StringVar()
search_entry = ttk.Entry(country_listbox_frame, textvariable=search_var, width=25)
search_entry.pack(side="top", fill="x", pady=(0, 2))

def filter_countries(*args):
    search_term = search_var.get().lower()
    country_listbox.delete(0, tk.END)
    for c in countries_list:
        if search_term in c.lower():
            country_listbox.insert(tk.END, c)
            
search_var.trace_add("write", filter_countries)

country_scrollbar = tk.Scrollbar(country_listbox_frame, orient="vertical")
country_listbox = tk.Listbox(
    country_listbox_frame,
    selectmode=tk.BROWSE,
    yscrollcommand=country_scrollbar.set,
    height=8,
    font=("Segoe UI", 10),
    exportselection=False,
)
country_scrollbar.config(command=country_listbox.yview)
country_listbox.pack(side="left", fill="both", expand=True)
country_scrollbar.pack(side="right", fill="y")

for c in countries_list:
    country_listbox.insert(tk.END, c)


def get_selected_countries():
    return [country_listbox.get(i) for i in country_listbox.curselection()]

sa_status_label = tk.Label(
    country_frame,
    text="Wait...",
    fg="#b3b3b3",
    bg=CARD_BG,
    font=("Segoe UI", 9, "italic"),
    justify="left",
    wraplength=350,
)
sa_status_label.pack(side="left", padx=(10, 0))

current_sa_info_holder = {"sa_info": None}


# ==================================================
# SERVICE ACCOUNT MANAGEMENT MENU
# ==================================================
def check_country_sa_main(event=None):
    selected = get_selected_countries()
    if not selected:
        sa_status_label.config(text="Waiting", fg="#b3b3b3")
        current_sa_info_holder["sa_info"] = None
        update_session_status()
        return
    if len(selected) > 1:
        sel_str = ", ".join(selected)
        if len(sel_str) > 200:
            sel_str = sel_str[:197] + "..."
        sa_status_label.config(
            text=f"Batch Mode: {len(selected)} selected\n{sel_str}", fg="#b3b3b3"
        )
        update_session_status()
        return
    c = selected[0]
    try:

        data = load_local_sa()
        if c in data:
            sa_status_label.config(text="Linked (Ready)", fg="#4CAF50")
            current_sa_info_holder["sa_info"] = data[c]
        else:
            sa_status_label.config(text="Not Uploaded", fg="#E50914")
            current_sa_info_holder["sa_info"] = None
        update_session_status()
    except Exception as e:
        sa_status_label.config(text="Local Error", fg="#E50914")


country_listbox.bind("<<ListboxSelect>>", check_country_sa_main)


def upload_sa_main():
    selected = get_selected_countries()
    if len(selected) != 1:
        messagebox.showinfo(
            "Info", "Please select exactly one country for this operation."
        )
        return
    c = selected[0]
    file_path = filedialog.askopenfilename(
        title=f"Select JSON Key for {c}", filetypes=[("JSON Files", "*.json")]
    )
    if file_path:
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                sa_data = json.load(f)

            data = load_local_sa()
            data[c] = sa_data
            if save_local_sa(data):
                messagebox.showinfo(
                    "Success", f"Service Account saved locally for {c}!"
                )
                check_country_sa_main()
            else:
                messagebox.showerror("Error", "Failed to save to local file.")
        except Exception as e:
            messagebox.showerror("Error", f"Failed to upload JSON: {e}")


# Hidden Context Menu for Upload
sa_menu = tk.Menu(root, tearoff=0)
sa_menu.add_command(
    label="Upload Service Account JSON", command=lambda: upload_sa_main()
)


def show_sa_menu(event):
    sa_menu.post(event.x_root, event.y_root)


country_listbox.bind("<Button-3>", show_sa_menu)


# --- Country to Domain Mapping ---
COUNTRY_DOMAINS = {
    "Italy": "ubuy.co.it",
    "Georgia": "ubuy.ge",
    "Colombia": "ubuy.com.co",
    "Algeria": "ubuy.dz",
    "Albania": "ubuy.al",
    "Réunion": "ubuy.re",
    "Greenland": "ubuy.gl",
    "French Polynesia": "ubuy.pf",
    "Bhutan": "ubuy.bt",
    "Aruba": "ubuy.aw",
    "Wallis and Futuna Islands": "ubuy.wf",
    "Antigua and Barbuda": "ubuy.com.ag",
    "Oman": "ubuy.com.om",
    "Denmark": "ubuy.dk",
    "Norway": "ubuy.co.no",
    "Sri Lanka": "ubuy.com.lk",
    "Poland": "ubuy.com.pl",
    "Armenia": "ubuy.co.am",
    "Zambia": "ubuy.com.zm",
    "Kyrgyzstan": "ubuy.kg",
    "Libya": "ubuy.com.ly",
    "Montserrat": "ubuy.ms",
    "Saint Kitts and Nevis": "ubuy.kn",
    "Sierra Leone": "ubuy.sl",
    "Kenya": "ubuy.ke",
    "Bulgaria": "ubuy.bg",
    "Serbia": "ubuy.rs",
    "Rwanda": "ubuy.rw",
    "Cote d'Ivoire": "ubuy.ci",
    "Togo": "ubuy.tg",
    "Honduras": "ubuy.hn",
    "Jamaica": "ubuy.com.jm",
    "Micronesia": "ubuy.fm",
    "Mongolia": "ubuy.mn",
    "Turkmenistan": "ubuy.tm",
    "Benin": "ubuy.bj",
    "Japan": "u-buy.jp",
    "Finland": "ubuy.fi",
    "Switzerland": "u-buy.ch",
    "Philippines": "ubuy.com.ph",
    "Thailand": "ubuy.co.th",
    "Myanmar": "ubuy.com.mm",
    "Bolivia": "ubuy.com.bo",
    "Comoros": "comoros.ubuy.com",
    "Falkland Islands": "falkand.ubuy.com",
    "Grenada": "ubuy.gd",
    "Guinea": "guinea.ubuy.com",
    "Curacao": "ubuy.com.cw",
    "Croatia": "ubuy.hr",
    "Pakistan": "ubuy.com.pk",
    "Saudi": "ubuy.com.sa",
    "UK": "u-buy.co.uk",
    "Netherlands": "ubuy.co.nl",
    "Nepal": "nepal.ubuy.com",
    "Azerbaijan": "ubuy.az",
    "Guinea-Bissau": "ubuy.gw",
    "Tonga": "ubuy.to",
    "Western Samoa": "ubuy.ws",
    "Anguilla": "ubuy.ai",
    "Mali": "ubuy.ml",
    "Angola": "ubuy.co.ao",
    "Austria": "ubuy.co.at",
    "Latvia": "ubuy.lv",
    "Moldova": "ubuy.md",
    "Paraguay": "ubuy.com.py",
    "New Caledonia": "caledonia.ubuy.com",
    "Martinique": "ubuy.mq",
    "Nicaragua": "ubuy.com.ni",
    "Vanuatu": "ubuy.vu",
    "Central African Republic": "ubuy.cf",
    "Cook Islands": "ubuy.co.ck",
    "Gabon": "ubuy.ga",
    "Czech Republic": "ubuy.cz",
    "Uganda": "ubuy.ug",
    "Iceland": "ubuy.is",
    "Mexico": "ubuy.com.mx",
    "Kuwait": "a.ubuy.com.kw",
    "Mozambique": "ubuy.co.mz",
    "Ethiopia": "ubuy.et",
    "Guyana": "ubuy.gy",
    "Haiti": "ubuy.ht",
    "Isle of Man": "ubuy.im",
    "Kiribati": "ubuy.com.ki",
    "Laos": "ubuy.la",
    "Romania": "ubuy.com.ro",
    "Taiwan": "u-buy.com.tw",
    "Kazakhstan": "ubuy.com.kz",
    "New Zealand": "u-buy.co.nz",
    "Ireland": "ubuy.ie",
    "Cambodia": "ubuy.com.kh",
    "Madagascar": "ubuy.mg",
    "Dominican Republic": "ubuy.do",
    "Equatorial Guinea": "ubuy.gq",
    "Guernsey": "ubuy.gg",
    "Djibouti": "ubuy.dj",
    "Liechtenstein": "ubuy.li",
    "Sweden": "ubuy.com.se",
    "Lebanon": "ubuy.com.lb",
    "Ghana": "ubuy.com.gh",
    "Morocco": "ubuy.ma",
    "Chile": "ubuy.cl",
    "Cameroon": "ubuy.cm",
    "Senegal": "ubuy.sn",
    "Faroe Islands": "ubuy.fo",
    "Burkina Faso": "ubuy.bf",
    "Burundi": "ubuy.bi",
    "Cape Verde": "ubuy.com.cv",
    "Cayman Islands": "u-buy.ky",
    "Belgium": "u-buy.be",
    "Jordan": "ubuy.com.jo",
    "Spain": "ubuy.com.es",
    "Hong Kong": "ubuy.hk",
    "Mauritius": "ubuy.mu",
    "Slovakia": "ubuy.sk",
    "Saint Lucia": "ubuy.lc",
    "Saint Pierre and Miquelon": "ubuy.pm",
    "Saint Vincent": "ubuy.com.vc",
    "San Marino": "ubuy.sm",
    "Sint Maarten": "ubuy.sx",
    "Bahrain": "ubuy.com.bh",
    "Uzbekistan": "ubuy.uz",
    "Seychelles": "ubuy.sc",
    "Ecuador": "ubuy.ec",
    "Vietnam": "ubuy.vn",
    "Argentina": "ubuy.com.ar",
    "Costa Rica": "ubuy.cr",
    "Luxembourg": "ubuy.lu",
    "Mauritania": "ubuy.mr",
    "Barbados": "barbabos.ubuy.com",
    "Bermuda": "bermuda.ubuy.com",
    "Dominica": "dominica.ubuy.com",
    "Malaysia": "ubuy.com.my",
    "Hungary": "ubuy.hu",
    "Portugal": "ubuy.com.pt",
    "Qatar": "ubuy.qa",
    "France": "ubuy.fr",
    "Trinidad and Tobago": "ubuy.tt",
    "Tajikistan": "ubuy.tj",
    "Puerto Rico": "ubuy.com.pr",
    "Belize": "ubuy.com.bz",
    "Jersey": "ubuy.je",
    "Aland Islands": "ubuy.ax",
    "India": "ubuy.co.in",
    "Australia": "u-buy.com.au",
    "UAE": "ubuy.ae",
    "South Africa": "ubuy.co.za",
    "Canada": "ubuy.ca",
    "North Macedonia": "ubuy.mk",
    "Malta": "ubuy.mt",
    "Palau": "ubuy.pw",
    "Palestine": "ubuy.com.ps",
    "Chad": "ubuy.td",
    "Solomon Islands": "ubuy.com.sb",
    "Nigeria": "u-buy.com.ng",
    "Indonesia": "ubuy.co.id",
    "Egypt": "ubuy.com.eg",
    "Brazil": "ubuy.com.br",
    "Germany": "ubuy.de.com",
    "Kosovo": "kosovo.ubuy.com",
    "Lithuania": "ubuy.lt",
    "Suriname": "ubuy.sr",
    "Nauru": "ubuy.com.nr",
    "Republic of the Congo": "ubuy.cg",
    "Saint Helena": "ubuy.sh",
    "Tuvalu": "u-buy.tv",
    "Estonia": "ubuy.ee",
    "Singapore": "ubuy.com.sg",
    "Iraq": "ubuy.iq",
    "Bosnia and Herzegovina": "ubuy.ba",
    "Cyprus": "ubuy.cy",
    "Fiji": "ubuy.com.fj",
    "Monaco": "ubuy.mc",
    "French Guiana": "ubuy.gf",
    "Brunei": "ubuy.com.bn",
    "Montenegro": "ubuys.me",
    "Timor-Leste": "ubuy.tl",
    "Malawi": "ubuy.mw",
    "Peru": "ubuy.pe",
    "Turkey": "ubuy.tr",
    "Slovenia": "ubuy.si",
    "Botswana": "ubuy.co.bw",
    "Namibia": "ubuy.co.na",
    "Zimbabwe": "ubuy.co.zw",
    "Guadeloupe": "ubuy.gp",
    "Niger": "ubuy.ne",
    "Lesotho": "ubuy.ls",
    "The Bahamas": "ubuy.bs",
    "The Gambia": "ubuy.gm",
    "Turks and Caicos": "ubuy.tc",
    "Greece": "ubuy.com.gr",
    "South Korea": "ubuy.kr",
    "Bangladesh": "ubuy.com.bd",
    "El Salvador": "ubuy.sv",
    "Uruguay": "ubuy.uy",
    "Tanzania": "ubuy.co.tz",
    "Panama": "ubuy.com.pa",
    "Reunion": "ubuy.re",
    "Guatemala": "ubuy.gt",
    "Tunisia": "ubuy.tn",
    "Maldives": "ubuy.mv",
    "Macao": "macao.ubuy.com",
}







# Status Frame for DB status
status_frame = tk.Frame(card_creds, bg=CARD_BG)
status_frame.pack(fill="x", pady=(5, 0))

db_label_text = "DB Online" if DB_STATUS else "DB Offline"
db_label_fg = "#4CAF50" if DB_STATUS else "#E50914"
tk.Label(
    status_frame,
    text=f"• {db_label_text}",
    font=("Segoe UI", 9, "bold"),
    bg=CARD_BG,
    fg=db_label_fg,
).pack(side="right")


# 2. Tasks Card
card_tasks = ttk.LabelFrame(left_col, text=" ⚡ Automated Tasks ", padding=10)
card_tasks.pack(fill="both", expand=True, pady=(0, 5))

tasks_left_outer = tk.Frame(card_tasks, bg=CARD_BG)
tasks_left_outer.pack(side="left", fill="both", expand=True)

tasks_left_canvas = tk.Canvas(tasks_left_outer, bg=CARD_BG, highlightthickness=0)
tasks_left_scrollbar = ttk.Scrollbar(tasks_left_outer, orient="vertical", command=tasks_left_canvas.yview)
tasks_left_canvas.configure(yscrollcommand=tasks_left_scrollbar.set)

tasks_left_scrollbar.pack(side="right", fill="y")
tasks_left_canvas.pack(side="left", fill="both", expand=True)

tasks_left_frame = tk.Frame(tasks_left_canvas, bg=CARD_BG)
tasks_left_canvas_window = tasks_left_canvas.create_window((0, 0), window=tasks_left_frame, anchor="nw")

def on_left_frame_configure(event):
    tasks_left_canvas.configure(scrollregion=tasks_left_canvas.bbox("all"))
tasks_left_frame.bind("<Configure>", on_left_frame_configure)

def on_left_canvas_configure(event):
    tasks_left_canvas.itemconfig(tasks_left_canvas_window, width=event.width)
tasks_left_canvas.bind("<Configure>", on_left_canvas_configure)

tasks_right_frame = tk.Frame(card_tasks, bg=CARD_BG)
tasks_right_frame.pack(side="right", fill="both", expand=True, padx=(10, 0))


chk_bing_resubmit = tk.BooleanVar()
chk_gse_fetch = tk.BooleanVar()
chk_gsc_resubmit = tk.BooleanVar()
chk_gsc_inner_validation = tk.BooleanVar()
chk_delete_stale = tk.BooleanVar()

# Sub-options for GSC sitemap filtering
chk_gsc_d = tk.BooleanVar()
chk_gsc_f = tk.BooleanVar()
chk_gsc_others = tk.BooleanVar()

# Sub-options for Bing sitemap filtering
chk_bing_d = tk.BooleanVar()
chk_bing_f = tk.BooleanVar()
chk_bing_others = tk.BooleanVar()

# Sub-options for Validation sitemap filtering
chk_val_d = tk.BooleanVar()
chk_val_f = tk.BooleanVar()
chk_val_others = tk.BooleanVar()

ttk.Checkbutton(
    tasks_left_frame, text="Auto-Submit to Bing Webmaster", variable=chk_bing_resubmit
).pack(anchor="w", pady=3)

# Collapsible sub-frame for D/F/Others (indented under "Auto-Submit to Bing Webmaster")
bing_sub_frame = tk.Frame(tasks_left_frame, bg=CARD_BG)
bing_sub_indent = tk.Frame(bing_sub_frame, width=25, bg=CARD_BG)
bing_sub_indent.pack(side="left", fill="y")
bing_sub_options_col = tk.Frame(bing_sub_frame, bg=CARD_BG)
bing_sub_options_col.pack(side="left", fill="x", expand=True)
ttk.Checkbutton(
    bing_sub_options_col, text="Submit D sitemaps to Bing", variable=chk_bing_d
).pack(anchor="w", pady=1)
ttk.Checkbutton(
    bing_sub_options_col, text="Submit F sitemaps to Bing", variable=chk_bing_f
).pack(anchor="w", pady=1)
ttk.Checkbutton(
    bing_sub_options_col, text="Submit Other sitemaps to Bing", variable=chk_bing_others
).pack(anchor="w", pady=1)


def toggle_bing_sub_options(*args):
    if chk_bing_resubmit.get():
        bing_sub_frame.pack(
            anchor="w", pady=(0, 3), after=tasks_left_frame.winfo_children()[0]
        )
    else:
        bing_sub_frame.pack_forget()
        chk_bing_d.set(False)
        chk_bing_f.set(False)
        chk_bing_others.set(False)


chk_bing_resubmit.trace_add("write", toggle_bing_sub_options)

ttk.Checkbutton(
    tasks_left_frame, text="Fetch Indexing Data (GSE)", variable=chk_gse_fetch
).pack(anchor="w", pady=3)
ttk.Checkbutton(
    tasks_left_frame, text="Submit Sitemaps to GSC", variable=chk_gsc_resubmit
).pack(anchor="w", pady=3)

# Collapsible sub-frame for D/F/Others (indented under "Submit Sitemaps to GSC")
gsc_sub_frame = tk.Frame(tasks_left_frame, bg=CARD_BG)
sub_indent = tk.Frame(gsc_sub_frame, width=25, bg=CARD_BG)
sub_indent.pack(side="left", fill="y")
sub_options_col = tk.Frame(gsc_sub_frame, bg=CARD_BG)
sub_options_col.pack(side="left", fill="x", expand=True)
ttk.Checkbutton(
    sub_options_col, text="Submit D sitemaps to GSC", variable=chk_gsc_d
).pack(anchor="w", pady=1)
ttk.Checkbutton(
    sub_options_col, text="Submit F sitemaps to GSC", variable=chk_gsc_f
).pack(anchor="w", pady=1)
ttk.Checkbutton(
    sub_options_col, text="Submit Other sitemaps to GSC", variable=chk_gsc_others
).pack(anchor="w", pady=1)


def toggle_gsc_sub_options(*args):
    if chk_gsc_resubmit.get():
        gsc_sub_frame.pack(
            anchor="w", pady=(0, 3), after=tasks_left_frame.winfo_children()[3]
        )
    else:
        gsc_sub_frame.pack_forget()
        chk_gsc_d.set(False)
        chk_gsc_f.set(False)
        chk_gsc_others.set(False)


chk_gsc_resubmit.trace_add("write", toggle_gsc_sub_options)

ttk.Checkbutton(
    tasks_left_frame,
    text="Validate GSC Coverage Errors",
    variable=chk_gsc_inner_validation,
).pack(anchor="w", pady=3)

# Collapsible sub-frame for D/F/Others (indented under "Validate GSC Coverage Errors")
val_sub_frame = tk.Frame(tasks_left_frame, bg=CARD_BG)
val_sub_indent = tk.Frame(val_sub_frame, width=25, bg=CARD_BG)
val_sub_indent.pack(side="left", fill="y")
val_sub_options_col = tk.Frame(val_sub_frame, bg=CARD_BG)
val_sub_options_col.pack(side="left", fill="x", expand=True)
ttk.Checkbutton(
    val_sub_options_col, text="Validate D sitemaps", variable=chk_val_d
).pack(anchor="w", pady=1)
ttk.Checkbutton(
    val_sub_options_col, text="Validate F sitemaps", variable=chk_val_f
).pack(anchor="w", pady=1)
ttk.Checkbutton(
    val_sub_options_col, text="Validate Other sitemaps", variable=chk_val_others
).pack(anchor="w", pady=1)


def toggle_val_sub_options(*args):
    if chk_gsc_inner_validation.get():
        val_sub_frame.pack(
            anchor="w", pady=(0, 3), after=tasks_left_frame.winfo_children()[5]
        )
    else:
        val_sub_frame.pack_forget()
        chk_val_d.set(False)
        chk_val_f.set(False)
        chk_val_others.set(False)


chk_gsc_inner_validation.trace_add("write", toggle_val_sub_options)

ttk.Checkbutton(
    tasks_left_frame, text="Delete old/stale sitemaps", variable=chk_delete_stale
).pack(anchor="w", pady=3)


# 3. Sessions Card
card_sessions = ttk.LabelFrame(
    tasks_right_frame, text=" 🔐 Browser Sessions ", padding=10
)
card_sessions.pack(fill="both", expand=True, pady=0)

# Google Session Row
google_session_frame = tk.Frame(card_sessions, bg=CARD_BG)
google_session_frame.pack(fill="x", pady=(2, 5))

tk.Label(
    google_session_frame,
    text="Google:",
    bg=CARD_BG,
    fg=TEXT_COLOR,
    font=("Segoe UI", 10, "bold"),
).pack(side="left")
google_session_status = tk.Label(
    google_session_frame,
    text="Select country",
    fg="#b3b3b3",
    bg=CARD_BG,
    font=("Segoe UI", 9, "italic"),
    anchor="w",
)


def clear_google_session():
    selected = get_selected_countries()
    if len(selected) != 1:
        messagebox.showinfo(
            "Info", "Please select exactly one country for this operation."
        )
        return
    c = selected[0]
    if clear_session(c, "google"):
        update_session_status()
        messagebox.showinfo("Cleared", f"Google session for {c} has been cleared.")
    else:
        messagebox.showerror(
            "Error",
            "Could not clear the session. A background Chrome process is locking the files.\n\nPlease close any hanging Chrome processes or run 'taskkill /F /IM chrome.exe' in the terminal.",
        )


def login_google_session():
    selected = get_selected_countries()
    if len(selected) != 1:
        messagebox.showinfo(
            "Info", "Please select exactly one country for this operation."
        )
        return
    c = selected[0]
    open_login_browser(c, "google")
    update_session_status()


btn_clear_google = tk.Button(
    google_session_frame,
    text="Clear",
    command=clear_google_session,
    bg="#555",
    fg="white",
    font=("Segoe UI", 8),
    relief="flat",
    padx=4,
    cursor="hand2",
)
btn_clear_google.pack(side="right", padx=(5, 0))

def paste_cookies_google_session():
    selected = get_selected_countries()
    if len(selected) != 1:
        messagebox.showinfo("Info", "Please select exactly one country for this operation.")
        return
    open_cookie_paste_window(selected[0], "google")

btn_login_google = tk.Button(
    google_session_frame,
    text="Login",
    command=login_google_session,
    bg="#333",
    fg="white",
    font=("Segoe UI", 8),
    relief="flat",
    padx=4,
    cursor="hand2",
)
btn_login_google.pack(side="right")

btn_cookies_google = tk.Button(
    google_session_frame,
    text="Cookies",
    command=paste_cookies_google_session,
    bg="#1E88E5",
    fg="white",
    font=("Segoe UI", 8),
    relief="flat",
    padx=4,
    cursor="hand2",
)
btn_cookies_google.pack(side="right", padx=(0, 5))

google_session_status.pack(side="left", fill="x", expand=True, padx=(10, 5), anchor="w")

# Bing Session Row
bing_session_frame = tk.Frame(card_sessions, bg=CARD_BG)
bing_session_frame.pack(fill="x", pady=(0, 2))

tk.Label(
    bing_session_frame,
    text="Bing:",
    bg=CARD_BG,
    fg=TEXT_COLOR,
    font=("Segoe UI", 10, "bold"),
).pack(side="left")
bing_session_status = tk.Label(
    bing_session_frame,
    text="Select country",
    fg="#b3b3b3",
    bg=CARD_BG,
    font=("Segoe UI", 9, "italic"),
    anchor="w",
)


def clear_bing_session():
    selected = get_selected_countries()
    if len(selected) != 1:
        messagebox.showinfo(
            "Info", "Please select exactly one country for this operation."
        )
        return
    c = selected[0]
    if clear_session(c, "bing"):
        update_session_status()
        messagebox.showinfo("Cleared", f"Bing session for {c} has been cleared.")
    else:
        messagebox.showerror(
            "Error",
            "Could not clear the session. A background Chrome process is locking the files.\n\nPlease close any hanging Chrome processes or run 'taskkill /F /IM chrome.exe' in the terminal.",
        )


def login_bing_session():
    selected = get_selected_countries()
    if len(selected) != 1:
        messagebox.showinfo(
            "Info", "Please select exactly one country for this operation."
        )
        return
    c = selected[0]
    open_login_browser(c, "bing")
    update_session_status()


def paste_cookies_bing_session():
    selected = get_selected_countries()
    if len(selected) != 1:
        messagebox.showinfo("Info", "Please select exactly one country for this operation.")
        return
    open_cookie_paste_window(selected[0], "bing")

btn_clear_bing = tk.Button(
    bing_session_frame,
    text="Clear",
    command=clear_bing_session,
    bg="#555",
    fg="white",
    font=("Segoe UI", 8),
    relief="flat",
    padx=4,
    cursor="hand2",
)
btn_clear_bing.pack(side="right", padx=(5, 0))

btn_login_bing = tk.Button(
    bing_session_frame,
    text="Login",
    command=login_bing_session,
    bg="#333",
    fg="white",
    font=("Segoe UI", 8),
    relief="flat",
    padx=4,
    cursor="hand2",
)
btn_login_bing.pack(side="right")

btn_cookies_bing = tk.Button(
    bing_session_frame,
    text="Cookies",
    command=paste_cookies_bing_session,
    bg="#1E88E5",
    fg="white",
    font=("Segoe UI", 8),
    relief="flat",
    padx=4,
    cursor="hand2",
)
btn_cookies_bing.pack(side="right", padx=(0, 5))

bing_session_status.pack(side="left", fill="x", expand=True, padx=(10, 5), anchor="w")



# Batch Status Area
batch_status_frame = tk.Frame(card_sessions, bg=CARD_BG)
batch_status_scrollbar = tk.Scrollbar(batch_status_frame, orient="vertical")
batch_status_text = tk.Text(
    batch_status_frame, 
    bg=CARD_BG, 
    fg=TEXT_COLOR, 
    font=("Segoe UI", 9), 
    height=5,
    yscrollcommand=batch_status_scrollbar.set,
    state=tk.DISABLED,
    relief="flat",
    wrap="word"
)
batch_status_scrollbar.config(command=batch_status_text.yview)
batch_status_text.pack(side="left", fill="both", expand=True)
batch_status_scrollbar.pack(side="right", fill="y")
# Don't pack it yet, pack it in update_session_status



def quick_login(c, platform):
    open_login_browser(c, platform)
    update_session_status()

def update_session_status():
    """Update session status labels based on current country selection."""
    selected = get_selected_countries()
    
    if len(selected) > 1:
        # Show batch status frame
        batch_status_frame.pack(fill="both", expand=True, pady=(10, 0))
        
        # Build status text
        status_lines = []
        
        google_needs = []
        bing_needs = []
        
        for c in selected:
            if not session_exists(c, "google"):
                google_needs.append(c)
            if not session_exists(c, "bing"):
                bing_needs.append(c)
                
        batch_status_text.config(state=tk.NORMAL)
        batch_status_text.delete(1.0, tk.END)
        
        # Destroy old buttons to prevent memory leak
        for widget in batch_status_text.winfo_children():
            widget.destroy()

        batch_status_text.insert(tk.END, "--- Google ---\n")
        if not google_needs:
            batch_status_text.insert(tk.END, "✅ All logged in\n")
        else:
            batch_status_text.insert(tk.END, f"❌ Missing ({len(google_needs)}):\n")
            for c in google_needs:
                batch_status_text.insert(tk.END, f"  - {c}  ")
                btn = tk.Button(
                    batch_status_text, 
                    text="Login", 
                    command=lambda country=c: quick_login(country, "google"), 
                    bg="#333", fg="white", font=("Segoe UI", 8), cursor="hand2", padx=5, pady=0
                )
                batch_status_text.window_create(tk.END, window=btn)
                batch_status_text.insert(tk.END, "\n")
                
        batch_status_text.insert(tk.END, "\n--- Bing ---\n")
        if not bing_needs:
            batch_status_text.insert(tk.END, "✅ All logged in\n")
        else:
            batch_status_text.insert(tk.END, f"❌ Missing ({len(bing_needs)}):\n")
            for c in bing_needs:
                batch_status_text.insert(tk.END, f"  - {c}  ")
                btn = tk.Button(
                    batch_status_text, 
                    text="Login", 
                    command=lambda country=c: quick_login(country, "bing"), 
                    bg="#333", fg="white", font=("Segoe UI", 8), cursor="hand2", padx=5, pady=0
                )
                batch_status_text.window_create(tk.END, window=btn)
                batch_status_text.insert(tk.END, "\n")
                
        batch_status_text.config(state=tk.DISABLED)
        
        google_session_status.config(text="Batch mode", fg="#b3b3b3")
        bing_session_status.config(text="Batch mode", fg="#b3b3b3")
    elif len(selected) == 1:
        batch_status_frame.pack_forget()
        c = selected[0]
        if session_exists(c, "google"):
            google_session_status.config(text="\u2705 Logged in", fg="#4CAF50")
        else:
            google_session_status.config(text="\ud83d\udd34 Not logged in", fg="#E50914")
        if session_exists(c, "bing"):
            bing_session_status.config(text="\u2705 Logged in", fg="#4CAF50")
        else:
            bing_session_status.config(text="\ud83d\udd34 Not logged in", fg="#E50914")
    else:
        batch_status_frame.pack_forget()
        google_session_status.config(text="Select country", fg="#b3b3b3")
        bing_session_status.config(text="Select country", fg="#b3b3b3")

    try:
        update_start_button_state()
    except NameError:
        pass


# === RIGHT COLUMN: STATUS & ACTION ===
right_col = ttk.Frame(grid_frame)
right_col.grid(row=0, column=1, sticky="nsew")

# 1. Action Button
btn_frame = ttk.Frame(right_col)
btn_frame.pack(fill="x", pady=(0, 20))

global chk_dev_headless
chk_dev_headless = tk.Checkbutton(
    btn_frame, 
    text="Show Browser Window", 
    variable=dev_headless_var, 
    onvalue=False,
    offvalue=True,
    bg=BG_COLOR, 
    fg=TEXT_COLOR, 
    selectcolor=BG_COLOR,
    font=("Segoe UI", 10, "bold")
)
chk_dev_headless.pack(side="top", pady=(0, 10))

start_btn = PulsingButton(
    btn_frame,
    text="START EXECUTION ►",
    command=lambda: run_bot(
        get_selected_countries,
        progressbar,
        logbox,
        start_btn,
        stop_btn,
        current_sa_info_holder["sa_info"],
        chk_bing_resubmit,
        chk_gse_fetch,
        chk_gsc_resubmit,
        chk_gsc_inner_validation,
        chk_delete_stale,
        chk_gsc_d,
        chk_gsc_f,
        chk_gsc_others,
        chk_bing_d,
        chk_bing_f,
        chk_bing_others,
        chk_val_d,
        chk_val_f,
        chk_val_others,
    ),
)
start_btn.pack(side="left", fill="x", expand=True, padx=(0, 5))

stop_btn = PulsingButton(
    btn_frame, text="STOP ⏹", command=lambda: stop_bot(), bg="#B22222"
)
stop_btn.pack(side="right", fill="x", expand=True, padx=(5, 0))
stop_btn.config(state="disabled")


def update_start_button_state(*args):
    selected = get_selected_countries()
    if not selected:
        start_btn.config(state="disabled")
        return

    google_needed = (
        chk_gse_fetch.get()
        or chk_gsc_inner_validation.get()
        or chk_gsc_resubmit.get()
        or chk_delete_stale.get()
    )
    bing_needed = chk_bing_resubmit.get() or chk_delete_stale.get()

    if not (google_needed or bing_needed):
        start_btn.config(state=tk.DISABLED)
        return

    # In Batch Mode, run_bot automatically performs Pre-flight Login Checks for all selected countries.
    # Therefore, the start button should always be enabled if tasks and countries are selected.
    start_btn.config(state=tk.NORMAL)


chk_bing_resubmit.trace_add("write", update_start_button_state)
chk_gse_fetch.trace_add("write", update_start_button_state)
chk_gsc_resubmit.trace_add("write", update_start_button_state)
chk_gsc_inner_validation.trace_add("write", update_start_button_state)
chk_delete_stale.trace_add("write", update_start_button_state)
chk_gsc_d.trace_add("write", update_start_button_state)
chk_gsc_f.trace_add("write", update_start_button_state)
chk_gsc_others.trace_add("write", update_start_button_state)
chk_bing_d.trace_add("write", update_start_button_state)
chk_bing_f.trace_add("write", update_start_button_state)
chk_bing_others.trace_add("write", update_start_button_state)
chk_val_d.trace_add("write", update_start_button_state)
chk_val_f.trace_add("write", update_start_button_state)
chk_val_others.trace_add("write", update_start_button_state)
update_start_button_state()

# 2. Progress
progress_frame = tk.Frame(right_col, bg=BG_COLOR)
progress_frame.pack(fill="x", pady=(0, 20))
ttk.Label(
    progress_frame, text="Task Progress", background=BG_COLOR, foreground=TEXT_COLOR
).pack(anchor="w", pady=(0, 5))
progressbar = ttk.Progressbar(progress_frame, mode="determinate")
progressbar.pack(fill="x", ipady=5)

# 3. Live Stats (Custom Grid)
stats_card = tk.Frame(right_col, bg=CARD_BG, padx=15, pady=15)
stats_card.pack(fill="x", pady=(0, 20))
tk.Label(
    stats_card,
    text="Live Statistics",
    bg=CARD_BG,
    fg=SUBTEXT_COLOR,
    font=("Segoe UI", 10, "bold"),
).pack(anchor="w", pady=(0, 10))

stats_grid = tk.Frame(stats_card, bg=CARD_BG)
stats_grid.pack(fill="x")
stats_grid.columnconfigure((0, 1, 2), weight=1)


def make_stat_box(parent, label, row, col):
    f = tk.Frame(parent, bg=CARD_BG, pady=5)
    f.grid(row=row, column=col, sticky="nsew")
    tk.Label(f, text=label, bg=CARD_BG, fg=SUBTEXT_COLOR, font=("Segoe UI", 9)).pack(
        anchor="center"
    )
    val = tk.Label(
        f, text="--", bg=CARD_BG, fg=ACCENT_COLOR, font=("Segoe UI", 14, "bold")
    )
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
log_label = tk.Label(
    right_col,
    text="System Log",
    bg=BG_COLOR,
    fg=TEXT_COLOR,
    font=("Segoe UI", 10, "bold"),
)
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
    highlightbackground="#333",
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

def type_writer(label, text, index=0):
    """Effect: Type text character by character"""
    if index < len(text):
        label.config(text=label.cget("text") + text[index])
        # Randomize typing speed slightly for realism
        delay = 50
        label.after(delay, type_writer, label, text, index + 1)

# Start Typewriter
root.after(500, lambda: type_writer(lbl_main_title, f"SEO Automation {CURRENT_VERSION.upper()}(beta)"))

root.mainloop()
