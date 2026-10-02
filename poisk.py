# -*- coding: utf-8 -*-
"""
Поиск картинок в Яндексе по списку кадров + проверка «не ИИ ли это».

Это «движок». Окно программы — в файле okno.py, запуск — «Запустить.command».
"""
import csv
import glob
import hashlib
import io
import os
import random
import re
import subprocess
import sys
import tempfile
import threading
import time
import urllib.parse
from datetime import datetime
from pathlib import Path

import requests
from PIL import Image

ВЕРСИЯ = "1.0.1"
РЕПОЗИТОРИЙ = "PlugIN73/images-search"

# ---------------- Настройки ----------------
КАРТИНОК_НА_ЗАПРОС = 2    # сколько картинок скачивать на каждый запрос
МИН_РАЗМЕР = 400          # минимальная сторона картинки в пикселях
МАКС_ФАЙЛ = 30 * 1024 * 1024   # картинки больше 30 МБ не качаем
# -------------------------------------------

СОБРАНО = getattr(sys, "frozen", False)      # запущено как готовое приложение (.app / .exe)
ПАПКА = Path(sys.executable if СОБРАНО else __file__).resolve().parent

if СОБРАНО:
    # Свой Chromium лежит внутри пакета: на Mac — в .app/Contents/Resources,
    # на Windows — в служебной папке _internal рядом с .exe. В систему ничего не ставится.
    for _встроенный in (ПАПКА.parent / "Resources" / "ms-playwright", ПАПКА / "_internal" / "ms-playwright"):
        if _встроенный.is_dir():
            os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(_встроенный)
            break


def _во_временной_папке(путь):
    """Windows умеет запускать программу прямо из zip-архива — тогда она живёт
    во временной папке, и всё сохранённое потом не найти."""
    try:
        return str(путь).lower().startswith(str(Path(tempfile.gettempdir()).resolve()).lower())
    except Exception:
        return False


def _можно_писать(путь):
    try:
        путь.mkdir(parents=True, exist_ok=True)
        проба = путь / ".проба"
        проба.write_text("ok", encoding="utf-8")
        проба.unlink()
        return True
    except Exception:
        return False


def выбрать_папки(собрано, на_windows, папка_программы, дом, из_окружения=None):
    """Возвращает (база, служебная): в базе лежат «Результаты», в служебной —
    журнал, список кадров и профили браузера.
    - из исходников: всё рядом с программой;
    - готовое приложение на Windows: «Результаты» рядом с .exe, служебное — в _internal;
    - готовое приложение на Mac (или если рядом с .exe писать нельзя): Документы."""
    if из_окружения:
        варианты = [Path(из_окружения)]
    else:
        варианты = []
        if (not собрано or на_windows) and not _во_временной_папке(папка_программы):
            варианты.append(Path(папка_программы))
        варианты += [Path(дом) / "Documents" / "Поиск картинок", Path(дом) / "Поиск картинок"]
    for база in варианты:
        if not _можно_писать(база):
            continue
        if собрано and на_windows and база == Path(папка_программы):
            служебная = база / "_internal" / "данные"
            if _можно_писать(служебная):
                return база, служебная
            continue
        return база, база
    запасная = Path(дом) / "Documents"
    return запасная, запасная


БАЗА, СЛУЖЕБНАЯ = выбрать_папки(СОБРАНО, sys.platform.startswith("win"), ПАПКА,
                                Path.home(), os.environ.get("POISK_DATA"))
ПАПКА_РЕЗУЛЬТАТОВ = БАЗА / "Результаты"

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36")


ВЫВОД = [lambda msg: print(msg, flush=True)]
# События для окна: («начало» | «ищу» | «запрос» | «капча» | «капча_прошла» | «конец», данные)
СОБЫТИЯ = [lambda тип, данные: None]


def say(msg=""):
    ВЫВОД[0](msg)


def событие(тип, **данные):
    try:
        СОБЫТИЯ[0](тип, данные)
    except Exception:
        pass


def звук():
    try:
        if sys.platform == "darwin":
            subprocess.Popen(["afplay", "/System/Library/Sounds/Glass.aiff"])
        elif sys.platform.startswith("win"):
            import winsound
            winsound.MessageBeep()
        else:
            print("\a", end="", flush=True)
    except Exception:
        pass


НОМЕР_В_НАЧАЛЕ = re.compile(r"^\s*(\d+)\s*[-–—.):]\s*(.+)$")


def разделить_номер(текст):
    """«4 - Ан-225 хвост» → ("4", "Ан-225 хвост"). Если номера нет → (None, текст)."""
    m = НОМЕР_В_НАЧАЛЕ.match(текст or "")
    return (m.group(1), m.group(2).strip()) if m else (None, (текст or "").strip())


ЗАПРЕТНЫЕ_ИМЕНА = {"CON", "PRN", "AUX", "NUL",
                  *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}


def безопасное_имя(s):
    s = re.sub(r'[\\/:*?"<>|\n\r\t]+', " ", s)
    # Windows молча отрезает пробелы и точки в конце — обрезаем сами, уже после укорачивания
    s = s.strip()[:80].strip(" .")
    if s.split(".")[0].upper() in ЗАПРЕТНЫЕ_ИМЕНА:   # con.jpg на Windows — это устройство, не файл
        s = "_" + s
    return s or "без названия"


# ---------- браузер и Яндекс ----------
def это_капча(page):
    try:
        url = page.url
        if "showcaptcha" in url or "captcha" in url.split("?")[0]:
            return True
        return page.locator("form#checkbox-captcha-form, .CheckboxCaptcha, .AdvancedCaptcha").count() > 0
    except Exception:
        return False


ЖДАТЬ_КАПЧУ = 600   # секунд ждём, пока человек поставит галочку «Я не робот»


def ждать_капчу(page, ритм=None):
    if not это_капча(page):
        return
    if ритм is not None:
        ритм.была_капча()
    звук()
    событие("капча", ждать=ЖДАТЬ_КАПЧУ)
    say("\n  >>> Яндекс спрашивает «Я не робот». Нажми галочку в окне браузера — я подожду 10 минут.")
    старт = time.time()
    try:
        while это_капча(page):
            if time.time() - старт > ЖДАТЬ_КАПЧУ:
                say("  Ждал 10 минут, пропускаю этот запрос.")
                return
            time.sleep(1.5)
    finally:
        событие("капча_прошла")   # и когда браузер закрылся посреди ожидания — иначе плашка не уберётся
    say("  Спасибо, продолжаю.\n")
    time.sleep(2)


def _чистые(список):
    итог, видели = [], set()
    for u in список:
        u = (u or "").replace("&amp;", "&")
        if not u.startswith("http"):
            continue
        try:
            if "yandex" in urllib.parse.urlparse(u).netloc:
                continue
        except Exception:
            continue
        if u not in видели:
            видели.add(u)
            итог.append(u)
    return итог


def ссылки_из_сетки(hrefs):
    """Адреса оригиналов из ссылок самой сетки результатов — ровно в порядке выдачи."""
    найдено = []
    for h in hrefs:
        m = re.search(r"img_url=([^&]+)", h or "")
        if m:
            найдено.append(urllib.parse.unquote(urllib.parse.unquote(m.group(1))))
    return _чистые(найдено)


def ссылки_из_страницы(html):
    """Запасной способ: выдрать адреса из кода страницы. Порядок здесь хуже."""
    найдено = []
    for m in re.finditer(r'img_url=([^&"\'\s]+)', html):
        найдено.append(urllib.parse.unquote(urllib.parse.unquote(m.group(1))))
    for m in re.finditer(r'"(?:origUrl|img_href)":"(https?:[^"]+?\.(?:jpe?g|png|webp)[^"]*)"', html, re.I):
        найдено.append(m.group(1).encode().decode("unicode_escape", "ignore").replace("\\/", "/"))
    return _чистые(найдено)


def найти_ссылки(page, запрос, нужно, ритм=None, ждать=True):
    """Возвращает адреса картинок В ПОРЯДКЕ ВЫДАЧИ Яндекса."""
    url = "https://yandex.ru/images/search?" + urllib.parse.urlencode({"text": запрос})
    for попытка in range(3):
        try:
            page.goto(url, wait_until="commit", timeout=25000)
            break
        except Exception as e:
            # Свежий профиль браузера иногда перебивает первый переход
            # (своей страницей ошибки или повтором того же адреса) — это не беда.
            if "interrupted by another navigation" in str(e) and "chrome-error" not in str(e):
                break
            if попытка == 2:
                raise
            time.sleep(2)
    try:
        page.wait_for_selector("a[href*='img_url='], form#checkbox-captcha-form, .CheckboxCaptcha, .AdvancedCaptcha",
                               timeout=15000)
    except Exception:
        pass
    if ждать:
        ждать_капчу(page, ритм)
    ссылки = []
    for _ in range(3):
        try:
            hrefs = page.eval_on_selector_all("a[href*='img_url=']", "els => els.map(e => e.href)")
        except Exception:
            hrefs = []
        ссылки = ссылки_из_сетки(hrefs)
        if len(ссылки) >= нужно:
            break
        page.mouse.wheel(0, 2500)
        time.sleep(1)
    if len(ссылки) < нужно:            # сетка не прочиталась — запасной способ
        try:
            запас = ссылки_из_страницы(page.content())
        except Exception:
            запас = []
        ссылки = ссылки + [u for u in запас if u not in set(ссылки)]
    if ритм is not None and ссылки:
        ритм.всё_хорошо()
    return ссылки


def _варианты_браузера():
    """None — свой Chromium (встроенный в пакет); системные Edge и Chrome — запасные."""
    if sys.platform.startswith("win"):
        return [None, "msedge", "chrome"]
    return [None, "chrome"]


ИСПОЛЬЗУЕМЫЙ_БРАУЗЕР = [""]


def открыть_браузер(pw, профиль, headless=False, args=()):
    """Запускает браузер с постоянным профилем, перебирая доступные варианты."""
    ошибки = []
    for канал in _варианты_браузера():
        try:
            # без окна Playwright ищет отдельный headless-браузер; «chromium» — значит обычный
            ctx = pw.chromium.launch_persistent_context(
                профиль, channel=канал or ("chromium" if headless else None), headless=headless, locale="ru-RU",
                viewport={"width": 1100, "height": 800}, args=list(args))
            ИСПОЛЬЗУЕМЫЙ_БРАУЗЕР[0] = канал or "встроенный Chromium"
            if канал and ошибки:
                say(f"  Внимание: встроенный браузер не запустился, работаю через {канал}.\n"
                    f"  Если что-то пойдёт не так — скачай программу заново.\n  ({ошибки[0]})")
            return ctx
        except Exception as e:
            текст = str(e)
            if "ProcessSingleton" in текст or "SingletonLock" in текст or "already in use" in текст:
                raise RuntimeError("браузер с этим профилем уже открыт — похоже, программа запущена "
                                   "дважды. Закрой лишнюю копию.") from None
            ошибки.append(f"{канал or 'chromium'}: {текст.splitlines()[0] if текст else e!r}")
    raise RuntimeError("не смог запустить браузер. Попробуй скачать программу заново.\n  "
                       + "\n  ".join(ошибки))


# ---------- отметка «скачано из интернета» ----------
def снять_отметку_интернета(папка):
    """Windows помечает каждый файл из скачанного zip (поток Zone.Identifier).
    С такой отметкой .NET не загружает библиотеки WebView2, и окно программы
    не открывается. Снимаем её со своих файлов — как галочка «Разблокировать»
    в свойствах. Возвращает, сколько отметок снято."""
    if not sys.platform.startswith("win"):
        return 0
    снято = 0
    for корень, _, файлы in os.walk(папка):
        for имя in файлы:
            try:
                os.remove(os.path.join(корень, имя) + ":Zone.Identifier")
                снято += 1
            except OSError:
                pass
    return снято


# ---------- одна копия программы ----------
def занять_замок(путь):
    """Не даёт запустить программу дважды с одной папкой данных: два окна
    делили бы профили браузера и отчёт. Замок снимается сам, когда процесс
    завершается (даже аварийно). Возвращает открытый файл или None, если занято."""
    try:
        f = open(путь, "a+")
    except OSError:
        return None
    try:
        if sys.platform.startswith("win"):
            import msvcrt
            f.seek(0)
            msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        return f
    except OSError:
        f.close()
        return None


def отпустить_замок(f):
    try:
        if sys.platform.startswith("win"):
            import msvcrt
            f.seek(0)
            msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
        f.close()
    except OSError:
        pass


# ---------- обновления ----------
def _как_числа(версия):
    return tuple(int(ч) for ч in re.findall(r"\d+", версия)[:3])


def проверить_обновление():
    """Есть ли на GitHub версия новее. Возвращает (версия, ссылка) или None."""
    try:
        r = requests.get(f"https://api.github.com/repos/{РЕПОЗИТОРИЙ}/releases/latest",
                         timeout=5, headers={"Accept": "application/vnd.github+json"})
        if r.status_code != 200:
            return None
        данные = r.json()
        новая = (данные.get("tag_name") or "").lstrip("v")
        if новая and _как_числа(новая) > _как_числа(ВЕРСИЯ):
            return новая, данные.get("html_url") or f"https://github.com/{РЕПОЗИТОРИЙ}/releases/latest"
    except Exception:
        pass
    return None


# ---------- самопроверка (для сборки) ----------
def самопроверка(с_яндексом=True):
    """Проверяет, что в собранном пакете всё на месте: браузер запускается,
    картинки открываются. Возвращает True, если всё хорошо."""
    хорошо = True
    say(f"Поиск картинок {ВЕРСИЯ}, {sys.platform}, Python {sys.version.split()[0]}")
    say(f"Папка данных: {БАЗА} (служебная: {СЛУЖЕБНАЯ})")
    try:
        Image.open(io.BytesIO(_тестовая_картинка())).load()
        say("Pillow: ок")
    except Exception as e:
        say(f"Pillow: ОШИБКА {e}")
        хорошо = False
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as pw, tempfile.TemporaryDirectory() as профиль:
            ctx = открыть_браузер(pw, профиль, headless=True)
            page = ctx.pages[0] if ctx.pages else ctx.new_page()
            page.set_content("<p id=x>ok</p>")
            say(f"Браузер: ок ({ИСПОЛЬЗУЕМЫЙ_БРАУЗЕР[0]}"
                f"{', ' + ctx.browser.version if ctx.browser else ''})")
            if СОБРАНО and ИСПОЛЬЗУЕМЫЙ_БРАУЗЕР[0] != "встроенный Chromium":
                say("Браузер: ОШИБКА — встроенный Chromium не запустился, сработал запасной")
                хорошо = False
            if с_яндексом:
                try:
                    ссылки = найти_ссылки(page, "кот на подоконнике", 4, ждать=False)
                    if это_капча(page):
                        say("Яндекс: открылся, но спросил капчу (для проверки это нормально)")
                    else:
                        say(f"Яндекс: найдено ссылок {len(ссылки)}")
                except Exception as e:
                    try:
                        подробно = " ".join(page.inner_text("body").split())[:200]
                    except Exception:
                        подробно = ""
                    say(f"Яндекс: не открылся ({str(e).splitlines()[0]}) {подробно}"
                        " — не считаю ошибкой сборки")
            ctx.close()
    except Exception as e:
        say(f"Браузер: ОШИБКА {e}")
        хорошо = False
    return хорошо


def _тестовая_картинка():
    буфер = io.BytesIO()
    Image.new("RGB", (8, 8), "red").save(буфер, "PNG")
    return буфер.getvalue()


# ---------- скачивание ----------
def скачать(url):
    try:
        with requests.get(url, headers={"User-Agent": UA, "Referer": "https://yandex.ru/"},
                          timeout=(5, 10), stream=True) as r:
            if r.status_code != 200:
                return None
            if int(r.headers.get("Content-Length") or 0) > МАКС_ФАЙЛ:
                return None
            куски, всего = [], 0
            for кусок in r.iter_content(64 * 1024):
                всего += len(кусок)
                if всего > МАКС_ФАЙЛ:
                    return None
                куски.append(кусок)
        data = b"".join(куски)
        if len(data) < 10_000:
            return None
        img = Image.open(io.BytesIO(data))
        img.load()
        if min(img.size) < МИН_РАЗМЕР:
            return None
        return data, img
    except Exception:
        return None


# ---------- умная пауза и капча ----------
class Ритм:
    """Пауза между запросами: маленькая, пока Яндекс не возмущается,
    и большая сразу после проверки «Я не робот»."""

    МИН, МАКС = 0.5, 8.0

    def __init__(self):
        self.пауза = 0.6
        self.тихо_подряд = 0
        self.свободно_с = 0.0      # до этого времени все дорожки ждут
        self.замок = threading.Lock()

    def подождать(self):
        while True:
            with self.замок:
                осталось = self.свободно_с - time.time()
                пауза = self.пауза
            if осталось <= 0:
                break
            time.sleep(min(осталось, 0.5))
        time.sleep(random.uniform(пауза * 0.7, пауза * 1.3))

    def всё_хорошо(self):
        with self.замок:
            self.тихо_подряд += 1
            if self.тихо_подряд >= 8 and self.пауза > self.МИН:
                self.пауза = max(self.МИН, self.пауза * 0.8)
                self.тихо_подряд = 0

    def была_капча(self):
        with self.замок:
            self.пауза = min(self.МАКС, max(3.0, self.пауза * 2))
            self.тихо_подряд = 0
            self.свободно_с = time.time() + 5   # дать всем дорожкам выдохнуть


def собрать(ссылки, сколько, стоп_ф, лог):
    """Качает картинки СТРОГО по порядку выдачи и берёт первые `сколько` удачных."""
    from concurrent.futures import ThreadPoolExecutor
    итог, хэши, пропущено = [], set(), 0
    with ThreadPoolExecutor(max_workers=6) as ex:
        for i in range(0, len(ссылки), 6):
            if стоп_ф() or len(итог) >= сколько:
                break
            пачка = ссылки[i:i + 6]
            for место, (url, res) in enumerate(zip(пачка, ex.map(скачать, пачка)), start=i + 1):
                if len(итог) >= сколько:
                    break
                if not res:
                    пропущено += 1
                    continue
                data, img = res
                h = hashlib.md5(data).hexdigest()
                if h in хэши:
                    continue
                хэши.add(h)
                итог.append((data, img, f"в выдаче {место}-я", url))
    return итог, пропущено


def свободно_гб(путь):
    try:
        import shutil
        return shutil.disk_usage(str(путь)).free / 1024 ** 3
    except Exception:
        return None


def _расширение(img):
    return {"JPEG": "jpg", "PNG": "png", "WEBP": "webp", "GIF": "gif"}.get(img.format, "jpg")


def сохранить_целиком(путь, данные):
    """Записать картинку так, чтобы при закрытии программы посреди записи не осталось
    обрезанного файла: сначала во временный «.~имя», потом переименовать."""
    путь = Path(путь)
    временный = путь.with_name(".~" + путь.name)
    временный.write_bytes(данные)
    os.replace(временный, путь)


def _уже_скачано(имя, сколько):
    """True, если файлы этого запроса уже лежат в «Результатах»."""
    хвосты = [""] if сколько == 1 else [f"-{i}" for i in range(1, сколько + 1)]
    for хвост in хвосты:
        if not list(ПАПКА_РЕЗУЛЬТАТОВ.glob(glob.escape(f"{имя}{хвост}") + ".*")):
            return False
    return True


# Имена, которые программа раньше придумывала сама: в имена файлов они не попадают.
АВТОИМЯ = re.compile(r"\s*(?:Кадр\s*\d+|без названия)\s*", re.I)


def задания_из_кадров(кадры):
    """[{"имя", "ru"}] → [(имя файла, запрос, номер кадра в списке)].
    Номер кадра берётся из поля «Кадр»; в старых списках он стоял в начале запроса."""
    задания = []
    for i, к in enumerate(кадры):
        куски = [ч.strip() for ч in (к.get("ru") or "").split("|") if ч.strip()]
        if not куски:
            continue
        поле = (к.get("имя") or "").strip()
        поле = "" if АВТОИМЯ.fullmatch(поле) else поле
        в_запросе, без_номера = разделить_номер(куски[0])
        # Номер кадра — из поля «Кадр», его человек видит в окне. Число в начале запроса
        # считается номером, только если поле пустое (старые списки) или с тем же номером;
        # иначе это часть запроса («1945. Победа») и ищется как есть.
        if в_запросе and поле in ("", в_запросе):
            куски[0] = без_номера
        номер = поле or в_запросе or ""
        for запрос in куски:
            задания.append((безопасное_имя(f"{номер} - {запрос}" if номер else запрос), запрос, i))
    return задания


class Прогресс:
    """Сколько запросов и кадров готово. Кадр готов, когда готовы все его запросы."""

    def __init__(self, кадры_заданий):
        self.кадр_задания = list(кадры_заданий)
        self.всего = len(self.кадр_задания)
        self.осталось = {}
        for к in self.кадр_задания:
            self.осталось[к] = self.осталось.get(к, 0) + 1
        self.кадров = len(self.осталось)
        self.сделано = 0
        self.кадров_готово = 0
        self.пустые = []
        self.замок = threading.Lock()

    def отметить(self, n, имя, найдено):
        """n — номер задания в том же списке, по которому создан Прогресс."""
        with self.замок:
            кадр = self.кадр_задания[n]
            self.сделано += 1
            self.осталось[кадр] -= 1
            if self.осталось[кадр] == 0:
                self.кадров_готово += 1
            if not найдено:
                self.пустые.append(имя)
            return {"сделано": self.сделано, "всего": self.всего, "кадров_готово": self.кадров_готово,
                    "кадров": self.кадров, "пустые": list(self.пустые)}


def запустить(кадры, сколько=КАРТИНОК_НА_ЗАПРОС, стоп=None, дорожек=3):
    """кадры: список словарей {"имя", "ru"}; дорожек: сколько браузеров сразу."""
    остановлено = lambda: стоп is not None and стоп.is_set()

    задания = задания_из_кадров(кадры)
    нечего_делать = dict(остановлено=False, найдено=0, пустые=[], минут=0, нечего=True)
    if not задания:
        say("Нет ни одного запроса.")
        событие("конец", **нечего_делать)
        return

    ПАПКА_РЕЗУЛЬТАТОВ.mkdir(parents=True, exist_ok=True)
    for недокачанный in ПАПКА_РЕЗУЛЬТАТОВ.glob(".~*"):     # остались, если программу закрыли посреди записи
        недокачанный.unlink(missing_ok=True)
    свободно = свободно_гб(ПАПКА_РЕЗУЛЬТАТОВ)
    if свободно is not None and свободно < 2:
        say(f"МАЛО МЕСТА НА ДИСКЕ: свободно {свободно:.1f} ГБ.\n"
            f"Освободи хотя бы 5 ГБ, иначе программа упадёт на середине.")
    пропущено_готовых = [з for з in задания if _уже_скачано(з[0], сколько)]
    if пропущено_готовых:
        задания = [з for з in задания if з not in пропущено_готовых]
        say(f"Уже скачано раньше и пропущено: {len(пропущено_готовых)}.")
    if not задания:
        say("Всё из списка уже скачано.")
        событие("конец", **нечего_делать)
        return

    дорожек = max(1, min(4, int(дорожек)))
    say(f"Запросов: {len(задания)}. Картинок на запрос: {сколько}. Браузеров сразу: {дорожек}.\n")
    прогресс = Прогресс([з[2] for з in задания])   # номера заданий те же, что в «очереди» ниже
    событие("начало", сделано=0, всего=прогресс.всего, кадров_готово=0, кадров=прогресс.кадров)

    очередь = list(задания)
    индекс = [0]
    замок_очереди = threading.Lock()
    замок_файлов = threading.Lock()
    ритм = Ритм()
    отчёт_путь = ПАПКА_РЕЗУЛЬТАТОВ / f"отчёт {datetime.now():%Y-%m-%d %H-%M}.csv"
    f = open(отчёт_путь, "w", newline="", encoding="utf-8-sig")
    w = csv.writer(f, delimiter=";")
    w.writerow(["файл", "запрос", "пометка", "откуда"])
    счёт = {"готово": 0, "пусто": 0}
    старт = time.time()

    def дорожка(номер_дорожки):
        from playwright.sync_api import sync_playwright
        метка = f"[{номер_дорожки}]" if дорожек > 1 else ""
        текущее = None    # взятое, но ещё не отмеченное задание: если браузер упадёт, отметим его пустым
        try:
            with sync_playwright() as pw:
                профиль = str(СЛУЖЕБНАЯ / f".браузер{номер_дорожки if номер_дорожки > 1 else ''}")
                ctx = открыть_браузер(pw, профиль, args=[
                    f"--window-position={40 + 60 * номер_дорожки},{40 + 40 * номер_дорожки}",
                    "--disk-cache-size=33554432",     # кеш браузера не больше 32 МБ
                    "--media-cache-size=16777216",
                    "--disable-application-cache"])
                page = ctx.pages[0] if ctx.pages else ctx.new_page()
                while not остановлено():
                    with замок_очереди:
                        if индекс[0] >= len(очередь):
                            break
                        n = индекс[0]
                        индекс[0] += 1
                        имя, запрос, _ = очередь[n]
                        текущее = n
                    say(f"{метка}[{n + 1}/{len(очередь)}] {имя}")
                    событие("ищу", имя=имя)
                    ритм.подождать()
                    try:
                        ссылки = найти_ссылки(page, запрос, сколько * 8 + 8, ритм)
                    except Exception as e:
                        say(f"{метка}  не удалось открыть Яндекс ({str(e).splitlines()[0]})")
                        текущее = None
                        событие("запрос", имя=имя, найдено=False, **прогресс.отметить(n, имя, False))
                        continue
                    найдено, битых = собрать(ссылки, сколько, остановлено, say)
                    for i, (data, img, почему, url) in enumerate(найдено, 1):
                        файл = f"{имя}-{i}.{_расширение(img)}" if сколько > 1 else f"{имя}.{_расширение(img)}"
                        try:
                            with замок_файлов:
                                сохранить_целиком(ПАПКА_РЕЗУЛЬТАТОВ / файл, data)
                                w.writerow([файл, запрос, почему, url])
                                f.flush()
                        except OSError as e:
                            if getattr(e, "errno", None) == 28:
                                say("\nНА ДИСКЕ ЗАКОНЧИЛОСЬ МЕСТО. Останавливаюсь, чтобы ничего не испортить.\n"
                                    "Освободи место и запусти снова — уже скачанное пропустится.")
                                if стоп is not None:
                                    стоп.set()
                            else:
                                say(f"  не смог сохранить файл: {e}")
                            break
                    with замок_файлов:
                        счёт["готово" if найдено else "пусто"] += 1
                    хвост = f", пропущено битых: {битых}" if битых else ""
                    say(f"{метка}  скачано {len(найдено)} из {сколько}{хвост}")
                    текущее = None
                    событие("запрос", имя=имя, найдено=bool(найдено), **прогресс.отметить(n, имя, bool(найдено)))
                ctx.close()
        except Exception as e:
            if текущее is not None:
                имя = очередь[текущее][0]
                событие("запрос", имя=имя, найдено=False, **прогресс.отметить(текущее, имя, False))
            ещё = " Остальные браузеры продолжают." if дорожек > 1 else ""
            say(f"{метка} браузер №{номер_дорожки} остановился: {e}{ещё}")

    потоки = [threading.Thread(target=дорожка, args=(i,), daemon=True) for i in range(1, дорожек + 1)]
    for t in потоки:
        t.start()
    for t in потоки:
        t.join()
    f.close()

    минут = (time.time() - старт) / 60
    if остановлено():
        say("\nОстановлено. Запусти снова — уже скачанное пропустится.")
    else:
        say(f"\nВсё! Заняло {минут:.0f} мин. Найдено: {счёт['готово']}, пусто: {счёт['пусто']}.")
    if прогресс.пустые:
        say("Ничего не нашлось по запросам:\n  " + "\n  ".join(прогресс.пустые))
    say(f"Картинки в папке: {ПАПКА_РЕЗУЛЬТАТОВ}")
    событие("конец", остановлено=остановлено(), найдено=счёт["готово"], пустые=list(прогресс.пустые),
            минут=минут, нечего=False)
