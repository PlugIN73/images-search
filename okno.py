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
import time
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


def разобрать_строку(line):
    """«4 - запрос|запрос|запрос» → ("4", "запрос|запрос|запрос").
    Без номера в начале → (None, строка); пустая строка → None."""
    line = line.strip().lstrip("\ufeff").strip()
    if not line:
        return None
    line = re.sub(r"^\s*(?:кадр|frame|shot)\s*(\d+)\s*[:\-–—.)]\s*", r"\1 - ", line, flags=re.I)
    return poisk.разделить_номер(line)


def следующий_номер(имена):
    """Номер для нового кадра: на единицу больше самого большого (а не «сколько строк + 1» —
    после удаления строки номера повторялись бы)."""
    числа = [int(m.group(1)) for и in имена if (m := re.match(r"\s*(\d+)", и or ""))]
    return str(max(числа, default=0) + 1)


def привести_кадр(имя, ru):
    """Номер кадра живёт только в поле «Кадр». В старых списках он стоял ещё и в начале
    запроса, а «Кадр 3» и «без названия» программа придумывала сама."""
    имя = (имя or "").strip()
    if poisk.АВТОИМЯ.fullmatch(имя):
        имя = ""
    первый, *остальные = (ru or "").split("|")
    номер, запрос = poisk.разделить_номер(первый)
    if номер and (not имя or имя == номер):
        return номер, "|".join([запрос, *остальные])
    return имя, ru


def пронумеровать(новые, имеющиеся):
    """Строкам без номера — следующие свободные номера по порядку: после самого большого
    и среди уже имеющихся кадров, и среди номеров в самой вставке."""
    следующий = int(следующий_номер([*имеющиеся, *(номер for номер, _ in новые if номер)]))
    итог = []
    for номер, запросы in новые:
        if not номер:
            номер, следующий = str(следующий), следующий + 1
        итог.append((номер, запросы))
    return итог


def сводка_вставки(текст, имеющиеся=()):
    кадры = [к for к in map(разобрать_строку, текст.splitlines()) if к]
    if not кадры:
        return "Вставь список в поле ниже."
    без_номера = [i for i, (номер, _) in enumerate(кадры) if not номер]
    if not без_номера:
        return f"Будет кадров: {len(кадры)}."
    номера = [пронумеровать(кадры, имеющиеся)[i][0] for i in без_номера]
    дам = f"дам номер {номера[0]}" if len(номера) == 1 else f"дам номера с {номера[0]} по {номера[-1]}"
    return f"Будет кадров: {len(кадры)}. Без номера: {len(номера)} — {дам}."


def осталось_минут(прошло, сделано, всего):
    if not сделано:
        return None
    return round(прошло / сделано * (всего - сделано) / 60)


def текст_прогресса(д, прошло):
    текст = (f"Кадров готово: {д['кадров_готово']} из {д['кадров']} · "
             f"запросов: {д['сделано']} из {д['всего']}")
    минут = осталось_минут(прошло, д["сделано"], д["всего"])
    if минут is None or д["сделано"] >= д["всего"]:
        return текст
    return текст + (f" · осталось ≈ {минут} мин" if минут >= 1 else " · осталось меньше минуты")


def число_в_пределах(текст, мин, макс, по_умолчанию):
    try:
        число = int(str(текст).strip())
    except ValueError:
        return по_умолчанию
    return max(мин, min(макс, число))


def можно_ввести(текст):
    """В поля-счётчики можно вписать только число из одной-двух цифр."""
    return текст == "" or (текст.isdigit() and len(текст) <= 2)


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


def мигнуть(root):
    """Мигнуть кнопкой программы на панели задач Windows, чтобы капчу заметили.
    На Mac о капче сообщает звук."""
    if not НА_WINDOWS:
        return
    try:
        import ctypes
        ctypes.windll.user32.FlashWindow(int(root.wm_frame(), 16), True)
    except Exception:
        pass


def настроить_клавиши(root):
    for класс in ("TEntry", "Entry", "Text", "TSpinbox"):
        for mod in модификаторы():
            root.bind_class(класс, f"<{mod}-KeyPress>", _горячие_клавиши, add="+")
        for кнопка in ("<Button-2>", "<Button-3>", "<Control-Button-1>"):
            root.bind_class(класс, кнопка, _меню, add="+")


# Плашка «Я не робот»: тёплый жёлтый, заметный и в светлой, и в тёмной теме.
ЦВЕТ_ПЛАШКИ = ("#fff4ce", "#4d3800")


class Окно:
    def __init__(self, root):
        self.root = root
        root.title(f"Поиск картинок в Яндексе — версия {poisk.ВЕРСИЯ}")
        м = self._масштаб()
        root.geometry(f"{int(980 * м)}x{int(720 * м)}")
        root.minsize(int(760 * м), int(520 * м))
        root.columnconfigure(0, weight=1)
        self.очередь = queue.Queue()
        self.стоп = threading.Event()
        self.поток = None
        self.строки = []
        self.идёт = False
        self.капчи = 0
        self.старт_время = 0.0
        self.конец_пришёл = False
        проверка = (root.register(можно_ввести), "%P")

        # --- новая версия (появляется, только если вышла) ---
        self.обновление = ttk.Button(root)
        self.обновление.grid(row=0, column=0, sticky="e", padx=12, pady=(8, 0))
        self.обновление.grid_remove()

        # --- список кадров ---
        верх = ttk.Frame(root, padding=(12, 10, 12, 0))
        верх.grid(row=1, column=0, sticky="we")
        self.кнопка_вставить = ttk.Button(верх, text="Вставить списком…", command=self.вставить_списком)
        self.кнопка_вставить.pack(side="left")
        self.кнопка_добавить = ttk.Button(верх, text="+ Добавить кадр",
                                          command=lambda: self.добавить_строку(фокус=True))
        self.кнопка_добавить.pack(side="left", padx=(8, 0))
        self.кнопка_очистить = ttk.Button(верх, text="Очистить всё", command=self.очистить)
        self.кнопка_очистить.pack(side="right")

        рамка = ttk.Frame(root, padding=(12, 8, 12, 0))
        рамка.grid(row=2, column=0, sticky="nsew")
        root.rowconfigure(2, weight=1)
        self.холст = tk.Canvas(рамка, highlightthickness=0, height=int(200 * м))
        прокрутка = ttk.Scrollbar(рамка, orient="vertical", command=self.холст.yview)
        self.таблица = ttk.Frame(self.холст)
        self.таблица.columnconfigure(1, weight=1)
        self.таблица.bind("<Configure>", lambda e: self.холст.configure(scrollregion=self.холст.bbox("all")))
        окно_таблицы = self.холст.create_window((0, 0), window=self.таблица, anchor="nw")
        self.холст.bind("<Configure>", lambda e: self.холст.itemconfigure(окно_таблицы, width=e.width))
        self.холст.configure(yscrollcommand=прокрутка.set)
        for i, текст in enumerate(["Кадр", "Запросы (несколько — через |)"]):
            ttk.Label(self.таблица, text=текст, font="TkHeadingFont").grid(row=0, column=i, sticky="w", padx=3)
        self.пусто = ttk.Label(self.таблица, justify="left", padding=(3, 12), text=(
            "Список пока пуст.\n"
            "Нажми «Вставить списком…» и вставь сценарий: одна строка — один кадр.\n"
            "Или добавь кадры по одному кнопкой «+ Добавить кадр»."))
        self.ряд = 0
        self.холст.pack(side="left", fill="both", expand=True)
        прокрутка.pack(side="right", fill="y")
        self.холст.bind_all("<MouseWheel>", self._колесо)
        self.холст.bind_all("<Button-4>", lambda e: self._колесо(e, -1))
        self.холст.bind_all("<Button-5>", lambda e: self._колесо(e, 1))

        # --- запуск и настройки ---
        запуск = ttk.Frame(root, padding=(12, 10, 12, 0))
        запуск.grid(row=3, column=0, sticky="we")
        self.кнопка_старт = ttk.Button(запуск, text="Найти картинки", command=self.старт, default="active")
        self.кнопка_старт.pack(side="left")
        self.кнопка_стоп = ttk.Button(запуск, text="Стоп", command=self.остановить, state="disabled")
        self.кнопка_стоп.pack(side="left", padx=(8, 24))
        ttk.Label(запуск, text="Картинок на запрос:").pack(side="left")
        self.сколько = tk.StringVar(value="2")
        self.поле_сколько = ttk.Spinbox(запуск, from_=1, to=20, width=3, textvariable=self.сколько,
                                        validate="key", validatecommand=проверка)
        self.поле_сколько.pack(side="left", padx=(6, 16))
        ttk.Label(запуск, text="Браузеров сразу:").pack(side="left")
        self.дорожек = tk.StringVar(value="2")
        self.поле_дорожек = ttk.Spinbox(запуск, from_=1, to=4, width=3, textvariable=self.дорожек,
                                        validate="key", validatecommand=проверка)
        self.поле_дорожек.pack(side="left", padx=(6, 6))
        self.совет = ttk.Label(запуск, text="")
        self.совет.pack(side="left")
        self.дорожек.trace_add("write", lambda *_: self._совет_про_браузеры())
        self.кнопка_папка = ttk.Button(запуск, text="Открыть папку с результатами", command=self.открыть_папку)
        self.кнопка_папка.pack(side="right")

        # --- состояние ---
        состояние = ttk.Frame(root, padding=(12, 10, 12, 0))
        состояние.grid(row=4, column=0, sticky="we")
        состояние.columnconfigure(0, weight=1)
        self.надпись = ttk.Label(состояние, text="")
        self.надпись.grid(row=0, column=0, sticky="w")
        self.кнопка_подробно = ttk.Button(состояние, text="Скрыть подробности", command=self._подробности)
        self.кнопка_подробно.grid(row=0, column=1, sticky="e")
        self.кнопка_подробно.grid_remove()
        self.полоса = ttk.Progressbar(состояние, mode="determinate")
        self.полоса.grid(row=1, column=0, columnspan=2, sticky="we", pady=(6, 0))
        self.полоса.grid_remove()

        self.плашка = tk.Label(root, justify="left", anchor="w", padx=12, pady=10,
                               bg=ЦВЕТ_ПЛАШКИ[0], fg=ЦВЕТ_ПЛАШКИ[1], text=(
                                   "Яндекс просит подтвердить «Я не робот».\n"
                                   "Поставь галочку в окне браузера — поиск продолжится сам. "
                                   "Жду 10 минут, потом пропущу этот запрос."))
        self.плашка.grid(row=5, column=0, sticky="we", padx=12, pady=(10, 0))
        self.плашка.grid_remove()

        # --- журнал: появляется, когда в нём что-то есть ---
        self.журнал_рамка = ttk.Frame(root, padding=12)
        self.журнал_рамка.grid(row=6, column=0, sticky="nsew")
        self.журнал = tk.Text(self.журнал_рамка, height=8, wrap="word", state="disabled",
                              font="TkTextFont", relief="solid", borderwidth=1)
        self.журнал.pack(fill="both", expand=True)
        self.журнал_рамка.grid_remove()
        self.журнал_виден = False
        self.журнал_пуст = True

        for mod in модификаторы():
            root.bind(f"<{mod}-Return>", lambda e: (self.старт(), "break")[1])

        self.загрузить()
        self._ждём_запуска()
        poisk.ВЫВОД[0] = lambda msg: self.очередь.put(msg)
        poisk.СОБЫТИЯ[0] = lambda тип, данные: self.очередь.put((тип, данные))
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
        self.обновление.configure(text=f"Вышла версия {версия} — скачать",
                                  command=lambda: webbrowser.open(ссылка))
        self.обновление.grid()
        в_журнал(f"Вышла новая версия {версия}. Скачать: {ссылка}")

    def _совет_про_браузеры(self):
        много = число_в_пределах(self.дорожек.get(), 1, 4, 2) >= 3
        self.совет.configure(text="больше браузеров — чаще капча" if много else "")

    # ---------- таблица ----------
    def добавить_строку(self, имя=None, ru="", фокус=False):
        if имя is None:
            имя = следующий_номер(с["vars"][0].get() for с in self.строки)
        self.ряд += 1
        р = self.ряд
        vars_ = (tk.StringVar(value=имя), tk.StringVar(value=ru))
        поля = []
        for i, (v, ширина) in enumerate(zip(vars_, (6, 60))):
            e = ttk.Entry(self.таблица, textvariable=v, width=ширина)
            e.grid(row=р, column=i, padx=3, pady=2, sticky="we")
            e.bind("<FocusIn>", lambda ev: self._показать(ev.widget), add="+")
            поля.append(e)
        # ✕ не в порядке Tab: иначе на длинном списке до «Найти» сотни нажатий, а пробел удаляет строку
        кнопка = ttk.Button(self.таблица, text="✕", width=2, takefocus=0)
        кнопка.grid(row=р, column=2, padx=3)
        строка = {"vars": vars_, "виджеты": поля + [кнопка]}
        кнопка.configure(command=lambda: self.удалить_строку(строка))
        self.строки.append(строка)
        self._обновить_пустоту()
        if фокус:
            поля[1].focus_set()
            self.root.after(50, lambda: self.холст.yview_moveto(1.0))

    def удалить_строку(self, строка):
        for w in строка["виджеты"]:
            w.destroy()
        self.строки.remove(строка)
        self._обновить_пустоту()

    def _обновить_пустоту(self):
        if self.строки:
            self.пусто.grid_remove()
        else:
            self.пусто.grid(row=1, column=0, columnspan=3, sticky="w")
        if not self.идёт:
            self._ждём_запуска()

    def _показать(self, w):
        """Строка, в которую перешли по Tab, должна быть видна."""
        self.root.update_idletasks()
        всего = max(1, self.таблица.winfo_height())
        видно = self.холст.winfo_height()
        с = self.холст.canvasy(0)
        верх, низ = w.winfo_y(), w.winfo_y() + w.winfo_height()
        if верх < с:
            self.холст.yview_moveto(верх / всего)
        elif низ > с + видно:
            self.холст.yview_moveto((низ - видно) / всего)

    def очистить(self):
        if self.строки and messagebox.askyesno("Очистить", "Удалить все кадры из списка?"):
            for с in list(self.строки):
                self.удалить_строку(с)

    def данные(self):
        return [{"имя": с["vars"][0].get().strip(), "ru": с["vars"][1].get().strip()}
                for с in self.строки]

    def вставить_списком(self):
        окно = tk.Toplevel(self.root)
        окно.title("Вставить списком")
        окно.transient(self.root)
        м = self._масштаб()
        окно.geometry(f"{int(700 * м)}x{int(520 * м)}")
        ttk.Label(окно, padding=12, justify="left", text=(
            "Одна строка — один кадр. Сначала номер кадра, потом запросы через |:\n"
            "   53 - кот на подоконнике|кот у окна|кошка подоконник солнце\n"
            "   54 - старый трамвай зимой|трамвай снег город\n"
            "Номер кадра попадёт в имена файлов. Если кадр с таким номером уже есть — он заменится.\n"
            "Строкам без номера дам следующие свободные номера по порядку.\n"
            f"Когда вставишь — нажми «Готово» внизу (или {КЛАВИША_ГОТОВО}).")).pack(anchor="w")
        низ = ttk.Frame(окно, padding=(12, 8, 12, 12))
        низ.pack(side="bottom", fill="x")
        сводка = ttk.Label(низ, text=сводка_вставки(""))
        сводка.pack(side="top", anchor="w", pady=(0, 8))
        поле = tk.Text(окно, wrap="word", font=(ШРИФТ, 12), height=10)
        поле.pack(fill="both", expand=True, padx=12)
        поле.focus_set()
        окно.grab_set()

        def имеющиеся():
            """Номера кадров, которые останутся: пустые строки вставка убирает."""
            return [с["vars"][0].get() for с in self.строки if с["vars"][1].get().strip()]

        def обновить_сводку(*_):
            сводка.configure(text=сводка_вставки(поле.get("1.0", "end"), имеющиеся()))

        def добавить():
            новые = [к for к in map(разобрать_строку, поле.get("1.0", "end").splitlines()) if к]
            новые = пронумеровать(новые, имеющиеся())
            номера = {номер for номер, _ in новые}
            лишние = [с for с in self.строки if not с["vars"][1].get().strip()
                      or с["vars"][0].get().strip() in номера]
            for с in лишние:
                self.удалить_строку(с)
            for номер, запросы in новые:
                self.добавить_строку(номер, запросы)
            self.root.after(50, lambda: self.холст.yview_moveto(1.0))
            окно.destroy()

        def из_буфера():
            вставить(поле)
            обновить_сводку()

        поле.bind("<KeyRelease>", обновить_сводку, add="+")
        поле.bind("<<Paste>>", lambda e: окно.after(10, обновить_сводку), add="+")
        ttk.Button(низ, text="Вставить из буфера", command=из_буфера).pack(side="left")
        ttk.Button(низ, text="Готово — добавить кадры", command=добавить,
                   default="active").pack(side="left", padx=(8, 0))
        ttk.Button(низ, text="Отмена", command=окно.destroy).pack(side="right")
        окно.bind("<Escape>", lambda e: окно.destroy())
        поле.bind(f"<{'Command' if sys.platform == 'darwin' else 'Control'}-Return>",
                  lambda e: (добавить(), "break")[1])

    def _колесо(self, e, шаги=None):
        # Колесо крутит список, только когда мышь над ним: журнал и окно вставки прокручиваются сами.
        try:
            под_мышью = str(self.root.winfo_containing(e.x_root, e.y_root) or "")
        except KeyError:   # под мышью виджет, о котором Python не знает (выпадающий список и т. п.)
            return
        if not под_мышью.startswith(str(self.холст)):
            return
        self.холст.yview_scroll(шаги if шаги is not None else шаги_прокрутки(e.delta), "units")

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
                self.добавить_строку(*привести_кадр(к.get("имя"), к.get("ru", "")))
        except Exception:
            pass
        self._обновить_пустоту()

    def сохранить(self):
        try:
            ФАЙЛ_СОСТОЯНИЯ.write_text(json.dumps(self.данные(), ensure_ascii=False, indent=1), encoding="utf-8")
        except Exception:
            pass

    # ---------- состояние ----------
    def _ждём_запуска(self):
        if self.строки:
            self._надпись(f"Кадров в списке: {len(self.строки)}. Нажми «Найти картинки» "
                          f"(или {КЛАВИША_ГОТОВО}).")
        else:
            self._надпись("Вставь список кадров, чтобы начать.")

    def _надпись(self, текст):
        self.надпись.configure(text=текст)

    def _главная(self, кнопка):
        """Выделенная кнопка — та, что нужна следующей: «Найти» до поиска, «Открыть папку» после."""
        for к in (self.кнопка_старт, self.кнопка_папка):
            к.configure(default="active" if к is кнопка else "normal")

    def _заблокировать(self, да):
        """Пока идёт поиск, список и настройки не меняются: правки всё равно не учлись бы."""
        состояние = "disabled" if да else "normal"
        for с in self.строки:
            for w in с["виджеты"]:
                w.configure(state=состояние)
        for w in (self.кнопка_вставить, self.кнопка_добавить, self.кнопка_очистить,
                  self.поле_сколько, self.поле_дорожек):
            w.configure(state=состояние)
        self.кнопка_старт.configure(state="disabled" if да else "normal")
        self.кнопка_стоп.configure(state="normal" if да else "disabled")

    def _подробности(self, показать=None):
        self.журнал_виден = not self.журнал_виден if показать is None else показать
        if self.журнал_виден:
            self.журнал_рамка.grid()
            self.root.rowconfigure(6, weight=1)
        else:
            self.журнал_рамка.grid_remove()
            self.root.rowconfigure(6, weight=0)
        self.кнопка_подробно.configure(text="Скрыть подробности" if self.журнал_виден else "Показать подробности")
        self.кнопка_подробно.grid()

    def _плашка_капчи(self, показать):
        if показать:
            self.плашка.grid()
            if self.root.state() == "iconic":
                self.root.deiconify()
            мигнуть(self.root)
        else:
            self.плашка.grid_remove()

    # ---------- запуск ----------
    def старт(self):
        if self.идёт:
            return
        кадры = [к for к in self.данные() if к["ru"]]
        if not кадры:
            messagebox.showinfo("Нет запросов", "Нажми «Вставить списком…» и вставь сценарий "
                                                "или впиши запросы хотя бы в один кадр.")
            return
        self.сохранить()
        сколько = число_в_пределах(self.сколько.get(), 1, 20, 2)
        дорожек = число_в_пределах(self.дорожек.get(), 1, 4, 2)
        self.сколько.set(str(сколько))
        self.дорожек.set(str(дорожек))
        self.стоп.clear()
        self.идёт = True
        self.капчи = 0
        self.конец_пришёл = False
        self.старт_время = time.time()
        self._заблокировать(True)
        self._главная(None)
        self._надпись("Запускаю браузер… Он откроется отдельным окном — его не закрывай.")
        self.полоса.configure(value=0, maximum=1)
        self.полоса.grid()
        self.поток = threading.Thread(target=self._работа, args=(кадры, сколько, дорожек), daemon=True)
        self.поток.start()

    def _работа(self, кадры, сколько, дорожек):
        try:
            poisk.запустить(кадры, сколько, self.стоп, дорожек)
        except Exception as e:
            self.очередь.put(f"\nЧто-то пошло не так: {e}\n"
                             "Запусти снова — уже скачанное пропустится. Если повторяется — "
                             "пришли разработчику файл журнал.txt.")
        self.очередь.put(None)

    def остановить(self):
        self.стоп.set()
        self.кнопка_стоп.configure(state="disabled")
        текст = "Останавливаю: дождусь, пока закончатся текущие запросы…"
        self._надпись(текст)
        self.очередь.put(текст)

    def _событие(self, тип, д):
        if тип in ("начало", "запрос"):
            self.полоса.configure(maximum=max(1, д["всего"]), value=д["сделано"])
            if not self.стоп.is_set():
                self._надпись(текст_прогресса(д, time.time() - self.старт_время))
        elif тип == "капча":
            self.капчи += 1
            self._плашка_капчи(True)
        elif тип == "капча_прошла":
            self.капчи = max(0, self.капчи - 1)
            if not self.капчи:
                self._плашка_капчи(False)
        elif тип == "конец":
            self._конец(д)

    def _конец(self, д):
        self.конец_пришёл = True
        self.капчи = 0
        self._плашка_капчи(False)
        self.полоса.grid_remove()
        if д.get("нечего"):
            self._надпись("Всё из списка уже скачано. Новые кадры добавь в список и запусти снова.")
            self._главная(self.кнопка_папка)
            return
        if д["остановлено"]:
            self._надпись("Остановлено. Запусти снова — уже скачанное пропустится.")
            self._главная(self.кнопка_старт)
            return
        пустые = len(д["пустые"])
        текст = f"Готово за {д['минут']:.0f} мин: картинки есть по {д['найдено']} запросам"
        if пустые:
            текст += f", по {пустые} ничего не нашлось — список в подробностях."
            self._подробности(True)
        else:
            текст += "."
        self._надпись(текст)
        self._главная(self.кнопка_папка)
        poisk.звук()

    def _завершено(self):
        """Поток поиска закончился — сам или с ошибкой."""
        if self.идёт and not self.конец_пришёл:
            # поиск прервался с ошибкой
            self._надпись("Поиск прервался — подробности ниже.")
            self._подробности(True)
            self._главная(self.кнопка_старт)
        self.идёт = False
        self.полоса.grid_remove()
        self._плашка_капчи(False)
        self._заблокировать(False)

    def _читать_очередь(self):
        try:
            while True:
                msg = self.очередь.get_nowait()
                if msg is None:
                    self._завершено()
                    continue
                if isinstance(msg, tuple):
                    self._событие(*msg)
                    continue
                в_консоль(msg)
                в_журнал(msg)
                if self.журнал_пуст:
                    self.журнал_пуст = False
                    self._подробности(True)
                self.журнал.configure(state="normal")
                self.журнал.insert("end", msg + "\n")
                self.журнал.see("end")
                self.журнал.configure(state="disabled")
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
            messagebox.showinfo("Папка с результатами", f"Не получилось открыть папку сама — "
                                                        f"открой её вручную:\n{путь}\n\n({e})")

    def закрыть(self):
        if self.идёт and not messagebox.askyesno(
                "Поиск ещё идёт", "Остановить поиск и закрыть программу?\n"
                                  "Скачанное сохранится, а при следующем запуске уже скачанное пропустится."):
            return
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
        messagebox.showinfo("Поиск картинок", "Программа уже запущена — поищи её среди открытых окон.")
        sys.exit(0)
    настроить_клавиши(root)
    Окно(root)
    root.lift()
    root.attributes("-topmost", True)
    root.after(500, lambda: root.attributes("-topmost", False))
    root.mainloop()
