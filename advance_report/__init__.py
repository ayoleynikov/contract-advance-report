"""Отчёт: сколько выплачено и сколько осталось по работам и договорам на дату."""

from advance_report.allocate import build_report
from advance_report.load import load_data

__all__ = ["build_report", "load_data"]
