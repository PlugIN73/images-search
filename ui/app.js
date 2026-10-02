"use strict";
// Окно программы. Всё, что знает программа, приходит с её маленького сервера (most.py):
// состояние — по запросу, а ход поиска — событиями (долгий опрос /api/события).

const КЛЮЧ = document.documentElement.dataset.key;
const НА_MAC = /Mac/.test(navigator.platform);
const КЛАВИША = НА_MAC ? "⌘↵" : "Ctrl+Enter";
const $ = id => document.getElementById(id);

let сост = null;                       // состояние с сервера
const поиск = {
  активные: new Set(),              // имена запросов, которые ищутся прямо сейчас
  готовые: new Map(),               // имя запроса → нашлось ли что-то (в этом запуске)
  прогресс: null, итог: null, останавливаю: false,
  капчи: [],                        // открытые капчи: {до: когда поиск перестанет ждать}; браузеров бывает несколько
};
const ВНУТРИ_PYWEBVIEW = () => Boolean(window.pywebview);
const журнал = [];
let правка = null;                  // номер строки, чьи запросы сейчас правят

// ---------- связь с программой ----------
async function api(путь, данные, метод) {
  const ответ = await fetch("/api/" + путь, {
    method: метод || (данные === undefined ? "GET" : "POST"),
    headers: { "X-Token": КЛЮЧ, "Content-Type": "application/json" },
    body: данные === undefined ? undefined : JSON.stringify(данные),
  });
  const json = await ответ.json().catch(() => ({}));
  if (!ответ.ok) throw new Error(json.ошибка || "Не получилось. Попробуй ещё раз.");
  return json;
}

function принять(новое) {
  сост = новое;
  нарисовать();
}

let очередьСохранений = Promise.resolve();

// Изменить список: «изменить» получает свежий список и правит его.
// Сохранения идут строго по очереди, поэтому два быстрых изменения не затирают друг друга,
// а ошибка в одном не останавливает следующие.
function изменитьКадры(изменить) {
  очередьСохранений = очередьСохранений.then(async () => {
    try {
      const кадры = кадрыСейчас();
      изменить(кадры);
      принять(await api("кадры", { кадры }, "PUT"));
    } catch (e) {
      if (!(e instanceof СтрокаИсчезла)) сообщить(e.message);
      нарисоватьСписок();       // вернуть в поля то, что на самом деле сохранено
    }
  });
  return очередьСохранений;
}

// Строку запоминаем не только по месту, но и по содержимому: пока сохранение ждёт очереди,
// список мог сдвинуться (например, удалили строку выше).
function снимок(i) {
  const к = сост.кадры[i];
  return к ? { i, имя: к.номер, ru: к.ru } : null;
}

function найтиСтроку(кадры, сн) {
  if (!сн) return -1;
  const та = к => к && к.имя === сн.имя && к.ru === сн.ru;
  return та(кадры[сн.i]) ? сн.i : кадры.findIndex(та);
}

class СтрокаИсчезла extends Error {}

// Изменить одну строку, запомненную снимком. Если её уже нет — ничего не сохраняем, просто перерисовываем.
function изменитьСтроку(сн, изменить) {
  return изменитьКадры(кадры => {
    const j = найтиСтроку(кадры, сн);
    if (j < 0) throw new СтрокаИсчезла();
    изменить(кадры, j);
  });
}

function кадрыСейчас() {
  return сост.кадры.map(к => ({ имя: к.номер, ru: к.ru }));
}

let ждёмСостояние = null;
function обновитьСостояние() {
  // во время поиска картинки появляются часто — не чаще раза в полсекунды
  if (ждёмСостояние) return;
  ждёмСостояние = setTimeout(async () => {
    ждёмСостояние = null;
    try { принять(await api("состояние")); } catch (e) { /* окно закрывается */ }
  }, 500);
}

// ---------- события поиска ----------
async function слушать() {
  let после = 0;
  for (;;) {
    try {
      const о = await api(`события?после=${после}&ждать=25`);
      о.события.forEach(обработать);
      после = о.последнее;
      if (о.события.length) перерисовать();
    } catch (e) {
      await new Promise(r => setTimeout(r, 1000));
    }
  }
}

function обработать(e) {
  switch (e.тип) {
    case "старт":
      поиск.активные.clear(); поиск.готовые.clear();
      Object.assign(поиск, { прогресс: null, капчи: [], итог: null, останавливаю: false });
      сост.идёт = true;
      if (журнал.length) журнал.push("");
      break;
    case "журнал":
      журнал.push(e.текст);
      if (журнал.length > 3000) журнал.splice(0, журнал.length - 3000);
      break;
    case "ищу":
      поиск.активные.add(e.имя);
      break;
    case "начало":
      поиск.прогресс = e;
      break;
    case "запрос":
      поиск.активные.delete(e.имя);
      поиск.готовые.set(e.имя, e.найдено);
      поиск.прогресс = e;
      if (e.найдено) обновитьСостояние();
      break;
    case "капча":
      поиск.капчи.push({ до: e.время + (e.ждать || 600) });
      break;
    case "капча_прошла":
      поиск.капчи.shift();
      break;
    case "останавливаю":
      поиск.останавливаю = true;
      break;
    case "конец":
      поиск.итог = e;
      break;
    case "завершено":
      сост.идёт = false;
      поиск.активные.clear();
      поиск.капчи = [];
      if (!поиск.итог) {
        поиск.итог = { сломалось: true };
        $("журнал").hidden = false;           // что случилось — сразу видно
      }
      обновитьСостояние();
      break;
    case "обновление":
      сост.обновление = [e.версия, e.ссылка];
      break;
  }
}

let кадрЗапрошен = false;
function перерисовать() {
  if (кадрЗапрошен) return;
  кадрЗапрошен = true;
  requestAnimationFrame(() => { кадрЗапрошен = false; нарисовать(); });
}

// ---------- состояние строк ----------
function статусЗапроса(з) {
  if (поиск.активные.has(з.имя)) return "active";
  if (з.файлы.length) return "done";
  if (поиск.готовые.get(з.имя) === false) return "none";
  return "idle";
}

function статусКадра(к) {
  const все = к.запросы.map(статусЗапроса);
  if (!все.length) return "blank";
  if (все.includes("active")) return "active";
  if (все.every(x => x === "done")) return "done";
  if (все.includes("none") && все.every(x => x === "done" || x === "none")) return "partial";
  return сост.идёт ? "waiting" : "idle";
}

// ---------- рисование ----------
const экран = s => String(s).replace(/[&<>"']/g, ч => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[ч]));

function склонять(n, формы) {
  const n10 = n % 10, n100 = n % 100;
  const ф = n10 === 1 && n100 !== 11 ? 0 : n10 >= 2 && n10 <= 4 && (n100 < 12 || n100 > 14) ? 1 : 2;
  return `${n} ${формы[ф]}`;
}

const ЗНАЧКИ = {
  done: '<svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="9"/><path d="m8 12 3 3 5-6"/></svg>',
  partial: '<svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="9"/><path d="M12 8v5M12 16h.01"/></svg>',
  waiting: '<svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="9" stroke-dasharray="3 3.2"/></svg>',
  active: '<span class="spin"></span>',
  idle: "", blank: "",
};
const КРЕСТИК = '<svg viewBox="0 0 24 24"><path d="M6 6l12 12M18 6 6 18"/></svg>';

function превью(файлы) {
  const показать = файлы.slice(0, 3).map(ф =>
    `<button data-файл="${экран(ф)}" title="Открыть ${экран(ф)}" tabindex="-1">` +
    `<img alt="" loading="lazy" src="/превью?ф=${encodeURIComponent(ф)}&k=${encodeURIComponent(КЛЮЧ)}"></button>`).join("");
  const ещё = файлы.length > 3 ? `<button class="more" data-файл="${экран(файлы[3])}" tabindex="-1">+${файлы.length - 3}</button>` : "";
  return `<span class="thumbs">${показать}${ещё}</span>`;
}

function плашки(к, i) {
  if (правка === i) {
    return `<input class="edit" value="${экран(к.ru)}" aria-label="Запросы кадра ${экран(к.номер)}" placeholder="запрос|ещё запрос">`;
  }
  if (!к.запросы.length) return '<span class="q placeholder">Впиши запросы через |</span>';
  return к.запросы.map(з => {
    const st = статусЗапроса(з);
    const текст = `<span>${экран(з.текст)}</span>`;
    if (st === "active") return `<span class="q">${текст}<span class="spin" aria-label="ищу"></span></span>`;
    if (st === "done") return `<span class="q">${текст}${превью(з.файлы)}</span>`;
    if (st === "none") return `<span class="q none"><span>${экран(з.текст)} — ничего не нашлось</span></span>`;
    return `<span class="q plain">${текст}</span>`;
  }).join("");
}

function нарисоватьСписок() {
  const список = $("список");
  // кто был в фокусе — вернём ему фокус после перерисовки
  const фокус = document.activeElement;
  const был = фокус && список.contains(фокус) && (фокус.classList.contains("num") || фокус.classList.contains("edit"))
    ? { i: фокус.closest(".frame").dataset.i, класс: фокус.classList.contains("num") ? "num" : "edit",
        начало: фокус.selectionStart, конец: фокус.selectionEnd, значение: фокус.value }
    : null;
  список.innerHTML = сост.кадры.map((к, i) => {
    const st = статусКадра(к);
    return `<div class="frame ${st === "active" ? "active" : st}" data-i="${i}">` +
      `<input class="num" value="${экран(к.номер)}" placeholder="№" inputmode="numeric" aria-label="Номер кадра"` +
      `${к.номер ? "" : ' title="Без номера: у картинок этого кадра не будет номера в имени файла"'}${сост.идёт ? " disabled" : ""}>` +
      (правка === i
        ? `<div class="queries editing">${плашки(к, i)}</div>`
        : `<div class="queries"${сост.идёт ? "" : ` tabindex="0" role="button" aria-label="Запросы кадра ${экран(к.номер)}: ${экран(к.ru) || "пусто"}. Enter — изменить"`}>${плашки(к, i)}</div>`) +
      `<div class="side"><span class="status">${ЗНАЧКИ[st] || ""}</span>` +
      `<button class="del" tabindex="-1" aria-label="Удалить кадр ${экран(к.номер)}" title="Удалить кадр">${КРЕСТИК}</button></div></div>`;
  }).join("");
  if (был) {
    const поле = список.querySelector(`.frame[data-i="${был.i}"] .${был.класс}`);
    if (поле) { поле.value = был.значение; поле.focus(); поле.setSelectionRange(был.начало, был.конец); }
  }
  const ввод = список.querySelector(".edit");
  if (ввод && document.activeElement !== ввод && !был) {
    ввод.focus();
    ввод.setSelectionRange(ввод.value.length, ввод.value.length);
  }
}

function нарисоватьНиз() {
  const идёт = сост.идёт;
  // капча
  $("капча").hidden = !поиск.капчи.length;
  обновитьТаймер();
  // ход поиска
  $("прогресс").hidden = !идёт;
  if (идёт) {
    let левый = "Запускаю браузер — он откроется отдельным окном, не закрывай его.", правый = "";
    if (поиск.останавливаю) {
      левый = "Останавливаю: дождусь, пока закончатся текущие запросы…";
    } else if (поиск.прогресс) {
      const п = поиск.прогресс;
      левый = `<b>Кадров готово: ${п.кадров_готово} из ${п.кадров}</b> · запросов: ${п.сделано} из ${п.всего}`;
      const мин = п.осталось_мин;
      if (мин !== null && мин !== undefined && п.сделано < п.всего) {
        правый = мин >= 1 ? `осталось ≈ ${мин} мин` : "осталось меньше минуты";
      }
    }
    $("прогресс-текст").innerHTML = левый;
    $("прогресс-осталось").innerHTML = правый;
    const п = поиск.прогресс;
    $("полоса").style.transform = `scaleX(${п && п.всего ? п.сделано / п.всего : 0})`;
  }
  // итог
  const итог = поиск.итог;
  $("итог").hidden = идёт || !итог;
  if (!идёт && итог) {
    const блок = $("итог");
    блок.classList.toggle("warn", Boolean(итог.пустые && итог.пустые.length) || Boolean(итог.сломалось));
    блок.classList.toggle("neutral", Boolean(итог.нечего || итог.остановлено));
    $("итог-текст").innerHTML = текстИтога(итог);
  }
  // журнал
  document.querySelectorAll("[data-подробности]").forEach(к => {
    к.hidden = !журнал.length;
    к.textContent = $("журнал").hidden ? "Подробности" : "Скрыть подробности";
  });
  const лог = $("журнал");
  if (!лог.hidden) {
    const внизу = лог.scrollTop + лог.clientHeight >= лог.scrollHeight - 8;
    лог.textContent = журнал.join("\n");
    if (внизу) лог.scrollTop = лог.scrollHeight;
  }
}

function текстИтога(и) {
  if (и.сломалось) return "<b>Поиск прервался.</b><small>Что случилось — в подробностях. Запусти снова: уже скачанное пропустится.</small>";
  if (и.нечего) return "<b>Всё из списка уже скачано.</b><small>Добавь новые кадры и запусти снова.</small>";
  if (и.остановлено) return "<b>Остановлено.</b><small>Запусти снова — уже скачанное пропустится.</small>";
  const сЗапросами = сост.кадры.filter(к => к.запросы.length);
  const готовы = сЗапросами.filter(к => к.запросы.some(з => з.файлы.length)).length;
  let т = `<b>Готово за ${Math.max(1, Math.round(и.минут))} мин.</b> Картинки есть у ${готовы} из ${склонять(сЗапросами.length, ["кадра", "кадров", "кадров"])}.`;
  if (и.пустые && и.пустые.length) {
    const имена = и.пустые.slice(0, 4).map(и => `«${экран(и)}»`).join(", ");
    const ещё = и.пустые.length > 4 ? ` и ещё ${и.пустые.length - 4}` : "";
    т += `<small>Ничего не нашлось по ${и.пустые.length === 1 ? "запросу" : "запросам"} <span class="names">${имена}${ещё}</span> — попробуй переформулировать.</small>`;
  }
  return т;
}

function обновитьТаймер() {
  if (!поиск.капчи.length) return;
  const до = Math.min(...поиск.капчи.map(к => к.до));
  const осталось = Math.max(0, Math.round(до - Date.now() / 1000));
  $("капча-время").textContent = `жду ещё ${Math.floor(осталось / 60)}:${String(осталось % 60).padStart(2, "0")}`;
}

function нарисовать() {
  if (!сост) return;
  const app = $("app");
  const есть = сост.кадры.length > 0;
  app.dataset.state = есть ? "список" : "пусто";
  app.classList.toggle("running", сост.идёт);
  $("список").hidden = !есть;
  $("пусто").hidden = есть;
  const запросов = сост.кадры.reduce((n, к) => n + к.запросы.length, 0);
  $("счёт").textContent = есть ? `${склонять(сост.кадры.length, ["кадр", "кадра", "кадров"])} · ${склонять(запросов, ["запрос", "запроса", "запросов"])}` : "";
  const об = $("обновление");
  об.hidden = !сост.обновление;
  if (сост.обновление) об.textContent = `Вышла версия ${сост.обновление[0]} — скачать`;
  $("сколько").textContent = сост.сколько;
  $("дорожек").textContent = сост.дорожек;
  $("совет").textContent = сост.дорожек >= 3 ? "чаще капча" : "";
  document.querySelectorAll("[data-lock]").forEach(b => { b.disabled = сост.идёт; });
  // после поиска главное — посмотреть, что скачалось
  const и = поиск.итог;
  const папкаГлавная = Boolean(и && !сост.идёт && !и.сломалось && !и.остановлено);
  $("найти").classList.toggle("quiet", папкаГлавная);
  $("папка").classList.toggle("accent", папкаГлавная);
  $("найти").disabled = сост.идёт || !запросов;
  $("стоп").disabled = !сост.идёт || поиск.останавливаю;
  $("найти-текст").textContent = поиск.итог && !сост.идёт ? "Найти снова" : "Найти картинки";
  if (правка !== null && (сост.идёт || правка >= сост.кадры.length)) правка = null;
  нарисоватьСписок();
  нарисоватьНиз();
}

let таймерСообщения = null;
function сообщить(текст) {
  const т = $("сообщение");
  т.textContent = текст;
  т.hidden = false;
  clearTimeout(таймерСообщения);
  таймерСообщения = setTimeout(() => { т.hidden = true; }, 5000);
}

// ---------- действия со списком ----------
function начатьПравку(i) {
  if (сост.идёт) return;
  правка = i;
  нарисоватьСписок();
}

// Возвращает обещание: оно выполнится, когда правка сохранится.
function закончитьПравку(сохранить) {
  const ввод = $("список").querySelector(".edit");
  if (правка === null || !ввод) return очередьСохранений;
  const сн = снимок(правка);
  const текст = ввод.value.trim();
  правка = null;
  if (сохранить && сн && текст !== сн.ru) {
    return изменитьСтроку(сн, (кадры, j) => { кадры[j].ru = текст; });
  }
  нарисоватьСписок();
  return очередьСохранений;
}

async function добавитьКадр() {
  await закончитьПравку(true);
  await очередьСохранений;
  try {
    принять(await api("добавить", {}));
  } catch (e) {
    сообщить(e.message);
    return;
  }
  начатьПравку(сост.кадры.length - 1);
  const строка = $("список").lastElementChild;
  if (строка) строка.scrollIntoView({ block: "nearest", behavior: "smooth" });
}

$("список").addEventListener("click", e => {
  const картинка = e.target.closest("[data-файл]");
  if (картинка) { api("открыть", { файл: картинка.dataset.файл }).catch(() => {}); return; }
  const строка = e.target.closest(".frame");
  if (!строка) return;
  const i = Number(строка.dataset.i);
  if (e.target.closest(".del")) {
    // двойной клик не удалит соседнюю строку: второй раз эта строка уже не найдётся
    изменитьСтроку(снимок(i), (кадры, j) => { кадры.splice(j, 1); });
    return;
  }
  if (e.target.closest(".queries") && !e.target.closest(".edit") && правка !== i) начатьПравку(i);
});

$("список").addEventListener("keydown", e => {
  if (e.target.classList.contains("edit")) {
    if (e.key === "Enter" && !e.metaKey && !e.ctrlKey) { e.preventDefault(); закончитьПравку(true); }
    if (e.key === "Escape") { e.preventDefault(); закончитьПравку(false); }
  }
  if (e.target.classList.contains("num") && e.key === "Enter") e.target.blur();
  if (e.target.classList.contains("queries") && (e.key === "Enter" || e.key === " ")) {
    e.preventDefault();
    начатьПравку(Number(e.target.closest(".frame").dataset.i));
  }
});

$("список").addEventListener("focusout", e => {
  if (e.target.classList.contains("edit")) закончитьПравку(true);
  if (e.target.classList.contains("num")) {
    const i = Number(e.target.closest(".frame").dataset.i);
    const номер = e.target.value.trim();
    const сн = снимок(i);
    if (!сн || номер === сн.имя) return;
    if (!номер) {
      e.target.value = сн.имя;
      сообщить("У кадра должен быть номер: по нему называются файлы с картинками.");
      return;
    }
    изменитьСтроку(сн, (кадры, j) => { кадры[j].имя = номер; });
  }
});

// Поле номера: с клавиатуры можно перейти к запросам этого кадра
$("список").addEventListener("focusin", e => {
  if (e.target.classList.contains("num")) e.target.closest(".frame").scrollIntoView({ block: "nearest" });
});

$("добавить").addEventListener("click", добавитьКадр);

$("очистить").addEventListener("click", async () => {
  if (!сост.кадры.length) return;
  if (await спросить("Удалить все кадры из списка?", "Скачанные картинки останутся в папке.", "Удалить")) {
    изменитьКадры(кадры => { кадры.length = 0; });
  }
});

// ---------- вставка списком ----------
const окноВставки = $("окно-вставки");
const полеВставки = $("вставка-текст");
let таймерСводки = null;

function открытьВставку() {
  полеВставки.value = "";
  $("вставка-сводка").textContent = "Вставь список в поле выше.";
  окноВставки.showModal();
  полеВставки.focus();
}

function обновитьСводку() {
  clearTimeout(таймерСводки);
  таймерСводки = setTimeout(async () => {
    try { $("вставка-сводка").textContent = (await api("сводка", { текст: полеВставки.value })).текст; } catch (e) { /* не важно */ }
  }, 150);
}

async function вставкаГотово() {
  if (!полеВставки.value.trim()) { полеВставки.focus(); return; }
  await очередьСохранений;
  try {
    принять(await api("вставить", { текст: полеВставки.value }));
    окноВставки.close();
    const список = $("список");
    список.scrollTop = список.scrollHeight;
  } catch (e) {
    сообщить(e.message);
  }
}

function вставитьТекст(поле, текст) {
  поле.setRangeText(текст, поле.selectionStart, поле.selectionEnd, "end");
  поле.dispatchEvent(new Event("input", { bubbles: true }));
}

$("вставить").addEventListener("click", открытьВставку);
$("вставить-пусто").addEventListener("click", открытьВставку);
$("вставка-готово").addEventListener("click", вставкаГотово);
полеВставки.addEventListener("input", обновитьСводку);
$("из-буфера").addEventListener("click", async () => {
  try {
    const { текст } = await api("буфер");
    if (!текст) { сообщить("В буфере обмена нет текста. Скопируй список из сценария и попробуй снова."); return; }
    вставитьТекст(полеВставки, текст);
    полеВставки.focus();
  } catch (e) {
    сообщить(e.message);
  }
});

// ---------- вопрос «точно?» ----------
function спросить(вопрос, пояснение, да) {
  const окно = $("окно-вопроса");
  $("вопрос-текст").textContent = вопрос;
  $("вопрос-пояснение").textContent = пояснение;
  $("вопрос-да").textContent = да;
  окно.returnValue = "";
  окно.showModal();
  return new Promise(готово => окно.addEventListener("close", () => готово(окно.returnValue === "да"), { once: true }));
}

// ---------- поиск и настройки ----------
async function найти() {
  if (сост.идёт || $("найти").disabled) return;
  await закончитьПравку(true);
  await очередьСохранений;
  try {
    принять(await api("старт", {}));
  } catch (e) {
    сообщить(e.message);
  }
}

$("найти").addEventListener("click", найти);
$("стоп").addEventListener("click", () => api("стоп", {}).catch(e => сообщить(e.message)));
$("папка").addEventListener("click", () => api("папка", {}).catch(e => сообщить(e.message)));
$("обновление").addEventListener("click", () => api("ссылка", {}).catch(() => {}));
$("итог-закрыть").addEventListener("click", () => { поиск.итог = null; нарисовать(); });
document.querySelectorAll("[data-подробности]").forEach(к => к.addEventListener("click", () => {
  $("журнал").hidden = !$("журнал").hidden;
  нарисоватьНиз();
  if (!$("журнал").hidden) $("журнал").scrollTop = $("журнал").scrollHeight;
}));

document.querySelectorAll("[data-шаг]").forEach(кнопка => кнопка.addEventListener("click", async () => {
  const [что, шаг] = кнопка.dataset.шаг.split(":");
  try { принять(await api("настройки", { [что]: сост[что] + Number(шаг) })); } catch (e) { сообщить(e.message); }
}));

// ---------- клавиши ----------
document.addEventListener("keydown", e => {
  const мод = НА_MAC ? e.metaKey : e.ctrlKey;
  if (мод && e.key === "Enter") {
    e.preventDefault();
    if (окноВставки.open) вставкаГотово();
    else if (!document.querySelector("dialog[open]")) найти();
    return;
  }
  if (НА_MAC && e.metaKey && !e.ctrlKey && !e.altKey) русскаяРаскладка(e);
});

// На Mac окно само понимает ⌘C/⌘V только в английской раскладке.
// В русской ⌘V приходит как ⌘М — разбираем клавишу по её месту на клавиатуре.
function русскаяРаскладка(e) {
  if (/^[a-z]$/i.test(e.key)) return;
  const поле = document.activeElement;
  const можноПисать = поле && (поле.tagName === "TEXTAREA" || поле.tagName === "INPUT") && !поле.disabled;
  switch (e.code) {
    case "KeyV":
      if (!можноПисать) return;
      e.preventDefault();
      api("буфер").then(({ текст }) => { if (текст) вставитьТекст(поле, поле.tagName === "INPUT" ? текст.replace(/\s*\n\s*/g, " ").trim() : текст); }).catch(() => {});
      break;
    case "KeyC": e.preventDefault(); document.execCommand("copy"); break;
    case "KeyX": e.preventDefault(); document.execCommand("cut"); break;
    case "KeyA": if (можноПисать) { e.preventDefault(); поле.select(); } break;
    case "KeyZ": e.preventDefault(); document.execCommand(e.shiftKey ? "redo" : "undo"); break;
  }
}

// Окно в Chromium: сказать программе, что его закрыли, — иначе на Mac она так и осталась бы работать.
window.addEventListener("pagehide", () => {
  if (!ВНУТРИ_PYWEBVIEW()) navigator.sendBeacon(`/api/закрыто?k=${encodeURIComponent(КЛЮЧ)}`);
});

window.addEventListener("beforeunload", e => {
  if (сост && сост.идёт && !ВНУТРИ_PYWEBVIEW()) { e.preventDefault(); e.returnValue = ""; }
});

// ---------- запуск ----------
(async () => {
  $("клавиша").textContent = КЛАВИША;
  document.querySelectorAll(".клавиша").forEach(k => { k.textContent = КЛАВИША; });
  for (;;) {
    try { сост = await api("состояние"); break; } catch (e) { await new Promise(r => setTimeout(r, 300)); }
  }
  нарисовать();
  setInterval(() => { if (поиск.капчи.length) обновитьТаймер(); }, 1000);
  слушать();
})();
