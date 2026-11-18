"""python3 report.py --date 10.11.2026 --data data --out out"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

from advance_report.allocate import build_report
from advance_report.load import DataError, load_data
from advance_report.render import render_text, write_report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Отчёт на дату: по каждой работе цена, выплачено и остаток, плюс итог по договору.",
        epilog="Пример: python3 report.py --date 10.11.2026 --data data --out out",
    )
    parser.add_argument("--date", required=True, type=_parse_date, help="Дата отчёта: 2026-11-10 или 10.11.2026")
    parser.add_argument("--data", help="Папка с contracts.csv, works.csv, payments.csv. По умолчанию ./data")
    parser.add_argument("--out", help="Папка для текста и CSV. Без неё отчёт только на экране")
    args = parser.parse_args(argv)
    try:
        data = load_data(_data_dir(args.data))
        report = build_report(data, args.date)
    except DataError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    sys.stdout.write(render_text(report))
    if args.out:
        for path in write_report(report, Path(args.out)):
            print(path, file=sys.stderr)
    return 0


def _parse_date(raw: str):
    for fmt in ("%Y-%m-%d", "%d.%m.%Y"):
        try:
            return datetime.strptime(raw, fmt).date()
        except ValueError:
            continue
    raise argparse.ArgumentTypeError("нужна дата вида 2026-11-10 или 10.11.2026")


def _data_dir(raw: str | None) -> Path:
    if raw:
        return Path(raw)
    local = Path("data")
    if local.is_dir():
        return local
    return Path(__file__).resolve().parents[1] / "data"


if __name__ == "__main__":
    sys.exit(main())
