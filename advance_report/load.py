"""Чтение CSV и проверки, без которых отчёт лучше не строить."""

from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

from advance_report.money import parse_amount

STATUSES_WORK = {"planned", "done", "cancelled"}
STATUSES_PAYMENT = {"paid", "queued", "refunded"}


class DataError(Exception):
    def __init__(self, errors: list[str]):
        self.errors = errors
        super().__init__("\n".join(errors))


@dataclass(frozen=True)
class Contract:
    contract_id: str
    contractor: str
    amount: Decimal
    signed_at: date


@dataclass(frozen=True)
class Work:
    work_id: str
    contract_id: str
    title: str
    price: Decimal
    status: str
    completed_at: date | None
    cancelled_at: date | None


@dataclass(frozen=True)
class Payment:
    payment_id: str
    contract_id: str
    work_id: str | None
    purpose: str
    amount: Decimal
    status: str
    created_at: date
    paid_at: date | None
    refunded_at: date | None


@dataclass(frozen=True)
class Data:
    contracts: tuple[Contract, ...]
    works: tuple[Work, ...]
    payments: tuple[Payment, ...]


def id_key(value: str) -> tuple[str, int, str]:
    prefix, sep, tail = value.partition("-")
    if sep and tail.isdigit():
        return (prefix, int(tail), value)
    return (value, 0, value)


def load_data(folder: Path) -> Data:
    folder = Path(folder)
    if not folder.is_dir():
        raise DataError([f"Нет папки с данными: {folder}"])
    errors: list[str] = []
    contracts, err = _load_contracts(folder / "contracts.csv")
    errors.extend(err)
    works, err = _load_works(folder / "works.csv")
    errors.extend(err)
    payments, err = _load_payments(folder / "payments.csv")
    errors.extend(err)
    if errors:
        raise DataError(errors)
    data = Data(tuple(contracts), tuple(works), tuple(payments))
    validate(data)
    return data


def validate(data: Data) -> None:
    errors: list[str] = []
    _duplicates(errors, "договор", [item.contract_id for item in data.contracts])
    _duplicates(errors, "работа", [item.work_id for item in data.works])
    _duplicates(errors, "платёж", [item.payment_id for item in data.payments])

    contracts = {item.contract_id: item for item in data.contracts}
    works = {item.work_id: item for item in data.works}

    for work in data.works:
        if work.contract_id not in contracts:
            errors.append(f"Работа {work.work_id} ссылается на неизвестный договор {work.contract_id}")
        _check_work_status(errors, work)

    for payment in data.payments:
        if payment.contract_id not in contracts:
            errors.append(
                f"Платёж {payment.payment_id} ссылается на неизвестный договор {payment.contract_id}"
            )
        if payment.work_id:
            work = works.get(payment.work_id)
            if work is None:
                errors.append(
                    f"Платёж {payment.payment_id} ссылается на неизвестную работу {payment.work_id}"
                )
            elif work.contract_id != payment.contract_id:
                errors.append(
                    f"Платёж {payment.payment_id} привязан к работе {payment.work_id}, "
                    f"а эта работа в договоре {work.contract_id}, не в {payment.contract_id}"
                )
        _check_payment_status(errors, payment)

    if errors:
        raise DataError(errors)


def _duplicates(errors: list[str], label: str, values: list[str]) -> None:
    seen: set[str] = set()
    for value in values:
        if value in seen:
            errors.append(f"Повторяется {label} {value}")
        seen.add(value)


def _check_work_status(errors: list[str], work: Work) -> None:
    if work.status == "done":
        if work.completed_at is None or work.cancelled_at is not None:
            errors.append(f"Работа {work.work_id}: у выполненной должна быть только дата приёмки")
    elif work.status == "cancelled":
        if work.cancelled_at is None or work.completed_at is not None:
            errors.append(f"Работа {work.work_id}: у отменённой должна быть только дата отмены")
    elif work.status == "planned":
        if work.completed_at is not None or work.cancelled_at is not None:
            errors.append(f"Работа {work.work_id}: у запланированной не должно быть дат приёмки и отмены")
    else:
        errors.append(f"Работа {work.work_id}: неизвестный статус {work.status}")


def _check_payment_status(errors: list[str], payment: Payment) -> None:
    if payment.status == "paid":
        if payment.paid_at is None or payment.refunded_at is not None:
            errors.append(f"Платёж {payment.payment_id}: у оплаченного нужна дата оплаты и не нужен возврат")
    elif payment.status == "queued":
        if payment.paid_at is not None or payment.refunded_at is not None:
            errors.append(f"Платёж {payment.payment_id}: у очереди не должно быть даты оплаты и возврата")
    elif payment.status == "refunded":
        if payment.paid_at is None or payment.refunded_at is None:
            errors.append(f"Платёж {payment.payment_id}: у возврата нужны обе даты")
        elif payment.refunded_at < payment.paid_at:
            errors.append(f"Платёж {payment.payment_id}: возврат раньше оплаты")
    else:
        errors.append(f"Платёж {payment.payment_id}: неизвестный статус {payment.status}")


def _load_contracts(path: Path) -> tuple[list[Contract], list[str]]:
    rows, errors = _read_csv(path, ["contract_id", "contractor", "amount", "signed_at"])
    built: list[Contract] = []
    for line_no, row in rows:
        ctx = f"{path.name}:{line_no}"
        amount = _optional(_amount, row["amount"], errors, ctx)
        signed = _optional(_required_date, row["signed_at"], errors, ctx)
        contract_id = row["contract_id"]
        if not contract_id:
            errors.append(f"{ctx}: пустой contract_id")
        if not row["contractor"]:
            errors.append(f"{ctx}: пустой подрядчик")
        if amount is None or signed is None or not contract_id or not row["contractor"]:
            continue
        built.append(Contract(contract_id, row["contractor"], amount, signed))
    return built, errors


def _load_works(path: Path) -> tuple[list[Work], list[str]]:
    rows, errors = _read_csv(
        path,
        ["work_id", "contract_id", "title", "price", "status", "completed_at", "cancelled_at"],
    )
    built: list[Work] = []
    for line_no, row in rows:
        ctx = f"{path.name}:{line_no}"
        price = _optional(_amount, row["price"], errors, ctx)
        completed = _optional(_optional_date, row["completed_at"], errors, ctx)
        cancelled = _optional(_optional_date, row["cancelled_at"], errors, ctx)
        if row["status"] not in STATUSES_WORK:
            errors.append(f"{ctx}: статус работы {row['status'] or 'пустой'}")
        if not row["work_id"] or not row["contract_id"] or not row["title"]:
            errors.append(f"{ctx}: пустой номер работы, договор или название")
        if price is None or not row["work_id"] or not row["contract_id"] or not row["title"]:
            continue
        if row["status"] not in STATUSES_WORK:
            continue
        built.append(
            Work(
                row["work_id"],
                row["contract_id"],
                row["title"],
                price,
                row["status"],
                completed,
                cancelled,
            )
        )
    return built, errors


def _load_payments(path: Path) -> tuple[list[Payment], list[str]]:
    rows, errors = _read_csv(
        path,
        [
            "payment_id",
            "contract_id",
            "work_id",
            "purpose",
            "amount",
            "status",
            "created_at",
            "paid_at",
            "refunded_at",
        ],
    )
    built: list[Payment] = []
    for line_no, row in rows:
        ctx = f"{path.name}:{line_no}"
        amount = _optional(_amount, row["amount"], errors, ctx)
        created = _optional(_required_date, row["created_at"], errors, ctx)
        paid = _optional(_optional_date, row["paid_at"], errors, ctx)
        refunded = _optional(_optional_date, row["refunded_at"], errors, ctx)
        if row["status"] not in STATUSES_PAYMENT:
            errors.append(f"{ctx}: статус платежа {row['status'] or 'пустой'}")
        if not row["payment_id"] or not row["contract_id"]:
            errors.append(f"{ctx}: пустой номер платежа или договора")
        if (
            amount is None
            or created is None
            or not row["payment_id"]
            or not row["contract_id"]
            or row["status"] not in STATUSES_PAYMENT
        ):
            continue
        built.append(
            Payment(
                row["payment_id"],
                row["contract_id"],
                row["work_id"] or None,
                row["purpose"],
                amount,
                row["status"],
                created,
                paid,
                refunded,
            )
        )
    return built, errors


def _read_csv(path: Path, header: list[str]) -> tuple[list[tuple[int, dict[str, str]]], list[str]]:
    if not path.is_file():
        return [], [f"Нет файла {path.name}"]
    try:
        with path.open(encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            actual = list(reader.fieldnames or [])
            if actual != header:
                expected = ", ".join(header)
                got = ", ".join(actual) if actual else "пусто"
                return [], [f"{path.name}: ожидались колонки {expected}, в файле: {got}"]
            rows: list[tuple[int, dict[str, str]]] = []
            for number, row in enumerate(reader, start=2):
                cleaned = {key: (row.get(key) or "").strip() for key in header}
                if not any(cleaned.values()):
                    continue
                rows.append((number, cleaned))
            return rows, []
    except UnicodeDecodeError:
        return [], [f"{path.name}: нужна кодировка UTF-8"]


def _optional(parser, raw: str, errors: list[str], ctx: str):
    try:
        return parser(raw)
    except ValueError as exc:
        errors.append(f"{ctx}: {exc}")
        return None


def _amount(raw: str):
    return parse_amount(raw)


def _required_date(raw: str) -> date:
    if not raw.strip():
        raise ValueError("пустая дата")
    return _date(raw)


def _optional_date(raw: str) -> date | None:
    if not raw.strip():
        return None
    return _date(raw)


def _date(raw: str) -> date:
    try:
        return datetime.strptime(raw.strip(), "%Y-%m-%d").date()
    except ValueError as exc:
        raise ValueError(f"дата {raw!r} нужна в виде ГГГГ-ММ-ДД") from exc
