# LogAnalyzerEVOL

**LogAnalyzerEVOL** is a modern, high-performance log analysis tool written in Python and PyQt6. Designed for developers and system administrators, it offers a flexible Multi-Document Interface (MDI) similar to Notepad++, allowing you to analyze multiple log files simultaneously with ease.

---

## 🇬🇧 English

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

### 🛠️ Installation & Running

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
The search bar at the top of each tab supports standard text search and **Regular Expressions (Regex)**.

*   **Standard Search:** Just type the word you are looking for (e.g., `Database`).
*   **Regex Search:** The search automatically detects regex patterns.
    *   *Example:* Find any error code: `Error \d+`
    *   *Example:* Find lines starting with a timestamp: `^\d{2}:\d{2}:\d{2}`
    *   *Example:* Case-insensitive search for "fail": `(?i)fail`

#### 3. Filtering Logs
Use the checkboxes in the top toolbar (`INFO`, `DEBUG`, `WARN`, `ERROR`).
*   **Note:** These filters are **global**. Unchecking `DEBUG` will hide debug messages in **all** currently open tabs simultaneously.

#### 4. Zooming
*   Hold `Ctrl` and scroll the **Mouse Wheel** to increase or decrease the font size.
*   The setting is saved automatically for your next session.

### ⚙️ Configuration
Settings (Theme and Font Size) are automatically saved to `settings.json` in the application directory. You can also change them via the **Settings** button in the toolbar.

---

## 🇷🇺 Русский

**LogAnalyzerEVOL** — это современный, высокопроизводительный инструмент для анализа логов, написанный на Python и PyQt6. Разработанный для разработчиков и системных администраторов, он предлагает гибкий многодокументный интерфейс (MDI), аналогичный Notepad++, позволяющий легко анализировать несколько лог-файлов одновременно.

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

### 🛠️ Установка и запуск

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
Строка поиска в верхней части каждой вкладки поддерживает обычный текстовый поиск и **Регулярные выражения (Regex)**.

*   **Обычный поиск:** Просто введите слово, которое ищете (например, `Database`).
*   **Поиск Regex:** Поиск автоматически определяет паттерны regex.
    *   *Пример:* Найти любой код ошибки: `Error \d+`
    *   *Пример:* Найти строки, начинающиеся с временной метки: `^\d{2}:\d{2}:\d{2}`
    *   *Пример:* Поиск без учета регистра для слова "fail": `(?i)fail`

#### 3. Фильтрация логов
Используйте чекбоксы в верхней панели инструментов (`INFO`, `DEBUG`, `WARN`, `ERROR`).
*   **Примечание:** Эти фильтры являются **глобальными**. Снятие галочки с `DEBUG` скроет отладочные сообщения во **всех** открытых в данный момент вкладках одновременно.

#### 4. Масштабирование
*   Удерживайте `Ctrl` и прокручивайте **Колесо мыши**, чтобы увеличить или уменьшить размер шрифта.
*   Настройка сохраняется автоматически для следующей сессии.

### ⚙️ Конфигурация
Настройки (Тема и Размер шрифта) автоматически сохраняются в файл `settings.json` в директории приложения. Вы также можете изменить их через кнопку **Settings** в панели инструментов.

---

## 📄 License
[MIT License](LICENSE)
