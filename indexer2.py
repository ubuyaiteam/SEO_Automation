import csv
import json
import time
import os
import sys
import webbrowser
import tempfile
import shutil
import threading
import tkinter as tk
from tkinter import filedialog, scrolledtext, messagebox, ttk

import requests
from pymongo.mongo_client import MongoClient
from pymongo.server_api import ServerApi
from google.oauth2 import service_account
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

import certifi

# Local JSON Storage Setup
DATA_FILE = "service_accounts_data.json"

def load_local_sa():
    """Load all service accounts from the local JSON file."""
    if not os.path.exists(DATA_FILE):
        return {}
    try:
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print(f"Error loading local SA data: {e}")
        return {}

def save_local_sa(data):
    """Save all service accounts to the local JSON file."""
    try:
        with open(DATA_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=4)
        return True
    except Exception as e:
        print(f"Error saving local SA data: {e}")
        return False

# Initialize status
DB_STATUS = True 

def resource_path(relative_path):
    """ Get absolute path to resource, works for dev and for PyInstaller """
    try:
        # PyInstaller creates a temp folder and stores path in _MEIPASS
        base_path = sys._MEIPASS
    except Exception:
        base_path = os.path.abspath(".")
    return os.path.join(base_path, relative_path)

ENDPOINT = "https://indexing.googleapis.com/v3/urlNotifications:publish"
SCOPES = [
    "https://www.googleapis.com/auth/indexing",
    "https://www.googleapis.com/auth/webmasters.readonly"
]

def run_automation(csv_file, credentials_info, summary_callback=None):
    print("Initializing Google Indexing API automation...")

    try:
        credentials = service_account.Credentials.from_service_account_info(
            credentials_info, scopes=SCOPES
        )
        print("Service Account credentials loaded successfully from MongoDB.")
    except Exception as e:
        print(f"Error loading Service Account JSON info: {e}")
        return

    if not os.path.exists(csv_file):
         print(f"Error: {csv_file} not found.")
         return
         
    try:
        with open(csv_file, mode='r', encoding='utf-8-sig') as file:
            reader = csv.DictReader(file)
            fieldnames = reader.fieldnames
            if not fieldnames:
                print("Error: CSV file is empty or invalid.")
                return
            if "Summary" not in fieldnames:
                fieldnames.append("Summary")
            if "Date" not in fieldnames:
                fieldnames.append("Date")
            csv_rows = list(reader)
    except Exception as e:
        print(f"Error reading CSV: {e}")
        return

    print(f"Found {len(csv_rows)} rows to process from {os.path.basename(csv_file)}.")

    if summary_callback:
        urls_list = [(idx, row.get("Original Url")) for idx, row in enumerate(csv_rows) if row.get("Original Url")]
        summary_callback("INIT", urls_list)

    # Initialize a generic requests Session
    session = requests.Session()

    try:
        # Create an auth request object just to refresh token
        auth_req = Request()
    except Exception as e:
        print(f"Error preparing auth request: {e}")
        return

    for index, row in enumerate(csv_rows):
        url = row.get("Original Url")
        if not url:
            continue
            
        if summary_callback: summary_callback("UPDATE", (index, "Processing..."))
            
        print(f"\n[{index + 1}/{len(csv_rows)}] Requesting Indexing for: {url}")
        
        try:
            # Refresh the access token before requests 
            # (credentials.refresh will only make a network request if the token is expired)
            credentials.refresh(auth_req)
            access_token = credentials.token
        except Exception as e:
            msg = f"Failed to get/refresh access token: {e}"
            print(msg)
            row["Summary"] = "Auth Error - Check Key"
            if summary_callback: summary_callback("UPDATE", (index, row["Summary"]))
            continue

        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {access_token}"
        }

        payload = {
            "url": url,
            "type": "URL_UPDATED"
        }

        try:
            response = session.post(ENDPOINT, headers=headers, json=payload, timeout=15)
            
            if response.status_code == 200:
                print(f"Success! '{url}' indexing requested.")
                row["Summary"] = "Indexing requested successfully"
            elif response.status_code == 429:
                print("Warning: Quota exceeded or Rate limited.")
                row["Summary"] = "Quota exceeded (429)"
            elif response.status_code == 403:
                print("Error: Permission denied. Ensure Service Account is added as Owner in Search Console.")
                row["Summary"] = "Permission Denied (403)"
            elif response.status_code == 404:
                print("Error: 404 Not Found. Google API endpoint issue.")
                row["Summary"] = "Endpoint Not Found (404)"
            else:
                try:
                    resp_json = response.json()
                    err_msg = resp_json.get("error", {}).get("message", "Unknown error")
                except:
                    err_msg = response.text
                print(f"Failed with status {response.status_code}: {err_msg}")
                row["Summary"] = f"Error {response.status_code}"

        except requests.exceptions.RequestException as e:
            print(f"Network request error: {e}")
            row["Summary"] = "Network error"

        row["Date"] = time.strftime('%Y-%m-%d %H:%M:%S')
        if summary_callback: summary_callback("UPDATE", (index, row["Summary"]))
        
        # Save progress back to CSV
        try:
            with open(csv_file, mode='w', encoding='utf-8-sig', newline='') as f_out:
                writer = csv.DictWriter(f_out, fieldnames=fieldnames)
                writer.writeheader()
                writer.writerows(csv_rows)
        except Exception as e:
            print(f"Warning: Could not save progress to CSV due to lock/permission: {e}")

        # Adding a small delay to be safe and avoid hammering the API
        delay = 1
        print(f"Waiting {delay} second(s) before next URL...")
        time.sleep(delay)

    print("\n--- Automation Finished ---")


def run_inspection(csv_file, credentials_info, site_url, summary_callback=None):
    print(f"Initializing Google Search Console Inspection API for Site: {site_url}")

    try:
        credentials = service_account.Credentials.from_service_account_info(
            credentials_info, scopes=SCOPES
        )
        service = build("searchconsole", "v1", credentials=credentials)
        print("Service Account connected to GSC API securely.")
    except Exception as e:
        print(f"Error authenticating GSC client: {e}")
        return

    if not os.path.exists(csv_file):
         print(f"Error: {csv_file} not found.")
         return
         
    try:
        with open(csv_file, mode='r', encoding='utf-8-sig') as file:
            reader = csv.DictReader(file)
            fieldnames = reader.fieldnames
            if not fieldnames:
                print("Error: CSV file is empty or invalid.")
                return
            for col in ["Status", "Coverage State", "Crawl Time", "Summary", "Date"]:
                if col not in fieldnames:
                    fieldnames.append(col)
            csv_rows = list(reader)
    except Exception as e:
        print(f"Error reading CSV: {e}")
        return

    print(f"Found {len(csv_rows)} rows to inspect from {os.path.basename(csv_file)}.")

    if summary_callback:
        urls_list = [(idx, row.get("Original Url")) for idx, row in enumerate(csv_rows) if row.get("Original Url")]
        summary_callback("INIT", urls_list)

    delay = 1
    
    for index, row in enumerate(csv_rows):
        url = row.get("Original Url")
        if not url:
            continue
            
        if summary_callback: summary_callback("UPDATE", (index, "Inspecting..."))
        print(f"\n[{index + 1}/{len(csv_rows)}] Inspecting: {url}")
        
        def do_inspect():
            try:
                result = service.urlInspection().index().inspect(
                    body={"inspectionUrl": url, "siteUrl": site_url}
                ).execute()
                inspection   = result.get("inspectionResult", {})
                index_status = inspection.get("indexStatusResult", {})
                verdict      = index_status.get("verdict", "UNKNOWN")
                status = "INDEXED" if verdict == "PASS" else "NOT INDEXED" if verdict in ("FAIL", "NEUTRAL") else "UNKNOWN"
                return {
                    "status": status,
                    "coverage_state": index_status.get("coverageState", "Unknown"),
                    "crawl_time": index_status.get("lastCrawlTime", "")
                }
            except HttpError as e:
                if e.resp.status == 429:
                    print("Quota exceeded - waiting 60 seconds...")
                    return {"status": "RETRY", "coverage_state": "", "crawl_time": ""}
                print(f"HTTP Error {e.resp.status}: {e}")
                return {"status": f"ERROR:{e.resp.status}", "coverage_state": "", "crawl_time": ""}
            except Exception as e:
                print(f"Unexpected error: {e}")
                return {"status": "ERROR", "coverage_state": "", "crawl_time": ""}

        res = do_inspect()
        if res["status"] == "RETRY":
            time.sleep(60)
            res = do_inspect()
            
        row["Status"] = res["status"]
        row["Coverage State"] = res["coverage_state"]
        row["Crawl Time"] = res["crawl_time"]
        
        disp_summary = f"{res['status']} | {res['coverage_state']}"
        row["Summary"] = disp_summary
        row["Date"] = time.strftime('%Y-%m-%d %H:%M:%S')
        
        if summary_callback: summary_callback("UPDATE", (index, disp_summary))
        
        try:
            with open(csv_file, mode='w', encoding='utf-8-sig', newline='') as f_out:
                writer = csv.DictWriter(f_out, fieldnames=fieldnames)
                writer.writeheader()
                writer.writerows(csv_rows)
        except Exception as e:
            print(f"Warning: Could not save progress to CSV: {e}")

        print(f"Waiting {delay} second(s)...")
        time.sleep(delay)

    print("\n--- Inspection Finished ---")


class ThreadSafeConsole:
    def __init__(self, text_widget):
        self.text_widget = text_widget

    def write(self, text):
        self.text_widget.after(0, self._write, text)

    def _write(self, text):
        self.text_widget.configure(state='normal')
        self.text_widget.insert(tk.END, text)
        self.text_widget.see(tk.END)
        self.text_widget.configure(state='disabled')

    def flush(self):
        pass

class AnimatedButton(tk.Button):
    def __init__(self, master, bg_col, hover_col, **kwargs):
        super().__init__(master, bg=bg_col, activebackground=hover_col, relief="flat", cursor="hand2", bd=0, **kwargs)
        self.bg_col = bg_col
        self.hover_col = hover_col
        self.bind("<Enter>", self.on_hover)
        self.bind("<Leave>", self.on_leave)

    def on_hover(self, e):
        self._animate(self.bg_col, self.hover_col, 0)

    def on_leave(self, e):
        self._animate(self.hover_col, self.bg_col, 0)

    def _animate(self, start_color, end_color, step):
        if step > 5:
            self.config(bg=end_color)
            return

        r1, g1, b1 = int(start_color[1:3], 16), int(start_color[3:5], 16), int(start_color[5:7], 16)
        r2, g2, b2 = int(end_color[1:3], 16), int(end_color[3:5], 16), int(end_color[5:7], 16)

        factor = step / 5.0
        r = int(r1 + (r2 - r1) * factor)
        g = int(g1 + (g2 - g1) * factor)
        b = int(b1 + (b2 - b1) * factor)

        color = f"#{max(0, min(255, r)):02x}{max(0, min(255, g)):02x}{max(0, min(255, b)):02x}"
        self.config(bg=color)
        self.after(15, lambda: self._animate(start_color, end_color, step + 1))


class IndexerApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Ubuy Google Indexer")
        self.root.geometry("850x700")
        
        # Netflix UI Theme Colors
        self.bg_color = "#141414"
        self.card_color = "#222222"
        self.text_color = "#FFFFFF"
        self.accent_red = "#E50914"
        self.hover_red = "#f40612"
        self.accent_dark = "#333333"
        self.hover_dark = "#4f4f4f"
        
        self.root.configure(bg=self.bg_color)

        self.style = ttk.Style()
        try:
            self.style.theme_use('clam')
        except:
            pass
        self.style.configure("Card.TFrame", background=self.card_color)
        self.style.configure("Treeview", rowheight=25, font=("Helvetica", 9), background=self.card_color, foreground=self.text_color, fieldbackground=self.card_color)
        self.style.configure("Treeview.Heading", font=("Helvetica", 10, "bold"), background="#333333", foreground=self.text_color)
        self.style.configure("TButton", font=("Helvetica", 10), padding=5, background=self.accent_dark, foreground=self.text_color)
        self.style.configure("TRadiobutton", background=self.card_color, foreground=self.text_color, font=("Helvetica", 10))
        self.style.map("TRadiobutton", background=[('active', self.card_color)])

        self.csv_file_path = None
        self.is_running = False
        self.current_sa_info = None

        self.create_widgets()

    def create_widgets(self):
        # Header
        header_frame = tk.Frame(self.root, bg=self.bg_color)
        header_frame.pack(fill='x', pady=(15, 5))
        
        title_lbl = tk.Label(header_frame, text="Ubuy Google Indexing Automator", font=("Helvetica", 24, "bold"), bg=self.bg_color, fg=self.accent_red)
        title_lbl.pack()

        # Main Container
        main_container = tk.Frame(self.root, bg=self.bg_color)
        main_container.pack(fill='both', expand=True, padx=30, pady=5)

        # --- Section 1: Input URLs ---
        input_card = tk.Frame(main_container, bg=self.card_color, bd=0)
        input_card.pack(fill='x', pady=8)
        
        tk.Label(input_card, text="1. Input URLs:", font=("Helvetica", 11, "bold"), bg=self.card_color, fg=self.text_color).pack(anchor='w', padx=20, pady=(15, 5))

        # Paste URLs Frame
        self.paste_frame = tk.Frame(input_card, bg=self.card_color)
        self.paste_frame.pack(fill='x', pady=(0, 10))
        
        tk.Label(self.paste_frame, text="Paste URLs (max 200/day, one per line):", font=("Helvetica", 10), bg=self.card_color, fg=self.text_color).pack(anchor='w', padx=20)
        self.urls_text = scrolledtext.ScrolledText(self.paste_frame, height=5, font=("Consolas", 10), bg="#333333", fg=self.text_color, insertbackground="white", bd=0, padx=5, pady=5)
        self.urls_text.pack(fill='x', padx=20, pady=(5, 15))

        self.tree_frame = tk.Frame(self.paste_frame, bg=self.card_color)
        tree_scroll = ttk.Scrollbar(self.tree_frame)
        tree_scroll.pack(side='right', fill='y')

        columns = ("URL", "Summary")
        self.tree = ttk.Treeview(self.tree_frame, columns=columns, show="headings", height=5, yscrollcommand=tree_scroll.set)
        self.tree.heading("URL", text="URL")
        self.tree.heading("Summary", text="Summary")
        self.tree.column("URL", width=500, anchor='w')
        self.tree.column("Summary", width=250, anchor='w')
        self.tree.pack(side='left', fill='both', expand=True, padx=(20, 0), pady=(0, 15))
        tree_scroll.config(command=self.tree.yview) 

        # --- Section 2: Country Selection & MongoDB ---
        sa_card = tk.Frame(main_container, bg=self.card_color, bd=0)
        sa_card.pack(fill='x', pady=8)

        tk.Label(sa_card, text="2. Select Country & Manage Service Account:", font=("Helvetica", 11, "bold"), bg=self.card_color, fg=self.text_color).pack(anchor='w', padx=20, pady=(15, 5))
        
        # Row 1: Dropdown
        country_frame = tk.Frame(sa_card, bg=self.card_color)
        country_frame.pack(fill='x', padx=20, pady=(5, 10))
        
        tk.Label(country_frame, text="Country:", font=("Helvetica", 10), bg=self.card_color, fg=self.text_color).pack(side='left')
        
        self.country_var = tk.StringVar(value="Select Country")
        # --- Country to Domain Mapping ---
        self.COUNTRY_DOMAINS = {
            "Italy": "ubuy.co.it", "Georgia": "ubuy.ge", "Colombia": "ubuy.com.co", "Algeria": "ubuy.dz", "Albania": "ubuy.al", "Réunion": "ubuy.re", "Greenland": "ubuy.gl", "French Polynesia": "ubuy.pf", "Bhutan": "ubuy.bt", "Aruba": "ubuy.aw", "Wallis and Futuna Islands": "ubuy.wf", "Antigua and Barbuda": "ubuy.com.ag", "Oman": "ubuy.com.om", "Denmark": "ubuy.dk", "Norway": "ubuy.co.no", "Sri Lanka": "ubuy.com.lk", "Poland": "ubuy.com.pl", "Armenia": "ubuy.co.am", "Zambia": "ubuy.com.zm", "Kyrgyzstan": "ubuy.kg", "Libya": "ubuy.com.ly", "Montserrat": "ubuy.ms", "Saint Kitts and Nevis": "ubuy.kn", "Sierra Leone": "ubuy.sl", "Kenya": "ubuy.ke", "Bulgaria": "ubuy.bg", "Serbia": "ubuy.rs", "Rwanda": "ubuy.rw", "Cote d'Ivoire": "ubuy.ci", "Togo": "ubuy.tg", "Honduras": "ubuy.hn", "Jamaica": "ubuy.com.jm", "Micronesia": "ubuy.fm", "Mongolia": "ubuy.mn", "Turkmenistan": "ubuy.tm", "Benin": "ubuy.bj", "Japan": "u-buy.jp", "Finland": "ubuy.fi", "Switzerland": "u-buy.ch", "Philippines": "ubuy.com.ph", "Thailand": "ubuy.co.th", "Myanmar": "ubuy.com.mm", "Bolivia": "ubuy.com.bo", "Comoros": "comoros.ubuy.com", "Falkland Islands": "falkand.ubuy.com", "Grenada": "ubuy.gd", "Guinea": "guinea.ubuy.com", "Curacao": "ubuy.com.cw", "Croatia": "ubuy.hr", "Pakistan": "ubuy.com.pk", "Saudi": "ubuy.com.sa", "Uk": "u-buy.co.uk", "Netherlands": "ubuy.co.nl", "Nepal": "nepal.ubuy.com", "Azerbaijan": "ubuy.az", "Guinea-Bissau": "ubuy.gw", "Tonga": "ubuy.to", "Western Samoa": "ubuy.ws", "Anguilla": "ubuy.ai", "Mali": "ubuy.ml", "Angola": "ubuy.co.ao", "Austria": "ubuy.co.at", "Latvia": "ubuy.lv", "Moldova": "ubuy.md", "Paraguay": "ubuy.com.py", "New Caledonia": "caledonia.ubuy.com", "Martinique": "ubuy.mq", "Nicaragua": "ubuy.com.ni", "Vanuatu": "ubuy.vu", "Central African Republic": "ubuy.cf", "Cook Islands": "ubuy.co.ck", "Gabon": "ubuy.ga", "Czech Republic": "ubuy.cz", "Uganda": "ubuy.ug", "Iceland": "ubuy.is", "Mexico": "ubuy.com.mx", "Kuwait": "a.ubuy.com.kw", "Mozambique": "ubuy.co.mz", "Ethiopia": "ubuy.et", "Guyana": "ubuy.gy", "Haiti": "ubuy.ht", "Isle of Man": "ubuy.im", "Kiribati": "ubuy.com.ki", "La Laos": "ubuy.la", "Romania": "ubuy.com.ro", "Taiwan": "u-buy.com.tw", "Kazakhstan": "ubuy.com.kz", "NewZealand": "u-buy.co.nz", "Ireland": "ubuy.ie", "Cambodia": "ubuy.com.kh", "Madagascar": "ubuy.mg", "Dominican Republic": "ubuy.do", "Equatorial Guinea": "ubuy.gq", "Guernsey": "ubuy.gg", "Djibouti": "ubuy.dj", "Liechtenstein": "ubuy.li", "Sweden": "ubuy.com.se", "Lebanon": "ubuy.com.lb", "Ghana": "ubuy.com.gh", "Morocco": "ubuy.ma", "Chile": "ubuy.cl", "Cameroon": "ubuy.cm", "Senegal": "ubuy.sn", "Faroe Islands": "ubuy.fo", "Burkina Faso": "ubuy.bf", "Burundi": "ubuy.bi", "Cape Verde": "ubuy.com.cv", "Cayman Islands": "u-buy.ky", "Belgium": "u-buy.be", "jordan": "ubuy.com.jo", "Spain": "ubuy.com.es", "Hong Kong": "ubuy.hk", "Mauritius": "ubuy.mu", "Slovakia": "ubuy.sk", "Saint Lucia": "ubuy.lc", "Saint Pierre and Miquelon": "ubuy.pm", "Saint Vincent": "ubuy.com.vc", "San Marino": "ubuy.sm", "Sint Maarten": "ubuy.sx", "Bahrain": "ubuy.com.bh", "Uzbekistan": "ubuy.uz", "Seychelles": "ubuy.sc", "Ecuador": "ubuy.ec", "Vietnam": "ubuy.vn", "Argentina": "ubuy.com.ar", "Costa Rica": "ubuy.cr", "Luxembourg": "ubuy.lu", "Mauritania": "ubuy.mr", "Barbados": "barbabos.ubuy.com", "Bermuda": "bermuda.ubuy.com", "Dominica": "dominica.ubuy.com", "Malaysia": "ubuy.com.my", "Hungary": "ubuy.hu", "Portugal": "ubuy.com.pt", "Qatar": "ubuy.qa", "France": "ubuy.fr", "Trinidad and Tobago": "ubuy.tt", "Tajikistan": "ubuy.tj", "Puerto Rico": "ubuy.com.pr ", "Belize": "ubuy.com.bz", "Jersey": "ubuy.je", "Aland Islands": "ubuy.ax", "India": "ubuy.co.in", "Australia": "u-buy.com.au", "UAE": "ubuy.ae", "SouthAfrica": "ubuy.co.za", "Canada": "ubuy.ca", "North Macedonia": "ubuy.mk", "Malta": "ubuy.mt", "Palau": "ubuy.pw", "Palestine": "ubuy.com.ps", "Chad": "ubuy.td", "Solomon Islands": "ubuy.com.sb", "Nigeria": "u-buy.com.ng", "Indonesia": "ubuy.co.id", "Egypt": "ubuy.com.eg", "Brazil": "ubuy.com.br", "Germany": "ubuy.de.com", "Kosovo": "kosovo.ubuy.com", "Lithuania": "ubuy.lt", "Suriname": "ubuy.sr", "Nauru": "ubuy.com.nr", "Republic of the Congo": "ubuy.cg", "Saint Helena": "ubuy.sh", "Tuvalu": "u-buy.tv", "Estonia": "ubuy.ee", "Singapore": "ubuy.com.sg", "Iraq": "ubuy.iq", "Bosnia and Herzegovina": "ubuy.ba", "Cyprus": "ubuy.cy", "Fiji": "ubuy.com.fj", "Monaco": "ubuy.mc", "French Guiana": "ubuy.gf", "Brunei": "ubuy.com.bn", "Montenegro": "ubuys.me", "Timor-Leste": "ubuy.tl", "Malawi": "ubuy.mw", "Peru": "ubuy.pe", "Turkey": "ubuy.com.tr", "Slovenia": "ubuy.si", "Botswana": "ubuy.co.bw", "Namibia": "ubuy.co.na", "Zimbabwe": "ubuy.co.zw", "Guadeloupe": "ubuy.gp", "Niger": "ubuy.ne", "Lesotho": "ubuy.ls", "The Bahamas": "ubuy.bs", "The Gambia": "ubuy.gm", "Turks and Caicos": "ubuy.tc", "Greece": "ubuy.com.gr", "South Korea": "ubuy.kr", "Bangladesh": "ubuy.com.bd", "El Salvador": "ubuy.sv", "Uruguay": "ubuy.uy", "Tanzania": "ubuy.co.tz", "Panama": "ubuy.com.pa", "Reunion": "ubuy.re", "Guatemala": "ubuy.gt", "Tunisia": "ubuy.tn", "Maldives": "ubuy.mv", "Macao": "macao.ubuy.com"
        }
        self.countries = sorted(list(self.COUNTRY_DOMAINS.keys()))
        
        self.country_dropdown = ttk.Combobox(country_frame, textvariable=self.country_var, values=self.countries, state="readonly", font=("Helvetica", 10), width=20)
        self.country_dropdown.pack(side='left', padx=10)
        self.country_dropdown.bind("<<ComboboxSelected>>", self.check_country_sa)
        self.root.bind_all('<Key>', self.handle_combobox_search)

        # Connect indicator
        db_label_text = "Database Online" if DB_STATUS else "Database Offline"
        db_label_fg = "#4CAF50" if DB_STATUS else "#E50914"
        tk.Label(country_frame, text=f"• {db_label_text}", font=("Helvetica", 10, "bold"), bg=self.card_color, fg=db_label_fg).pack(side='right')

        # Row 2: Status & Upload
        upload_frame = tk.Frame(sa_card, bg=self.card_color)
        upload_frame.pack(fill='x', padx=20, pady=(0, 15))

        self.sa_btn = AnimatedButton(upload_frame, bg_col=self.accent_dark, hover_col=self.hover_dark, text="Upload JSON to DB", command=self.upload_sa, fg=self.text_color, font=("Helvetica", 10), padx=10, pady=2)
        self.sa_btn.pack(side='left')
        
        if not DB_STATUS:
            self.sa_btn.config(state="disabled")

        self.sa_status_label = tk.Label(upload_frame, text="Status: Select a country", fg="#b3b3b3", bg=self.card_color, font=("Helvetica", 10, "italic"))
        self.sa_status_label.pack(side='left', fill='x', expand=True, padx=20)

        # Row 3: Site URL
        site_frame = tk.Frame(sa_card, bg=self.card_color)
        site_frame.pack(fill='x', padx=20, pady=(0, 15))
        tk.Label(site_frame, text="Search Console Domain (e.g. ubuy.com.gh): sc-domain:", font=("Helvetica", 10), bg=self.card_color, fg=self.text_color).pack(side='left')
        self.site_url_var = tk.StringVar()
        tk.Entry(site_frame, textvariable=self.site_url_var, font=("Helvetica", 10), width=30, bg="#333333", fg="white", insertbackground="white").pack(side='left', padx=10)

        # --- Section 3: Actions ---
        btn_frame = tk.Frame(main_container, bg=self.bg_color)
        btn_frame.pack(pady=15)
        
        self.start_btn = AnimatedButton(btn_frame, bg_col=self.accent_red, hover_col=self.hover_red, text="▶ Start Automation", font=("Helvetica", 12, "bold"), fg="white", command=self.start_automation, width=19, height=2)
        self.start_btn.pack(side='left', padx=5)

        self.inspect_btn = AnimatedButton(btn_frame, bg_col="#0071eb", hover_col="#005bbf", text="🔍 Check Index", font=("Helvetica", 11, "bold"), fg="white", command=self.start_inspection, width=15, height=2)
        self.inspect_btn.pack(side='left', padx=5)
        
        self.download_btn = AnimatedButton(btn_frame, bg_col=self.accent_dark, hover_col=self.hover_dark, text="⬇ Download CSV", font=("Helvetica", 11, "bold"), fg="white", command=self.download_csv, height=2)

        # --- Section 4: Logs ---
        log_frame = tk.Frame(main_container, bg=self.bg_color)
        log_frame.pack(fill='both', expand=True)

        tk.Label(log_frame, text="Event Logs:", font=("Helvetica", 11, "bold"), bg=self.bg_color, fg=self.text_color).pack(anchor='w')

        self.log_text = scrolledtext.ScrolledText(log_frame, state='disabled', height=7, bg="#000000", fg="#b3b3b3", font=("Consolas", 10), insertbackground="white", bd=0, padx=8, pady=8)
        self.log_text.pack(fill='both', expand=True, pady=5)

        self.console = ThreadSafeConsole(self.log_text)
        sys.stdout = self.console
        sys.stderr = self.console

    def handle_combobox_search(self, event):
        # Ignore keys if user is typing inside the Search Console URL box, or the Paste URLs text box
        if isinstance(event.widget, (tk.Entry, tk.Text, scrolledtext.ScrolledText)):
            return

        if not event.char or not event.char.isprintable() or event.keysym in ("Return", "Tab", "Escape"):
            return

        import time
        current_time = time.time()
        
        # Reset if more than 1.5 seconds passed
        if current_time - getattr(self, "last_search_time", 0) > 1.5:
            self.search_buffer = ""
            
        self.last_search_time = current_time
        self.search_buffer = getattr(self, "search_buffer", "") + event.char.lower()

        # Find match
        match_idx = -1
        # Prefix match first
        for idx, c in enumerate(self.countries):
            if c.lower().startswith(self.search_buffer):
                match_idx = idx
                break
        
        # Substring match if no prefix match
        if match_idx == -1:
            for idx, c in enumerate(self.countries):
                if self.search_buffer in c.lower():
                    match_idx = idx
                    break

        if match_idx != -1:
            self.country_dropdown.current(match_idx)
            self.check_country_sa()
            
            # Sync the open Tcl dropdown listbox so that hitting Enter grabs our searched item instead of whatever the mouse hovered over
            try:
                popdown = self.root.tk.call('ttk::combobox::PopdownWindow', self.country_dropdown)
                if popdown:
                    lb = popdown + '.f.l'
                    self.root.tk.call(lb, 'selection', 'clear', 0, 'end')
                    self.root.tk.call(lb, 'selection', 'set', match_idx)
                    self.root.tk.call(lb, 'activate', match_idx)
                    self.root.tk.call(lb, 'see', match_idx)
            except tk.TclError:
                pass

    def check_country_sa(self, event=None):
        country = self.country_var.get()
        if country == "Select Country" or not country or country not in self.countries:
            self.sa_status_label.config(text="Status: Waiting for valid country", fg="#b3b3b3")
            self.current_sa_info = None
            return
        
        try:
            # Auto-fill site domain if mapping exists
            if country in self.COUNTRY_DOMAINS:
                self.site_url_var.set(self.COUNTRY_DOMAINS[country])

            data = load_local_sa()
            if country in data:
                self.sa_status_label.config(text=f"Status: Linked (Ready)", fg="#4CAF50") # Green
                self.current_sa_info = data[country]
            else:
                self.sa_status_label.config(text="Status: Not Uploaded", fg=self.accent_red)
                self.current_sa_info = None
        except Exception as e:
            print(f"Local storage error checking country: {e}")
            self.sa_status_label.config(text="Status: Local Data Error", fg=self.accent_red)

    def upload_sa(self):
        country = self.country_var.get()
        if country == "Select Country" or not country or country not in self.countries:
            messagebox.showerror("Error", "Please select a valid country from the dropdown first.")
            return

        file_path = filedialog.askopenfilename(title=f"Select JSON Key for {country}", filetypes=[("JSON Files", "*.json"), ("All Files", "*.*")])
        if file_path:
            try:
                with open(file_path, "r", encoding="utf-8") as f:
                    sa_data = json.load(f)
                
                # Check for validity pattern loosely
                if "project_id" not in sa_data or "private_key" not in sa_data:
                    messagebox.showerror("Invalid File", "Does not appear to be a valid Google Service Account JSON key.")
                    return

                data = load_local_sa()
                data[country] = sa_data
                if save_local_sa(data):
                    messagebox.showinfo("Success", f"Service Account saved locally for {country}!")
                    self.check_country_sa()
                else:
                    messagebox.showerror("Error", "Failed to save to local file.")
            except Exception as e:
                messagebox.showerror("Error", f"Failed to parse and save JSON: {e}")

    def download_csv(self):
        if hasattr(self, 'temp_csv_file') and os.path.exists(self.temp_csv_file):
            save_path = filedialog.asksaveasfilename(defaultextension=".csv", filetypes=[("CSV Files", "*.csv")])
            if save_path:
                try:
                    shutil.copy(self.temp_csv_file, save_path)
                    messagebox.showinfo("Success", "File saved successfully!")
                    self.download_btn.pack_forget()
                except Exception as e:
                    messagebox.showerror("Error", f"Failed to save file: {e}")

    def start_automation(self):
        if self.is_running:
            return
            
        if not DB_STATUS:
            messagebox.showerror("Database Offline", "Cannot proceed. SQLite database is offline.")
            return
            
        if not self.current_sa_info:
            messagebox.showerror("Error", "No Service Account linked for the selected country. Please select a country and upload a JSON.")
            return

        self.download_btn.pack_forget()
        
        urls = self.urls_text.get("1.0", tk.END).strip().split('\n')
        urls = [u.strip() for u in urls if u.strip()]
        if not urls:
            messagebox.showerror("Error", "Please paste at least one URL.")
            return
        if len(urls) > 200:
            messagebox.showerror("Error", f"Maximum 200 URLs allowed per day. You provided {len(urls)}.")
            return
        
        self.temp_csv_file = os.path.join(tempfile.gettempdir(), "temp_auto_indexer_urls_api_2.csv")
        try:
            with open(self.temp_csv_file, mode='w', encoding='utf-8-sig', newline='') as f:
                writer = csv.DictWriter(f, fieldnames=["Original Url", "Summary", "Date"])
                writer.writeheader()
                for url in urls:
                    writer.writerow({"Original Url": url, "Summary": "", "Date": ""})
        except Exception as e:
            messagebox.showerror("Error", f"Failed to create temp file: {e}")
            return
            
        csv_to_process = self.temp_csv_file
        
        # Hide text area and show treeview
        self.urls_text.pack_forget()
        self.tree_frame.pack(fill='both', expand=True, pady=5)

        self.is_running = True
        self.start_btn.config(state='disabled', text="Running...")
        self.inspect_btn.config(state='disabled')

        # Clear log
        self.log_text.configure(state='normal')
        self.log_text.delete("1.0", tk.END)
        self.log_text.configure(state='disabled')

        thread = threading.Thread(target=self.run_thread, args=(csv_to_process, self.current_sa_info, True))
        thread.daemon = True
        thread.start()

    def handle_summary_callback(self, action, data):
        self.root.after(0, self._handle_summary_callback, action, data)

    def _handle_summary_callback(self, action, data):
        if action == "INIT":
            for item in self.tree.get_children():
                self.tree.delete(item)
            for idx, url in data:
                self.tree.insert("", "end", iid=str(idx), values=(url, "Pending..."))
        elif action == "UPDATE":
            idx, summary = data
            iid_str = str(idx)
            if self.tree.exists(iid_str):
                url = self.tree.item(iid_str, "values")[0]
                self.tree.item(iid_str, values=(url, summary))

    def run_thread(self, csv_file, sa_info, show_download):
        try:
            run_automation(csv_file, sa_info, self.handle_summary_callback)
            if show_download:
                self.root.after(0, lambda: self.download_btn.pack(side='left', padx=10))
        except Exception as e:
            print(f"\n[!] Critical error in automation: {e}")
        finally:
            self.is_running = False
            self.root.after(0, lambda: self.start_btn.config(state='normal', text="▶ Start Automation"))
            self.root.after(0, lambda: self.inspect_btn.config(state='normal', text="🔍 Check Index"))
            
            if show_download:
               # Reset UI back to text input so user can paste again if they want
               self.root.after(0, lambda: self.tree_frame.pack_forget())
               self.root.after(0, lambda: self.urls_text.pack(fill='x', pady=5))

    def start_inspection(self):
        if self.is_running:
            return
            
        if not DB_STATUS:
            messagebox.showerror("Database Offline", "Cannot proceed. SQLite database is offline.")
            return
            
        if not self.current_sa_info:
            messagebox.showerror("Error", "No Service Account linked for the selected country.")
            return
            
        domain_input = self.site_url_var.get().strip()
        if not domain_input:
            messagebox.showerror("Error", "Please provide a Domain for the inspection.")
            return
            
        # Automatically prepend sc-domain: if missing
        if domain_input.startswith("sc-domain:"):
            site_url = domain_input
        elif domain_input.startswith("http://") or domain_input.startswith("https://"):
            messagebox.showerror("Error", "Please just provide the domain name (e.g. ubuy.com.gh). I will add sc-domain: for you.")
            return
        else:
            site_url = f"sc-domain:{domain_input}"

        self.download_btn.pack_forget()
        
        urls = self.urls_text.get("1.0", tk.END).strip().split('\n')
        urls = [u.strip() for u in urls if u.strip()]
        if not urls:
            messagebox.showerror("Error", "Please paste at least one URL.")
            return
        if len(urls) > 200:
            messagebox.showerror("Error", f"Maximum 200 URLs allowed per day. You provided {len(urls)}.")
            return
        
        self.temp_csv_file = os.path.join(tempfile.gettempdir(), "temp_auto_indexer_urls_api_2.csv")
        try:
            with open(self.temp_csv_file, mode='w', encoding='utf-8-sig', newline='') as f:
                writer = csv.DictWriter(f, fieldnames=["Original Url", "Summary", "Date"])
                writer.writeheader()
                for url in urls:
                    writer.writerow({"Original Url": url, "Summary": "", "Date": ""})
        except Exception as e:
            messagebox.showerror("Error", f"Failed to create temp file: {e}")
            return
            
        csv_to_process = self.temp_csv_file
        
        self.urls_text.pack_forget()
        self.tree_frame.pack(fill='both', expand=True, pady=5)

        self.is_running = True
        self.inspect_btn.config(state='disabled', text="Running...")
        self.start_btn.config(state='disabled')

        self.log_text.configure(state='normal')
        self.log_text.delete("1.0", tk.END)
        self.log_text.configure(state='disabled')

        thread = threading.Thread(target=self.run_inspection_thread_func, args=(csv_to_process, self.current_sa_info, site_url, True))
        thread.daemon = True
        thread.start()

    def run_inspection_thread_func(self, csv_file, sa_info, site_url, show_download):
        try:
            run_inspection(csv_file, sa_info, site_url, self.handle_summary_callback)
            if show_download:
                self.root.after(0, lambda: self.download_btn.pack(side='left', padx=10))
        except Exception as e:
            print(f"\n[!] Critical error in inspection: {e}")
        finally:
            self.is_running = False
            self.root.after(0, lambda: self.start_btn.config(state='normal', text="▶ Start Automation"))
            self.root.after(0, lambda: self.inspect_btn.config(state='normal', text="🔍 Check Index"))
            
            if show_download:
               self.root.after(0, lambda: self.tree_frame.pack_forget())
               self.root.after(0, lambda: self.urls_text.pack(fill='x', pady=5))

if __name__ == "__main__":
    root = tk.Tk()
    try:
        root.iconbitmap(resource_path('logo.ico'))
    except Exception as e:
        print(f"Failed to load logo.ico (safe to ignore): {e}")

    app = IndexerApp(root)
    print("Welcome to Ubuy Google Indexing API Automator.")
    if DB_STATUS:
        print("MongoDB is Online. Please select a country to get started.")
    else:
        print("ERROR: MongoDB Initialization Failed! Please check your connection string and internet.")
    root.mainloop()
