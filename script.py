import os
import sys
import csv
import json
import time
import urllib.parse
from datetime import datetime

# Import Selenium components
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.common.keys import Keys
from webdriver_manager.chrome import ChromeDriverManager

def print_banner():
    print("=" * 60)
    print("      GOOGLE SEARCH CONSOLE BULK URL REMOVAL TOOL      ")
    print("=" * 60)

def setup_driver(headless=False):
    """Initializes the Selenium Chrome WebDriver with stealth configurations."""
    options = Options()
    if headless:
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
    
    # 🌎 Force English language to ensure text-based XPath selectors match perfectly
    options.add_argument("--lang=en")
    
    prefs = {
        "download.prompt_for_download": False,
        "download.directory_upgrade": True,
        "safebrowsing.enabled": True,
        "intl.accept_languages": "en,en-US"
    }
    options.add_experimental_option("prefs", prefs)
    
    service = Service(ChromeDriverManager().install())
    if sys.platform.startswith("win"):
        # Hide Chrome command prompt window on Windows
        service.creationflags = 0x08000000
        
    driver = webdriver.Chrome(service=service, options=options)
    
    # Bypass simple webdriver detection
    driver.execute_cdp_cmd(
        "Page.addScriptToEvaluateOnNewDocument",
        {"source": "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"}
    )
    return driver

def load_cookies(driver, cookies_input):
    """Loads Google session cookies from a JSON file or direct JSON text string."""
    driver.get("https://www.google.com/")
    time.sleep(2)
    
    try:
        # Check if input is a file path or direct JSON string
        if os.path.exists(cookies_input):
            with open(cookies_input, "r", encoding="utf-8") as f:
                cookies = json.load(f)
        else:
            cookies = json.loads(cookies_input.strip())
    except Exception as e:
        print(f"❌ Error parsing cookie JSON: {e}")
        return False
        
    if not isinstance(cookies, list):
        print("❌ Invalid cookie format: Cookies should be a JSON array of cookie objects.")
        return False
        
    print(f"🔄 Injecting {len(cookies)} cookies into domain '.google.com'...")
    for cookie in cookies:
        # Strip keys that cause Selenium errors
        for key in ["sameSite", "storeId", "id", "hostOnly", "expirationDate"]:
            cookie.pop(key, None)
        cookie["domain"] = ".google.com"
        try:
            driver.add_cookie(cookie)
        except Exception:
            pass
            
    time.sleep(2)
    return True

def check_login_status(driver):
    """Verifies if the injected cookies successfully logged into Google Search Console."""
    driver.get("https://search.google.com/search-console")
    time.sleep(4)
    current_url = driver.current_url.lower()
    
    # Check if we were redirected to accounts login page or GSC landing/about page
    if "accounts.google.com" in current_url or "search-console/about" in current_url or "signin" in current_url:
        return False
    return True


def read_urls_from_csv(csv_path):
    """Reads URLs from a CSV file, auto-detecting the URL column."""
    if not os.path.exists(csv_path):
        print(f"❌ CSV file not found: {csv_path}")
        return []
        
    urls = []
    try:
        with open(csv_path, mode="r", encoding="utf-8-sig") as f:
            reader = csv.reader(f)
            rows = list(reader)
            
        if not rows:
            print("❌ CSV file is empty.")
            return []
            
        # Try to locate URL column index
        headers = [h.strip().lower() for h in rows[0]]
        url_idx = -1
        url_keywords = ["url", "urls", "link", "links", "loc", "location", "address", "page"]
        
        for i, h in enumerate(headers):
            if any(k == h for k in url_keywords):
                url_idx = i
                break
                
        if url_idx == -1:
            for i, h in enumerate(headers):
                if "url" in h or "link" in h:
                    url_idx = i
                    break
                    
        # Check if first cell of first row is a URL (in case there's no header)
        is_header = True
        if url_idx == -1:
            if rows[0][0].strip().lower().startswith(("http://", "https://")):
                url_idx = 0
                is_header = False
            else:
                url_idx = 0
                is_header = True
                
        start_row = 1 if is_header else 0
        for row in rows[start_row:]:
            if not row or len(row) <= url_idx:
                continue
            url = row[url_idx].strip()
            if url:
                if url.lower().startswith(("http://", "https://")):
                    urls.append(url)
                else:
                    print(f"⚠️ Skipping invalid URL: '{url}'")
                    
    except Exception as e:
        print(f"❌ Error reading CSV file: {e}")
        return []
        
    return urls

def infer_resource_id(url):
    """Extracts property domain name to guess GSC resource ID if not specified."""
    try:
        parsed = urllib.parse.urlparse(url)
        netloc = parsed.netloc.lower()
        if netloc.startswith("www."):
            domain = netloc[4:]
        else:
            domain = netloc
        return f"sc-domain:{domain}"
    except Exception:
        return None

def robust_enter_text(driver, element, text):
    """Tries to enter text normally. If that fails or gets stuck, forces it via JavaScript."""
    try:
        element.clear()
    except:
        pass 
    
    try:
        element.send_keys(text)
        time.sleep(0.5) 
        if element.get_attribute("value") == text:
            return True
    except:
        pass

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
        print(f"⚠️ JS Injection failed: {e}")
        return False

def process_removal_request(driver, url, remove_prefix=False):
    """Executes the Selenium steps to submit a removal request for a single URL."""
    try:
        # 1. Click "NEW REQUEST"
        new_request_btn = None
        new_request_xpaths = [
            "//*[text()='New Request' or text()='New request' or text()='NEW REQUEST']",
            "//*[contains(text(), 'New Request') or contains(text(), 'New request') or contains(text(), 'NEW REQUEST')]",
            "//div[@role='button'][contains(., 'New Request') or contains(., 'New request') or contains(., 'NEW REQUEST')]",
            "//button[contains(., 'New Request') or contains(., 'New request') or contains(., 'NEW REQUEST')]",
            "//span[contains(text(), 'New Request') or contains(text(), 'New request')]"
        ]
        for xpath in new_request_xpaths:
            try:
                btn = WebDriverWait(driver, 3).until(EC.element_to_be_clickable((By.XPATH, xpath)))
                try:
                    btn.click()
                except:
                    try:
                        from selenium.webdriver.common.action_chains import ActionChains
                        ActionChains(driver).move_to_element(btn).click().perform()
                    except:
                        driver.execute_script("arguments[0].click();", btn)
                new_request_btn = btn
                break
            except:
                continue
                
        if not new_request_btn:
            return False, "Could not locate or click 'New request' button"
            
        # 2. Wait for the modal dialog to appear (must be visible dialog)
        try:
            dialog = WebDriverWait(driver, 5).until(EC.visibility_of_element_located((By.XPATH, "//div[@role='dialog']")))
        except:
            return False, "Timed out waiting for URL input dialog"
            
        # 3. Enter URL in the input field (XPath queries must start with . to be relative to dialog)
        input_field = None
        input_xpaths = [
            ".//input[not(@type='radio' or @type='checkbox' or @type='hidden' or @type='submit')]",
            ".//input[@type='text' or @type='url']",
            ".//input[@aria-label='URL']",
            ".//input"
        ]
        for xpath in input_xpaths:
            try:
                candidates = dialog.find_elements(By.XPATH, xpath)
                for cand in candidates:
                    tag_type = (cand.get_attribute("type") or "").lower()
                    if tag_type in ["radio", "checkbox", "hidden", "submit"]:
                        continue
                    if cand.is_displayed() and cand.is_enabled():
                        input_field = cand
                        break
                if input_field:
                    break
            except:
                continue
                
        if not input_field:
            # Close dialog using ESC
            driver.find_element(By.TAG_NAME, "body").send_keys(Keys.ESCAPE)
            return False, "Could not find URL input box inside the dialog"
            
        if not robust_enter_text(driver, input_field, url):
            driver.find_element(By.TAG_NAME, "body").send_keys(Keys.ESCAPE)
            return False, "Failed to enter URL in input field"
        
        # 4. Click the appropriate scope option ("Remove this URL only" vs "Remove all URLs with this prefix")
        radio_clicked = False
        target_option = "Remove all URLs with this prefix" if remove_prefix else "Remove this URL only"
        try:
            # Try finding radio by text (using relative XPath)
            option_element = dialog.find_element(
                By.XPATH,
                f".//*[contains(text(), '{target_option}') or contains(text(), 'Remove all URLs starting with this prefix')]" if remove_prefix else f".//*[contains(text(), '{target_option}')]"
            )
            try:
                option_element.click()
            except:
                driver.execute_script("arguments[0].click();", option_element)
            radio_clicked = True
        except:
            pass

        if not radio_clicked:
            try:
                # Fallback to finding input elements or role="radio" (relative XPath)
                radios = dialog.find_elements(By.XPATH, ".//input[@type='radio'] | .//div[@role='radio']")
                if len(radios) >= 2:
                    target_radio = radios[1] if remove_prefix else radios[0]
                    try:
                        target_radio.click()
                    except:
                        driver.execute_script("arguments[0].click();", target_radio)
                    radio_clicked = True
            except Exception as e:
                print(f"⚠️ Could not select radio option: {e}")
                
        # 5. Click "Next" (using relative XPaths)
        next_btn = None
        next_xpaths = [
            ".//*[text()='Next' or text()='NEXT' or text()='next']",
            ".//*[contains(text(), 'Next') or contains(text(), 'NEXT') or contains(text(), 'next')]",
            ".//button[contains(., 'Next') or contains(., 'NEXT') or contains(., 'next')]",
            ".//div[@role='button'][contains(., 'Next') or contains(., 'NEXT') or contains(., 'next')]"
        ]
        for xpath in next_xpaths:
            try:
                btn = dialog.find_element(By.XPATH, xpath)
                next_btn = btn
                break
            except:
                continue
                
        if not next_btn:
            driver.find_element(By.TAG_NAME, "body").send_keys(Keys.ESCAPE)
            return False, "Could not locate 'Next' button inside the dialog"
            
        try:
            next_btn.click()
        except:
            try:
                from selenium.webdriver.common.action_chains import ActionChains
                ActionChains(driver).move_to_element(next_btn).click().perform()
            except:
                driver.execute_script("arguments[0].click();", next_btn)
        
        # 6. Wait for verification screen (or capture errors) (using relative XPaths)
        submit_btn = None
        error_detected = False
        error_msg = ""
        
        submit_xpaths = [
            ".//*[text()='Submit request' or text()='Submit Request' or text()='Submit' or text()='SUBMIT']",
            ".//*[contains(text(), 'Submit request') or contains(text(), 'Submit Request') or contains(text(), 'Submit') or contains(text(), 'SUBMIT')]",
            ".//button[contains(., 'Submit') or contains(., 'SUBMIT')]",
            ".//div[@role='button'][contains(., 'Submit') or contains(., 'SUBMIT')]"
        ]
        
        start_time = time.time()
        while time.time() - start_time < 5:
            # Check if Submit button is present and clickable
            for xpath in submit_xpaths:
                try:
                    btn = dialog.find_element(By.XPATH, xpath)
                    if btn.is_displayed():
                        submit_btn = btn
                        break
                except:
                    continue
            if submit_btn:
                break
                
            # Check if Google returned an error text in the dialog
            dialog_text = dialog.text
            lower_text = dialog_text.lower()
            if any(err in lower_text for err in ["not in the property", "not allowed", "invalid", "error", "already", "pending", "submitted", "بالفعل", "الانتظار", "مسموح", "صالح", "خطأ", "ليس في"]):
                error_detected = True
                error_msg = "Google Error: "
                lines = [line.strip() for line in dialog_text.split("\n") if line.strip()]
                for line in lines:
                    if any(w in line.lower() for w in ["not in", "invalid", "not allowed", "error", "already", "pending", "submitted", "بالفعل", "الانتظار", "مسموح", "صالح", "خطأ", "ليس في"]):
                        error_msg += line
                        break
                if error_msg == "Google Error: ":
                    error_msg += dialog_text.replace("\n", " ")[:100]
                break
                
            time.sleep(0.5)
            
        if error_detected or not submit_btn:
            # Close dialog using ESC
            driver.find_element(By.TAG_NAME, "body").send_keys(Keys.ESCAPE)
            # Ensure it closes
            try:
                WebDriverWait(driver, 3).until(EC.invisibility_of_element_located((By.XPATH, "//div[@role='dialog']")))
            except:
                # Try cancel button (using relative XPath)
                try:
                    cancel_btn = dialog.find_element(By.XPATH, ".//*[text()='Cancel' or text()='CANCEL']")
                    driver.execute_script("arguments[0].click();", cancel_btn)
                except:
                    pass
            return False, error_msg if error_msg else "Timeout waiting for confirmation screen (Submit button)"
            
        # 7. Click "Submit request"
        try:
            try:
                submit_btn.click()
            except:
                try:
                    from selenium.webdriver.common.action_chains import ActionChains
                    ActionChains(driver).move_to_element(submit_btn).click().perform()
                except:
                    driver.execute_script("arguments[0].click();", submit_btn)
        except Exception as e:
            try:
                driver.find_element(By.TAG_NAME, "body").send_keys(Keys.ESCAPE)
            except:
                pass
            return False, f"Failed to click Submit button: {e}"
            
        # 8. Wait for modal to disappear
        try:
            def is_dialog_gone(d):
                try:
                    return not dialog.is_displayed()
                except:
                    return True
            WebDriverWait(driver, 10).until(is_dialog_gone)
            return True, "Success"
        except:
            # Check if there is an error message displayed on the dialog after submission attempt
            dialog_text = ""
            try:
                dialog_text = dialog.text
            except:
                pass
                
            try:
                driver.find_element(By.TAG_NAME, "body").send_keys(Keys.ESCAPE)
            except:
                pass
            try:
                cancel_btn = dialog.find_element(By.XPATH, ".//*[text()='Cancel' or text()='CANCEL']")
                driver.execute_script("arguments[0].click();", cancel_btn)
            except:
                pass
                
            if dialog_text:
                lines = [line.strip() for line in dialog_text.split("\n") if line.strip()]
                for line in lines:
                    if any(w in line.lower() for w in ["error", "fail", "not allowed", "invalid", "wrong", "cannot", "could not", "already", "pending", "submitted", "بالفعل", "الانتظار", "مسموح", "صالح", "خطأ"]):
                        return False, f"Google Error (after submit): {line}"
                return False, f"Submit clicked, but dialog stayed open (Dialog text: {dialog_text.replace(chr(10), ' ')[:100]})"
                
            return False, "Submit clicked, but dialog did not close automatically"
    except Exception as ex:
        try:
            driver.find_element(By.TAG_NAME, "body").send_keys(Keys.ESCAPE)
        except:
            pass
        return False, f"Unexpected error: {str(ex)}"

def main():
    print_banner()
    
    import argparse
    
    # Check if command line arguments are provided
    if len(sys.argv) > 1:
        parser = argparse.ArgumentParser(description="Google Search Console Bulk URL Removal Tool")
        parser.add_argument("--csv", default="urls.csv", help="Path to CSV file containing URLs")
        parser.add_argument("--cookies", default="cookies.json", help="Path to Google cookies JSON file or pasted JSON array string")
        parser.add_argument("--resource-id", help="GSC Resource ID (e.g. sc-domain:u-buy.co.nz)")
        parser.add_argument("--prefix", action="store_true", help="Remove all URLs with this prefix")
        parser.add_argument("--headless", action="store_true", help="Run browser in headless mode")
        parser.add_argument("--delay", type=float, default=3.0, help="Delay between requests in seconds")
        
        args = parser.parse_args()
        csv_path = args.csv
        cookies_input = args.cookies
        resource_id = args.resource_id if args.resource_id else ""
        remove_prefix = args.prefix
        headless = args.headless
        delay = args.delay
        
        print(f"📂 CSV Path: {csv_path}")
        print(f"🍪 Cookies Path: {cookies_input}")
        if resource_id:
            print(f"🔑 Resource ID: {resource_id}")
        print(f"✂️ Remove Prefix: {remove_prefix}")
        print(f"🖥️ Headless: {headless}")
        print(f"⏱️ Delay: {delay}s")
    else:
        # Prompt Configuration
        csv_path = input("📂 Path to CSV file containing URLs [default: urls.csv]: ").strip()
        if not csv_path:
            csv_path = "urls.csv"
            
        cookies_input = input("🍪 Path to Google cookies JSON file (or paste JSON array text) [default: cookies.json]: ").strip()
        if not cookies_input:
            cookies_input = "cookies.json"
            
        resource_id = input("🔑 GSC Resource ID (e.g. sc-domain:u-buy.co.nz) [leave blank to infer from CSV]: ").strip()
        
        remove_prefix_str = input("✂️ Remove all URLs with this prefix? (y/n) [default: n]: ").strip().lower()
        remove_prefix = remove_prefix_str.startswith("y")
        
        headless_str = input("🖥️ Run browser in headless mode? (y/n) [default: n]: ").strip().lower()
        headless = headless_str.startswith("y")
        
        delay_str = input("⏱️ Delay between requests (seconds) [default: 3]: ").strip()
        try:
            delay = float(delay_str) if delay_str else 3.0
        except ValueError:
            delay = 3.0
        
    print("\n🔄 Reading CSV and parsing URLs...")
    urls = read_urls_from_csv(csv_path)
    if not urls:
        print("❌ No valid URLs found in CSV. Exiting.")
        return
        
    print(f"📊 Found {len(urls)} URLs to process.")
    
    if not resource_id:
        resource_id = infer_resource_id(urls[0])
        if not resource_id:
            print("❌ Could not infer Resource ID. Please restart the script and specify it manually.")
            return
            
    print(f"📌 GSC Property/Resource ID: {resource_id}")
    
    print("\n🚀 Launching Selenium browser...")
    driver = None
    try:
        driver = setup_driver(headless=headless)
    except Exception as e:
        print(f"❌ Failed to initialize WebDriver: {e}")
        return
        
    try:
        # Load Google authentication cookies
        if not load_cookies(driver, cookies_input):
            print("❌ Exiting due to cookie loading errors.")
            return
            
        print("🔍 Checking if logged in successfully...")
        if not check_login_status(driver):
            print("⚠️ Login check could not confirm active session (session might be expired or using a secondary account).")
            print("🔄 Attempting to proceed to GSC Removals page anyway...")
        else:
            print("✅ Authenticated successfully!")
        
        # Navigate to GSC removals tool page
        encoded_resource_id = urllib.parse.quote(resource_id)
        removals_url = f"https://search.google.com/search-console/removals?resource_id={encoded_resource_id}&hl=en"
        print(f"🌐 Navigating to GSC Removals page:\n🔗 {removals_url}")
        driver.get(removals_url)
        time.sleep(5)
        
        # Verify page loaded
        try:
            WebDriverWait(driver, 10).until(EC.presence_of_element_located((By.TAG_NAME, "body")))
        except:
            print("❌ Failed to load GSC removals page. Please check internet connection or property ID.")
            return
            
        # Process URL removal requests
        results = []
        success_count = 0
        failure_count = 0
        
        print("\n🏁 Starting removal requests...")
        print("-" * 60)
        
        for i, url in enumerate(urls, 1):
            print(f"[{i}/{len(urls)}] Processing: {url}")
            success, msg = process_removal_request(driver, url, remove_prefix)
            
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            results.append({
                "url": url,
                "status": "Success" if success else "Failed",
                "message": msg,
                "timestamp": timestamp
            })
            
            if success:
                print(f"   ✅ Submitted successfully.")
                success_count += 1
            else:
                print(f"   ❌ Failed: {msg}")
                failure_count += 1
                
            # Inter-request delay
            if i < len(urls):
                time.sleep(delay)
                
        print("-" * 60)
        print("🎉 Bulk URL removal process completed!")
        print(f"   ✅ Successful: {success_count}")
        print(f"   ❌ Failed: {failure_count}")
        
        # Save results to a CSV log file
        results_file = "removal_results.csv"
        try:
            with open(results_file, mode="w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=["url", "status", "message", "timestamp"])
                writer.writeheader()
                writer.writerows(results)
            print(f"📝 Full log of results saved to: {os.path.abspath(results_file)}")
        except Exception as e:
            print(f"⚠️ Failed to save results CSV: {e}")
            
    except Exception as e:
        print(f"\n💥 An unexpected error occurred: {e}")
    finally:
        if driver:
            print("🚪 Closing browser window...")
            driver.quit()

if __name__ == "__main__":
    main()
