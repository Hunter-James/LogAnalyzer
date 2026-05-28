"""Замер: сколько занимает MarkerScrollBar.paintEvent с N маркерами.

На реальных файлах Ивитек где почти каждая строка ERROR — это до 14M
маркеров. paintEvent срабатывает на каждое движение скроллбара во время
drag'а — нужно понять реальную стоимость и стоит ли deduplicate."""

import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(os.path.dirname(HERE), 'src')
sys.path.insert(0, SRC)

# Симулируем работу set_markers + paint без Qt: измеряем чистую стоимость
# итерации по N items.


def measure_iter(n):
    """Цикл как в paintEvent: y = top + int(rel * (h-2)); fillRect."""
    h = 800  # типичная высота скроллбара
    # Создаём список (rel_pos, color)
    markers = [(i / n, 'red' if i % 2 else 'orange') for i in range(n)]
    t = time.perf_counter()
    # Имитируем drawing — пустой цикл с минимумом работы
    rects = []
    for rel, _color in markers:
        y = int(rel * (h - 2))
        rects.append(y)
    return time.perf_counter() - t


def measure_dedupe(n):
    """Deduplicate по округлённой позиции — то что предлагаю реализовать."""
    h = 800
    markers = [(i / n, 'red' if i % 2 else 'orange') for i in range(n)]
    t = time.perf_counter()
    seen = {}
    for rel, color in markers:
        key = (int(rel * 10000), color)
        if key not in seen:
            seen[key] = (rel, color)
    deduped = list(seen.values())
    return time.perf_counter() - t, len(deduped)


def main():
    print(f"{'N маркеров':>14}{'naive paint':>14}{'dedupe':>14}{'unique':>10}")
    print('-' * 55)
    for n in [10_000, 100_000, 1_000_000, 5_000_000, 14_000_000]:
        t1 = measure_iter(n)
        t2, uniq = measure_dedupe(n)
        print(f"{n:>14,}{t1*1000:>12.1f}ms{t2*1000:>12.1f}ms{uniq:>10,}")
    print()
    print("Интерпретация:")
    print("  naive paint = стоимость текущего paintEvent ОДНОГО рендера.")
    print("  При drag scrollbar paintEvent срабатывает ~30 раз/сек —")
    print("  на 14M маркеров каждый кадр > секунды → UI висит.")
    print("  dedupe = одноразовая стоимость в set_markers; paint потом")
    print("  работает только с unique позициями (max 10000 при rel*10000).")


if __name__ == '__main__':
    main()
