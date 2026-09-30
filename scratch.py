from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager
from selenium.webdriver.chrome.options import Options

import os
import traceback

appdata = os.getenv('APPDATA')
session_dir = os.path.join(appdata, "UbuySEOAutomation", "sessions", "Niger", "google")
print("Session dir:", session_dir)

try:
    chrome_service = Service(ChromeDriverManager().install())
    options = Options()
    options.add_argument(f"--user-data-dir={session_dir}")
    options.add_argument(f"--profile-directory=Default")
    
    driver = webdriver.Chrome(service=chrome_service, options=options)
    print("Success!")
    driver.quit()
except Exception as e:
    traceback.print_exc()
