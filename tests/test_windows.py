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

    def test_недокачанный_файл_не_считается(self):
        # картинка пишется во временный файл «.~…» и переименовывается, только когда записана целиком
        (poisk.ПАПКА_РЕЗУЛЬТАТОВ / ".~кот-1.jpg").write_bytes(b"x")
        self.assertFalse(poisk._уже_скачано("кот", 1) or poisk._уже_скачано("кот", 2))

    def test_сохранить_целиком(self):
        poisk.сохранить_целиком(poisk.ПАПКА_РЕЗУЛЬТАТОВ / "кот-1.jpg", b"picture")
        self.assertEqual((poisk.ПАПКА_РЕЗУЛЬТАТОВ / "кот-1.jpg").read_bytes(), b"picture")
        self.assertEqual([f.name for f in poisk.ПАПКА_РЕЗУЛЬТАТОВ.iterdir()], ["кот-1.jpg"])

    def test_квадратные_скобки_в_имени(self):
        имя = "5 - фото [1942] архив"
        (poisk.ПАПКА_РЕЗУЛЬТАТОВ / f"{имя}-1.jpg").write_bytes(b"x")
        (poisk.ПАПКА_РЕЗУЛЬТАТОВ / f"{имя}-2.png").write_bytes(b"x")
        self.assertTrue(poisk._уже_скачано(имя, 2))
        self.assertFalse(poisk._уже_скачано(имя, 3))


class ПапкиДанных(unittest.TestCase):
    def setUp(self):
        self.дом = Path(tempfile.mkdtemp())
        self.программа = Path(tempfile.mkdtemp())

    def выбрать(self, **kw):
        параметры = dict(собрано=True, на_windows=True, папка_программы=self.программа,
                         дом=self.дом, из_окружения=None)
        параметры.update(kw)
        return poisk.выбрать_папки(**параметры)

    def test_windows_рядом_с_exe_служебное_в_internal(self):
        база, служебная = self.выбрать()
        self.assertEqual(база, self.программа)
        self.assertEqual(служебная, self.программа / "_internal" / "данные")
        self.assertTrue(служебная.is_dir())

    @unittest.skipIf(sys.platform.startswith("win"), "chmod не запрещает запись на Windows")
    def test_windows_нельзя_писать_рядом_с_exe_значит_документы(self):
        self.программа.chmod(0o500)
        try:
            база, служебная = self.выбрать()
        finally:
            self.программа.chmod(0o700)
        self.assertEqual(база, self.дом / "Documents" / "Поиск картинок")
        self.assertEqual(служебная, база)

    def test_mac_приложение_в_документах(self):
        база, служебная = self.выбрать(на_windows=False)
        self.assertEqual(база, self.дом / "Documents" / "Поиск картинок")
        self.assertEqual(служебная, база)

    def test_из_исходников_рядом_с_программой(self):
        for на_windows in (True, False):
            with self.subTest(на_windows=на_windows):
                self.assertEqual(self.выбрать(собрано=False, на_windows=на_windows),
                                 (self.программа, self.программа))

    def test_переменная_окружения_важнее_всего(self):
        своя = Path(tempfile.mkdtemp()) / "данные"
        self.assertEqual(self.выбрать(из_окружения=str(своя)), (своя, своя))


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
