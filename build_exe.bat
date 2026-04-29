@echo off
cd /d "%~dp0"

if not exist venv\Scripts\python.exe (
  python -m venv venv
)

call venv\Scripts\activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python -m pip install pyinstaller
pyinstaller --clean --noconfirm app.spec

for /f %%v in ('python -c "import re; print(re.search(r'APP_VERSION = \"([^\"]+)\"', open('app.py', encoding='utf-8').read()).group(1))"') do set APP_VERSION=%%v
powershell -NoProfile -Command "Compress-Archive -Path 'dist\ZebraPrint\*' -DestinationPath 'dist\ZebraPrint-v%APP_VERSION%.zip' -Force"

echo.
echo Ready: dist\ZebraPrint
echo Ready: dist\ZebraPrint-v%APP_VERSION%.zip
pause
