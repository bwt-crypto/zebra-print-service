# Zebra Print Service

Локальный сервис печати этикеток Zebra GK420t для 21 Век.

## Запуск из исходников

```bat
python -m venv venv
call venv\Scripts\activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python app.py
```

После запуска интерфейс доступен по адресу:

```text
http://localhost:5000
```

С другого компьютера в локальной сети:

```text
http://<IP-адрес-компьютера>:5000
```

## Настройка принтера

Имя принтера задается в `app.py`:

```python
PRINTER_NAME = "ZDesigner GK420t"
```

Если в Windows принтер называется иначе, поменяй эту строку на имя из панели
`Устройства и принтеры`.

## Сборка exe

```bat
call venv\Scripts\activate
python -m pip install pyinstaller
pyinstaller --clean --noconfirm app.spec
powershell -NoProfile -Command "Compress-Archive -Path 'dist\ZebraPrint\*' -DestinationPath 'dist\ZebraPrint-v1.0.7.zip' -Force"
```

Готовая сборка лежит в `dist\ZebraPrint`:

- `ZebraPrint.exe`
- `_internal`
- `ZebraPrint-v1.0.7.zip`

В публичном репозитории `catalog.json` и `catalog_custom.json` являются заглушками.
На рабочем компьютере с принтером положите реальные локальные файлы рядом с
`ZebraPrint.exe`.

Архив релиза содержит `ZebraPrint.exe` и папку `_internal`, но не содержит рабочие
`catalog.json` и `catalog_custom.json`.

## Автообновление

В собранном exe приложение при запуске проверяет последний релиз:

```text
https://github.com/bwt-crypto/zebra-print-service/releases/latest
```

Если тег релиза новее текущей версии `APP_VERSION`, сервис скачивает zip-архив
`ZebraPrint-vX.Y.Z.zip`, заменяет файлы приложения и перезапускается.
Локальные `catalog.json` и `catalog_custom.json` при обновлении не перезаписываются,
если эти файлы уже есть рядом с exe.

## Копии

Сервис ограничивает количество копий диапазоном `1..999`.

## Отладка ZPL

```text
POST http://localhost:5000/api/zpl_preview
{"template": "npvh", "name": "Тест", "barcode": "T01T123"}
```
