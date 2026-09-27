"""One-batch PAPER authorization and a non-research, fail-closed watcher."""
from __future__ import annotations

from contextlib import contextmanager
import json
import os
from pathlib import Path
import sqlite3
import time

import pandas as pd

from .data import ROOT, calendar, digest, now_utc, save_json
from .paper import (BrokerSafetyError, PaperLedger, canonical, connection_config,
                    execution_policy, load_forecast, masked, number, redacted,
                    require, stamp, validate_forecast, validate_snapshot)

def opening_schedule(session):
    """Report the frozen cadence without shifting it after a missed opening."""
    from .competition import verify_frozen_policy
    policy = json.loads((ROOT / "config/model_v1/COMPETITION_V1.json").read_text())
    verify_frozen_policy(policy)
    day = pd.Timestamp(session)
    require(day.tzinfo is None and str(day.date()) == session, "Session must be YYYY-MM-DD")
    anchor = pd.Timestamp(policy["rebalance_anchor"])
    require(day >= anchor, "Session precedes the frozen rebalance anchor")
    cal = calendar(str(anchor.date()), str((day + pd.Timedelta(days=40)).date()))
    require(cal.is_session(day), "Requested date is not an exchange session")
    offset = len(cal.sessions_in_range(anchor, day)) - 1
    remaining = (-offset) % policy["spec"]["cadence"]
    next_day = day
    for _ in range(remaining):
        next_day = cal.next_session(next_day)
    entry = cal.session_open(day)
    return {"session":session, "entry_at":entry.isoformat(), "rebalance_anchor":str(anchor.date()),
            "cadence_sessions":policy["spec"]["cadence"], "eligible_rebalance":remaining == 0,
            "next_rebalance_session":str(next_day.date()),
            "required_feature_session":str(cal.previous_session(next_day).date()),
            "startup_utc":(entry - pd.Timedelta(minutes=45)).isoformat(),
            "startup_vancouver":(entry - pd.Timedelta(minutes=45)).tz_convert("America/Vancouver").isoformat(),
            "expires_utc":(entry + pd.Timedelta(minutes=30)).isoformat()}


def scheduled_opening():
    value = json.loads((ROOT / "config/paper_schedule.json").read_text())
    result = opening_schedule(value["session"])
    require(value["mode"] in {"readiness", "watch"}, "Unknown scheduled PAPER mode")
    if value["mode"] == "watch":
        require(result["eligible_rebalance"], "Scheduled execution is off the frozen rebalance cadence")
        batch, _ = frozen_batch(value["batch"])
        require(batch["entry_at"] == result["entry_at"], "Scheduled batch/session mismatch")
    else:
        require(value["batch"] is None, "Readiness-only scheduling must not select a batch")
    return {**result, "batch":value["batch"], "mode":value["mode"],
            "task_name":"QuantResearch-PAPER-" + value["session"].replace("-", "")}


@contextmanager
def watcher_lock(path=None):
    """OS-held byte lock; automatically released on process death."""
    import msvcrt
    path = path or ROOT / "state/private/paper-watcher.lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as handle:
        if handle.tell() == 0:
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        try:
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError as exc:
            raise BrokerSafetyError("Another PAPER watcher/commissioner already holds the execution lock") from exc
        try:
            yield
        finally:
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)


def window(batch, policy):
    entry = stamp(batch["entry_at"])
    first, last = policy["submit_minutes_before_open"]
    return entry - pd.Timedelta(minutes=first), entry - pd.Timedelta(minutes=last)


def frozen_batch(batch_id, now=None):
    require(bool(batch_id), "An explicit frozen batch ID is required")
    batch, policy = load_forecast(batch_id), execution_policy()
    validate_forecast(batch, policy, now or now_utc())
    frozen = json.loads((ROOT / "config/model_v1/COMPETITION_V1.json").read_text())
    require(batch["policy"] == frozen, "Forecast differs from frozen COMPETITION_V1")
    require(frozen["name"] == "COMPETITION_V1" and frozen["aggressive_version"] == "AGGRESSIVE_V1"
            and frozen["active_model"] == "ridge10" and frozen["allocation"] == {"aggressive": 1.0, "overnight": 0.0}
            and frozen["spec"]["night"] is False and frozen["overnight_version"] is None,
            "Only frozen AGGRESSIVE_V1 / ridge10 with overnight rejected is supported")
    return batch, policy


def no_unresolved(ledger, snapshot):
    orders = ledger.reconcile(snapshot)
    require(all(r["state"] in {"FILLED", "CANCELLED", "REJECTED"} for r in orders),
            "Unresolved prior reserved/submitted/partial/ambiguous batch; no retry or re-arm")
    result = ledger.reconcile_positions(snapshot)
    require(not result["differences"], "Positions do not reconcile to baseline plus executions")
    require(not snapshot["open_orders"], "Existing broker open orders require review")
    return result


class ArmStore:
    """Append-only authorization and terminal event, using the order ledger DB.

    An arm can be consumed only once. Consuming precedes the batch reservation
    and every wire call: a crash in between sacrifices execution, never safety.
    A batch cannot be armed again, even following expiry or failed submission.
    """
    def __init__(self, ledger):
        self.ledger = ledger
        with ledger.lock:
            ledger.db.executescript("""
              CREATE TABLE IF NOT EXISTS paper_arms (
                id TEXT PRIMARY KEY, batch_id TEXT UNIQUE NOT NULL, payload TEXT NOT NULL);
              CREATE TABLE IF NOT EXISTS paper_arm_terminal (
                arm_id TEXT PRIMARY KEY REFERENCES paper_arms(id), at TEXT NOT NULL,
                kind TEXT NOT NULL, detail TEXT NOT NULL);
              CREATE TRIGGER IF NOT EXISTS paper_arms_no_update BEFORE UPDATE ON paper_arms
                BEGIN SELECT RAISE(ABORT, 'Immutable PAPER arm'); END;
              CREATE TRIGGER IF NOT EXISTS paper_arms_no_delete BEFORE DELETE ON paper_arms
                BEGIN SELECT RAISE(ABORT, 'Immutable PAPER arm'); END;
              CREATE TRIGGER IF NOT EXISTS paper_arm_terminal_no_update BEFORE UPDATE ON paper_arm_terminal
                BEGIN SELECT RAISE(ABORT, 'Immutable PAPER arm event'); END;
              CREATE TRIGGER IF NOT EXISTS paper_arm_terminal_no_delete BEFORE DELETE ON paper_arm_terminal
                BEGIN SELECT RAISE(ABORT, 'Immutable PAPER arm event'); END;
            """)

    def get(self, batch_id):
        with self.ledger.lock:
            row = self.ledger.db.execute("SELECT id,payload FROM paper_arms WHERE batch_id=?", (batch_id,)).fetchone()
        require(row is not None, "No explicit PAPER arm for this forecast batch")
        record = json.loads(row[1])
        require(record["id"] == row[0] == digest(canonical({k:v for k,v in record.items() if k != "id"}).encode()),
                "PAPER arm integrity failure")
        require(record["forecast_batch"] == batch_id, "PAPER arm batch identity mismatch")
        return record

    def terminal(self, record):
        with self.ledger.lock:
            row = self.ledger.db.execute("SELECT kind,at,detail FROM paper_arm_terminal WHERE arm_id=?", (record["id"],)).fetchone()
        return {"kind":row[0], "at":row[1], "detail":json.loads(row[2])} if row else None

    def finish(self, record, kind, detail=None, strict=False):
        with self.ledger.lock, self.ledger.db:
            result = self.ledger.db.execute("INSERT OR IGNORE INTO paper_arm_terminal VALUES(?,?,?,?)",
                                          (record["id"], now_utc(), kind, canonical(detail or {})))
            require(not strict or result.rowcount == 1, "PAPER arm already consumed/disarmed; never resend")

    def expire(self, now):
        with self.ledger.lock:
            rows = self.ledger.db.execute("SELECT batch_id FROM paper_arms a WHERE NOT EXISTS "
                "(SELECT 1 FROM paper_arm_terminal t WHERE t.arm_id=a.id)").fetchall()
        for row in rows:
            record = self.get(row[0])
            if stamp(now) >= stamp(record["expires_at"]):
                self.finish(record, "EXPIRED")

    def create(self, value):
        record = dict(value)
        record["id"] = digest(canonical(record).encode())
        with self.ledger.lock:
            try:
                self.ledger.db.execute("BEGIN IMMEDIATE")
                require(not self.ledger.db.execute("SELECT 1 FROM paper_arms a WHERE NOT EXISTS "
                    "(SELECT 1 FROM paper_arm_terminal t WHERE t.arm_id=a.id)").fetchone(), "An existing PAPER arm must finish first")
                self.ledger.db.execute("INSERT INTO paper_arms VALUES(?,?,?)",
                                       (record["id"], record["forecast_batch"], canonical(record)))
                self.ledger.db.commit()
            except sqlite3.IntegrityError as exc:
                self.ledger.db.rollback()
                raise BrokerSafetyError("This batch already has an immutable arm; never re-arm or resend") from exc
            except BaseException:
                self.ledger.db.rollback()
                raise
        return record


def inspect_connection(batch_id=None, ledger=None):
    """Read-only broker requests, including server clock and contract resolution."""
    from .tws import PaperTWS
    from .paper_cli import source_fingerprint
    cfg = connection_config()
    require(cfg["host"] == "127.0.0.1", "Commissioning requires host 127.0.0.1")
    require(cfg["execution_enabled"] is False, "Persistent execution_enabled must remain false; only an arm grants temporary authority")
    batch, policy = frozen_batch(batch_id) if batch_id else (None, execution_policy())
    own_ledger = ledger is None
    ledger = ledger or PaperLedger()
    broker = PaperTWS(cfg, policy, ledger)
    before = source_fingerprint()
    try:
        broker.start()
        snapshot = broker.snapshot()
        validate_snapshot(snapshot, cfg, policy, now_utc())
        reconciliation = no_unresolved(ledger, snapshot)
        needed = {r["symbol"] for r in batch["position_intents"] if r["role"] == "active" and r["target_weight"] > 0} if batch else set()
        needed |= {p["symbol"] for p in snapshot["positions"] if number(p["quantity"]) != 0}
        contracts = broker.qualify(needed)
        broker.clock_check()
        require(before == source_fingerprint(), "Source/config changed during inspection")
        evidence = {"at":now_utc(), "source_sha256":before, "batch_id":batch_id,
                    "account_sha256":digest(cfg["expected_account"].encode()),
                    "account_masked":masked(cfg["expected_account"]), "snapshot":snapshot,
                    "contracts":contracts, "server_time_utc":pd.Timestamp(broker.server_time, unit="s", tz="UTC").isoformat(),
                    "position_reconciliation":reconciliation, "orders_transmitted":False}
        ledger.observe("autonomy_inspection", evidence)
        save_json(ROOT / "state/private/paper-inspection.json", evidence)
        return evidence
    finally:
        broker.stop()
        if own_ledger:
            ledger.close()


def validate_arm(record, cfg, batch, policy, now, submission=False):
    from .paper_cli import require_tested, source_fingerprint
    require_tested()
    disk_cfg = connection_config()
    require(disk_cfg["execution_enabled"] is False, "Persistent execution switch changed; re-commissioning required")
    require({**cfg, "execution_enabled":False} == disk_cfg, "Broker session/config mismatch")
    require(record["source_sha256"] == source_fingerprint(), "Source/config differs from explicit PAPER arm")
    require(record["account_sha256"] == digest(cfg["expected_account"].encode()), "Armed account mismatch")
    require(record["forecast_batch"] == batch["id"] and record["model_version"] == batch["version"]
            and record["entry_at"] == batch["entry_at"] and record["forecast_sha256"] == digest(canonical(batch).encode()),
            "Armed forecast/model/session mismatch")
    opening, cutoff = window(batch, policy)
    require(record["window_start"] == opening.isoformat() and record["window_end"] == cutoff.isoformat()
            and stamp(record["expires_at"]) == cutoff + pd.Timedelta(seconds=30), "Armed execution window mismatch")
    require(stamp(record["armed_at"]) <= stamp(now) < stamp(record["expires_at"]), "PAPER arm expired or future")
    require(record["read_only_api_disabled_confirmed"] is True, "Local confirmation of Read-Only API disabled is missing")
    validate_forecast(batch, policy, now, submission=submission)


def arm(batch_id):
    """An explicit, interactive local command. Never calls order transport."""
    from .paper_cli import require_tested, source_fingerprint
    require_tested()
    batch, policy = frozen_batch(batch_id)
    start, end = window(batch, policy)
    require(stamp(now_utc()) < start, "Arm must be commissioned before this batch's opening window")
    print(f"PAPER only: {batch_id}, opening {batch['entry_at']}. ARM sends no orders.", flush=True)
    print("Confirm TWS PAPER > Global Configuration > API > Settings > Read-Only API is unchecked.", flush=True)
    confirmation = input("Type ARM PAPER to confirm and arm this exact batch: ")
    require(confirmation == "ARM PAPER", "Explicit local PAPER arming confirmation was not supplied")
    with watcher_lock():
        ledger = PaperLedger()
        try:
            store = ArmStore(ledger)
            store.expire(now_utc())
            evidence = inspect_connection(batch_id, ledger)
            require_tested()
            cfg = connection_config()
            before = source_fingerprint()
            require(evidence["source_sha256"] == before, "Configuration changed after inspection")
            batch, policy = frozen_batch(batch_id)
            require(stamp(now_utc()) < start, "Opening window started while arming; no late arm")
            record = {"schema":"paper_arm_v1", "forecast_batch":batch_id, "model_version":batch["version"],
                      "model":"AGGRESSIVE_V1/ridge10", "account_sha256":digest(cfg["expected_account"].encode()),
                      "account_masked":masked(cfg["expected_account"]), "entry_at":batch["entry_at"],
                      "session_new_york":stamp(batch["entry_at"]).tz_convert("America/New_York").isoformat(),
                      "armed_at":now_utc(), "window_start":start.isoformat(), "window_end":end.isoformat(),
                      "expires_at":(end + pd.Timedelta(seconds=30)).isoformat(), "source_sha256":before,
                      "forecast_sha256":digest(canonical(batch).encode()), "inspection_sha256":digest(canonical(evidence).encode()),
                      "read_only_api_disabled_confirmed":True, "read_only_confirmation_basis":"operator local confirmation; API permission checked by what-if at execution"}
            record = store.create(record)
            save_json(ROOT / "state/paper_checks/ARMED.json", record)
            return record
        finally:
            ledger.close()


def require_active_arm(ledger, batch, cfg, policy):
    store = ArmStore(ledger)
    store.expire(now_utc())
    record = store.get(batch["id"])
    require(store.terminal(record) is None, "PAPER arm is consumed/disarmed/expired; never resend")
    validate_arm(record, cfg, batch, policy, now_utc())
    return record


def consume_for_transmission(broker, batch, plan, snapshot):
    record = require_active_arm(broker.ledger, batch, broker.cfg, broker.policy)
    validate_arm(record, broker.cfg, batch, broker.policy, now_utc(), submission=True)
    no_unresolved(broker.ledger, snapshot)
    ArmStore(broker.ledger).finish(record, "CONSUMED", {"plan_id":plan["id"], "pid":os.getpid()}, strict=True)
    return record


def check_wire_arm(broker, batch, plan):
    record = broker.arm_authorization
    require(record is not None, "Actual order transport requires a consumed arm in this process")
    store = ArmStore(broker.ledger)
    require(store.get(batch["id"]) == record, "PAPER arm record changed")
    terminal = store.terminal(record)
    require(terminal is not None and terminal["kind"] == "CONSUMED"
            and terminal["detail"] == {"plan_id":plan["id"], "pid":os.getpid()}, "PAPER arm consumption mismatch")
    validate_arm(record, broker.cfg, batch, broker.policy, now_utc(), submission=True)


def watch(batch_id=None, poll_seconds=15, monitor_only=False, max_seconds=None):
    """No generation, promotion or latest-batch fallback. No retry after consume."""
    from .tws import PaperTWS
    from .paper_cli import commission, require_tested
    require(1 <= poll_seconds <= 60, "Watcher poll interval must be 1..60 seconds")
    require(max_seconds is None or (monitor_only and 0 < max_seconds <= 300), "Bounded startup tests must be monitor-only")
    start_clock = time.monotonic()
    successful_checks = 0
    with watcher_lock():
        ledger = PaperLedger()
        store = ArmStore(ledger)
        broker = None
        try:
            if batch_id is None:
                with ledger.lock:
                    rows = ledger.db.execute("SELECT batch_id FROM paper_arms ORDER BY rowid DESC LIMIT 1").fetchall()
                require(len(rows) == 1, "Watcher requires one explicit arm or --batch for monitor-only commissioning")
                batch_id = rows[0][0]
            batch = load_forecast(batch_id)
            policy = execution_policy()
            cfg = connection_config()
            require(cfg["execution_enabled"] is False, "Persistent execution switch must remain disabled")
            require_tested()
            if not monitor_only:
                # Missing authorization/config is a startup failure, not a daemon
                # that silently waits all morning without being commissioned.
                store.get(batch_id)
            first, last = window(batch, policy)
            observation_end = stamp(batch["entry_at"]) + pd.Timedelta(minutes=5)
            while True:
                now = now_utc()
                state = {"at":now, "pid":os.getpid(), "batch_id":batch_id, "monitor_only":monitor_only,
                         "source":"explicit immutable arm only; research disabled", "status":"blocked"}
                try:
                    store.expire(now)
                    if stamp(now) >= observation_end:
                        state["status"] = "finished; opening session ended, no catch-up"
                        save_json(ROOT / "state/paper_checks/watcher.json", state)
                        if not monitor_only:
                            terminal = store.terminal(store.get(batch_id))
                            require(terminal and terminal["kind"] == "CONSUMED", "Opening window ended without an authorized submission; see watcher blockers")
                        return state
                    cfg = connection_config()
                    require_tested()
                    require(cfg["execution_enabled"] is False, "Persistent execution switch must remain disabled")
                    # Re-read integrity-protected records each cycle, never replace the selected batch.
                    require(load_forecast(batch_id) == batch, "Frozen batch changed while watching")
                    require(execution_policy() == policy, "Frozen execution policy changed")
                    if stamp(now) < last:
                        frozen_batch(batch_id, now)
                    if broker is None:
                        broker = PaperTWS(cfg, policy, ledger)
                        broker.start()
                    require(broker.cfg == cfg, "Connected broker configuration changed")
                    broker.clock_check()
                    snapshot = broker.snapshot()
                    validate_snapshot(snapshot, cfg, policy, now_utc(), allow_open=True)
                    reconciled = ledger.reconcile(snapshot)
                    positions = ledger.reconcile_positions(snapshot)
                    state.update(connection_healthy=True, account_masked=masked(snapshot["account"]),
                                 reconciliation=[{"symbol":r["order"]["symbol"], "state":r["state"]} for r in reconciled],
                                 position_reconciliation=positions)
                    successful_checks += 1
                    record = None
                    if not monitor_only:
                        record = store.get(batch_id)
                    terminal = store.terminal(record) if record else None
                    now = now_utc()
                    if monitor_only:
                        state["status"] = "monitor-only; order transport disabled"
                    elif terminal:
                        state["status"] = "disarmed; reconciliation only"
                        state["arm_terminal"] = terminal
                    elif stamp(now) >= last:
                        store.finish(record, "EXPIRED", {"reason":"submission window ended"})
                        state["status"] = "expired; no catch-up"
                    else:
                        validate_arm(record, cfg, batch, policy, now)
                        no_unresolved(ledger, snapshot)
                        if stamp(now) < first:
                            state["status"] = "armed; waiting for frozen opening window"
                        else:
                            # Close the monitoring connection before the same fixed client ID is used.
                            broker.stop()
                            broker = None
                            seconds = max(0, int((observation_end - stamp(now)).total_seconds()))
                            commission("execute", batch_id, observe_seconds=seconds)
                            state["status"] = "consumed; submission attempted, reconcile broker results"
                except Exception as exc:
                    state["blocker"] = str(redacted(str(exc)))
                    ledger.observe("watcher_blocked", state)
                    # Fresh object + full reconciliation on retry. The durable arm consumption
                    # and batch reservation prohibit all retries once transmission was possible.
                    if broker:
                        broker.stop()
                        broker = None
                save_json(ROOT / "state/paper_checks/watcher.json", redacted(state))
                print(json.dumps(redacted(state), sort_keys=True), flush=True)
                if max_seconds is not None and time.monotonic() - start_clock >= max_seconds:
                    from .paper_cli import source_fingerprint
                    result = {**state, "successful_checks":successful_checks, "completed_at":now_utc(),
                              "source_sha256":source_fingerprint()}
                    save_json(ROOT / "state/paper_checks/watcher-startup-test.json", result)
                    require(successful_checks > 0 and "blocker" not in state, "Monitor-only startup test failed; see watcher log")
                    return result
                time.sleep(poll_seconds)
        finally:
            if broker:
                broker.stop()
            ledger.close()


def readiness(batch_id=None, session=None):
    """Fresh, non-transmitting checks. Missing prerequisites are explicit FAILs."""
    import subprocess
    from .paper_cli import require_tested, source_fingerprint
    checks = {}

    def check(name, operation):
        try:
            detail = operation()
            checks[name] = {"status":"PASS", "detail":redacted(detail)}
            return detail
        except Exception as exc:
            checks[name] = {"status":"FAIL", "detail":str(redacted(str(exc)))}
            return None

    def tests():
        require_tested()
        return json.loads((ROOT / "state/paper_checks/passed.json").read_text())

    scheduled = check("configured_scheduled_session", scheduled_opening)
    session = session or (scheduled["session"] if scheduled else None)
    batch_id = batch_id or (scheduled["batch"] if scheduled else None)
    opening = check("requested_exchange_session", lambda: opening_schedule(session))

    def cadence():
        require(opening is not None, "Valid requested session required")
        require(opening["eligible_rebalance"], "Requested session is off the frozen five-session cadence; next rebalance "
                + opening["next_rebalance_session"] + " requires " + opening["required_feature_session"] + " completed data")
        return opening

    check("frozen_rebalance_cadence", cadence)
    attestation = check("complete_test_suite_and_source_config_fingerprint", tests)

    def coverage():
        require(attestation is not None and opening is not None, "Passing tests and a valid session required")
        require(stamp(attestation["at"]) + pd.Timedelta(hours=24) >= stamp(opening["entry_at"]),
                "Test attestation expires before the requested opening; rerun paper_cli check within 24 hours")
        return "Test attestation covers the requested opening"

    check("test_attestation_covers_requested_opening", coverage)

    def frozen():
        batch, policy = frozen_batch(batch_id)
        require(opening is not None and batch["entry_at"] == opening["entry_at"],
                "Frozen batch does not match the requested opening session")
        start, end = window(batch, policy)
        return {"batch":batch_id, "version":batch["version"], "decision_at":batch["issued_at_utc"],
                "entry_at":batch["entry_at"], "window_start":start.isoformat(), "window_end_exclusive":end.isoformat(),
                "forecast_sha256":digest(canonical(batch).encode()), "model":"AGGRESSIVE_V1/ridge10", "overnight": "REJECTED"}

    check("COMPETITION_V1_and_exact_frozen_batch_timing_integrity", frozen)

    def config():
        cfg = connection_config()
        require(cfg["host"] == "127.0.0.1" and cfg["execution_enabled"] is False, "Local PAPER config must keep execution disabled")
        return {"mode":cfg["mode"], "account":masked(cfg["expected_account"]), "client_id":cfg["client_id"],
                "port":cfg["port"], "execution_enabled":False, "exact_single_allowlist":True}

    check("exact_independently_verified_PAPER_allowlist", config)

    def inspect():
        with watcher_lock():
            evidence = inspect_connection(batch_id)
        snapshot = evidence["snapshot"]
        return {"at":evidence["at"], "account":evidence["account_masked"], "nav":snapshot["nav"],
                "cash":snapshot["cash"], "buying_power":snapshot["buying_power"],
                "positions_count":len(snapshot["positions"]), "open_orders_count":len(snapshot["open_orders"]),
                "executions_count":len(snapshot["executions"]), "server_time_utc":evidence["server_time_utc"],
                "contracts":sorted(evidence["contracts"]), "source_sha256":evidence["source_sha256"]}

    check("authenticated_TWS_account_balances_positions_orders_executions_clock_contracts", inspect)

    def armed():
        batch, policy = frozen_batch(batch_id)
        ledger = PaperLedger()
        try:
            record = require_active_arm(ledger, batch, connection_config(), policy)
            return record
        finally:
            ledger.close()

    record = check("explicit_one_batch_immutable_arm", armed)
    checks["read_only_API_disabled"] = {"status":"PASS" if record else "FAIL",
        "detail":"Operator locally confirmed disabled when arming; IBKR what-if must still accept before submission"
                 if record else "Not locally confirmed by a successful explicit PAPER ARM; keep Read-Only API checked until inspection/testing finish"}

    def task():
        result = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File",
                                 str(ROOT / "scripts/verify_paper_task.ps1")], cwd=ROOT, capture_output=True, text=True, timeout=30)
        require(result.returncode == 0, "Scheduled task verification failed: " + result.stderr.strip())
        value = json.loads(result.stdout)
        require(value["passed"] is True and value["session"] == session and value["batch"] == batch_id,
                "Scheduled task settings/batch/session mismatch")
        return value

    check("one_time_Windows_scheduled_task", task)

    def execution_mode():
        require(scheduled is not None and scheduled["mode"] == "watch", "Scheduled task is readiness-only; cannot submit orders")
        return "Exact-batch watcher configured; immutable arm still required"

    check("scheduled_execution_mode", execution_mode)

    def startup():
        value = json.loads((ROOT / "state/paper_checks/watcher-startup-test.json").read_text())
        require(value["monitor_only"] is True and value["successful_checks"] > 0 and "blocker" not in value,
                "Watcher monitor-only startup did not pass")
        require(value["batch_id"] == batch_id and value["source_sha256"] == source_fingerprint(), "Watcher startup evidence is outdated")
        logs = ROOT / "state/private/logs"
        require(logs.exists() and any("Watcher exited with code 0" in p.read_text() for p in logs.glob("*-startup.log")),
                "No successful logged launcher startup")
        return {"at":value["completed_at"], "successful_checks":value["successful_checks"], "logs":str(logs)}

    check("watcher_startup_and_timestamped_logs", startup)

    def duplicates():
        with watcher_lock():
            pass
        require(attestation is not None, "Passing complete test attestation required for duplicate prevention evidence")
        log = (ROOT / "state/paper_checks/latest-tests.txt").read_text()
        require("test_duplicate_watcher_process" in log and "test_consumed_arm_never_retries" in log
                and "test_wire_rejects_changed_config_after_consumption" in log, "Required fail-closed regression evidence missing")
        return "No watcher currently holds the OS lock; complete suite covers cross-process exclusion, replay and changed-config rejection"

    check("no_duplicate_watcher_and_fail_closed_regressions", duplicates)
    ready = all(row["status"] == "PASS" for row in checks.values())
    result = {"at":now_utc(), "ready":ready, "status":"READY FOR AUTONOMOUS PAPER OPEN" if ready else "NOT READY",
              "session":session, "batch":batch_id, "opening":opening, "checks":checks,
              "runtime_requirements":"Authenticated TWS PAPER must remain connected; fresh live quotes, account reconciliation, clock, arm/source checks and exact what-if previews must pass inside the frozen window. Failures never authorize catch-up or ambiguous retransmission."}
    save_json(ROOT / "state/paper_checks/TOMORROW_PREOPEN_READINESS.json", result)
    return result
