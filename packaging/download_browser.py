"""Загружает закреплённый Chrome из Google, проверяет SHA-256 и сохраняет вне профилей."""

import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
import urllib.request
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from browser_config import CHROME_SHA256, CHROME_VERSION, chrome_executable, chrome_platform


def install(destination, archive=None):
    destination = Path(destination).resolve()
    executable = chrome_executable(destination)
    marker = destination / ".chrome-version"
    if executable.is_file() and marker.is_file() and marker.read_text().strip() == CHROME_VERSION:
        return executable
    if destination.exists():
        raise RuntimeError(f"Неполная установка Chrome: {destination}. Существующие файлы не изменены.")
    if archive is not None and not Path(archive).is_file():
        raise RuntimeError("Указанный архив Chrome не найден.")
    destination.parent.mkdir(parents=True, exist_ok=True)
    target = chrome_platform()
    with tempfile.TemporaryDirectory(prefix="poisk-chrome-", dir=destination.parent) as temp:
        temp = Path(temp)
        archive = Path(archive) if archive else temp / "chrome.zip"
        if not archive.is_file():
            url = f"https://storage.googleapis.com/chrome-for-testing-public/{CHROME_VERSION}/{target}/chrome-{target}.zip"
            with urllib.request.urlopen(url, timeout=120) as response, archive.open("xb") as output:
                shutil.copyfileobj(response, output)
        digest = hashlib.sha256()
        with archive.open("rb") as file:
            for chunk in iter(lambda: file.read(1024 * 1024), b""):
                digest.update(chunk)
        if digest.hexdigest() != CHROME_SHA256[target]:
            raise RuntimeError("Контрольная сумма Chrome не совпала. Браузер не установлен.")
        unpacked = temp / "unpacked"
        with zipfile.ZipFile(archive) as bundle:
            for member in bundle.infolist():
                path = Path(member.filename.replace("\\", "/"))
                if path.is_absolute() or ".." in path.parts or ":" in member.filename:
                    raise RuntimeError("Недопустимый путь в архиве Chrome.")
            if sys.platform == "darwin":
                # ditto сохраняет символические ссылки внутри macOS .app.
                subprocess.run(["ditto", "-x", "-k", str(archive), str(unpacked)], check=True)
            else:
                bundle.extractall(unpacked)
        if not chrome_executable(unpacked).is_file():
            raise RuntimeError("В архиве нет исполняемого файла Chrome.")
        (unpacked / ".chrome-version").write_text(CHROME_VERSION + "\n", encoding="utf-8")
        shutil.move(str(unpacked), str(destination))
    return executable


if __name__ == "__main__":
    destination = Path(sys.argv[1]) if len(sys.argv) > 1 else Path.home() / ".poisk-kartinok/chrome" / CHROME_VERSION
    print(install(destination, os.environ.get("POISK_CHROME_ARCHIVE")))
