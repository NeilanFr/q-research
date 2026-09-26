"""A paper-account policy and optional read-only IBKR connection.

Research never imports ibapi. No order transport is implemented here: even a
passing policy check cannot submit, cancel, or modify an order.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import math
from threading import Event, Thread
from time import monotonic
from typing import Iterable, NoReturn


class BrokerSafetyError(RuntimeError):
    """The account is not verified, the session is ambiguous, or execution is off."""


@dataclass(frozen=True)
class PaperOnlyPolicy:
    """Allowlist entries must be independently verified paper-account IDs.

    Neither a port nor an account prefix constitutes verification. The allowlist
    is an operator assertion, not something inferred from broker observations.
    This first adapter deliberately permits only single-account sessions.
    """

    expected_account: str = ""
    paper_account_allowlist: frozenset[str] = frozenset()
    execution_enabled: bool = False

    def __post_init__(self) -> None:
        if type(self.execution_enabled) is not bool:
            raise ValueError("execution_enabled must be a boolean, not a string or integer")
        if not isinstance(self.paper_account_allowlist, frozenset):
            raise ValueError("paper_account_allowlist must be an immutable frozenset")
        for account in (self.expected_account, *self.paper_account_allowlist):
            if not isinstance(account, str) or (account and not account.isalnum()):
                raise ValueError("Use exact alphanumeric account IDs; no whitespace or aliases")
        if "" in self.paper_account_allowlist:
            raise ValueError("Empty allowlist entries are invalid")

    def verify_managed_accounts(self, managed_accounts: Iterable[str]) -> str:
        """Check a current broker response against independent configuration."""
        if not self.expected_account or self.expected_account not in self.paper_account_allowlist:
            raise BrokerSafetyError("Configure an independently verified paper account allowlist")
        if isinstance(managed_accounts, (str, bytes)):
            raise BrokerSafetyError("Pass parsed broker account IDs, not a raw string")
        accounts = tuple(managed_accounts)
        if accounts != (self.expected_account,):
            raise BrokerSafetyError("Broker must report exactly the configured paper account")
        return self.expected_account

    def authorize_order_account(self, order_account: str, managed_accounts: Iterable[str]) -> str:
        """Necessary policy check for a future transport; not a submission token.

        A future transport must apply this to its own current authenticated
        connection immediately before transmission and after every reconnect.
        """
        if self.execution_enabled is not True:
            raise BrokerSafetyError("Execution is disabled")
        account = self.verify_managed_accounts(managed_accounts)
        if order_account != account:
            raise BrokerSafetyError("Every order must explicitly target the verified paper account")
        return account


def submit_order(*args: object, **kwargs: object) -> NoReturn:
    """No transport exists in this milestone, regardless of configuration."""
    raise BrokerSafetyError("Order transmission is not implemented; this adapter is read-only")


def read_only_snapshot(
    policy: PaperOnlyPolicy,
    *,
    host: str = "127.0.0.1",
    port: int = 7497,
    client_id: int = 71,
    timeout: float = 10.0,
) -> dict:
    """Fetch managed accounts and a complete positions snapshot, then disconnect.

    Install ibapi from IBKR's official download first. This function requests no
    orders, executions, market-data subscriptions, or account changes. A probe
    can read the verified account with execution_enabled=False.
    """
    # Validate before connecting, including before importing the optional SDK.
    policy.verify_managed_accounts((policy.expected_account,))
    if host not in {"127.0.0.1", "::1", "localhost"}:
        raise ValueError("This initial adapter accepts loopback connections only")
    if type(port) is not int or not 1 <= port <= 65535:
        raise ValueError("port must be an integer between 1 and 65535")
    if type(client_id) is not int or client_id <= 0:
        raise ValueError("Use a positive, nonzero client ID, separate from manual orders")
    if not math.isfinite(timeout) or not 0 < timeout <= 60:
        raise ValueError("timeout must be between zero and 60 seconds")
    try:
        from ibapi.client import EClient
        from ibapi.wrapper import EWrapper
    except ImportError as exc:
        raise RuntimeError("Official ibapi is not installed; see docs/IBKR.md") from exc

    class Reader(EWrapper, EClient):
        def __init__(self) -> None:
            EWrapper.__init__(self)
            EClient.__init__(self, self)
            self.ready = Event()
            self.accounts_received = Event()
            self.positions_received = Event()
            self.accounts: tuple[str, ...] = ()
            self.positions: dict[tuple[str, int], dict] = {}
            self.messages: list[str] = []

        def nextValidId(self, orderId):
            # Used only as IBKR's connection-ready callback, never as an order ID.
            self.ready.set()

        def managedAccounts(self, accountsList):
            self.accounts = tuple(x.strip() for x in accountsList.split(",") if x.strip())
            self.accounts_received.set()

        def position(self, account, contract, position, avgCost):
            self.positions[(account, contract.conId)] = {
                "account": account,
                "con_id": contract.conId,
                "symbol": contract.symbol,
                "security_type": contract.secType,
                "currency": contract.currency,
                "quantity": str(position),
                "average_cost": avgCost,
            }

        def positionEnd(self):
            self.positions_received.set()

        def error(self, *args, **kwargs):
            # IBKR added an error timestamp to recent SDKs; accept both callback
            # signatures. Preserve diagnostics without interpreting all as fatal.
            self.messages.append(str((args, kwargs)))

        def placeOrder(self, *args, **kwargs):
            submit_order()

    reader = Reader()
    worker = None
    deadline = monotonic() + timeout

    def await_callback(event: Event, label: str) -> None:
        if not event.wait(max(0.0, deadline - monotonic())):
            raise TimeoutError(f"IBKR did not complete {label}: {reader.messages[-3:]}")
        if not reader.isConnected():
            raise BrokerSafetyError("Broker disconnected before snapshot completed")

    try:
        reader.connect(host, port, clientId=client_id)
        worker = Thread(target=reader.run, daemon=True)
        worker.start()
        await_callback(reader.ready, "connection handshake")
        reader.reqManagedAccts()
        await_callback(reader.accounts_received, "managed accounts")
        policy.verify_managed_accounts(reader.accounts)
        reader.reqPositions()
        await_callback(reader.positions_received, "positions")
        reader.cancelPositions()
        policy.verify_managed_accounts(reader.accounts)
        positions = list(reader.positions.values())
        if any(p["account"] != policy.expected_account for p in positions):
            raise BrokerSafetyError("Position callback contained an unexpected account")
        return {
            "observed_at_utc": datetime.now(timezone.utc).isoformat(),
            "account": policy.expected_account,
            "managed_accounts": list(reader.accounts),
            "positions": positions,
            "messages": reader.messages.copy(),
            "execution_enabled": False,
            "transport": "read_only",
            "paper_verification": "broker ID matches independently configured allowlist",
        }
    finally:
        reader.disconnect()
        if worker is not None:
            worker.join(timeout=1.0)
