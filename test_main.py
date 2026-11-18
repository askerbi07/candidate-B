"""Базовые тесты отчёта: контрольные цифры, чистота build_contract, ошибки ввода."""

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from main import (
    Contract,
    DataError,
    Payment,
    Work,
    build_contract,
    load_data,
)

DATA_DIR = Path(__file__).resolve().parent / "data"
REPORT_DATE = date(2026, 11, 10)


@pytest.fixture
def dataset():
    return load_data(DATA_DIR)


def contract_by_id(contracts, contract_id):
    return next(c for c in contracts if c.id == contract_id)


def test_control_totals_d1_and_d2(dataset):
    contracts, works, payments = dataset

    d1 = contract_by_id(contracts, "D-1")
    result_d1 = build_contract(
        d1,
        [w for w in works if w.contract_id == "D-1"],
        [p for p in payments if p.contract_id == "D-1"],
        REPORT_DATE,
    )
    assert result_d1["paid_total"] == Decimal("315000")
    assert result_d1["remaining_total"] == Decimal("185000")
    assert result_d1["unallocated"] == Decimal("15000")

    d2 = contract_by_id(contracts, "D-2")
    result_d2 = build_contract(
        d2,
        [w for w in works if w.contract_id == "D-2"],
        [p for p in payments if p.contract_id == "D-2"],
        REPORT_DATE,
    )
    assert result_d2["paid_total"] == Decimal("75000")
    assert result_d2["remaining_total"] == Decimal("15000")
    assert result_d2["unallocated"] == Decimal("25000")


def test_build_contract_does_not_mutate_works(dataset):
    contracts, works, payments = dataset
    d1 = contract_by_id(contracts, "D-1")
    d1_works = [w for w in works if w.contract_id == "D-1"]
    d1_payments = [p for p in payments if p.contract_id == "D-1"]

    first = build_contract(d1, d1_works, d1_payments, REPORT_DATE)
    second = build_contract(d1, d1_works, d1_payments, REPORT_DATE)

    assert all(w.advance_alloc == Decimal("0") for w in d1_works)
    assert first["advance"] == second["advance"]
    assert first["paid_total"] == second["paid_total"]
    assert first["remaining_total"] == second["remaining_total"]


def test_direct_payment_for_cancelled_work_is_shown_separately():
    contract = Contract("D-T", "Тестовый подрядчик", Decimal("1000.00"), date(2026, 1, 1))
    cancelled = Work(
        "W-1", "D-T", "Отменённая работа", Decimal("600.00"), "cancelled",
        None, date(2026, 2, 1),
    )
    planned = Work("W-2", "D-T", "Плановая работа", Decimal("400.00"), "planned", None, None)
    direct = Payment(
        "P-1", "D-T", "W-1", "Прямой платёж", Decimal("250.00"), "paid",
        date(2026, 2, 2), date(2026, 2, 2), None,
    )

    result = build_contract(contract, [cancelled, planned], [direct], date(2026, 3, 1))

    assert "Списано по отмененной работе" in result["rows"]
    assert "250" in result["rows"]
    assert result["cancelled_direct"] == Decimal("250.00")
    assert result["paid_total"] == Decimal("250.00")
    assert result["remaining_total"] == Decimal("400.00")
    assert cancelled.advance_alloc == Decimal("0")


def test_load_data_reports_missing_files_and_columns(tmp_path):
    with pytest.raises(DataError, match="не найден файл данных"):
        load_data(tmp_path)

    (tmp_path / "contracts.csv").write_text(
        "contract_id,contractor,signed_at\nD-1,Тест,2026-01-01\n",
        encoding="utf-8",
    )
    with pytest.raises(DataError, match="нет обязательной колонки 'amount'"):
        load_data(tmp_path)
