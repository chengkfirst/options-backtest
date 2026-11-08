# ArcticDB

**Repository:** https://github.com/man-group/ArcticDB · **Read on:** 2026-11-06-batch2 ·
**Licence:** Business Source License 1.1 — read from the repository's own `LICENSE.txt`
(line 1, "**Business Source License 1.1**"; Licensor "Man Group Operations Limited" at :3;
Change License "Apache License, version 2.0" at :25). **Not permissive today.** The licence
converts per release version, two years after that version's own release date
(`LICENSE.txt:17-23`), capped at "the fourth anniversary of the first publicly available
distribution of a specific version" (`LICENSE.txt:33`). Borrowing code from a *converted*
version (≤ 5.0 as of today — see dimension 7) is an Apache-2.0 question; borrowing from the
current tree is not.

> **How this record was produced, and where to distrust it.** The payload handed to this
> writer carried claims for three dimensions only — data model, fill and cost realism, and
> look-ahead protection — and it was **truncated mid-claim** (the last look-ahead claim ends
> inside `std::lower_bound(std::begin(keys), std::end(keys), from_t`). **No cross-check
> verdict arrived with any claim**: not one claim object carried a verdict field. So every
> claim below was **re-opened in the clone by this writer**, and the "did not survive"
> section at the end is built from that re-reading rather than from a verifier stage.
> Dimensions 4, 5, 6 and 8 had no claims at all and were answered by reading the clone
> directly — one agent reading, not one reading and a second re-opening, and weaker evidence
> for it.
>
> A second payload fault worth recording: **every claim was labelled with the wrong
> dimension.** Licence and sizing claims arrived tagged `"data model and option chains"` or
> `"fill and cost realism"`; the look-ahead claims arrived tagged `"fill and cost realism"`.
> They are filed below under the dimension they actually belong to.

## What it is, in a paragraph

ArcticDB is a serverless, versioned, columnar **time-series store** — a Python API over a C++
storage engine writing to S3, Azure Blob, GCP, MongoDB, LMDB or memory. Its contract is
`README.md:32` — "**Pandas in, Pandas out**: Read and write Pandas DataFrames, NumPy arrays
and native types to S3 and LMDB without leaving Python" — and its target scale is
`README.md:33`, "Efficiently index and query time-series data across _billions_ of rows". It
is **not a backtester and contains no finance**: a grep over all 2,247 tracked files for
`slippage|commission|fill_price|execution_lag|bid_ask|market_impact` returns zero hits, as
does `backtest`, as does `black.?scholes|black76|binomial|newton`. The only option-pricing
words in the tree are two reads of a vendor `implied_volatility` column in one notebook
(`docs/mkdocs/docs/notebooks/ArcticDB_demo_equity_options.ipynb:224,423`). What makes it
worth benchmarking against is not what it computes but **two mechanisms it has and we do
not**: an immutable version chain per symbol with a named multi-symbol pin (snapshots), and
a query layer that pushes filters and arithmetic projections down into storage. And one
thing it demonstrates by accident: its own worked example for equity options chose, without
argument, **exactly our row grain** — one row per contract-day, indexed by quote date.

Read it as a storage comparison, not a platform comparison. The eight dimensions below are
mostly answered "absent, and out of scope for a store" — the record is kept so a later run
does not re-read 97 MB to learn that again.

## The eight dimensions

### 1 · Data model and option chains

**Their own option-chain example uses our row grain.** `ArcticDB_demo_equity_options.ipynb:188`
is `return raw_df.set_index(pd.DatetimeIndex(raw_df['quote_date'])).drop(columns=['Unnamed: 0'])`
— a long-form frame indexed by quote date, so many contract rows share one timestamp: one
row per contract-day. The symbol is one per underlying, not one for the universe:
`:190-191` `def uly_symbol(uly): return f"options/{uly}"`, written per date at `:293`
`lib.update(sym, uly_df, upsert=True)`. Measured in the shipped sample
(`docs/mkdocs/docs/notebooks/data/2013-06-03tech-options.csv`, 6,792 data rows, 11
underlyings): AAPL alone carries 2,370 contract-rows on that one date. **Neither better** —
it is the same decision, reached independently, which is the useful part.

**There is no chain object and no per-date container.** `docs/mkdocs/docs/tutorials/data_organisation.md:105-107`
— "* The primary and performant index is the first DataFrame row index. * The columns names
are also a performant index. * There are no secondary row or column indices." So
`contract`, `expiration`, `strike` and `type` are ordinary data columns, and every chain
query is a filter scan over the day's rows, exactly as their notebook does it at
`ArcticDB_demo_equity_options.ipynb:196` (`lazy_df['expiration'] != as_of`) and `:221-222`
(`abs(lazy_df['delta'])` bracketed). **Neither better** — our chain is a DataFrame too.

**A chain-object-per-date is expressible but unusable.** Recursive normalizers can store
nested structures, but `ArcticDB_demo_recursive_normalizers.ipynb` cell 30 states "Recursive
normalized data cannot be filtered. Attempting to filter such data will raise a
SchemaException", and cell 31 catches that exception and then catches a second one from
`lib.head("nested")`. So the long-form one-row-per-contract-day layout is effectively the
only workable option in this store. **Neither better.**

**Greeks and implied volatility are read from the vendor and never derived.** The sample
header (`data/2013-06-03tech-options.csv:1`) is
`,contract,underlying,expiration,type,strike,style,bid,bid_size,ask,ask_size,volume,open_interest,quote_date,delta,gamma,theta,vega,implied_volatility`,
and there is no solver, no Black-Scholes and no tree anywhere in the repository (grep above).
**We are better here** — `backtest/data/options/pricing.py:134-171` (`implied_vol`) solves
from the quote and returns an explicit absence with a reason rather than a silent zero.

**There is no provenance marking on a value, and no mechanism to carry one.** The store's
contract is "Pandas in, Pandas out" (`README.md:32`) with no reserved columns; the only
per-version annotation is free-form user metadata
(`python/arcticdb/version_store/library.py:1588` — `metadata: Any = None` in `update`'s
signature — and `:1625`, "Metadata to persist along with the new symbol version"). **We are
better here**: `backtest/core/types.py:229-230` reserves `quality_flags` and
`greeks_model_derived` on the row itself, and
`backtest/portfolio/scenario_rule.py:189,195,201` reads the latter.

**The vendor column set carries no underlying spot, so moneyness is not computable from a
row.** Measured on the sample: no `spot`, `mid` or `rho` column exists. That is why their own
example brackets the vol curve by **absolute delta** rather than by strike relative to spot —
`ArcticDB_demo_equity_options.ipynb:117-118` (`delta_low = 0.05`, `delta_high = 0.55`) and
`:221-222`. **We are better here**: `underlying_price` is a declared chain column
(`backtest/core/types.py`, within `CHAIN_COLUMNS` at :202ff).

**Columns the source cannot fill are kept rather than dropped, and the default static schema
then requires them forever.** In the sample, `bid_size` and `ask_size` are NaN on 6,792 of
6,792 rows (measured: `df.bid_size.isna().mean() == 1.0`), yet the notebook drops only
`'Unnamed: 0'` (`ipynb:188`) — so both all-NaN columns are written. `python/arcticdb/options.py:45`
is `dynamic_schema: bool = False`, and `:64-66` says what that means: "If False, then the
schema for a symbol is set on each `write` call, and cannot then be modified by successive
updates or appends. Each successive update or append must contain the same column set in the
same order with the same types as the initial write." Same keep-the-column policy as our
`CHAIN_COLUMNS`, mechanically enforced. **Neither better.**

**`update()` replaces the whole index range it spans.** `library.py:1597-1599` — "The entire
range between the first and last index entry in ``data`` is replaced in its entirety with the
contents of ``data``, adding additional index entries if required. `update` only operates over
the outermost index level - this means secondary index rows will be removed if not contained
in ``data``." Their own example makes the hazard explicit at `:1669`, "`# Note that 2018-01-02
is gone despite not being in update_df`". Exactly right for writing one whole day's chain —
which is what `ipynb:293` does — and silently destructive on a *partial* redelivery of a day.
**Neither better**, but the failure mode is worth remembering: a short redelivery deletes
contracts rather than erroring.

**Single-frame-for-everything is possible and loses the index.** `data_organisation.md:171` —
"The security identifier is included as an extra index column. Note that DataFrames with a
Pandas MultiIndex will round trip correctly, which is useful. However the high performance
ArcticDB indexing is only on the primary index level." So a `(quote_date, contract)`
MultiIndex gives no indexed access on `contract`, and multi-index updates are listed as
unbuilt at `:228` ("**Multi-Index:** support for more flexible data updates for multi-index
symbols."). **Neither better.**

**One symbol per underlying plus `list_symbols()` as the universe index is cleaner than our
single frame, and it is what their guide recommends.** `data_organisation.md:119-121` —
"* Prefer a smaller number of symbols that have a large amount of data each * Accessing date
ranges and subsets of columns is very efficient * Collecting together data from a large
number of symbols can be slower"; and `ArcticDB_demo_equity_options.ipynb:208-209`,
`def read_all_underlyings(): # use the symbol list to get all underlyings` /
`return sorted([s.split('/')[1] for s in lib.list_symbols()])`. The universe is enumerated
**from the store** rather than configured. **They are better here.**

**No contract master, no symbology, no corporate-action or OCC adjustment handling.** A grep
over `*.py`/`*.md`/`*.ipynb` for `corporate.action|symbology|ticker.chang|back.adjust|split.adjust|OCC`
returns no substantive hit. The `Library` API surface is write/append/update/read/snapshot/
delete plus admin tooling (`python/arcticdb/version_store/library.py`); nothing models an
instrument's identity over time. The same gap our platform records. **Neither better**, and
out of scope for a store.

**Writes are last-writer-wins with no isolation.** `docs/mkdocs/docs/faq.md:66` — "It ensures
atomicity, consistency, and durability but does not isolate transactions. Changes to symbols
are performed on a last-writer-wins principle, and without isolation, write-after-read
transactions are not supported, which is not suitable for use cases requiring strong
transactional guarantees." Reinforced at `library.py:1609`, "Note that `update` is not
designed for multiple concurrent writers over a single symbol." **Neither better** — a daily
chain ingest has no protection against a concurrent writer in either project.

### 2 · Fill and cost realism

**There is nothing to compare.** No execution model, no fill price, no spread, no commission,
no slippage, no capacity, no execution lag. Evidence is the absence itself:
`grep -ril -E 'slippage|commission|fill_price|execution_lag|bid_ask|market_impact'` over
`*.py`/`*.md`/`*.cpp`/`*.hpp`/`*.ipynb` returns **zero** hits across all 2,247 tracked files,
and `grep -ril backtest` returns zero. The C++ tree (`cpp/arcticdb/`) is codec, column_store,
pipeline, processing, storage, stream, version — there is no execution directory.
**Neither better**: a store is not expected to have one. Recorded so the next run does not
look for it again.

**`bid` and `ask` appear once in the tree, as arbitrary labels, with the spread inverted.**
`python/tests/unit/arcticdb/version_store/test_filtering.py:629`
`df = pd.DataFrame({"bid": np.arange(0, 20, 2, dtype=np.int64), "ask": np.arange(10, dtype=np.int64)})`,
then `:634-635` `spread = q["bid"] - q["ask"]` / `q = q[(spread > 0) & (spread < limit)]`. A
spread is ask minus bid; here `bid` is deliberately the larger series so the filter keeps
rows. The test is `test_filter_reused_derived_expression` — the names are decoration on an
expression-reuse test, not a quote model. **We are better here**, trivially.

**Their query layer can express a cost formula server-side; ours cannot.**
`python/arcticdb/version_store/processing.py:105-127` composes `ExpressionNode`s for
`__abs__`, `__neg__`, `__invert__`, `__add__`, `__sub__`, `__mul__`, `__truediv__`, `__pow__`;
`:289` `def where(condition, left, right)` is "Ternary operator choosing from the left
expression where condition is true, and from the right expression where it is false"; `:543`
`def apply(self, name, expr)` is "Apply enables new columns to be created using supported
QueryBuilder numeric operations." So a mid price, or `mid + lambda*(adverse-mid)`, is a
pushed-down projection. Ours is pandas in `backtest/execution/costs.py` and cannot be
evaluated inside the store. **They are better here** — on mechanism, not on content.

**Their query language has no row-offset operator, so a decision-to-fill lag is not
expressible in it.** `grep -rnE 'def (shift|cumsum|rolling|ffill|diff|pct_change)' python/arcticdb/`
returns nothing. The only stateful clauses are groupby/resample plus aggregations, and `agg`
raises otherwise — `processing.py:703`, `"Aggregation only makes sense after groupby or
resample"`. The full C++ clause vocabulary confirms it
(`cpp/arcticdb/processing/clause.hpp`: `PassthroughClause`, `FilterClause`, `ProjectClause`,
`PartitionClause`, `AggregationClause`, `ResampleClause`, `RemoveColumnPartitioningClause`,
`SplitClause`, `SortClause`, `MergeClause`, `ColumnStatsGenerationClause`, `RowRangeClause`,
`DateRangeClause`, `ConcatClause`, `WriteClause`, `MergeUpdateClause`, `CompactDataClause`,
`RenameColumnsClause` — no shift, lag, lead, cumulative or window). Our
`execution_lag_days` (`backtest/core/config.py:177`, default 1, required `>= 1` at `:191`)
has no counterpart that could live in their layer. **We are better here.**

**Their only option workflow applies no tradability test at all.** Across the 22 cells of
`ArcticDB_demo_equity_options.ipynb` the words **bid, ask, mid and spread do not appear once**
(measured by parsing the notebook JSON). The three queries are an expiry-volume rollup, an
expiry filter, and a delta-bracketed mean of `implied_volatility`; ingestion is the raw CSV.
**We are better here**: `backtest/execution/costs.py:40` `check_tradable(bid, ask, costs, *, flags)`
refuses an absent, non-positive or crossed quote and a relative spread over the configured
maximum, and reads the row's `quality_flags`.

**And their sample chain is full of rows nothing could trade.** Measured by reading
`data/2013-06-03tech-options.csv` directly (not by running their code): `(bid == 0).mean() = 0.105`,
`(volume == 0).mean() = 0.631`, `(open_interest == 0).mean() = 0.231`, median relative spread
`(ask-bid)/mid = 0.041`, **p90 = 2.00**, `((ask-bid)/mid > 0.2).mean() = 0.248`, crossed
quotes = 0. The first data row is `AAPL130607C00330000 … bid 118.55, ask 121.10, volume 0,
open_interest 0, delta 1.0` — a zero-volume deep-ITM quote. Their pipeline ingests and charts
whatever the vendor sent. **We are better here.**

**Capacity cannot be modelled from their source even in principle:** `bid_size` and `ask_size`
are columns but 100% NaN (measured). `volume` and `open_interest` are present, so a
participation cap is conceivable, but quoted depth is not. Neither project models capacity;
theirs could not from this file. **Neither better.**

### 3 · Look-ahead protection

**The vocabulary is absent from the entire tree.** A case-insensitive grep for
`look.?ahead|lookahead|survivorship` over `*.py`/`*.md`/`*.cpp`/`*.hpp`/`*.ipynb` (excluding
`cpp/third_party`) returns **zero hits**. There is no guard, no error type and no documented
convention. The exception ecosystem — registered in
`cpp/arcticdb/python/python_module.cpp:120-320`, about twenty types including
`InternalException`, `StorageException`, `UserInputException`, `SchemaException`,
`StreamDescriptorMismatch`, `NormalizationException`, `MissingDataException`,
`SortingException`, `UnsortedDataException` (`:210`), `CompatibilityException`,
`CodecException`, `NoDataFoundException`, `NoSuchVersionException` — is storage- and
schema-shaped throughout, with nothing temporal-correctness shaped. **We are better here.**

**`as_of` is write time, not knowledge time.** `python/arcticdb/version_store/library.py:2139`
documents `datetime.datetime` as "the version of the data that existed ``as_of`` the
requested point in time". The implementation selects on creation timestamp alone —
`cpp/arcticdb/version/version_functions.hpp:304-307`:

```cpp
inline std::optional<T> get_index_key_from_time(timestamp from_time, const std::vector<T>& keys) {
    auto at_or_after = std::lower_bound(std::begin(keys), std::end(keys), from_time, [](const T& v_key, timestamp cmp) {
        return v_key.creation_ts() > cmp;
    });
```

reached from `cpp/arcticdb/version/local_versioned_engine.cpp:379-401`
(`LocalVersionedEngine::get_version_at_time`) and dispatched at `:434-435`. The axis is
documented in Python too: `library.py:166-167`, "`date: datetime.datetime` — Time that the
version was written in UTC", and `python/arcticdb/version_store/_store.py:254-256`,
"The time in nanoseconds since epoch that this version was written."

The consequence is the decisive one for a backtest: **it cuts the version chain and never the
rows.** A bulk backfill of ten years in one write leaves `as_of` nothing to cut. It protects
against look-ahead only if the write history happens to mirror the real arrival history.
**We are better here.**

**And that timestamp is the writing client's own wall clock.**
`cpp/arcticdb/async/async_store.hpp:53` is `template<class ClockType = util::SysClock>`;
`:249` `timestamp current_timestamp() override { return ClockType::nanos_since_epoch(); }`,
used at key construction (`:82`, `:162`) and at segment encode (`:508`, `:526`). The engine
itself acknowledges skew: `cpp/arcticdb/version/version_map.hpp:951-953` subtracts a
`clock_unsync_tolerance` when caching reload times. There is no logical or business-time
stamp. **Neither better** in kind — ours is a column, theirs a clock — but a column the
loader writes is auditable and a writer's clock is not.

**There is no row-level availability concept anywhere.** No `available_at`, no valid-time
column, no as-of cut on the data axis. "Bitemporal" in their documentation means version/write
time only: `docs/mkdocs/docs/index.md:21` ("Bitemporal - stores all previous versions of
stored data"), `:366`, `docs/mkdocs/docs/faq.md:33` ("Versioned modifications (\"time
travel\") - ArcticDB is bitemporal."), `docs/mkdocs/docs/history.md:30` ("offering a
bi-temporal view of data history with no performance penalty"),
`docs/mkdocs/docs/tutorials/fundamentals.md:141`. Framing statements, every one — no column,
no API parameter. **We are better here**: `available_at` is a declared chain column
(`backtest/core/types.py:212`), the cut is applied at
`backtest/data/options/adapters.py:435-441` (`known.loc[known["available_at"] <= known["asof"]]`),
and the two questions stay apart in the signature
`snapshot(self, day: date, *, known_by: date | None = None)`
(`backtest/data/options/chain.py:46`, `backtest/data/options/adapters.py:448`).

**Their own options example conflates the two in one name.**
`ArcticDB_demo_equity_options.ipynb:200-203` is
`def read_expiries(as_of, underlying): read_date = pd.Timestamp(as_of) … lazy_df = lib.read(sym, date_range=(read_date, read_date), lazy=True)`,
repeated at `:227-229` and `:235-237`. The parameter called `as_of` never reaches the `as_of`
argument of `read` — it is a **data date** passed as a degenerate `date_range`. **We are
better here**, and this is the clearest single illustration of why the distinction needs two
names.

**What they do have, and we do not: an immutable version chain.**
`docs/mkdocs/docs/technical/on_disk_storage.md:25` names four layers (Reference, Version,
Index, Data); `:29` — "the reference layer is the only mutable part of ArcticDB's storage
format with the value of each reference-layer-key able to be overwritten"; `:35` — "The
version layer contains a linked list of immutable atom keys/values … traversing the version
layer linked list for a symbol allows you to travel backwards through time to retrieve data
as it were at a previous version/point in time"; `:43` — the index layer "provides a B-Tree
index over the data layer". Once written, a version cannot be corrupted by a later update.
**They are better here.**

**Immutable-and-versioned is not append-only, though.** The reference layer is mutable by
design (`:29`), and history can be destroyed: `library.py:1637-1639`,
"prune_previous_versions: Optional[bool], default=None. Removes previous (non-snapshotted)
versions from the database. If None, the value is taken from the library configuration
(defaults to False)." Snapshots are the hedge — `library.py:2736-2738`, "If a version or
symbol referenced in a snapshot is deleted then the underlying data will be preserved …
Only once all referencing snapshots have been removed will the underlying data be removed as
well." Nothing constrains the relation between a version's write time and the index
timestamps inside it, and `update` is explicitly built to overwrite an arbitrary past range
(`library.py:1597`, `:1630` "the existing data within that range is cleared and overwritten
by data"). So a later version can silently restate history; only the version boundary records
that it happened, and **no row carries its own vintage**. **They are better** on the
immutability of what was written; **we are better** on being able to say when a row became
knowable.

**Snapshots are the one real point-in-time mechanism they offer.**
`docs/mkdocs/docs/tutorials/snapshots.md:3` — "Snapshots enable multiple symbols to be
versioned together via a human readable string name"; `:25-26` —
`# SNAP_{i} will forever point to all symbols that exist at this time at their current latest version`
/ `library.snapshot(f"SNAP_{i}")`; and `docs/mkdocs/docs/index.md:390` — "This allows certain
versions to be protected from deletion". A named, multi-symbol, deletion-proof pin. **They are
better here**, and this is the single mechanism from this repository most worth borrowing in
shape: our `snapshot(day, known_by=)` answers the point-in-time question *computationally*
from a column, which is auditable; theirs answers it by *naming* a pinned set, which is
reproducible by reference. They are not the same guarantee.

**Reading an old version gets slower the more versions a symbol has.**
`on_disk_storage.md:37` — "This means that for symbols with a lot of versions, this linked
list can get quite long, and so reading old versions (using the `as_of` argument) can become
slower as lots of tiny object reads are required in order to find the relevant index key. A
method will soon be added to the `Library` API allowing users to 'compact' this linked list
into a few larger objects." A daily option-chain ingest is the worst case for this: one
version per underlying per day, ~2,520 versions per underlying over ten years. **We are
better here** — a parquet file has no version chain to walk.

**That promised mitigation is not on the public API.** Version-map compaction exists only as
a private method: `cpp/arcticdb/version/version_store_api.cpp:1482`
`void PythonVersionStore::_compact_version_map(const StreamId& id) { version_map()->compact(store(), id); }`,
declared at `version_store_api.hpp:280` and bound at
`cpp/arcticdb/version/python_bindings.cpp:1192-1193`. Its only caller in the tree is
`python/tests/stress/arcticdb/version_store/test_stress_version_map_compact.py:67`
(`lib.version_store._compact_version_map(sym)`). The documented `Library` API offers
`compact_symbol_list` (`library.py:3285`), `compact_data` (`:3358`) and
`defragment_symbol_data` (`:3515`) — **none of which compacts the version chain**. **We are
better here.**

**Two boundary failures worth naming, both of the kind our guards exist to prevent.**
`docs/mkdocs/docs/error_messages.md:129` — "All calls to `delete_data_in_range` and `update`,
and calls to `read` using the `date_range` optional argument, require the existing data to
have a *sorted* timestamp index. **ArcticDB does not check this condition at write time.**"
The failure surfaces much later, at read time, as `:136` "Non-contiguous rows, range search on
unsorted data?…", resolved only by "the user must ensure the data is sorted on the index at
write time". And `:141-144` — "Due to the client-only nature of ArcticDB, it is not possible
to know if a processing operation … makes sense for the given symbol without interacting with
the storage. In particular, we do not know: * Whether a specified column exists * What the
type of the data held in a specified column is if it does exist" — so a mistyped chain column
surfaces as a runtime "Unexpected column name" (`:150`), with `get_description` offered as the
user's own discipline. **We are better here** on both: the `CONTRACT` declaration
(`app/static/js/contract.js`) is checked against a real response by `tools/check_wiring.py`
before anything reads a field.

### 4 · Validation and overfitting control

**Absent, and not applicable.** A grep over `*.py`/`*.md`/`*.ipynb` for
`sharpe|p.?value|bootstrap|cross.?valid|walk.forward|monte.?carlo|t.?statistic|deflated`
returns no substantive hit (the matches are coincidental: `build_tooling/format.py:5`
"First bootstrap by installing the linting tools", and `capture_wrap`/`survive` inside test
names). There is no statistical machinery of any kind.

What they do have in this slot is **performance** regression control, not statistical control:
`asv.conf.json` configures airspeed-velocity benchmarks over the repository, the suite lives
in `python/benchmarks/` (`basic_functions.py`, `column_stats.py`, `compact_data.py`,
`finalize_staged_data.py`, `list_symbols.py`, `arrow.py`, …), and
`.github/workflows/benchmark_commits.yml` runs it in CI. **Nothing to compare** — recorded so
a later run reads the slot as "a store, deliberately empty" rather than "not checked".

### 5 · Extensibility

**Answered, but from reading rather than from a verified claim.** The extension points are
real and they are mostly in C++:

- **Storage backends** are a factory over a directory per backend —
  `cpp/arcticdb/storage/storage_factory.cpp` with `s3/`, `azure/`, `lmdb/`, `mongo/`,
  `memory/`, `file/`, `coalesced/`, `mock/` beside it; the Python side mirrors this with one
  adapter per backend in `python/arcticdb/adapters/` (`s3_library_adapter.py`,
  `azure_library_adapter.py`, `gcpxml_library_adapter.py`, `lmdb_library_adapter.py`,
  `mongo_library_adapter.py`, `in_memory_library_adapter.py`) plus a decorator
  (`prefixing_library_adapter_decorator.py`). Adding a backend is a clean, demonstrated
  operation.
- **Query clauses** are an interface: `cpp/arcticdb/processing/clause.hpp:55` `struct IClause`
  with eighteen implementing structs after it (listed under dimension 2). Extending the query
  language means writing C++ and rebuilding — a high wall for a research user.
- **Normalizers** are Python classes (`python/arcticdb/version_store/_normalization.py:696`
  `class Normalizer`, with `ArrowTableNormalizer`, `SeriesNormalizer`, `NdArrayNormalizer`,
  `DataFrameNormalizer`, `MsgPackNormalizer`, `TimeFrameNormalizer`, `KnownTypeFallbackOnError`)
  but they are **not a registry**: they are hard-wired into `CompositeNormalizer` at `:1557`
  and bound once at `:1707` `_NORMALIZER = CompositeNormalizer()`. Adding a type means editing
  that class, not registering against it.

The shape to take from this: one adapter per backend behind a factory is the pattern our
`backtest/data/options/adapters.py` already follows. The anti-pattern to avoid is the
normalizer composite — a fixed chain presented as a class hierarchy, which looks extensible
and is not.

### 6 · Usability

**Answered from reading.** Documentation is a built mkdocs site (`docs/mkdocs/docs/`) with a
genuinely useful split: tutorials (`fundamentals.md`, `data_organisation.md`, `snapshots.md`,
`library_sizes.md`), technical internals (`technical/on_disk_storage.md` — the four-layer
format, with diagrams), an `error_messages.md` table that pairs each message with its cause
*and* its resolution, and seventeen runnable notebooks in `docs/mkdocs/docs/notebooks/` with
their data committed beside them in `notebooks/data/`.

Two things are worth copying. First, **`error_messages.md` as a document type**: a table of
"error message | cause | resolution" is the artefact a user actually needs, and it is where
the two boundary failures under dimension 3 were found — they documented their own weaknesses
plainly. Second, **shipping the data with the notebook**: the 803,920-byte options CSV is in
the repository, so the example runs. The counterweight, from dimension 2: the data they
shipped is 63% zero-volume and p90 relative spread 2.00, and the notebook never says so — a
runnable example built on unfiltered vendor data teaches the pipeline and mis-teaches the
data.

`faq.md` is honest about limits in a way that is rarer than it should be — `:66` on the
absence of transaction isolation, `:58` on Python-only bindings. Install is `pip install
arcticdb`; the C++ build is a vcpkg/CMake affair documented in `CLAUDE.md` (build commands
section) and `build_tooling/`.

### 7 · Licence and health

**Licence — the operative text and the README contradict each other, and the README is
stricter.** All three statements were read:

- `LICENSE.txt:7-15` (Additional Use Grant) — "You may make use of the Licensed Work under
  the terms of this License, provided that you may not use the Licensed Work for a Database
  Service. A 'Database Service' is a commercial offering that allows third parties (other than
  your employees and contractors) to access the functionality of the Licensed Work by creating
  tables whose schemas are controlled by such third parties." Together with `:31` ("the right
  to copy, modify, create derivative works, redistribute, and make non-production use" plus
  the Additional Use Grant "permitting limited production use"), the **licence permits
  in-house production use** and withholds only a Database Service.
- `README.md:26` — "Use of ArcticDB in production (including business or commercial
  environments) or for a Database Service requires a paid for license from ArcticDB Limited."
  `README.md:142-143` — "users may not use ArcticDB for production use or for a Database
  Service, without agreement with Man Group Operations Limited." **The README forbids
  production use outright.**
- `docs/mkdocs/docs/licensing.md:9` goes furthest of the three — "a commercial agreement is
  required for any business use. That includes use in research or dev environments, or where
  any economic benefit is being derived." **Research use too.**

Two different named entities appear: **Man Group Operations Limited** is the Licensor
(`LICENSE.txt:3`) and the party whose agreement `README.md:143` requires; **ArcticDB Limited**
is the seller in `README.md:26,145`. For our purposes the conclusion is the same under all
three readings: **this is not a repository to borrow current code from.**

**Conversion.** `LICENSE.txt:17-23` — "For ArcticDB version 1.0 and all later version releases
the change date is two years as of the release date of the respective version with the
two-year term beginning for each individual version on the release date"; `:25` Change License
is Apache 2.0; `:33` caps the term at the fourth anniversary of a version's first public
distribution, whichever comes first. The published table (`README.md:152-196`) lists 46
versions. As of **2026-11-06**: every release up to and including **5.0 (Oct 31, 2026) has
already converted to Apache 2.0**; **5.1 converts Nov 15, 2026** — nine days out; the table
runs to **6.21 (Aug 4, 2028)**. So borrowing from a ≤ 5.0 tag is an Apache-2.0 question, and
borrowing from master is not. Worth re-checking rather than trusting this paragraph: the
table is maintained per release.

**Health — mostly not answerable from this tree, and that is a checkout limitation, not a
finding.** The clone is **shallow (depth 1)**: `git rev-parse --is-shallow-repository` is
`true` and `git log --oneline | wc -l` is `1`. So commit cadence, contributor count, release
frequency and issue activity **cannot be read here**. What *is* visible:

- HEAD is `b14a8f0`, dated **2026-11-06** (today) — "Make merge updates work with sparse data"
  (PR #3492). A four-digit PR number and a same-day HEAD are consistent with an actively
  developed project, but they are one data point, not a cadence.
- **166** Python test files under `python/tests/` (unit, integration, stress, compat, hypothesis
  directories) and **114** C++ test files under `cpp/` outside `third_party`.
- **30 GitHub Actions workflows** in `.github/workflows/`, including a full release
  automation chain (`automated_release.*.yml`, seven files), `clang_tidy.yml`,
  `build_with_conda.yml`, `installation_tests.yml`, `persistent_storage.yml`,
  `benchmark_commits.yml`, `flaky_tests_summary.yml` and `failure_notification.yaml`. A
  project with a dedicated flaky-test summary job is a project with enough tests to have the
  problem.

What would answer the health dimension properly: a non-shallow clone (`git clone` without
`--depth`), or the GitHub API for commit/issue/release cadence. Neither was done.

### 8 · AI and agent integration

**This is the dimension where they are furthest ahead of us, and it was not in the payload at
all.** The repository ships agent infrastructure as a first-class, committed artefact:

- **`CLAUDE.md`, 271 lines**, at the repository root — project overview, build commands
  including the exact `sudo apt install` prerequisites, and a documentation contract:
  "**New features must include documentation.** Full requirements and checklists for both
  user-facing docs and Claude-maintained technical docs are in
  `.claude/skills/update-docs/SKILL.md` — read that file when adding or modifying public API,
  and follow its checklists before considering the work complete."
- **`docs/claude/` — 25 committed files written for an agent to read**: `ARCHITECTURE.md`
  (431 lines), `PR_REVIEW_GUIDELINES.md`, eleven C++ module documents
  (`docs/claude/cpp/`: CACHING, VERSIONING, STORAGE_BACKENDS, ENTITY, CODEC, COLUMN_STORE,
  PIPELINE, PROCESSING, STREAM, ASYNC, PYTHON_BINDINGS), seven Python module documents
  (`docs/claude/python/`: ARCTIC_CLASS, LIBRARY_API, NATIVE_VERSION_STORE, QUERY_PROCESSING,
  NORMALIZATION, ADAPTERS, TOOLBOX), and **per-branch work logs** under
  `docs/claude/plans/<branch-name>/branch-work-log.md`.
- **Two committed skills**: `.claude/skills/update-docs/SKILL.md` and
  `.claude/skills/review-pr/SKILL.md`. The review skill is written to run in **both** modes —
  "When invoked from a GitHub Actions workflow the following variables are injected before
  this prompt: `PR_NUMBER`, `REPO`, `EVENT_ACTION`, `BEFORE_SHA`, `AFTER_SHA`. When invoked
  locally as a slash command the PR number is `$ARGUMENTS`".
- **Two CI workflows that execute those skills.**
  `.github/workflows/claude-code-review.yml` runs on every non-draft
  `pull_request` (`opened, synchronize, ready_for_review, reopened`) and literally reads the
  skill file into the prompt:
  `- name: Read review skill` … `cat .claude/skills/review-pr/SKILL.md`.
  `.github/workflows/claude.yml` runs on `@claude` mentions in issue comments, review
  comments and reviews, with `contents: write` and `pull-requests: write`.
- A community **MCP server** is linked from `README.md:229` — "arcticdb-mcp: A
  community-maintained Model Context Protocol server that enables compatible AI assistants
  and agents to interact with ArcticDB libraries, symbols, versions, snapshots, batch
  operations and queries." Third-party, not theirs; recorded as ecosystem, not as a feature.

**They are better here**, and the specific thing worth taking is not the skills but the
**pattern**: one skill file, read by both a CI workflow and a local slash command from the
same path, so the rule a reviewer follows in CI is the same text a developer invokes by hand.
The `docs/claude/plans/<branch>/branch-work-log.md` convention is the second — a committed,
per-branch record of what an agent did, which is the same problem our
`CLAUDE.md` "write **why** in the commit message" rule addresses by a different route.

## What was not checked out

The clone at `/Users/lichengkai/Desktop/benchmark-clones/ArcticDB` (97 MB, 2,247 tracked
files) has the **full directory tree** — every `python/`, `cpp/`, `docs/` and `.github/`
directory cited above was present and read — but it is restricted in two ways that bound
specific dimensions:

1. **Shallow: depth 1.** `git rev-parse --is-shallow-repository` → `true`; `.git/shallow`
   holds one entry; `git log --oneline` returns one commit. **Dimension 7's health half is
   answered from a partial tree and should be read as "not answered".** Commit cadence,
   contributor count, release frequency and issue activity are not in this checkout. The
   static substitutes used instead — test-file counts, workflow counts, the HEAD date — are
   weaker evidence and are labelled as such above.
2. **Seven git submodules are declared and empty.** `.gitmodules` declares
   `cpp/third_party/pybind11`, `cpp/third_party/lmdb`, `cpp/third_party/lmdbxx`,
   `cpp/third_party/recycle`, `cpp/third_party/rapidcheck`, `cpp/third_party/entt` and
   `cpp/vcpkg`; all seven directories contain **0 files**. (`cpp/third_party/msgpack-c` and
   `cpp/third_party/Remotery` *are* vendored in-tree and were present.) No claim above rests
   on third-party code, and every grep cited excluded `cpp/third_party` deliberately — but
   the LZ4-codec reachability argument under dimension 7's sizing material rests on
   ArcticDB's own call sites, not on the LZ4 library, which was not read.
3. The clone is also a **partial clone** (`partialclonefilter = blob:limit=1048576`). In
   practice the working tree was complete — the 47 MB `notebooks/data/2025-08-26-sports.csv`
   was present and the four options CSVs were read byte-for-byte — so this restricted nothing
   that was needed.

Nothing else was left out. Dimensions 1, 2 and 3 rest on files that were all present and
individually opened.

## Claims that did not survive the cross-check

**First, the two faults in the payload itself**, because they change how everything above
should be read:

- **No verdicts arrived.** Not one claim object carried a verdict field. There was no
  verifier stage to report, so this whole section is **this writer re-opening the clone**,
  which is strictly weaker than an independent second reading.
- **The payload was truncated** mid-claim, ending inside
  `std::lower_bound(std::begin(keys), std::end(keys), from_t`. Claims arrived for three
  dimensions only. Dimensions 4, 5, 6 and 8 above have no analyst claim behind them.
- **Every claim was labelled with the wrong dimension.** All licence and sizing claims
  arrived tagged `"data model and option chains"` or `"fill and cost realism"`; all
  look-ahead claims arrived tagged `"fill and cost realism"`. The payload's `dimension`
  field is unreliable and was ignored.

**Then, the claims that were wrong or overstated:**

**1 · "The package's entire export list is `Arctic`, `LibraryOptions`, `QueryBuilder`,
`where`, `VersionedItem`, `WritePayload`, `ReadRequest`, `LazyDataFrame`, `concat`, `KeyType`,
`Size`" — cited `python/arcticdb/__init__.py:1-36`. Overstated: the list is an abridgement
presented as complete.** The file imports **thirty** names, not eleven. Missing from the
claim: `EnterpriseLibraryOptions`, `OutputFormat`, `RuntimeOptions`, `ArrowOutputStringFormat`
(`:9-13`), `library`, `set_config_from_env_vars` (`:17-18`), `DataError`,
`VersionRequestType`, `ErrorCode`, `ErrorCategory` (`:19-20`), `UpdatePayload`,
`ReadInfoRequest`, `DeleteRequest`, `col`, `LazyDataFrameCollection`,
`LazyDataFrameAfterJoin`, `StagedDataFinalizeMethod`, `WriteMetadataPayload`, `InputFormat`
(`:22-34`). **The conclusion survives** — every one of those thirty is storage, query, option
or error shaped, and nothing is execution shaped — but a truncated list offered as "entire"
is the kind of claim that becomes false the moment the file changes.

**2 · "The complete public exception list is [ten classes] plus re-exports
NoDataFoundException / NoSuchVersionException / UnsortedDataException" — cited
`python/arcticdb/exceptions.py:1-82 (whole file)`. Wrong: that file does not contain the
list.** `exceptions.py:11` is `from arcticdb_ext.exceptions import *` — a **wildcard from the
C++ extension module**. The real registry is
`cpp/arcticdb/python/python_module.cpp:120-320`: `ArcticException` (`:298`),
`InternalException` (`:147`), `StorageException` (`:151`), `LmdbMapFullError` (`:155`),
`UserInputException` (`:159`), `DuplicateKeyException` (`:195`), `KeyNotFoundException`
(`:198`), `PermissionException` (`:201`), `SchemaException` (`:203`),
`StreamDescriptorMismatch` (`:204`), `NormalizationException` (`:205`),
`MissingDataException` (`:206`), `SortingException` (`:209`), `UnsortedDataException`
(`:210`), `CompatibilityException` (`:211`), `CodecException` (`:212`),
`NoDataFoundException` (`:306`), `NoSuchVersionException` (`:319`) — about twenty types.
`UnsortedDataException` in particular is **not named anywhere in `exceptions.py`**; it arrives
through the wildcard. **The conclusion survives** on the larger list — nothing in it is
temporal-correctness shaped — but the citation pointed at a file that delegates.

**3 · "Repo-wide grep for `model_derived|quality_flag|provenance` over `*.py`/`*.md`/`*.ipynb`
returns nothing." Wrong as stated.** `provenance` has four hits:
`python/arcticdb/version_store/_normalization.py:1106` ("Arrow data that has a pandas
provenance carries the pandas metadata itself…") and three notebook-metadata keys —
`ArcticDB_demo_equity_options.ipynb:618`, `ArcticDB_demo_lmdb.ipynb:2504`,
`ArcticDB_aws_public_blockchain.ipynb:540`, each the literal `"provenance": []` that Jupyter
writes. **The conclusion survives**: none is a data-provenance column, and
`model_derived`/`quality_flag` genuinely return zero. Recorded because "grep returns nothing"
is a claim that is either true or false, and this one was false.

**4 · "A repo-wide grep for `bitemporal|available_at|knowledge.time|valid_time` … returns only
these framing statements plus the as_of docstrings", enumerating `index.md:21`, `faq.md:33`,
`history.md:30`. Incomplete.** "Bitemporal" also appears at `docs/mkdocs/docs/index.md:366`
("it is a bitemporal database"), `docs/mkdocs/docs/tutorials/fundamentals.md:141` ("ArcticDB
is bitemporal - all new versions are timestamped!") and
`docs/mkdocs/docs/notebooks/ArcticDB_demo_lmdb.ipynb:1306` ("### ArcticDB is bitemporal").
All framing, so **the conclusion survives** — but the word is marketed harder and in more
places than the claim implied, which matters for a reader who might meet one of the missed
three first and believe it.

**5 · "Version-map compaction exists only as a private method on the low-level store,
undocumented and exercised nowhere but a stress test." Nearly right; one omission.** It is
*deliberately exposed to Python* through pybind — declared at
`cpp/arcticdb/version/version_store_api.hpp:280` and bound at
`cpp/arcticdb/version/python_bindings.cpp:1192-1193` — which is how the stress test at
`python/tests/stress/arcticdb/version_store/test_stress_version_map_compact.py:67` can call
`lib.version_store._compact_version_map(sym)` at all. So it is reachable from Python, not
buried in C++. **The load-bearing half survives**: it is absent from the documented `Library`
API, whose three compaction methods (`compact_symbol_list` `:3285`, `compact_data` `:3358`,
`defragment_symbol_data` `:3515`) all compact something else.

**6 · "LZ4 at acceleration 1 is the only codec reachable." Needs one word changed.**
`cpp/arcticdb/codec/default_codecs.hpp:22` also defines `default_passthrough_codec()`, and
passthrough encoding is live in `cpp/arcticdb/codec/passthrough.hpp` and in the shapes path.
What is actually true, and is what the argument needs: `default_lz4_codec()` is the codec
hard-coded at **every engine construction site** — `local_versioned_engine.cpp:75`,
`library_manager.cpp:80`, `version_store_api.cpp:1533` and `:1544`,
`storage/file/file_store.hpp:133`, `toolbox/storage_mover.hpp:316` and `:319` — and
`default_passthrough_codec()` is called from none of them. The `Zstd` and TurboPfor
(`P4_DELTA`, `P4_DELTA_RLE`, `FP_DELTA`, `FP_GORILLA_RLE`, …) messages declared at
`cpp/proto/arcticc/pb2/encoding.proto:63-83` have no selecting code path, confirmed by
`grep -rn -iE 'zstd|codec' python/arcticdb/ --include='*.py'` returning exactly two hits — a
logger name (`log.py:54`) and `decode_segment` (`toolbox/library_tool.py:11`). The claim's own
caveat — "the actual ratio cannot be measured without running their code" — is honest and
stands.

**7 · "A ten-year QQQ end-of-day chain is roughly 0.15-0.35 GB in parquet." This is an
extrapolation, not a measurement, and should not be cited as one.** Everything the claim
*measured* checks out exactly against the clone: 803,920 bytes for 6,792 data rows across the
19-column schema = 118 bytes/row as CSV text; `gzip -9` of the whole file = 199,771 bytes =
29.4 bytes/row; AAPL = 2,370 contract-rows on 2013-06-03. The parquet figure is an inference
from those numbers — **no parquet file was written and no columnar encoding was measured.**
The direction of the inference is reasonable (dictionary-encoded `contract`/`type`/`expiration`
beating whole-file gzip of row-oriented CSV), and the conclusion it supports — laptop scale,
single file, a versioned object store does not earn its place at this volume — is not in
doubt at any plausible ratio. But "0.15-0.35 GB" has no file behind it, and under this
project's own rule it should carry that mark. The companion claim is better founded: at
`rows_per_segment = 100_000` (`python/arcticdb/options.py:47`) and
`columns_per_segment = 127` (`:48`), 10M rows × 19 columns is **~100 row-slices in a single
column-slice**, against a headline demonstration of `num_rows = 1_000_000_000` with
`rows_per_segment=10_000_000` (`ArcticDB_billion_row_challenge.ipynb:77,99-100`) and a stated
target of "billions of rows" (`faq.md:34-35`, `README.md:33`) and "a 20-year history of more
than 400,000 unique securities … in a single *symbol*" (`README.md:40`). Two to three orders
of magnitude above us, measured from their own defaults.

**8 · Line drift — the right file, the wrong line.** Each of these cites a file that contains
the quoted text, at a different line than given. Recorded because a later run following these
citations will land in the wrong place, and because the pattern (drift of 1-6 lines, always in
the same direction) suggests the analyst read a different checkout than this one:

| Claimed | Actual | What is there |
|:---|:---|:---|
| `python/arcticdb/options.py:58-64` | **:64-66** | the static-schema docstring |
| `python/arcticdb/options.py:46` | **:45** | `dynamic_schema: bool = False` |
| `docs/.../technical/on_disk_storage.md:27` | **:25** | "comprised of 4 layers" |
| `docs/.../technical/on_disk_storage.md:95-97` | **:94** | the fragmentation warning |
| `docs/mkdocs/docs/error_messages.md:148` | **:150** | the `get_description` resolution |
| `docs/mkdocs/docs/error_messages.md:140-142` | **:141**, bullets at **:143-144** | "we do not know: * Whether a specified column exists …" |
| `cpp/arcticdb/version/local_versioned_engine.cpp:378-383` | opens at **:379** | `get_version_at_time` |
| `cpp/arcticdb/version/version_functions.hpp:303-313` | opens at **:304** | `get_index_key_from_time` |
| `python/arcticdb/version_store/processing.py:105-126` | **:105-127** | the operator overloads (`__pow__` is at :127) |

**Everything else held exactly.** Worth saying explicitly, because it is most of the payload
and because the next run should know these are safe to build on without re-reading: the
`LICENSE.txt` quotations and all three contradicting licence statements, the whole README
conversion table and both named entities, every quotation from `data_organisation.md`
(:105-107, :119-121, :171, :228 — all four exact to the line), `snapshots.md:3,25-26`,
`on_disk_storage.md:29,35,37,43`, `faq.md:33,66`, `index.md:21,390`, `history.md:30`, every
`library.py` citation (:166-167, :1588, :1597-1599, :1609, :1625, :1630, :1637-1639, :1669,
:2139, :2736-2738, :3285, :3358, :3515), `_store.py:254-256`, `version_functions.hpp:305-307`,
`local_versioned_engine.cpp:434-435`, `async_store.hpp:53,82,249,508`,
`version_map.hpp:951-953`, `version_store_api.cpp:1482`, `default_codecs.hpp:15-20` and all
seven call sites, `test_filtering.py:629-637` including the inverted spread,
`processing.py:289,543,703`, the recursive-normalizer notebook cells 30-31 verbatim, every
line reference into `ArcticDB_demo_equity_options.ipynb` (117-118, 188, 190-191, 200-203,
208-209, 221-222, 224, 227-229, 235-237, 293, 423), the absence of bid/ask/mid/spread from all
22 of its cells, and **every one of the eleven statistics measured from
`data/2013-06-03tech-options.csv`** — file size, row count, the all-NaN size columns, bid = 0
at 10.5%, volume = 0 at 63.1%, open interest = 0 at 23.1%, median relative spread 0.041, p90
exactly 2.00, spread > 20% at 24.8%, zero crossed quotes, AAPL at 2,370 rows, and the first
data row quoted character for character. The zero-hit greps for the execution vocabulary, for
`backtest`, for the pricing vocabulary (two hits, both column reads) and for
`look.?ahead|lookahead|survivorship` (zero) were each re-run and each returned what was
claimed.
