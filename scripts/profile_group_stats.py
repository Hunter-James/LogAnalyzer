"""Профайл GroupStatsWorker и _populate_tree.

Запуск: cd src && py ../scripts/profile_group_stats.py "I:/Ивитек-тест"

Замеряет:
- сколько занимает парсинг каждого файла (топ-20 медленных)
- общее время парсинга
- сколько уникальных партий и кодов получилось
- сколько времени занял бы _populate_tree (создание QTreeWidgetItem
  для каждой партии и каждого кода). Тут считаем теоретически — без
  реального Qt, только число операций.
"""
import os
import sys
import time
import glob

# Добавляем src в sys.path
HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(os.path.dirname(HERE), 'src')
sys.path.insert(0, SRC)

from core.workers import _open_log_stream, LINE_PATTERN
from core.models import _BATCH_OPEN_RE, NO_BATCH, SGTIN_CODE_RE, GROUP_CODE_RE
from core.group_stats_worker import (
    _classify_for_stats, _new_batch_bucket, _COUNTER_KEYS)
import re

_SSCC_RE = re.compile(r'\b00\d{18}\b')


def stream_one(path, batches, intern):
    """Копия логики из GroupStatsWorker._stream_one (без QThread)."""
    try:
        stream, _ = _open_log_stream(path)
    except Exception as e:
        print(f"  ERR opening: {e}")
        return

    current_batch = NO_BATCH
    last_ts_in_line = ''
    sgtin_find = SGTIN_CODE_RE.findall
    sscc_find = _SSCC_RE.findall
    group_find = GROUP_CODE_RE.findall

    try:
        for line in stream:
            m = LINE_PATTERN.match(line)
            if m:
                last_ts_in_line = m.group(1)
                logger = intern(m.group(3))
            else:
                logger = ''

            if 'setCurrentBatch' in line:
                mb = _BATCH_OPEN_RE.search(line)
                if mb:
                    new_id = mb.group(1)
                    current_batch = NO_BATCH if new_id == '-1' else intern(new_id)

            key = _classify_for_stats(line, logger) if logger else None
            if key:
                bucket = batches.get(current_batch)
                if bucket is None:
                    bucket = _new_batch_bucket()
                    batches[current_batch] = bucket
                bucket['counters'][key] += 1
                bucket['files'][path] = bucket['files'].get(path, 0) + 1
                pfc = bucket['per_file_counters'].get(path)
                if pfc is None:
                    pfc = {k: 0 for k in _COUNTER_KEYS}
                    bucket['per_file_counters'][path] = pfc
                pfc[key] += 1
                if last_ts_in_line:
                    if not bucket['first_ts']:
                        bucket['first_ts'] = last_ts_in_line
                        bucket['first_file'] = path
                    bucket['last_ts'] = last_ts_in_line
                    bucket['last_file'] = path

            codes_in_line = sgtin_find(line)
            codes_in_line += sscc_find(line)
            codes_in_line += group_find(line)
            if codes_in_line:
                bucket = batches.get(current_batch)
                if bucket is None:
                    bucket = _new_batch_bucket()
                    batches[current_batch] = bucket
                bucket['codes'].update(intern(c) for c in codes_in_line)

            if (current_batch != NO_BATCH
                    and '/api/close' in line
                    and 'CustomLogFilter' in line):
                current_batch = NO_BATCH
    finally:
        try:
            stream.close()
        except Exception:
            pass


def main():
    if len(sys.argv) < 2:
        print("Usage: profile_group_stats.py <folder>")
        return 1
    folder = sys.argv[1]
    files = sorted(glob.glob(os.path.join(folder, '*.log.zip')))
    if not files:
        files = sorted(glob.glob(os.path.join(folder, '*.log')))
    print(f"Файлов: {len(files)}")

    intern = sys.intern
    batches = {}
    per_file_time = []

    t0 = time.time()
    for i, path in enumerate(files):
        ts = time.time()
        stream_one(path, batches, intern)
        elapsed = time.time() - ts
        per_file_time.append((elapsed, path))
        if (i + 1) % 30 == 0 or i == len(files) - 1:
            print(f"  {i+1:>3}/{len(files)}  total={time.time()-t0:.1f}s  "
                  f"batches={len(batches)}  "
                  f"codes_total={sum(len(b['codes']) for b in batches.values())}")
    total_parse = time.time() - t0

    print()
    print(f"=== Парсинг всех файлов: {total_parse:.1f}s "
          f"({total_parse/60:.1f} мин) ===")
    print(f"Партий: {len(batches)}")
    total_codes = sum(len(b['codes']) for b in batches.values())
    print(f"Уникальных кодов всего: {total_codes:,}")
    total_events = sum(sum(b['counters'].values()) for b in batches.values())
    print(f"Событий-счётчиков: {total_events:,}")

    # Топ-10 партий по размеру
    print("\nТоп-10 партий по числу кодов:")
    sorted_b = sorted(batches.items(),
                      key=lambda x: -len(x[1]['codes']))[:10]
    for bid, b in sorted_b:
        bid_disp = 'NO_BATCH' if bid == NO_BATCH else bid
        print(f"  {bid_disp:>10s}: {len(b['codes']):>8,} кодов, "
              f"{len(b['files']):>4d} файлов, "
              f"{sum(b['counters'].values()):>6,} событий")

    # Топ-10 медленных файлов
    print("\nТоп-10 медленных файлов:")
    per_file_time.sort(reverse=True)
    for elapsed, path in per_file_time[:10]:
        size_mb = os.path.getsize(path) / 1024 / 1024
        print(f"  {elapsed:>6.2f}s  ({size_mb:.1f}MB)  {os.path.basename(path)}")

    # Теоретическая стоимость _populate_tree
    # На каждую партию: ~12 QTreeWidgetItem (root + 6 stat rows + 4 sub-headers)
    # + по 1 на каждый файл партии (sum len(b['files']))
    # + по 1 на каждый код (min(MAX_CODES_PER_BATCH=5000, len(b['codes'])))
    MAX_CODES = 5000
    total_items = 0
    for b in batches.values():
        total_items += 12  # партия + статистика
        total_items += len(b['files'])
        total_items += min(MAX_CODES, len(b['codes']))
    print(f"\nТеоретическое количество QTreeWidgetItem для _populate_tree:")
    print(f"  {total_items:,}")
    # На современной машине ~50 микросек на QTreeWidgetItem (с виджет-операциями)
    # Реально может быть медленнее из-за repaint, layout.
    est_seconds = total_items * 50e-6
    print(f"  ~ {est_seconds:.1f}s на создание (50µs per item, оценка)")
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
