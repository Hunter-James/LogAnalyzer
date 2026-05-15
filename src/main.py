import sys
import os
import tracemalloc

# Add the src directory to sys.path to allow imports from src
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

# tracemalloc включаем ДО создания QApplication: чем раньше старт, тем
# точнее snapshot покажет реальные потребители памяти. frames=1 - храним
# только верхний фрейм аллокации (достаточно для отчёта file:line),
# overhead минимальный по сравнению с дефолтом frames=25.
if not tracemalloc.is_tracing():
    tracemalloc.start(1)

from gui.window import MainWindow  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402


if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setStyle("Fusion")

    window = MainWindow()
    window.show()
    sys.exit(app.exec())
