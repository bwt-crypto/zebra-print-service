"""
Сервис печати этикеток на Zebra GK420t
Запуск: python app.py
Открыть в браузере: http://localhost:5000

Кириллица: используется TrueType-шрифт TT0003M_.FNT (Swiss 721)
            + ^CI28 (UTF-8) + данные в кодировке UTF-8
"""

from flask import Flask, request, jsonify, render_template_string
import logging, json, os, re, shutil, subprocess, sys, tempfile, time, urllib.request, zipfile

app = Flask(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

# ─── Настройки ────────────────────────────────────────────────────────────────

PRINTER_NAME = "ZDesigner GK420t"   # Имя из Панель управления → Устройства и принтеры
HOST = "0.0.0.0"                    # доступен с любого ПК в сети
PORT = 5000
APP_VERSION = "1.0.4"
UPDATE_REPO = "bwt-crypto/zebra-print-service"
UPDATE_TIMEOUT = 8
MAX_COPIES = 999

# ─── Каталог товаров ──────────────────────────────────────────────────────────

# Если программа запущена как .exe, берем путь до него. Иначе — путь до скрипта.
if getattr(sys, 'frozen', False):
    BASE_DIR = os.path.dirname(sys.executable)
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))

CATALOG_FILE        = os.path.join(BASE_DIR, "catalog.json")
CUSTOM_CATALOG_FILE = os.path.join(BASE_DIR, "catalog_custom.json")
SERVICE_LOG_FILE    = os.path.join(BASE_DIR, "zebra_service.log")

try:
    file_handler = logging.FileHandler(SERVICE_LOG_FILE, encoding="utf-8")
    file_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logging.getLogger().addHandler(file_handler)
except Exception as e:
    logging.warning(f"Не удалось включить файловый лог {SERVICE_LOG_FILE}: {e}")


def _log_uncaught_exception(exc_type, exc_value, exc_traceback):
    logging.critical("Необработанная ошибка", exc_info=(exc_type, exc_value, exc_traceback))


sys.excepthook = _log_uncaught_exception


def load_catalog() -> list[dict]:
    """Читает оба файла каталога. Изменения в файлах подхватываются налету."""
    result = []
    for path in (CUSTOM_CATALOG_FILE, CATALOG_FILE):
        if os.path.exists(path):
            try:
                with open(path, encoding="utf-8") as f:
                    result += json.load(f)
            except Exception as e:
                logging.warning(f"Ошибка чтения {path}: {e}")
    return result


def save_custom_catalog(items: list[dict]):
    with open(CUSTOM_CATALOG_FILE, "w", encoding="utf-8") as f:
        json.dump(items, f, ensure_ascii=False, indent=2)


# ─── Автообновление ───────────────────────────────────────────────────────────

def _version_tuple(value: str) -> tuple[int, int, int]:
    parts = [int(p) for p in re.findall(r"\d+", value)[:3]]
    return tuple((parts + [0, 0, 0])[:3])


def _request_json(url: str) -> dict:
    req = urllib.request.Request(url, headers={
        "Accept": "application/vnd.github+json",
        "User-Agent": f"ZebraPrint/{APP_VERSION}",
    })
    with urllib.request.urlopen(req, timeout=UPDATE_TIMEOUT) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _find_update_asset(release: dict) -> dict | None:
    for asset in release.get("assets", []):
        name = asset.get("name", "").lower()
        if name == "zebraprint.zip" or (name.startswith("zebraprint-") and name.endswith(".zip")):
            return asset
    return None


def _download_file(url: str, target: str):
    req = urllib.request.Request(url, headers={"User-Agent": f"ZebraPrint/{APP_VERSION}"})
    with urllib.request.urlopen(req, timeout=UPDATE_TIMEOUT) as resp, open(target, "wb") as f:
        shutil.copyfileobj(resp, f)


def _find_file(root: str, filename: str) -> str | None:
    for dirpath, _, filenames in os.walk(root):
        if filename in filenames:
            return os.path.join(dirpath, filename)
    return None


def _bat_quote(value: str) -> str:
    return '"' + value.replace('"', '') + '"'


def _start_update_script(update_dir: str, latest_tag: str):
    current_exe = sys.executable
    base_dir = os.path.dirname(current_exe)
    update_log = os.path.join(base_dir, "zebra_update.log")
    new_exe = _find_file(update_dir, "ZebraPrint.exe")
    if not new_exe:
        raise RuntimeError("В архиве обновления нет ZebraPrint.exe")

    new_catalog = _find_file(update_dir, "catalog.json")
    new_custom = _find_file(update_dir, "catalog_custom.json")
    updater_path = os.path.join(update_dir, "install_update.bat")

    commands = [
        "@echo off",
        "chcp 65001 >nul",
        "setlocal",
        f"set \"LOG={update_log}\"",
        f"echo [%date% %time%] Updating Zebra Print Service to {latest_tag} > \"%LOG%\"",
        "timeout /t 2 /nobreak >> \"%LOG%\" 2>&1",
        f"taskkill /PID {os.getpid()} /F >> \"%LOG%\" 2>&1",
        "timeout /t 2 /nobreak >> \"%LOG%\" 2>&1",
        "for /L %%i in (1,1,30) do (",
        f"  copy /Y {_bat_quote(new_exe)} {_bat_quote(current_exe)} >> \"%LOG%\" 2>&1",
        "  if not errorlevel 1 goto copied",
        "  echo [%date% %time%] Copy attempt %%i failed, retrying... >> \"%LOG%\"",
        "  timeout /t 1 /nobreak >> \"%LOG%\" 2>&1",
        ")",
        "echo [%date% %time%] ERROR: failed to replace exe >> \"%LOG%\"",
        "exit /b 1",
        ":copied",
    ]
    if new_catalog:
        commands.append(
            f"if not exist {_bat_quote(CATALOG_FILE)} copy /Y {_bat_quote(new_catalog)} {_bat_quote(CATALOG_FILE)} >> \"%LOG%\" 2>&1"
        )
    if new_custom:
        commands.append(
            f"if not exist {_bat_quote(CUSTOM_CATALOG_FILE)} copy /Y {_bat_quote(new_custom)} {_bat_quote(CUSTOM_CATALOG_FILE)} >> \"%LOG%\" 2>&1"
        )
    commands.extend([
        f"echo [%date% %time%] Starting updated app >> \"%LOG%\"",
        f"start \"Zebra Print Service\" /D {_bat_quote(base_dir)} {_bat_quote(current_exe)}",
        "if errorlevel 1 echo [%date% %time%] ERROR: failed to start app >> \"%LOG%\"",
        "exit",
    ])

    with open(updater_path, "w", encoding="utf-8") as f:
        f.write("\n".join(commands) + "\n")

    creationflags = 0
    if os.name == "nt":
        creationflags = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS
    subprocess.Popen(["cmd.exe", "/c", "start", "", updater_path], cwd=BASE_DIR,
                     close_fds=True, creationflags=creationflags)


def install_update_if_available() -> bool:
    """Возвращает True, если запущен внешний установщик и текущий процесс надо закрыть."""
    if not getattr(sys, "frozen", False):
        return False

    try:
        latest = _request_json(f"https://api.github.com/repos/{UPDATE_REPO}/releases/latest")
        latest_tag = latest.get("tag_name", "").strip()
        if not latest_tag or _version_tuple(latest_tag) <= _version_tuple(APP_VERSION):
            logging.info(f"Версия актуальна: {APP_VERSION}")
            return False

        asset = _find_update_asset(latest)
        if not asset:
            logging.warning(f"В релизе {latest_tag} нет архива ZebraPrint.zip")
            return False

        update_dir = os.path.join(tempfile.gettempdir(), f"zebra-print-update-{latest_tag}-{int(time.time())}")
        os.makedirs(update_dir, exist_ok=True)
        archive_path = os.path.join(update_dir, asset["name"])

        logging.info(f"Найдена новая версия {latest_tag}, скачиваем обновление")
        _download_file(asset["browser_download_url"], archive_path)

        with zipfile.ZipFile(archive_path) as zf:
            zf.extractall(update_dir)

        _start_update_script(update_dir, latest_tag)
        return True
    except Exception as e:
        logging.warning(f"Не удалось проверить или установить обновление: {e}")
        return False


# ─── ZPL-генерация (кириллица через TT0003M_ + UTF-8) ────────────────────────
#
# КЛЮЧЕВЫЕ ИЗМЕНЕНИЯ по сравнению со старой версией:
#
# 1. ^CI28              — принтер ожидает UTF-8 данные (было ^CI26 = CP1251)
# 2. ^CWZ,E:TT0003M_.FNT — назначаем TrueType-шрифт Swiss 721 на букву Z
# 3. ^AZN,h,w           — используем шрифт Z вместо ^A0 (bitmap без кириллицы)
# 4. .encode("utf-8")   — отправляем данные в UTF-8 (было cp1251)
#
# Шрифт TT0003M_.FNT предустановлен на большинстве Zebra GK420t.
# Проверить: отправить ^XA^WDE:*.FNT*^XZ — принтер напечатает список шрифтов.
# Если шрифта нет — скачать Zebra Setup Utilities и загрузить Swiss 721.

# Общая «шапка» ZPL: назначение шрифта + UTF-8
ZPL_FONT_INIT = "^CWZ,E:TT0003M_.FNT"

def _wrap(name: str, max_chars: int = 40, max_lines: int = 3) -> list[str]:
    """Разбивает название на строки по словам. Уважает явные \\n."""
    name = name.replace("^", "").replace("~", "")
    raw_lines = name.replace("\r\n", "\n").split("\n")
    result = []
    for raw in raw_lines:
        line = ""
        for word in raw.split():
            if not line:
                line = word
            elif len(line) + 1 + len(word) <= max_chars:
                line += " " + word
            else:
                result.append(line)
                line = word
                if len(result) >= max_lines:
                    break
        if line and len(result) < max_lines:
            result.append(line)
        if len(result) >= max_lines:
            break
    return result[:max_lines]


def _zpl_title(name: str, x: int, y: int, line_h: int = 18) -> list[str]:
    """Заголовок: TrueType шрифт Z, размер 16 точек."""
    return [f"^FO{x},{y + i * line_h}^AZN,16,0^FD{line}^FS"
            for i, line in enumerate(_wrap(name))]


def _zpl(lines: list[str]) -> bytes:
    """Собирает ZPL-команды в байты UTF-8."""
    return "\n".join(lines).encode("utf-8")


def zpl_npvh(name: str, barcode: str) -> bytes:
    """Труба НПВХ канализационная (АО «Хемкор»). Гарантия 2 года."""
    return _zpl([
        "^XA",
        ZPL_FONT_INIT,
        "^CI28",
        "^PW400",
        "^LL320",
        "^LH0,0",
        *_zpl_title(name, x=8, y=8),
        f"^FO20,60^BY2,3,2^BCN,136,N,N,N^FD{barcode}^FS",  # Сдвинули штрихкод на X=20
        f"^FO0,200^AZN,16,0^FB400,1,0,C^FD{barcode}^FS",
        "^FO3,218^AZN,14,0^FDСрок службы: 50 лет^FS",
        "^FO200,218^AZN,14,0^FB190,1,0,R^FDПроизводитель:^FS", # Блок шириной 190, выравнивание вправо (R)
        "^FO3,234^AZN,14,0^FDДата изг.: указана на товаре^FS",
        "^FO200,234^AZN,14,0^FB190,1,0,R^FDАО \"Хемкор\"^FS",   # Выравнивание вправо
        "^FO3,250^AZN,14,0^FDГарантия: 2 года с даты изготовления^FS",
        "^FO0,272^AZN,12,0^FB400,2,0,C^FD212008, г. Могилев, пер. Мечникова 4-й, 17Б"
        "   т/ф: +375 222 72 77 72, GSM: +375 29 32 32 800^FS",
        "^XZ",
    ])


def zpl_pe100(name: str, barcode: str) -> bytes:
    """Труба ПЭ100 (ООО «СКТ инжиниринг»). Гарантия 2 года."""
    return _zpl([
        "^XA",
        ZPL_FONT_INIT,
        "^CI28",
        "^PW400",
        "^LL320",
        "^LH0,0",
        *_zpl_title(name, x=1, y=8),
        f"^FO20,62^BY2,3,2^BCN,130,N,N,N^FD{barcode}^FS",  # Сдвинули штрихкод на X=20
        f"^FO0,196^AZN,16,0^FB400,1,0,C^FD{barcode}^FS",
        "^FO4,214^AZN,14,0^FDСрок службы: 50 лет^FS",
        "^FO200,214^AZN,14,0^FB190,1,0,R^FDПроизводитель:^FS", # Выравнивание вправо
        "^FO4,230^AZN,14,0^FDДата изг.: указана на товаре^FS",
        "^FO200,230^AZN,14,0^FB190,1,0,R^FDООО \"СКТ инжиниринг\"^FS", # Выравнивание вправо
        "^FO4,246^AZN,14,0^FDГарантия: 2 года с даты изготовления^FS",
        "^FO0,272^AZN,12,0^FB400,2,0,C^FD212008, г. Могилев, пер. Мечникова 4-й, 17Б"
        "   т/ф: +375 222 72 77 72, GSM: +375 29 32 32 800^FS",
        "^XZ",
    ])


def zpl_valfex(name: str, barcode: str) -> bytes:
    """Valfex (ООО «Валф-Рус»). Гарантия 10 лет."""
    return _zpl([
        "^XA",
        ZPL_FONT_INIT,
        "^CI28",
        "^PW400",
        "^LL320",
        "^LH0,0",
        *_zpl_title(name, x=8, y=8),
        f"^FO20,60^BY2,3,2^BCN,136,N,N,N^FD{barcode}^FS",  # Сдвинули штрихкод на X=20
        f"^FO0,200^AZN,16,0^FB400,1,0,C^FD{barcode}^FS",
        "^FO3,218^AZN,14,0^FDСрок службы: 50 лет^FS",
        "^FO200,218^AZN,14,0^FB190,1,0,R^FDПроизводитель:^FS", # Выравнивание вправо
        "^FO3,234^AZN,14,0^FDДата изг.: указана на товаре^FS",
        "^FO200,234^AZN,14,0^FB190,1,0,R^FDООО \"Валф-Рус\"^FS", # Выравнивание вправо
        "^FO3,250^AZN,14,0^FDГарантия: 10 лет с даты изготовления^FS",
        "^FO0,272^AZN,12,0^FB400,2,0,C^FD212008, г. Могилев, пер. Мечникова 4-й, 17Б"
        "   т/ф: +375 222 72 77 72, GSM: +375 29 32 32 800^FS",
        "^XZ",
    ])


TEMPLATES = {
    "npvh":   {"label": "Труба НПВХ",  "fn": zpl_npvh},
    "pe100":  {"label": "Труба ПЭ100", "fn": zpl_pe100},
    "valfex": {"label": "Valfex",      "fn": zpl_valfex},
}

# ─── Печать ───────────────────────────────────────────────────────────────────

def send_to_printer(zpl_bytes: bytes, copies: int = 1):
    try:
        import win32print
    except ImportError:
        raise RuntimeError("Установи pywin32: pip install pywin32")
    handle = win32print.OpenPrinter(PRINTER_NAME)
    try:
        win32print.StartDocPrinter(handle, 1, ("ZPL Label", None, "RAW"))
        win32print.StartPagePrinter(handle)
        for _ in range(copies):
            win32print.WritePrinter(handle, zpl_bytes)
        win32print.EndPagePrinter(handle)
        win32print.EndDocPrinter(handle)
    finally:
        win32print.ClosePrinter(handle)


def list_printers() -> list[str]:
    try:
        import win32print
        flags = win32print.PRINTER_ENUM_LOCAL | win32print.PRINTER_ENUM_CONNECTIONS
        return [p[2] for p in win32print.EnumPrinters(flags)]
    except Exception:
        return []

# ─── HTML-интерфейс ───────────────────────────────────────────────────────────

HTML = r"""<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Печать этикеток — 21 Век</title>
<style>
  *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: Arial, sans-serif; background: #f0f2f5; min-height: 100vh;
         display: flex; align-items: flex-start; justify-content: center; padding: 32px 16px; }
  .card { background: #fff; border-radius: 12px; box-shadow: 0 2px 12px rgba(0,0,0,.10);
          padding: 28px 32px; width: 100%; max-width: 540px; }
  h1 { font-size: 20px; font-weight: 700; margin-bottom: 20px; color: #1a1a2e; }
  .lbl { font-size: 12px; font-weight: 600; color: #888; text-transform: uppercase;
         letter-spacing: .6px; margin-bottom: 6px; margin-top: 16px; }
  .tmpl-row { display: flex; gap: 8px; }
  .tmpl-btn { flex: 1; padding: 9px 4px; border: 1.5px solid #ddd; border-radius: 8px;
              background: #fafafa; font-size: 13px; cursor: pointer; color: #555;
              transition: all .15s; font-weight: 500; }
  .tmpl-btn.active { border-color: #2563eb; background: #eff6ff; color: #2563eb; }
  .tmpl-btn:hover:not(.active) { border-color: #aaa; background: #f3f3f3; }
  textarea, input[type=text] { width: 100%; border: 1.5px solid #ddd; border-radius: 8px;
    padding: 10px 12px; font-size: 14px; color: #1a1a2e; resize: vertical; outline: none;
    transition: border .15s; font-family: Arial, sans-serif; }
  textarea:focus, input[type=text]:focus { border-color: #2563eb; }
  textarea { min-height: 68px; line-height: 1.5; }
  .row2 { display: grid; grid-template-columns: 1fr 120px; gap: 12px; }
  .qty-wrap { display: flex; align-items: center; gap: 6px; }
  .qty-btn { width: 34px; height: 40px; border: 1.5px solid #ddd; border-radius: 8px;
             background: #fafafa; font-size: 18px; cursor: pointer; color: #444;
             display: flex; align-items: center; justify-content: center; transition: background .1s; }
  .qty-btn:hover { background: #eee; }
  input[type=number] { width: 46px; height: 40px; text-align: center; border: 1.5px solid #ddd;
    border-radius: 8px; font-size: 15px; font-weight: 600; color: #1a1a2e; outline: none;
    -moz-appearance: textfield; }
  input[type=number]::-webkit-outer-spin-button,
  input[type=number]::-webkit-inner-spin-button { -webkit-appearance: none; }
  .btn-print { width: 100%; margin-top: 20px; padding: 13px; border: none; border-radius: 10px;
               background: #2563eb; color: #fff; font-size: 16px; font-weight: 700;
               cursor: pointer; transition: background .15s; }
  .btn-print:hover { background: #1d4ed8; }
  .btn-print:active { transform: scale(.98); }
  .btn-print:disabled { background: #93b4f5; cursor: default; }
  .btn-save { width: 100%; margin-top: 8px; padding: 10px; border: 1.5px solid #ddd;
              border-radius: 10px; background: #fff; color: #555; font-size: 14px;
              font-weight: 600; cursor: pointer; transition: all .15s; }
  .btn-save:hover { border-color: #2563eb; color: #2563eb; background: #eff6ff; }
  .status { margin-top: 12px; padding: 10px 14px; border-radius: 8px; font-size: 14px; display: none; }
  .status.ok  { background: #d1fae5; color: #065f46; display: block; }
  .status.err { background: #fee2e2; color: #991b1b; display: block; }
  .preview-box { border: 1px solid #e5e7eb; border-radius: 8px; background: #f9fafb;
                 padding: 10px; margin-top: 6px; min-height: 60px; }
  .printer-info { font-size: 12px; color: #aaa; margin-top: 12px; text-align: center; }
  .search-wrap { position: relative; }
  .dd { display: none; position: absolute; top: 100%; left: 0; right: 0; background: #fff;
        border: 1.5px solid #2563eb; border-top: none; border-radius: 0 0 10px 10px;
        max-height: 230px; overflow-y: auto; z-index: 200;
        box-shadow: 0 6px 16px rgba(0,0,0,.12); }
  .dd-item { padding: 9px 12px; font-size: 13px; cursor: pointer;
             border-bottom: 0.5px solid #f0f0f0; line-height: 1.4; }
  .dd-item:last-child { border-bottom: none; }
  .dd-item:hover { background: #eff6ff; }
  .dd-item .bc { color: #888; font-size: 11px; margin-left: 6px; }
  .badge { font-size: 10px; font-weight: 600; padding: 1px 5px; border-radius: 4px; margin-left: 4px; }
  .badge-npvh   { background: #e0f2fe; color: #0369a1; }
  .badge-pe100  { background: #dcfce7; color: #166534; }
  .badge-valfex { background: #fef3c7; color: #92400e; }
</style>
</head>
<body>
<div class="card">
  <h1>🏷 Печать этикеток</h1>

  <div class="lbl">Поиск по каталогу</div>
  <div class="search-wrap">
    <input type="text" id="search" placeholder="Введи название или штрихкод…" autocomplete="off" />
    <div class="dd" id="dd"></div>
  </div>

  <div class="lbl">Шаблон</div>
  <div class="tmpl-row">
    <button class="tmpl-btn active" onclick="setTmpl(this,'npvh')">Труба НПВХ</button>
    <button class="tmpl-btn" onclick="setTmpl(this,'pe100')">Труба ПЭ100</button>
    <button class="tmpl-btn" onclick="setTmpl(this,'valfex')">Valfex</button>
  </div>

  <div class="lbl">Название товара <span style="font-weight:400;text-transform:none;letter-spacing:0;color:#bbb">— Shift+Enter для переноса строки</span></div>
  <textarea id="name" placeholder="Труба НПВХ SN4 DN160 S4,0 L3000 канализационная" oninput="updatePreview()"></textarea>

  <div class="lbl">Штрихкод</div>
  <div class="row2">
    <input type="text" id="barcode" placeholder="T01T008451011" oninput="updatePreview()" />
    <div>
      <div class="lbl" style="margin-top:0">Копий</div>
      <div class="qty-wrap">
        <button class="qty-btn" onclick="adj(-1)">−</button>
        <input type="number" id="qty" value="1" min="1" max="999" />
        <button class="qty-btn" onclick="adj(1)">+</button>
      </div>
    </div>
  </div>

  <div class="lbl">Предпросмотр этикетки</div>
  <div class="preview-box" id="preview-box">
    <div id="prev-placeholder" style="text-align:center;color:#bbb;font-size:13px;padding:8px 0">
      Заполните название и штрихкод
    </div>
    <img id="prev-img" style="display:none;width:100%;border-radius:4px;image-rendering:pixelated" alt="Предпросмотр этикетки" />
    <div id="prev-loading" style="display:none;text-align:center;color:#888;font-size:13px;padding:8px 0">
      Загрузка…
    </div>
    <!-- Схематичный предпросмотр (fallback когда Labelary недоступен) -->
    <div id="prev-fallback" style="display:none">
      <div id="prev-name" style="font-size:13px;font-weight:700;color:#1a1a2e;line-height:1.4;word-break:break-word;margin-bottom:6px"></div>
      <div id="bars" style="display:flex;gap:1px;margin:4px 0;height:28px;align-items:flex-end"></div>
      <div id="prev-bc" style="font-size:11px;color:#555;margin-bottom:4px"></div>
      <div style="font-size:11px;color:#bbb">⚠ Labelary недоступен — схематичный предпросмотр</div>
    </div>
    <div id="prev-error" style="display:none;font-size:12px;color:#aaa;padding:4px 0;text-align:center"></div>
  </div>

  <button class="btn-print" onclick="doPrint()">🖨 Печатать</button>
  <button class="btn-save" id="btn-save" onclick="doSave()">＋ Добавить в каталог</button>
  <div class="status" id="status"></div>
  <div class="printer-info" id="printer-info">
    Принтер: ZDesigner GK420t
  </div>
</div>

<script>
const TMPL_IDX = {npvh: 0, pe100: 1, valfex: 2};
let currentTmpl = 'npvh';
let catalog = [];

fetch('/api/catalog').then(r => r.json()).then(data => { catalog = data; });

document.getElementById('search').addEventListener('input', filterCatalog);
document.getElementById('search').addEventListener('focus', filterCatalog);
document.addEventListener('click', e => {
  if (!e.target.closest('.search-wrap')) closeDd();
});

function filterCatalog() {
  const q = document.getElementById('search').value.toLowerCase().trim();
  const dd = document.getElementById('dd');
  if (!q || q.length < 2) { closeDd(); return; }
  const matches = catalog.filter(p =>
    p.name.toLowerCase().includes(q) || p.barcode.toLowerCase().includes(q)
  ).slice(0, 15);
  if (!matches.length) { closeDd(); return; }
  dd.innerHTML = matches.map(p => {
    const badgeLabel = p.template === 'npvh' ? 'НПВХ' : p.template === 'pe100' ? 'ПЭ100' : 'Valfex';
    const idx = catalog.indexOf(p);
    return `<div class="dd-item" data-i="${idx}">
      <span>${p.name}</span>
      <span class="badge badge-${p.template}">${badgeLabel}</span>
      <span class="bc">${p.barcode}</span>
    </div>`;
  }).join('');
  dd.querySelectorAll('.dd-item').forEach(el => {
    el.addEventListener('click', () => applyProduct(catalog[parseInt(el.dataset.i)]));
  });
  dd.style.display = 'block';
}

function closeDd() {
  document.getElementById('dd').style.display = 'none';
}

function applyProduct(item) {
  document.getElementById('search').value  = '';
  document.getElementById('name').value    = item.name;
  document.getElementById('barcode').value = item.barcode;
  closeDd();
  currentTmpl = item.template;
  document.querySelectorAll('.tmpl-btn').forEach((b, i) => {
    b.classList.toggle('active', i === TMPL_IDX[item.template]);
  });
  clearTimeout(previewTimer);
  fetchPreview();
}

function setTmpl(el, key) {
  document.querySelectorAll('.tmpl-btn').forEach(b => b.classList.remove('active'));
  el.classList.add('active');
  currentTmpl = key;
  clearTimeout(previewTimer);
  fetchPreview();
}

function showFallback(name, barcode) {
  const raw = name || '—';
  document.getElementById('prev-name').innerHTML = raw.split('\n').map(l => l || '&nbsp;').join('<br>');
  document.getElementById('prev-bc').textContent = barcode || '—';
  const c = document.getElementById('bars');
  c.innerHTML = '';
  const w = [3,1,2,1,3,2,1,3,1,2,1,3,2,1,2,3,1,2,3,1,2,1,3,2,1,3,1,2];
  const h = [28,20,28,16,28,22,28,18,28,24,28,14,28,22,28,18,28,26,28,16,28,20,28,24,28,18,28,22];
  w.forEach((ww, i) => {
    const b = document.createElement('div');
    b.style.cssText = `width:${ww}px;height:${h[i]}px;background:#1a1a2e;border-radius:1px;opacity:${i%2===0?1:.3}`;
    c.appendChild(b);
  });
  document.getElementById('prev-placeholder').style.display = 'none';
  document.getElementById('prev-img').style.display        = 'none';
  document.getElementById('prev-loading').style.display    = 'none';
  document.getElementById('prev-error').style.display      = 'none';
  document.getElementById('prev-fallback').style.display   = 'block';
}

function fetchPreview() {
  const name    = document.getElementById('name').value.trim();
  const barcode = document.getElementById('barcode').value.trim();
  const ph   = document.getElementById('prev-placeholder');
  const img  = document.getElementById('prev-img');
  const load = document.getElementById('prev-loading');
  const err  = document.getElementById('prev-error');
  const fb   = document.getElementById('prev-fallback');

  if (!name || !barcode) {
    img.style.display = 'none'; load.style.display = 'none';
    err.style.display = 'none'; fb.style.display   = 'none';
    ph.style.display  = 'block';
    return;
  }
  ph.style.display = 'none'; img.style.display = 'none';
  err.style.display = 'none'; fb.style.display  = 'none';
  load.style.display = 'block';

  fetch('/api/label_preview', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ template: currentTmpl, name, barcode }),
  }).then(r => r.json().then(d => ({ ok: r.ok, d }))).then(({ ok, d }) => {
    load.style.display = 'none';
    if (ok && d.image) {
      img.src = d.image;
      img.style.display = 'block';
    } else if (d.fallback) {
      showFallback(name, barcode);
    } else {
      err.textContent = d.error || 'Ошибка предпросмотра';
      err.style.display = 'block';
    }
  }).catch(() => {
    load.style.display = 'none';
    showFallback(name, barcode);
  });
}

function adj(d) {
  const el = document.getElementById('qty');
  el.value = Math.max(1, Math.min(999, parseInt(el.value || 1) + d));
}

function showStatus(msg, ok) {
  const s = document.getElementById('status');
  s.className = 'status ' + (ok ? 'ok' : 'err');
  s.textContent = msg;
  if (ok) setTimeout(() => { s.className = 'status'; }, 4000);
}

async function doSave() {
  const name    = document.getElementById('name').value.trim();
  const barcode = document.getElementById('barcode').value.trim();
  if (!name)    { showStatus('⚠ Введите название товара', false); return; }
  if (!barcode) { showStatus('⚠ Введите штрихкод', false); return; }
  const btn = document.getElementById('btn-save');
  btn.disabled = true; btn.textContent = 'Сохранение…';
  try {
    const r = await fetch('/api/catalog/add', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ template: currentTmpl, name, barcode }),
    });
    const d = await r.json();
    if (r.ok) {
      showStatus('✓ Товар добавлен в каталог', true);
      fetch('/api/catalog').then(r => r.json()).then(data => { catalog = data; });
    } else if (r.status === 409) {
      showStatus('ℹ Товар с таким штрихкодом уже есть в каталоге', false);
    } else {
      showStatus('✗ Ошибка: ' + (d.error || 'неизвестная'), false);
    }
  } catch (e) {
    showStatus('✗ Ошибка: ' + e.message, false);
  } finally {
    btn.disabled = false; btn.textContent = '＋ Добавить в каталог';
  }
}

// Shift+Enter вставляет \n, обычный Enter — не делает ничего особенного
document.getElementById('name').addEventListener('keydown', e => {
  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault();
  }
});

async function doPrint() {
  const name    = document.getElementById('name').value.trim();
  const barcode = document.getElementById('barcode').value.trim();
  const copies  = parseInt(document.getElementById('qty').value) || 1;
  if (!name)    { showStatus('⚠ Введите название товара', false); return; }
  if (!barcode) { showStatus('⚠ Введите штрихкод', false); return; }
  const btn = document.querySelector('.btn-print');
  btn.disabled = true; btn.textContent = 'Отправка…';
  try {
    const r = await fetch('/api/print', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ template: currentTmpl, name, barcode, copies }),
    });
    const d = await r.json();
    r.ok ? showStatus(`✓ Отправлено на принтер (${copies} шт.)`, true)
         : showStatus('✗ Ошибка: ' + (d.error || 'неизвестная'), false);
  } catch (e) {
    showStatus('✗ Сервис недоступен: ' + e.message, false);
  } finally {
    btn.disabled = false; btn.textContent = '🖨 Печатать';
  }
}

async function checkPrinter() {
  const el = document.getElementById('printer-info');
  try {
    const d = await (await fetch('/api/status')).json();
    const prefix = d.found ? '🟢' : '🔴';
    el.innerHTML = prefix + ' Принтер: ' + d.printer + ' · v' + d.version;
  } catch {
    el.innerHTML = '🔴 Сервис недоступен';
  }
}


let previewTimer = null;

function updatePreview() {
  clearTimeout(previewTimer);
  previewTimer = setTimeout(fetchPreview, 700);
}

updatePreview();
checkPrinter();
</script>
</body>
</html>"""

# ─── API-маршруты ─────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template_string(HTML)


@app.route("/api/catalog")
def api_catalog():
    return jsonify(load_catalog())


@app.route("/api/catalog/add", methods=["POST"])
def api_catalog_add():
    data     = request.get_json(force=True)
    name     = data.get("name", "").strip()
    barcode  = data.get("barcode", "").strip()
    template = data.get("template", "npvh")
    if not name or not barcode:
        return jsonify({"error": "Нужны name и barcode"}), 400
    if template not in TEMPLATES:
        return jsonify({"error": f"Неизвестный шаблон: {template}"}), 400
    all_items = load_catalog()
    if any(p["barcode"] == barcode for p in all_items):
        return jsonify({"error": "Товар с таким штрихкодом уже есть в каталоге"}), 409
    custom = []
    if os.path.exists(CUSTOM_CATALOG_FILE):
        try:
            with open(CUSTOM_CATALOG_FILE, encoding="utf-8") as f:
                custom = json.load(f)
        except Exception:
            pass
    item = {"name": name, "barcode": barcode, "template": template}
    custom.append(item)
    save_custom_catalog(custom)
    logging.info(f"Добавлен в каталог: {barcode} {name!r}")
    return jsonify({"status": "ok", "item": item})


@app.route("/api/print", methods=["POST"])
def api_print():
    data     = request.get_json(force=True)
    template = data.get("template", "")
    name     = data.get("name", "").strip()
    barcode  = data.get("barcode", "").strip()
    try:
        copies = max(1, min(MAX_COPIES, int(data.get("copies", 1))))
    except (TypeError, ValueError):
        return jsonify({"error": f"Количество копий должно быть числом от 1 до {MAX_COPIES}"}), 400
    if not name:
        return jsonify({"error": "Имя товара не может быть пустым"}), 400
    if not barcode:
        return jsonify({"error": "Штрихкод не может быть пустым"}), 400
    if template not in TEMPLATES:
        return jsonify({"error": f"Неизвестный шаблон: {template}"}), 400
    try:
        zpl = TEMPLATES[template]["fn"](name, barcode)
        send_to_printer(zpl, copies=copies)
        logging.info(f"Печать: шаблон={template} copies={copies} barcode={barcode} name={name!r}")
        return jsonify({"status": "ok"})
    except Exception as e:
        logging.error(f"Ошибка печати: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/status")
def api_status():
    printers = list_printers()
    found = PRINTER_NAME in printers
    return jsonify({"version": APP_VERSION, "printer": PRINTER_NAME, "found": found, "all_printers": printers})


@app.route("/api/label_preview", methods=["POST"])
def api_label_preview():
    """Рендерит реальное изображение этикетки через Labelary API.

    Labelary не поддерживает TT0003M_.FNT — для превью конвертируем
    в ^CI26 (CP1251) + встроенный Font 0, что достаточно для визуальной оценки.
    На реальном принтере будет использоваться TrueType + UTF-8.
    """
    import urllib.request, urllib.error, base64
    data     = request.get_json(force=True)
    template = data.get("template", "npvh")
    name     = data.get("name", "").strip()
    barcode  = data.get("barcode", "").strip()
    if not name or not barcode or template not in TEMPLATES:
        return jsonify({"error": "Нет данных"}), 400
    try:
        zpl_bytes = TEMPLATES[template]["fn"](name, barcode)
        zpl_str = zpl_bytes.decode("utf-8")

        # Для Labelary: удаляем TrueType-шрифт, но ОСТАВЛЯЕМ ^CI28
        zpl_for_labelary = zpl_str.replace(ZPL_FONT_INIT + "\n", "")
        
        # ^AZN → ^A0N  (Font Z → Font 0, который Labelary слинкует с юникодом)
        import re
        zpl_for_labelary = re.sub(r'\^AZN,(\d+),(?:\d+)?', r'^A0N,\1,\1', zpl_for_labelary)
        
        # Отправляем чистый UTF-8, конвертировать в cp1251 не нужно!
        labelary_bytes = zpl_for_labelary.encode("utf-8")

        url = "http://api.labelary.com/v1/printers/8dpmm/labels/1.97x1.57/0/"
        req = urllib.request.Request(url, data=labelary_bytes,
              headers={"Content-Type": "application/x-www-form-urlencoded", "Accept": "image/png"})
        with urllib.request.urlopen(req, timeout=5) as resp:
            png = resp.read()
        return jsonify({"image": "data:image/png;base64," + base64.b64encode(png).decode()})
    except urllib.error.URLError as e:
        logging.warning(f"Labelary недоступен: {e.reason}")
        return jsonify({"error": "Labelary недоступен", "fallback": True}), 502
    except Exception as e:
        logging.error(f"Ошибка предпросмотра: {type(e).__name__}: {e}")
        return jsonify({"error": str(e), "fallback": True}), 500


@app.route("/api/zpl_preview", methods=["POST"])
def api_zpl_preview():
    """Возвращает сырой ZPL без печати — для отладки."""
    data     = request.get_json(force=True)
    template = data.get("template", "npvh")
    name     = data.get("name", "Тест товар")
    barcode  = data.get("barcode", "123456789")
    if template not in TEMPLATES:
        return jsonify({"error": "Неизвестный шаблон"}), 400
    zpl_bytes = TEMPLATES[template]["fn"](name, barcode)
    return jsonify({"zpl": zpl_bytes.decode("utf-8")})


# ─── Точка входа ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import socket
    import webbrowser
    from threading import Timer

    socket.getfqdn = lambda name="": name or "localhost"

    if install_update_if_available():
        sys.exit(0)

    logging.info(f"Запуск сервиса v{APP_VERSION} на http://{HOST}:{PORT}")
    logging.info(f"Принтер: {PRINTER_NAME}")
    #logging.info("Кириллица: TT0003M_.FNT + ^CI28 (UTF-8)")
    
    available = list_printers()
    if PRINTER_NAME in available:
        logging.info("✓ Принтер найден")
    else:
        logging.warning(f"⚠ Принтер НЕ найден. Доступные: {available}")

    # Функция для автоматического открытия браузера
    def open_browser():
        try:
            webbrowser.open(f"http://localhost:{PORT}")
        except Exception as e:
            logging.error(f"Не удалось открыть браузер: {e}")

    # Запускаем таймер: браузер откроется через 1.5 секунды параллельно с сервером
    Timer(1.5, open_browser).start()

    try:
        from waitress import serve
        logging.info("Сервер: waitress (production)")
        serve(app, host=HOST, port=PORT)
    except ImportError:
        logging.warning("waitress не установлен, используется dev-сервер. Запусти: pip install waitress")
        app.run(host=HOST, port=PORT, debug=False)
    except Exception as e:
        logging.exception(f"Сервис не смог запуститься: {e}")
        print()
        print(f"Сервис не смог запуститься: {e}")
        print(f"Подробности записаны в: {SERVICE_LOG_FILE}")
        input("Нажмите Enter, чтобы закрыть окно...")
