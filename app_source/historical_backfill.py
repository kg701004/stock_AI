"""Incremental, resumable multi-year daily-bar backfill for TWSE and TPEx.

TPEx per-stock history is fetched from `www.tpex.org.tw/www/zh-tw/afterTrading/
tradingStock` -- the same AJAX endpoint behind TPEx's own "個股日成交資訊" page
(which states data is available back to ROC 83 / 1994), captured by driving
that real page in a browser and inspecting its outgoing request, then
confirmed to work standalone (no cookies/auth) with a plain HTTP POST. This
is a different endpoint from TPEx's documented OpenAPI
(openapi.tpex.org.tw / www.tpex.org.tw/openapi/) -- an earlier pass checked
only the OpenAPI's 120+ /tpex_* swagger-declared endpoints (all parameterless
"today only" snapshots) and wrongly concluded TPEx had no historical source
at all. Its volume figures are in whole board lots (1 張 = 1,000 shares),
which is a rounded approximation missing odd-lot (零股) trades -- cross-checked
against the daily snapshot importer's exact TradingShares figure for 6182 on
115/07/24 (20,847,262 actual vs. 20,847,000 here, ~0.001% difference,
immaterial for technical analysis).

Design constraints (see 功能檢測與改善計畫.md section 5):
- Only fetch (symbol, year-month) combinations missing from local history.
- Persist progress after every single fetch so a restart resumes cleanly.
- Throttle between requests to stay a well-behaved consumer of a free public API.
- A single failed month must not abort the whole run.
"""
from __future__ import annotations

import json
import sqlite3
import ssl
import threading
import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Iterable
from urllib.parse import urlencode
from urllib.request import Request, urlopen
import certifi

from database_utils import database_connection
from dividend_adjustment import fetch_ex_rights_events, parse_ex_rights_events, store_events
from historical_storage import DailyBar, archive_and_import, ensure_wal_mode
from security_catalog import lookup_market
from twse_daily_importer import _number, write_normalized_csv

### 2026-08-06 live-tested finding while investigating why the backtest
# validation sample was so thin: 69% (52,327/75,195) of every historical
# backfill attempt ever made this session had status="failed" in
# backfill_progress. Reproduced live: the OLD "/rwd/zh/..." TWSE URL prefix
# now returns a bare HTTP 307 with no Location header (unfollowable) for
# EVERY request, including well-established symbols/months that definitely
# have data (e.g. 2330 2026-07). The current, working prefix is
# "/exchangeReport/..." -- confirmed live (stat=OK, real rows) and already
# what twse_daily_importer.py/external_data_importers.py use for their own
# (working) daily endpoints. Retries already exhaust MAX_RETRIES against the
# same dead URL, so this was never a transient blip.
TWSE_STOCK_DAY_URL = "https://www.twse.com.tw/exchangeReport/STOCK_DAY"
TPEX_TRADING_STOCK_URL = "https://www.tpex.org.tw/www/zh-tw/afterTrading/tradingStock"

# Real, confirmed floors of each free daily-bar source -- not the exchange's
# founding year, which is a different (older, misleading) number. TWSE:
# confirmed both by STOCK_DAY itself rejecting any date before this with
# "查詢日期小於99年1月4日，請重新查詢!", and by TWSE's own "個股日成交資訊"
# page text ("本資訊自民國99年1月4日起開始提供"). TPEx: confirmed by its own
# "個股日成交資訊" page text ("本資訊自民國83年1月起開始提供"). Requesting
# months before these floors would just accumulate permanent "failed" retries
# for no reason, so plan_pending_months clamps to them per symbol's market.
TWSE_EARLIEST_YEAR = 2010
TPEX_EARLIEST_YEAR = 1994

# 0.3s is the fastest per-request pace actually measured against both real
# endpoints without any failure or slowdown (24-25 sequential requests each,
# interval swept 1.0s -> 0.0s) -- see 功能檢測與改善計畫.md. That test was
# single-threaded and small-scale, so it does NOT by itself prove 3 workers
# sustained over hundreds of requests is equally safe; MAX_RETRIES exists
# specifically to absorb whatever that larger, unverified load turns out to
# trigger (rate limiting, transient errors) instead of assuming it won't.
DEFAULT_THROTTLE_SECONDS = 0.3
DEFAULT_MAX_WORKERS = 3
MAX_RETRIES = 3


@dataclass(frozen=True, slots=True)
class BackfillSummary:
    attempted: int
    succeeded: int
    failed: tuple[str, ...]
    stopped_early: bool


def ensure_schema(connection) -> None:
    connection.execute("""
        CREATE TABLE IF NOT EXISTS backfill_progress (
            symbol TEXT NOT NULL, year_month TEXT NOT NULL, market TEXT NOT NULL,
            status TEXT NOT NULL, attempted_at TEXT,
            PRIMARY KEY (symbol, year_month, market)
        )
    """)


def plan_pending_months(history_database: Path, symbols: Iterable[str], years: int = 10, as_of: date | None = None) -> list[tuple[str, int, int]]:
    """Return [(symbol, year, month), ...] still missing, oldest first, skipping
    months that already have local daily-bar data (however it got there) or
    that backfill already marked done.

    Deliberately month-level and inclusive of the current, in-progress
    calendar year up to `as_of` -- historical_coverage.check_coverage's
    missing_years exists to answer "is N *completed* calendar years ready for
    a backtest" and therefore always excludes the current year by design.
    Reusing that here to decide what to backfill silently made any gap within
    the current year permanently invisible to this function: a confirmed real
    production gap (2025-12-31 to 2026-07-21, across multiple symbols) was
    never picked up by a prior backfill run for exactly this reason, because
    the whole of the still-in-progress year was treated as "not missing"
    regardless of how much of it actually had data.

    Also clamps per symbol to that symbol's real market floor
    (TWSE_EARLIEST_YEAR / TPEX_EARLIEST_YEAR) so a large `years` request never
    enqueues months the source is confirmed to reject outright -- those would
    fail every single run (failures aren't marked "done") and be retried
    forever for no benefit.
    """
    as_of = as_of or date.today()
    done: set[tuple[str, str]] = set()
    present_by_symbol: dict[str, set[str]] = {}
    markets: dict[str, str] = {}
    if history_database.exists():
        with database_connection(history_database) as connection:
            ensure_schema(connection)
            done = {(symbol, year_month) for symbol, year_month in connection.execute(
                "SELECT symbol, year_month FROM backfill_progress WHERE status='done'"
            )}
            # One full-table scan instead of one query per symbol -- with the
            # "全歷史資料下載" scope (every catalogued symbol, ~2000), the old
            # per-symbol loop took ~2.5s of real, measured latency on the
            # production database; this takes well under 0.2s for the same
            # data, confirmed against the same real database (~200k rows).
            try:
                for symbol, year_month in connection.execute(
                    "SELECT symbol, substr(trading_date, 1, 4) || substr(trading_date, 6, 2) FROM daily_bars"
                ):
                    present_by_symbol.setdefault(symbol, set()).add(year_month)
            except sqlite3.OperationalError:
                pass  # daily_bars not created yet (e.g. only the securities catalog exists so far)
            try:
                for symbol, market in connection.execute("SELECT symbol, market FROM securities"):
                    markets[symbol] = market
            except sqlite3.OperationalError:
                pass  # securities catalog not created yet
    pending: list[tuple[str, int, int]] = []
    for symbol in symbols:
        floor_year = TPEX_EARLIEST_YEAR if markets.get(symbol, "TWSE") == "TPEx" else TWSE_EARLIEST_YEAR
        earliest_year = max(as_of.year - years, floor_year)
        present = present_by_symbol.get(symbol, set())
        for year in range(earliest_year, as_of.year + 1):
            last_month = as_of.month if year == as_of.year else 12
            for month in range(1, last_month + 1):
                year_month = f"{year:04d}{month:02d}"
                if (symbol, year_month) in done or year_month in present:
                    continue
                pending.append((symbol, year, month))
    return pending


def estimate_work(pending: list[tuple[str, int, int]], throttle_seconds: float = DEFAULT_THROTTLE_SECONDS, max_workers: int = DEFAULT_MAX_WORKERS) -> tuple[int, float]:
    """Return (request_count, estimated_seconds). Assumes no retries fire --
    a lower bound, not a worst case; real transient failures add real time."""
    request_count = len(pending)
    return request_count, request_count * throttle_seconds / max(1, max_workers)


TWSE_NO_DATA_STAT = "很抱歉，沒有符合條件的資料!"


def fetch_month(symbol: str, year: int, month: int, timeout_seconds: int = 20) -> list[dict[str, object]]:
    url = f"{TWSE_STOCK_DAY_URL}?date={year:04d}{month:02d}01&stockNo={symbol}&response=json"
    request = Request(url, headers={"User-Agent": "StockAI-OfflineResearch/1.0 contact: local-user"})
    context = ssl.create_default_context(cafile=certifi.where())
    with urlopen(request, timeout=timeout_seconds, context=context) as response:
        payload = json.loads(response.read().decode("utf-8"))
    stat = payload.get("stat")
    if stat == TWSE_NO_DATA_STAT:
        # Legitimate empty result -- symbol didn't trade on TWSE that month
        # (not yet listed, already delisted, or the code doesn't exist there
        # at all). Same "done, zero bars" treatment as TPEx's empty response,
        # so a permanently-empty period isn't retried forever as a "failure".
        return []
    if stat != "OK":
        raise ValueError(f"TWSE STOCK_DAY returned stat={stat!r}")
    return payload.get("data", [])


def parse_month(symbol: str, records: Iterable[list[object]], published_at: datetime) -> list[DailyBar]:
    """Rows are [ROC date, volume, value, open, high, low, close, change, count, note]."""
    if published_at.tzinfo is None:
        raise ValueError("published_at must include a timezone")
    bars: list[DailyBar] = []
    for row in records:
        if not isinstance(row, list) or len(row) < 7:
            continue
        try:
            roc_year, roc_month, roc_day = (int(part) for part in str(row[0]).split("/"))
            trading_date = date(roc_year + 1911, roc_month, roc_day)
            bars.append(DailyBar(
                symbol=symbol, trading_date=trading_date,
                open_price=_number(row[3]), high_price=_number(row[4]), low_price=_number(row[5]), close_price=_number(row[6]),
                volume=int(_number(row[1])), source="TWSE_STOCK_DAY_BACKFILL", published_at=published_at,
            ))
        except (ValueError, IndexError):
            continue
    return bars


def fetch_month_tpex(symbol: str, year: int, month: int, timeout_seconds: int = 20) -> list[list[object]]:
    body = urlencode({"code": symbol, "date": f"{year:04d}/{month:02d}/01", "id": "", "response": "json"}).encode("ascii")
    request = Request(
        TPEX_TRADING_STOCK_URL, data=body,
        headers={"User-Agent": "StockAI-OfflineResearch/1.0 contact: local-user", "Content-Type": "application/x-www-form-urlencoded"},
    )
    context = ssl.create_default_context(cafile=certifi.where())
    with urlopen(request, timeout=timeout_seconds, context=context) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if payload.get("stat") != "ok":
        raise ValueError(f"TPEx tradingStock returned stat={payload.get('stat')!r}")
    tables = payload.get("tables", [])
    return tables[0].get("data", []) if tables else []


def parse_month_tpex(symbol: str, records: Iterable[list[object]], published_at: datetime) -> list[DailyBar]:
    """Rows are [ROC date, volume(board lots), value, open, high, low, close, change, count].

    Volume is in whole board lots (1 張 = 1,000 shares) -- multiplied up to
    match the exact-share-count convention `parse_month`/DailyBar use, at the
    cost of losing odd-lot (零股) precision (see module docstring).
    """
    if published_at.tzinfo is None:
        raise ValueError("published_at must include a timezone")
    bars: list[DailyBar] = []
    for row in records:
        if not isinstance(row, list) or len(row) < 7:
            continue
        try:
            roc_year, roc_month, roc_day = (int(part) for part in str(row[0]).split("/"))
            trading_date = date(roc_year + 1911, roc_month, roc_day)
            bars.append(DailyBar(
                symbol=symbol, trading_date=trading_date,
                open_price=_number(row[3]), high_price=_number(row[4]), low_price=_number(row[5]), close_price=_number(row[6]),
                volume=int(_number(row[1])) * 1000, source="TPEX_TRADING_STOCK_BACKFILL", published_at=published_at,
            ))
        except (ValueError, IndexError):
            continue
    return bars


def _retry_with_backoff(action: Callable[[], object], throttle_seconds: float) -> object:
    """Retry a transient failure (network blip, possible rate limiting, or a
    real-but-rare local I/O/SQLite hiccup under heavy concurrent load --
    confirmed as the latter by a flaky test failure that only ever
    reproduced under a 300+-test full-suite run, never in isolation) with
    exponential backoff before giving up.

    Wraps the whole fetch-parse-archive-import pipeline, not just the network
    fetch: archive_and_import's own idempotency (checksum-gated import,
    atomic-rename archive write) makes retrying it safe even if a prior
    attempt partially completed."""
    last_error: Exception | None = None
    for attempt in range(MAX_RETRIES):
        try:
            return action()
        except Exception as error:
            last_error = error
            if attempt < MAX_RETRIES - 1:
                time.sleep(throttle_seconds * (2 ** (attempt + 1)))
    assert last_error is not None
    raise last_error


def run_backfill(
    history_database: Path, imports_directory: Path, archive_directory: Path,
    symbols: Iterable[str], years: int = 10,
    throttle_seconds: float = DEFAULT_THROTTLE_SECONDS,
    max_workers: int = DEFAULT_MAX_WORKERS,
    progress_callback: Callable[[int, int, str], None] | None = None,
    should_stop: Callable[[], bool] | None = None,
    as_of: date | None = None,
) -> BackfillSummary:
    """Fetch every missing (symbol, month) with up to `max_workers` requests
    in flight at once, persisting progress after each one. Each worker still
    paces its OWN successive requests by `throttle_seconds` -- concurrency and
    per-request pacing are independent knobs, not a replacement for each
    other; see DEFAULT_THROTTLE_SECONDS/DEFAULT_MAX_WORKERS for how far this
    has actually been validated against the real endpoints.
    """
    symbols = list(symbols)
    pending = plan_pending_months(history_database, symbols, years, as_of=as_of)
    markets = {symbol: (lookup_market(history_database, symbol) or "TWSE") if history_database.exists() else "TWSE" for symbol in symbols}
    return _run_pending_months(
        history_database, imports_directory, archive_directory, pending, markets,
        throttle_seconds, max_workers, progress_callback, should_stop,
    )


def _run_pending_months(
    history_database: Path, imports_directory: Path, archive_directory: Path,
    pending: list[tuple[str, int, int]], markets: dict[str, str],
    throttle_seconds: float, max_workers: int,
    progress_callback: Callable[[int, int, str], None] | None,
    should_stop: Callable[[], bool] | None,
) -> BackfillSummary:
    """Shared fetch/import engine behind both run_backfill (a filtered
    pending list from plan_pending_months) and catch_up_recent_gap (an
    explicit, unfiltered pending list -- it deliberately does NOT go through
    plan_pending_months's "any bar this month already exists -> skip" check,
    since a real few-day gap after being closed for a while very often falls
    inside a month that already has SOME data, which that check would wrongly
    treat as already covered)."""
    if pending:
        # Single-threaded, before any concurrent worker touches the database
        # -- avoids several workers racing each other on the WAL-mode switch
        # for a fresh (not-yet-WAL) file, a real confirmed source of
        # intermittent "database is locked" errors under concurrency.
        ensure_wal_mode(history_database)
    total = len(pending)
    ex_rights_years_done: set[int] = set()
    ex_rights_lock = threading.Lock()

    def ensure_ex_rights_fetched(year: int) -> None:
        with ex_rights_lock:
            if year in ex_rights_years_done:
                return
            ex_rights_years_done.add(year)
        try:
            # Market-wide per calendar year, not per symbol/month -- the
            # endpoint supports a full-year range query in one call.
            store_events(history_database, parse_ex_rights_events(fetch_ex_rights_events(date(year, 1, 1), date(year, 12, 31))))
        except Exception:
            pass  # Ex-dividend data is supplementary; never let it block the core price backfill.

    def process_one(symbol: str, year: int, month: int) -> tuple[bool, str | None]:
        year_month = f"{year:04d}{month:02d}"
        market = markets.get(symbol, "TWSE")
        ensure_ex_rights_fetched(year)
        now = datetime.now(timezone.utc)

        def attempt() -> None:
            if market == "TPEx":
                records = fetch_month_tpex(symbol, year, month)
                bars = parse_month_tpex(symbol, records, now)
                filename = imports_directory / f"tpex_backfill_{symbol}_{year_month}.csv"
            else:
                records = fetch_month(symbol, year, month)
                bars = parse_month(symbol, records, now)
                filename = imports_directory / f"twse_backfill_{symbol}_{year_month}.csv"
            if bars:
                write_normalized_csv(bars, filename)
                archive_and_import(filename, history_database, archive_directory)

        try:
            _retry_with_backoff(attempt, throttle_seconds)
            _record_progress(history_database, symbol, year_month, market, "done", now)
            time.sleep(throttle_seconds)  # this worker's own pacing before it picks up its next job
            return True, None
        except Exception as error:
            _record_progress(history_database, symbol, year_month, market, "failed", now)
            time.sleep(throttle_seconds)
            return False, f"{symbol} {year}-{month:02d}: {error}"

    succeeded = 0
    failed: list[str] = []
    attempted = 0
    stopped_early = False
    pending_iter = iter(pending)
    futures: dict = {}

    with ThreadPoolExecutor(max_workers=max(1, max_workers)) as executor:
        def submit_next() -> None:
            nonlocal stopped_early
            if should_stop is not None and should_stop():
                stopped_early = True
                return
            try:
                symbol, year, month = next(pending_iter)
            except StopIteration:
                return
            future = executor.submit(process_one, symbol, year, month)
            futures[future] = (symbol, year, month)

        for _ in range(min(max(1, max_workers), total)):
            submit_next()

        while futures:
            done, _not_yet = wait(list(futures.keys()), return_when=FIRST_COMPLETED)
            for finished in done:
                symbol, year, month = futures.pop(finished)
                attempted += 1
                ok, message = finished.result()
                if ok:
                    succeeded += 1
                else:
                    failed.append(message)
                if progress_callback is not None:
                    progress_callback(attempted, total, f"{symbol} {year}-{month:02d}")
                submit_next()

    return BackfillSummary(attempted, succeeded, tuple(failed), stopped_early)


def catch_up_recent_gap(
    history_database: Path, imports_directory: Path, archive_directory: Path,
    symbols: Iterable[str], as_of: date | None = None,
    throttle_seconds: float = DEFAULT_THROTTLE_SECONDS, max_workers: int = DEFAULT_MAX_WORKERS,
) -> BackfillSummary:
    """Force-refetch the current and previous calendar month for `symbols`,
    for real per-symbol per-day catch-up after the app hasn't been opened for
    a few days.

    The routine "daily update" (update_manager.run_all_public_daily_updates)
    only ever calls TWSE/TPEx's whole-market "today's snapshot" endpoints --
    confirmed they take no date parameter at all, so they cannot retroactively
    fill in days the app was closed for. This reuses the per-symbol STOCK_DAY/
    tradingStock endpoints (the same ones historical_backfill already uses)
    to genuinely close that gap for the symbols the user actually tracks.

    Deliberately bypasses plan_pending_months's "any bar this month already
    exists -> skip" logic (appropriate for a bulk multi-year backfill, where
    "this month has data" really does mean done) -- a few-days gap after
    being closed almost always falls inside a month that already has SOME
    data, which that check would then wrongly treat as already covered.
    Always re-fetching the current + previous month is cheap (at most 2
    requests per symbol) and correct regardless of which days are missing;
    archive_and_import's INSERT OR REPLACE makes the re-fetch idempotent for
    days that were already present.
    """
    symbols = list(symbols)
    if not symbols:
        return BackfillSummary(0, 0, (), False)
    as_of = as_of or date.today()
    previous_month_date = date(as_of.year, as_of.month, 1) - timedelta(days=1)
    months = [(as_of.year, as_of.month), (previous_month_date.year, previous_month_date.month)]
    markets = {symbol: (lookup_market(history_database, symbol) or "TWSE") if history_database.exists() else "TWSE" for symbol in symbols}
    pending = [(symbol, year, month) for symbol in symbols for year, month in months]
    return _run_pending_months(
        history_database, imports_directory, archive_directory, pending, markets,
        throttle_seconds, max_workers, None, None,
    )


def _record_progress(history_database: Path, symbol: str, year_month: str, market: str, status: str, attempted_at: datetime) -> None:
    history_database.parent.mkdir(parents=True, exist_ok=True)
    with database_connection(history_database) as connection:
        ensure_schema(connection)
        connection.execute(
            "INSERT OR REPLACE INTO backfill_progress VALUES (?, ?, ?, ?, ?)",
            (symbol, year_month, market, status, attempted_at.isoformat()),
        )
