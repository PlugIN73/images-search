#!/bin/bash
# Запуск из исходников на Mac (для разработки). Обычным людям — готовый .dmg из Releases.
cd "$(dirname "$0")"
chmod +x "$0" 2>/dev/null   # чтобы в следующий раз открывался двойным щелчком
export PATH="$HOME/.local/bin:$PATH"

# Общая «начинка» для всех копий программы: ставится один раз
# и переустанавливается, только когда меняется requirements.txt.
VENV="$HOME/.poisk-kartinok/venv"
MARK="$VENV/.ready-$(shasum requirements.txt | cut -c1-12)"

echo; echo "=== Поиск картинок в Яндексе — запуск $(date '+%d.%m.%Y %H:%M') ==="
if ! command -v uv >/dev/null 2>&1; then
  echo "Первый запуск: ставлю инструменты (один раз, пару минут)..."
  curl -LsSf https://astral.sh/uv/install.sh | sh
  export PATH="$HOME/.local/bin:$PATH"
fi

if [ ! -f "$MARK" ]; then
  echo "Устанавливаю нужное (2-5 минут, около 200 МБ)..."
  mkdir -p "$(dirname "$VENV")"
  [ -x "$VENV/bin/python" ] || uv venv --python 3.11 "$VENV" || exit 1
  uv pip install --python "$VENV/bin/python" -r requirements.txt || exit 1
  "$VENV/bin/python" -m playwright install chromium || exit 1
  rm -f "$VENV"/.ready*
  touch "$MARK"
fi

"$VENV/bin/python" okno.py
echo
echo "Окно программы закрыто. Это окно тоже можно закрыть."
