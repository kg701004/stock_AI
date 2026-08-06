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
from external_data_importers import fetch_fred_vix_csv, fetch_taifex_daily_report, fetch_tpex_index, fetch_tpex_institutional_flow_report, fetch_twse_index, fetch_twse_institutional_flow_report, import_institutional_flow, import_market_indices, import_taifex, import_vix, parse_fred_vix_csv, parse_taifex_daily_report, parse_tpex_institutional_flow_report, parse_twse_institutional_flow_report
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
    "REVERSAL 短期反彈檢查": "開機時自動執行，每日至多一次",
    "DRIFT 配置偏離檢查": "開機時自動執行，每日至多一次",
    "MARKET_INDEX 大盤櫃買指數": "開機時自動執行，每日至多一次",
    "INSTITUTIONAL_FLOW 三大法人買賣超": "開機時自動執行，每日至多一次",
    "ARCHIVE 封存完整性驗證": "開機時自動執行，每日至多一次",
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


def _notify_data_update_failure(decision_database: Path | None, source: str, message: str, now: datetime) -> None:
    """Best-effort; a notification-log write failure must never mask the
    original update failure it's trying to surface."""
    if decision_database is None:
        return
    try:
        from notification_center import record_notification
        record_notification(decision_database, "data_update_failed", str(source), f"{source}：{message}", now)
    except Exception:
        pass


def run_manual_update(source: str, history_database: Path, imports_directory: Path, archive_directory: Path, decision_database: Path | None = None) -> str:
    """Fetch/import supported free public EOD sources and audit outcome in SQLite.

    decision_database is optional and, when given, durably notifies on
    failure -- previously only the manual "更新" button's caller notified
    (by wrapping the returned message itself); the automatic startup-check
    path below fed the same failures only into the 資料管理 status table,
    invisible unless the user opened that tab. Left unwired from the manual
    button's own call sites in stock_ai_app.py so those keep notifying
    exactly once via their existing post-call record_notification, not twice."""
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
        elif source.startswith("TAIFEX"):
            # Live-verified 2026-08-06: openapi.taifex.com.tw's
            # DailyMarketReportFut is a real, free, working endpoint covering
            # both trading sessions (regular + after-hours/夜盤) -- contrary
            # to this project's earlier notes, night-session futures data
            # does not actually require an official downloaded file.
            parsed = parse_taifex_daily_report(fetch_taifex_daily_report())
            inserted = import_taifex(history_database, parsed)
            message = f"成功匯入 TAIFEX 日盤／盤後 {inserted} 筆"
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
        _notify_data_update_failure(decision_database, source, message, now)
        return message

def run_all_public_daily_updates(history_database: Path, imports_directory: Path, archive_directory: Path, decision_database: Path | None = None) -> str:
    """Update the free TWSE/TPEx all-stock daily snapshots and verify archives."""
    # TAIFEX moved from manual_only to automatic 2026-08-06: DailyMarketReportFut
    # (openapi.taifex.com.tw) is a real, free, working endpoint -- confirmed live --
    # covering both trading sessions, so it no longer needs an official downloaded file.
    automatic = [source for source in SCHEDULES if source.startswith(("TWSE", "TPEx", "VIX", "TAIFEX"))]
    results=[run_manual_update(source,history_database,imports_directory,archive_directory,decision_database) for source in automatic]
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
    errors=_verify_archive_safe(history_database)
    return "；".join(results)+("；封存驗證失敗："+"、".join(errors) if errors else "；封存驗證通過")

def _verify_archive_safe(history_database: Path) -> list[str]:
    """verify_archive can itself raise -- a corrupted gzip header or a
    permission error during hashing -- rather than returning an error list;
    historical_storage.verify_archive has no try/except around its per-file
    work. Confirmed: an unguarded call here previously meant one bad archived
    file could abort run_startup_check entirely before GAP/REVERSAL/DRIFT/
    MARKET_INDEX ever ran. Treat a raised exception the same as a returned
    error -- something is wrong with the archive either way."""
    try:
        return verify_archive(history_database)
    except Exception as error:
        return [f"驗證過程發生例外：{type(error).__name__}：{error}"]


def run_startup_check(history_database: Path, imports_directory: Path, archive_directory: Path, now: datetime | None = None, decision_database: Path | None = None) -> str:
    """Skip downloads when today's scheduled public files are already verified."""
    # Local import: update_scheduler imports SCHEDULES from this module, so a
    # module-level import here would be circular.
    from update_scheduler import due_sources
    now=now or datetime.now(ZoneInfo("Asia/Taipei")); statuses=list_statuses(history_database)
    completed={item.source for item in statuses if item.last_updated_at and item.last_updated_at.date()==now.date() and item.status=="成功"}
    # due_sources() is the single, fully-tested schedule definition. TAIFEX
    # used to be excluded here (no automatic fetcher existed), but now has a
    # real one (openapi.taifex.com.tw) -- see run_manual_update.
    due=[source for source in due_sources(now, completed) if source.startswith(("TWSE","TPEx","VIX","TAIFEX"))]
    archive_source = "ARCHIVE 封存完整性驗證"
    if archive_source in completed:
        # Already verified clean today -- skip re-hashing every archived file.
        # verify_archive checksums every .csv.gz in raw_archive; confirmed by
        # actually running the app that this had grown to 13,000+ real files
        # after a session of historical backfills, making every single
        # startup take multiple minutes for a check whose whole point is
        # catching rare bit-rot/tampering, not a per-launch necessity (the
        # manual "檢查完整性" button remains available for an on-demand deep
        # check any time). A real failure is NOT cached this way below --
        # only a clean pass counts as "completed today", so genuine
        # corruption keeps getting re-flagged on every launch until fixed.
        archive_errors: list[str] = []
    else:
        archive_errors = _verify_archive_safe(history_database)
        record_status(
            history_database, archive_source,
            "成功" if not archive_errors else "失敗",
            "封存驗證通過" if not archive_errors else f"封存驗證失敗：{'、'.join(archive_errors)}",
            now,
        )
        if archive_errors:
            _notify_data_update_failure(decision_database, archive_source, "、".join(archive_errors), now)
    if archive_errors: result = run_all_public_daily_updates(history_database,imports_directory,archive_directory,decision_database)
    elif not due: result = "歷史資料已通過封存驗證，且未到下一個更新時間；跳過下載。"
    else: result = "；".join(run_manual_update(source,history_database,imports_directory,archive_directory,decision_database) for source in due)
    gap_source = "GAP 個股缺口補齊"
    if gap_source not in completed:
        gap_result = _catch_up_tracked_symbols_gap(history_database, imports_directory, archive_directory, decision_database, now)
        if gap_result:
            result = f"{result}；{gap_result}"

    reversal_source = "REVERSAL 短期反彈檢查"
    if reversal_source not in completed and decision_database is not None:
        try:
            from notification_center import check_short_term_reversal_triggers
            fired = check_short_term_reversal_triggers(decision_database, history_database, now)
            message = f"短期反彈檢查完成，觸發 {len(fired)} 筆"
            record_status(history_database, reversal_source, "成功", message, now)
            result = f"{result}；{message}"
        except Exception as error:
            record_status(history_database, reversal_source, "失敗", f"短期反彈檢查失敗：{error}", now)
            _notify_data_update_failure(decision_database, reversal_source, str(error), now)

    drift_source = "DRIFT 配置偏離檢查"
    if drift_source not in completed and decision_database is not None:
        try:
            from notification_center import check_allocation_drift
            fired = check_allocation_drift(decision_database, history_database, now=now)
            message = f"配置偏離檢查完成，觸發 {len(fired)} 筆"
            record_status(history_database, drift_source, "成功", message, now)
            result = f"{result}；{message}"
        except Exception as error:
            record_status(history_database, drift_source, "失敗", f"配置偏離檢查失敗：{error}", now)
            _notify_data_update_failure(decision_database, drift_source, str(error), now)

    market_index_source = "MARKET_INDEX 大盤櫃買指數"
    if market_index_source not in completed:
        # market_context_factor_score (the "sentiment" auto factor) reads
        # market_index_history -- previously nothing ever called the
        # fetchers, so the factor silently stayed at the neutral 50
        # fallback forever. This was ALSO once nested inside
        # run_all_public_daily_updates, which only runs when TWSE/TPEx/VIX
        # have something due or the archive fails verification -- on an
        # otherwise-quiet day it would never fire at all. Standalone here
        # (like GAP/REVERSAL/DRIFT) guarantees one real attempt per day
        # regardless of the other sources' state.
        try:
            twse_close = fetch_twse_index(now.date())
            records = [(now.date(), "TWSE", twse_close)] if twse_close is not None else []
            records.extend((d, "TPEx", close) for d, close in fetch_tpex_index())
            if records:
                import_market_indices(history_database, records)
            message = f"大盤/櫃買指數更新完成，寫入 {len(records)} 筆"
            record_status(history_database, market_index_source, "成功", message, now)
        except Exception as error:
            record_status(history_database, market_index_source, "失敗", f"大盤/櫃買指數更新失敗：{error}", now)
            if decision_database is not None:
                try:
                    from notification_center import record_notification
                    record_notification(decision_database, "market_index_fetch_failed", "", f"大盤/櫃買指數更新失敗（{type(error).__name__}）；大盤情境因子可能使用過期或不足資料。", now)
                except Exception:
                    pass

    institutional_flow_source = "INSTITUTIONAL_FLOW 三大法人買賣超"
    if institutional_flow_source not in completed:
        # institutional_flow_factor_score reads institutional_flow_history --
        # standalone here (like GAP/REVERSAL/DRIFT/MARKET_INDEX) for the same
        # reason MARKET_INDEX is standalone: guarantees one real attempt per
        # day regardless of whether TWSE/TPEx/VIX have anything due. TWSE's
        # T86 covers 上市 symbols, TPEx's tpex_3insti_daily_trading covers
        # 上櫃 symbols -- together they're the full market.
        try:
            twse_records = parse_twse_institutional_flow_report(now.date(), fetch_twse_institutional_flow_report(now.date()))
            tpex_records = parse_tpex_institutional_flow_report(fetch_tpex_institutional_flow_report())
            inserted = import_institutional_flow(history_database, twse_records + tpex_records)
            message = f"三大法人買賣超更新完成，寫入 {inserted} 筆"
            record_status(history_database, institutional_flow_source, "成功", message, now)
        except Exception as error:
            record_status(history_database, institutional_flow_source, "失敗", f"三大法人買賣超更新失敗：{error}", now)
            _notify_data_update_failure(decision_database, institutional_flow_source, str(error), now)

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
        _notify_data_update_failure(decision_database, "GAP 個股缺口補齊", str(error), now)
        return ""
