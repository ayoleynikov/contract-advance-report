#!/usr/bin/env python3
"""Отчёт по авансам на дату.

Запуск из этой папки:

    python3 report.py --date 10.11.2026 --data data --out out
"""

from __future__ import annotations

import sys

from advance_report.__main__ import main

if __name__ == "__main__":
    sys.exit(main())
