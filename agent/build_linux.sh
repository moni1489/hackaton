#!/usr/bin/env bash
# build_linux.sh — Сборка агента для Linux в один бинарный файл
# Запускать: bash build_linux.sh
set -e

cd "$(dirname "$0")"
echo "=== Установка зависимостей ==="
pip install --quiet -r requirements-tray.txt pyinstaller

echo "=== Сборка PyInstaller ==="
pyinstaller --onefile \
  --noconsole \
  --name "SAM-VKO-Agent" \
  --hidden-import "pystray._xorg" \
  --hidden-import "PIL.ImageFont" \
  --hidden-import "PIL.ImageDraw" \
  --hidden-import "tkinter" \
  --hidden-import "tkinter.ttk" \
  --hidden-import "speedtest" \
  tray_agent.py

echo ""
echo "=== Готово! ==="
echo "Файл: dist/SAM-VKO-Agent"
echo ""
echo "Установить автозапуск:"
echo "  ./dist/SAM-VKO-Agent --install-autostart"
echo ""
echo "Запустить:"
echo "  ./dist/SAM-VKO-Agent"
