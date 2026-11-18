"""Модели домена: договоры, работы, платежи и ошибка входных данных."""

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal


class DataError(Exception):
    """Ошибка входных данных с понятным пользователю сообщением."""


@dataclass
class Contract:
    id: str
    contractor: str
    amount: Decimal
    signed_at: date | None


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
    created_at: date | None
    paid_at: date | None
    refunded_at: date | None
