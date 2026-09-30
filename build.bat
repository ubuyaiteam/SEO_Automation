@echo off
echo Compiling SEO Auto with PyInstaller...

:: Compile the executable
.\venv\Scripts\pyinstaller.exe --noconfirm --onedir --windowed --name "SEO_Auto" --icon "icon.ico" --add-data "ubuy.gif;." --add-data "icon.ico;." --add-data "service_accounts_data.json;." --hidden-import pyotp --hidden-import pyotp.totp --hidden-import pyotp.hotp --hidden-import urllib3 --hidden-import urllib.request --hidden-import urllib.error --hidden-import subprocess --hidden-import google.oauth2.service_account --hidden-import googleapiclient.discovery --hidden-import selenium --hidden-import selenium.webdriver.chrome.webdriver "test_main5.py"

echo PyInstaller completed successfully!

echo.
echo Please ensure Inno Setup is installed.
echo Compiling Inno Setup script...
"C:\Users\Ubuy101\AppData\Local\Programs\Inno Setup 6\ISCC.exe" setup.iss
echo Installer created in Output directory!
pause
