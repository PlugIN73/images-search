# -*- coding: utf-8 -*-
"""Окно программы «Поиск картинок».

Само окно — страница на HTML (папка ui/), её обслуживает маленький сервер (most.py).
Показываем её в окне pywebview: на Mac это встроенный в систему WebKit, на Windows —
WebView2. Если WebView2 в Windows нет (бывает на старых Windows 10), окно открывается
во встроенном Chromium, который и так лежит в пакете ради поиска.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.parse
import urllib.request
import webbrowser
from datetime import datetime
from pathlib import Path

import most
import poisk

ФАЙЛ_СОСТОЯНИЯ = poisk.СЛУЖЕБНАЯ / ".кадры.json"
ФАЙЛ_ЖУРНАЛА = poisk.СЛУЖЕБНАЯ / "журнал.txt"
ФАЙЛ_ОКНА = poisk.СЛУЖЕБНАЯ / ".окно.json"     # адрес уже открытого окна — для второго запуска
МАКС_ЖУРНАЛ = 2 * 1024 * 1024
НА_WINDOWS = sys.platform.startswith("win")
НА_MAC = sys.platform == "darwin"
ПАПКА_UI = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent)) / "ui"
ЗАГОЛОВОК = f"Поиск картинок в Яндексе — версия {poisk.ВЕРСИЯ}"

ПЕРЕВОД = {
    "global.quitConfirmation": "Поиск ещё идёт. Остановить его и закрыть программу?\n"
                               "Скачанное сохранится, а при следующем запуске уже скачанное пропустится.",
    "global.ok": "ОК", "global.quit": "Закрыть", "global.cancel": "Отмена",
    "global.saveFile": "Сохранить файл",
    "cocoa.menu.about": "О программе", "cocoa.menu.services": "Службы", "cocoa.menu.view": "Вид",
    "cocoa.menu.edit": "Правка", "cocoa.menu.hide": "Скрыть", "cocoa.menu.hideOthers": "Скрыть остальные",
    "cocoa.menu.showAll": "Показать все", "cocoa.menu.quit": "Завершить", "cocoa.menu.fullscreen": "Во весь экран",
    "cocoa.menu.cut": "Вырезать", "cocoa.menu.copy": "Копировать", "cocoa.menu.paste": "Вставить",
    "cocoa.menu.selectAll": "Выбрать всё",
}


def в_журнал(msg):
    """Пишет строку в журнал.txt; когда файл разрастается — начинает новый."""
    try:
        if ФАЙЛ_ЖУРНАЛА.exists() and ФАЙЛ_ЖУРНАЛА.stat().st_size > МАКС_ЖУРНАЛ:
            ФАЙЛ_ЖУРНАЛА.replace(ФАЙЛ_ЖУРНАЛА.with_name("журнал (старый).txt"))
        with open(ФАЙЛ_ЖУРНАЛА, "a", encoding="utf-8") as f:
            f.write(msg + "\n")
    except Exception:
        pass


def в_консоль(msg):
    # В готовом приложении для Windows консоли нет вовсе (sys.stdout = None).
    if sys.stdout is not None:
        try:
            print(msg, flush=True)
        except Exception:
            pass


def открыть(путь):
    """Открыть файл или папку программой, которая открывает их в системе."""
    if НА_MAC:
        subprocess.Popen(["open", str(путь)])
    elif НА_WINDOWS:
        os.startfile(str(путь))   # noqa
    else:
        subprocess.Popen(["xdg-open", str(путь)])


def буфер_обмена():
    """Текст из буфера обмена (для кнопки «Вставить из буфера» и ⌘V в русской раскладке)."""
    try:
        if НА_MAC:
            return subprocess.run(["pbpaste"], capture_output=True, timeout=3,
                                  env={**os.environ, "LANG": "en_US.UTF-8"}).stdout.decode("utf-8", "replace")
        if НА_WINDOWS:
            return _буфер_windows()
        return subprocess.run(["xclip", "-o", "-selection", "clipboard"], capture_output=True,
                              timeout=3).stdout.decode("utf-8", "replace")
    except Exception:
        return ""


def _буфер_windows():
    import ctypes
    from ctypes import wintypes
    user32, kernel32 = ctypes.windll.user32, ctypes.windll.kernel32
    user32.OpenClipboard.argtypes = [wintypes.HWND]
    user32.GetClipboardData.restype = wintypes.HANDLE
    kernel32.GlobalLock.argtypes = [wintypes.HGLOBAL]
    kernel32.GlobalLock.restype = ctypes.c_void_p
    kernel32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
    if not user32.OpenClipboard(None):
        return ""
    try:
        данные = user32.GetClipboardData(13)          # CF_UNICODETEXT
        if not данные:
            return ""
        указатель = kernel32.GlobalLock(данные)
        try:
            return ctypes.wstring_at(указатель) if указатель else ""
        finally:
            kernel32.GlobalUnlock(данные)
    finally:
        user32.CloseClipboard()


def сообщить_системно(текст):
    """Короткое сообщение без окна программы (например, при втором запуске)."""
    try:
        if НА_MAC:
            subprocess.run(["osascript", "-e", f'display dialog {json.dumps(текст)} buttons {{"ОК"}} '
                            f'default button 1 with title {json.dumps("Поиск картинок")}'], timeout=120)
        elif НА_WINDOWS:
            import ctypes
            ctypes.windll.user32.MessageBoxW(None, текст, "Поиск картинок", 0x40)
    except Exception:
        pass


def мигнуть_windows(hwnd):
    """Мигать кнопкой программы на панели задач, пока её не откроют."""
    import ctypes
    from ctypes import wintypes

    class FLASHWINFO(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.UINT), ("hwnd", wintypes.HWND), ("dwFlags", wintypes.DWORD),
                    ("uCount", wintypes.UINT), ("dwTimeout", wintypes.DWORD)]
    FLASHW_ALL, FLASHW_TIMERNOFG = 0x3, 0xC
    сведения = FLASHWINFO(ctypes.sizeof(FLASHWINFO), hwnd, FLASHW_ALL | FLASHW_TIMERNOFG, 0, 0)
    ctypes.windll.user32.FlashWindowEx(ctypes.byref(сведения))


def есть_webview2():
    """Установлен ли в Windows движок WebView2. Без него pywebview открыл бы окно
    в Internet Explorer, где страница не работает."""
    import winreg
    ключ = r"Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}"
    for корень, путь in ((winreg.HKEY_LOCAL_MACHINE, rf"SOFTWARE\WOW6432Node\{ключ}"),
                         (winreg.HKEY_LOCAL_MACHINE, rf"SOFTWARE\{ключ}"),
                         (winreg.HKEY_CURRENT_USER, rf"SOFTWARE\{ключ}")):
        try:
            with winreg.OpenKey(корень, путь) as k:
                версия, _ = winreg.QueryValueEx(k, "pv")
                if версия and версия != "0.0.0.0":
                    return True
        except OSError:
            pass
    return False


def тёмная_тема():
    """Тёмная ли тема в системе — чтобы окно не вспыхивало белым, пока грузится страница."""
    try:
        if НА_MAC:
            return subprocess.run(["defaults", "read", "-g", "AppleInterfaceStyle"], capture_output=True,
                                  text=True, timeout=2).stdout.strip() == "Dark"
        if НА_WINDOWS:
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                                r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize") as k:
                return winreg.QueryValueEx(k, "AppsUseLightTheme")[0] == 0
    except Exception:
        pass
    return False


class Действия:
    """То, что умеет только настоящее окно; мост (most.py) просит об этом от имени страницы."""

    def __init__(self, папка_результатов):
        self.папка = папка_результатов
        self.окно = None              # окно pywebview, если оно есть
        self.закрыто = threading.Event()   # запасное окно в Chromium сообщило, что его закрыли

    def открыть_папку(self):
        открыть(self.папка)

    def открыть_файл(self, путь):
        открыть(путь)

    def открыть_ссылку(self, ссылка):
        webbrowser.open(ссылка)

    def буфер(self):
        return буфер_обмена()

    def в_журнал(self, текст):
        в_консоль(текст)
        в_журнал(текст)

    def звук(self):
        poisk.звук()

    def окно_закрыто(self):
        self.закрыто.set()

    def окно_открыто(self):
        # страница загрузилась снова — значит, это была перезагрузка (Cmd+R), а не закрытие
        self.закрыто.clear()

    def поиск_идёт(self, да):
        # Пока идёт поиск, закрытие окна спрашивает «точно?»
        if self.окно is not None:
            self.окно.confirm_close = да

    def показать(self):
        """Вывести окно вперёд. False — если окна pywebview нет (окно открыто в Chromium)."""
        if self.окно is None:
            return False
        self.окно.restore()
        self.окно.show()
        if НА_WINDOWS:
            # Windows не даёт чужому окну просто так выйти вперёд — ненадолго «поверх всех»
            self.окно.on_top = True
            self.окно.on_top = False
        self._на_главном_потоке(lambda AppKit: AppKit.NSApp.activateIgnoringOtherApps_(True))
        return True

    def внимание(self):
        """Капча: свёрнутое окно разворачивается; на Mac подпрыгивает значок в Dock,
        на Windows мигает кнопка на панели задач. Поверх всех окно не выходит —
        иначе закрыло бы окно браузера с галочкой «Я не робот»."""
        if self.окно is None:
            return
        try:
            self.окно.restore()
        except Exception:
            pass
        if НА_MAC:
            self._на_главном_потоке(lambda AppKit: AppKit.NSApp.requestUserAttention_(AppKit.NSCriticalRequest))
        elif НА_WINDOWS:
            try:
                мигнуть_windows(self.окно.native.Handle.ToInt64())
            except Exception:
                pass

    @staticmethod
    def _на_главном_потоке(f):
        """На Mac окном можно управлять только из главного потока."""
        if not НА_MAC:
            return
        try:
            import AppKit
            from PyObjCTools import AppHelper
            AppHelper.callAfter(f, AppKit)
        except Exception:
            pass


# ---------- окно ----------
def открыть_в_pywebview(сервер, действия):
    import webview
    окно = webview.create_window(
        ЗАГОЛОВОК, сервер.адрес_окна, width=1040, height=780, min_size=(760, 560),
        background_color="#161618" if тёмная_тема() else "#faf9f7", text_select=True)
    действия.окно = окно
    webview.start(localization=ПЕРЕВОД)


def открыть_в_chromium(сервер, действия):
    """Запасной путь: окно во встроенном Chromium, в режиме приложения (без адресной строки).

    Профиль каждый раз новый: иначе уже работающий Chromium с тем же профилем «забрал» бы
    окно себе, а наш процесс сразу завершился. Конец работы — когда страница сообщила, что её
    закрыли (на Mac сам Chromium после закрытия окна продолжает жить), или когда Chromium вышел."""
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        chromium = pw.chromium.executable_path
    for старый in poisk.СЛУЖЕБНАЯ.glob(".окно-chromium*"):      # остались после сбоя прошлого запуска
        shutil.rmtree(старый, ignore_errors=True)
    профиль = tempfile.mkdtemp(prefix=".окно-chromium-", dir=poisk.СЛУЖЕБНАЯ)
    процесс = subprocess.Popen([chromium, f"--app={сервер.адрес_окна}", f"--user-data-dir={профиль}",
                                "--window-size=1040,780", "--no-first-run", "--no-default-browser-check",
                                "--disable-features=Translate",
                                # не лезть в связку ключей macOS: пароли окну не нужны, а вопрос пугает
                                "--use-mock-keychain", "--password-store=basic"])
    try:
        while процесс.poll() is None:
            if действия.закрыто.wait(0.5):
                # после перезагрузки страница за пару секунд снова спросит состояние и снимет флаг
                time.sleep(3)
                if действия.закрыто.is_set():
                    break
    finally:
        if процесс.poll() is None:
            процесс.terminate()
            try:
                процесс.wait(10)
            except subprocess.TimeoutExpired:
                процесс.kill()
        shutil.rmtree(профиль, ignore_errors=True)


def нужен_chromium():
    if os.environ.get("POISK_OKNO") == "chromium":
        return True
    return НА_WINDOWS and not есть_webview2()


def показать_уже_открытое():
    """Программу запустили второй раз — просим первое окно показаться.
    Если оно не может (или не отвечает) — хотя бы говорим, что программа уже открыта."""
    try:
        окно = json.loads(ФАЙЛ_ОКНА.read_text(encoding="utf-8"))
        запрос = urllib.request.Request(окно["адрес"] + "api/" + urllib.parse.quote("показать"),
                                        data=b"{}", headers={"X-Token": окно["ключ"]}, method="POST")
        if json.loads(urllib.request.urlopen(запрос, timeout=3).read()).get("показано"):
            return
    except Exception:
        pass
    сообщить_системно("Программа уже запущена — поищи её окно среди открытых.")


def main():
    замок = poisk.занять_замок(poisk.СЛУЖЕБНАЯ / ".запущено")
    if замок is None:
        показать_уже_открытое()
        return 0

    действия = Действия(poisk.ПАПКА_РЕЗУЛЬТАТОВ)
    прил = most.Приложение(poisk.ПАПКА_РЕЗУЛЬТАТОВ, ФАЙЛ_СОСТОЯНИЯ, действия=действия)
    сервер = most.Сервер(прил, ПАПКА_UI)
    сервер.запустить()
    try:
        ФАЙЛ_ОКНА.write_text(json.dumps({"адрес": сервер.адрес, "ключ": сервер.ключ}), encoding="utf-8")
    except OSError:
        pass
    в_журнал(f"\n=== Запуск {datetime.now():%d.%m.%Y %H:%M}, версия {poisk.ВЕРСИЯ} ===")

    def проверить_обновление():
        новая = poisk.проверить_обновление()
        if новая:
            прил.обновление = новая
            прил.событие("обновление", версия=новая[0], ссылка=новая[1])
            в_журнал(f"Вышла новая версия {новая[0]}. Скачать: {новая[1]}")
    threading.Thread(target=проверить_обновление, daemon=True).start()

    try:
        if нужен_chromium():
            открыть_в_chromium(сервер, действия)
        else:
            try:
                открыть_в_pywebview(сервер, действия)
            except Exception as e:
                в_журнал(f"Окно pywebview не открылось ({e}), открываю во встроенном Chromium.")
                действия.окно = None
                открыть_в_chromium(сервер, действия)
    finally:
        прил.остановить()
        сервер.остановить()
        ФАЙЛ_ОКНА.unlink(missing_ok=True)      # ключ окна на диске больше не нужен
        poisk.отпустить_замок(замок)
    return 0


# ---------- самопроверка собранного пакета ----------
def _самопроверка(путь_отчёта):
    """Запуск с ключом --self-test: проверка собранного пакета без участия человека."""
    строки = []

    def записать(msg=""):
        строки.append(str(msg))
        в_консоль(msg)

    poisk.ВЫВОД[0] = записать
    хорошо = poisk.самопроверка()

    нет = [ф for ф in ("index.html", "app.js", "app.css", "fonts/onest-cyrillic.woff2")
           if not (ПАПКА_UI / ф).is_file()]
    if нет:
        записать(f"Окно: ОШИБКА — нет файлов {', '.join(нет)} в {ПАПКА_UI}")
        хорошо = False
    else:
        записать("Окно (файлы страницы): ок")

    try:
        with tempfile.TemporaryDirectory() as папка:
            прил = most.Приложение(Path(папка), Path(папка) / ".кадры.json")
            сервер = most.Сервер(прил, ПАПКА_UI)
            сервер.запустить()
            try:
                страница = urllib.request.urlopen(сервер.адрес_окна, timeout=5).read().decode("utf-8")
                if сервер.ключ not in страница or "app.js" not in страница:
                    raise RuntimeError("страница пришла не та")
                записать("Окно (сервер страницы): ок")
            finally:
                сервер.остановить()
    except Exception as e:
        записать(f"Окно (сервер страницы): ОШИБКА {e!r}")
        хорошо = False

    try:
        # тот же модуль, которым окно откроется на этой системе: проверяем, что он попал в пакет
        import importlib
        importlib.import_module("webview.platforms.cocoa" if НА_MAC else
                                "webview.platforms.winforms" if НА_WINDOWS else "webview")
        if НА_WINDOWS:
            # здесь загружаются .NET-библиотеки WebView2 — то, что ломает отметка «из интернета»
            importlib.import_module("webview.platforms.edgechromium")
        движок = ""
        if НА_WINDOWS:
            движок = ", WebView2 есть" if есть_webview2() else ", WebView2 нет — окно откроется в Chromium"
        записать(f"Окно (pywebview): ок{движок}")
    except Exception as e:
        записать(f"Окно (pywebview): ОШИБКА {e!r}")
        хорошо = False

    записать("САМОПРОВЕРКА: " + ("ОК" if хорошо else "ПРОВАЛЕНА"))
    if путь_отчёта:
        with open(путь_отчёта, "w", encoding="utf-8") as f:
            f.write("\n".join(строки) + "\n")
    return 0 if хорошо else 1


if __name__ == "__main__":
    if "--self-test" in sys.argv:
        i = sys.argv.index("--self-test")
        sys.exit(_самопроверка(sys.argv[i + 1] if len(sys.argv) > i + 1 else None))
    sys.exit(main())
