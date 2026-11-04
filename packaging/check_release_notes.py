"""Не разрешает выпуск без пошаговой инструкции использования."""

import re
import sys
from pathlib import Path


def check_notes(text):
    usage = re.search(r"^## Как пользоваться\s*\n(.*?)(?=^## |\Z)", text, re.MULTILINE | re.DOTALL)
    if not usage or not re.search(r"^\d+\.\s+\S", usage.group(1), re.MULTILINE):
        raise ValueError("В описании релиза нужен раздел «Как пользоваться» с нумерованными шагами.")


if __name__ == "__main__":
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).with_name("release-notes.md")
    try:
        check_notes(path.read_text(encoding="utf-8"))
    except ValueError as error:
        raise SystemExit(str(error))
    print("Инструкция использования в описании релиза: ОК")
