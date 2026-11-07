# gs-quant

**Repository:** https://github.com/goldmansachs/gs-quant · **Read on:** 2026-11-06-batch2 ·
**Licence:** Apache License 2.0 — read from the repository's own `LICENSE` (lines 2-3,
"Apache License / Version 2.0, January 2004"); `NOTICE.txt` adds "This product contains code
copyright Scott Weinstein, licensed under Apache 2.0 license", and every source file carries
the Apache header (for example `gs_quant/backtests/backtest_engine.py:1-15`). Permissive:
borrowing code is allowed with attribution and notice.

> **How this record was produced, and where to distrust it.** Dimensions 1 and 2 come from
> the analyst stage and arrived with their files and quoted evidence. The payload handed to
> this writer was truncated after dimension 2 — the cross-check verdicts did not arrive at
> all, and no claims arrived for dimensions 3 to 8. So: **every claim below was re-opened in
> the clone by the writer** (that is what the "did not survive" section at the end is built
> from), and dimensions 3 to 8 were answered by reading the clone directly rather than
> through the analyst/verifier pair. Dimensions 3 and 8 overlap heavily with claims that did
> arrive, and those are marked. A dimension answered by one agent reading is weaker evidence
> than a dimension answered by one agent reading and a second re-opening the file, and the
> ones below are the former.

## What it is, in a paragraph

gs-quant is Goldman Sachs' public Python client for its Marquee platform: instruments as
dataclasses, risk measures as named objects, datasets as a query API, and three backtesting
engines on top. The decisive fact about it is that **it is a client, not a library of
models**. `README.md:7` — "In order to access the APIs you will need a client id and secret.
These are available to institutional clients of Goldman Sachs." Pricing, resolution of an
instrument's unspecified economics, greeks, implied volatility and the server-side
`EquityVolEngine` are all HTTP calls to `/risk/calculate` (`gs_quant/api/gs/risk.py:89`) and
the backtest API. `gs_quant/models/` contains `epidemiology.py`, `risk_model.py` and
`risk_model_utils.py` — there is no option pricing model in the repository at all. The
consequence for a benchmark is sharp: the numerical quality of its greeks, the realism of
its fills and the correctness of its P&L attribution are **not in the artifact** and cannot
be evaluated from it, not merely "not run". What *is* reviewable, and what is worth reading
it for, is the shape of the API: how a relative trade spec becomes a dated contract, how
provenance travels with a risk number, how a strategy is composed from triggers and actions,
and how costs are modelled as composable units.

## The eight dimensions

### 1 · Data model and option chains

**There is no option-chain concept.** An option is a single dataclass instance:
`gs_quant/target/instrument.py:588-614` (`class EqOption`) has 26 fields, essentially all
optional — `underlier`, `expiration_date`, `strike_price`, `option_type`, `option_style`,
`number_of_options`, `exchange`, `multiplier`, `settlement_date`, … — so an option whose
economics are left unspecified is a valid object. There is no per-contract-day row, no
contract universe, and no `snapshot(day)` analogue. The single occurrence of the word as a
chain of options is a dead enum member: `gs_quant/target/trades.py:422`
`OptionChain = 'OptionChain'`, a member of `MqexsPricingType` ("The source to be used to get
a quote"), referenced nowhere else in the tree.

**The universe is never enumerated because the strike and expiry are a relative formula that
the server resolves.** `gs_quant/skills/gs-quant-overview/instruments.md:3` — "any parameter
you omit will be resolved by the server later"; `:12` gives the equity syntax: `'ATMS'`,
`'ATMF'`, `'95%'`, `'1.05*ATMF'`, `'25d'`, `'p=10%'`. The notebook
`gs_quant/documentation/02_pricing_and_risk/00_instruments_and_measures/examples/03_eq/01_calc_eq_option_price.ipynb`
builds `EqOption(..., expiration_date='3m', strike_price='ATMS+10%')`, calls `.resolve()`,
then reads `.strike_price`; it also contains `'P=10%'` and `'Price=1000'` — a strike solved
for a target premium, which only a server holding a vol surface can do. A listing or expiry
calendar is therefore never represented locally.

**Resolution is a network round-trip**, so "what contract am I holding" has no offline
answer: `gs_quant/instrument/core.py:74-110` ends in
`return self.calc(ResolvedInstrumentValues, fn=handle_result)` — resolution is just another
risk measure — and `gs_quant/api/gs/risk.py:89` is
`return '/risk/calculate{}'.format('/bulk' if is_bulk else '')`.
`gs_quant/skills/gs-quant-overview/SKILL.md:63-67` states it plainly: "Sends the instrument
to the GS analytics service along with the current `PricingContext`. The service computes
any missing parameters."

**Provenance travels with the object, which is better than our row flags.**
`gs_quant/base.py:623-629`: `new_instrument.__unresolved = copy.copy(self)` and
`new_instrument.__resolution_key = resolution_key`, where
`gs_quant/base.py:128` is
`class RiskKey(namedtuple('RiskKey', ('provider', 'date', 'market', 'params', 'scenario', 'risk_measure')))`.
A resolved instrument keeps both its pre-resolution relative spec and the
(date, market, scenario) it was resolved under. Our chain row carries availability and
quality flags but not the unresolved form it came from. **They are better here.**

**Units and keys are enforced at aggregation rather than recorded for inspection.**
`gs_quant/api/gs/backtests_xasset/response_datatypes/risk_result_datatypes.py:38-40`:
`if self.unit != other.unit: raise ValueError(f'Cannot aggregate risk results with different units: ...')`;
`gs_quant/risk/results.py:191` compares `historical_risk_key(...).ex_measure`, and
`:326-328` raises `ValueError('Results must have matching scenario and location')`.
**They are better here** — arithmetic that refuses mismatched provenance is a stronger
guarantee than a column a reader may ignore.

**The per-date structure is positions, never availability.**
`gs_quant/backtests/backtest_objects.py:106`:
`self._portfolio_dict = defaultdict(Portfolio)  # portfolio by state`; and the service
response is the same shape — `gs_quant/api/gs/backtests_xasset/response.py:65-67`:
`portfolio: dict[dt.date, tuple[Instrument, ...]]`. Nothing in the repository can answer
"what could I have bought on this date".

**A rolling option strategy gets a new strike per roll** because each trade is resolved under
a `PricingContext` pinned to its trigger date and then frozen:
`gs_quant/backtests/generic_engine_action_impls.py:69-72`
(`for s in states: ... with PricingContext(pricing_date=s): orders[s] = Portfolio(active_portfolio).calc(...)`),
and `gs_quant/skills/gs-quant-overview/backtesting.md:216-218` says so in words.

**The absolute strike a relative spec produced is not kept in the result.** To see it you ask
the server for a separate measure per date: `gs_quant/target/backtests.py:26-44`
(`BacktestRiskMeasureType` with `Strike_Level`, `Implied_Volatility`,
`Strike_Level_Adj_Implied`, `Strike_Level_Adj_Realised`, `Barrier_Level`, `Price_ATMS`), and
`backtesting.md:547-551` documents the request shape. **We are better here**: our chain row
*is* the record.

**Greeks and IV are remote named measures, defined as repricings.**
`gs_quant/target/measures.py:313` —
`EqAnnualImpliedVol = RiskMeasure(..., measure_type=RiskMeasureType("Annual Implied Volatility"), unit=RiskMeasureUnit("Percent"))`;
`:317` `EqDelta.__doc__ = "Change in Dollar Price (USD present value) due to individual 1% move in the spot price..."`;
`:323` `EqGamma.__doc__ = "Change in EqDelta for a 1% move..."`; `:332` `EqVega` for "individual
1bp moves in the implied volatility". A 1%-spot-move delta in dollar PV is a bump-and-reprice,
not an analytic partial. On the data side IV is a surface, not a contract attribute:
`gs_quant/data/fields.py:128-137` is the entire dimension vocabulary (`ASSET_ID`, `NAME`,
`RIC`, `TENOR`, `STRIKE_REFERENCE`, `RELATIVE_STRIKE`, `EXPIRATION_DATE`, `UPDATE_TIME`), and
`gs_quant/timeseries/measures.py:949` builds
`where = dict(tenor=tenor, strikeReference=ref_string, relativeStrike=relative_strike)`.
There is no per-contract IV in this representation.

**No pricing model ships with it.** `gs_quant/models/` holds only `epidemiology.py`,
`risk_model.py`, `risk_model_utils.py`. The only Black-Scholes expression in the whole tree is
`def black_sholes_raw_gamma(spot, strike, T, df_1, df_2, volatility)` inside the tutorial
notebook `gs_quant/documentation/02_pricing_and_risk/00_instruments_and_measures/examples/02_fx/10_fx_gamma_measures.ipynb`
— written to compare against the service's number, not to produce one. **We are better here**:
our `pricing.py` / `american.py` are the model, and they can be read, tested and argued with.

**Underlying identity is genuinely temporal — for underlyings only.**
`gs_quant/markets/securities.py:330-336` defaults `as_of` to the current PricingContext's
pricing date; `:344-351` filters cross-references by `start_date <= as_of <= end_date`. The
docstring at `:306` is explicit: "Where the identifiers are temporal (and can change over
time), this function will return the identifiers as of that point in time." **They are better
here** — this is the contract-master-with-history our chain lacks. There is no equivalent for
option contracts.

**A dataset's universe is a flat coverage list.** `gs_quant/data/dataset.py:397-425`:
`include_history: bool = False` / ":param include_history: Return column for
historyStartDate", scrolling `/data/{dataset_id}/coverage` (`gs_quant/api/gs/data.py:458`).
`historyStartDate` alone cannot express "this contract existed between these two dates", so
point-in-time universe membership is not representable. **We are better here.**

**Positions that fail resolution are dropped, not refused.**
`gs_quant/markets/position_set.py:692-694` keeps only `p.asset_id is not None` and assigns
`self.positions = resolved_positions`; `:684` stashes the rest in `__unresolved_positions`;
`:1079-1083` logs at INFO: "Error in resolving the following identifiers: ... Sifting them out
and resolving the rest...". Nothing raises; the caller must know to ask
`get_unresolved_positions()`. Resolution is at least dated —
`GsAssetApi.resolve_assets(..., as_of=date, ...)` at `:1065-1066`, the PositionSet's own date.
**We are better here**: our rule is that a row is not dropped.

**The listed-versus-OTC distinction is a two-member enum forwarded to the service.**
`gs_quant/api/gs/backtests_xasset/response_datatypes/backtest_datatypes.py:61-78`:
`class RollDateMode(Enum): OTC / Listed` and `class ExpiryDateMode(EnumBase, Enum): OTC /
Listed`, passed into `Configuration(...)` at `gs_quant/backtests/strategy_systematic.py:181-188`.
Its behaviour is not in the repository. One checked-in recorded response shows the outcome:
`gs_quant/test/calc_cache/request04e5386077a564977740976627ab683d.json`
(`request_id: "1_Action1_callResolvedInstrumentValues2021Dec01"`) carries
`"tradeAs": "OTC"`, `"expirationDate": "2022-12-01"`, `"strikePrice": 4179.15` — a trade
resolved on 2021-12-01 came back as an OTC leg with an exact-anniversary expiry, which no
listed calendar would give.

### 2 · Fill and cost realism

**There is no bid, no ask, and no spread anywhere in the fill path.**
`gs_quant/backtests/core.py:48-49`: `class ValuationFixingType(EnumBase, Enum): PRICE = 'price'`
— one member — and `ValuationMethod(data_tag=ValuationFixingType.PRICE, window=None)` at
`:52-54` is the only valuation knob. Repo-wide, bid/ask appear only as
`gs_quant/timeseries/measures_rates.py` (a swap-rate mid helper) and
`gs_quant/timeseries/measures_risk_models.py` ('Bid Ask Spread' as a factor descriptor);
nothing under `gs_quant/backtests/` reads either. No engine can distinguish a buy price from a
sell price. **We are better here** — our `check_tradable`, crossed-quote refusal and
relative-spread cap have no counterpart because there is no quote to inspect.

**In the GenericEngine an entry or exit fills at the model Price.**
`gs_quant/backtests/generic_engine.py:89`: `def __init__(self, action_impl_map=None, price_measure=Price)`;
`_handle_cash` then does `with PricingContext(cash_date): cash_results[cash_date] = Portfolio(trades).calc(price_risk)`
(`:678-684`). The transaction cost is a separate additive cash charge, not a worse fill:
`gs_quant/backtests/generic_engine.py:331-335`
(`backtest.transaction_costs = {d: -sum(tce.get_final_cost() for tce in tce_list) ...}`).

**Every cost default is zero.** `gs_quant/backtests/actions.py:50-51`:
`def default_transaction_cost(): return ConstantTransactionModel(0)`; `:66`
`_transaction_cost = ConstantTransactionModel(0)`; `:161-164` in `AddTradeAction.__post_init__`:
`if self.transaction_cost is None: self.transaction_cost = ConstantTransactionModel(0)`, and
`if self.transaction_cost_exit is None: self.transaction_cost_exit = self.transaction_cost`.
All ten Action classes take `transaction_cost: TransactionModel = field(default_factory=default_transaction_cost)`.
A backtest written without opting in to a cost model is frictionless. **We are better here**,
because our defaults sit at the pessimistic end.

**Their cost vocabulary is more composable than ours.**
`gs_quant/backtests/backtest_objects.py:542-595`: `ConstantTransactionModel(cost=0)`;
`ScaledTransactionModel(scaling_type='notional_amount' | RiskMeasure, scaling_level=0.0001)`,
which for a risk measure does `with PricingContext(state): risk = instrument.calc(self.scaling_type)`
— so a cost can be a cent per vega or a percentage of premium; and
`AggregateTransactionModel(transaction_models, aggregate_type=SUM|MAX|MIN)`. MAX of a fixed and
a scaled model reproduces a per-order minimum commission, which we hard-code. **They are
better here.**

**No engine has a decision-to-fill lag and none can be configured.**
`gs_quant/backtests/actions.py:119-120` (AddTradeAction): "The trades are resolved on the
trigger date (state)"; `:387-389` says the same for HedgeAction. The only lag-shaped parameter
in the package is `EventTriggerRequirements.offset_days` (`gs_quant/backtests/triggers.py:408`),
which shifts an economic-calendar event date and is not an execution lag. **We are better
here** — the one-day lag is a refusal, not a missing feature.

**The same bar that produces the signal fills it.**
`gs_quant/backtests/triggers.py:173-179`: `data_value = self.data_source.get_data(state)` then
`check_barrier(self.direction, data_value, self.trigger_level)`; `:191-201`
`risk_value = backtest.results[state][self.risk].aggregate()`. The matching entry is booked on
the same date — `gs_quant/backtests/generic_engine_action_impls.py:115-120`:
`CashPayment(inst, effective_date=create_date, direction=-1, ...)` — and an exit is rewritten
onto the trigger date at `:497-498`: `cp.effective_date = s`.

**The PredefinedAssetEngine fills at the signal timestamp itself.**
`gs_quant/backtests/predefined_asset_engine.py:51-58`:
`OrderAtMarket(instrument=pricable, quantity=quantity, generation_time=state, execution_datetime=state, source=...)`.
In `_run` (`:218-251`) `fills = self.execution_engine.ping(state)` runs *before* the
`MarketEvent()` that generates orders, so the order is processed one tick later but priced at
`data_handler.get_data(self.execution_datetime, ...)` — the `state` price
(`gs_quant/backtests/order.py:176-181`). The clock guard only forbids reading *later* than now
(`gs_quant/backtests/data_handler.py:42-52`), so a same-timestamp fill is permitted by design.

**They do model orders as first-class objects, which we do not.**
`gs_quant/backtests/order.py:73-219`: `OrderTWAP` (`fixings = data_handler.get_data_range(...)`,
`self.executed_price = np.mean(fixings)`), `OrderMarketOnClose`, `OrderCost`, `OrderAtMarket`,
and `OrderTwapBTIC` (`self.executed_price = close + btic_twap`). **They are better here** on
vocabulary — though all five take their price from the same single `ValuationFixingType.PRICE`
with no spread, so the vocabulary is richer than what it can express.

**There is no tradability gate: an unavailable price is a crash, not a refusal with a reason.**
`gs_quant/backtests/order.py:48-53`:
`price = self._execution_price(data_handler); if np.isnan(price): raise RuntimeError('can not compute the execution price')`.
The GenericEngine's equivalent is also a crash —
`gs_quant/backtests/generic_engine.py:700-713`:
`raise RuntimeError(f'failed to get cash value for {trade.name} on {cp.effective_date} ...')`.
**We are better here.**

**Costs never reach the per-trade record.** `gs_quant/backtests/backtest_objects.py:265-302`
(`trade_ledger`) builds rows keyed `'Open','Close','Open Value','Close Value','Long Short','Status','Trade PnL'`
with `Trade PnL = Close Value + open_value`, both sides being `sum(cash.cash_paid.values())` —
no cost term. Cost appears only as a cumulative column on the equity curve: `:246-251`
`transaction_costs.sort_index().cumsum()` and
`df[TOTAL] = df[price_measure] + df[CUMULATIVE_CASH] + df[TRANSACTION_COSTS]`. The
PredefinedAssetBacktest ledger is the same shape: `(end_value - open_value) * long_or_short`
at `:857`, where both values are `open_order.executed_price`. Every per-trade PnL they report
is gross of cost. **We are better here.**

**The PredefinedAssetEngine has, in practice, no transaction cost, and the accessor is broken.**
`gs_quant/backtests/backtest_objects.py:899-904` (`get_costs`) calls
`order.execution_quantity(self.data_handler)`, while every definition is
`def execution_quantity(self) -> float` (`gs_quant/backtests/order.py:55, 95, 122, 146, 183`)
— a `TypeError` on any call. `OrderCost` is never constructed anywhere outside its own
definition; the engine's action handler only ever builds `OrderAtMarket`
(`gs_quant/backtests/predefined_asset_engine.py:51-69`). `OrderCost._execution_price` returns
0 (`gs_quant/backtests/order.py:141-144`) with the charge carried as a `Cash` holding marked at
`fixing = 1` (`gs_quant/backtests/backtest_objects.py:874-875`). Read, not run.

**A risk-scaled cost silently becomes NaN in the future and is then zero-filled.**
`gs_quant/backtests/backtest_objects.py:569-571`: `if state > dt.date.today(): return np.nan`
inside `ScaledTransactionModel.get_unit_cost`; `result_summary` then does
`transaction_costs.sort_index().cumsum()` followed by
`pd.concat([...]).ffill().fillna(0)` (`:246-248`) — the cumsum propagates the NaN and
`fillna(0)` erases it. A cost model can stop charging rather than raise. Read, not run.

**They model cash financing, which we do not.**
`gs_quant/backtests/backtest_objects.py:914-992`: `ConstantCashAccrualModel(rate=0)` with
`value * (1 + rate/365) ** days`; `DataCashAccrualModel` off a user series; and
`OisFixingCashAccrualModel`, which builds
`IRSwap(notional_currency=currency, floating_rate_frequency='1b', floating_rate_option='OIS')`,
calls `swap.calc(Cashflows)` and feeds the fixings into a `DataCashAccrualModel`. Applied only
when supplied: `current_value[0] if cash_accrual is None else cash_accrual.get_accrued_value(...)`
(`gs_quant/backtests/generic_engine.py:693-696`). **They are better here.**

**The server-side engine accepts an option-specific cost dimension we cannot express.**
`gs_quant/backtests/equity_vol_engine.py:414-431` maps client cost models into the service's
schema, including `if model.scaling_type == EqVega: scaling_quantity_type = TransactionCostScalingType.Vega`,
where `class TransactionCostScalingType(Enum): Quantity / Notional / Delta / Vega`
(`.../backtest_datatypes.py:79-83`). The service also keeps price and cost separate:
`class Transaction: portfolio_price: Optional[float]; cost: Optional[float]`
(same file, `:97-105`). **They are better here** — cost per unit of vega is the natural unit
for an options cost model.

**A real institutional impact model exists only as a request schema, and no backtest touches it.**
`gs_quant/target/risk.py:59-66` (`class OptimizationUrgency` — "Parameter which controls the
urgency of executing the basket ... whereas Very Low would minimise cost, carrying a
relatively higher risk"), `:240-249` (`total_cost_spread`, `total_cost_volatility`,
`total_cost_permanent`, `total_cost`, `adv_percentage`), `:408-414` (`participation_rate`).
It is reached only through `GsRiskApi.create_pretrade_execution_optimization`, posting to
`/risk/execution/pretrade` (`gs_quant/api/gs/risk.py:336-338`). `grep -rn "pretrade\|participation"
gs_quant/backtests/` returns nothing: no backtest in this repository is affected by market
impact or capacity.

**The one local backtest that charges turnover also defaults to zero and rebalances at the
price that set the weights.** `gs_quant/timeseries/backtesting.py:56-63`:
`def backtest_basket(series, weights, costs: list = None, rebal_freq=RebalFreq.DAILY): ... costs = costs or [0] * num_assets`;
`:123-131` subtracts `np.dot(costs.values[i,], np.abs(weights.values[i,] - actual_weights_arr[i,])) * output_arr[i]`
and then immediately sets `units_arr[i,] = output_arr[i] * weights.values[i,] / series.values[i,]`
— the same index `i`. The public `Basket` class never passes costs at all:
`results = backtest_basket(spot_series, self.weights, rebal_freq=self.rebal_freq)` (`:259`),
so stock-basket backtests are unconditionally frictionless.

### 3 · Look-ahead protection

*Partly from arriving claims, partly read at write time.*

**One engine of three has a real mechanism.** `gs_quant/backtests/data_handler.py:22-52` is a
`Clock` that refuses both directions of error: `:35-36`
`if time < compare_time: raise RuntimeError(f'current time is {compare_time}, cannot run backwards to {time}')`,
and `:51-52` `if lookahead: raise RuntimeError(f'accessing data at {state} not allowed, current time is {self._time}')`.
Every read goes through it — `:74-81`:
`def get_data(self, state, *key): self._clock.time_check(state)` and the same for
`get_data_range` (checking `end`). This is a mechanism, not a convention, and it is the
closest thing in the repository to our windowed view.

**It is wired to one engine only.** `DataHandler` is constructed in exactly one place:
`gs_quant/backtests/predefined_asset_engine.py:129`
(`self.data_handler = DataHandler(data_mgr, tz=tz)`). The `GenericEngine` — the flagship,
and the one the skill documents — never builds one, and its triggers read the decision date's
own value with no guard and no lag (`gs_quant/backtests/triggers.py:173-179`, `:356-358`).
**We are better here**: our protection is not optional per engine.

**The guard permits a same-timestamp fill by construction.** `time_check` raises only when
`state > self._time`, so reading the current state is allowed; combined with
`execution_datetime=state` (`gs_quant/backtests/predefined_asset_engine.py:51-58`) the fill is
at the decision-time price. That is a deliberate boundary, not an oversight.

**One rolling-window asymmetry worth knowing.**
`gs_quant/backtests/data_sources.py:170-171`: `GenericDataSource.get_data_range(start, n)` with
an integer `n` returns `self.data_set.loc[self.data_set.index < start].tail(n)` — strictly
*before* the state date — while `get_data(state)` returns the state's own point
(`:123-145`). So `MeanReversionTriggerRequirements` (`gs_quant/backtests/triggers.py:356-358`)
computes its rolling mean and standard deviation from history excluding today and compares
today's own price against them. `GsDataSource.get_data_range` has the same shape
(`gs_quant/backtests/data_sources.py:92-94`).

**Forward-looking guards exist; backward-looking ones do not.**
`gs_quant/markets/core.py:163-170` refuses a pricing date more than five days in the future
("Please use the RollFwd Scenario..."), and `:172-188` refuses a market dated in the future.
The comment at `:164-165` is candid: "We allow a small tolerance to rolling over
weekends/holidays / We should use a calendar but not everyone has access". There is no
counterpart that asks whether a row was *knowable* on a past date.

**The one availability rule is wall-clock, not historical.**
`gs_quant/markets/markets.py:60-89` (`close_market_date`):
`now_time = dt.datetime.now().astimezone(location_tz)...`, `roll_time = dt.datetime(date...) + timedelta(hours=hr_offset, minutes=min_offset)`,
`if now_time < roll_time: date = prev_business_date(date)`, with
`roll_hr_and_min = (24, 0)  # tuple of today's expected hr/min of market data availability in the location tz`
at `:146`. This answers "is today's close out yet", never "was this row known on 3 March 2019".

**A genuine bitemporal read exists on the data API and the backtest never uses it.**
`gs_quant/data/dataset.py:171-211` takes `as_of: Optional[dt.datetime]` (":param as_of: Request
data as_of"), which becomes `as_of_time` on the wire (`gs_quant/api/data.py` →
`gs_quant/target/data.py`: `as_of_time: Optional[datetime.datetime]`), and
`gs_quant/skills/gs-quant-overview/datasets.md:57` documents it as "Return data as it existed
at this point in time". But `GsDataSource`, the only backtest-facing reader, never passes it:
`ds.get_data(self.min_date, self.max_date, assetId=(self.asset_id,))`
(`gs_quant/backtests/data_sources.py:76`) and `ds.get_data(start, self.max_date, **kwargs)`
(`:90`). **We are better here** on use, though not on the capability.

**`as_of` means two different things in the same module.** On `get_data` it is knowledge time
(`gs_quant/data/dataset.py:171-211`); on `get_data_last` it is observation time —
`gs_quant/data/dataset.py:391`:
`query = self.provider.build_query(start=start, end=as_of, fields=fields, format='JSON', **kwargs)`,
with the docstring at `:374` reading "Get the last point for this DataSet, at or before
as_of". A caller reasoning about look-ahead has to know which function they are in. **We are
better here** on naming discipline.

**Adjusted prices: not answerable from this repository.** The adjustment vocabulary exists as
dataset fields only — `gs_quant/data/fields.py:42-49`: `ADJUSTED_ASK_PRICE`,
`ADJUSTED_BID_PRICE`, `ADJUSTED_HIGH_PRICE`, `ADJUSTED_LOW_PRICE`, `ADJUSTED_OPEN_PRICE`,
`ADJUSTED_CLOSE_PRICE`, `ADJUSTED_TRADE_PRICE`, `ADJUSTED_VOLUME` — and the adjustment is
performed vendor-side. `grep -rni "split\|back_adjust"` over `gs_quant/data/` and
`gs_quant/backtests/` returns no adjustment logic. **What would answer it:** the Marquee
dataset documentation for the adjusted-price fields, which is not in the repository, or a
response showing whether `adjustedClosePrice` for a past date changes after a later split.
Nothing in the clone checks it.

### 4 · Validation and overfitting control

**There is none in the backtest package.** `grep -rni "benchmark|buy_and_hold|random"` over
`gs_quant/backtests/*.py` returns nothing, and the package file list
(`gs_quant/backtests/`: `action_handler.py`, `actions.py`, `backtest_engine.py`,
`backtest_objects.py`, `backtest_utils.py`, `core.py`, `data_handler.py`, `data_sources.py`,
`decorator.py`, `equity_vol_engine.py`, `event.py`, `execution_engine.py`,
`generic_engine_action_impls.py`, `generic_engine.py`, `order.py`,
`predefined_asset_engine.py`, `strategy_systematic.py`, `strategy.py`, `triggers.py`) contains
no module for baselines, walk-forward, hold-out, bootstrap, deflated Sharpe or
multiple-testing correction. Nothing records how many configurations were tried.

**What a result is.** `gs_quant/backtests/backtest_objects.py:246-251` — a single equity curve
plus cumulative cash and cumulative cost columns — and `:265-302`, the trade ledger. The
server-side engine adds a P&L attribution (see dimension 8's note on
`FlowVolBacktestMeasure`), which is a different and better thing than a validation statistic.

**Statistics exist, one series at a time, outside the backtester.**
`gs_quant/timeseries/econometrics.py:219-245` is a rolling `sharpe_ratio(series, currency, w,
curve_type, method)`; `gs_quant/timeseries/measures_portfolios.py:615-649` and
`gs_quant/timeseries/measures_reports.py:898-938` compute portfolio Sharpe through the Marquee
reports API. `gs_quant/timeseries/` also carries `statistics.py`, `event_study.py` and an
`_event_study` package. None of these is reachable from a `Strategy` result; a user would
hand-carry the series across.

**What would answer this better:** the Marquee product documentation for the hosted
backtesting service, which may offer validation facilities the client does not expose. From
this repository the answer is that the client offers none. **We are better here** — this is
the dimension with the widest gap in our favour.

### 5 · Extensibility

**Two real extension points, both narrow and both clean.** First, the action-to-handler map:
`gs_quant/backtests/generic_engine.py:69-85` —
`self.action_impl_map = { AddTradeAction: AddTradeActionImpl, HedgeAction: HedgeActionImpl, ... } | (action_impl_map or {})`,
and `get_action_handler` raises
`RuntimeError(f'Action {type(action)} not supported by engine')` for anything unmapped. The
skill documents the intended use (`gs_quant/skills/gs-quant-overview/backtesting.md:218-220`:
"To keep a field unresolved, or to change how trades are built, subclass the action's handler
and map the action to it when you create the engine"). Second, a subclass registry used for
serialisation: `Action.sub_classes()` (`gs_quant/backtests/actions.py:69-77`),
`TriggerRequirements.__sub_classes` (`gs_quant/backtests/triggers.py:59-63`) and
`DataSource.sub_classes()` (`gs_quant/backtests/data_sources.py:42-50`), consumed by
`dc_decode(*Trigger.sub_classes(), allow_missing=True)` in
`gs_quant/backtests/strategy.py:48-50`. Registration is by `__init_subclass__`, so defining a
subclass is the whole of it.

**Engine selection is capability-based, which is a good idea.**
`gs_quant/backtests/strategy.py:30-34` lists `[GenericEngine(), PredefinedAssetEngine(),
EquityVolEngine()]` and `:66` returns
`[engine for engine in _backtest_engines() if engine.supports_strategy(self)]`. The
`EquityVolEngine` implements that check as an explicit list of refusals with messages —
`gs_quant/backtests/equity_vol_engine.py:145-290`: "Error: Maximum of 3 triggers", "Error:
Only AggregateTrigger and PeriodTrigger supported", "Error: Only EqOption or EqVarianceSwap
supported", "Error: HedgeAction: Hedge instrument must be a synthetic forward …". **They are
better here**: a strategy is matched to the engines that can run it, and an engine says in
words what it cannot do. That is close to what our interface tries to do when it names the
engine's limits.

**Adding a data source is small.** `gs_quant/backtests/data_sources.py:41-59` — a `DataSource`
base with `get_data` / `get_data_range`, two implementations (`GsDataSource:61`,
`GenericDataSource:99`), and `MissingDataStrategy` (`:33-39`: `fail`, `interpolate`,
`fill_forward`) with `fail` as the default (`:112`).

**What it does not offer:** no plugin discovery (no entry points, no registry file), and no
way to compare two candidate *forms* of a component on equal footing — the comparison the
`action_impl_map` enables is between two handler implementations a user writes and wires by
hand, with nothing recording that a comparison happened.

### 6 · Usability

**The first run needs institutional credentials.** `README.md:7`: a client id and secret
"available to institutional clients of Goldman Sachs. Please speak to your sales coverage or
Marquee Sales". `README.md:11-19` is `pip install gs-quant` and Python 3.10+. There is no
local-data path: the three engines all price through the service. For a reader without
Marquee access, the repository is documentation.

**There is no interface beyond a script.** The `.ipynb` surface is large — 307 notebooks under
`gs_quant/documentation/` and `gs_quant/content/` — and `README.md:22-28` points at
developer.gs.com for guides. The result object is a DataFrame
(`gs_quant/backtests/backtest_objects.py:246-258`, `:265-302`); it does not carry a record of
what it assumed, which is the thing our result header exists for.

**Error messages are a mixed picture, and the good half is worth copying.** The strategy
pre-flight check in `gs_quant/backtests/equity_vol_engine.py:145-290` accumulates readable
refusals ("Error: AggregateTrigger must be composed of 2 triggers", "Error:
EnterPositionQuantityScaledAction trade_quantity or trade_quantity_type is None") and reports
them together rather than failing on the first. Against that, the data-side failures are bare
`RuntimeError`s with no remedy — `gs_quant/backtests/order.py:48-53` ("can not compute the
execution price"), `gs_quant/backtests/generic_engine.py:700-713` ("failed to get cash value
for …"). The typed error hierarchy exists (`gs_quant/errors.py:20-60`: `MqError`,
`MqValueError`, `MqTypeError`, `MqRequestError` with status/message/context,
`MqAuthenticationError`, `MqAuthorizationError`, `MqUninitialisedError`) and the skill
package documents failures in `gs_quant/skills/gs-quant-overview/errors.md`, but the backtest
engines mostly raise untyped `RuntimeError`.

### 7 · Licence and health

**Licence: Apache 2.0**, from `LICENSE:2-3`, with `NOTICE.txt` declaring third-party
Apache-2.0 code. It permits what we would want — reading, borrowing with attribution,
modification — and it is the licence on each source file's header
(`gs_quant/backtests/backtest_engine.py:1-15`).

**Tests exist in volume:** 112 `test_*.py` files under `gs_quant/test/`, a root `conftest.py`,
and 155 recorded response cassettes in `gs_quant/test/calc_cache/`. The harness is explicit
about what it is: `gs_quant/test/utils/mock_request.py:101-102` fabricates a session
(`OAuth2Session._authenticate = mock.MagicMock(return_value=None)`;
`GsSession.use(Environment.PROD, 'fake_client_id', 'fake_secret', ...)`) and patches one
method, and `gs_quant/test/utils/mock_calc.py:61-64` names it: `api = GsRiskApi; method = '_exec'`.
`mock_calc` then returns a checked-in JSON file keyed by a request hash
(`gs_quant/test/utils/mock_request.py:110-123`). So the tests verify request construction and
response handling. They do not — cannot — verify a price, a greek or a fill.

**Public CI runs no tests.** `.github/workflows/` contains exactly one file,
`python-publish.yml`, which builds and twine-uploads on a GitHub release. The test pipeline is
`.gitlab-ci.yml`, which includes `project: 'fp/open-source/os-ci'` and runs against
GS-internal infrastructure (`registry.aws.site.gs.com`, `pypi.site.gs.com`,
`GS_PYPI_URL: "http://pypi.site.gs.com/simple"`). An outside contributor cannot run that
pipeline, and no public CI result is produced for a pull request.

**Release cadence, last commit date, contributor count and open issue count: not determinable
from the clone.** It is a `--depth 1` clone (`.git/shallow` present), so the only commit in it
is `8725ddf Chore: Make release 2.2.5`, and the version is computed by `versioneer`
(`setup.py:19-23`) rather than written down. **What would answer it:** the GitHub API for
commits, releases, issues and contributors — a network call this workflow does not make.
Treat "worth learning from" as established and "worth depending on" as unmeasured.

### 8 · AI and agent integration

*From arriving claims, re-opened at write time. This is the dimension where they are
furthest ahead of us.*

**They ship a Claude-Code skill package.** `gs_quant/skills/gs-quant-overview/` holds ten
markdown files — `SKILL.md` plus `pricing.md`, `instruments.md`, `risk-measures.md`,
`results.md`, `datasets.md`, `backtesting.md`, `scenarios.md`, `errors.md`, `measure.md` —
installed by a CLI: `python -m gs_quant.skills install --global`
(`gs_quant/skills/README.md:9-12`), which **symlinks** `.claude/skills/<name>` to the
directory inside the installed package so "Updating gs_quant updates the skill automatically"
(`:62`), copying instead on Windows (`:63`). Scope flags are `--global` (`~/.claude/skills/`)
and `--project`; adding a skill needs no registration step (`:101`). Its own README marks it
"Experimental" (`:3`). The content is substantive rather than a README restated — the
strike-syntax table at `instruments.md:12` and the P&L-attribution arithmetic at
`backtesting.md:541-546` are the kind of thing a model needs and a human reference usually
omits. **They are better here**, and the symlink-to-package idea is the specific thing worth
stealing: the skill cannot drift from the version installed.

**They ship an experimental MCP server with 16 read-only tools.**
`gs_quant/mcp/` (`run.py`, `client.py`, `config.py`, `dependencies.py`, `middleware.py`,
`session_utils.py`, `tools/registry.py`, and three tool packages). Counted from source:
`gs_quant/mcp/tools/data/tools.py` — `get_daily_data:29`, `get_intraday_data:46`,
`get_last_data:63`, `get_dataset_coverage:84`, `find_asset_identifiers:98`,
`get_underliers:141`; `gs_quant/mcp/tools/users/tools.py` — `current_user_info:26`,
`whois:47`; `gs_quant/mcp/tools/marketview/tools.py` — `search_dashboards:31`,
`get_dashboard:55`, `get_trending_dashboards:73`, `get_personalized_dashboards:85`,
`search_widgets:102`, `get_widget:128`, `get_trending_widgets:141`,
`get_personalized_widgets:153`. **Nothing prices, resolves, backtests or trades.** The
boundary is authentication rather than capability: `--auth local` shares one server-side
session with every caller and `--auth passthrough` builds a per-request `GsSession` from the
caller's cookie or bearer token (`gs_quant/mcp/README.md:119-153`, `:432-435`), with tags and
keys filterable at startup (`--enable-tags user,data`, `--disable-keys tool:current_user_info`
at `:108-113`, `:180-183`). Marked "Experimental." at `:3`.

**There is no automated research loop**, so the question that matters most on this dimension —
which split a model sees when it judges a result — does not arise. No tool runs a backtest, no
module proposes a hypothesis, and nothing counts trials or deflates for a search. `grep` for
`pretrade|participation` across `gs_quant/backtests/` returning nothing is one symptom of the
same shape: this is a client library with an agent-facing *documentation and read* surface,
not a research agent.

**Their P&L attribution is the one place our ledger has no counterpart.**
`gs_quant/target/backtests.py:80-100` (`class FlowVolBacktestMeasure`): `PNL_spot`, `PNL_vol`,
`PNL_carry`, `PNL_delta`, `PNL_gamma`, `PNL_higher_order_spot`, `PNL_higher_order_vol`,
`PNL_theta`, `PNL_transactions`, **`PNL_unexplained`**, `PNL_vega`, `PNL`, `delta`, `gamma`,
`vega`, `NAV`; and `gs_quant/skills/gs-quant-overview/backtesting.md:544-546` gives the
arithmetic — "`PNL` minus `PNL_spot`, `PNL_vol` and `PNL_theta` is the unexplained part."
A named residual is a discipline our two equity curves do not impose. It is computed
server-side and so cannot be checked from here. **They are better here** on the idea; the
number itself is unverifiable from the artifact.

## What was not checked out

Nothing was excluded by path: the clone at `../benchmark-clones/gs-quant` is the full tree
(no `core.sparseCheckout`, no `.git/info/sparse-checkout`), covering `gs_quant/` with
`backtests/`, `api/`, `data/`, `markets/`, `models/`, `risk/`, `target/`, `timeseries/`,
`skills/`, `mcp/`, `test/`, `documentation/` and `content/`.

Two real limits on the reading:

1. **It is a `--depth 1` clone** (`.git/shallow` exists; one commit, `8725ddf Chore: Make
   release 2.2.5`). All history-based health questions — cadence, last commit date,
   contributors — are unanswered for that reason, not because the files were missing.
2. **Its code was never executed**, per the workflow's rule. Two claims above are explicitly
   read-only inferences about a runtime path and are labelled as such: the
   `execution_quantity` `TypeError` (`gs_quant/backtests/backtest_objects.py:899-904`) and the
   NaN-then-zero cost path (`:569-571` with `:246-248`).

Beyond the clone, the part of gs-quant that does the work is not in it at all: pricing,
resolution, greeks, implied volatility and the `EquityVolEngine` run on Goldman's servers
behind institutional OAuth (`README.md:7`; `gs_quant/api/gs/risk.py:89`;
`gs_quant/api/gs/backtests_xasset/`). Fill realism, the numerical quality of the greeks and
the correctness of the P&L attribution are therefore not assessable from this repository —
absent from it, rather than merely unrun.

## Claims that did not survive the cross-check

**Read this first: the designated cross-check verdicts never reached this file.** The claims
payload was truncated before the `checked` array, so the verdicts recorded below are the
*writer's* re-opening of every cited file, not an independent verifier's. Every citation in
the dimensions above was opened; the discrepancies found were all of degree, and none of them
overturned a dimension's conclusion. That is a weaker result than a real cross-check would
be, and the next run should not read it as one.

1. **"EqOption … 25 optional fields, every one defaulting to None" — overstated.**
   `gs_quant/target/instrument.py:588-614` has 26 fields, and three do not default to `None`:
   `premium: Optional[float] = field(default=0.0, ...)` (`:600`), and `asset_class` / `type_`
   are `field(init=False, default=AssetClass.Equity / AssetType.Option)` (`:612-613`), so they
   are fixed rather than optional. A default premium of **0.0** is in fact the more alarming
   fact than `None` would be, and the claim's phrasing hides it. The substantive point — that
   an option's economics may be left unspecified — stands.

2. **"grep for `chain` returns only itertools.chain imports (portfolio.py:21, data.py:24)" —
   wrong as stated, right in conclusion.** Three files import it
   (`gs_quant/markets/portfolio.py:21`, `gs_quant/api/gs/data.py:24`,
   `gs_quant/risk/results.py`), there is an unrelated `def test_chaining()` at
   `gs_quant/test/datetime_/test_relative_date.py:231`, and — the one that matters —
   `gs_quant/target/trades.py:422` defines `OptionChain = 'OptionChain'` as a member of
   `MqexsPricingType` ("The source to be used to get a quote"), referenced nowhere else in the
   tree. So the repository does contain the token: as a dead quote-source label for the
   commodity-streaming API, not as a data structure. A future run should not repeat "no
   occurrences"; the correct statement is "one occurrence, an unused enum member".

3. **"`EqAnnualImpliedVol = RiskMeasure(...)`, `EqDelta`, `EqGamma`, `EqVega`" — overstated on
   type.** At `gs_quant/target/measures.py:316, 322, 331`, `EqDelta`, `EqGamma` and `EqVega`
   are `RiskMeasureWithCurrencyParameter`, not `RiskMeasure`; only `EqAnnualImpliedVol`
   (`:313`) is a plain `RiskMeasure`. The quoted docstrings are verbatim correct and the
   bump-and-reprice reading of them holds.

4. **"the transaction cost is a separate additive cash charge" — correct, but the exit-cost
   half was missing a line.** `gs_quant/backtests/actions.py:167-168` sets
   `if self.transaction_cost_exit is None: self.transaction_cost_exit = self.transaction_cost`,
   so an exit is never silently free when an entry cost was given. The zero-default claim
   remains true for the case where neither is supplied, which is the default.

5. **"DataHandler is constructed only by PredefinedAssetEngine (predefined_asset_engine.py:33
   import)" — right conclusion, wrong line, and incomplete.** `:33` is the import; the
   construction is at `gs_quant/backtests/predefined_asset_engine.py:129`. `DataHandler` is
   also *consumed* by `gs_quant/backtests/execution_engine.py:29`,
   `gs_quant/backtests/order.py:45` and `PredefinedAssetBacktest`
   (`gs_quant/backtests/backtest_objects.py:783`) — all of them inside that one engine's
   object graph, so "one engine only" survives.

6. **"MeanReversionTriggerRequirements likewise uses `get_data(state)` for the current price" —
   confirmed, and incomplete in a way that matters.** `gs_quant/backtests/triggers.py:356-358`
   reads the state's own price, but its rolling mean and standard deviation come from
   `get_data_range(state, window)`, which with an integer window returns points strictly
   *before* `state` (`gs_quant/backtests/data_sources.py:170-171`:
   `self.data_set.loc[self.data_set.index < start].tail(end)`). The window is therefore not
   contaminated; only the comparison price is same-bar. Stated without that, the claim reads
   as a worse defect than the code has.

7. **No claim came back `file_not_found`.** Every path cited in dimensions 1 and 2 exists in
   the clone, including the recorded response
   `gs_quant/test/calc_cache/request04e5386077a564977740976627ab683d.json` and the two cited
   notebooks. Line numbers were accurate to within a line or two throughout, with the single
   exception noted in item 5.
