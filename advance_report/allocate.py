"""Сколько на дату уже закрыто по каждой работе и сколько осталось по договору.

Правило зачёта, коротко:

* деньги входят с paid_at и выходят с refunded_at;
* платёж с работой остаётся на этой работе и считается частью её цены;
* платёж без работы зачитывается только в уже принятые работы, с самой ранней;
* что не влезло в принятые — не разносится на будущие, а остаётся авансом договора;
* отменённая на эту дату работа в долг не входит.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from decimal import Decimal

from advance_report.load import Data, DataError, Payment, Work, id_key, validate
from advance_report.money import ZERO, quantize

IN = "in"
REFUND = "refund"
PENDING = "pending"
LATER = "later"

DONE = "done"
OPEN = "open"
CANCELLED = "cancelled"


@dataclass(frozen=True)
class PaymentLine:
    payment: Payment
    counted: Decimal
    kind: str
    note: str


@dataclass(frozen=True)
class WorkLine:
    work: Work
    state: str
    obligation: Decimal
    direct: Decimal
    direct_ids: tuple[str, ...]
    offset: Decimal
    paid: Decimal
    remaining: Decimal
    order: int | None


@dataclass(frozen=True)
class ContractReport:
    contract_id: str
    contractor: str
    signed_at: date
    contract_amount: Decimal
    price_sum: Decimal
    obligation: Decimal
    cancelled_amount: Decimal
    cash_paid: Decimal
    attributed: Decimal
    unallocated: Decimal
    works_remaining: Decimal
    remaining: Decimal
    pending: Decimal
    later: Decimal
    notes: tuple[str, ...]
    works: tuple[WorkLine, ...]
    payments: tuple[PaymentLine, ...]


@dataclass(frozen=True)
class Report:
    as_of: date
    contracts: tuple[ContractReport, ...]


def build_report(data: Data, as_of: date) -> Report:
    # Повторная проверка: отчёт из кривых ссылок не строим.
    validate(data)
    works_of = _grouped(data.works, lambda item: item.contract_id)
    payments_of = _grouped(data.payments, lambda item: item.contract_id)
    contracts: list[ContractReport] = []
    for contract in sorted(data.contracts, key=lambda item: id_key(item.contract_id)):
        works = works_of.get(contract.contract_id, [])
        payments = payments_of.get(contract.contract_id, [])
        if contract.signed_at > as_of:
            _reject_hidden_activity(contract.contract_id, works, payments, as_of)
            continue
        contracts.append(_contract_report(contract, works, payments, as_of))
    return Report(as_of, tuple(contracts))


def work_state(work: Work, as_of: date) -> str:
    """Состояние именно на дату отчёта. Столбец status — на момент выгрузки."""
    if work.cancelled_at is not None and work.cancelled_at <= as_of:
        return CANCELLED
    if work.completed_at is not None and work.completed_at <= as_of:
        return DONE
    return OPEN


def classify_payment(payment: Payment, as_of: date) -> PaymentLine:
    paid = payment.paid_at is not None and payment.paid_at <= as_of
    refunded = payment.refunded_at is not None and payment.refunded_at <= as_of
    if paid and not refunded:
        note = "деньги у подрядчика, входит в выплаченное"
        if payment.refunded_at is not None and payment.refunded_at > as_of:
            note += (
                f"; возврат {_dot(payment.refunded_at)} на эту дату ещё не случился, "
                "хотя в выгрузке платёж уже возвращён"
            )
        return PaymentLine(payment, payment.amount, IN, note)
    if paid and refunded:
        return PaymentLine(
            payment,
            ZERO,
            REFUND,
            f"возврат {_dot(payment.refunded_at)}, в выплаченное не входит",
        )
    if payment.created_at > as_of and (payment.paid_at is None or payment.paid_at > as_of):
        return PaymentLine(payment, ZERO, LATER, "заведён позже даты отчёта, не входит")
    return PaymentLine(payment, ZERO, PENDING, "деньги ещё не ушли, в выплаченное не входит")


def _contract_report(contract, works: list[Work], payments: list[Payment], as_of: date) -> ContractReport:
    payment_lines = tuple(
        sorted(
            (classify_payment(payment, as_of) for payment in payments),
            key=lambda line: (line.payment.paid_at or line.payment.created_at, id_key(line.payment.payment_id)),
        )
    )
    direct = {work.work_id: ZERO for work in works}
    direct_ids: dict[str, list[str]] = {work.work_id: [] for work in works}
    pool = ZERO
    for line in payment_lines:
        if line.counted == ZERO:
            continue
        if line.payment.work_id:
            direct[line.payment.work_id] = quantize(direct[line.payment.work_id] + line.counted)
            direct_ids[line.payment.work_id].append(line.payment.payment_id)
        else:
            pool = quantize(pool + line.counted)

    states = {work.work_id: work_state(work, as_of) for work in works}
    offset = {work.work_id: ZERO for work in works}
    order: dict[str, int] = {}
    done_works = sorted(
        (work for work in works if states[work.work_id] == DONE),
        key=lambda work: (work.completed_at or date.min, id_key(work.work_id)),
    )
    for index, work in enumerate(done_works, start=1):
        order[work.work_id] = index
        need = work.price - direct[work.work_id]
        if need < ZERO:
            need = ZERO
        take = need if need < pool else pool
        offset[work.work_id] = quantize(take)
        pool = quantize(pool - take)

    lines: list[WorkLine] = []
    for work in sorted(works, key=lambda item: id_key(item.work_id)):
        state = states[work.work_id]
        obligation = ZERO if state == CANCELLED else work.price
        paid = quantize(direct[work.work_id] + offset[work.work_id])
        lines.append(
            WorkLine(
                work=work,
                state=state,
                obligation=obligation,
                direct=direct[work.work_id],
                direct_ids=tuple(direct_ids[work.work_id]),
                offset=offset[work.work_id],
                paid=paid,
                remaining=quantize(obligation - paid),
                order=order.get(work.work_id),
            )
        )

    cash = quantize(sum((line.counted for line in payment_lines), ZERO))
    obligation = quantize(sum((line.obligation for line in lines), ZERO))
    price_sum = quantize(sum((work.price for work in works), ZERO))
    cancelled_amount = quantize(sum((work.price for work in works if states[work.work_id] == CANCELLED), ZERO))
    attributed = quantize(sum((line.paid for line in lines), ZERO))
    works_remaining = quantize(sum((line.remaining for line in lines), ZERO))
    pending = quantize(sum((line.payment.amount for line in payment_lines if line.kind == PENDING), ZERO))
    later = quantize(sum((line.payment.amount for line in payment_lines if line.kind == LATER), ZERO))
    notes: list[str] = []
    if price_sum != contract.amount:
        notes.append(
            "Сумма цен работ не равна сумме договора. "
            "К оплате и остаток считаются по работам, сумма договора показана рядом для сверки."
        )
    report = ContractReport(
        contract_id=contract.contract_id,
        contractor=contract.contractor,
        signed_at=contract.signed_at,
        contract_amount=contract.amount,
        price_sum=price_sum,
        obligation=obligation,
        cancelled_amount=cancelled_amount,
        cash_paid=cash,
        attributed=attributed,
        unallocated=pool,
        works_remaining=works_remaining,
        remaining=quantize(obligation - cash),
        pending=pending,
        later=later,
        notes=tuple(notes),
        works=tuple(lines),
        payments=payment_lines,
    )
    _check_identity(report)
    return report


def _check_identity(report: ContractReport) -> None:
    paid = quantize(sum((line.paid for line in report.works), ZERO))
    if paid != report.attributed:
        raise AssertionError(f"{report.contract_id}: разнесённое не сходится с работами")
    if quantize(report.attributed + report.unallocated) != report.cash_paid:
        raise AssertionError(f"{report.contract_id}: выплаченное не равно разнесённому плюс аванс")
    if quantize(report.obligation - report.cash_paid) != report.remaining:
        raise AssertionError(f"{report.contract_id}: остаток договора не равен долгу минус выплаты")
    if quantize(report.works_remaining - report.unallocated) != report.remaining:
        raise AssertionError(f"{report.contract_id}: остатки работ не сходятся с итогом договора")
    if quantize(report.price_sum - report.cancelled_amount) != report.obligation:
        raise AssertionError(f"{report.contract_id}: к оплате не равно ценам без отменённых")
    for line in report.works:
        if line.paid != quantize(line.direct + line.offset):
            raise AssertionError(f"{line.work.work_id}: выплачено не равно прямому плюс зачёт")
        if line.remaining != quantize(line.obligation - line.paid):
            raise AssertionError(f"{line.work.work_id}: остаток не равен к оплате минус выплачено")
        if line.state != DONE and line.offset != ZERO:
            raise AssertionError(f"{line.work.work_id}: зачёт аванса попал не в принятую работу")
        if line.state == CANCELLED and line.obligation != ZERO:
            raise AssertionError(f"{line.work.work_id}: отменённая работа осталась в долге")


def _reject_hidden_activity(contract_id: str, works: list[Work], payments: list[Payment], as_of: date) -> None:
    errors: list[str] = []
    for work in works:
        if (work.completed_at is not None and work.completed_at <= as_of) or (
            work.cancelled_at is not None and work.cancelled_at <= as_of
        ):
            errors.append(
                f"Договор {contract_id} на { _dot(as_of) } ещё не подписан, "
                f"но у работы {work.work_id} уже есть приёмка или отмена"
            )
    for payment in payments:
        happened = [
            value
            for value in (payment.created_at, payment.paid_at, payment.refunded_at)
            if value is not None and value <= as_of
        ]
        if happened:
            errors.append(
                f"Договор {contract_id} на {_dot(as_of)} ещё не подписан, "
                f"но платёж {payment.payment_id} уже есть в учёте"
            )
    if errors:
        raise DataError(errors)


def _grouped(items, key):
    groups: dict[str, list] = {}
    for item in items:
        groups.setdefault(key(item), []).append(item)
    return groups


def _dot(value: date) -> str:
    return value.strftime("%d.%m.%Y")
