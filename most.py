# -*- coding: utf-8 -*-
"""Мост между окном и программой.

Окно — это страница на HTML (папка ui/). Она общается с программой через маленький
веб-сервер, который слушает только этот компьютер (127.0.0.1) и пускает только
со своим ключом. Так одна и та же страница работает и в окне pywebview, и во
встроенном Chromium, а всё, кроме самого окна, проверяется тестами.
"""
import io
import json
import mimetypes
import secrets
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from PIL import Image

import kadry
import poisk

НЕ_ПРОЧИТАТЬ = "Не получилось прочитать запрос окна. Попробуй ещё раз."
МАКС_ЗАПРОС = 10 * 1024 * 1024   # больше этого окно не присылает; больше — значит, что-то не так
ВЫСОТА_ПРЕВЬЮ = 96            # вдвое больше, чем на экране: чётко на Retina и 200% Windows
ХРАНИТЬ_СОБЫТИЙ = 3000


class Приложение:
    """Всё, что окно может узнать и попросить: список кадров, настройки, поиск, события.

    действия — то, что умеет только настоящее окно: открыть папку или файл, ссылку,
    прочитать буфер обмена, показать окно, привлечь внимание к капче."""

    def __init__(self, папка_результатов, файл_состояния, запуск=None, действия=None):
        self.папка = Path(папка_результатов)
        self.файл_состояния = Path(файл_состояния)
        self.запуск = запуск or poisk.запустить
        self.действия = действия
        self.сколько = 2
        self.дорожек = 2
        self.обновление = None          # (версия, ссылка), если вышла новая
        self.стоп = threading.Event()
        self.поток = None
        self._идёт = False
        self.старт_время = 0.0
        self.замок = threading.RLock()
        self.события = []
        self.номер_события = 0
        self.есть_события = threading.Condition()
        self.кадры = self._загрузить()

    # ---------- список кадров ----------
    def _загрузить(self):
        try:
            сохранено = json.loads(self.файл_состояния.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []
        кадры = []
        for к in сохранено if isinstance(сохранено, list) else []:
            if isinstance(к, dict):
                имя, ru = kadry.привести_кадр(к.get("имя"), к.get("ru", ""))
                кадры.append({"имя": имя, "ru": ru})
        return кадры

    def _сохранить(self):
        try:
            self.файл_состояния.write_text(json.dumps(self.кадры, ensure_ascii=False, indent=1),
                                           encoding="utf-8")
        except OSError:
            pass

    @property
    def идёт(self):
        return self._идёт

    def состояние(self):
        with self.замок:
            кадры = [dict(к) for к in self.кадры]
            итог = {"сколько": self.сколько, "дорожек": self.дорожек, "идёт": self.идёт,
                    "версия": poisk.ВЕРСИЯ, "обновление": self.обновление, "последнее": self.номер_события}
        # папку с картинками читаем уже без замка: во время поиска это делается часто
        return {"кадры": kadry.кадры_с_файлами(кадры, self.папка), **итог}

    def задать_кадры(self, кадры):
        with self.замок:
            if self.идёт:
                raise ОшибкаДействия("Пока идёт поиск, список менять нельзя.")
            новые = []
            for к in кадры:
                if isinstance(к, dict):
                    имя, ru = kadry.привести_кадр(str(к.get("имя") or ""), str(к.get("ru") or "").strip())
                    новые.append({"имя": имя, "ru": ru})
            self.кадры = новые
            self._сохранить()

    def добавить_кадр(self):
        """Пустой кадр в конец списка — со следующим свободным номером."""
        with self.замок:
            номер = kadry.следующий_номер(к["имя"] for к in self.кадры)
            self.задать_кадры(self.кадры + [{"имя": номер, "ru": ""}])

    def вставить(self, текст):
        with self.замок:
            self.задать_кадры(kadry.вставить_в_список(self.кадры, текст))

    def сводка(self, текст):
        with self.замок:
            имеющиеся = [к["имя"] for к in self.кадры if к["ru"]]
        return kadry.сводка_вставки(текст, имеющиеся)

    def задать_настройки(self, сколько=None, дорожек=None):
        with self.замок:
            if сколько is not None:
                self.сколько = kadry.число_в_пределах(сколько, 1, 20, 2)
            if дорожек is not None:
                self.дорожек = kadry.число_в_пределах(дорожек, 1, 4, 2)

    # ---------- поиск ----------
    def старт(self):
        with self.замок:
            if self.идёт:
                return
            кадры = [dict(к) for к in self.кадры if к["ru"]]
            if not кадры:
                raise ОшибкаДействия("Список пуст. Нажми «Вставить списком» и вставь сценарий "
                                     "или впиши запросы хотя бы в один кадр.")
            self.стоп.clear()
            self._идёт = True
            self.старт_время = time.time()
            self.событие("старт")
            self._окну("поиск_идёт", True)
            self.поток = threading.Thread(target=self._работа, args=(кадры, self.сколько, self.дорожек),
                                          daemon=True)
            self.поток.start()

    def _работа(self, кадры, сколько, дорожек):
        было = poisk.ВЫВОД[0], poisk.СОБЫТИЯ[0]
        poisk.ВЫВОД[0] = lambda msg: self.событие("журнал", текст=str(msg))
        poisk.СОБЫТИЯ[0] = self._событие_поиска
        try:
            self.запуск(кадры, сколько, self.стоп, дорожек)
        except Exception as e:
            self.событие("журнал", текст=f"\nЧто-то пошло не так: {e}\n"
                                          "Запусти снова — уже скачанное пропустится. Если повторяется — "
                                          "пришли разработчику файл журнал.txt.")
        finally:
            poisk.ВЫВОД[0], poisk.СОБЫТИЯ[0] = было
            # под замком: новый поиск не начнётся, пока окно не узнало, что этот закончился
            with self.замок:
                self._идёт = False
                self._окну("поиск_идёт", False)
                self.событие("завершено")

    def _событие_поиска(self, тип, данные):
        if тип in ("начало", "запрос"):
            данные = dict(данные, осталось_мин=kadry.осталось_минут(
                time.time() - self.старт_время, данные["сделано"], данные["всего"]))
        if тип == "капча":
            self._окну("внимание")
        if тип == "конец" and not данные.get("остановлено") and not данные.get("нечего"):
            self._окну("звук")
        self.событие(тип, **данные)

    def остановить(self):
        if self.идёт:
            self.стоп.set()
            self.событие("останавливаю", текст="Останавливаю: дождусь, пока закончатся текущие запросы…")

    # ---------- события для окна ----------
    def событие(self, тип, **данные):
        with self.есть_события:
            self.номер_события += 1
            self.события.append({"id": self.номер_события, "тип": тип, "время": time.time(), **данные})
            del self.события[:-ХРАНИТЬ_СОБЫТИЙ]
            self.есть_события.notify_all()
        if тип == "журнал":
            self._окну("в_журнал", данные.get("текст", ""))

    def _окну(self, действие, *args):
        """Попросить настоящее окно что-то сделать; без окна (в тестах) — ничего."""
        if self.действия is None:
            return
        try:
            getattr(self.действия, действие)(*args)
        except Exception:
            pass

    def события_после(self, после, ждать=0.0):
        """События новее «после»; если их нет — ждёт до «ждать» секунд (долгий опрос)."""
        конец = time.time() + max(0.0, min(float(ждать), 30.0))
        with self.есть_события:
            while self.номер_события <= после and time.time() < конец:
                self.есть_события.wait(конец - time.time())
            return [с for с in self.события if с["id"] > после], self.номер_события

    # ---------- просьбы к окну ----------
    def буфер(self):
        if self.действия is None:
            return ""
        try:
            return self.действия.буфер() or ""
        except Exception:
            return ""

    def открыть_папку(self):
        self.папка.mkdir(parents=True, exist_ok=True)
        self._окну("открыть_папку")

    def открыть_файл(self, имя):
        файл = self.файл_результата(имя)
        if файл is not None:
            self._окну("открыть_файл", файл)

    def открыть_обновление(self):
        if self.обновление:
            self._окну("открыть_ссылку", self.обновление[1])

    def окно_открыто(self):
        self._окну("окно_открыто")

    def окно_закрыто(self):
        self._окну("окно_закрыто")

    def показать(self):
        """Второй запуск программы просит показать это окно. True — если окно смогло."""
        try:
            return bool(self.действия is not None and self.действия.показать())
        except Exception:
            return False

    # ---------- картинки ----------
    def файл_результата(self, имя):
        """Путь к картинке в «Результатах» — или None, если имя ведёт куда-то ещё
        (в том числе «D:x.jpg» на Windows — это путь на другом диске)."""
        имя = str(имя or "")
        if not имя or any(з in имя for з in "/\\:\0") or имя in (".", ".."):
            return None
        путь = self.папка / имя
        if путь.parent != self.папка:
            return None
        return путь if путь.is_file() else None

    def превью(self, имя):
        путь = self.файл_результата(имя)
        if путь is None:
            return None
        try:
            with Image.open(путь) as img:
                img.draft("RGB", (ВЫСОТА_ПРЕВЬЮ * 4, ВЫСОТА_ПРЕВЬЮ * 2))   # JPEG быстрее читается уменьшенным
                img = img.convert("RGB")
                img.thumbnail((ВЫСОТА_ПРЕВЬЮ * 3, ВЫСОТА_ПРЕВЬЮ))
                буфер = io.BytesIO()
                img.save(буфер, "JPEG", quality=82)
                return буфер.getvalue()
        except Exception:
            return None


class ОшибкаДействия(Exception):
    """Понятное человеку объяснение, почему просьбу окна выполнить нельзя."""


class Сервер:
    def __init__(self, приложение, папка_ui, порт=0):
        self.приложение = приложение
        self.папка_ui = Path(папка_ui).resolve()
        self.ключ = secrets.token_urlsafe(24)
        self.http = ThreadingHTTPServer(("127.0.0.1", порт), _обработчик(self))
        self.http.daemon_threads = True
        self.поток = None

    @property
    def адрес(self):
        return f"http://127.0.0.1:{self.http.server_address[1]}/"

    @property
    def адрес_окна(self):
        return f"{self.адрес}?k={self.ключ}"

    def запустить(self):
        self.поток = threading.Thread(target=self.http.serve_forever, daemon=True)
        self.поток.start()

    def остановить(self):
        self.http.shutdown()
        self.http.server_close()


def _обработчик(сервер):
    прил = сервер.приложение

    class Обработчик(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *a):
            pass

        # ---------- ответы ----------
        def _ответ(self, код, тело=b"", тип="application/json; charset=utf-8", кеш=False):
            if код >= 400:
                self.close_connection = True      # тело запроса могло остаться непрочитанным
            self.send_response(код)
            self.send_header("Content-Type", тип)
            self.send_header("Content-Length", str(len(тело)))
            self.send_header("Cache-Control", "max-age=3600" if кеш else "no-store")
            if код >= 400:
                self.send_header("Connection", "close")
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(тело)

        def _json(self, данные, код=200):
            self._ответ(код, json.dumps(данные, ensure_ascii=False).encode("utf-8"))

        def _тело(self):
            """Тело запроса как словарь. Если его не прочитать — ОшибкаДействия (отвечаем 400),
            а не делаем вид, что пришёл пустой список кадров."""
            try:
                длина = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                raise ОшибкаДействия(НЕ_ПРОЧИТАТЬ)
            if длина <= 0:
                return {}
            if длина > МАКС_ЗАПРОС:
                # дочитываем и выбрасываем, чтобы окно получило понятный ответ, а не обрыв связи
                while длина > 0:
                    кусок = self.rfile.read(min(длина, 1024 * 1024))
                    if not кусок:
                        break
                    длина -= len(кусок)
                raise ОшибкаДействия("Текст слишком большой. Вставь сценарий частями.")
            try:
                данные = json.loads(self.rfile.read(длина))
            except ValueError:
                данные = None
            if not isinstance(данные, dict):
                raise ОшибкаДействия(НЕ_ПРОЧИТАТЬ)
            return данные

        def _ключ_верный(self, ключ=None):
            ключ = self.headers.get("X-Token", "") if ключ is None else ключ
            return secrets.compare_digest(ключ, сервер.ключ)

        def _ключ_из_адреса(self):
            return (self._разобрать()[1].get("k") or [""])[0]

        def _свой_адрес(self):
            """Чужая страница в браузере может подменить имя сайта на 127.0.0.1 (DNS rebinding) —
            тогда в запросе будет чужое имя в Host. Отвечаем только на свой адрес."""
            порт = сервер.http.server_address[1]
            return self.headers.get("Host", "") in (f"127.0.0.1:{порт}", f"localhost:{порт}")

        # ---------- разбор адреса ----------
        def _разобрать(self):
            адрес = urllib.parse.urlsplit(self.path)
            return urllib.parse.unquote(адрес.path), urllib.parse.parse_qs(адрес.query)

        def do_GET(self):
            if not self._свой_адрес():
                return self._ответ(403)
            путь, параметры = self._разобрать()
            if путь == "/":
                return self._страница(параметры)
            if путь.startswith("/ui/"):
                return self._файл_ui(путь[len("/ui/"):])
            if путь == "/превью":
                # картинке в <img> заголовок не передать — ключ приходит в адресе
                if not self._ключ_верный(self._ключ_из_адреса()):
                    return self._ответ(403)
                данные = прил.превью((параметры.get("ф") or [""])[0])
                return self._ответ(200, данные, "image/jpeg", кеш=True) if данные else self._ответ(404)
            if not путь.startswith("/api/"):
                return self._ответ(404)
            if not self._ключ_верный():
                return self._ответ(403)
            if путь == "/api/состояние":
                прил.окно_открыто()
                return self._json(прил.состояние())
            if путь == "/api/события":
                после = kadry.число_в_пределах((параметры.get("после") or ["0"])[0], 0, 10 ** 12, 0)
                ждать = kadry.число_в_пределах((параметры.get("ждать") or ["0"])[0], 0, 30, 0)
                события, последнее = прил.события_после(после, ждать)
                return self._json({"события": события, "последнее": последнее})
            if путь == "/api/буфер":
                return self._json({"текст": прил.буфер()})
            return self._ответ(404)

        do_HEAD = do_GET

        def do_PUT(self):
            self.do_POST()

        def do_POST(self):
            путь, параметры = self._разобрать()
            if путь == "/api/закрыто":
                # маячок от закрывшегося окна в Chromium: заголовков у него нет, ключ — в адресе
                if not self._свой_адрес() or not self._ключ_верный(self._ключ_из_адреса()):
                    return self._ответ(403)
                прил.окно_закрыто()
                return self._ответ(204)
            if not self._свой_адрес() or not self._ключ_верный():
                return self._ответ(403)
            try:
                данные = self._тело()
                if путь == "/api/кадры":
                    if not isinstance(данные.get("кадры"), list):
                        return self._json({"ошибка": "Не получилось прочитать список кадров."}, 400)
                    прил.задать_кадры(данные["кадры"])
                elif путь == "/api/добавить":
                    прил.добавить_кадр()
                elif путь == "/api/вставить":
                    прил.вставить(str(данные.get("текст") or ""))
                elif путь == "/api/сводка":
                    return self._json({"текст": прил.сводка(str(данные.get("текст") or ""))})
                elif путь == "/api/настройки":
                    прил.задать_настройки(данные.get("сколько"), данные.get("дорожек"))
                elif путь == "/api/старт":
                    прил.старт()
                elif путь == "/api/стоп":
                    прил.остановить()
                elif путь == "/api/папка":
                    прил.открыть_папку()
                elif путь == "/api/открыть":
                    прил.открыть_файл(данные.get("файл"))
                elif путь == "/api/ссылка":
                    прил.открыть_обновление()
                elif путь == "/api/показать":
                    return self._json({"показано": прил.показать()})
                else:
                    return self._ответ(404)
            except ОшибкаДействия as e:
                return self._json({"ошибка": str(e)}, 400)
            return self._json(прил.состояние())

        # ---------- страница ----------
        def _страница(self, параметры):
            if not self._ключ_верный(self._ключ_из_адреса()):
                return self._ответ(403, "Открой программу заново.".encode("utf-8"), "text/plain; charset=utf-8")
            try:
                html = (сервер.папка_ui / "index.html").read_text(encoding="utf-8")
            except OSError:
                return self._ответ(500)
            self._ответ(200, html.replace("{{КЛЮЧ}}", сервер.ключ).encode("utf-8"), "text/html; charset=utf-8")

        def _файл_ui(self, имя):
            try:
                путь = (сервер.папка_ui / имя).resolve()
                путь.relative_to(сервер.папка_ui)
                тело = путь.read_bytes()
            except (OSError, ValueError):
                return self._ответ(404)
            тип = {".js": "text/javascript", ".css": "text/css", ".woff2": "font/woff2",
                   ".svg": "image/svg+xml", ".html": "text/html"}.get(путь.suffix) \
                or mimetypes.guess_type(путь.name)[0] or "application/octet-stream"
            if тип.startswith("text/") or тип == "image/svg+xml":
                тип += "; charset=utf-8"
            self._ответ(200, тело, тип, кеш=путь.suffix == ".woff2")

    return Обработчик
