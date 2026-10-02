#!/bin/bash
# Сборка «Поиск картинок.app» и .dmg для Mac.
# Запуск из корня проекта:  bash packaging/build_mac.sh
# Нужен только uv (https://docs.astral.sh/uv/). Собирает под архитектуру этого Mac.
set -euo pipefail
cd "$(dirname "$0")/.."

NAME="Поиск картинок"
VERSION=$(sed -n 's/^ВЕРСИЯ = "\(.*\)"/\1/p' poisk.py)
ARCH=$(uname -m)                                  # arm64 или x86_64
BUILD="build/mac-$ARCH"
APP="dist/$NAME.app"
DMG="dist/Poisk-kartinok-$VERSION-mac-$ARCH.dmg"

echo "== Версия $VERSION, $ARCH"
rm -rf "$BUILD" "dist"
mkdir -p "$BUILD"

echo "== Python и библиотеки"
uv venv --python 3.11 "$BUILD/venv"
PY="$BUILD/venv/bin/python"
uv pip install --python "$PY" -r requirements.txt -r packaging/requirements-build.txt

echo "== Chromium для встраивания"
PLAYWRIGHT_BROWSERS_PATH="$PWD/$BUILD/ms-playwright" "$PY" -m playwright install chromium --no-shell

echo "== PyInstaller"
"$PY" -m PyInstaller --noconfirm --clean --windowed \
  --name "$NAME" \
  --osx-bundle-identifier "io.github.plugin73.poisk-kartinok" \
  --workpath "$BUILD/work" --specpath "$BUILD" --distpath dist \
  okno.py

# Версия в «Об этой программе» / Finder
/usr/libexec/PlistBuddy -c "Set :CFBundleShortVersionString $VERSION" "$APP/Contents/Info.plist"
/usr/libexec/PlistBuddy -c "Add :NSHighResolutionCapable bool true" "$APP/Contents/Info.plist" 2>/dev/null || true

echo "== Встраиваю Chromium"
ditto "$BUILD/ms-playwright" "$APP/Contents/Resources/ms-playwright"

echo "== Подпись (ad-hoc, без аккаунта разработчика)"
codesign --force --deep --sign - "$APP"

echo "== Самопроверка"
REPORT="$BUILD/самопроверка.txt"
POISK_DATA="$PWD/$BUILD/данные" "$APP/Contents/MacOS/$NAME" --self-test "$REPORT" || {
  cat "$REPORT" 2>/dev/null; echo "Самопроверка провалена"; exit 1; }

echo "== DMG"
DMG_DIR="$BUILD/dmg"
mkdir -p "$DMG_DIR"
ditto "$APP" "$DMG_DIR/$NAME.app"
ln -s /Applications "$DMG_DIR/Программы (Applications)"
hdiutil create -volname "$NAME" -srcfolder "$DMG_DIR" -fs HFS+ -format UDZO -ov "$DMG"

echo "== Готово: $DMG ($(du -h "$DMG" | cut -f1))"
