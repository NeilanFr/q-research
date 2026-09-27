"""Official IBKR socket transport, restricted to independently allowlisted PAPER.

SDK 10.50.2. Direct public placeOrder is intentionally disabled. What-if and
actual orders enter separate guarded methods; reconnect invalidates the object.
"""
from __future__ import annotations

from copy import deepcopy
from decimal import Decimal
import logging
from threading import Event, RLock, Thread
import time

from ibapi.client import EClient
from ibapi.contract import Contract
from ibapi.execution import ExecutionFilter
from ibapi.order import Order
from ibapi.order_cancel import OrderCancel
from ibapi.wrapper import EWrapper

from .data import now_utc
from .paper import (BrokerSafetyError, age, canonical, currency_cash, digest, load_forecast, number, require,
                    stamp, usd_amounts, validate_contract, validate_plan, validate_quote, verify_account)


def fields(obj, names):
    return {name: getattr(obj, name, None) for name in names.split()}


def contract_record(c):
    return {"symbol":c.symbol,"con_id":c.conId,"sec_type":c.secType,"currency":c.currency,
            "exchange":c.exchange,"primary_exchange":c.primaryExchange}


def make_order(row, account, what_if=False):
    o = Order()
    o.account, o.action, o.totalQuantity = account, row["side"], Decimal(row["quantity"])
    o.orderType, o.tif, o.lmtPrice = "LMT", "OPG", row["limit_price"]
    o.outsideRth, o.transmit, o.whatIf = False, True, what_if
    o.orderRef = ("WI-" if what_if else "") + row["order_ref"]
    return o


class PaperTWS(EWrapper, EClient):
    def __init__(self, cfg, policy, ledger):
        EWrapper.__init__(self)
        EClient.__init__(self, self)
        verify_account(cfg, [cfg["expected_account"]])
        self.cfg, self.policy, self.ledger = deepcopy(cfg), deepcopy(policy), ledger
        self.lock = RLock()
        self.events, self.errors, self.contracts, self.contract_objects = {}, {}, {}, {}
        self.accounts, self.positions, self.summary, self.account_values = [], {}, {}, {}
        self.portfolio = {}
        self.open_orders, self.executions, self.completed = {}, {}, []
        self.quotes, self.quote_symbols, self.previews = {}, {}, {}
        self.healthy, self.next_id, self.request_id = False, None, 1_000_000
        self.connected_at, self.worker, self.pnl_value = None, None, None
        self.preview_ids, self.used = set(), False
        self.fatal_connection = False
        self._wire_context = None
        self.order_error = False
        self.arm_authorization = None
        self.armed_batch, self.armed_plan = None, None
        # Default SDK logs can include full IDs. All selected callbacks instead
        # enter the private ledger; CLI/report output is separately redacted.
        logging.getLogger("ibapi").propagate = False
        logging.getLogger("ibapi").addHandler(logging.NullHandler())

    def evt(self, key, clear=False):
        with self.lock:
            ev = self.events.setdefault(key, Event())
            if clear:
                ev.clear()
            return ev

    def request(self):
        with self.lock:
            self.request_id += 1
            return self.request_id

    def alloc_order_id(self):
        with self.lock:
            require(self.next_id is not None, "Missing broker order ID sequence")
            value = self.next_id
            self.next_id += 1
            return value

    def await_event(self, key, timeout=15):
        deadline = time.monotonic()+timeout
        while not self.evt(key).wait(.1):
            self.assert_session()
            require(time.monotonic() < deadline, f"IBKR timed out waiting for {key}")
        self.assert_session()
        require(key not in self.errors, f"IBKR rejected request {key}; inspect private broker errors")

    def assert_session(self, transmit=False):
        require(self.isConnected() and self.healthy, "Disconnected/reconnect ambiguity; fresh connection and reconciliation required")
        verify_account(self.cfg, self.accounts, transmit)
        if transmit:
            require(not self.order_error, "An order was rejected; reconcile before any more transmission")

    def start(self):
        require(not self.used, "Do not reuse/reconnect an IBKR session object")
        self.used = True
        self.connect(self.cfg["host"], self.cfg["port"], clientId=self.cfg["client_id"])
        def run():
            try:
                self.run()
            except BaseException as exc:
                self.healthy = False
                self.ledger.observe("callback_failure", {"type":type(exc).__name__})
        self.worker = Thread(target=run, daemon=True)
        self.worker.start()
        require(self.evt("ready").wait(15) and self.isConnected(), "No TWS handshake; start TWS PAPER with socket API enabled")
        self.reqManagedAccts()
        require(self.evt("accounts").wait(15), "No managed accounts response")
        verify_account(self.cfg, self.accounts)
        require(not self.fatal_connection, "Connection reported a fatal/reconnect error")
        self.healthy = True
        self.connected_at = now_utc()
        self.clock_check()
        self.ledger.observe("connection", {"account":self.cfg["expected_account"],"client_id":self.cfg["client_id"],
                            "at":self.connected_at,"server_version":self.serverVersion(),"environment":"PAPER independently verified allowlist; API AccountType is not environment proof"})
        return self

    def stop(self):
        self.healthy = False
        self.disconnect()
        if self.worker:
            self.worker.join(2)

    def nextValidId(self, orderId):
        with self.lock:
            self.next_id = max(self.next_id or 0, orderId)
        self.evt("ready").set()

    def managedAccounts(self, accountsList):
        self.accounts = [x for x in accountsList.split(",") if x]
        if self.connected_at:
            try:
                verify_account(self.cfg,self.accounts)
            except BrokerSafetyError:
                self.healthy = False
        self.evt("accounts").set()

    def connectionClosed(self):
        self.healthy = False

    def error(self, reqId, *args):
        if len(args) >= 3 and isinstance(args[1], int):
            error_time, code, message, *advanced = args
        else:
            code, message, *advanced = args
            error_time = None
        payload = {"req_id":reqId,"code":code,"message":message,"error_time":error_time,"advanced":advanced}
        self.ledger.event("ERROR",self.cfg["client_id"],reqId,payload)
        if code in {1100,1101,1102,1300,326,502,504}:
            self.healthy = False
            self.fatal_connection = True
        if code not in {2104,2106,2107,2108,2158}:
            self.errors[reqId] = payload
            self.evt(reqId).set()
        with self.ledger.lock:
            own = self.ledger.db.execute("SELECT 1 FROM orders WHERE client_id=? AND order_id=?",(self.cfg["client_id"],reqId)).fetchone()
        if own and reqId not in self.preview_ids:
            self.order_error = True
            if code in {201,202}:
                self.ledger.event("STATUS",self.cfg["client_id"],reqId,{"status":"Inactive" if code==201 else "Cancelled","error":payload})

    def currentTime(self, time_):
        self.server_time = time_
        self.evt("clock").set()

    def clock_check(self):
        self.evt("clock",True)
        self.reqCurrentTime()
        self.await_event("clock")
        require(abs(stamp(now_utc()).timestamp()-self.server_time) <= 3, "Local/broker clocks disagree")

    def accountSummary(self, reqId, account, tag, value, currency):
        with self.lock:
            self.summary[(account,tag,currency)] = value

    def accountSummaryEnd(self, reqId):
        self.evt(reqId).set()

    def updateAccountValue(self, key, val, currency, accountName):
        with self.lock:
            self.account_values[(accountName,key,currency)] = val

    def accountDownloadEnd(self, accountName):
        self.evt("account_download").set()

    def updatePortfolio(self, contract, position, marketPrice, marketValue, averageCost, unrealizedPNL, realizedPNL, accountName):
        with self.lock:
            self.portfolio[(accountName,contract.conId)] = {**contract_record(contract),"account":accountName,
                "quantity":str(position),"market_price":marketPrice,"market_value":marketValue,"average_cost":averageCost,
                "unrealized_pnl":unrealizedPNL,"realized_pnl":realizedPNL,"observed_at":now_utc()}

    def position(self, account, contract, position, avgCost):
        with self.lock:
            self.positions[(account,contract.conId)] = {**contract_record(contract),"account":account,"quantity":str(position),"average_cost":avgCost}

    def positionEnd(self):
        self.evt("positions").set()

    def pnl(self, reqId, dailyPnL, unrealizedPnL, realizedPnL):
        def optional(v):
            try:
                return number(v)
            except BrokerSafetyError:
                return None
        self.pnl_value = {"daily_pnl":optional(dailyPnL),"unrealized_pnl":optional(unrealizedPnL),"realized_pnl":optional(realizedPnL),"at":now_utc()}
        self.evt(reqId).set()

    def openOrder(self, orderId, contract, order, orderState):
        with self.lock:
            self.next_id = max(self.next_id or 0, orderId+1)
        if orderId in self.preview_ids:
            self.previews[orderId] = fields(orderState, "status initMarginBefore initMarginChange initMarginAfter maintMarginAfter equityWithLoanAfter commissionAndFees minCommissionAndFees maxCommissionAndFees commissionAndFeesCurrency warningText rejectReason")
            self.evt(orderId).set()
            return
        value = {**contract_record(contract),"order_id":orderId,"perm_id":order.permId,"client_id":order.clientId,
                 "account":order.account,"order_ref":order.orderRef,"side":order.action,"quantity":str(order.totalQuantity),
                 "order_type":order.orderType,"tif":order.tif,"limit_price":order.lmtPrice,"status":orderState.status}
        with self.lock:
            self.open_orders[(order.clientId,orderId)] = value
        self.ledger.event("ACKNOWLEDGED",order.clientId,orderId,value)

    def openOrderEnd(self):
        self.evt("open_orders").set()

    def orderStatus(self, orderId, status, filled, remaining, avgFillPrice, permId, parentId, lastFillPrice, clientId, whyHeld, mktCapPrice):
        if orderId in self.preview_ids:
            return
        value = {"status":status,"filled":str(filled),"remaining":str(remaining),"average_fill_price":avgFillPrice,
                 "perm_id":permId,"last_fill_price":lastFillPrice,"why_held":whyHeld}
        self.ledger.event("STATUS",clientId,orderId,value)

    def execDetails(self, reqId, contract, execution):
        value = {"execution_id":execution.execId,"account":execution.acctNumber,"order_ref":execution.orderRef,
                 "order_id":execution.orderId,"perm_id":execution.permId,"client_id":execution.clientId,
                 "symbol":contract.symbol,"con_id":contract.conId,"side":{"BOT":"BUY","SLD":"SELL"}.get(execution.side,execution.side),
                 "quantity":str(execution.shares),"price":execution.price,"average_price":execution.avgPrice,"broker_time":execution.time}
        self.ledger.fill(value)
        with self.lock:
            self.executions[execution.execId] = value

    def execDetailsEnd(self, reqId):
        self.evt(reqId).set()

    def commissionAndFeesReport(self, report):
        self.ledger.commission({"execution_id":report.execId,"commission":report.commissionAndFees,"currency":report.currency,"realized_pnl":report.realizedPNL})

    def commissionReport(self, report):
        self.ledger.commission({"execution_id":report.execId,"commission":report.commission,"currency":report.currency,"realized_pnl":report.realizedPNL})

    def completedOrder(self, contract, order, orderState):
        value = {**contract_record(contract),"order_id":order.orderId,"perm_id":order.permId,"client_id":order.clientId,
                 "account":order.account,"order_ref":order.orderRef,"status":orderState.status,"completed_status":orderState.completedStatus}
        self.completed.append(value)
        self.ledger.event("COMPLETED",order.clientId,order.orderId,value)

    def completedOrdersEnd(self):
        self.evt("completed").set()

    def snapshot(self):
        self.assert_session()
        account = self.cfg["expected_account"]
        with self.lock:
            self.summary, self.account_values, self.positions, self.open_orders, self.executions, self.completed, self.portfolio = {},{},{},{},{},[],{}
        summary_id, execution_id, pnl_id = self.request(),self.request(),self.request()
        for k in (summary_id, execution_id, "positions", "open_orders", "completed", "account_download"):
            self.evt(k,True)
        self.reqAccountSummary(summary_id,"All","AccountType,NetLiquidation,AvailableFunds,BuyingPower,TotalCashValue")
        self.reqAccountUpdates(True,account)
        self.reqPositions()
        self.reqAllOpenOrders()
        execution_filter = ExecutionFilter()
        execution_filter.acctCode = account
        self.reqExecutions(execution_id,execution_filter)
        self.reqCompletedOrders(False)
        self.pnl_value = None
        self.reqPnL(pnl_id,account,"")
        try:
            for k in (summary_id, execution_id,"positions","open_orders","completed","account_download"):
                self.await_event(k)
            self.evt(pnl_id).wait(2)
            with self.lock:
                summary, values = dict(self.summary),dict(self.account_values)
                positions, opens, executions, completed = deepcopy(list(self.positions.values())),deepcopy(list(self.open_orders.values())),deepcopy(list(self.executions.values())),deepcopy(self.completed)
                portfolio = deepcopy(list(self.portfolio.values()))
        finally:
            self.cancelAccountSummary(summary_id)
            self.reqAccountUpdates(False,account)
            self.cancelPositions()
            self.cancelPnL(pnl_id)
        require(all(k[0] == account for k in [*summary,*values]), "Account callback mismatch")
        nav_rows = [(ccy,number(v)) for (acct,tag,ccy),v in summary.items() if tag == "NetLiquidation"]
        require(len(nav_rows)==1 and nav_rows[0][0] in {"USD", "CAD"}, "Unsupported/ambiguous account base currency")
        base_currency = nav_rows[0][0]
        cash_by_currency = currency_cash(values, account, base_currency)
        def amount(tag):
            return number(summary.get((account,tag,base_currency)))
        cash = amount("TotalCashValue") if base_currency == "CAD" else cash_by_currency["USD"]
        base = {"currency":base_currency,"nav":nav_rows[0][1],"cash":cash,
                "available_funds":amount("AvailableFunds"),"buying_power":amount("BuyingPower"),
                "total_cash_value":amount("TotalCashValue")}
        fx = self.usd_cad_quote() if base_currency == "CAD" else None
        amounts = usd_amounts(base,fx,self.policy,now_utc()) if fx else {k:v for k,v in base.items() if k != "currency"}
        result = {"account":account,"managed_accounts":self.accounts.copy(),"observed_at":now_utc(),"connection_at":self.connected_at,
                  "client_id":self.cfg["client_id"],"complete":True,"connection_healthy":self.healthy,"base_currency":base_currency,
                  "account_type":next((v for (a,t,c),v in summary.items() if t == "AccountType"),"unavailable"),
                  "environment":"PAPER asserted independently; exact connected account allowlist match",
                  **amounts,"base_amounts":base,"execution_currency":"USD","fx_quote":fx,"cash_by_currency":cash_by_currency,
                  "fx_conversion":"CAD divided by USD.CAD ask (CAD per USD); no FX order" if fx else "USD account",
                  "execution_capacity_usd":min(cash_by_currency["USD"], *(amounts[k] for k in ("nav","cash","available_funds","buying_power"))),
                  "positions":positions,"portfolio":portfolio,"open_orders":opens,"executions":executions,"completed_orders":completed,"pnl":self.pnl_value,
                  "account_values":[{"account":a,"tag":t,"currency":c,"value":v} for (a,t,c),v in values.items()]}
        self.ledger.observe("account_snapshot",result)
        return result

    def usd_cad_quote(self, timeout=15):
        self.assert_session()
        req = self.request()
        contract = Contract()
        contract.symbol, contract.currency, contract.secType, contract.exchange = "USD", "CAD", "CASH", "IDEALPRO"
        self.reqMarketDataType(1)
        self.reqMktData(req,contract,"",False,False,[])
        deadline = time.monotonic() + timeout
        last_error = "Live USD/CAD FX quote unavailable"
        try:
            while True:
                self.assert_session()
                require(req not in self.errors, "TWS rejected live USD/CAD FX request")
                with self.lock:
                    quote = {**contract_record(contract), **deepcopy(self.quotes.get(req,{}))}
                try:
                    usd_amounts({"currency":"CAD", **{k:1 for k in ("nav","cash","available_funds","buying_power","total_cash_value")}},
                                quote,self.policy,now_utc())
                    self.ledger.observe("live_usd_cad_fx",quote)
                    return quote
                except BrokerSafetyError as exc:
                    last_error = str(exc)
                require(time.monotonic() < deadline, "Live USD/CAD FX unavailable or invalid: " + last_error)
                time.sleep(.1)
        finally:
            self.cancelMktData(req)

    def contractDetails(self, reqId, contractDetails):
        c = contractDetails.contract
        row = {**contract_record(c),"min_tick":contractDetails.minTick,"order_types":contractDetails.orderTypes.split(","),"market_rule_ids":contractDetails.marketRuleIds}
        # A query for SMART must actually resolve a SMART contract, not have its
        # returned exchange silently overwritten.
        self.contracts.setdefault(reqId,[]).append(row)
        self.contract_objects.setdefault(reqId,[]).append(c)

    def contractDetailsEnd(self, reqId):
        self.evt(reqId).set()

    def qualify(self, symbols=None):
        result, objects = {},{}
        symbols = sorted(self.policy["symbols"] if symbols is None else symbols)
        require(set(symbols)<=set(self.policy["symbols"]),"Unknown symbol in contract request")
        for symbol in symbols:
            req = self.request()
            c = Contract()
            c.symbol,c.secType,c.exchange,c.currency = symbol,"STK","SMART","USD"
            self.reqContractDetails(req,c)
            self.await_event(req)
            result[symbol] = validate_contract(symbol,self.contracts.get(req,[]))
            objects[symbol] = self.contract_objects[req][0]
        self.resolved, self.resolved_objects = result,objects
        self.ledger.observe("qualified_contracts",result)
        return result

    def marketDataType(self, reqId, marketDataType):
        with self.lock:
            self.quotes.setdefault(reqId,{})["market_data_type"] = marketDataType

    def tickPrice(self, reqId, tickType, price, attrib):
        side = {1:"bid",2:"ask",4:"last",66:"bid",67:"ask",68:"last"}.get(tickType)
        if side:
            with self.lock:
                q = self.quotes.setdefault(reqId,{})
                q[side],q[side+"_at"] = price,now_utc()
                if tickType in {66,67,68}:
                    q["market_data_type"] = 3

    def tickString(self, reqId, tickType, value):
        if tickType in {45,88}:
            with self.lock:
                self.quotes.setdefault(reqId,{})["last_exchange_timestamp"] = value

    def subscribe_quotes(self):
        self.reqMarketDataType(1)
        for symbol,c in self.resolved_objects.items():
            req = self.request()
            self.quote_symbols[symbol] = req
            self.reqMktData(req,c,"",False,False,[])

    def current_quotes(self, timeout=15):
        deadline = time.monotonic()+timeout
        while True:
            self.assert_session()
            with self.lock:
                quotes = {s:{**deepcopy(self.quotes.get(req,{})),"symbol":s,"timestamp_basis":"local receipt of live bid/ask callbacks; last exchange timestamp separate", "last":self.quotes.get(req,{}).get("last")} for s,req in self.quote_symbols.items()}
            try:
                require(set(quotes) == set(self.resolved), "Missing quote subscriptions")
                for q in quotes.values():
                    validate_quote(q,self.policy,now_utc())
                return quotes
            except BrokerSafetyError:
                if time.monotonic() >= deadline:
                    self.ledger.observe("invalid_market_data",quotes)
                    raise
                time.sleep(.2)

    def placeOrder(self, *args, **kwargs):
        raise BrokerSafetyError("Direct placeOrder disabled; use commissioned PAPER pipeline")

    def placeOrderProtoBuf(self, request):
        require(self._wire_context is not None,"Direct protobuf order transport disabled")
        order_id, account, what_if = self._wire_context
        require(request.orderId == order_id and request.order.account == account and request.order.whatIf == what_if,
                "SDK protobuf order differs from guarded order")
        self.assert_session(transmit=not what_if)
        return EClient.placeOrderProtoBuf(self,request)

    def _send_guarded(self, order_id, row, what_if=False):
        self.assert_session(transmit=not what_if)
        if not what_if:
            from .paper_autonomy import check_wire_arm
            check_wire_arm(self,self.armed_batch,self.armed_plan)
        account = self.cfg["expected_account"]
        require(self._wire_context is None,"Nested order transport rejected")
        self._wire_context = (order_id,account,what_if)
        try:
            return EClient.placeOrder(self,order_id,self.resolved_objects[row["symbol"]],make_order(row,account,what_if))
        finally:
            self._wire_context = None

    def what_if(self, plan, batch, snapshot, row):
        self.assert_session()
        require(load_forecast(batch["id"]) == batch, "Preview requires the immutable forward ledger batch")
        require(row in plan["orders"], "Preview must match a generated order")
        validate_plan(plan,batch,snapshot,self.resolved,self.current_quotes(),self.cfg,self.policy,now_utc(),submission=True)
        with self.ledger.lock:
            previous = self.ledger.db.execute("SELECT at FROM observations WHERE kind='what_if_requested' ORDER BY at DESC LIMIT 1").fetchone()
        if previous:
            require((stamp(now_utc())-stamp(previous[0])).total_seconds() >= self.policy["what_if_min_interval_seconds"], "What-if pacing: wait at least 61 seconds")
        order_id = self.alloc_order_id()
        self.preview_ids.add(order_id)
        self.ledger.observe("what_if_requested",{"plan_id":plan["id"],"order":row,"order_id":order_id})
        self._send_guarded(order_id,row,True)
        try:
            self.await_event(order_id)
            state = self.previews[order_id]
            require(not state["warningText"] and not state["rejectReason"], "IBKR preview warning/rejection requires review")
            require(number(state["initMarginAfter"]) >= 0 and number(state["equityWithLoanAfter"]) >= number(state["initMarginAfter"]), "Preview margin check failed")
            require(state["commissionAndFeesCurrency"] == "USD", "Unknown preview commission currency")
            fee = number(state["commissionAndFees"])
            require(0 <= fee <= row["estimated_cost"], "Preview commission exceeds reserved cost")
            result = {"at":now_utc(),"plan_id":plan["id"],"order_key":row["key"],"state":state,"what_if":True}
            self.ledger.observe("what_if_passed",result)
            return result
        finally:
            # Cancels only this known simulation ID, never actual/user orders.
            if self.isConnected() and order_id in self.preview_ids:
                EClient.cancelOrder(self,order_id,OrderCancel())

    def transmit_batch(self, plan, batch, snapshot, previews):
        from .paper_cli import require_tested
        require_tested()
        self.assert_session(transmit=True)
        require(load_forecast(batch["id"]) == batch, "Transmission requires the immutable forward ledger batch")
        self.clock_check()
        quotes = self.current_quotes()
        validate_plan(plan,batch,snapshot,self.resolved,quotes,self.cfg,self.policy,now_utc(),submission=True)
        require({p["order_key"] for p in previews} == {o["key"] for o in plan["orders"]}, "Every order requires its exact successful what-if preview")
        for preview in previews:
            require(preview["plan_id"] == plan["id"] and preview["what_if"] is True, "Preview/plan mismatch")
            age(preview["at"],now_utc(),self.policy["preview_max_age_seconds"],"what-if preview")
            with self.ledger.lock:
                saved = self.ledger.db.execute("SELECT 1 FROM observations WHERE kind='what_if_passed' AND payload=?",(canonical(preview),)).fetchone()
            require(saved is not None,"Preview must have been recorded by the broker adapter")
        # IDs are persisted as a single atomic reservation before any wire call.
        from .paper_autonomy import consume_for_transmission
        self.arm_authorization = consume_for_transmission(self,batch,plan,snapshot)
        self.armed_batch, self.armed_plan = batch, plan
        first = self.next_id
        self.ledger.reserve(plan,self.cfg["client_id"],first)
        self.next_id += len(plan["orders"])
        for offset,row in enumerate(plan["orders"]):
            self.assert_session(transmit=True)
            validate_plan(plan,batch,snapshot,self.resolved,self.current_quotes(timeout=0),self.cfg,self.policy,now_utc(),submission=True)
            order_id = first+offset
            reserved = self.ledger.once_submitting(self.cfg["client_id"],order_id)
            require(reserved == row, "Reserved order mismatch")
            self._send_guarded(order_id,row)
            self.ledger.event("SUBMITTED",self.cfg["client_id"],order_id,{"order_ref":row["order_ref"],"meaning":"socket call returned; acknowledgement/fill not implied"})
        return [first+i for i in range(len(plan["orders"]))]
