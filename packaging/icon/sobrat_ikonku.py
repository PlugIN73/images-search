# -*- coding: utf-8 -*-
"""Собирает иконки программы из ikonka.svg:
  ikonka.icns — для Mac (с прозрачными полями по сетке Apple, как у всех иконок в Dock);
  ikonka.ico  — для Windows (плитка без полей: на панели задач иконки идут во весь размер).

Готовые файлы лежат в репозитории, поэтому сборке этот скрипт не нужен — запускать его,
только когда поменялся рисунок:  python packaging/icon/sobrat_ikonku.py
Нужны playwright с Chromium и Pillow (они и так в requirements.txt), для .icns — Mac (iconutil).
"""
import io
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from PIL import Image
from playwright.sync_api import sync_playwright

ПАПКА = Path(__file__).resolve().parent
РИСУНОК = (ПАПКА / "ikonka.svg").read_text(encoding="utf-8")
ПЛИТКА = "100 100 824 824"          # где на холсте 1024×1024 лежит сама плитка


def нарисовать(svg, размер=1024):
    """SVG → PNG с прозрачным фоном."""
    svg = svg.replace("<svg ", f'<svg width="{размер}" height="{размер}" ', 1)
    with sync_playwright() as pw:
        браузер = pw.chromium.launch()
        страница = браузер.new_page(viewport={"width": размер, "height": размер})
        страница.set_content(f"<html><body style='margin:0;background:transparent'>{svg}</body></html>")
        png = страница.locator("svg").screenshot(omit_background=True)
        браузер.close()
    return Image.open(io.BytesIO(png)).convert("RGBA")


def иконка_mac(картинка):
    with tempfile.TemporaryDirectory() as временная:
        набор = Path(временная) / "ikonka.iconset"
        набор.mkdir()
        for размер in (16, 32, 128, 256, 512):
            картинка.resize((размер, размер), Image.LANCZOS).save(набор / f"icon_{размер}x{размер}.png")
            картинка.resize((размер * 2, размер * 2), Image.LANCZOS).save(набор / f"icon_{размер}x{размер}@2x.png")
        subprocess.run(["iconutil", "-c", "icns", str(набор), "-o", str(ПАПКА / "ikonka.icns")], check=True)


def иконка_windows(картинка):
    размеры = [(р, р) for р in (16, 20, 24, 32, 40, 48, 64, 128, 256)]
    картинка.save(ПАПКА / "ikonka.ico", sizes=размеры)


def main():
    для_mac = нарисовать(РИСУНОК)
    для_windows = нарисовать(РИСУНОК.replace('viewBox="0 0 1024 1024"', f'viewBox="{ПЛИТКА}"', 1))
    для_mac.save(ПАПКА / "ikonka-1024.png")
    иконка_windows(для_windows)
    if shutil.which("iconutil"):
        иконка_mac(для_mac)
    else:
        print("iconutil есть только на Mac — ikonka.icns не обновлена", file=sys.stderr)
    print("Готово:", ", ".join(f.name for f in sorted(ПАПКА.iterdir()) if f.suffix in (".icns", ".ico", ".png")))


if __name__ == "__main__":
    main()
