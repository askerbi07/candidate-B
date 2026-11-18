"""CLI: парсинг аргументов и вывод отчёта в консоль или JSON."""

import argparse
import json
import sys
from datetime import date
from pathlib import Path

from models import DataError, Payment, Work
from services import (
    build_contract,
    build_json_report,
    fmt_money,
    load_data,
    payment_lines,
    payment_state,
    work_state,
)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(description="Отчёт по договорам: выплачено/осталось по работам")
    ap.add_argument("--date", default="2026-11-10",
                    help="отчётная дата YYYY-MM-DD (по умолчанию 2026-11-10)")
    ap.add_argument("--data-dir", default=None,
                    help="папка с CSV (по умолчанию data/ рядом со скриптом)")
    ap.add_argument("--json", action="store_true", help="вывести отчёт в JSON")
    return ap.parse_args(argv)


def main() -> None:
    args = parse_args()

    try:
        report_date = date.fromisoformat(args.date)
    except ValueError:
        print(f"Некорректная дата '{args.date}', ожидается формат YYYY-MM-DD.", file=sys.stderr)
        sys.exit(2)

    data_dir = Path(args.data_dir) if args.data_dir else Path(__file__).resolve().parent / "data"
    try:
        contracts, works, payments = load_data(data_dir)
    except DataError as exc:
        print(f"Ошибка данных: {exc}", file=sys.stderr)
        sys.exit(1)

    if args.json:
        print(json.dumps(build_json_report(contracts, works, payments, report_date),
                         ensure_ascii=False, indent=2))
        return

    print(f"Отчёт на {report_date.isoformat()}\n{'=' * 60}")
    for c in sorted(contracts, key=lambda c: c.id):
        cw = [w for w in works if w.contract_id == c.id]
        cp = [p for p in payments if p.contract_id == c.id]
        r = build_contract(c, cw, cp, report_date)
        signed = f", подписан {c.signed_at.isoformat()}" if c.signed_at else ""
        print(f"\n{c.id} — {c.contractor}, сумма договора {fmt_money(c.amount)} руб.{signed}")
        print(r["rows"])
        print("\nПлатежи:")
        for line in payment_lines(cp, report_date):
            print(line)
        if r["cancelled_amount"]:
            print(f"\nОтменено работ на {fmt_money(r['cancelled_amount'])} руб. без допсоглашения "
                  f"— требует допсоглашения.")

    after_payments = [p for p in payments if payment_state(p, report_date) == "after_date"]
    after_works: list[Work] = []
    for c in contracts:
        cw = [w for w in works if w.contract_id == c.id]
        after_works.extend(w for w in cw if work_state(w, report_date) == "after_date")

    if after_payments or after_works:
        print(f"\n{'=' * 60}\nСправочно, после отчётной даты ({report_date.isoformat()}):")
        for p in after_payments:
            assert p.paid_at is not None  # инвариант: состояние "after_date" требует paid_at
            print(f"  Платёж {p.id} по {p.contract_id}: {fmt_money(p.amount)} руб., "
                  f"оплачен {p.paid_at.isoformat()} — в расчёт не включён.")
        for w in after_works:
            assert w.completed_at is not None  # инвариант: состояние "after_date" требует completed_at
            print(f"  Работа {w.id} ({w.title}): выполнена {w.completed_at.isoformat()}, "
                  f"{fmt_money(w.price)} руб. — на отчётную дату ещё не выполнена.")
