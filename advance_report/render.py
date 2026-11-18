"""Текст для чтения и CSV, который можно открыть в таблице."""

from __future__ import annotations

import csv
from pathlib import Path

from advance_report.allocate import (
    CANCELLED,
    DONE,
    IN,
    LATER,
    OPEN,
    PENDING,
    REFUND,
    ContractReport,
    PaymentLine,
    Report,
    WorkLine,
)
from advance_report.money import ZERO, format_money, format_money_csv

WORK_HEADERS = (
    "договор",
    "подрядчик",
    "работа",
    "название",
    "состояние",
    "статус_в_выгрузке",
    "дата_состояния",
    "порядок_зачёта",
    "цена",
    "к_оплате",
    "прямой_платёж",
    "зачёт_аванса",
    "выплачено",
    "осталось",
    "комментарий",
)

CONTRACT_HEADERS = (
    "договор",
    "подрядчик",
    "подписан",
    "сумма_договора",
    "сумма_цен_работ",
    "к_оплате",
    "отменено",
    "выплачено",
    "разнесено_на_работы",
    "не_разнесено",
    "остатки_по_работам",
    "осталось_оплатить",
    "справочно_ещё_не_ушло",
    "справочно_после_даты",
    "примечание",
)

PAYMENT_HEADERS = (
    "платёж",
    "договор",
    "работа",
    "назначение",
    "сумма",
    "статус_в_выгрузке",
    "заведён",
    "оплачен",
    "возвращён",
    "учтено",
    "комментарий",
)

UNALLOCATED_ID = "НЕ-РАЗНЕСЕНО"

_EXPORT_STATUS = {
    "planned": "запланирована",
    "done": "выполнена",
    "cancelled": "отменена",
    "paid": "оплачен",
    "queued": "в очереди",
    "refunded": "возвращён",
}


def render_text(report: Report) -> str:
    as_of = _dot(report.as_of)
    parts = [
        f"Отчёт по выплатам на {as_of}",
        "",
        "Аванс без номера работы зачтён только в уже принятые работы, с самой ранней приёмки.",
        "Строка «Не разнесено по работам» — это уже выплаченные деньги. В «осталось оплатить»",
        "по договору они уже вычтены, второй раз их вычитать не нужно.",
        "",
    ]
    if not report.contracts:
        parts.append("На эту дату подписанных договоров нет.")
        parts.append("")
        parts.extend(_rules())
        return "\n".join(parts).rstrip() + "\n"

    parts.append("Итого по всем договорам")
    parts.append(_pair("К оплате по работам", _sum(report, "obligation")))
    parts.append(_pair("Выплачено", _sum(report, "cash_paid")))
    parts.append(_pair("Из них не разнесено", _sum(report, "unallocated")))
    parts.append(_pair("Осталось оплатить", _sum(report, "remaining")))
    obligation = _sum(report, "obligation")
    paid = _sum(report, "cash_paid")
    remaining = _sum(report, "remaining")
    parts.append(
        f"  Сверка: к оплате {format_money(obligation)} минус выплачено "
        f"{format_money(paid)} = осталось {format_money(remaining)}."
    )
    pending = _sum(report, "pending")
    later = _sum(report, "later")
    if pending != ZERO:
        parts.append(_pair("Справочно: заведено, не ушло", pending))
    if later != ZERO:
        parts.append(_pair("Справочно: платежи после даты", later))
    if pending != ZERO or later != ZERO:
        parts.append(_reference_note((pending != ZERO) + (later != ZERO)))
    parts.append("")

    for contract in report.contracts:
        parts.extend(_render_contract(contract, report.as_of))
    parts.extend(_rules())
    return "\n".join(parts).rstrip() + "\n"


def write_report(report: Report, folder: Path) -> list[Path]:
    folder.mkdir(parents=True, exist_ok=True)
    stamp = report.as_of.isoformat()
    text_path = folder / f"report-{stamp}.txt"
    works_path = folder / f"works-{stamp}.csv"
    contracts_path = folder / f"contracts-{stamp}.csv"
    payments_path = folder / f"payments-{stamp}.csv"
    text_path.write_text(render_text(report), encoding="utf-8")
    _write_csv(works_path, WORK_HEADERS, _work_rows(report))
    _write_csv(contracts_path, CONTRACT_HEADERS, _contract_rows(report))
    _write_csv(payments_path, PAYMENT_HEADERS, _payment_rows(report))
    return [text_path, works_path, contracts_path, payments_path]


def _render_contract(contract: ContractReport, as_of) -> list[str]:
    lines = [
        "—" * 36,
        "",
        f"Договор {contract.contract_id} — {contract.contractor}",
        f"Подписан {_dot(contract.signed_at)}",
        "",
        _pair("Сумма договора", contract.contract_amount),
        _pair("Сумма цен всех работ", contract.price_sum),
        _pair("К оплате (без отменённых)", contract.obligation),
        _pair("Отменено и из долга убрано", contract.cancelled_amount),
        _pair("Выплачено", contract.cash_paid),
        _pair("Разнесено на работы", contract.attributed),
        _pair("Не разнесено", contract.unallocated),
        _pair("Остатки по строкам работ", contract.works_remaining),
        _pair("Осталось оплатить", contract.remaining),
        (
            f"  Остатки по работам {format_money(contract.works_remaining)} "
            f"минус не разнесено {format_money(contract.unallocated)} "
            f"= осталось оплатить {format_money(contract.remaining)}."
        ),
    ]
    if contract.pending != ZERO:
        lines.append(_pair("Справочно: заведено, не ушло", contract.pending))
    if contract.later != ZERO:
        lines.append(_pair("Справочно: платежи после даты", contract.later))
    shown = (contract.pending != ZERO) + (contract.later != ZERO)
    if shown:
        lines.append(_reference_note(shown))
    if contract.remaining < ZERO:
        lines.append("  Переплата: подрядчику выплачено больше, чем стоят работы к оплате.")
    for note in contract.notes:
        lines.append(f"  {note}")
    lines.append("")
    lines.append("  Работы")
    for work in contract.works:
        lines.append("")
        lines.extend(_render_work(work, as_of))
    lines.append("")
    lines.extend(_render_unallocated(contract))
    lines.append("")
    lines.extend(_render_payments(contract))
    lines.append("")
    return lines


def _render_work(line: WorkLine, as_of) -> list[str]:
    return [
        f"  {line.work.work_id}  {line.work.title}",
        f"  {_state_label(line)}",
        (
            f"  Цена {format_money(line.work.price)}"
            f"    К оплате {format_money(line.obligation)}"
            f"    Выплачено {format_money(line.paid)}"
            f"    Осталось {format_money(line.remaining)}"
        ),
        f"  {_work_comment(line, as_of)}",
    ]


def _render_unallocated(contract: ContractReport) -> list[str]:
    return [
        "  НЕ-РАЗНЕСЕНО  Не разнесено по работам",
        "  Уже выплачено по договору, на конкретную работу не отнесено",
        (
            f"  Выплачено {format_money(contract.unallocated)}"
            f"    Осталось {format_money(-contract.unallocated)}"
        ),
        (
            "  В колонке «осталось» сумма со знаком минус, чтобы сложение строк дало итог договора. "
            "Это не новая сумма к оплате: деньги уже у подрядчика и из «осталось оплатить» уже вычтены."
        ),
    ]


def _render_payments(contract: ContractReport) -> list[str]:
    included = [line for line in contract.payments if line.kind == IN]
    excluded = [line for line in contract.payments if line.kind != IN]
    lines = ["  Платежи"]
    if included:
        lines.append("  Вошли в выплаченное:")
        lines.extend(_payment_line(line) for line in included)
    if excluded:
        lines.append("  Не вошли в выплаченное:")
        lines.extend(_payment_line(line) for line in excluded)
    if not included and not excluded:
        lines.append("  Платежей нет.")
    return lines


def _payment_line(line: PaymentLine) -> str:
    payment = line.payment
    work = payment.work_id or "без работы"
    if line.kind in (IN, REFUND) and payment.paid_at is not None:
        when = f"оплачен {_dot(payment.paid_at)}"
    else:
        when = f"заведён {_dot(payment.created_at)}"
    status = _EXPORT_STATUS.get(payment.status, payment.status)
    purpose = payment.purpose or "без назначения"
    return (
        f"    {payment.payment_id:<5} {format_money(payment.amount):>12}"
        f"   {when}   {work}   {purpose}"
        f"   (в выгрузке: {status}; {line.note})"
    )


def _state_label(line: WorkLine) -> str:
    if line.state == DONE:
        return f"Принята {_dot(line.work.completed_at)}, порядок зачёта {line.order}"
    if line.state == CANCELLED:
        return f"Отменена {_dot(line.work.cancelled_at)}"
    return "Ещё не принята"


def _work_comment(line: WorkLine, as_of) -> str:
    work = line.work
    if line.state == CANCELLED:
        text = "В «к оплате» не входит."
        if line.paid > ZERO:
            text += " Прямой платёж оставлен на этой работе и на другие не переносился."
        return text
    if line.state == OPEN:
        if work.status == "done" and work.completed_at is not None and work.completed_at > as_of:
            head = (
                f"В выгрузке работа уже «выполнена», но приёмка {_dot(work.completed_at)} "
                "позже даты отчёта, поэтому на эту дату она ещё не принята."
            )
        elif work.status == "cancelled" and work.cancelled_at is not None and work.cancelled_at > as_of:
            head = (
                f"На эту дату работа ещё в договоре. В выгрузке она отменена {_dot(work.cancelled_at)} — "
                "это позже даты отчёта."
            )
        else:
            head = ""
        if line.direct > ZERO:
            paid = f"Учтён прямой платёж {_ids(line)} на {format_money(line.direct)}."
            return _join(head, paid, "Общий аванс в непринятые работы не зачитывается.")
        return _join(head, "Общий аванс в непринятые работы не зачитывается.")

    parts: list[str] = []
    if line.direct > ZERO:
        parts.append(f"прямой платёж {_ids(line)} {format_money(line.direct)}")
    if line.offset > ZERO:
        parts.append(f"зачёт аванса {format_money(line.offset)}")
    how = "Выплачено = " + " + ".join(parts) + "." if parts else "Денег на эту работу пока не отнесено."
    if line.remaining == ZERO:
        return f"{how} Работа закрыта."
    if line.remaining > ZERO:
        return f"{how} Принята, но закрыта не до конца."
    return f"{how} Прямой платёж больше цены, излишек на другие работы не переносился."


def _rules() -> list[str]:
    return [
        "Правила этого отчёта",
        "1. В «выплачено» входят деньги, которые на дату отчёта уже у подрядчика: с даты оплаты",
        "   и до даты возврата, если возврат был. Платёж в очереди не входит. Дата отчёта включительно.",
        "2. Платёж с номером работы относится на эту работу и уменьшает её цену, даже если работу",
        "   ещё не приняли. На другие работы он не переносится.",
        "3. Платёж без номера работы зачитывается в принятые работы по дате приёмки: раньше принятая",
        "   закрывается первой. Если даты совпадут, первой идёт меньший номер работы.",
        "4. В ещё не принятые работы этот аванс не зачитывается. Остаток — строка «Не разнесено».",
        "5. Работа, отменённая на дату отчёта, в «к оплате» не входит. Состояние берётся по датам",
        "   приёмки и отмены. Столбец статуса в файле — это состояние на момент выгрузки.",
        "",
    ]


def _work_rows(report: Report) -> list[list[str]]:
    rows: list[list[str]] = []
    for contract in report.contracts:
        for line in contract.works:
            rows.append(
                [
                    contract.contract_id,
                    contract.contractor,
                    line.work.work_id,
                    line.work.title,
                    _state_short(line),
                    _EXPORT_STATUS.get(line.work.status, line.work.status),
                    _state_date(line),
                    "" if line.order is None else str(line.order),
                    format_money_csv(line.work.price),
                    format_money_csv(line.obligation),
                    format_money_csv(line.direct),
                    format_money_csv(line.offset),
                    format_money_csv(line.paid),
                    format_money_csv(line.remaining),
                    _work_comment(line, report.as_of),
                ]
            )
        rows.append(
            [
                contract.contract_id,
                contract.contractor,
                UNALLOCATED_ID,
                "Не разнесено по работам",
                "аванс",
                "",
                "",
                "",
                format_money_csv(ZERO),
                format_money_csv(ZERO),
                format_money_csv(ZERO),
                format_money_csv(ZERO),
                format_money_csv(contract.unallocated),
                format_money_csv(-contract.unallocated),
                (
                    "Уже выплачено, на работы не отнесено. В «осталось» стоит со знаком минус, "
                    "чтобы сумма колонки сошлась с «осталось оплатить» договора. "
                    "Это не новая сумма к оплате."
                ),
            ]
        )
    return rows


def _contract_rows(report: Report) -> list[list[str]]:
    rows: list[list[str]] = []
    for contract in report.contracts:
        remark = list(contract.notes)
        if contract.remaining < ZERO:
            remark.append("переплата, выплачено больше суммы работ к оплате")
        rows.append(
            [
                contract.contract_id,
                contract.contractor,
                _dot(contract.signed_at),
                format_money_csv(contract.contract_amount),
                format_money_csv(contract.price_sum),
                format_money_csv(contract.obligation),
                format_money_csv(contract.cancelled_amount),
                format_money_csv(contract.cash_paid),
                format_money_csv(contract.attributed),
                format_money_csv(contract.unallocated),
                format_money_csv(contract.works_remaining),
                format_money_csv(contract.remaining),
                format_money_csv(contract.pending),
                format_money_csv(contract.later),
                " ".join(remark),
            ]
        )
    return rows


def _payment_rows(report: Report) -> list[list[str]]:
    rows: list[list[str]] = []
    for contract in report.contracts:
        for line in contract.payments:
            payment = line.payment
            rows.append(
                [
                    payment.payment_id,
                    payment.contract_id,
                    payment.work_id or "",
                    payment.purpose,
                    format_money_csv(payment.amount),
                    _EXPORT_STATUS.get(payment.status, payment.status),
                    _dot(payment.created_at),
                    _dot(payment.paid_at) if payment.paid_at else "",
                    _dot(payment.refunded_at) if payment.refunded_at else "",
                    format_money_csv(line.counted),
                    line.note,
                ]
            )
    return rows


def _state_short(line: WorkLine) -> str:
    if line.state == DONE:
        return "принята"
    if line.state == CANCELLED:
        return "отменена"
    if line.state == OPEN:
        return "не принята"
    return line.state


def _state_date(line: WorkLine) -> str:
    if line.state == DONE and line.work.completed_at is not None:
        return _dot(line.work.completed_at)
    if line.state == CANCELLED and line.work.cancelled_at is not None:
        return _dot(line.work.cancelled_at)
    return ""


def _reference_note(shown: int) -> str:
    if shown == 1:
        return "  Эта справочная сумма в «выплачено» и в «осталось оплатить» не входит."
    return "  Эти справочные суммы в «выплачено» и в «осталось оплатить» не входят."


def _join(*parts: str) -> str:
    return " ".join(part for part in parts if part)


def _ids(line: WorkLine) -> str:
    return ", ".join(line.direct_ids)


def _pair(label: str, value) -> str:
    return f"  {label:<36}{format_money(value):>14}"


def _sum(report: Report, name: str):
    return sum((getattr(contract, name) for contract in report.contracts), ZERO)


def _dot(value) -> str:
    return value.strftime("%d.%m.%Y")


def _write_csv(path: Path, header: tuple[str, ...], rows: list[list[str]]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle, delimiter=";", lineterminator="\n")
        writer.writerow(header)
        writer.writerows(rows)
