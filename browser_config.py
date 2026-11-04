"""Проверенный Chrome для поиска и установочных пакетов."""

import os
import platform
import plistlib
import re
import sys
from pathlib import Path

CHROME_VERSION = "157.0.8103.38"
CHROME_SHA256 = {
    "mac-arm64": "3406b38b52b7d341d74450eb7b369be038c9fec9894c8b8a9e9e9f97333caf2f",
    "mac-x64": "c6fcc2869b0d5a421e880d7bcdabe96e4261c4100e125fc4e4daf6346474efa6",
    "win64": "ae0deef27d449e1d7529df7705d609bab102de48464da13567ed3dd2ee1a0586",
}
SANDBOX_DISABLE_FLAGS = frozenset({
    "--no-sandbox", "--disable-setuid-sandbox",
    "--disable-seccomp-filter-sandbox", "--disable-gpu-sandbox",
})


def песочница_отключена(args):
    return any(arg.split("=", 1)[0] in SANDBOX_DISABLE_FLAGS for arg in args)


def chrome_platform():
    if sys.platform == "darwin":
        return "mac-arm64" if platform.machine() == "arm64" else "mac-x64"
    if sys.platform.startswith("win"):
        return "win64"
    raise RuntimeError("Поддерживаются Windows 10/11 и macOS.")


def chrome_executable(root, target=None):
    target = target or chrome_platform()
    folder = Path(root) / f"chrome-{target}"
    if target.startswith("mac-"):
        return folder / "Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing"
    return folder / "chrome.exe"


def проверить_версию(version):
    if not re.fullmatch(r"\d+\.\d+\.\d+\.\d+", version):
        raise RuntimeError("Не удалось проверить версию браузера Chrome.")
    if tuple(map(int, version.split("."))) < tuple(map(int, CHROME_VERSION.split("."))):
        raise RuntimeError(f"Нужен Chrome {CHROME_VERSION} или новее. Обновите программу или браузер.")


def найти_браузер():
    """Выбирает явную установку; старый кэш Playwright не используется."""
    if getattr(sys, "frozen", False):
        folder = Path(sys.executable).resolve().parent
        root = folder.parent / "Resources/chrome" if sys.platform == "darwin" else folder / "_internal/chrome"
        candidates = [chrome_executable(root)]
    elif os.environ.get("POISK_BROWSER_PATH"):
        candidates = [Path(os.environ["POISK_BROWSER_PATH"]).expanduser()]
    else:
        candidates = [chrome_executable(Path.home() / ".poisk-kartinok/chrome" / CHROME_VERSION)]
        if sys.platform == "darwin":
            candidates.append(Path.home() / "Applications/YVFactory/Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing")
    for executable in candidates:
        if executable.is_file():
            if sys.platform == "darwin":
                info = executable.parent.parent / "Info.plist"
                with info.open("rb") as file:
                    проверить_версию(plistlib.load(file).get("CFBundleShortVersionString", ""))
            return executable
    raise RuntimeError("Обновлённый Chrome не найден. Запустите «Запустить.command» или скачайте новую версию программы.")
