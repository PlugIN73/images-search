"""Публикация требует инструкции, а не только списка изменений или пустого заголовка."""

import importlib.util
import unittest
from pathlib import Path

spec = importlib.util.spec_from_file_location(
    "check_release_notes", Path(__file__).resolve().parents[1] / "packaging/check_release_notes.py"
)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class ИнструкцияРелиза(unittest.TestCase):
    def test_пошаговая_инструкция_разрешает_выпуск(self):
        module.check_notes("## Изменения\nИсправление.\n\n## Как пользоваться\n\n1. Вставьте запросы.\n2. Начните поиск.\n")

    def test_списка_изменений_недостаточно(self):
        with self.assertRaises(ValueError):
            module.check_notes("## Изменения\n1. Обновлён браузер.\n")

    def test_пустого_раздела_недостаточно(self):
        with self.assertRaises(ValueError):
            module.check_notes("## Как пользоваться\n\n## Обновление\n1. Закройте старую версию.\n")


if __name__ == "__main__":
    unittest.main()
