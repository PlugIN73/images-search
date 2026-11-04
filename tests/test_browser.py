"""Выбор обновлённого Chrome, сохранение профиля и обязательная песочница."""

import hashlib
import importlib.util
import os
import plistlib
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import Mock, patch

os.environ.setdefault("POISK_DATA", tempfile.mkdtemp(prefix="poisk-test-"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import browser_config
import poisk

spec = importlib.util.spec_from_file_location(
    "download_browser", Path(__file__).resolve().parents[1] / "packaging/download_browser.py"
)
download_browser = importlib.util.module_from_spec(spec)
spec.loader.exec_module(download_browser)


class ВыборБраузера(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name).resolve()
        for context in (
            patch.object(browser_config.sys, "frozen", False, create=True),
            patch.object(browser_config.sys, "platform", "darwin"),
            patch.object(browser_config.platform, "machine", return_value="arm64"),
            patch.object(browser_config.Path, "home", return_value=self.home),
            patch.dict(os.environ, {"POISK_BROWSER_PATH": ""}),
        ):
            context.start()
            self.addCleanup(context.stop)

    def chrome(self, path, version=browser_config.CHROME_VERSION):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"browser")
        (path.parent.parent / "Info.plist").write_bytes(
            plistlib.dumps({"CFBundleShortVersionString": version})
        )
        return path

    def test_использует_обновлённую_установку(self):
        executable = self.chrome(self.home / "Applications/YVFactory/Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing")
        self.assertEqual(browser_config.найти_браузер(), executable)

    def test_старый_кэш_playwright_не_используется(self):
        self.chrome(self.home / "Library/Caches/ms-playwright/chromium-1243/chrome-mac-arm64/Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing", "153.0.8010.12")
        with self.assertRaisesRegex(RuntimeError, "Chrome не найден"):
            browser_config.найти_браузер()

    def test_источник_использует_закреплённую_копию_после_обновления(self):
        executable = self.chrome(browser_config.chrome_executable(self.home / ".poisk-kartinok/chrome" / browser_config.CHROME_VERSION))
        self.assertEqual(browser_config.найти_браузер(), executable)

    def test_явный_старый_браузер_отклоняется(self):
        executable = self.chrome(self.home / "old.app/Contents/MacOS/Chrome", "153.0.8010.12")
        with patch.dict(os.environ, {"POISK_BROWSER_PATH": str(executable)}):
            with self.assertRaisesRegex(RuntimeError, "Нужен Chrome"):
                browser_config.найти_браузер()

    def test_готовое_приложение_использует_свой_chrome(self):
        app_executable = self.home / "Поиск картинок.app/Contents/MacOS/Поиск картинок"
        executable = self.chrome(browser_config.chrome_executable(self.home / "Поиск картинок.app/Contents/Resources/chrome"))
        with patch.object(browser_config.sys, "frozen", True), patch.object(browser_config.sys, "executable", str(app_executable)):
            self.assertEqual(browser_config.найти_браузер(), executable)

    def test_windows_путь_внутри_готового_пакета(self):
        executable = self.home / "_internal/chrome/chrome-win64/chrome.exe"
        executable.parent.mkdir(parents=True)
        executable.write_bytes(b"browser")
        with patch.object(browser_config.sys, "platform", "win32"), patch.object(browser_config.sys, "frozen", True), patch.object(browser_config.sys, "executable", str(self.home / "Poisk-kartinok.exe")):
            self.assertEqual(browser_config.найти_браузер(), executable)


class ЗапускБраузера(unittest.TestCase):
    def setUp(self):
        self.pw = Mock()
        self.context = self.pw.chromium.launch_persistent_context.return_value
        self.context.browser.version = browser_config.CHROME_VERSION
        self.finder = patch.object(poisk, "найти_браузер", return_value=Path("/updated/chrome"))
        self.finder.start()
        self.addCleanup(self.finder.stop)

    def test_профиль_сохраняется_и_песочница_включена(self):
        profile = "/existing/.браузер2"
        self.assertIs(poisk.открыть_браузер(self.pw, profile), self.context)
        args, kwargs = self.pw.chromium.launch_persistent_context.call_args
        self.assertEqual(args, (profile,))
        self.assertEqual(kwargs["executable_path"], str(Path("/updated/chrome")))
        self.assertIs(kwargs["chromium_sandbox"], True)
        self.assertNotIn("ignore_default_args", kwargs)
        self.assertNotIn("--no-sandbox", kwargs["args"])

    def test_отказ_песочницы_не_запускает_другой_браузер(self):
        self.pw.chromium.launch_persistent_context.side_effect = RuntimeError("sandbox failed")
        with self.assertRaisesRegex(RuntimeError, "с песочницей"):
            poisk.открыть_браузер(self.pw, "/existing/.браузер")
        self.assertEqual(self.pw.chromium.launch_persistent_context.call_count, 1)

    def test_параметры_отключения_песочницы_отклоняются(self):
        for flag in ("--no-sandbox", "--no-sandbox=true", "--disable-setuid-sandbox", "--disable-gpu-sandbox", "--disable-seccomp-filter-sandbox"):
            with self.subTest(flag=flag), self.assertRaisesRegex(RuntimeError, "Отключать песочницу"):
                poisk.открыть_браузер(self.pw, "/existing/.браузер", args=[flag])
        self.pw.chromium.launch_persistent_context.assert_not_called()

    def test_старый_браузер_закрывается_до_перехода_на_сайты(self):
        self.context.browser.version = "156.0.8078.4"
        with self.assertRaisesRegex(RuntimeError, "Нужен Chrome"):
            poisk.открыть_браузер(self.pw, "/existing/.браузер")
        self.context.close.assert_called_once_with()


class УстановкаБраузера(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def archive(self, entries):
        archive = self.root / "chrome.zip"
        with zipfile.ZipFile(archive, "w") as bundle:
            for name, data in entries.items():
                bundle.writestr(name, data)
        return archive

    def test_установка_проверяет_хеш_и_не_трогает_профили(self):
        archive = self.archive({"chrome-win64/chrome.exe": b"chrome"})
        profile = self.root / ".браузер"
        profile.mkdir()
        (profile / "Cookies").write_bytes(b"existing profile")
        digest = hashlib.sha256(archive.read_bytes()).hexdigest()
        with patch.object(browser_config.sys, "platform", "win32"), patch.dict(download_browser.CHROME_SHA256, {"win64": digest}):
            executable = download_browser.install(self.root / "installed", archive)
            self.assertEqual(executable.read_bytes(), b"chrome")
            with patch.object(download_browser.urllib.request, "urlopen", side_effect=AssertionError("no download")):
                self.assertEqual(download_browser.install(self.root / "installed"), executable)
        self.assertEqual((profile / "Cookies").read_bytes(), b"existing profile")

    def test_неверный_хеш_не_распаковывается(self):
        archive = self.archive({"chrome-win64/chrome.exe": b"wrong"})
        with patch.object(browser_config.sys, "platform", "win32"):
            with self.assertRaisesRegex(RuntimeError, "сумма"):
                download_browser.install(self.root / "installed", archive)
        self.assertFalse((self.root / "installed").exists())

    def test_отсутствующий_входной_архив_не_создаётся(self):
        archive = self.root / "missing.zip"
        with self.assertRaisesRegex(RuntimeError, "архив Chrome не найден"):
            download_browser.install(self.root / "installed", archive)
        self.assertFalse(archive.exists())

    def test_архив_не_может_выйти_за_папку(self):
        archive = self.archive({"../outside": b"wrong"})
        digest = hashlib.sha256(archive.read_bytes()).hexdigest()
        with patch.object(browser_config.sys, "platform", "win32"), patch.dict(download_browser.CHROME_SHA256, {"win64": digest}):
            with self.assertRaisesRegex(RuntimeError, "Недопустимый путь"):
                download_browser.install(self.root / "installed", archive)
        self.assertFalse((self.root / "outside").exists())


if __name__ == "__main__":
    unittest.main()
