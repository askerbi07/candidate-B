#!/usr/bin/env python3
"""Отчёт по авансам и платежам по договорам на заданную дату.

Читает data/contracts.csv, data/works.csv, data/payments.csv.
Решения — в DECISIONS.md: даты (paid_at/completed_at/refunded_at) — источник
истины, деньги в decimal.Decimal, ROUND_HALF_UP только при выводе,
FIFO-распределение аванса по выполненным работам.

Таблицы рисуются стандартной библиотекой (tabulate не нужна).
"""

import argparse
import csv
import json
import sys
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path


class DataError(Exception):
    """Ошибка входных данных с понятным пользователю сообщением."""


def parse_date(value):
    value = (value or "").strip()
    return date.fromisoformat(value) if value else None


@dataclass
class Contract:
    id: str
    contractor: str
    amount: Decimal
    signed_at: date


@dataclass
class Work:
    id: str
    contract_id: str
    title: str
    price: Decimal
    status: str
    completed_at: date | None
    cancelled_at: date | None
    advance_alloc: Decimal = field(default_factory=lambda: Decimal("0"))


@dataclass
class Payment:
    id: str
    contract_id: str
    work_id: str
    purpose: str
    amount: Decimal
    status: str
    created_at: date
    paid_at: date | None
    refunded_at: date | None


def load_data(data_dir):
    data_dir = Path(data_dir)
    if not data_dir.is_dir():
        raise DataError(
            f"папка с данными не найдена: {data_dir}. "
            f"Укажите существующую папку через --data-dir.")

    def read(name, make):
        path = data_dir / name
        try:
            with open(path, newline="", encoding="utf-8") as f:
                rows = list(csv.DictReader(f))
        except FileNotFoundError:
            raise DataError(
                f"не найден файл данных: {path}. Проверьте --data-dir.") from None
        except OSError as exc:
            raise DataError(f"не удалось прочитать файл {path}: {exc}") from exc

        result = []
        for line_no, row in enumerate(rows, start=2):
            if any(value is None for value in row.values()):
                raise DataError(
                    f"{path.name}, строка {line_no}: не хватает значений — "
                    f"проверьте число колонок.")
            try:
                result.append(make(row))
            except KeyError as exc:
                raise DataError(
                    f"{path.name}, строка {line_no}: нет обязательной колонки {exc}.") from exc
            except (ValueError, InvalidOperation) as exc:
                raise DataError(
                    f"{path.name}, строка {line_no}: некорректное значение — {exc}.") from exc
        return result

    contracts = read("contracts.csv", lambda r: Contract(
        r["contract_id"], r["contractor"], Decimal(r["amount"]), parse_date(r["signed_at"])))
    works = read("works.csv", lambda r: Work(
        r["work_id"], r["contract_id"], r["title"], Decimal(r["price"]), r["status"].strip(),
        parse_date(r["completed_at"]), parse_date(r["cancelled_at"])))
    payments = read("payments.csv", lambda r: Payment(
        r["payment_id"], r["contract_id"], (r["work_id"] or "").strip(), r["purpose"],
        Decimal(r["amount"]), r["status"].strip(), parse_date(r["created_at"]),
        parse_date(r["paid_at"]), parse_date(r["refunded_at"])))
    return contracts, works, payments


def payment_state(p, report_date):
    """P-2/P-4/P-8: считаем по датам, не по статусу (DECISIONS.md, п. 1 и 2)."""
    if p.paid_at is None:
        return "queued"
    if p.refunded_at is not None and p.refunded_at <= report_date:
        return "refunded"
    if p.paid_at > report_date:
        return "after_date"
    return "counted"


def work_state(w, report_date):
    """W-23: done по статусу, но выполнена после отчётной даты (DECISIONS.md, п. 6)."""
    if w.cancelled_at is not None and w.cancelled_at <= report_date:
        return "cancelled"
    if w.completed_at is not None and w.completed_at <= report_date:
        return "completed"
    if w.completed_at is not None and w.completed_at > report_date:
        return "after_date"
    return "active"


def fmt_money(value):
    q = value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    if q == q.to_integral_value():
        return f"{int(q):,}".replace(",", " ")
    return f"{q:,}".replace(",", " ")


def render_table(headers, rows):
    widths = [len(str(h)) for h in headers]
    for row in rows:
        for i, cell in enumerate(row):
            widths[i] = max(widths[i], len(str(cell)))

    def line(cells):
        return "| " + " | ".join(str(c).ljust(w) for c, w in zip(cells, widths)) + " |"

    out = [line(headers), "|" + "---|" * len(headers)]
    out.extend(line(row) for row in rows)
    return "\n".join(out)


def build_contract(contract, works, payments, report_date):
    counted = [p for p in payments if payment_state(p, report_date) == "counted"]

    direct = {}
    pool = Decimal("0")
    for p in counted:
        if p.work_id:
            direct[p.work_id] = direct.get(p.work_id, Decimal("0")) + p.amount
        else:
            pool += p.amount

    # FIFO по дате выполнения, лимит — цена минус прямые платежи (DECISIONS.md, п. 3).
    # Распределение храним локально: build_contract не мутирует Work.
    advance = {}
    for w in sorted((w for w in works if work_state(w, report_date) == "completed"),
                    key=lambda w: (w.completed_at, w.id)):
        cap = max(Decimal("0"), w.price - direct.get(w.id, Decimal("0")))
        alloc = min(pool, cap)
        advance[w.id] = alloc
        pool -= alloc

    unallocated = pool
    rows = []
    paid_sum = remaining_sum = cancelled_direct = Decimal("0")
    for w in sorted(works, key=lambda w: w.id):
        state = work_state(w, report_date)
        direct_paid = direct.get(w.id, Decimal("0"))
        if state == "cancelled":
            cancelled_direct += direct_paid
            label, paid, remaining = "cancelled", Decimal("0"), Decimal("0")
        elif state == "completed":
            label = w.status
            paid = direct_paid + advance.get(w.id, Decimal("0"))
            remaining = w.price - paid
        elif state == "after_date":
            label, paid, remaining = "после даты", direct_paid, w.price - direct_paid
        else:
            label, paid, remaining = w.status, direct_paid, w.price - direct_paid
        rows.append([w.id, label, fmt_money(w.price), fmt_money(paid),
                     "0 (не оплачивается)" if state == "cancelled" else fmt_money(remaining)])
        paid_sum += paid
        remaining_sum += remaining

    total_price = sum((w.price for w in works), Decimal("0"))
    cancelled_amount = sum((w.price for w in works
                            if work_state(w, report_date) == "cancelled"), Decimal("0"))
    active_amount = total_price - cancelled_amount
    paid_total = paid_sum + cancelled_direct + unallocated
    remaining_total = remaining_sum - unallocated

    rows.append(["**Итого по работам**", "", fmt_money(total_price),
                 fmt_money(paid_sum), fmt_money(remaining_sum)])
    if cancelled_direct > 0:
        rows.append(["**Списано по отмененной работе**", "", "",
                     fmt_money(cancelled_direct), "0"])
    if unallocated > 0:
        rows.append(["**Нераспр. аванс**", "", "", fmt_money(unallocated),
                     fmt_money(-unallocated)])
    price_cell = (f"{fmt_money(total_price)} (активных {fmt_money(active_amount)})"
                  if cancelled_amount else fmt_money(total_price))
    rows.append([f"**{contract.id} ИТОГО**", "", price_cell,
                 fmt_money(paid_total), fmt_money(remaining_total)])

    return {
        "rows": render_table(["Работа", "Статус", "Цена", "Выплачено", "Осталось"], rows),
        "advance": advance,
        "unallocated": unallocated,
        "cancelled_amount": cancelled_amount,
        "cancelled_direct": cancelled_direct,
        "paid_total": paid_total,
        "remaining_total": remaining_total,
    }


def payment_lines(payments, report_date):
    lines = []
    for p in sorted(payments, key=lambda p: p.id):
        state = payment_state(p, report_date)
        if state == "counted":
            note = f"прямой платёж на {p.work_id}" if p.work_id else "аванс по договору"
            lines.append(f"  {p.id} {p.purpose} — {fmt_money(p.amount)} руб. ({note}, оплачен {p.paid_at})")
        elif state == "refunded":
            lines.append(f"  {p.id} {p.purpose} — {fmt_money(p.amount)} руб. (возвращён {p.refunded_at})")
        elif state == "queued":
            lines.append(f"  {p.id} {p.purpose} — {fmt_money(p.amount)} руб. (в очереди, деньги не ушли)")
    return lines


def main():
    ap = argparse.ArgumentParser(description="Отчёт по договорам: выплачено/осталось по работам")
    ap.add_argument("--date", default="2026-11-10", help="отчётная дата YYYY-MM-DD (по умолчанию 2026-11-10)")
    ap.add_argument("--data-dir", default=None, help="папка с CSV (по умолчанию data/ рядом со скриптом)")
    ap.add_argument("--json", action="store_true", help="вывести отчёт в JSON (деньги — строками)")
    args = ap.parse_args()

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8")

    try:
        report_date = date.fromisoformat(args.date)
    except ValueError:
        ap.error(f"некорректная дата {args.date!r}: ожидается формат YYYY-MM-DD")

    data_dir = Path(args.data_dir) if args.data_dir else Path(__file__).resolve().parent / "data"
    try:
        contracts, works, payments = load_data(data_dir)
    except DataError as exc:
        sys.exit(f"Ошибка: {exc}")

    after_payments = [p for p in payments if payment_state(p, report_date) == "after_date"]
    after_works = []
    result = {}
    for c in sorted(contracts, key=lambda c: c.id):
        cw = [w for w in works if w.contract_id == c.id]
        cp = [p for p in payments if p.contract_id == c.id]
        r = build_contract(c, cw, cp, report_date)
        result[c.id] = (c, r)
        after_works.extend(w for w in cw if work_state(w, report_date) == "after_date")

    if args.json:
        dump = {"report_date": report_date.isoformat(), "contracts": {}, "after_report_date": {}}
        for cid, (c, r) in result.items():
            dump["contracts"][cid] = {
                "contractor": c.contractor,
                "amount": str(c.amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)),
                "paid_total": str(r["paid_total"].quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)),
                "remaining_total": str(r["remaining_total"].quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)),
                "unallocated_advance": str(r["unallocated"].quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)),
                "cancelled_amount": str(r["cancelled_amount"].quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)),
                "cancelled_direct": str(r["cancelled_direct"].quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)),
            }
        dump["after_report_date"] = {
            "payments": [{"id": p.id, "contract_id": p.contract_id, "amount": str(p.amount),
                          "paid_at": p.paid_at.isoformat()} for p in after_payments],
            "works": [{"id": w.id, "contract_id": w.contract_id, "price": str(w.price),
                       "completed_at": w.completed_at.isoformat()} for w in after_works],
        }
        print(json.dumps(dump, ensure_ascii=False, indent=2))
        return

    print(f"Отчёт на {report_date.isoformat()}\n{'=' * 60}")
    for cid, (c, r) in result.items():
        cp = [p for p in payments if p.contract_id == c.id]
        print(f"\n{cid} — {c.contractor}, сумма договора {fmt_money(c.amount)} руб. "
              f"(подписан {c.signed_at.isoformat()})")
        print(r["rows"])
        print("\nПлатежи:")
        for line in payment_lines(cp, report_date):
            print(line)
        if r["cancelled_amount"]:
            print(f"\nОтменено работ на {fmt_money(r['cancelled_amount'])} руб. без допсоглашения "
                  f"— требует допсоглашения.")

    if after_payments or after_works:
        print(f"\n{'=' * 60}\nСправочно, после отчётной даты ({report_date.isoformat()}):")
        for p in after_payments:
            print(f"  Платёж {p.id} по {p.contract_id}: {fmt_money(p.amount)} руб., "
                  f"оплачен {p.paid_at.isoformat()} — в расчёт не включён.")
        for w in after_works:
            print(f"  Работа {w.id} ({w.title}): выполнена {w.completed_at.isoformat()}, "
                  f"{fmt_money(w.price)} руб. — на отчётную дату ещё не выполнена.")


if __name__ == "__main__":
    main()
