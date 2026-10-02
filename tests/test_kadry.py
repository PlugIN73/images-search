# -*- coding: utf-8 -*-
"""Номера кадров, ход поиска и проверка ввода — всё, что видно без окна и браузера.
Запуск из корня проекта:  python -m unittest discover tests"""
import os
import sys
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("POISK_DATA", tempfile.mkdtemp(prefix="poisk-test-"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import poisk  # noqa: E402
import kadry  # noqa: E402


class РазборСтроки(unittest.TestCase):
    def test_номер_уходит_из_запроса(self):
        self.assertEqual(kadry.разобрать_строку("53 - кот|кот у окна"), ("53", "кот|кот у окна"))

    def test_разные_разделители(self):
        for строка in ("53. кот", "53) кот", "53: кот", "53 — кот", "кадр 53: кот", "Frame 53 - кот"):
            with self.subTest(строка=строка):
                self.assertEqual(kadry.разобрать_строку(строка), ("53", "кот"))

    def test_без_номера(self):
        self.assertEqual(kadry.разобрать_строку("кот на подоконнике"), (None, "кот на подоконнике"))

    def test_пустая_строка(self):
        for строка in ("", "   ", "\ufeff"):
            with self.subTest(строка=строка):
                self.assertIsNone(kadry.разобрать_строку(строка))


class СледующийНомер(unittest.TestCase):
    def test_после_самого_большого(self):
        self.assertEqual(kadry.следующий_номер(["1", "5", "3"]), "6")

    def test_после_удаления_не_повторяется(self):
        # было 1, 2, 3 — второй удалили; новый кадр не должен стать «3»
        self.assertEqual(kadry.следующий_номер(["1", "3"]), "4")

    def test_пустой_список_и_нечисловые_имена(self):
        self.assertEqual(kadry.следующий_номер([]), "1")
        self.assertEqual(kadry.следующий_номер(["", "интро", "12а"]), "13")


class ПривестиКадр(unittest.TestCase):
    """Старые сохранённые списки: номер стоял и в поле «Кадр», и в начале запроса."""

    def test_номер_из_запроса_переезжает_в_поле(self):
        self.assertEqual(kadry.привести_кадр("53", "53 - кот|окно"), ("53", "кот|окно"))
        self.assertEqual(kadry.привести_кадр("", "53 - кот"), ("53", "кот"))

    def test_старое_автоимя_значит_без_номера(self):
        # «Кадр 3» программа придумывала сама, и в имена файлов он не попадал
        self.assertEqual(kadry.привести_кадр("Кадр 3", "кот"), ("", "кот"))
        self.assertEqual(kadry.привести_кадр("без названия", "кот"), ("", "кот"))

    def test_свой_номер_не_трогаем(self):
        self.assertEqual(kadry.привести_кадр("54", "кот"), ("54", "кот"))
        self.assertEqual(kadry.привести_кадр("54", "53 - кот"), ("54", "53 - кот"))


class ЗаданияИзКадров(unittest.TestCase):
    def test_номер_из_поля_кадр(self):
        self.assertEqual(poisk.задания_из_кадров([{"имя": "53", "ru": "кот|окно"}]),
                         [("53 - кот", "кот", 0), ("53 - окно", "окно", 0)])

    def test_номер_в_начале_запроса_по_старинке(self):
        self.assertEqual(poisk.задания_из_кадров([{"имя": "", "ru": "7 - пёс"}]),
                         [("7 - пёс", "пёс", 0)])

    def test_номер_в_запросе_главнее_поля(self):
        # так было до правок: имена файлов старых списков не меняются, уже скачанное не качается заново
        self.assertEqual(poisk.задания_из_кадров([{"имя": "54", "ru": "53 - кот"}]),
                         [("53 - кот", "кот", 0)])

    def test_без_номера(self):
        for имя in ("", "Кадр 3", "без названия"):
            with self.subTest(имя=имя):
                self.assertEqual(poisk.задания_из_кадров([{"имя": имя, "ru": "кот"}]),
                                 [("кот", "кот", 0)])

    def test_пустые_пропускаются_номера_кадров_по_порядку(self):
        задания = poisk.задания_из_кадров([{"имя": "1", "ru": ""}, {"имя": "2", "ru": " | a"},
                                           {"имя": "3", "ru": "b"}])
        self.assertEqual(задания, [("2 - a", "a", 1), ("3 - b", "b", 2)])


class ХодПоиска(unittest.TestCase):
    def test_кадр_готов_когда_готовы_все_его_запросы(self):
        п = poisk.Прогресс([0, 0, 1])
        self.assertEqual((п.кадров, п.всего), (2, 3))
        с = п.отметить(0, "1 - a", True)
        self.assertEqual((с["сделано"], с["кадров_готово"]), (1, 0))
        с = п.отметить(1, "1 - b", False)
        self.assertEqual((с["сделано"], с["кадров_готово"]), (2, 1))
        self.assertEqual(с["пустые"], ["1 - b"])

    def test_по_номеру_задания(self):
        п = poisk.Прогресс([0, 1, 1])
        п.отметить(2, "2 - b", True)
        с = п.отметить(0, "1 - a", True)
        self.assertEqual(с["кадров_готово"], 1)
        с = п.отметить(1, "2 - c", True)
        self.assertEqual(с["кадров_готово"], 2)


class ОсталосьВремени(unittest.TestCase):
    def test_оценка(self):
        self.assertIsNone(kadry.осталось_минут(30, 0, 10))
        self.assertEqual(kadry.осталось_минут(60, 1, 11), 10)
        self.assertEqual(kadry.осталось_минут(60, 10, 10), 0)


class ПроверкаВвода(unittest.TestCase):
    def test_число_в_пределах(self):
        for текст, итог in (("abc", 2), ("", 2), ("50", 20), ("0", 1), (" 7 ", 7), ("-3", 1)):
            with self.subTest(текст=текст):
                self.assertEqual(kadry.число_в_пределах(текст, 1, 20, 2), итог)


class Пронумеровать(unittest.TestCase):
    def test_строкам_без_номера_следующие_свободные(self):
        новые = [("53", "a"), (None, "кот"), (None, "пёс")]
        self.assertEqual(kadry.пронумеровать(новые, ["1", "60"]),
                         [("53", "a"), ("61", "кот"), ("62", "пёс")])

    def test_учитывает_номера_из_самой_вставки(self):
        self.assertEqual(kadry.пронумеровать([(None, "a"), ("7", "b")], []),
                         [("8", "a"), ("7", "b")])

    def test_весь_список_без_номеров(self):
        self.assertEqual(kadry.пронумеровать([(None, "a"), (None, "b")], []),
                         [("1", "a"), ("2", "b")])


class СводкаВставки(unittest.TestCase):
    def test_считает_кадры_и_строки_без_номера(self):
        self.assertEqual(kadry.сводка_вставки("53 - a|b\nкот\n\n54 - c\n"),
                         "Будет кадров: 3. Без номера: 1 — дам номер 55.")
        self.assertEqual(kadry.сводка_вставки("кот\nпёс", ["9"]),
                         "Будет кадров: 2. Без номера: 2 — дам номера с 10 по 11.")

    def test_все_с_номерами(self):
        self.assertEqual(kadry.сводка_вставки("1 - a\n2 - b"), "Будет кадров: 2.")

    def test_пусто(self):
        self.assertEqual(kadry.сводка_вставки("\n  \n"), "Вставь список в поле ниже.")


class ВставитьВСписок(unittest.TestCase):
    def test_заменяет_кадры_с_теми_же_номерами_и_убирает_пустые(self):
        было = [{"имя": "5", "ru": "старое"}, {"имя": "7", "ru": "заменится"}, {"имя": "9", "ru": ""}]
        self.assertEqual(kadry.вставить_в_список(было, "кот|окно\n7 - пёс\nтрамвай"),
                         [{"имя": "5", "ru": "старое"}, {"имя": "8", "ru": "кот|окно"},
                          {"имя": "7", "ru": "пёс"}, {"имя": "9", "ru": "трамвай"}])

    def test_пустая_вставка_ничего_не_меняет(self):
        было = [{"имя": "1", "ru": "кот"}]
        self.assertEqual(kadry.вставить_в_список(было, "\n \n"), было)


class ФайлыЗапроса(unittest.TestCase):
    def setUp(self):
        self.папка = Path(tempfile.mkdtemp())
        for имя in ("53 - кот-2.png", "53 - кот-1.jpg", "53 - кот-рыжий-1.jpg", "53 - кот.webp",
                    "53 - кот-1.txt", "153 - кот-1.jpg", "отчёт.csv"):
            (self.папка / имя).write_bytes(b"x")

    def test_только_свои_картинки_по_порядку(self):
        self.assertEqual(kadry.файлы_запроса(self.папка, "53 - кот"),
                         ["53 - кот.webp", "53 - кот-1.jpg", "53 - кот-2.png"])

    def test_скобки_и_точки_в_имени(self):
        (self.папка / "5 - фото [1942] г.-1.jpg").write_bytes(b"x")
        self.assertEqual(kadry.файлы_запроса(self.папка, "5 - фото [1942] г."), ["5 - фото [1942] г.-1.jpg"])

    def test_нет_папки(self):
        self.assertEqual(kadry.файлы_запроса(self.папка / "нет", "кот"), [])

    def test_кадры_с_файлами(self):
        итог = kadry.кадры_с_файлами([{"имя": "53", "ru": "кот|пёс"}], self.папка)
        self.assertEqual(итог, [{"номер": "53", "ru": "кот|пёс", "запросы": [
            {"текст": "кот", "имя": "53 - кот", "файлы": ["53 - кот.webp", "53 - кот-1.jpg", "53 - кот-2.png"]},
            {"текст": "пёс", "имя": "53 - пёс", "файлы": []}]}])


if __name__ == "__main__":
    unittest.main()
