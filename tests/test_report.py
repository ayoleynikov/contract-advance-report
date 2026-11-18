import csv
import io
import subprocess
import sys
import tempfile
import unittest
from datetime import date
from decimal import Decimal
from pathlib import Path

from advance_report.allocate import CANCELLED, DONE, IN, LATER, OPEN, PENDING, REFUND, build_report
from advance_report.load import Contract, Data, DataError, Payment, Work, load_data
from advance_report.money import format_money, parse_amount, quantize
from advance_report.render import WORK_HEADERS, render_text, write_report

ROOT = Path(__file__).resolve().parents[1]
ZERO = Decimal("0.00")
D = Decimal


def money(text: str) -> Decimal:
    return D(text)


class ReportTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = load_data(ROOT / "data")

    def report(self, day: date):
        return build_report(self.data, day)

    def contract(self, day: date, contract_id: str):
        report = self.report(day)
        for item in report.contracts:
            if item.contract_id == contract_id:
                return item
        self.fail(f"нет договора {contract_id} на {day}")

    def work(self, day: date, contract_id: str, work_id: str):
        item = self.contract(day, contract_id)
        for line in item.works:
            if line.work.work_id == work_id:
                return line
        self.fail(f"нет работы {work_id}")

    def test_names_survive_csv_quotes(self):
        d1 = self.contract(date(2026, 11, 10), "D-1")
        d2 = self.contract(date(2026, 11, 10), "D-2")
        self.assertEqual(d1.contractor, "Студия «Свет»")
        self.assertEqual(d2.contractor, "Иван Петров, дизайнер")
        purpose = {
            line.payment.payment_id: line.payment.purpose
            for line in d1.payments
        }
        self.assertEqual(purpose["P-2"], "Аванс, ошибочный платёж")

    def test_nov_10_contract_d1(self):
        item = self.contract(date(2026, 11, 10), "D-1")
        self.assertEqual(item.contract_amount, money("600000.00"))
        self.assertEqual(item.price_sum, money("600000.00"))
        self.assertEqual(item.obligation, money("500000.00"))
        self.assertEqual(item.cancelled_amount, money("100000.00"))
        self.assertEqual(item.cash_paid, money("315000.00"))
        self.assertEqual(item.attributed, money("300000.00"))
        self.assertEqual(item.unallocated, money("15000.00"))
        self.assertEqual(item.works_remaining, money("200000.00"))
        self.assertEqual(item.remaining, money("185000.00"))
        self.assertEqual(item.pending, money("150000.00"))
        self.assertEqual(item.later, ZERO)
        self.assertEqual(item.notes, ())
        self.assertIsInstance(item.cash_paid, Decimal)

        counted = {line.payment.payment_id: line for line in item.payments}
        self.assertEqual(counted["P-1"].kind, IN)
        self.assertEqual(counted["P-1"].counted, money("300000.00"))
        self.assertEqual(counted["P-2"].kind, REFUND)
        self.assertEqual(counted["P-2"].counted, ZERO)
        self.assertEqual(counted["P-3"].kind, IN)
        self.assertEqual(counted["P-3"].counted, money("15000.00"))
        self.assertEqual(counted["P-4"].kind, PENDING)
        self.assertEqual(counted["P-4"].counted, ZERO)

    def test_nov_10_works_d1_are_settled_fifo_not_half(self):
        day = date(2026, 11, 10)
        spring = self.work(day, "D-1", "W-11")
        summer = self.work(day, "D-1", "W-12")
        accessories = self.work(day, "D-1", "W-13")
        autumn = self.work(day, "D-1", "W-14")
        winter = self.work(day, "D-1", "W-15")
        new_year = self.work(day, "D-1", "W-16")

        self.assertEqual(spring.state, DONE)
        self.assertEqual(spring.order, 1)
        self.assertEqual(spring.paid, money("100000.00"))
        self.assertEqual(spring.offset, money("100000.00"))
        self.assertEqual(spring.remaining, ZERO)

        self.assertEqual(summer.order, 2)
        self.assertEqual(summer.paid, money("100000.00"))
        self.assertEqual(summer.remaining, ZERO)

        self.assertEqual(accessories.state, CANCELLED)
        self.assertEqual(accessories.obligation, ZERO)
        self.assertEqual(accessories.paid, ZERO)
        self.assertEqual(accessories.remaining, ZERO)
        self.assertIsNone(accessories.order)

        self.assertEqual(autumn.state, DONE)
        self.assertEqual(autumn.order, 3)
        self.assertEqual(autumn.direct, money("15000.00"))
        self.assertEqual(autumn.direct_ids, ("P-3",))
        self.assertEqual(autumn.offset, money("85000.00"))
        self.assertEqual(autumn.paid, money("100000.00"))
        self.assertEqual(autumn.remaining, ZERO)

        self.assertEqual(winter.state, OPEN)
        self.assertEqual(winter.paid, ZERO)
        self.assertEqual(winter.remaining, money("100000.00"))
        self.assertEqual(new_year.state, OPEN)
        self.assertEqual(new_year.remaining, money("100000.00"))
        # Не «аванс 50%»: иначе по каждой работе было бы 50 000, а не полное закрытие.
        self.assertNotEqual(spring.paid, money("50000.00"))

    def test_nov_10_contract_d2_packaging_not_accepted_yet(self):
        day = date(2026, 11, 10)
        item = self.contract(day, "D-2")
        self.assertEqual(item.cash_paid, money("75000.00"))
        self.assertEqual(item.obligation, money("90000.00"))
        self.assertEqual(item.unallocated, money("25000.00"))
        self.assertEqual(item.attributed, money("50000.00"))
        self.assertEqual(item.remaining, money("15000.00"))
        self.assertEqual(item.works_remaining, money("40000.00"))
        self.assertEqual(item.later, money("20000.00"))
        self.assertEqual(item.pending, ZERO)

        logo = self.work(day, "D-2", "W-21")
        style = self.work(day, "D-2", "W-22")
        pack = self.work(day, "D-2", "W-23")
        self.assertEqual(logo.paid, money("20000.00"))
        self.assertEqual(logo.remaining, ZERO)
        self.assertEqual(style.paid, money("30000.00"))
        self.assertEqual(style.remaining, ZERO)
        self.assertEqual(pack.state, OPEN)
        self.assertEqual(pack.work.status, "done")
        self.assertEqual(pack.paid, ZERO)
        self.assertEqual(pack.offset, ZERO)
        self.assertEqual(pack.remaining, money("40000.00"))

        counted = {line.payment.payment_id: line for line in item.payments}
        self.assertEqual(counted["P-7"].kind, IN)
        self.assertEqual(counted["P-7"].counted, money("5000.00"))
        self.assertEqual(counted["P-8"].kind, LATER)
        self.assertEqual(counted["P-8"].counted, ZERO)

    def test_refund_is_in_cash_only_between_payment_and_refund(self):
        def cash(day):
            return self.contract(day, "D-1").cash_paid

        self.assertEqual(cash(date(2026, 9, 1)), money("300000.00"))
        self.assertEqual(cash(date(2026, 9, 2)), money("350000.00"))
        self.assertEqual(cash(date(2026, 9, 9)), money("350000.00"))
        self.assertEqual(cash(date(2026, 9, 10)), money("300000.00"))

    def test_advance_is_not_spread_before_any_acceptance(self):
        item = self.contract(date(2026, 9, 1), "D-1")
        self.assertEqual([c.contract_id for c in self.report(date(2026, 9, 1)).contracts], ["D-1"])
        self.assertEqual(item.unallocated, money("300000.00"))
        self.assertEqual(item.remaining, money("300000.00"))
        self.assertTrue(all(line.state == OPEN and line.paid == ZERO for line in item.works))

    def test_contract_hidden_until_signed(self):
        self.assertEqual(self.report(date(2026, 8, 24)).contracts, ())
        signed = self.contract(date(2026, 8, 25), "D-1")
        self.assertEqual(signed.cash_paid, ZERO)
        self.assertEqual(signed.remaining, money("600000.00"))
        self.assertNotIn("D-2", [c.contract_id for c in self.report(date(2026, 9, 19)).contracts])
        self.assertIn("D-2", [c.contract_id for c in self.report(date(2026, 9, 20)).contracts])

    def test_cancellation_starts_on_its_date(self):
        before = self.work(date(2026, 10, 11), "D-1", "W-13")
        on_day = self.work(date(2026, 10, 12), "D-1", "W-13")
        self.assertEqual(before.state, OPEN)
        self.assertEqual(before.obligation, money("100000.00"))
        self.assertEqual(on_day.state, CANCELLED)
        self.assertEqual(on_day.obligation, ZERO)
        text = render_text(self.report(date(2026, 10, 11)))
        self.assertIn("12.10.2026", text)
        self.assertIn("позже даты отчёта", text)

    def test_linked_payment_counts_before_acceptance(self):
        before = self.work(date(2026, 10, 27), "D-1", "W-14")
        on_day = self.work(date(2026, 10, 28), "D-1", "W-14")
        self.assertEqual(before.state, OPEN)
        self.assertEqual(before.direct, money("15000.00"))
        self.assertEqual(before.offset, ZERO)
        self.assertEqual(before.remaining, money("85000.00"))
        self.assertEqual(self.contract(date(2026, 10, 27), "D-1").unallocated, money("100000.00"))
        self.assertEqual(on_day.state, DONE)
        self.assertEqual(on_day.offset, money("85000.00"))
        self.assertEqual(on_day.remaining, ZERO)
        self.assertEqual(self.contract(date(2026, 10, 28), "D-1").unallocated, money("15000.00"))
        self.assertEqual(self.contract(date(2026, 10, 28), "D-1").pending, ZERO)
        self.assertEqual(self.contract(date(2026, 11, 1), "D-1").pending, money("150000.00"))
        self.assertEqual(self.contract(date(2026, 11, 1), "D-1").cash_paid, money("315000.00"))

    def test_fifo_when_accepted_works_are_only_partly_funded(self):
        day = date(2026, 10, 10)
        item = self.contract(day, "D-2")
        logo = self.work(day, "D-2", "W-21")
        style = self.work(day, "D-2", "W-22")
        pack = self.work(day, "D-2", "W-23")
        self.assertEqual(item.cash_paid, money("45000.00"))
        self.assertEqual(item.unallocated, ZERO)
        self.assertEqual(logo.order, 1)
        self.assertEqual(logo.paid, money("20000.00"))
        self.assertEqual(logo.remaining, ZERO)
        self.assertEqual(style.order, 2)
        self.assertEqual(style.paid, money("25000.00"))
        self.assertEqual(style.remaining, money("5000.00"))
        self.assertEqual(pack.state, OPEN)
        self.assertEqual(pack.paid, ZERO)
        self.assertEqual(item.remaining, money("45000.00"))

    def test_acceptance_of_packaging_absorbs_unallocated_advance(self):
        day = date(2026, 11, 11)
        item = self.contract(day, "D-2")
        pack = self.work(day, "D-2", "W-23")
        self.assertEqual(pack.state, DONE)
        self.assertEqual(pack.paid, money("25000.00"))
        self.assertEqual(pack.remaining, money("15000.00"))
        self.assertEqual(item.cash_paid, money("75000.00"))
        self.assertEqual(item.unallocated, ZERO)
        self.assertEqual(item.remaining, money("15000.00"))

    def test_later_payment_makes_d2_overpaid(self):
        day = date(2026, 11, 12)
        item = self.contract(day, "D-2")
        pack = self.work(day, "D-2", "W-23")
        self.assertEqual(item.cash_paid, money("95000.00"))
        self.assertEqual(pack.paid, money("40000.00"))
        self.assertEqual(pack.remaining, ZERO)
        self.assertEqual(item.unallocated, money("5000.00"))
        self.assertEqual(item.remaining, money("-5000.00"))
        year_end = self.contract(date(2026, 12, 31), "D-1")
        self.assertEqual(year_end.cash_paid, money("315000.00"))
        self.assertEqual(year_end.pending, money("150000.00"))

    def test_identities_hold_on_every_boundary_date(self):
        days = [
            date(2026, 8, 24),
            date(2026, 8, 25),
            date(2026, 9, 1),
            date(2026, 9, 2),
            date(2026, 9, 5),
            date(2026, 9, 9),
            date(2026, 9, 10),
            date(2026, 9, 19),
            date(2026, 9, 20),
            date(2026, 9, 25),
            date(2026, 10, 1),
            date(2026, 10, 5),
            date(2026, 10, 10),
            date(2026, 10, 11),
            date(2026, 10, 12),
            date(2026, 10, 25),
            date(2026, 10, 27),
            date(2026, 10, 28),
            date(2026, 10, 30),
            date(2026, 11, 1),
            date(2026, 11, 2),
            date(2026, 11, 10),
            date(2026, 11, 11),
            date(2026, 11, 12),
            date(2026, 12, 31),
        ]
        for day in days:
            report = self.report(day)
            for item in report.contracts:
                counted = sum((line.counted for line in item.payments), ZERO)
                self.assertEqual(counted, item.cash_paid, day)
                self.assertEqual(item.works_remaining - item.unallocated, item.remaining, day)
                self.assertEqual(item.obligation - item.cash_paid, item.remaining, day)

    def test_text_mentions_every_payment_and_the_check_totals(self):
        text = render_text(self.report(date(2026, 11, 10)))
        for payment_id in [f"P-{n}" for n in range(1, 9)]:
            self.assertIn(payment_id, text)
        self.assertIn("185 000,00", text)
        self.assertIn("15 000,00", text)
        self.assertIn("200 000,00", text)
        self.assertIn("590 000,00", text)
        self.assertIn("390 000,00", text)
        self.assertIn("Не разнесено по работам", text)
        self.assertIn("позже даты отчёта", text)
        self.assertIn("договоров нет", render_text(self.report(date(2026, 8, 24))))

    def test_csv_columns_sum_to_contract_totals(self):
        for day in (date(2026, 10, 10), date(2026, 10, 25), date(2026, 11, 10), date(2026, 11, 12)):
            with tempfile.TemporaryDirectory() as tmp:
                paths = write_report(self.report(day), Path(tmp))
                works = _read_csv(paths[1])
                contracts = _read_csv(paths[2])
            self.assertEqual(works[0], list(WORK_HEADERS))
            by_contract: dict[str, list[dict[str, str]]] = {}
            for row in works[1:]:
                record = dict(zip(WORK_HEADERS, row))
                by_contract.setdefault(record["договор"], []).append(record)
            totals = {row[0]: dict(zip(contracts[0], row)) for row in contracts[1:]}
            self.assertEqual(set(by_contract), set(totals))
            for contract_id, rows in by_contract.items():
                total = totals[contract_id]
                self.assertEqual(_col(rows, "цена"), _csv_money(total["сумма_цен_работ"]))
                self.assertEqual(_col(rows, "к_оплате"), _csv_money(total["к_оплате"]))
                self.assertEqual(_col(rows, "выплачено"), _csv_money(total["выплачено"]))
                self.assertEqual(_col(rows, "осталось"), _csv_money(total["осталось_оплатить"]))
                advance = [row for row in rows if row["работа"] == "НЕ-РАЗНЕСЕНО"]
                self.assertEqual(len(advance), 1)
                self.assertEqual(_csv_money(advance[0]["осталось"]), -_csv_money(advance[0]["выплачено"]))
                for row in rows:
                    if row["работа"].startswith("W-"):
                        direct = _csv_money(row["прямой_платёж"])
                        offset = _csv_money(row["зачёт_аванса"])
                        self.assertEqual(direct + offset, _csv_money(row["выплачено"]))

    def test_cli_writes_the_check_date_report(self):
        with tempfile.TemporaryDirectory() as tmp:
            completed = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "report.py"),
                    "--date",
                    "10.11.2026",
                    "--data",
                    str(ROOT / "data"),
                    "--out",
                    tmp,
                ],
                cwd=ROOT,
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertIn("185 000,00", completed.stdout)
            report = Path(tmp, "report-2026-11-10.txt").read_text(encoding="utf-8")
            self.assertEqual(report, completed.stdout)
            raw = Path(tmp, "works-2026-11-10.csv").read_bytes()
            self.assertTrue(raw.startswith(b"\xef\xbb\xbf"))
            self.assertIn("15000,00".encode(), raw)
            self.assertIn("85000,00".encode(), raw)

    def test_money_stays_exact(self):
        self.assertEqual(format_money(D("100000")), "100 000,00")
        self.assertEqual(format_money(D("-5000")), "-5 000,00")
        self.assertEqual(format_money(D("0")), "0,00")
        self.assertEqual(quantize(D("1.005")), D("1.01"))
        self.assertEqual(quantize(D("1.004")), D("1.00"))
        with self.assertRaises(ValueError):
            parse_amount("10.555")
        with self.assertRaises(ValueError):
            parse_amount("10,50")

    def test_sources_do_not_call_float(self):
        for path in (ROOT / "advance_report").glob("*.py"):
            text = path.read_text(encoding="utf-8")
            self.assertNotIn("float(", text)
            self.assertNotIn("round(", text)

    def test_bad_rows_and_links_are_rejected(self):
        contract = Contract("D-9", "Тест", money("100.00"), date(2026, 1, 1))
        work = Work("W-9", "D-9", "Тест", money("100.00"), "planned", None, None)
        payment = Payment(
            "P-9",
            "D-9",
            "W-missing",
            "",
            money("10.00"),
            "paid",
            date(2026, 1, 2),
            date(2026, 1, 2),
            None,
        )
        with self.assertRaises(DataError) as caught:
            build_report(Data((contract,), (work,), (payment,)), date(2026, 2, 1))
        self.assertIn("W-missing", str(caught.exception))

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "contracts.csv").write_text(
                "contract_id,contractor,amount,signed_at\nD-1,Тест,10.555,2026-01-01\n",
                encoding="utf-8",
            )
            (root / "works.csv").write_text(
                "work_id,contract_id,title,price,status,completed_at,cancelled_at\n",
                encoding="utf-8",
            )
            (root / "payments.csv").write_text(
                "payment_id,contract_id,work_id,purpose,amount,status,created_at,paid_at,refunded_at\n",
                encoding="utf-8",
            )
            with self.assertRaises(DataError) as caught:
                load_data(root)
        self.assertIn("10.555", str(caught.exception))

    def test_excess_linked_payment_is_not_moved_to_another_work(self):
        contract = Contract("D-9", "Тест", money("150.00"), date(2026, 1, 1))
        first = Work("W-1", "D-9", "Первая", money("100.00"), "done", date(2026, 2, 1), None)
        second = Work("W-2", "D-9", "Вторая", money("50.00"), "done", date(2026, 2, 15), None)
        payment = Payment(
            "P-1",
            "D-9",
            "W-1",
            "сверх цены",
            money("130.00"),
            "paid",
            date(2026, 2, 2),
            date(2026, 2, 2),
            None,
        )
        report = build_report(Data((contract,), (first, second), (payment,)), date(2026, 3, 1))
        item = report.contracts[0]
        lines = {line.work.work_id: line for line in item.works}
        self.assertEqual(lines["W-1"].paid, money("130.00"))
        self.assertEqual(lines["W-1"].remaining, money("-30.00"))
        self.assertEqual(lines["W-2"].paid, ZERO)
        self.assertEqual(lines["W-2"].remaining, money("50.00"))
        self.assertEqual(item.unallocated, ZERO)
        self.assertEqual(item.remaining, money("20.00"))

    def test_linked_payment_on_cancelled_work_is_not_reallocated(self):
        contract = Contract("D-9", "Тест", money("100.00"), date(2026, 1, 1))
        cancelled = Work("W-1", "D-9", "Отмена", money("40.00"), "cancelled", None, date(2026, 3, 1))
        live = Work("W-2", "D-9", "Живая", money("60.00"), "done", date(2026, 2, 1), None)
        payment = Payment(
            "P-1",
            "D-9",
            "W-1",
            "по отменённой",
            money("40.00"),
            "paid",
            date(2026, 2, 2),
            date(2026, 2, 2),
            None,
        )
        # Сумма цен 100 совпадает с договором. На 01.04 отмена уже случилась,
        # живая работа принята раньше платежа и должна была бы забрать аванс,
        # но этот платёж не аванс: он привязан к W-1.
        report = build_report(
            Data((contract,), (cancelled, live), (payment,)),
            date(2026, 4, 1),
        )
        item = report.contracts[0]
        lines = {line.work.work_id: line for line in item.works}
        self.assertEqual(lines["W-1"].paid, money("40.00"))
        self.assertEqual(lines["W-1"].remaining, money("-40.00"))
        self.assertEqual(lines["W-2"].paid, ZERO)
        self.assertEqual(item.unallocated, ZERO)
        self.assertEqual(item.obligation, money("60.00"))
        self.assertEqual(item.remaining, money("20.00"))


def _read_csv(path: Path) -> list[list[str]]:
    raw = path.read_bytes()
    if not raw.startswith(b"\xef\xbb\xbf"):
        raise AssertionError(f"нет BOM: {path.name}")
    text = raw.decode("utf-8-sig")
    return list(csv.reader(io.StringIO(text), delimiter=";"))


def _csv_money(text: str) -> Decimal:
    return D(text.replace(",", "."))


def _col(rows: list[dict[str, str]], name: str) -> Decimal:
    return sum((_csv_money(row[name]) for row in rows), ZERO)


if __name__ == "__main__":
    unittest.main()
