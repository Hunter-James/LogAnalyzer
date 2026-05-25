"""Детальный профайл парсинга ОДНОГО лога — найти где конкретно тормозит.

Запуск из корня проекта:
  py scripts/profile_one_file.py "I:/Ивитек-тест/application.2026-03-26.log.zip"

Замеряет:
- общее время / число строк / число continuation lines
- время на: LINE_PATTERN.match, _BATCH_OPEN_RE.search, SGTIN.findall,
  SSCC.findall, GROUP.findall, classify, set.update
- какой regex даёт большинство срабатываний (т.е. где данные)
"""
import os
import sys
import time
import re

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(os.path.dirname(HERE), 'src')
sys.path.insert(0, SRC)

from core.workers import _open_log_stream, LINE_PATTERN
from core.models import _BATCH_OPEN_RE, NO_BATCH, SGTIN_CODE_RE, GROUP_CODE_RE
from core.group_stats_worker import (
    _classify_for_stats, _new_batch_bucket, _COUNTER_KEYS, ALL_CODES_RE)

_SSCC_RE = re.compile(r'\b00\d{18}\b')


def main():
    if len(sys.argv) < 2:
        print("Usage: profile_one_file.py <path>")
        return 1
    path = sys.argv[1]
    sz = os.path.getsize(path)
    print(f"Файл: {path}")
    print(f"Размер: {sz/1024/1024:.2f} MB")
    print()

    intern = sys.intern
    stream, _ = _open_log_stream(path)

    # Счётчики
    n_lines = 0
    n_continuation = 0
    n_matches_line = 0
    n_setcurr = 0
    n_sgtin = 0
    n_sscc = 0
    n_group = 0
    n_classified = 0
    n_codes_total = 0
    codes_set = set()

    # Время по фазам
    t_line_pattern = 0.0
    t_batch_re = 0.0
    t_sgtin = 0.0
    t_sscc = 0.0
    t_group = 0.0
    t_all_combined = 0.0
    t_classify = 0.0
    t_total = time.time()

    sgtin_find = SGTIN_CODE_RE.findall
    sscc_find = _SSCC_RE.findall
    group_find = GROUP_CODE_RE.findall
    all_find = ALL_CODES_RE.findall

    bucket = _new_batch_bucket()

    try:
        for line in stream:
            n_lines += 1

            t = time.perf_counter()
            m = LINE_PATTERN.match(line)
            t_line_pattern += time.perf_counter() - t
            if m:
                n_matches_line += 1
                logger = intern(m.group(3))
            else:
                n_continuation += 1
                logger = ''

            if 'setCurrentBatch' in line:
                t = time.perf_counter()
                mb = _BATCH_OPEN_RE.search(line)
                t_batch_re += time.perf_counter() - t
                if mb:
                    n_setcurr += 1

            t = time.perf_counter()
            key = _classify_for_stats(line, logger) if logger else None
            t_classify += time.perf_counter() - t
            if key:
                n_classified += 1
                bucket['counters'][key] += 1

            t = time.perf_counter()
            codes1 = sgtin_find(line)
            t_sgtin += time.perf_counter() - t

            t = time.perf_counter()
            codes2 = sscc_find(line)
            t_sscc += time.perf_counter() - t

            t = time.perf_counter()
            codes3 = group_find(line)
            t_group += time.perf_counter() - t

            # Замер ALL_CODES_RE для сравнения (не используется в счётчиках)
            t = time.perf_counter()
            codes_all = all_find(line)
            t_all_combined += time.perf_counter() - t

            if codes1:
                n_sgtin += len(codes1)
            if codes2:
                n_sscc += len(codes2)
            if codes3:
                n_group += len(codes3)

            if codes1 or codes2 or codes3:
                n_codes_total += len(codes1) + len(codes2) + len(codes3)
                codes_set.update(intern(c) for c in codes1)
                codes_set.update(intern(c) for c in codes2)
                codes_set.update(intern(c) for c in codes3)
    finally:
        stream.close()

    elapsed = time.time() - t_total
    print(f"=== Парсинг: {elapsed:.2f}s ===")
    print(f"Строк всего:           {n_lines:,}")
    print(f"  с timestamp:         {n_matches_line:,}")
    print(f"  continuation lines:  {n_continuation:,}  ({n_continuation*100/max(1,n_lines):.1f}%)")
    print(f"setCurrentBatch:       {n_setcurr:,}")
    print(f"Классифицировано:      {n_classified:,}")
    print(f"Найдено кодов SGTIN:   {n_sgtin:,}")
    print(f"Найдено кодов SSCC:    {n_sscc:,}")
    print(f"Найдено кодов GROUP:   {n_group:,}")
    print(f"Кодов всего (с дублями): {n_codes_total:,}")
    print(f"Уникальных кодов:      {len(codes_set):,}")
    print()
    print("=== Время по фазам ===")
    total_phase = (t_line_pattern + t_batch_re + t_sgtin + t_sscc
                   + t_group + t_classify)
    rows = [
        ("LINE_PATTERN.match", t_line_pattern),
        ("BATCH_OPEN_RE",      t_batch_re),
        ("SGTIN.findall",      t_sgtin),
        ("SSCC.findall",       t_sscc),
        ("GROUP.findall",      t_group),
        ("ALL_CODES (combo)",  t_all_combined),
        ("classify_for_stats", t_classify),
    ]
    for name, ts in rows:
        pct = ts*100/max(0.001, total_phase)
        print(f"  {name:<22s}  {ts:>7.2f}s  ({pct:>5.1f}% от фаз)")
    print(f"Сумма фаз: {total_phase:.2f}s, остальное (IO + decompress): "
          f"{elapsed - total_phase:.2f}s")
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
