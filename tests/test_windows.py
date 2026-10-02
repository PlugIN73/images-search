# -*- coding: utf-8 -*-
"""Тесты «детских болезней» Windows (и всего, что проверяется без браузера).
Запуск из корня проекта:  python -m unittest discover tests"""
import os
import sys
import tempfile
import unittest
from pathlib import Path

# Данные тестов — во временную папку, а не рядом с программой.
_ДАННЫЕ = tempfile.mkdtemp(prefix="poisk-test-")
os.environ["POISK_DATA"] = _ДАННЫЕ
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import poisk  # noqa: E402
import okno  # noqa: E402


class ИменаФайлов(unittest.TestCase):
    def test_зарезервированные_имена_windows(self):
        for имя in ("con", "CON", "nul", "Aux", "prn", "com1", "LPT9"):
            with self.subTest(имя=имя):
                self.assertEqual(poisk.безопасное_имя(имя), "_" + имя)

    def test_обычные_имена_не_трогаем(self):
        for имя in ("concert", "1 - con", "console log", "кот"):
            with self.subTest(имя=имя):
                self.assertEqual(poisk.безопасное_имя(имя), имя)

    def test_после_обрезки_нет_пробела_и_точки_в_конце(self):
        # Windows молча отрезает их, и файл потом не находится
        for хвост in (" b", ". b", " .b"):
            имя = poisk.безопасное_имя("a" * 79 + хвост)
            with self.subTest(хвост=хвост):
                self.assertFalse(имя.endswith((" ", ".")), repr(имя))


class УжеСкачано(unittest.TestCase):
    def setUp(self):
        self.было = poisk.ПАПКА_РЕЗУЛЬТАТОВ
        poisk.ПАПКА_РЕЗУЛЬТАТОВ = Path(tempfile.mkdtemp())

    def tearDown(self):
        poisk.ПАПКА_РЕЗУЛЬТАТОВ = self.было

    def test_квадратные_скобки_в_имени(self):
        имя = "5 - фото [1942] архив"
        (poisk.ПАПКА_РЕЗУЛЬТАТОВ / f"{имя}-1.jpg").write_bytes(b"x")
        (poisk.ПАПКА_РЕЗУЛЬТАТОВ / f"{имя}-2.png").write_bytes(b"x")
        self.assertTrue(poisk._уже_скачано(имя, 2))
        self.assertFalse(poisk._уже_скачано(имя, 3))


class Прокрутка(unittest.TestCase):
    def test_windows_колесо_и_тачпад(self):
        for delta, шаги in ((120, -1), (-120, 1), (240, -2), (40, -1), (-40, 1), (0, 0)):
            with self.subTest(delta=delta):
                self.assertEqual(okno.шаги_прокрутки(delta, на_windows=True), шаги)

    def test_mac(self):
        for delta, шаги in ((3, -3), (-1, 1), (0, 0)):
            with self.subTest(delta=delta):
                self.assertEqual(okno.шаги_прокрутки(delta, на_windows=False), шаги)


class ГорячиеКлавиши(unittest.TestCase):
    def test_на_windows_только_ctrl(self):
        # На Windows Tk считает включённый NumLock модификатором Command
        self.assertEqual(okno.модификаторы("win32"), ["Control"])

    def test_на_mac_cmd_и_ctrl(self):
        self.assertEqual(okno.модификаторы("darwin"), ["Command", "Control"])


class ОднаКопия(unittest.TestCase):
    def test_вторая_копия_не_получает_замок(self):
        путь = Path(tempfile.mkdtemp()) / ".запущено"
        первый = poisk.занять_замок(путь)
        self.assertIsNotNone(первый)
        try:
            self.assertIsNone(poisk.занять_замок(путь))
        finally:
            poisk.отпустить_замок(первый)
        второй = poisk.занять_замок(путь)
        self.assertIsNotNone(второй)
        poisk.отпустить_замок(второй)


if __name__ == "__main__":
    unittest.main()
