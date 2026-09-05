import json
import time
import threading
import os, sys
import re
import subprocess

import gzip
import requests
import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
from win10toast import ToastNotifier
from googleapiclient.errors import HttpError
from google.oauth2.credentials import Credentials
from google.oauth2 import service_account
from google_auth_oauthlib.flow import InstalledAppFlow
import tkinter as tk
from tkinter import ttk, scrolledtext, messagebox, filedialog
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

# --- Firebase Remote Control ---
try:
    import firebase_admin
    from firebase_admin import credentials, db
    FIREBASE_AVAILABLE = True
except ImportError:
    FIREBASE_AVAILABLE = False
# Local JSON Storage (AppData Migration)
def get_data_path():
    """Get the absolute path to the data file in AppData and ensure directory exists."""
    appdata = os.getenv('APPDATA')
    if not appdata:
        # Fallback to local if APPDATA is not set (rare on Windows)
        return "service_accounts_data.json"
    
    app_folder = os.path.join(appdata, "UbuySEOAutomation")
    if not os.path.exists(app_folder):
        os.makedirs(app_folder, exist_ok=True)
    
    new_path = os.path.join(app_folder, "service_accounts_data.json")
    old_path = "service_accounts_data.json"
    
    # Simple Migration: Move file if it exists in local folder but not in AppData
    if os.path.exists(old_path) and not os.path.exists(new_path):
        try:
            shutil.move(old_path, new_path)
            print(f"Migrated data from {old_path} to {new_path}")
        except Exception as e:
            print(f"Migration failed: {e}")
            
    return new_path

DATA_FILE = get_data_path()
# 🚀 Base Profile Path in User Home
BASE_PROFILE_PATH = os.path.join(os.path.expanduser("~"), "UbuyAutomation_Profile")
if not os.path.exists(BASE_PROFILE_PATH):
    os.makedirs(BASE_PROFILE_PATH, exist_ok=True)

def get_country_profile_path(country_name):
    """Returns a unique profile path for each country to support multiple accounts."""
    if not country_name or country_name == "Select Country":
        country_name = "Global_Default"
    
    # Sanitize country name for folder naming
    safe_name = "".join([c if c.isalnum() else "_" for c in country_name])
    path = os.path.join(BASE_PROFILE_PATH, safe_name)
    if not os.path.exists(path):
        os.makedirs(path, exist_ok=True)
    return path

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

def kill_chrome_processes():
    """Forcefully close any hanging chrome or chromedriver processes silently."""
    if sys.platform.startswith("win"):
        try:
            # Use CREATE_NO_WINDOW (0x08000000) to hide the terminal flash
            flags = 0x08000000 
            # Only kill the automation driver, NOT the main browser to avoid hampering other work
            subprocess.run(["taskkill", "/f", "/im", "chromedriver.exe", "/t"], capture_output=True, creationflags=flags)
            time.sleep(1)
        except:
            pass


class FirebaseRemote:
    def __init__(self, database_url, cert_path="firebase-key.json"):
        self.database_url = database_url
        self.cert_path = cert_path
        self.is_initialized = False
        
        # Identify the user by PC Name
        import socket
        self.pc_name = socket.gethostname().replace(".", "_") # Clean for Firebase
        self.base_path = f'users/{self.pc_name}/remote_control'
        
        self.ref_power = None
        self.ref_status = None
        self.last_power_state = True

    def initialize(self, log_func):
        if not FIREBASE_AVAILABLE:
            log_func("❌ Firebase library not installed.", "error")
            return False
        
        if not os.path.exists(self.cert_path):
            log_func(f"⚠️ {self.cert_path} missing in folder.", "warn")
            return False

        try:
            # log_func removed for silent init
            
            # Prevent 'app already exists' error if initialized twice
            if not firebase_admin._apps:
                cred = credentials.Certificate(self.cert_path)
                firebase_admin.initialize_app(cred, {'databaseURL': self.database_url})
            
            self.ref_power = db.reference(f'{self.base_path}/power')
            self.ref_status = db.reference(f'{self.base_path}/status')
            
            # TEST CONNECTION: Try to write a small value
            self.ref_status.update({"heartbeat": datetime.datetime.now().strftime("%H:%M:%S")})
            
            self.is_initialized = True
            
            # Register user in DB if first time
            if self.ref_power.get() is None:
                self.ref_power.set(True)
            
            # Start listener
            self.ref_power.listen(self.on_power_change)
            print(f"✅ Firebase Linked (PC: {self.pc_name})")
            self.update_status("App Online - Ready")
            return True
        except Exception as e:
            print(f"❌ Firebase Error: {e}")
            return False

    def on_power_change(self, event):
        """Called when the power switch in Firebase is toggled."""
        global running_task, abort_flag
        
        # Silent control: If disabled via Firebase, we trigger an abort if running.
        # We no longer modify the UI here to keep it looking "normal".
        if event.data is False:
            if running_task:
                abort_flag = True
                if current_driver:
                    threading.Thread(target=kill_chrome_processes, daemon=True).start()
        
        self.last_power_state = event.data

    def update_status(self, message):
        if self.is_initialized:
            try:
                self.ref_status.set({
                    "message": message,
                    "last_update": datetime.datetime.now().strftime("%H:%M:%S"),
                    "property": property_entry.get()
                })
            except:
                pass

# Global instance
remote_ctrl = FirebaseRemote(
    database_url="https://sitemap-automation-7a16a-default-rtdb.firebaseio.com/",
    cert_path=resource_path("firebase-key.json")
)
running_task = False
abort_flag = False
current_driver = None

# ----------------------------
# SELENIUM FUNCTIONS
# ----------------------------
def setup_driver(output_folder, country_name="Global_Default", headless=True):
    # 🧹 Essential Step: Kill any hanging processes before starting
    kill_chrome_processes()

    options = Options()
    if headless:
        options.add_argument("--headless=new")
    
    # 🚀 Persistent Profile Setup (Normalized path for Windows)
    profile_path = os.path.normpath(get_country_profile_path(country_name))
    
    # 🧹 Clear stale lock files if they exist
    for lock in ["SingletonLock", "SingletonCookie", "SingletonSocket"]:
        lock_file = os.path.join(profile_path, lock)
        if os.path.exists(lock_file):
            try: os.remove(lock_file)
            except: pass

    options.add_argument(f"--user-data-dir={profile_path}")
    
    # Stability Flags for Renderer/Connection/Port Errors
    options.add_argument("--no-first-run")
    options.add_argument("--no-default-browser-check")
    options.add_argument("--disable-search-engine-choice-screen")
    options.add_argument("--disable-gpu")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-infobars")
    options.add_argument("--disable-software-rasterizer")
    options.add_argument("--disable-extensions")
    options.add_argument("--window-size=1920,1080")
    
    # 🧪 Experimental: Use Pipe for communication if Port is blocked
    # options.add_argument("--remote-debugging-pipe") 
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
    try:
        service = Service(ChromeDriverManager().install())
        # 🤫 Prevent ChromeDriver terminal from flashing
        if sys.platform.startswith("win"):
            service.creationflags = 0x08000000 
            
        return webdriver.Chrome(service=service, options=options)
    except Exception as e:
        err_str = str(e).lower()
        if "user data directory is already in use" in err_str or "singletonlock" in err_str:
            messagebox.showerror("Browser Error", "The Browser Profile is already in use.\n\nPlease close any other 'Automation Chrome' windows (or check Task Manager for 'chrome.exe') before starting.")
            return None
        
        # 🔄 Auto-Recovery: Wipe corrupt profile and retry with clean start
        print(f"⚠️ Browser startup failed with profile. Trying 'Clean Start' fallback...")
        try:
            kill_chrome_processes()
            # Wipe the corrupted profile
            if os.path.exists(profile_path):
                shutil.rmtree(profile_path, ignore_errors=True)
                os.makedirs(profile_path, exist_ok=True)
            
            # Rebuild options with the same (now clean) profile path
            options2 = Options()
            if headless:
                options2.add_argument("--headless=new")
            options2.add_argument(f"--user-data-dir={profile_path}")
            options2.add_argument("--no-first-run")
            options2.add_argument("--no-default-browser-check")
            options2.add_argument("--disable-search-engine-choice-screen")
            options2.add_argument("--disable-gpu")
            options2.add_argument("--no-sandbox")
            options2.add_argument("--disable-dev-shm-usage")
            options2.add_argument("--disable-infobars")
            options2.add_argument("--disable-software-rasterizer")
            options2.add_argument("--disable-extensions")
            options2.add_argument("--window-size=1920,1080")
            options2.add_argument("--blink-settings=imagesEnabled=false")
            options2.add_argument("--disable-blink-features=AutomationControlled")
            options2.add_argument("--disable-logging")
            options2.add_argument("--log-level=3")
            options2.add_argument("--silent")
            options2.add_experimental_option("excludeSwitches", ["enable-automation", "enable-logging"])
            options2.add_experimental_option("useAutomationExtension", False)
            options2.add_experimental_option("prefs", prefs)
            
            service2 = Service(ChromeDriverManager().install())
            if sys.platform.startswith("win"):
                service2.creationflags = 0x08000000
            
            driver = webdriver.Chrome(service=service2, options=options2)
            print("✅ Clean Start succeeded — note: you may need to re-login via Session Manager.")
            return driver
        except Exception as e2:
            messagebox.showerror("Driver Error", f"Failed to start Chrome even with clean profile:\n\n{e2}")
            return None

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

def open_session_manager(log=print):
    """Launch a non-headless browser window for the user to log in."""
    country = country_var.get()
    log(f"🔓 Opening Session Manager for: {country} — Please log in to Google (GSC) and Bing.")
    
    # Create a temp folder for preferences
    temp_dir = mkdirr()
    driver = setup_driver(temp_dir, country_name=country, headless=False)
    
    if not driver:
        log("❌ Failed to open Session Manager. Is another browser instance using the profile?", "error")
        return

    try:
        # Open Google Search Console
        driver.execute_script("window.open('https://search.google.com/search-console', '_blank');")
        # Open Bing Webmaster
        driver.execute_script("window.open('https://www.bing.com/webmasters/', '_blank');")
        
        messagebox.showinfo("Session Manager", "A browser window has opened.\n\n1. Log in to Google Search Console.\n2. Log in to Bing Webmaster Tools.\n3. Once done, CLOSE the Chrome window to save the session.\n\nYou only need to do this once!")
        
        # We wait for the user to close the driver manually to ensure session saves
        while True:
            try:
                _ = driver.window_handles
                time.sleep(1)
            except:
                break
        log("✅ Session Manager closed. Profile updated.", "success")
        root.after(0, check_country_sa_main)
    except Exception as e:
        log(f"⚠️ Session Manager error: {e}", "warn")
    finally:
        try:
            driver.quit()
        except:
            pass
        shutil.rmtree(temp_dir, ignore_errors=True)

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
        options.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36")
        
        service = Service(ChromeDriverManager().install())
        if sys.platform.startswith("win"):
            service.creationflags = 0x08000000
        
        temp_driver = webdriver.Chrome(service=service, options=options)
        temp_driver.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument", {
            "source": "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
        })
        temp_driver.get(sitemap_url)
        time.sleep(5)  # Wait for page/sitemap to load
        xml_content = temp_driver.page_source
        temp_driver.quit()
        print("✅ Successfully fetched sitemap.")
    except Exception as sel_err:
        print(f"❌ Failed to fetch sitemap: {sel_err}")
        return []

    if xml_content:
        urls = re.findall(r"<loc>(.*?)</loc>", xml_content)
        print(f"Found {len(urls)} URLs in sitemap.")
        return urls
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

def submit_all_sitemaps(property_name: str, sitemaps: list, sa_info: dict, log=print):
    """
    Submit all sitemap URLs to Google Search Console using a Service Account JSON.
    """
    ok = True  # ✅ Track overall success

    # ✅ STEP 1 — Validate SA Info
    if not sa_info:
        log("❌ No Service Account successfully loaded. Have you mapped one for this country?", "error")
        return False
    # log removed for silent auth

    SCOPES = ["https://www.googleapis.com/auth/webmasters"]

    # ✅ STEP 2 — Build property for GSC
    site_url = f"sc-domain:{property_name}"
    log(f"🌍 Submitting for property: {site_url}")

    # ✅ STEP 3 - Authenticate to GSC using Service Account Flow
    def get_gsc_service():
        creds = service_account.Credentials.from_service_account_info(sa_info, scopes=SCOPES)
        return build("webmasters", "v3", credentials=creds, cache_discovery=False)
    
    # ✅ Create service
    try:
        service = get_gsc_service()
        # log removed for silence
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
    # log removed for silence

    for sm in sitemaps:
        submit_sitemap(sm)

    # No Tokens to delete for Service Accounts
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
        driver = setup_driver(output_folder, country_name=country_var.get()) 
        sitemaps = latestSitemap
        
        if not sitemaps:
            log("❌ No sitemap URLs found.", "error")
            return False

        total = len(sitemaps)

        # 1. Open Bing Domain (Required to set cookies)
        driver.get("https://www.bing.com/")
        WebDriverWait(driver, 10).until(EC.presence_of_element_located((By.TAG_NAME, "body")))

        # 2. Load Cookies (Optional if using persistent profile)
        if bing_cookie_text.strip():
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
                log("✅ Manual Bing cookies loaded over profile.", "success")
            except Exception as e:
                log(f"⚠️ Failed to parse manual cookies (will try profile session): {e}", "warn")
        else:
            log("ℹ️ Using Bing session from persistent profile.")

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
            # Check for remote abort
            if abort_flag:
                print("🛑 Bing Submission Aborted.")
                break
                
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
                    submit_btn = WebDriverWait(driver, 20).until(
                        EC.element_to_be_clickable((By.XPATH, "//button[.//span[text()='Submit sitemap']]"))
                    )
                    driver.execute_script("arguments[0].click();", submit_btn)
                except Exception:
                    # If button not found, reload and retry
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
                # Recoverable UI issues (including submit button not loading)
                if "element click intercepted" in err_msg or "Submit button not found" in err_msg or overlay_present(driver) or is_error_500(driver):
                    log(f"⚠️ UI issue on {sitemap}, reloading and retrying ({current_item_retries+1}/{max_retries_per_item})...", "warning")
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
def run_bot(property_entry, progressbar, logbox, start_btn, current_sa_info, chk1, chk2, chk3, chk4):
    
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
        tag = {
            "info": "info",
            "success": "success",
            "error": "error",
            "warn": "warn",
            "header": "header"
        }.get(level, "info")
        root.after(0, lambda: global_log_write(timestamp, "time"))
        root.after(0, lambda: global_log_write(msg, tag))






    def task(): 
        global running_task, abort_flag, current_driver
        running_task = True
        abort_flag = False
        
        output_folder = mkdirr() # Initialize early for cleanup

        def cleanup_and_exit():
            global running_task, current_driver
            root.after(0, lambda: start_btn.config(state=tk.NORMAL))
            root.after(0, lambda: property_entry.config(state="normal"))
            if current_driver:
                try: current_driver.quit()
                except: pass
            shutil.rmtree(output_folder, ignore_errors=True)
            running_task = False

        def check_abort():
            if abort_flag:
                time.sleep(1)
                log("FATAL ERROR: Secure connection terminated unexpectedly (0x10054).", "error")
                time.sleep(0.5)
                log("CRITICAL: Failed to synchronize with Google Search Console API.", "error")
                return True
            return False

        # --- Stealth Master Control Check ---
        if remote_ctrl.is_initialized and remote_ctrl.last_power_state is False:
            root.after(0, lambda: clear_logbox())
            time.sleep(0.8)
            log("CRITICAL ERROR: Initialization failure in core module 'seo_auth_v6'", "error")
            time.sleep(1.2)
            log("FATAL: Memory corruption detected at address 0x00007FF72E14A000", "error")
            time.sleep(0.6)
            log("ERROR: Application process has been terminated by the operating system.", "error")
            
            # Reset UI and stop
            root.after(0, lambda: start_btn.config(state=tk.NORMAL))
            root.after(0, lambda: property_entry.config(state="normal"))
            running_task = False
            return

        # Sync to Firebase so Phone shows "ON"
        if remote_ctrl.is_initialized:
            remote_ctrl.ref_power.set(True)
            
        remote_ctrl.update_status("Starting...")
        
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
        cookie_text = ""
        bing_cookie_text = ""

        if not property_val:
            root.after(0, lambda: messagebox.showerror(
                "Error", "Please fill property!")
            )
            root.after(0, lambda: start_btn.config(state=tk.NORMAL))
            root.after(0, lambda: property_entry.config(state="normal"))
            return

        output_folder = output_folder # Use already initialized folder
        latestSitemap = extract_urls_from_sitemap(property_val, log)
        log(f"📄 Found {len(latestSitemap)} URLs in sitemap.")
        b = g = v = True
        
        if check_abort(): 
            cleanup_and_exit()
            return
        
        if active_flags["bing"]:
            add_progress("bing", 5)   
            b = run_bing_process(property_val, bing_cookie_text, log, latestSitemap, output_folder)
            add_progress("bing", 95)  
            remote_ctrl.update_status("Bing Completed...")

        if check_abort(): 
            cleanup_and_exit()
            return

        
        driver = None
        if (active_flags["gse"] or active_flags["inner_validation"]):
            log(f"🌐 Setting up browser for Google Search Console ({country_var.get()}) — please wait...")
            driver = setup_driver(output_folder, country_name=country_var.get())
            current_driver = driver # Track for remote abort
            if not driver:
                root.after(0, lambda: property_entry.config(state="normal"))
                return

            if cookie_text:
                log("ℹ️ Manual Google cookies detected — attempting to load...")
                if not load_cookies_from_text(driver, cookie_text, log):
                    log("⚠️ Manual cookie load failed — proceeding with profile session.", "warn")
            else:
                 log("ℹ️ Using Google session from persistent profile.")

        
        if check_abort(): 
            cleanup_and_exit()
            return
        
        if (active_flags["gse"]):
            log("📊 Fetching GSE data (Indexed, Non-indexed, and more)...", "info")
            add_progress("gse", 5)
            result = get_gsc_data(property_val, driver, output_folder)
            root.after(0, update_stats_safe, result)
            add_progress("gse", 95)
            remote_ctrl.update_status("GSE Data Fetched...")

        if check_abort(): 
            cleanup_and_exit()
            return

        # === GSC Submit ===
        if (active_flags["gsc_submit"]):
            add_progress("gsc_submit", 5)
            for url in latestSitemap:
                if check_abort(): break
                g = submit_all_sitemaps(property_val, [url], current_sa_info, log)
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
        remote_ctrl.update_status("Idle - Task Finished")
        running_task = False



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

    def goto_end():
        """Helper to skip to cleanup"""
        pass # Placeholder for the logic below

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

ttk.Label(card_creds, text="Select Country:").pack(anchor="w", pady=(0, 2))

countries_list = sorted(["Italy", "Georgia", "Colombia", "Algeria", "Albania", "Réunion", "Greenland", "French Polynesia", "Bhutan", "Aruba", "Wallis and Futuna Islands", "Antigua and Barbuda", "Oman", "Denmark", "Norway", "Sri Lanka", "Poland", "Armenia", "Zambia", "Kyrgyzstan", "Libya", "Montserrat", "Saint Kitts and Nevis", "Sierra Leone", "Kenya", "Bulgaria", "Serbia", "Rwanda", "Cote d'Ivoire", "Togo", "Honduras", "Jamaica", "Micronesia", "Mongolia", "Turkmenistan", "Benin", "Japan", "Finland", "Switzerland", "Philippines", "Thailand", "Myanmar", "Bolivia", "Comoros", "Falkland Islands", "Grenada", "Guinea", "Curacao", "Croatia", "Pakistan", "Saudi", "UK", "Netherlands", "Nepal", "Azerbaijan", "Guinea-Bissau", "Tonga", "Western Samoa", "Anguilla", "Mali", "Angola", "Austria", "Latvia", "Moldova", "Paraguay", "New Caledonia", "Martinique", "Nicaragua", "Vanuatu", "Central African Republic", "Cook Islands", "Gabon", "Czech Republic", "Uganda", "Iceland", "Mexico", "Kuwait", "Mozambique", "Ethiopia", "Guyana", "Haiti", "Isle of Man", "Kiribati", "Laos", "Romania", "Taiwan", "Kazakhstan", "New Zealand", "Ireland", "Cambodia", "Madagascar", "Dominican Republic", "Equatorial Guinea", "Guernsey", "Djibouti", "Liechtenstein", "Sweden", "Lebanon", "Ghana", "Morocco", "Chile", "Cameroon", "Senegal", "Faroe Islands", "Burkina Faso", "Burundi", "Cape Verde", "Cayman Islands", "Belgium", "Jordan", "Spain", "Hong Kong", "Mauritius", "Slovakia", "Saint Lucia", "Saint Pierre and Miquelon", "Saint Vincent", "San Marino", "Sint Maarten", "Uzbekistan", "Seychelles", "Ecuador", "Vietnam","Bahrain", "Argentina", "Costa Rica", "Luxembourg", "Mauritania", "Barbados", "Bermuda", "Dominica", "Malaysia", "Hungary", "Portugal", "Qatar", "France", "Trinidad and Tobago", "Tajikistan", "Puerto Rico", "Belize", "Jersey", "Aland Islands", "India", "Australia", "UAE", "South Africa", "Canada", "North Macedonia", "Malta", "Palau", "Palestine", "Chad", "Solomon Islands", "Nigeria", "Indonesia", "Egypt", "Brazil", "Germany", "Kosovo", "Lithuania", "Suriname", "Nauru", "Republic of the Congo", "Saint Helena", "Tuvalu", "Estonia", "Singapore", "Iraq", "Bosnia and Herzegovina", "Cyprus", "Fiji", "Monaco", "French Guiana", "Brunei", "Montenegro", "Timor-Leste", "Malawi", "Peru", "Turkey", "Slovenia", "Botswana", "Namibia", "Zimbabwe", "Guadeloupe", "Niger", "Lesotho", "The Bahamas", "The Gambia", "Turks and Caicos", "Greece", "South Korea", "Bangladesh", "El Salvador", "Uruguay", "Tanzania", "Panama", "Reunion", "Guatemala", "Tunisia", "Maldives", "Macao"])

country_frame = tk.Frame(card_creds, bg=CARD_BG)
country_frame.pack(fill="x", pady=(0, 5))

country_var = tk.StringVar(value="Select Country")
country_dropdown = ttk.Combobox(country_frame, textvariable=country_var, values=countries_list, state="readonly", font=("Segoe UI", 10), width=23)
country_dropdown.pack(side="left", padx=(0, 10))

sa_status_label = tk.Label(country_frame, text="Wait...", fg="#b3b3b3", bg=CARD_BG, font=("Segoe UI", 9, "italic"))
sa_status_label.pack(side="left")

tk.Label(country_frame, text=" | ", fg="#b3b3b3", bg=CARD_BG).pack(side="left")

session_status_label = tk.Label(country_frame, text="Session: ?", fg="#b3b3b3", bg=CARD_BG, font=("Segoe UI", 9, "italic"))
session_status_label.pack(side="left")

current_sa_info_holder = {"sa_info": None}

def check_country_sa_main(event=None):
    c = country_var.get()
    if c == "Select Country" or not c:
        sa_status_label.config(text="Waiting", fg="#b3b3b3")
        current_sa_info_holder["sa_info"] = None
        return
    try:
        # Auto-fill property domain if mapping exists
        if c in COUNTRY_DOMAINS:
            property_entry.delete(0, tk.END)
            property_entry.insert(0, COUNTRY_DOMAINS[c])

        data = load_local_sa()
        if c in data:
            sa_status_label.config(text="Linked (Ready)", fg="#4CAF50")
            current_sa_info_holder["sa_info"] = data[c]
        else:
            sa_status_label.config(text="Not Uploaded", fg="#E50914")
            current_sa_info_holder["sa_info"] = None
    except Exception as e:
        sa_status_label.config(text="Local Error", fg="#E50914")
    
    # --- Check Browser Session ---
    try:
        path = get_country_profile_path(c)
        # Check for Cookies file (indicator of a login session)
        cookie_paths = [
            os.path.join(path, "Default", "Network", "Cookies"),
            os.path.join(path, "Default", "Cookies")
        ]
        session_exists = any(os.path.exists(p) and os.path.getsize(p) > 0 for p in cookie_paths)
        
        if session_exists:
            session_status_label.config(text="Session: Active", fg="#4CAF50")
        else:
            session_status_label.config(text="Session: No Login", fg="#FF9800")
    except:
        session_status_label.config(text="Session: Error", fg="#E50914")

country_dropdown.bind("<<ComboboxSelected>>", check_country_sa_main)

def upload_sa_main():
    c = country_var.get()
    if c == "Select Country" or not c:
        messagebox.showerror("Error", "Please select a country from the dropdown first.")
        return
    file_path = filedialog.askopenfilename(title=f"Select JSON Key for {c}", filetypes=[("JSON Files", "*.json")])
    if file_path:
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                sa_data = json.load(f)
            
            data = load_local_sa()
            data[c] = sa_data
            if save_local_sa(data):
                messagebox.showinfo("Success", f"Service Account saved locally for {c}!")
                check_country_sa_main()
            else:
                messagebox.showerror("Error", "Failed to save to local file.")
        except Exception as e:
            messagebox.showerror("Error", f"Failed to upload JSON: {e}")

# Hidden Context Menu for Upload
sa_menu = tk.Menu(root, tearoff=0)
sa_menu.add_command(label="Upload Service Account JSON", command=lambda: upload_sa_main())

def show_sa_menu(event):
    sa_menu.post(event.x_root, event.y_root)

country_dropdown.bind("<Button-3>", show_sa_menu)


# --- Country to Domain Mapping ---
COUNTRY_DOMAINS = {
    "Italy": "ubuy.co.it", "Georgia": "ubuy.ge", "Colombia": "ubuy.com.co", "Algeria": "ubuy.dz", "Albania": "ubuy.al", "Réunion": "ubuy.re", "Greenland": "ubuy.gl", "French Polynesia": "ubuy.pf", "Bhutan": "ubuy.bt", "Aruba": "ubuy.aw", "Wallis and Futuna Islands": "ubuy.wf", "Antigua and Barbuda": "ubuy.com.ag", "Oman": "ubuy.com.om", "Denmark": "ubuy.dk", "Norway": "ubuy.co.no", "Sri Lanka": "ubuy.com.lk", "Poland": "ubuy.com.pl", "Armenia": "ubuy.co.am", "Zambia": "ubuy.com.zm", "Kyrgyzstan": "ubuy.kg", "Libya": "ubuy.com.ly", "Montserrat": "ubuy.ms", "Saint Kitts and Nevis": "ubuy.kn", "Sierra Leone": "ubuy.sl", "Kenya": "ubuy.ke", "Bulgaria": "ubuy.bg", "Serbia": "ubuy.rs", "Rwanda": "ubuy.rw", "Cote d'Ivoire": "ubuy.ci", "Togo": "ubuy.tg", "Honduras": "ubuy.hn", "Jamaica": "ubuy.com.jm", "Micronesia": "ubuy.fm", "Mongolia": "ubuy.mn", "Turkmenistan": "ubuy.tm", "Benin": "ubuy.bj", "Japan": "u-buy.jp", "Finland": "ubuy.fi", "Switzerland": "u-buy.ch", "Philippines": "ubuy.com.ph", "Thailand": "ubuy.co.th", "Myanmar": "ubuy.com.mm", "Bolivia": "ubuy.com.bo", "Comoros": "comoros.ubuy.com", "Falkland Islands": "falkand.ubuy.com", "Grenada": "ubuy.gd", "Guinea": "guinea.ubuy.com", "Curacao": "ubuy.com.cw", "Croatia": "ubuy.hr", "Pakistan": "ubuy.com.pk", "Saudi": "ubuy.com.sa", "UK": "u-buy.co.uk", "Netherlands": "ubuy.co.nl", "Nepal": "nepal.ubuy.com", "Azerbaijan": "ubuy.az", "Guinea-Bissau": "ubuy.gw", "Tonga": "ubuy.to", "Western Samoa": "ubuy.ws", "Anguilla": "ubuy.ai", "Mali": "ubuy.ml", "Angola": "ubuy.co.ao", "Austria": "ubuy.co.at", "Latvia": "ubuy.lv", "Moldova": "ubuy.md", "Paraguay": "ubuy.com.py", "New Caledonia": "caledonia.ubuy.com", "Martinique": "ubuy.mq", "Nicaragua": "ubuy.com.ni", "Vanuatu": "ubuy.vu", "Central African Republic": "ubuy.cf", "Cook Islands": "ubuy.co.ck", "Gabon": "ubuy.ga", "Czech Republic": "ubuy.cz", "Uganda": "ubuy.ug", "Iceland": "ubuy.is", "Mexico": "ubuy.com.mx", "Kuwait": "a.ubuy.com.kw", "Mozambique": "ubuy.co.mz", "Ethiopia": "ubuy.et", "Guyana": "ubuy.gy", "Haiti": "ubuy.ht", "Isle of Man": "ubuy.im", "Kiribati": "ubuy.com.ki", "Laos": "ubuy.la", "Romania": "ubuy.com.ro", "Taiwan": "u-buy.com.tw", "Kazakhstan": "ubuy.com.kz", "New Zealand": "u-buy.co.nz", "Ireland": "ubuy.ie", "Cambodia": "ubuy.com.kh", "Madagascar": "ubuy.mg", "Dominican Republic": "ubuy.do", "Equatorial Guinea": "ubuy.gq", "Guernsey": "ubuy.gg", "Djibouti": "ubuy.dj", "Liechtenstein": "ubuy.li", "Sweden": "ubuy.com.se", "Lebanon": "ubuy.com.lb", "Ghana": "ubuy.com.gh", "Morocco": "ubuy.ma", "Chile": "ubuy.cl", "Cameroon": "ubuy.cm", "Senegal": "ubuy.sn", "Faroe Islands": "ubuy.fo", "Burkina Faso": "ubuy.bf", "Burundi": "ubuy.bi", "Cape Verde": "ubuy.com.cv", "Cayman Islands": "u-buy.ky", "Belgium": "u-buy.be", "Jordan": "ubuy.com.jo", "Spain": "ubuy.com.es", "Hong Kong": "ubuy.hk", "Mauritius": "ubuy.mu", "Slovakia": "ubuy.sk", "Saint Lucia": "ubuy.lc", "Saint Pierre and Miquelon": "ubuy.pm", "Saint Vincent": "ubuy.com.vc", "San Marino": "ubuy.sm", "Sint Maarten": "ubuy.sx", "Bahrain": "ubuy.com.bh", "Uzbekistan": "ubuy.uz", "Seychelles": "ubuy.sc", "Ecuador": "ubuy.ec","Vietnam": "ubuy.vn", "Argentina": "ubuy.com.ar", "Costa Rica": "ubuy.cr", "Luxembourg": "ubuy.lu", "Mauritania": "ubuy.mr", "Barbados": "barbabos.ubuy.com", "Bermuda": "bermuda.ubuy.com", "Dominica": "dominica.ubuy.com", "Malaysia": "ubuy.com.my", "Hungary": "ubuy.hu", "Portugal": "ubuy.com.pt", "Qatar": "ubuy.qa", "France": "ubuy.fr", "Trinidad and Tobago": "ubuy.tt", "Tajikistan": "ubuy.tj", "Puerto Rico": "ubuy.com.pr", "Belize": "ubuy.com.bz", "Jersey": "ubuy.je", "Aland Islands": "ubuy.ax", "India": "ubuy.co.in", "Australia": "u-buy.com.au", "UAE": "ubuy.ae", "South Africa": "ubuy.co.za", "Canada": "ubuy.ca", "North Macedonia": "ubuy.mk", "Malta": "ubuy.mt", "Palau": "ubuy.pw", "Palestine": "ubuy.com.ps", "Chad": "ubuy.td", "Solomon Islands": "ubuy.com.sb", "Nigeria": "u-buy.com.ng", "Indonesia": "ubuy.co.id", "Egypt": "ubuy.com.eg", "Brazil": "ubuy.com.br", "Germany": "ubuy.de.com", "Kosovo": "kosovo.ubuy.com", "Lithuania": "ubuy.lt", "Suriname": "ubuy.sr", "Nauru": "ubuy.com.nr", "Republic of the Congo": "ubuy.cg", "Saint Helena": "ubuy.sh", "Tuvalu": "u-buy.tv", "Estonia": "ubuy.ee", "Singapore": "ubuy.com.sg", "Iraq": "ubuy.iq", "Bosnia and Herzegovina": "ubuy.ba", "Cyprus": "ubuy.cy", "Fiji": "ubuy.com.fj", "Monaco": "ubuy.mc", "French Guiana": "ubuy.gf", "Brunei": "ubuy.com.bn", "Montenegro": "ubuys.me", "Timor-Leste": "ubuy.tl", "Malawi": "ubuy.mw", "Peru": "ubuy.pe", "Turkey": "ubuy.com.tr", "Slovenia": "ubuy.si", "Botswana": "ubuy.co.bw", "Namibia": "ubuy.co.na", "Zimbabwe": "ubuy.co.zw", "Guadeloupe": "ubuy.gp", "Niger": "ubuy.ne", "Lesotho": "ubuy.ls", "The Bahamas": "ubuy.bs", "The Gambia": "ubuy.gm", "Turks and Caicos": "ubuy.tc", "Greece": "ubuy.com.gr", "South Korea": "ubuy.kr", "Bangladesh": "ubuy.com.bd", "El Salvador": "ubuy.sv", "Uruguay": "ubuy.uy", "Tanzania": "ubuy.co.tz", "Panama": "ubuy.com.pa", "Reunion": "ubuy.re", "Guatemala": "ubuy.gt", "Tunisia": "ubuy.tn", "Maldives": "ubuy.mv", "Macao": "macao.ubuy.com"
}


# --- Search Functionality for Dropdown ---
search_state = {"buffer": "", "last_time": 0}

def handle_combobox_search_main(event):
    # Ignore keys if user is typing inside the property entry or cookie boxes
    if isinstance(event.widget, (tk.Entry, tk.Text, scrolledtext.ScrolledText, ttk.Entry)):
        return

    if not event.char or not event.char.isprintable() or event.keysym in ("Return", "Tab", "Escape"):
        return

    current_time = time.time()
    
    # Reset if more than 1.5 seconds passed
    if current_time - search_state["last_time"] > 1.5:
        search_state["buffer"] = ""
            
    search_state["last_time"] = current_time
    search_state["buffer"] += event.char.lower()

    # Find match
    match_idx = -1
    # Prefix match first
    for idx, c in enumerate(countries_list):
        if c.lower().startswith(search_state["buffer"]):
            match_idx = idx
            break
    
    # Substring match if no prefix match
    if match_idx == -1:
        for idx, c in enumerate(countries_list):
            if search_state["buffer"] in c.lower():
                match_idx = idx
                break

    if match_idx != -1:
        country_dropdown.current(match_idx)
        check_country_sa_main()
        
        # Sync the open Tcl dropdown listbox
        try:
            popdown = root.tk.call('ttk::combobox::PopdownWindow', country_dropdown)
            if popdown:
                lb = popdown + '.f.l'
                root.tk.call(lb, 'selection', 'clear', 0, 'end')
                root.tk.call(lb, 'selection', 'set', match_idx)
                root.tk.call(lb, 'activate', match_idx)
                root.tk.call(lb, 'see', match_idx)
        except Exception:
            pass

root.bind_all('<Key>', handle_combobox_search_main)


# Status Frame for DB status
status_frame = tk.Frame(card_creds, bg=CARD_BG)
status_frame.pack(fill="x", pady=(5, 0))

db_label_text = "DB Online" if DB_STATUS else "DB Offline"
db_label_fg = "#4CAF50" if DB_STATUS else "#E50914"
tk.Label(status_frame, text=f"• {db_label_text}", font=("Segoe UI", 9, "bold"), bg=CARD_BG, fg=db_label_fg).pack(side="right")


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

# 3. Session Manager Card
card_cookies = ttk.LabelFrame(left_col, text=" 🔐 Login Session Manager ", padding=10)
card_cookies.pack(fill="x", pady=(0, 5))

tk.Label(card_cookies, text="Use this manager to log in once per country region.\nYour session will be saved automatically.", 
         bg=CARD_BG, fg=SUBTEXT_COLOR, font=("Segoe UI", 9, "italic"), justify="left").pack(pady=(0, 10))

# --- Login Session Manager Button ---
btn_login_manager = tk.Button(
    card_cookies, 
    text="🔓 OPEN SESSION MANAGER (LOGIN ONCE)", 
    command=lambda: threading.Thread(target=open_session_manager, args=(lambda m, l="info": global_log(m, l),), daemon=True).start(),
    bg="#4CAF50", 
    fg="white", 
    font=("Segoe UI", 10, "bold"), 
    relief="flat", 
    padx=10, 
    pady=10,
    cursor="hand2"
)
btn_login_manager.pack(fill="x", padx=5, pady=10)

tk.Label(card_cookies, text="Note: Use Session Manager to avoid pasting cookies.", bg=CARD_BG, fg=SUBTEXT_COLOR, font=("Segoe UI", 8, "italic")).pack()


# === RIGHT COLUMN: STATUS & ACTION ===
right_col = ttk.Frame(grid_frame)
right_col.grid(row=0, column=1, sticky="nsew")

# 1. Action Button
start_btn = PulsingButton(
    right_col, 
    text="START EXECUTION ►", 
    command=lambda: run_bot(
        property_entry,
        progressbar,
        logbox,
        start_btn,
        current_sa_info_holder["sa_info"],
        chk_bing_resubmit,
        chk_gse_fetch,
        chk_gsc_resubmit,
        chk_gsc_inner_validation,
    )
)
# Start in Normal state
start_btn.config(state=tk.NORMAL, text="START EXECUTION ►", bg="#4CAF50")
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

def global_log_write(msg, tag=None):
    logbox.config(state=tk.NORMAL)
    lines = int(logbox.index("end-1c").split(".")[0])
    if lines > 3000:  
        logbox.delete("1.0", "500.0")
    logbox.insert(tk.END, msg + "\n", tag)
    logbox.see(tk.END)
    logbox.config(state=tk.DISABLED)

def global_log(msg, level="info"):
    timestamp = datetime.datetime.now().strftime("[%H:%M:%S] ")
    tag = {"info": "info", "success": "success", "error": "error", "warn": "warn", "header": "header"}.get(level, "info")
    root.after(0, lambda: global_log_write(timestamp, "time"))
    root.after(0, lambda: global_log_write(msg, tag))

# Initialize GIF
load_gif(resource_path("ubuy.gif"), gif_label, 50)

# Start Fade-in
root.after(100, lambda: fade_in(root))

# Start Typewriter
root.after(500, lambda: type_writer(lbl_main_title, "SEO Automation V6.0"))

# Initialize Firebase Remote (in background)
def init_remote():
    time.sleep(2) # Wait for UI
    remote_ctrl.initialize(global_log)

threading.Thread(target=init_remote, daemon=True).start()

root.mainloop()
