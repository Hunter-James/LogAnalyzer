# LogAnalyzer

**LogAnalyzer** — это современный, высокопроизводительный инструмент для анализа логов, написанный на Python и PyQt6. Разработанный для разработчиков и системных администраторов, он предлагает гибкий многодокументный интерфейс (MDI), аналогичный Notepad++, позволяющий легко анализировать несколько лог-файлов одновременно.
---
## 🇷🇺 Русский

**LogAnalyzer** — это современный, высокопроизводительный инструмент для анализа логов, написанный на Python и PyQt6. Разработанный для разработчиков и системных администраторов, он предлагает гибкий многодокументный интерфейс (MDI), аналогичный Notepad++, позволяющий легко анализировать несколько лог-файлов одновременно.

### 🚀 Ключевые возможности

#### 🖥️ Продвинутый интерфейс
*   **Поддержка вкладок:** Открывайте столько лог-файлов, сколько вам нужно, в отдельных вкладках.
*   **Разделение экрана (Split View):** Работайте с двумя файлами бок о бок, как в Notepad++.
    *   **Drag & Drop:** Просто перетащите вкладку на другую сторону экрана, чтобы разделить вид.
    *   **Контекстное меню:** Нажмите правой кнопкой мыши на вкладку и выберите "Move to Other View".
*   **Темы оформления:** Выбирайте из встроенных тем: *Default (Dark), Minimalist Black, Minimalist White, Windows 95 и Hacker*.

#### 🔍 Мощный поиск и фильтрация
*   **Поиск в каждой вкладке:** У каждого файла есть своя независимая строка поиска сверху.
*   **Поддержка Regex:** Полная поддержка регулярных выражений в строке поиска.
*   **Глобальные фильтры уровней:** Переключайте уровни логов (`INFO`, `DEBUG`, `WARN`, `ERROR`) глобально для всех открытых файлов одним кликом.
*   **Локальная статистика:** Счетчики уровней логов в реальном времени отображаются внизу каждой вкладки.

#### ⚡ Производительность
*   **Асинхронная загрузка:** Большие лог-файлы загружаются в фоновых потоках, не замораживая интерфейс.
*   **Умный рендеринг:** Оптимизированный список обрабатывает тысячи записей плавно.

### 🛠️ Установка и запуск из исходников

#### Требования
*   Python 3.8+
*   PyQt6

#### Шаги
1.  **Клонируйте репозиторий:**
    ```bash
    git clone https://github.com/Hunter-James/LogAnalyzerEVOL.git
    cd LogAnalyzerEVOL
    ```

2.  **Установите зависимости:**
    ```bash
    pip install PyQt6
    ```

3.  **Запустите приложение:**
    ```bash
    python src/main.py
    ```

### 📖 Руководство пользователя

#### 1. Разделение экрана и Drag-and-Drop
Организуйте свое рабочее пространство так же, как в продвинутых редакторах кода:
*   **Перетащите** вкладку из панели вкладок и бросьте ее в любое место. Если вы бросите ее на противоположную сторону, она переместится в тот вид.
*   Если второй вид скрыт, перетаскивание вкладки к правому краю (или использование контекстного меню) активирует режим разделения экрана (Split View).

#### 2. Использование поиска (Regex)
Строка поиска в верхней части окна поддерживает как обычный текстовый поиск, так и **Регулярные выражения (Regex)**. Поиск всегда выполняется без учета регистра. Если регулярное выражение введено с синтаксической ошибкой, программа автоматически переключится на обычный текстовый поиск.

* **Обычный поиск:** Просто введите слово, IP-адрес или фрагмент лога (например, `startPrint` или `192.168.5.2`).
* **Поиск Regex:** Позволяет применять гибкие шаблоны для фильтрации сложных производственных логов.
    * *Отслеживание оборудования:* Оставить в выдаче только взаимодействие со сканерами и ПЛК: `\[\s*(HIKROBOT|PLCService).*?\]`
    * *Поиск конкретного КМ (SGTIN):* Коды маркировки часто содержат спецсимвол `<GS>`, который не получится скопировать. Замените его на точку `.` (любой символ), а обычные точки экранируйте `\.`: `0104600905000875215C2\.Oj.93/nrd`
    * *Фильтрация JSON-ответов API:* Найти только строки HTTP-ответов, содержащие тело JSON: `Response\s+/api/.*?\{"timestamp"`
    * *Поиск скрытых проблем:* Найти отказы оборудования или ошибки в тексте лога, даже если уровень записи `INFO`: `(Запуск.*false|Не указан|error)`
    * *Точный поиск по времени (миллисекунды):* Найти все события, произошедшие в конкретную секунду (например, с 800 по 899 мс): `^07:13:43\.8\d{2}`
#### 3. Фильтрация логов
Используйте чекбоксы в верхней панели инструментов (`INFO`, `DEBUG`, `WARN`, `ERROR`).
*   **Примечание:** Эти фильтры являются **глобальными**. Снятие галочки с `DEBUG` скроет отладочные сообщения во **всех** открытых в данный момент вкладках одновременно.

#### 4. Масштабирование
*   Удерживайте `Ctrl` и прокручивайте **Колесо мыши**, чтобы увеличить или уменьшить размер шрифта.
*   Настройка сохраняется автоматически для следующей сессии.

### ⚙️ Конфигурация
Настройки (Тема и Размер шрифта) автоматически сохраняются в файл `settings.json` в директории приложения. Вы также можете изменить их через кнопку **Settings** в панели инструментов.

---

## 🇬🇧 English

**LogAnalyzer** is a modern, high-performance log analysis tool written in Python and PyQt6. Designed for developers and system administrators, it offers a flexible Multi-Document Interface (MDI) similar to Notepad++, allowing you to analyze multiple log files simultaneously with ease.

### 🚀 Key Features

#### 🖥️ Advanced Interface
*   **Multi-Tab Support:** Open as many log files as you need in separate tabs.
*   **Split View (Notepad++ Style):** Work with two files side-by-side.
    *   **Drag & Drop:** Simply drag a tab to the other side of the screen to split the view.
    *   **Context Menu:** Right-click a tab and select "Move to Other View".
*   **Themeable UI:** Choose from built-in themes: *Default (Dark), Minimalist Black, Minimalist White, Windows 95, and Hacker*.

#### 🔍 Powerful Search & Filtering
*   **Per-Tab Search:** Each file has its own independent search bar at the top.
*   **Regex Support:** Full support for Regular Expressions in the search bar.
*   **Global Level Filters:** Toggle log levels (`INFO`, `DEBUG`, `WARN`, `ERROR`) globally across all open files with a single click.
*   **Local Statistics:** Real-time counters for log levels displayed at the bottom of each tab.

#### ⚡ Performance
*   **Asynchronous Loading:** Large log files are loaded in background threads without freezing the UI.
*   **Smart Rendering:** Optimized list view handles thousands of log entries smoothly.

### 🛠️ Installation & Running from Scratch

#### Prerequisites
*   Python 3.8+
*   PyQt6

#### Steps
1.  **Clone the repository:**
    ```bash
    git clone https://github.com/Hunter-James/LogAnalyzerEVOL.git
    cd LogAnalyzerEVOL
    ```

2.  **Install dependencies:**
    ```bash
    pip install PyQt6
    ```

3.  **Run the application:**
    ```bash
    python src/main.py
    ```

### 📖 Usage Guide

#### 1. Split View & Drag-and-Drop
Just like in advanced code editors, you can organize your workspace:
*   **Drag** a tab from the tab bar and drop it anywhere. If you drop it on the opposite side, it will move to that view.
*   If the second view is hidden, dragging a tab to the right edge (or using the context menu) will activate the Split View.

#### 2. Using Search (Regex)
The search bar at the top of the window supports both standard text search and **Regular Expressions (Regex)**. The search is always case-insensitive. If a regular expression contains a syntax error, the application automatically falls back to a standard text search.

* **Standard Search:** Simply enter a word, IP address, or log fragment (e.g., `startPrint` or `192.168.5.2`).
* **Regex Search:** Allows you to use flexible patterns for filtering complex production logs.
    * *Hardware Tracking:* Filter the output to show only interactions with scanners and PLCs: `\[\s*(HIKROBOT|PLCService).*?\]`
    * *Specific Marking Code (SGTIN) Search:* Marking codes often contain a special `<GS>` character that cannot be copied. Replace it with a dot `.` (matches any character), and escape regular dots `\.`: `0104600905000875215C2\.Oj.93/nrd`
    * *Filtering API JSON Responses:* Find only HTTP response lines that contain a JSON body: `Response\s+/api/.*?\{"timestamp"`
    * *Finding Hidden Issues:* Locate equipment failures or text errors in the log, even if the log level is `INFO`: `(Запуск.*false|Не указан|error)`
    * *Exact Time Search (milliseconds):* Find all events that occurred within a specific second (e.g., from 800 to 899 ms): `^07:13:43\.8\d{2}`
#### 3. Filtering Logs
Use the checkboxes in the top toolbar (`INFO`, `DEBUG`, `WARN`, `ERROR`).
*   **Note:** These filters are **global**. Unchecking `DEBUG` will hide debug messages in **all** currently open tabs simultaneously.

#### 4. Zooming
*   Hold `Ctrl` and scroll the **Mouse Wheel** to increase or decrease the font size.
*   The setting is saved automatically for your next session.

### ⚙️ Configuration
Settings (Theme and Font Size) are automatically saved to `settings.json` in the application directory. You can also change them via the **Settings** button in the toolbar.

---

