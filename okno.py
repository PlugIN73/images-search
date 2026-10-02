# -*- coding: utf-8 -*-
"""Окно программы «Поиск картинок»."""
import os
import sys

if sys.platform == "darwin" and not any(os.environ.get(v) for v in ("LC_ALL", "LC_CTYPE", "LANG")):
    # При запуске из Finder macOS не передаёт кодировку, и Tcl/Tk не может
    # прочитать свои файлы, если в пути есть русские буквы («Поиск картинок.app»).
    # Задаём до импорта tkinter.
    os.environ["LANG"] = "ru_RU.UTF-8"

import json
import re
import queue
import subprocess
import threading
import webbrowser
from datetime import datetime

import poisk

ФАЙЛ_СОСТОЯНИЯ = poisk.СЛУЖЕБНАЯ / ".кадры.json"
ФАЙЛ_ЖУРНАЛА = poisk.СЛУЖЕБНАЯ / "журнал.txt"
МАКС_ЖУРНАЛ = 2 * 1024 * 1024
НА_WINDOWS = sys.platform.startswith("win")
ШРИФТ = "Consolas" if НА_WINDOWS else "Menlo"

if НА_WINDOWS:
    # Чёткое окно на экранах с масштабом 125–200%, иначе Windows его размывает.
    try:
        import ctypes
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass

import tkinter as tk                      # noqa: E402  (после настройки DPI)
from tkinter import ttk, messagebox      # noqa: E402


def в_журнал(msg):
    """Пишет строку в журнал.txt; когда файл разрастается — начинает новый."""
    try:
        if ФАЙЛ_ЖУРНАЛА.exists() and ФАЙЛ_ЖУРНАЛА.stat().st_size > МАКС_ЖУРНАЛ:
            ФАЙЛ_ЖУРНАЛА.replace(ФАЙЛ_ЖУРНАЛА.with_name("журнал (старый).txt"))
        with open(ФАЙЛ_ЖУРНАЛА, "a", encoding="utf-8") as f:
            f.write(msg + "\n")
    except Exception:
        pass


def в_консоль(msg):
    # В готовом приложении для Windows консоли нет вовсе (sys.stdout = None).
    if sys.stdout is not None:
        try:
            print(msg, flush=True)
        except Exception:
            pass

# На Mac с русской раскладкой Cmd+V приходит как Cmd+М и Tk его не понимает.
# Ловим клавиши по их месту на клавиатуре, а не по букве.
КЛАВИШИ = {
    "<<Paste>>": {"v", "V", "Cyrillic_em", "Cyrillic_EM"},
    "<<Copy>>": {"c", "C", "Cyrillic_es", "Cyrillic_ES"},
    "<<Cut>>": {"x", "X", "Cyrillic_che", "Cyrillic_CHE"},
    "<<SelectAll>>": {"a", "A", "Cyrillic_ef", "Cyrillic_EF"},
}


def _горячие_клавиши(event):
    for действие, keysyms in КЛАВИШИ.items():
        if event.keysym in keysyms:
            w = event.widget
            if действие == "<<SelectAll>>":
                try:
                    w.select_range(0, "end"); w.icursor("end")
                except Exception:
                    w.tag_add("sel", "1.0", "end")
            elif действие == "<<Paste>>":
                вставить(w)
            else:
                w.event_generate(действие)
            return "break"


def вставить(w):
    try:
        текст = w.clipboard_get()
    except Exception:
        return
    try:
        if w.selection_present():
            w.delete("sel.first", "sel.last")
    except Exception:
        try:
            w.delete("sel.first", "sel.last")
        except Exception:
            pass
    if isinstance(w, tk.Text):
        w.insert("insert", текст)
    else:
        w.insert("insert", текст.replace("\n", " ").strip())


def _меню(event):
    w = event.widget
    w.focus_set()
    m = tk.Menu(w, tearoff=0)
    m.add_command(label="Вставить", command=lambda: вставить(w))
    m.add_command(label="Копировать", command=lambda: w.event_generate("<<Copy>>"))
    m.add_command(label="Вырезать", command=lambda: w.event_generate("<<Cut>>"))
    m.tk_popup(event.x_root, event.y_root)


def похоже_на_кадр(t):
    return bool(re.fullmatch(r"(кадр|frame|shot|#|№)?\s*\d+[a-zа-я]?\.?", t.strip(), re.I))


def разобрать_строку(line):
    """«4 - запрос|запрос|запрос» → ("4", "запрос|запрос|запрос").
    Номер кадра остаётся в тексте, его разбирает уже сам поиск."""
    line = line.strip().lstrip("\ufeff")
    if not line:
        return None
    line = re.sub(r"^\s*(?:кадр|frame|shot)\s*(\d+)\s*[:\-–—.)]\s*", r"\1 - ", line, flags=re.I)
    m = re.match(r"^\s*(\d+)\s*[:\-–—.)]\s*(.+)$", line)
    имя = m.group(1) if m else None
    return имя, line, ""


def модификаторы(платформа=sys.platform):
    """Клавиши-модификаторы для Копировать/Вставить. На Windows Tk считает
    включённый NumLock модификатором Command — с ним любая буква стала бы командой."""
    return ["Command", "Control"] if платформа == "darwin" else ["Control"]


КЛАВИША_ГОТОВО = "Cmd+Enter" if sys.platform == "darwin" else "Ctrl+Enter"


def шаги_прокрутки(delta, на_windows=НА_WINDOWS):
    """Сколько строк прокрутить. Windows шлёт колесо порциями по 120, а тачпад —
    мелкими (±40); Mac — маленькими числами. Направление сохраняем всегда."""
    if not delta:
        return 0
    if на_windows:
        шаги = int(delta / 120) or (1 if delta > 0 else -1)
    else:
        шаги = delta
    return -шаги


def настроить_клавиши(root):
    for класс in ("TEntry", "Entry", "Text", "TSpinbox"):
        for mod in модификаторы():
            root.bind_class(класс, f"<{mod}-KeyPress>", _горячие_клавиши, add="+")
        for кнопка in ("<Button-2>", "<Button-3>", "<Control-Button-1>"):
            root.bind_class(класс, кнопка, _меню, add="+")


class Окно:
    def __init__(self, root):
        self.root = root
        root.title(f"Поиск картинок в Яндексе — версия {poisk.ВЕРСИЯ}")
        м = self._масштаб()
        root.geometry(f"{int(980 * м)}x{int(720 * м)}")
        root.minsize(int(760 * м), int(520 * м))
        self.очередь = queue.Queue()
        self.стоп = threading.Event()
        self.поток = None
        self.строки = []

        # --- верх ---
        верх = ttk.Frame(root, padding=(12, 10, 12, 0))
        верх.pack(fill="x")
        ttk.Label(верх, text="Картинок на каждый запрос:").pack(side="left")
        self.сколько = tk.IntVar(value=2)
        ttk.Spinbox(верх, from_=1, to=20, width=4, textvariable=self.сколько).pack(side="left", padx=(6, 18))
        ttk.Label(верх, text="Браузеров сразу:").pack(side="left")
        self.дорожек = tk.IntVar(value=2)
        ttk.Spinbox(верх, from_=1, to=4, width=3, textvariable=self.дорожек).pack(side="left", padx=(6, 18))
        self.обновление = ttk.Label(верх, text="", foreground="#c0392b", cursor="hand2")
        self.обновление.pack(side="right")

        # --- таблица кадров ---
        рамка = ttk.Frame(root, padding=(12, 8, 12, 0))
        рамка.pack(fill="both", expand=True)
        self.холст = tk.Canvas(рамка, highlightthickness=0, height=220)
        прокрутка = ttk.Scrollbar(рамка, orient="vertical", command=self.холст.yview)
        self.таблица = ttk.Frame(self.холст)
        self.таблица.bind("<Configure>", lambda e: self.холст.configure(scrollregion=self.холст.bbox("all")))
        self.холст.create_window((0, 0), window=self.таблица, anchor="nw")
        self.холст.configure(yscrollcommand=прокрутка.set)
        for i, текст in enumerate(["Кадр", "Запросы (несколько — через |)"]):
            ttk.Label(self.таблица, text=текст, font=("", 12, "bold")).grid(row=0, column=i, sticky="w", padx=3)
        self.ряд = 0
        self.холст.pack(side="left", fill="both", expand=True)
        прокрутка.pack(side="right", fill="y")
        self.холст.bind_all("<MouseWheel>", self._колесо)

        кнопки = ttk.Frame(root, padding=(12, 6, 12, 0))
        кнопки.pack(fill="x")
        ttk.Button(кнопки, text="+ Добавить кадр", command=self.добавить_строку).pack(side="left")
        ttk.Button(кнопки, text="Вставить списком…", command=self.вставить_списком).pack(side="left", padx=6)
        ttk.Button(кнопки, text="Очистить всё", command=self.очистить).pack(side="left")

        # --- запуск ---
        низ = ttk.Frame(root, padding=(12, 10, 12, 0))
        низ.pack(fill="x")
        self.кнопка_старт = ttk.Button(низ, text="▶  Найти картинки", command=self.старт)
        self.кнопка_старт.pack(side="left")
        self.кнопка_стоп = ttk.Button(низ, text="■  Стоп", command=self.остановить, state="disabled")
        self.кнопка_стоп.pack(side="left", padx=6)
        ttk.Button(низ, text="Открыть папку с результатами", command=self.открыть_папку).pack(side="right")

        # --- журнал ---
        журнал_рамка = ttk.Frame(root, padding=12)
        журнал_рамка.pack(fill="both", expand=True)
        self.журнал = tk.Text(журнал_рамка, height=12, wrap="word", state="disabled",
                              font=(ШРИФТ, 11), relief="solid", borderwidth=1)
        self.журнал.pack(fill="both", expand=True)

        self.загрузить()
        poisk.ВЫВОД[0] = lambda msg: self.очередь.put(msg)
        root.protocol("WM_DELETE_WINDOW", self.закрыть)
        root.after(150, self._читать_очередь)
        в_журнал(f"\n=== Запуск {datetime.now():%d.%m.%Y %H:%M}, версия {poisk.ВЕРСИЯ} ===")
        threading.Thread(target=self._проверить_обновление, daemon=True).start()

    def _проверить_обновление(self):
        новая = poisk.проверить_обновление()
        if новая:
            версия, ссылка = новая
            self.root.after(0, lambda: self._показать_обновление(версия, ссылка))

    def _показать_обновление(self, версия, ссылка):
        self.обновление.configure(text=f"Вышла версия {версия} — скачать")
        self.обновление.bind("<Button-1>", lambda e: webbrowser.open(ссылка))
        self.очередь.put(f"Вышла новая версия {версия}. Скачать: {ссылка}")

    # ---------- таблица ----------
    def добавить_строку(self, имя=None, ru="", en=""):
        n = len(self.строки) + 1
        имя = имя or f"Кадр {n}"
        self.ряд += 1
        р = self.ряд
        vars_ = (tk.StringVar(value=имя), tk.StringVar(value=ru))
        поля = []
        for i, (v, ширина) in enumerate(zip(vars_, (10, 80))):
            e = ttk.Entry(self.таблица, textvariable=v, width=ширина)
            e.grid(row=р, column=i, padx=3, pady=2, sticky="we")
            поля.append(e)
        кнопка = ttk.Button(self.таблица, text="✕", width=2)
        кнопка.grid(row=р, column=2, padx=3)
        строка = {"vars": vars_, "виджеты": поля + [кнопка]}
        кнопка.configure(command=lambda: self.удалить_строку(строка))
        self.строки.append(строка)
        поля[1].focus_set()
        self.root.after(50, lambda: self.холст.yview_moveto(1.0))

    def удалить_строку(self, строка):
        for w in строка["виджеты"]:
            w.destroy()
        self.строки.remove(строка)

    def очистить(self):
        if self.строки and messagebox.askyesno("Очистить", "Удалить все кадры из списка?"):
            for с in list(self.строки):
                self.удалить_строку(с)
            self.добавить_строку()

    def данные(self):
        return [{"имя": с["vars"][0].get().strip(), "ru": с["vars"][1].get().strip()}
                for с in self.строки]

    def вставить_списком(self):
        окно = tk.Toplevel(self.root)
        окно.title("Вставить списком")
        м = self._масштаб()
        окно.geometry(f"{int(700 * м)}x{int(520 * м)}")
        ttk.Label(окно, padding=10, justify="left", text=(
            "Одна строка = один кадр, запросы через |:\n"
            "   53 - кот на подоконнике|кот у окна|кошка подоконник солнце\n"
            "   54 - старый трамвай зимой|трамвай снег город\n"
            "На каждый запрос скачается своя картинка.\n"
            "Если кадр с таким номером уже есть — он заменится.\n"
            f"Когда вставил — нажми «Готово» внизу (или {КЛАВИША_ГОТОВО}).")).pack(anchor="w")
        низ = ttk.Frame(окно)
        низ.pack(side="bottom", pady=10)
        поле = tk.Text(окно, wrap="word", font=(ШРИФТ, 12), height=10)
        поле.pack(fill="both", expand=True, padx=10)
        поле.focus_set()
        окно.grab_set()

        def добавить():
            новые = [разобрать_строку(l) for l in поле.get("1.0", "end").splitlines()]
            имена = {к[0] for к in новые if к and к[0]}
            пустые = [с for с in self.строки if not any(v.get().strip() for v in с["vars"][1:])
                      or с["vars"][0].get().strip() in имена]
            for с in пустые:
                self.удалить_строку(с)
            for line in поле.get("1.0", "end").splitlines():
                кадр = разобрать_строку(line)
                if кадр:
                    self.добавить_строку(*кадр)
            окно.destroy()

        ttk.Button(низ, text="Вставить из буфера", command=lambda: вставить(поле)).pack(side="left", padx=6)
        ttk.Button(низ, text="✓  Готово — добавить кадры", command=добавить).pack(side="left", padx=6)
        поле.bind(f"<{'Command' if sys.platform == 'darwin' else 'Control'}-Return>",
                  lambda e: (добавить(), "break")[1])

    def _колесо(self, e):
        self.холст.yview_scroll(шаги_прокрутки(e.delta), "units")

    def _масштаб(self):
        """Во сколько раз крупнее рисовать окно: на Windows с масштабом экрана
        125–200% шрифты растут, а размеры в пикселях — нет."""
        if not НА_WINDOWS:
            return 1.0
        try:
            return max(1.0, float(self.root.tk.call("tk", "scaling")) / (96 / 72))
        except Exception:
            return 1.0

    # ---------- сохранение ----------
    def загрузить(self):
        try:
            for к in json.loads(ФАЙЛ_СОСТОЯНИЯ.read_text(encoding="utf-8")):
                self.добавить_строку(к.get("имя"), к.get("ru", ""), к.get("en", ""))
        except Exception:
            pass
        if not self.строки:
            self.добавить_строку()

    def сохранить(self):
        try:
            ФАЙЛ_СОСТОЯНИЯ.write_text(json.dumps(self.данные(), ensure_ascii=False, indent=1), encoding="utf-8")
        except Exception:
            pass

    # ---------- запуск ----------
    def старт(self):
        кадры = [к for к in self.данные() if к["ru"]]
        if not кадры:
            messagebox.showinfo("Нет запросов", "Впиши хотя бы один запрос.")
            return
        for к in кадры:
            if not к["имя"]:
                к["имя"] = "без названия"
        self.сохранить()
        try:
            сколько = max(1, int(self.сколько.get()))
        except Exception:
            сколько = 2
        try:
            дорожек = max(1, min(4, int(self.дорожек.get())))
        except Exception:
            дорожек = 3
        self.стоп.clear()
        self.кнопка_старт.configure(state="disabled")
        self.кнопка_стоп.configure(state="normal")
        self.поток = threading.Thread(target=self._работа, args=(кадры, сколько, дорожек), daemon=True)
        self.поток.start()

    def _работа(self, кадры, сколько, дорожек):
        try:
            poisk.запустить(кадры, сколько, self.стоп, дорожек)
        except Exception as e:
            self.очередь.put(f"\nОшибка: {e}")
        self.очередь.put(None)

    def остановить(self):
        self.стоп.set()
        self.очередь.put("Останавливаю после текущей картинки...")

    def _читать_очередь(self):
        try:
            while True:
                msg = self.очередь.get_nowait()
                if msg is None:
                    self.кнопка_старт.configure(state="normal")
                    self.кнопка_стоп.configure(state="disabled")
                    continue
                в_консоль(msg)
                в_журнал(msg)
                self.журнал.configure(state="normal")
                self.журнал.insert("end", msg + "\n")
                self.журнал.see("end")
                self.журнал.configure(state="disabled")
                if "Я не робот" in msg:
                    self.root.bell()
        except queue.Empty:
            pass
        self.root.after(150, self._читать_очередь)

    def открыть_папку(self):
        poisk.ПАПКА_РЕЗУЛЬТАТОВ.mkdir(parents=True, exist_ok=True)
        путь = str(poisk.ПАПКА_РЕЗУЛЬТАТОВ)
        try:
            if sys.platform == "darwin":
                subprocess.Popen(["open", путь])
            elif sys.platform.startswith("win"):
                os.startfile(путь)   # noqa
            else:
                subprocess.Popen(["xdg-open", путь])
        except Exception as e:
            messagebox.showinfo("Папка с результатами", f"{путь}\n\n({e})")

    def закрыть(self):
        self.сохранить()
        self.стоп.set()
        self.root.destroy()


def _самопроверка(путь_отчёта):
    """Запуск с ключом --self-test: проверка собранного пакета без участия человека."""
    строки = []

    def записать(msg=""):
        строки.append(str(msg))
        в_консоль(msg)

    poisk.ВЫВОД[0] = записать
    хорошо = poisk.самопроверка()
    try:
        root = tk.Tk()
        root.withdraw()
        root.update()
        root.destroy()
        записать("Окно (Tk): ок")
    except Exception as e:
        записать(f"Окно (Tk): ОШИБКА {e}")
        хорошо = False
    записать("САМОПРОВЕРКА: " + ("ОК" if хорошо else "ПРОВАЛЕНА"))
    if путь_отчёта:
        with open(путь_отчёта, "w", encoding="utf-8") as f:
            f.write("\n".join(строки) + "\n")
    return 0 if хорошо else 1


if __name__ == "__main__":
    if "--self-test" in sys.argv:
        i = sys.argv.index("--self-test")
        sys.exit(_самопроверка(sys.argv[i + 1] if len(sys.argv) > i + 1 else None))
    root = tk.Tk()
    замок = poisk.занять_замок(poisk.СЛУЖЕБНАЯ / ".запущено")
    if замок is None:
        root.withdraw()
        messagebox.showinfo("Поиск картинок", "Программа уже запущена — посмотрите среди открытых окон.")
        sys.exit(0)
    настроить_клавиши(root)
    Окно(root)
    root.lift()
    root.attributes("-topmost", True)
    root.after(500, lambda: root.attributes("-topmost", False))
    root.mainloop()
