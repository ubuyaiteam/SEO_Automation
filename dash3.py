import tkinter as tk
from tkinter import ttk, scrolledtext, messagebox, filedialog
import json
import os
import sys
import time
import threading
import datetime
import shutil
import ctypes

# ----------------------------
# LOCAL STORAGE SETUP (Persistent AppData)
# ----------------------------
DATA_DIR = os.path.join(os.getenv('APPDATA'), 'SEOAutomation')
DATA_FILE_NAME = "service_account_data1.json"
DATA_FILE = os.path.join(DATA_DIR, DATA_FILE_NAME)

def resource_path(filename):
    if hasattr(sys, '_MEIPASS'):
        return os.path.join(sys._MEIPASS, filename)
    return os.path.join(os.path.abspath("."), filename)

def load_local_sa():
    """Load all service accounts from the persistent AppData location."""
    if not os.path.exists(DATA_DIR):
        try:
            os.makedirs(DATA_DIR)
        except:
            return {}

    if not os.path.exists(DATA_FILE) or os.path.getsize(DATA_FILE) == 0:
        return {}

    try:
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print(f"Error loading local SA data: {e}")
        return {}

def save_local_sa(data):
    """Save all service accounts to the persistent AppData location."""
    try:
        if not os.path.exists(DATA_DIR):
            os.makedirs(DATA_DIR)
        with open(DATA_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=4)
        return True
    except Exception as e:
        print(f"Error saving local SA data: {e}")
        return False

# ----------------------------
# DATA & COUNTRIES
# ----------------------------
COUNTRIES_LIST = sorted(["Italy", "Georgia", "Colombia", "Algeria", "Albania", "Réunion", "Greenland", "French Polynesia", "Bhutan", "Aruba", "Wallis and Futuna Islands", "Antigua and Barbuda", "Oman", "Denmark", "Norway", "Sri Lanka", "Poland", "Armenia", "Zambia", "Kyrgyzstan", "Libya", "Montserrat", "Saint Kitts and Nevis", "Sierra Leone", "Kenya", "Bulgaria", "Serbia", "Rwanda", "Cote d'Ivoire", "Togo", "Honduras", "Jamaica", "Micronesia", "Mongolia", "Turkmenistan", "Benin", "Japan", "Finland", "Switzerland", "Philippines", "Thailand", "Myanmar", "Bolivia", "Comoros", "Falkland Islands", "Grenada", "Guinea", "Curacao", "Croatia", "Pakistan", "Saudi", "UK", "Netherlands", "Nepal", "Azerbaijan", "Guinea-Bissau", "Tonga", "Western Samoa", "Anguilla", "Mali", "Angola", "Austria", "Latvia", "Moldova", "Paraguay", "New Caledonia", "Martinique", "Nicaragua", "Vanuatu", "Central African Republic", "Cook Islands", "Gabon", "Czech Republic", "Uganda", "Iceland", "Mexico", "Kuwait", "Mozambique", "Ethiopia", "Guyana", "Haiti", "Isle of Man", "Kiribati", "Laos", "Romania", "Taiwan", "Kazakhstan", "New Zealand", "Ireland", "Cambodia", "Madagascar", "Dominican Republic", "Equatorial Guinea", "Guernsey", "Djibouti", "Liechtenstein", "Sweden", "Lebanon", "Ghana", "Morocco", "Chile", "Cameroon", "Senegal", "Faroe Islands", "Burkina Faso", "Burundi", "Cape Verde", "Cayman Islands", "Belgium", "Jordan", "Spain", "Hong Kong", "Mauritius", "Slovakia", "Saint Lucia", "Saint Pierre and Miquelon", "Saint Vincent", "San Marino", "Sint Maarten", "Uzbekistan", "Seychelles", "Ecuador", "Vietnam", "Argentina", "Costa Rica", "Luxembourg", "Mauritania", "Barbados", "Bermuda", "Dominica", "Malaysia", "Hungary", "Portugal", "Qatar", "France", "Trinidad and Tobago", "Tajikistan", "Puerto Rico", "Belize", "Jersey", "Aland Islands", "India", "Australia", "UAE", "South Africa", "Canada", "North Macedonia", "Malta", "Palau", "Palestine", "Chad", "Solomon Islands", "Nigeria", "Indonesia", "Egypt", "Brazil", "Germany", "Kosovo", "Lithuania", "Suriname", "Nauru", "Republic of the Congo", "Saint Helena", "Tuvalu", "Estonia", "Singapore", "Iraq", "Bosnia and Herzegovina", "Cyprus", "Fiji", "Monaco", "French Guiana", "Brunei", "Montenegro", "Timor-Leste", "Malawi", "Peru", "Turkey", "Slovenia", "Botswana", "Namibia", "Zimbabwe", "Guadeloupe", "Niger", "Lesotho", "The Bahamas", "The Gambia", "Turks and Caicos", "Greece", "South Korea", "Bangladesh", "El Salvador", "Uruguay", "Tanzania", "Panama", "Reunion", "Guatemala", "Tunisia", "Maldives", "Macao"])

# ----------------------------
# THEME & COLORS
# ----------------------------
BG_COLOR = "#F9B11E"       # Ubuy Yellow
CARD_BG = "#FFFFFF"        # White Cards
ACCENT_COLOR = "#000000"   # Black Accents
TEXT_COLOR = "#333333"     # Dark Text
SUBTEXT_COLOR = "#666666"
DANGER_COLOR = "#f44336"
SUCCESS_COLOR = "#4CAF50"

# ----------------------------
# APP CLASS
# ----------------------------
class DashboardApp:
    def __init__(self, root):
        self.root = root
        self.root.title("UBUY - Service Account Dashboard")
        self.root.geometry("1100x800")
        self.root.configure(bg=BG_COLOR)
        
        # High DPI support
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except:
            pass

        self.service_accounts = load_local_sa()
        self.create_widgets()
        self.update_stats()

    def create_widgets(self):
        # Header
        header = tk.Frame(self.root, bg=BG_COLOR, pady=20)
        header.pack(fill="x", padx=40)
        
        title_label = tk.Label(header, text="Service Account Collector", bg=BG_COLOR, fg=ACCENT_COLOR, font=("Segoe UI", 24, "bold"))
        title_label.pack(side="left")
        
        # Stats Frame
        self.stats_label = tk.Label(header, text="0 / 0 Uploaded", bg=BG_COLOR, fg=ACCENT_COLOR, font=("Segoe UI", 12))
        self.stats_label.pack(side="right", pady=(10, 0))

        # Search Bar Frame
        search_frame = tk.Frame(self.root, bg=CARD_BG, padx=15, pady=10)
        search_frame.pack(fill="x", padx=40, pady=(0, 20))
        
        tk.Label(search_frame, text="🔍 Search Country:", bg=CARD_BG, fg=SUBTEXT_COLOR, font=("Segoe UI", 10)).pack(side="left")
        
        self.search_var = tk.StringVar()
        self.search_var.trace_add("write", lambda *args: self.filter_grid())
        
        search_entry = tk.Entry(search_frame, textvariable=self.search_var, font=("Segoe UI", 12), bd=0, highlightthickness=1, highlightbackground="#eee")
        search_entry.pack(side="left", fill="x", expand=True, padx=(10, 0))
        search_entry.focus_set()

        # Content Grid with Scrollbar
        self.canvas_frame = tk.Frame(self.root, bg=BG_COLOR)
        self.canvas_frame.pack(fill="both", expand=True, padx=40, pady=(0, 40))
        
        self.canvas = tk.Canvas(self.canvas_frame, bg=BG_COLOR, highlightthickness=0)
        self.scrollbar = ttk.Scrollbar(self.canvas_frame, orient="vertical", command=self.canvas.yview)
        
        self.scrollable_frame = tk.Frame(self.canvas, bg=BG_COLOR)
        self.scrollable_frame.bind("<Configure>", lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        
        self.canvas.create_window((0, 0), window=self.scrollable_frame, anchor="nw")
        self.canvas.configure(yscrollcommand=self.scrollbar.set)
        
        self.canvas.pack(side="left", fill="both", expand=True)
        self.scrollbar.pack(side="right", fill="y")
        
        # Mousewheel support
        self.canvas.bind_all("<MouseWheel>", self._on_mousewheel)

        self.country_widgets = {}
        self.render_grid()

    def _on_mousewheel(self, event):
        self.canvas.yview_scroll(int(-1*(event.delta/120)), "units")

    def render_grid(self, search_text=""):
        # Clear existing widgets
        for widget in self.scrollable_frame.winfo_children():
            widget.destroy()

        self.country_widgets = {}
        
        filtered_list = [c for c in COUNTRIES_LIST if search_text.lower() in c.lower()]
        
        # Grid Configuration (4 columns)
        cols = 4
        for i, country in enumerate(filtered_list):
            row, col = divmod(i, cols)
            
            card = tk.Frame(self.scrollable_frame, bg=CARD_BG, padx=15, pady=15, highlightthickness=1, highlightbackground="#e0e0e0")
            card.grid(row=row, column=col, padx=10, pady=10, sticky="nsew")
            
            # Status Indicator
            status_color = SUCCESS_COLOR if country in self.service_accounts else "#ccc"
            status_dot = tk.Label(card, text="●", fg=status_color, bg=CARD_BG, font=("Segoe UI", 12))
            status_dot.pack(anchor="nw")
            
            name_label = tk.Label(card, text=country, bg=CARD_BG, fg=TEXT_COLOR, font=("Segoe UI", 11, "bold"), wraplength=180)
            name_label.pack(pady=(5, 10))
            
            btn_frame = tk.Frame(card, bg=CARD_BG)
            btn_frame.pack(fill="x")
            
            if country in self.service_accounts:
                status_text = "Uploaded"
                upload_btn_text = "Update"
                remove_btn = tk.Button(btn_frame, text="Remove", command=lambda c=country: self.remove_sa(c), bg=CARD_BG, fg=DANGER_COLOR, relief="flat", font=("Segoe UI", 9, "underline"))
                remove_btn.pack(side="right")
            else:
                status_text = "Missing"
                upload_btn_text = "Upload JSON"

            upload_btn = tk.Button(btn_frame, text=upload_btn_text, command=lambda c=country: self.upload_sa(c), bg=ACCENT_COLOR, fg="white", relief="flat", padx=10, pady=2, font=("Segoe UI", 9))
            upload_btn.pack(side="left")

            self.country_widgets[country] = {
                "card": card,
                "status_dot": status_dot
            }

    def filter_grid(self):
        query = self.search_var.get()
        self.render_grid(query)

    def upload_sa(self, country):
        file_path = filedialog.askopenfilename(title=f"Select JSON for {country}", filetypes=[("JSON Files", "*.json")])
        if file_path:
            try:
                with open(file_path, "r", encoding="utf-8") as f:
                    sa_data = json.load(f)
                
                # Basic validation
                if "project_id" not in sa_data or "private_key" not in sa_data:
                    messagebox.showerror("Invalid JSON", "This does not appear to be a valid Google Service Account JSON file.")
                    return

                self.service_accounts[country] = sa_data
                if save_local_sa(self.service_accounts):
                    # Local update UI
                    self.render_grid(self.search_var.get())
                    self.update_stats()
                else:
                    messagebox.showerror("Error", "Failed to save file locally.")
            except Exception as e:
                messagebox.showerror("Error", f"Failed to upload: {e}")

    def remove_sa(self, country):
        if messagebox.askyesno("Confirm", f"Remove Service Account for {country}?"):
            if country in self.service_accounts:
                del self.service_accounts[country]
                save_local_sa(self.service_accounts)
                self.render_grid(self.search_var.get())
                self.update_stats()

    def update_stats(self):
        total = len(COUNTRIES_LIST)
        uploaded = len(self.service_accounts)
        self.stats_label.config(text=f"📊 {uploaded} / {total} Countries Configured")

if __name__ == "__main__":
    app_root = tk.Tk()
    dashboard = DashboardApp(app_root)
    app_root.mainloop()
