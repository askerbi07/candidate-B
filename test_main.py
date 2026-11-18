"""Базовые тесты отчёта: контрольные цифры, чистота build_contract, ошибки ввода."""

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from models import Contract, DataError, Payment, Work
from services import (
    build_contract,
    build_json_report,
    fmt_money,
    load_data,
    payment_state,
    work_state,
)

DATA_DIR = Path(__file__).resolve().parent / "data"
REPORT_DATE = date(2026, 11, 10)


@pytest.fixture
def dataset() -> tuple[list[Contract], list[Work], list[Payment]]:
    return load_data(DATA_DIR)


def contract_by_id(contracts: list[Contract], contract_id: str) -> Contract:
    return next(c for c in contracts if c.id == contract_id)


def test_control_totals_d1_and_d2(dataset: tuple[list[Contract], list[Work], list[Payment]]) -> None:
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


def test_build_contract_does_not_mutate_works(
    dataset: tuple[list[Contract], list[Work], list[Payment]],
) -> None:
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


def test_direct_payment_for_cancelled_work_is_shown_separately() -> None:
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


def test_load_data_reports_missing_files_and_columns(tmp_path: Path) -> None:
    with pytest.raises(DataError, match="не найден файл данных"):
        load_data(tmp_path)

    (tmp_path / "contracts.csv").write_text(
        "contract_id,contractor,signed_at\nD-1,Тест,2026-01-01\n",
        encoding="utf-8",
    )
    with pytest.raises(DataError, match="нет обязательной колонки 'amount'"):
        load_data(tmp_path)


def test_fifo_order_and_price_cap() -> None:
    """DECISIONS.md п. 3: аванс расходуется FIFO по дате выполнения, лимит — цена минус прямые платежи."""
    contract = Contract("D-F", "Подрядчик", Decimal("1000"), date(2026, 1, 1))
    w_first = Work("W-A", "D-F", "Ранняя работа", Decimal("100.00"), "done", date(2026, 5, 1), None)
    w_later = Work("W-B", "D-F", "Поздняя работа", Decimal("100.00"), "done", date(2026, 5, 2), None)
    direct = Payment("P-D", "D-F", "W-B", "Прямой платёж", Decimal("40.00"), "paid",
                     date(2026, 5, 3), date(2026, 5, 3), None)
    pool_pay = Payment("P-P", "D-F", "", "Аванс", Decimal("120.00"), "paid",
                       date(2026, 4, 1), date(2026, 4, 1), None)

    r = build_contract(contract, [w_first, w_later], [direct, pool_pay], date(2026, 5, 31))
    assert r["advance"]["W-A"] == Decimal("100.00")  # FIFO: ранняя работа закрыта первой
    assert r["advance"]["W-B"] == Decimal("20.00")   # остаток пула 20 < лимита (100 - 40) = 60
    assert r["unallocated"] == Decimal("0")


def test_refunded_payment_excluded_by_date() -> None:
    """DECISIONS.md п. 2: refunded до отчётной даты не считается, после — считается."""
    contract = Contract("D-R", "Подрядчик", Decimal("100"), date(2026, 1, 1))
    work = Work("W-1", "D-R", "Работа", Decimal("50.00"), "done", date(2026, 3, 1), None)
    refunded_before = Payment("P-1", "D-R", "", "Аванс", Decimal("30.00"), "refunded",
                              date(2026, 2, 1), date(2026, 2, 5), date(2026, 2, 10))
    refunded_after = Payment("P-2", "D-R", "", "Аванс", Decimal("30.00"), "refunded",
                             date(2026, 2, 1), date(2026, 2, 5), date(2026, 4, 1))

    r = build_contract(contract, [work], [refunded_before, refunded_after], date(2026, 3, 15))
    assert payment_state(refunded_before, date(2026, 3, 15)) == "refunded"
    assert payment_state(refunded_after, date(2026, 3, 15)) == "counted"
    assert r["advance"]["W-1"] == Decimal("30.00")   # в пул попал только P-2
    assert r["unallocated"] == Decimal("0")


def test_queued_and_after_date_payments_not_counted() -> None:
    """DECISIONS.md п. 1 и 2: queued (paid_at пуст) и оплаченные после даты не входят в расчёт."""
    contract = Contract("D-Q", "Подрядчик", Decimal("100"), date(2026, 1, 1))
    work = Work("W-1", "D-Q", "Работа", Decimal("50.00"), "done", date(2026, 3, 1), None)
    queued = Payment("P-1", "D-Q", "", "Аванс", Decimal("40.00"), "queued",
                     date(2026, 3, 1), None, None)
    after = Payment("P-2", "D-Q", "", "Аванс", Decimal("40.00"), "paid",
                    date(2026, 3, 1), date(2026, 3, 20), None)

    r = build_contract(contract, [work], [queued, after], date(2026, 3, 15))
    assert payment_state(queued, date(2026, 3, 15)) == "queued"
    assert payment_state(after, date(2026, 3, 15)) == "after_date"
    # ни один платёж не попал в пул: распределение по всем работам нулевое
    assert all(v == Decimal("0") for v in r["advance"].values())
    assert r["unallocated"] == Decimal("0")
    assert r["paid_total"] == Decimal("0")


def test_work_completed_after_report_date_not_paid() -> None:
    """DECISIONS.md п. 6 (W-23): done по статусу, но выполнена после даты — аванс не распределяется."""
    contract = Contract("D-W", "Подрядчик", Decimal("100"), date(2026, 1, 1))
    work = Work("W-1", "D-W", "Работа", Decimal("50.00"), "done", date(2026, 3, 20), None)
    pool_pay = Payment("P-1", "D-W", "", "Аванс", Decimal("40.00"), "paid",
                       date(2026, 2, 1), date(2026, 2, 5), None)

    r = build_contract(contract, [work], [pool_pay], date(2026, 3, 15))
    assert work_state(work, date(2026, 3, 15)) == "after_date"
    assert r["advance"] == {}
    assert r["unallocated"] == Decimal("40.00")      # аванс висит нераспределённым
    assert r["paid_total"] == Decimal("40.00")
    assert r["remaining_total"] == Decimal("10.00")  # 50 - 40


def test_cancelled_direct_included_in_paid_total() -> None:
    """DECISIONS.md п. 4: списание по отменённой работе входит в paid_total договора."""
    contract = Contract("D-C", "Подрядчик", Decimal("100"), date(2026, 1, 1))
    work = Work("W-1", "D-C", "Работа", Decimal("50.00"), "cancelled", None, date(2026, 3, 1))
    direct = Payment("P-1", "D-C", "W-1", "Прямой платёж", Decimal("30.00"), "paid",
                     date(2026, 2, 1), date(2026, 2, 5), None)

    r = build_contract(contract, [work], [direct], date(2026, 3, 15))
    assert r["cancelled_direct"] == Decimal("30.00")
    assert r["paid_total"] == Decimal("30.00")       # работа не оплачивается, но списание учтено
    assert r["remaining_total"] == Decimal("0")


def test_json_report_money_as_strings_and_after_date_items(
    dataset: tuple[list[Contract], list[Work], list[Payment]],
) -> None:
    """DECISIONS.md п. 5 и справочный блок: деньги строками с копейками, P-8/W-23 после даты."""
    contracts, works, payments = dataset
    dump = build_json_report(contracts, works, payments, date(2026, 11, 10))

    assert dump["contracts"]["D-1"]["paid_total"] == "315000.00"
    assert dump["contracts"]["D-1"]["remaining_total"] == "185000.00"
    assert dump["contracts"]["D-2"]["paid_total"] == "75000.00"
    assert dump["contracts"]["D-2"]["remaining_total"] == "15000.00"

    after = dump["after_report_date"]
    assert [p["id"] for p in after["payments"]] == ["P-8"]
    assert [w["id"] for w in after["works"]] == ["W-23"]


def test_fmt_money_rounds_half_up() -> None:
    """DECISIONS.md п. 5: ROUND_HALF_UP при выводе, целые суммы — без копеек."""
    assert fmt_money(Decimal("10.005")) == "10.01"
    assert fmt_money(Decimal("600000")) == "600 000"
