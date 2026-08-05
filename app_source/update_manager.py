"""Update status registry and manual public end-of-day data refreshes."""

import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from database_utils import database_connection

from historical_storage import archive_and_import
from historical_storage import verify_archive
from tpex_daily_importer import fetch_current_daily_json as fetch_tpex, parse_daily_records as parse_tpex, extract_security_names as extract_tpex_names
from twse_daily_importer import fetch_current_daily_json as fetch_twse, parse_daily_records as parse_twse, write_normalized_csv, extract_security_names as extract_twse_names
from external_data_importers import fetch_fred_vix_csv, import_vix, parse_fred_vix_csv
import security_catalog
from dividend_adjustment import fetch_ex_rights_events, parse_ex_rights_events, store_events
from fundamentals_data import update_revenue_snapshots
from valuation_data import update_valuation_snapshots


SCHEDULES = {
    "TWSE 日行情": "每交易日約 16:00 後",
    "TPEx 日行情": "每交易日約 16:30 後",
    "TAIFEX 夜盤": "每交易日上午約 07:00 後",
    "VIX／全球風險": "美股收盤後，台灣時間約 07:00–09:00",
    "GAP 個股缺口補齊": "開機時自動執行，每日至多一次",
}


@dataclass(frozen=True, slots=True)
class UpdateStatus:
    source: str
    scheduled_time: str
    last_updated_at: datetime | None
    status: str
    detail: str


def initialize(database: Path) -> None:
    database.parent.mkdir(parents=True, exist_ok=True)
    with database_connection(database) as connection:
        connection.execute("""
            CREATE TABLE IF NOT EXISTS data_update_status (
                source TEXT PRIMARY KEY, last_updated_at TEXT, status TEXT NOT NULL, detail TEXT NOT NULL
            )
        """)
        for source in SCHEDULES:
            connection.execute("INSERT OR IGNORE INTO data_update_status VALUES (?, NULL, '尚未更新', '')", (source,))


def record_status(database: Path, source: str, status: str, detail: str, timestamp: datetime | None = None) -> None:
    if source not in SCHEDULES:
        raise ValueError("unknown data source")
    initialize(database)
    timestamp = timestamp or datetime.now().astimezone()
    with database_connection(database) as connection:
        connection.execute("INSERT OR REPLACE INTO data_update_status VALUES (?, ?, ?, ?)", (source, timestamp.isoformat(), status, detail))


def list_statuses(database: Path) -> list[UpdateStatus]:
    initialize(database)
    with database_connection(database) as connection:
        rows = connection.execute("SELECT source, last_updated_at, status, detail FROM data_update_status ORDER BY source").fetchall()
    return [UpdateStatus(source, SCHEDULES[source], None if updated is None else datetime.fromisoformat(updated), status, detail) for source, updated, status, detail in rows]


def _grow_securities_catalog(history_database: Path, entries: list[tuple[str, str]], market: str, now: datetime) -> None:
    """Best-effort catalog growth; a hiccup here must never block the core daily-bar import."""
    try:
        security_catalog.upsert_from_daily_snapshot(history_database, entries, market, now.isoformat())
    except Exception:
        pass


def run_manual_update(source: str, history_database: Path, imports_directory: Path, archive_directory: Path) -> str:
    """Fetch/import supported free public EOD sources and audit outcome in SQLite."""
    now = datetime.now(ZoneInfo("Asia/Taipei"))
    try:
        if source == "TWSE 日行情":
            records = fetch_twse()
            bars = parse_twse(records, now.date(), now)
            _grow_securities_catalog(history_database, extract_twse_names(records), "TWSE", now)
            filename = imports_directory / f"twse_daily_{now:%Y%m%d}.csv"
        elif source == "TPEx 日行情":
            records = fetch_tpex()
            bars = parse_tpex(records, now.date(), now)
            _grow_securities_catalog(history_database, extract_tpex_names(records), "TPEx", now)
            filename = imports_directory / f"tpex_daily_{now:%Y%m%d}.csv"
        elif source.startswith("VIX"):
            records = parse_fred_vix_csv(fetch_fred_vix_csv())
            inserted = import_vix(history_database, records)
            message = f"成功匯入 VIX {inserted} 筆"
            record_status(history_database, source, "成功", message, now)
            return message
        else:
            record_status(history_database, source, "尚未接入", "此來源的公開資料匯入器尚未完成；未寫入任何資料。", now)
            return "此來源的匯入器尚未完成。"
        write_normalized_csv(bars, filename)
        checksum, inserted = archive_and_import(filename, history_database, archive_directory)
        message = f"成功匯入 {inserted} 筆資料；校驗值 {checksum[:12]}…"
        record_status(history_database, source, "成功", message, now)
        return message
    except Exception as error:
        message = f"更新失敗：{error}"
        record_status(history_database, source, "失敗", message, now)
        return message

def run_all_public_daily_updates(history_database: Path, imports_directory: Path, archive_directory: Path, decision_database: Path | None = None) -> str:
    """Update the free TWSE/TPEx all-stock daily snapshots and verify archives."""
    automatic = [source for source in SCHEDULES if source.startswith(("TWSE", "TPEx", "VIX"))]
    manual_only = [source for source in SCHEDULES if source.startswith(("TAIFEX",))]
    results=[run_manual_update(source,history_database,imports_directory,archive_directory) for source in automatic]
    results.extend(f"{source}：需要官方下載檔匯入，未寫入資料" for source in manual_only)
    try:
        security_catalog.upsert_sectors(history_database, security_catalog.parse_company_basic_info(security_catalog.fetch_company_basic_info()))
    except Exception:
        pass  # Sector classification is supplementary; never let it fail the whole daily update.
    try:
        today = datetime.now(ZoneInfo("Asia/Taipei")).date()
        # A rolling recent window: catches newly announced upcoming events and
        # keeps recently-passed ones current, without re-fetching all history daily.
        store_events(history_database, parse_ex_rights_events(fetch_ex_rights_events(today - timedelta(days=30), today + timedelta(days=30))))
    except Exception as error:
        # Ex-dividend data is supplementary; never let it fail the whole daily
        # update. But this used to be a bare "except: pass" -- if TWSE's
        # ex-rights endpoint starts failing (confirmed live: it can return a
        # bare 307 with no Location header), dividend-adjusted prices would
        # silently go stale with zero visibility anywhere in the app. Record
        # it so a persistent failure is at least discoverable in 通知中心.
        if decision_database is not None:
            try:
                from notification_center import record_notification
                record_notification(decision_database, "ex_rights_fetch_failed", "", f"除權息事件更新失敗（{type(error).__name__}）；除權息還原可能使用過期資料。", datetime.now().astimezone())
            except Exception:
                pass
    try:
        update_valuation_snapshots(history_database)
    except Exception:
        pass  # Valuation (PE/yield/PB) is supplementary; never let it fail the whole daily update.
    try:
        update_revenue_snapshots(history_database)
    except Exception:
        pass  # Monthly revenue is supplementary; never let it fail the whole daily update.
    errors=verify_archive(history_database)
    return "；".join(results)+("；封存驗證失敗："+"、".join(errors) if errors else "；封存驗證通過")

def run_startup_check(history_database: Path, imports_directory: Path, archive_directory: Path, now: datetime | None = None, decision_database: Path | None = None) -> str:
    """Skip downloads when today's scheduled public files are already verified."""
    # Local import: update_scheduler imports SCHEDULES from this module, so a
    # module-level import here would be circular.
    from update_scheduler import due_sources
    now=now or datetime.now(ZoneInfo("Asia/Taipei")); statuses=list_statuses(history_database)
    completed={item.source for item in statuses if item.last_updated_at and item.last_updated_at.date()==now.date() and item.status=="成功"}
    # due_sources() is the single, fully-tested schedule definition (covers
    # TAIFEX and VIX too); manual_only sources like TAIFEX have no automatic
    # fetcher and are filtered out below rather than duplicating their timing here.
    due=[source for source in due_sources(now, completed) if source.startswith(("TWSE","TPEx","VIX"))]
    if verify_archive(history_database): result = run_all_public_daily_updates(history_database,imports_directory,archive_directory,decision_database)
    elif not due: result = "歷史資料已通過封存驗證，且未到下一個更新時間；跳過下載。"
    else: result = "；".join(run_manual_update(source,history_database,imports_directory,archive_directory) for source in due)
    gap_source = "GAP 個股缺口補齊"
    if gap_source not in completed:
        gap_result = _catch_up_tracked_symbols_gap(history_database, imports_directory, archive_directory, decision_database, now)
        if gap_result:
            result = f"{result}；{gap_result}"
    return result


def _catch_up_tracked_symbols_gap(history_database: Path, imports_directory: Path, archive_directory: Path, decision_database: Path | None, now: datetime) -> str:
    """Best-effort per-symbol catch-up for the user's own tracked (holdings +
    watchlist) symbols after the app was closed for a while: the whole-market
    snapshot endpoints run_all_public_daily_updates uses can only ever fetch
    "today" (confirmed -- neither TWSE's STOCK_DAY_ALL nor TPEx's
    tpex_mainboard_daily_close_quotes accepts a date parameter at all), so
    they structurally cannot retroactively fill in days the app was closed
    for. This reuses the per-symbol STOCK_DAY/tradingStock backfill endpoints
    (which DO support past months) to genuinely close that gap. Returns ""
    (silently skipped, never blocks the core daily update) when there is no
    decision_database to read tracked symbols from, or on any error."""
    if decision_database is None:
        return ""
    try:
        from transaction_ledger import calculate_holdings
        from watchlist_repository import list_items
        from historical_backfill import catch_up_recent_gap
        symbols = sorted({item.symbol for item in calculate_holdings(decision_database)} | {item.symbol for item in list_items(decision_database)})
        if not symbols:
            record_status(history_database, "GAP 個股缺口補齊", "成功", "無持股或自選股，略過。", now)
            return ""
        summary = catch_up_recent_gap(history_database, imports_directory, archive_directory, symbols, as_of=now.date())
        message = f"個股缺口補齊 {summary.succeeded}/{summary.attempted} 成功"
        record_status(history_database, "GAP 個股缺口補齊", "成功" if not summary.failed else "失敗", message, now)
        return message
    except Exception as error:
        record_status(history_database, "GAP 個股缺口補齊", "失敗", f"個股缺口補齊失敗：{error}", now)
        return ""
