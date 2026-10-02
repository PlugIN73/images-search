# -*- coding: utf-8 -*-
"""Мост между окном (страница на HTML) и программой: проверяем по-настоящему, через HTTP,
но без окна и без браузера. Запуск из корня проекта:  python -m unittest discover tests"""
import io
import json
import os
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

os.environ.setdefault("POISK_DATA", tempfile.mkdtemp(prefix="poisk-test-"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PIL import Image  # noqa: E402

import most  # noqa: E402


class Действия:
    """Подставные действия окна: запоминают, что их вызвали."""

    def __init__(self):
        self.вызовы = []

    def __getattr__(self, имя):
        return lambda *a: self.вызовы.append((имя, *a))

    def буфер(self):
        return "53 - кот|окно"


class ОбщееДляМоста(unittest.TestCase):
    def setUp(self):
        self.папка = Path(tempfile.mkdtemp())
        self.результаты = self.папка / "Результаты"
        self.результаты.mkdir()
        self.ui = self.папка / "ui"
        self.ui.mkdir()
        (self.ui / "index.html").write_text("<html>ключ={{КЛЮЧ}}</html>", encoding="utf-8")
        (self.ui / "app.js").write_text("console.log(1)", encoding="utf-8")
        self.запуски = []
        self.действия = Действия()
        self.открыть()

    def открыть(self):
        """Как запуск программы: список кадров читается с диска."""
        self.прил = most.Приложение(self.результаты, self.папка / ".кадры.json",
                                    запуск=self.поддельный_поиск, действия=self.действия)
        self.сервер = most.Сервер(self.прил, self.ui)
        self.сервер.запустить()
        self.addCleanup(self.сервер.остановить)

    def поддельный_поиск(self, кадры, сколько, стоп, дорожек):
        self.запуски.append((кадры, сколько, дорожек))
        most.poisk.say("строка журнала")
        most.poisk.событие("начало", сделано=0, всего=1, кадров_готово=0, кадров=1)
        most.poisk.событие("запрос", имя="1 - кот", найдено=True, сделано=1, всего=1,
                           кадров_готово=1, кадров=1, пустые=[])
        most.poisk.событие("конец", остановлено=False, найдено=1, пустые=[], минут=0.1, нечего=False)

    def запрос(self, путь, данные=None, метод=None, ключ=True):
        заголовки = {"Content-Type": "application/json"}
        if ключ:
            заголовки["X-Token"] = self.сервер.ключ
        тело = json.dumps(данные).encode() if данные is not None else None
        r = urllib.request.Request(self.сервер.адрес + urllib.parse.quote(путь, safe="/?=&"),
                                   data=тело, headers=заголовки, method=метод)
        with urllib.request.urlopen(r, timeout=10) as ответ:
            содержимое = ответ.read()
            if ответ.headers.get_content_type() == "application/json":
                return json.loads(содержимое)
            return содержимое

    def ждать_событие(self, тип, после=0):
        конец = time.time() + 5
        while time.time() < конец:
            ответ = self.запрос(f"api/события?после={после}&ждать=1")
            for с in ответ["события"]:
                if с["тип"] == тип:
                    return с, ответ["события"]
            после = ответ["последнее"]
        self.fail(f"не дождался события {тип}")


class Страница(ОбщееДляМоста):
    def test_страница_получает_ключ(self):
        текст = self.запрос(f"?k={self.сервер.ключ}", ключ=False).decode()
        self.assertEqual(текст, f"<html>ключ={self.сервер.ключ}</html>")

    def test_страница_без_ключа_не_открывается(self):
        with self.assertRaises(urllib.error.HTTPError) as e:
            self.запрос("", ключ=False)
        self.assertEqual(e.exception.code, 403)

    def test_файлы_страницы(self):
        self.assertEqual(self.запрос("ui/app.js", ключ=False), b"console.log(1)")

    def test_за_пределы_папки_не_выйти(self):
        for путь in ("ui/../../etc/passwd", "ui/%2e%2e/%2e%2e/x"):
            with self.subTest(путь=путь), self.assertRaises(urllib.error.HTTPError) as e:
                self.запрос(путь, ключ=False)
            self.assertEqual(e.exception.code, 404)

    def test_чужое_имя_сайта_не_пускаем(self):
        # страница с чужого сайта, подменившего свой адрес на 127.0.0.1 (DNS rebinding)
        r = urllib.request.Request(self.сервер.адрес + f"?k={self.сервер.ключ}", headers={"Host": "evil.example:80"})
        with self.assertRaises(urllib.error.HTTPError) as e:
            urllib.request.urlopen(r, timeout=5)
        self.assertEqual(e.exception.code, 403)

    def test_кривая_длина_тела_не_вешает_сервер(self):
        import http.client
        порт = self.сервер.http.server_address[1]
        for длина, код in (("-1", 200), ("abc", 400)):
            with self.subTest(длина=длина):
                с = http.client.HTTPConnection("127.0.0.1", порт, timeout=5)
                с.putrequest("POST", "/api/" + urllib.parse.quote("настройки"))
                с.putheader("X-Token", self.сервер.ключ)
                с.putheader("Content-Length", длина)
                с.endheaders()
                self.assertEqual(с.getresponse().status, код)
                с.close()
        self.assertEqual(self.запрос("api/состояние")["сколько"], 2)   # сервер жив

    def test_испорченный_запрос_не_стирает_список(self):
        import http.client
        self.запрос("api/кадры", {"кадры": [{"имя": "1", "ru": "кот"}]}, "PUT")
        порт = self.сервер.http.server_address[1]
        for тело in (b"{oops", b'{"\u043a": 1}', b"[]"):
            with self.subTest(тело=тело):
                с = http.client.HTTPConnection("127.0.0.1", порт, timeout=5)
                с.request("PUT", "/api/" + urllib.parse.quote("кадры"), body=тело,
                          headers={"X-Token": self.сервер.ключ, "Content-Type": "application/json"})
                self.assertEqual(с.getresponse().status, 400)
                с.close()
        self.assertEqual(len(self.запрос("api/состояние")["кадры"]), 1)

    def test_слишком_большая_вставка(self):
        with self.assertRaises(urllib.error.HTTPError) as e:
            self.запрос("api/вставить", {"текст": "кот\n" * 3_000_000})
        self.assertEqual(e.exception.code, 400)
        self.assertIn("слишком", json.loads(e.exception.read())["ошибка"])

    def test_api_без_ключа_нельзя(self):
        with self.assertRaises(urllib.error.HTTPError) as e:
            self.запрос("api/состояние", ключ=False)
        self.assertEqual(e.exception.code, 403)


class Кадры(ОбщееДляМоста):
    def test_состояние_со_скачанными_картинками(self):
        (self.папка / ".кадры.json").write_text(json.dumps([{"имя": "Кадр 1", "ru": "53 - кот"}]),
                                                encoding="utf-8")
        (self.результаты / "53 - кот-1.jpg").write_bytes(b"x")
        self.открыть()
        с = self.запрос("api/состояние")
        self.assertEqual(с["кадры"], [{"номер": "53", "ru": "кот", "запросы": [
            {"текст": "кот", "имя": "53 - кот", "файлы": ["53 - кот-1.jpg"]}]}])
        self.assertEqual((с["сколько"], с["дорожек"], с["идёт"]), (2, 2, False))

    def test_сохранить_список(self):
        с = self.запрос("api/кадры", {"кадры": [{"имя": "3", "ru": "пёс"}]}, "PUT")
        self.assertEqual(с["кадры"][0]["запросы"][0]["имя"], "3 - пёс")
        сохранено = json.loads((self.папка / ".кадры.json").read_text(encoding="utf-8"))
        self.assertEqual(сохранено, [{"имя": "3", "ru": "пёс"}])

    def test_вставить_списком(self):
        self.запрос("api/кадры", {"кадры": [{"имя": "5", "ru": "старое"}]}, "PUT")
        с = self.запрос("api/вставить", {"текст": "кот\n7 - пёс"})
        self.assertEqual([(к["номер"], к["ru"]) for к in с["кадры"]],
                         [("5", "старое"), ("8", "кот"), ("7", "пёс")])

    def test_сводка_вставки(self):
        self.запрос("api/кадры", {"кадры": [{"имя": "9", "ru": "x"}]}, "PUT")
        ответ = self.запрос("api/сводка", {"текст": "кот\nпёс"})
        self.assertEqual(ответ["текст"], "Будет кадров: 2. Без номера: 2 — дам номера с 10 по 11.")

    def test_добавить_кадр_со_следующим_номером(self):
        self.запрос("api/кадры", {"кадры": [{"имя": "5", "ru": "кот"}, {"имя": "9", "ru": "пёс"}]}, "PUT")
        с = self.запрос("api/добавить", {})
        self.assertEqual((с["кадры"][-1]["номер"], с["кадры"][-1]["ru"]), ("10", ""))

    def test_номер_из_запроса_переезжает_в_поле(self):
        с = self.запрос("api/кадры", {"кадры": [{"имя": "", "ru": "12 - кот"}]}, "PUT")
        self.assertEqual((с["кадры"][0]["номер"], с["кадры"][0]["ru"]), ("12", "кот"))

    def test_настройки_в_пределах(self):
        с = self.запрос("api/настройки", {"сколько": 50, "дорожек": "abc"})
        self.assertEqual((с["сколько"], с["дорожек"]), (20, 2))


class Поиск(ОбщееДляМоста):
    def test_пустой_список_не_запускается(self):
        with self.assertRaises(urllib.error.HTTPError) as e:
            self.запрос("api/старт", {})
        self.assertEqual(e.exception.code, 400)
        self.assertIn("Вставить списком", json.loads(e.exception.read())["ошибка"])
        self.assertEqual(self.запуски, [])

    def test_поиск_и_события(self):
        self.запрос("api/кадры", {"кадры": [{"имя": "1", "ru": "кот"}, {"имя": "2", "ru": ""}]}, "PUT")
        self.запрос("api/настройки", {"сколько": 3, "дорожек": 1})
        self.запрос("api/старт", {})
        конец, _ = self.ждать_событие("завершено")
        self.assertEqual(self.запуски, [([{"имя": "1", "ru": "кот"}], 3, 1)])
        все = self.запрос("api/события?после=0&ждать=0")["события"]
        типы = [с["тип"] for с in все]
        for тип in ("старт", "журнал", "начало", "запрос", "конец", "завершено"):
            self.assertIn(тип, типы)
        журнал = next(с for с in все if с["тип"] == "журнал")
        self.assertEqual(журнал["текст"], "строка журнала")
        запрос = next(с for с in все if с["тип"] == "запрос")
        self.assertEqual((запрос["имя"], запрос["найдено"]), ("1 - кот", True))
        self.assertEqual((запрос["кадров_готово"], запрос["кадров"], запрос["осталось_мин"]), (1, 1, 0))
        self.assertFalse(self.запрос("api/состояние")["идёт"])

    def test_ошибка_поиска_попадает_в_журнал(self):
        def сломанный(*a):
            raise RuntimeError("всё сломалось")
        self.прил.запуск = сломанный
        self.запрос("api/кадры", {"кадры": [{"имя": "1", "ru": "кот"}]}, "PUT")
        self.запрос("api/старт", {})
        _, события = self.ждать_событие("завершено")
        все = self.запрос("api/события?после=0&ждать=0")["события"]
        self.assertTrue(any("всё сломалось" in с.get("текст", "") for с in все if с["тип"] == "журнал"))

    def test_стоп(self):
        начат = threading.Event()

        def долгий(кадры, сколько, стоп, дорожек):
            начат.set()
            стоп.wait(5)
        self.прил.запуск = долгий
        self.запрос("api/кадры", {"кадры": [{"имя": "1", "ru": "кот"}]}, "PUT")
        self.запрос("api/старт", {})
        self.assertTrue(начат.wait(5))
        self.assertTrue(self.запрос("api/состояние")["идёт"])
        self.запрос("api/стоп", {})
        self.ждать_событие("завершено")
        self.assertFalse(self.запрос("api/состояние")["идёт"])

    def test_долгое_ожидание_событий(self):
        def позже():
            time.sleep(0.3)
            self.прил.событие("журнал", текст="позже")
        threading.Thread(target=позже).start()
        начало = time.time()
        ответ = self.запрос("api/события?после=0&ждать=5")
        self.assertLess(time.time() - начало, 3)
        self.assertEqual(ответ["события"][-1]["текст"], "позже")


class ПревьюИДействия(ОбщееДляМоста):
    def test_превью_уменьшенная_картинка(self):
        Image.new("RGB", (1200, 800), "red").save(self.результаты / "53 - кот-1.jpg")
        данные = self.запрос(f"превью?ф=53 - кот-1.jpg&k={self.сервер.ключ}", ключ=False)
        картинка = Image.open(io.BytesIO(данные))
        self.assertEqual(картинка.format, "JPEG")
        self.assertLessEqual(картинка.height, 120)

    def test_превью_только_из_результатов(self):
        for имя in ("../.кадры.json", "нет.jpg", "D:x.jpg"):
            with self.subTest(имя=имя), self.assertRaises(urllib.error.HTTPError) as e:
                self.запрос(f"превью?ф={имя}&k={self.сервер.ключ}", ключ=False)
            self.assertEqual(e.exception.code, 404)

    def test_превью_без_ключа_нельзя(self):
        Image.new("RGB", (10, 10)).save(self.результаты / "кот.jpg")
        with self.assertRaises(urllib.error.HTTPError) as e:
            self.запрос("превью?ф=кот.jpg", ключ=False)
        self.assertEqual(e.exception.code, 403)

    def test_окно_закрыли(self):
        # запасное окно в Chromium сообщает о закрытии маячком — без заголовков, ключ в адресе
        r = urllib.request.Request(self.сервер.адрес + "api/" + urllib.parse.quote("закрыто")
                                   + f"?k={self.сервер.ключ}", data=b"", method="POST")
        urllib.request.urlopen(r, timeout=5).read()
        self.assertIn(("окно_закрыто",), self.действия.вызовы)

    def test_окно_закрыли_только_с_ключом(self):
        r = urllib.request.Request(self.сервер.адрес + "api/" + urllib.parse.quote("закрыто") + "?k=chuzhoy",
                                   data=b"", method="POST")
        with self.assertRaises(urllib.error.HTTPError):
            urllib.request.urlopen(r, timeout=5)
        self.assertNotIn(("окно_закрыто",), self.действия.вызовы)

    def test_действия_окна(self):
        (self.результаты / "53 - кот-1.jpg").write_bytes(b"x")
        self.запрос("api/папка", {})
        self.запрос("api/открыть", {"файл": "53 - кот-1.jpg"})
        self.запрос("api/открыть", {"файл": "../секрет.txt"})
        self.запрос("api/открыть", {"файл": "C:секрет.jpg"})
        self.assertEqual(self.действия.вызовы, [("открыть_папку",),
                                                ("открыть_файл", self.результаты / "53 - кот-1.jpg")])
        self.assertEqual(self.запрос("api/буфер")["текст"], "53 - кот|окно")


if __name__ == "__main__":
    unittest.main()
