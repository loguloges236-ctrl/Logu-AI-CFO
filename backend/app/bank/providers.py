"""Read-only bank provider interface.

The interface has exactly two read methods. There is no method for transfers, payments, beneficiaries, settings,
passwords or transaction initiation, and a test asserts that no provider exposes a write-like name.

No live bank is connected in this codebase. `UnavailableBankProvider` refuses every call. `FixtureBankProvider` is a
test adapter that serves data the test supplies; it is never presented as a real bank.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

READ_ONLY_METHODS: frozenset[str] = frozenset({"list_accounts", "list_transactions"})
_FORBIDDEN_WORDS = ("transfer", "pay", "beneficiar", "create", "update", "delete", "set", "initiate", "password", "send")


class BankProviderUnavailable(RuntimeError):
    """Raised when no bank provider is connected."""


@dataclass(frozen=True)
class ProviderAccount:
    external_ref: str
    display_name: str
    currency: str
    balance: Decimal
    balance_as_of: date


@dataclass(frozen=True)
class ProviderTransaction:
    external_id: str
    txn_date: date
    amount: Decimal  # signed: positive = credit into the account, negative = debit out of it
    description: str
    balance_after: Decimal | None


class BankDataProvider(ABC):
    name: str

    @abstractmethod
    def list_accounts(self) -> list[ProviderAccount]:
        """Account balances as the provider reports them."""

    @abstractmethod
    def list_transactions(self, external_ref: str, since: date, until: date) -> list[ProviderTransaction]:
        """Transactions in [since, until]. Read only."""


class UnavailableBankProvider(BankDataProvider):
    name = "unavailable"

    def list_accounts(self) -> list[ProviderAccount]:
        raise BankProviderUnavailable("No bank provider is connected.")

    def list_transactions(self, external_ref: str, since: date, until: date) -> list[ProviderTransaction]:
        raise BankProviderUnavailable("No bank provider is connected.")


class FixtureBankProvider(BankDataProvider):
    """Test adapter. Serves exactly the accounts and transactions it was constructed with."""

    name = "fixture-test"

    def __init__(self, accounts: list[ProviderAccount], transactions: dict[str, list[ProviderTransaction]]) -> None:
        self._accounts = list(accounts)
        self._transactions = {ref: list(rows) for ref, rows in transactions.items()}

    def list_accounts(self) -> list[ProviderAccount]:
        return list(self._accounts)

    def list_transactions(self, external_ref: str, since: date, until: date) -> list[ProviderTransaction]:
        rows = self._transactions.get(external_ref, [])
        return sorted((r for r in rows if since <= r.txn_date <= until), key=lambda r: (r.txn_date, r.external_id))


def provider_method_names(provider_class: type[BankDataProvider]) -> set[str]:
    return {name for name in dir(provider_class) if not name.startswith("_") and callable(getattr(provider_class, name))}


def write_like(name: str) -> bool:
    lowered = name.lower()
    return any(word in lowered for word in _FORBIDDEN_WORDS)
