"""Fail-closed PAPER planning, immutable evidence and crash-safe order ledger.

No SDK import, socket or order transport in this module. Dollars in research
forecasts are references; execution always sizes against broker-observed NAV.
"""
from __future__ import annotations

from contextlib import closing
from copy import deepcopy
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR
import json
import math
from pathlib import Path
import re
import sqlite3
from threading import RLock
from uuid import uuid4

import pandas as pd

from .broker import BrokerSafetyError
from .data import ROOT, calendar, digest, now_utc, save_json
from .forward import forecast_times


def require(condition, message):
    if not condition:
        raise BrokerSafetyError(message)


def number(value):
    try:
        result = float(value)
    except (ValueError, TypeError) as exc:
        raise BrokerSafetyError("Missing/non-numeric broker value") from exc
    require(math.isfinite(result) and abs(result) < 1e100, "Unset/non-finite broker value")
    return result


def stamp(value):
    result = pd.Timestamp(value)
    require(result.tzinfo is not None and not pd.isna(result), "Timezone-aware timestamp required")
    return result


def age(value, now, seconds, label):
    elapsed = (stamp(now) - stamp(value)).total_seconds()
    require(0 <= elapsed <= seconds, f"Stale/future {label}")


def canonical(value):
    return json.dumps(value, sort_keys=True, allow_nan=False, separators=(",", ":"))


def masked(account):
    return account[:2] + "***" + account[-2:] if account else "UNCONFIGURED"


def redacted(value):
    """Also redact IDs embedded in IB error strings, not just account fields."""
    return json.loads(re.sub(r'\b(?:DUT|DU|U)\d+\b', lambda m: masked(m[0]), json.dumps(value)))


def execution_policy():
    active = ROOT/"config/model_v1/paper_execution.json"
    policy = json.loads((active if active.exists() else ROOT / "config/paper_execution_v1.json").read_text())
    if active.exists():
        with closing(sqlite3.connect(ROOT/"state/research.sqlite")) as db:
            record=db.execute("SELECT execution_json FROM model_policies WHERE version=?",(policy["research_policy_version"],)).fetchone()
        require(record and json.loads(record[0])==policy,"Frozen execution envelope modified")
    return policy


def connection_config(path=None):
    path = path or ROOT / "config/private_paper.json"
    require(path.exists(), "Missing config/private_paper.json: exact independently verified paper account ID and configured TWS port required")
    cfg = json.loads(path.read_text())
    verify_account(cfg, [cfg.get("expected_account")])
    require(cfg.get("host") in {"127.0.0.1", "localhost", "::1"}, "Only local TWS/Gateway is supported")
    require(type(cfg.get("port")) is int and 0 < cfg["port"] < 65536, "Invalid API port")
    require(type(cfg.get("client_id")) is int and cfg["client_id"] > 0, "Positive unique nonzero client ID required")
    require(type(cfg.get("execution_enabled")) is bool, "execution_enabled must be an explicit boolean")
    return cfg


def verify_account(cfg, accounts, transmit=False):
    require(cfg.get("host") == "127.0.0.1" and type(cfg.get("port")) is int and cfg["port"] == 7497,
            "Only 127.0.0.1:7497 PAPER TWS is permitted; no fallback")
    require(cfg.get("mode") == "PAPER", "Execution mode must explicitly equal PAPER")
    expected = cfg.get("expected_account", "")
    # Narrowly supports individually verified DU paper accounts only. Prefix is
    # additional rejection, never a substitute for independent verification.
    require(isinstance(expected, str) and re.fullmatch(r"DUT?[0-9]+", expected), "Live/unsupported account identifier rejected")
    require(cfg.get("independently_verified_paper") is True, "Independent paper-account verification required")
    require(cfg.get("paper_account_allowlist") == [expected], "Exact single paper-account allowlist required")
    require(isinstance(accounts, (list, tuple)) and list(accounts) == [expected], "Connected account mismatch/ambiguity")
    if transmit:
        require(cfg.get("execution_enabled") is True, "Execution disabled")
    return expected


def load_forecast(batch_id=None, db_path=None):
    with closing(sqlite3.connect(db_path or ROOT / "state/research.sqlite")) as db:
        generic = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='model_forecasts'").fetchone()
        if generic and (batch_id is None or batch_id.startswith("model_")):
            from .competition import load_model_forecast
            return load_model_forecast(batch_id,db)
        row = db.execute("SELECT id,version,issued_at,entry_at,path FROM forecast_batches " +
                         ("WHERE id=?" if batch_id else "ORDER BY entry_at DESC LIMIT 1"),
                         (batch_id,) if batch_id else ()).fetchone()
        require(row is not None, "No frozen forecast")
        path = (ROOT / row[4] / "forecast.json").resolve()
        require(path.is_relative_to(ROOT / "state/forward"), "Unexpected forecast path")
        payload = path.read_bytes()
        recorded = db.execute("SELECT sha256 FROM forecast_integrity WHERE batch_id=?", (row[0],)).fetchone()
        require(recorded and digest(payload) == recorded[0], "Frozen forecast integrity failure")
        batch = json.loads(payload)
        require((batch["id"], batch["version"], batch["issued_at_utc"], batch["entry_at"]) == row[:4], "Forecast/ledger mismatch")
        policy_row = db.execute("SELECT policy_json FROM policies WHERE version=?", (row[1],)).fetchone()
        require(policy_row and json.loads(policy_row[0]) == batch["policy"], "Frozen policy/ledger mismatch")
        expected = {(r["model"], r["symbol"], r["target_weight"], r["role"]) for r in batch["position_intents"]}
        actual = set(db.execute("SELECT model,symbol,weight,role FROM position_intents WHERE batch_id=?", (row[0],)))
        require(expected == actual, "Intents/ledger mismatch")
    return batch


def validate_forecast(batch, policy, now, submission=False):
    require(batch["version"] == policy["research_policy_version"], "Unapproved research policy version")
    require(digest(json.dumps(batch["policy"], sort_keys=True).encode())[:20] == batch["version"], "Policy hash mismatch")
    require(batch["active_model"] == policy["active_model"], "Only the frozen active model may trade")
    if batch.get("schema") == "frozen_targets_v1":
        from .competition import model_times, verify_frozen_policy
        verify_frozen_policy(batch["policy"])
        times = model_times(batch["decision_date"],batch["issued_at_utc"],batch["policy"])
    else:
        require(batch["active_model"] == "equal_weight", "Unknown legacy model")
        times = forecast_times(batch["decision_date"], batch["issued_at_utc"])
    require(all(batch[k] == v for k, v in times.items()), "Invalid frozen forecast timing")
    require(stamp(batch["issued_at_utc"]) <= stamp(now) < stamp(batch["entry_at"]), "Stale/future forecast; no retroactive execution")
    protocol = json.loads((ROOT / "config/protocol.json").read_text())
    require(protocol["competition_first_session"] <= batch["entry_date"] <= protocol["competition_end_assumption"], "Outside competition dates")
    rows = [r for r in batch["position_intents"] if r["role"] == "active"]
    if batch.get("schema") == "frozen_targets_v1":
        require(len(rows)==len({r["symbol"] for r in rows}) and {r["symbol"] for r in rows}<=set(policy["symbols"]),"Unknown/duplicate active symbol")
        require(all(r["model"]==policy["active_model"] and 0<=number(r["target_weight"])<=policy["max_weight"]+1e-12 for r in rows),"Invalid target weights or shadow model")
        require(sum(number(r["target_weight"]) for r in rows)<=policy["max_gross"]+1e-12,"Target gross limit exceeded")
    else:
        require(len(rows) == len(policy["symbols"]) and {r["symbol"] for r in rows} == set(policy["symbols"]), "Unknown/duplicate/missing active symbol")
        require(all(r["model"] == policy["active_model"] and abs(number(r["target_weight"]) - 1/9) < 1e-12 for r in rows), "Unapproved target weights or shadow model")
    if submission:
        before = (stamp(batch["entry_at"]) - stamp(now)).total_seconds() / 60
        first, last = policy["submit_minutes_before_open"]
        require(last < before <= first, "Outside frozen pre-open order window; no catch-up")
    return {r["symbol"]: number(r["target_weight"]) for r in rows}


def validate_contract(symbol, candidates):
    require(len(candidates) == 1, f"Ambiguous/unresolved contract: {symbol}")
    c = candidates[0]
    require(c.get("symbol") == symbol and c.get("sec_type") == "STK" and c.get("currency") == "USD"
            and c.get("exchange") == "SMART" and bool(c.get("primary_exchange"))
            and type(c.get("con_id")) is int and c["con_id"] > 0, f"Unqualified contract: {symbol}")
    require(0 < number(c["min_tick"]) <= 0.01, "Unsupported price increment")
    require("LMT" in c.get("order_types", []), "Limit orders unsupported")
    return c


def validate_quote(q, policy, now):
    require(q.get("market_data_type") == 1, "Live quotes required; delayed/frozen quotes rejected")
    for side in ("bid", "ask"):
        require(number(q.get(side)) > 0, f"Invalid {side}")
        age(q.get(side + "_at"), now, policy["quote_max_age_seconds"], side + " quote")
    require(q["ask"] >= q["bid"], "Crossed quote")
    mid = (q["ask"] + q["bid"]) / 2
    require((q["ask"] - q["bid"]) / mid * 10000 <= policy["max_spread_bps"], "Spread exceeds limit")
    return mid


def usd_amounts(base, fx, policy, now):
    """CAD-per-USD ask: CAD / ask is conservative USD purchasing capacity."""
    require(isinstance(base, dict) and base.get("currency") == "CAD", "Missing CAD account amounts")
    require(isinstance(fx, dict), "Live USD/CAD FX quote unavailable")
    require((fx.get("symbol"), fx.get("currency"), fx.get("sec_type"), fx.get("exchange"))
            == ("USD", "CAD", "CASH", "IDEALPRO"), "FX direction must be CAD per USD on USD.CAD")
    validate_quote(fx, policy, now)
    require(.5 <= number(fx["bid"]) <= number(fx["ask"]) <= 3, "Nonsensical USD/CAD FX quote")
    return {name:number(base.get(name)) / number(fx["ask"])
            for name in ("nav", "cash", "available_funds", "buying_power", "total_cash_value")}


def validate_snapshot(snapshot, cfg, policy, now, allow_open=False):
    account = verify_account(cfg, snapshot["managed_accounts"])
    require(snapshot.get("complete") is True and snapshot.get("account") == account, "Incomplete account state")
    age(snapshot["observed_at"], now, policy["account_max_age_seconds"], "account state")
    require(snapshot.get("base_currency") in {"USD", "CAD"}, "Unsupported account base currency")
    if snapshot["base_currency"] == "CAD":
        require(snapshot.get("execution_currency") == "USD", "Missing USD normalization")
        amounts = usd_amounts(snapshot.get("base_amounts"), snapshot.get("fx_quote"), policy, now)
        require(all(math.isclose(number(snapshot.get(k)), v, rel_tol=1e-12, abs_tol=1e-8)
                    for k,v in amounts.items()), "CAD/USD normalization mismatch")
    require(snapshot.get("connection_healthy") is True, "Disconnected/reconnect ambiguity")
    for name in ("nav", "cash", "available_funds", "buying_power"):
        require(number(snapshot[name]) >= 0, f"Invalid account {name}")
    require(snapshot["nav"] > 0, "NAV must be positive")
    if not allow_open:
        require(not snapshot["open_orders"], "Existing open orders require reconciliation")
    require(all(o["account"] == account for o in snapshot["open_orders"]), "Open order account mismatch")
    require(all(e["account"] == account for e in snapshot["executions"]), "Execution account mismatch")
    positions = {}
    for pos in snapshot["positions"]:
        require(pos["account"] == account, "Position account mismatch")
        qty = number(pos["quantity"])
        if qty == 0:
            continue
        require(pos["symbol"] in policy["symbols"] and pos["sec_type"] == "STK" and pos["currency"] == "USD", "Unknown existing position; review required")
        require(qty >= 0 and qty.is_integer(), "Short/fractional holdings require review")
        require(pos["symbol"] not in positions, "Duplicate position contract")
        positions[pos["symbol"]] = int(qty)
    return positions


def rounded_limit(price, tick, side):
    # Buy rounds down and sell rounds up: never widen the price collar.
    rounding = ROUND_FLOOR if side == "BUY" else ROUND_CEILING
    return float((Decimal(str(price)) / Decimal(str(tick))).to_integral_value(rounding=rounding) * Decimal(str(tick)))


def fee_bound(quantity, price, policy):
    return max(1.0, quantity * .005, quantity * price * policy["cost_reserve_bps"] / 10000)


def build_plan(batch, snapshot, contracts, quotes, cfg, policy, now):
    weights = validate_forecast(batch, policy, now)
    holdings = validate_snapshot(snapshot, cfg, policy, now)
    weights = {s:w for s,w in weights.items() if w>0}
    weights.update({s:0.0 for s in holdings if s not in weights})
    nav = snapshot["nav"]
    cash = min(snapshot["cash"], snapshot["available_funds"], snapshot["buying_power"])
    budget = max(0, cash - nav * policy["cash_reserve_fraction"])
    targets, orders, mids = {}, [], {}
    for symbol in sorted(weights):
        c = validate_contract(symbol, [contracts[symbol]])
        q = quotes[symbol]
        mid = mids[symbol] = validate_quote(q, policy, now)
        held = holdings.get(symbol, 0)
        if held:
            pos = next(p for p in snapshot["positions"] if p["symbol"] == symbol and number(p["quantity"]) != 0)
            require(pos["con_id"] == c["con_id"], "Held/qualified contract mismatch")
        buy_limit = rounded_limit(q["ask"] * (1 + policy["limit_collar_bps"] / 10000), c["min_tick"], "BUY")
        desired = math.floor(nav * (1-policy["cash_reserve_fraction"]) * weights[symbol] / buy_limit)
        targets[symbol] = {"desired_quantity": desired, "current_quantity": held, "target_weight": weights[symbol], "reference_price": mid}
        delta = desired - held
        if not delta:
            continue
        side = "BUY" if delta > 0 else "SELL"
        limit = buy_limit if side == "BUY" else rounded_limit(q["bid"] * (1-policy["limit_collar_bps"]/10000), c["min_tick"], "SELL")
        quantity = abs(delta)
        if batch.get("schema") == "frozen_targets_v1":
            liquidity = number(batch["dollar_adv"][symbol])
            quantity = min(quantity,math.floor(liquidity*policy["max_trade_adv_fraction"]*.99/limit))
            if quantity == 0:
                continue
        orders.append({"symbol": symbol, "con_id": c["con_id"], "side": side, "quantity": quantity,
                       "order_type": "LMT", "tif": "OPG", "limit_price": limit,
                       "reference_price": mid, "target_weight": weights[symbol], "current_weight": held * mid / nav,
                       "reason": "daily approved target minus broker position", "strategy_version": batch["version"], "forecast_batch_id": batch["id"]})
    # Proportional reduction avoids allowing one alphabetically early buy to
    # consume all cash; sell proceeds are deliberately not available yet.
    buy_cost = sum(o["quantity"]*o["limit_price"]+fee_bound(o["quantity"], o["limit_price"], policy) for o in orders if o["side"] == "BUY")
    scale = min(1, budget / buy_cost) if buy_cost else 1
    kept = []
    for o in orders:
        if o["side"] == "BUY":
            o["quantity"] = math.floor(o["quantity"] * scale)
            if scale < 1:
                o["reason"] += "; buys reduced to existing cash, no assumed sale proceeds"
        if not o["quantity"]:
            continue
        symbol, qty = o["symbol"], o["quantity"]
        o["estimated_notional"] = qty * o["limit_price"]
        o["estimated_cost"] = fee_bound(qty, o["limit_price"], policy)
        o["resulting_quantity"] = holdings.get(symbol, 0) + (qty if o["side"] == "BUY" else -qty)
        o["resulting_estimated_weight"] = o["resulting_quantity"] * mids[symbol] / nav
        key = [batch["version"], batch["id"], snapshot["account"], symbol, batch["entry_at"]]
        o["key"] = digest(canonical(key).encode())
        o["order_ref"] = "QR-" + o["key"][:28]
        kept.append(o)
    plan = {"created_at": now, "account": snapshot["account"], "batch_id": batch["id"], "strategy_version": batch["version"],
            "execution_policy_sha256": digest(canonical(policy).encode()), "entry_at": batch["entry_at"],
            "decision_at": batch["issued_at_utc"], "initial_snapshot": snapshot, "contracts": contracts,
            "quotes": quotes, "targets": targets, "orders": sorted(kept, key=lambda o: o["side"] != "SELL")}
    plan = deepcopy(plan)
    plan["id"] = digest(canonical(plan).encode())
    validate_plan(plan, batch, snapshot, contracts, quotes, cfg, policy, now)
    return plan


def validate_plan(plan, batch, snapshot, contracts, quotes, cfg, policy, now, submission=False):
    if submission:
        require(policy.get("transmission_suspended") is False, "Execution policy suspended pending new competition model")
    weights = validate_forecast(batch, policy, now, submission)
    positions = validate_snapshot(snapshot, cfg, policy, now)
    weights = {s:w for s,w in weights.items() if w>0}
    weights.update({s:0.0 for s in positions if s not in weights})
    require(plan["account"] == snapshot["account"] and plan["batch_id"] == batch["id"] and plan["strategy_version"] == batch["version"], "Plan identity mismatch")
    require(plan["id"] == digest(canonical({k:v for k,v in plan.items() if k != "id"}).encode()), "Plan was modified")
    require(plan["execution_policy_sha256"] == digest(canonical(policy).encode()), "Execution policy changed")
    require(abs(snapshot["nav"] / plan["initial_snapshot"]["nav"] - 1) <= .005, "NAV changed materially; generate a new dry run")
    require(positions == {s:t["current_quantity"] for s,t in plan["targets"].items() if t["current_quantity"]}, "Positions changed since dry run")
    mids = {s:validate_quote(quotes[s], policy, now) for s in weights}
    result = dict(positions)
    buys = 0.0
    seen = set()
    for o in plan["orders"]:
        s = o["symbol"]
        require(s in weights and s not in seen, "Duplicate/unknown order symbol")
        seen.add(s)
        c = validate_contract(s, [contracts[s]])
        require(c == plan["contracts"][s] and o["con_id"] == c["con_id"], "Order contract mismatch")
        qty, price = o["quantity"], number(o["limit_price"])
        require(type(qty) is int and 0 < qty <= 10_000_000 and price > 0, "Invalid order quantity/price")
        require(o["side"] in {"BUY", "SELL"} and o["order_type"] == "LMT" and o["tif"] == "OPG", "Unsupported order parameters")
        require(Decimal(str(price)) % Decimal(str(c["min_tick"])) == 0, "Invalid price increment")
        require(abs(price/mids[s]-1)*10000 <= policy["limit_collar_bps"]+policy["max_spread_bps"], "Limit detached from current quote")
        require(qty*price <= snapshot["nav"] * 1.01, "Nonsensical order notional")
        if batch.get("schema") == "frozen_targets_v1":
            require(qty*price<=number(batch["dollar_adv"][s])*policy["max_trade_adv_fraction"],"Trade exceeds frozen ADV capacity")
        result[s] = result.get(s, 0) + (qty if o["side"] == "BUY" else -qty)
        require(result[s] == o["resulting_quantity"] and result[s] >= 0, "Order delta does not reconcile")
        desired = plan["targets"][s]["desired_quantity"]
        require(abs(result[s]-desired) <= abs(positions.get(s,0)-desired), "Order moves away from target")
        if o["side"] == "BUY":
            buys += qty*price + fee_bound(qty,price,policy)
            require(result[s]*max(price,mids[s])/snapshot["nav"] <= policy["max_weight"], "Position cap exceeded")
    require(buys <= min(snapshot["cash"], snapshot["available_funds"], snapshot["buying_power"])-snapshot["nav"]*policy["cash_reserve_fraction"]+1e-6 or buys == 0, "Insufficient cash/buying power including costs")
    # Ignore potential sale fills when checking funding. Existing concentration
    # may only be reduced; no unexplained drift is silently accepted.
    for s, qty in result.items():
        require(qty*mids[s]/snapshot["nav"] <= policy["max_weight"]+1e-6, "Resulting concentration outside cap")
    require(sum(qty*mids[s] for s,qty in result.items())/snapshot["nav"] <= policy["max_gross"]+1e-6, "Gross exposure exceeded")


class PaperLedger:
    """Private, synchronous WAL journal. Reservation precedes network writes.

    No automatic retries of a reserved batch, including after a crash before
    the wire call. Reconciliation records broker evidence without unreserving.
    """
    def __init__(self, path=None):
        self.path = path or ROOT / "state/private/paper.sqlite"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = RLock()
        self.db = sqlite3.connect(self.path, timeout=10, check_same_thread=False)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.executescript("""
          CREATE TABLE IF NOT EXISTS observations(id TEXT PRIMARY KEY,at TEXT,kind TEXT,payload TEXT);
          CREATE TABLE IF NOT EXISTS batches(key TEXT PRIMARY KEY,plan_id TEXT UNIQUE,payload TEXT);
          CREATE TABLE IF NOT EXISTS orders(key TEXT PRIMARY KEY, batch_key TEXT,client_id INTEGER,order_id INTEGER,order_ref TEXT UNIQUE,payload TEXT,UNIQUE(client_id,order_id));
          CREATE TABLE IF NOT EXISTS order_events(seq INTEGER PRIMARY KEY AUTOINCREMENT,at TEXT,kind TEXT,client_id INTEGER,order_id INTEGER,payload TEXT);
          CREATE TABLE IF NOT EXISTS executions(id TEXT PRIMARY KEY,at TEXT,payload TEXT);
          CREATE TABLE IF NOT EXISTS commissions(id TEXT PRIMARY KEY,at TEXT,payload TEXT);
        """)
        self.db.commit()

    def close(self):
        self.db.close()

    def observe(self, kind, payload):
        with self.lock, self.db:
            self.db.execute("INSERT INTO observations VALUES(?,?,?,?)", (uuid4().hex, now_utc(), kind, canonical(payload)))

    def event(self, kind, client_id, order_id, payload):
        with self.lock, self.db:
            self.db.execute("INSERT INTO order_events(at,kind,client_id,order_id,payload) VALUES(?,?,?,?,?)", (now_utc(),kind,client_id,order_id,canonical(payload)))

    def reserve(self, plan, client_id, first_order_id):
        key = digest(canonical([plan["strategy_version"],plan["batch_id"],plan["account"],plan["entry_at"]]).encode())
        with self.lock:
            try:
                self.db.execute("BEGIN IMMEDIATE")
                self.db.execute("INSERT INTO batches VALUES(?,?,?)", (key,plan["id"],canonical(plan)))
                for offset,o in enumerate(plan["orders"]):
                    self.db.execute("INSERT INTO orders VALUES(?,?,?,?,?,?)", (o["key"],key,client_id,first_order_id+offset,o["order_ref"],canonical(o)))
                    self.db.execute("INSERT INTO order_events(at,kind,client_id,order_id,payload) VALUES(?,?,?,?,?)",(now_utc(),"GENERATED",client_id,first_order_id+offset,canonical(o)))
                self.db.commit()
            except sqlite3.IntegrityError as exc:
                self.db.rollback()
                raise BrokerSafetyError("Duplicate/replayed batch or order; reconcile, never resend") from exc
            except BaseException:
                self.db.rollback()
                raise
        return key

    def once_submitting(self, client, order_id):
        with self.lock:
            try:
                self.db.execute("BEGIN IMMEDIATE")
                row = self.db.execute("SELECT payload FROM orders WHERE client_id=? AND order_id=?",(client,order_id)).fetchone()
                require(row is not None, "Unreserved order")
                sent = self.db.execute("SELECT 1 FROM order_events WHERE client_id=? AND order_id=? AND kind='SUBMITTING'",(client,order_id)).fetchone()
                require(not sent, "Repeated transmission rejected")
                self.db.execute("INSERT INTO order_events(at,kind,client_id,order_id,payload) VALUES(?,?,?,?,?)",(now_utc(),"SUBMITTING",client,order_id,row[0]))
                self.db.commit()
                return json.loads(row[0])
            except BaseException:
                self.db.rollback()
                raise

    def fill(self, value):
        # Keep corrections as broker evidence, but reconciliation below treats
        # only the highest correction suffix as effective for each execution.
        with self.lock, self.db:
            old = self.db.execute("SELECT payload FROM executions WHERE id=?",(value["execution_id"],)).fetchone()
            require(not old or json.loads(old[0]) == value, "Conflicting duplicate execution")
            self.db.execute("INSERT OR IGNORE INTO executions VALUES(?,?,?)",(value["execution_id"],now_utc(),canonical(value)))

    def commission(self, value):
        with self.lock, self.db:
            self.db.execute("INSERT OR REPLACE INTO commissions VALUES(?,?,?)",(value["execution_id"],now_utc(),canonical(value)))

    def effective_fills(self):
        with self.lock:
            rows = [json.loads(r[0]) for r in self.db.execute("SELECT payload FROM executions")]
        effective = {}
        for row in rows:
            key, sep, suffix = row["execution_id"].rpartition(".")
            key = key if sep and suffix.isdigit() else row["execution_id"]
            rank = int(suffix) if sep and suffix.isdigit() else 0
            if key not in effective or rank > effective[key][0]:
                effective[key] = (rank,row)
        return [v[1] for v in effective.values()]

    def reconcile(self, snapshot):
        self.observe("reconciliation_snapshot", snapshot)
        for fill in snapshot["executions"]:
            self.fill(fill)
        fills = self.effective_fills()
        with self.lock:
            rows = self.db.execute("SELECT client_id,order_id,payload FROM orders").fetchall()
            commissions = {r[0]:json.loads(r[1]) for r in self.db.execute("SELECT id,payload FROM commissions")}
        result = []
        for client,order_id,payload in rows:
            order = json.loads(payload)
            matched = [f for f in fills if f["account"] == snapshot["account"] and f["order_ref"] == order["order_ref"]]
            require(all(f["symbol"] == order["symbol"] and f["side"] == order["side"] for f in matched), "Execution/order mismatch")
            filled = sum(number(f["quantity"]) for f in matched)
            require(0 <= filled <= order["quantity"], "Broker execution quantity exceeds intent")
            with self.lock:
                events = [(r[0],json.loads(r[1])) for r in self.db.execute("SELECT kind,payload FROM order_events WHERE client_id=? AND order_id=? ORDER BY seq",(client,order_id))]
            open_order = next((o for o in snapshot["open_orders"] if o["order_ref"] == order["order_ref"]), None)
            completed = next((o for o in snapshot.get("completed_orders",[]) if o["order_ref"] == order["order_ref"]), None)
            terminal = next((v["status"] for k,v in reversed(events) if k == "STATUS" and v["status"] in {"Cancelled","ApiCancelled","Inactive"}), None)
            if completed and completed["status"] in {"Cancelled","ApiCancelled","Inactive"}:
                terminal = completed["status"]
            state = "FILLED" if filled == order["quantity"] else "PARTIALLY_FILLED" if filled else "ACKNOWLEDGED" if open_order else "UNKNOWN_RECONCILE"
            if not filled and not open_order and not any(k == "SUBMITTING" for k,v in events):
                state = "NOT_SUBMITTED"
            if terminal and filled < order["quantity"]:
                state = "REJECTED" if terminal == "Inactive" else "CANCELLED"
            result.append({"order":order,"client_id":client,"order_id":order_id,"state":state,"filled":filled,
                           "remaining":order["quantity"]-filled,"fills":matched,
                           "average_fill_price":sum(number(f["quantity"])*number(f["price"]) for f in matched)/filled if filled else None,
                           "commissions":[commissions.get(f["execution_id"]) for f in matched],"events":events})
        self.observe("reconciled_orders", result)
        return result

    def reconcile_positions(self, snapshot):
        with self.lock:
            first = self.db.execute("SELECT payload FROM batches ORDER BY rowid LIMIT 1").fetchone()
            refs = {r[0] for r in self.db.execute("SELECT order_ref FROM orders")}
        if not first:
            return {"status":"initial broker baseline; no submitted strategy batch","differences":[]}
        plan = json.loads(first[0])
        expected = {p["symbol"]:number(p["quantity"]) for p in plan["initial_snapshot"]["positions"]}
        for f in self.effective_fills():
            if f["account"] == snapshot["account"] and f["order_ref"] in refs:
                expected[f["symbol"]] = expected.get(f["symbol"],0) + number(f["quantity"])*(1 if f["side"] == "BUY" else -1)
        actual = {p["symbol"]:number(p["quantity"]) for p in snapshot["positions"]}
        differences = [{"symbol":s,"expected_from_baseline_and_fills":expected.get(s,0),"broker_observed":actual.get(s,0)}
                       for s in sorted(set(expected)|set(actual)) if expected.get(s,0) != actual.get(s,0)]
        value = {"status":"reconciled" if not differences else "unexplained position change; broker state retained, transmission blocked",
                 "differences":differences,"at":snapshot["observed_at"]}
        self.observe("position_reconciliation",value)
        return value

