@echo off
echo Compiling SEO Auto with PyInstaller...

:: Compile the executable
pyinstaller --noconfirm --onedir --windowed --name "SEO_Auto" --icon "icon.ico" --add-data "ubuy.gif;." --add-data "icon.ico;." --add-data "service_accounts_data.json;." "test_main5.py"

echo PyInstaller completed successfully!

echo.
echo Please ensure Inno Setup is installed.
echo Compiling Inno Setup script...
"C:\Program Files (x86)\Inno Setup 6\ISCC.exe" setup.iss
echo Installer created in Output directory!
pause
