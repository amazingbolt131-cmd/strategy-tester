"""Binance USDT-M futures klines.

The futures REST host (fapi.binance.com) refuses this network with HTTP 451.
Daily files on data.binance.vision are the same public USDT-M perpetual
klines, published by Binance. They are downloaded once and cached as CSV.
Higher timeframes are resampled from 1m so every strategy shares one tape.
"""

from __future__ import annotations

import csv
import io
import threading
import time
import zipfile
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import numpy as np

from .config import DEFAULT_PERIOD, PERIODS, WARMUP_DAYS

CACHE = Path(__file__).resolve().parent.parent / "data" / "cache"
VISION = "https://data.binance.vision/data/futures/um/daily/klines"
USER_AGENT = "strategy-backtester/1.0"

_LOCK = threading.Lock()
_MEMO: "OrderedDict[tuple[str, int, int], Bars]" = OrderedDict()
_MEMO_MAX = 5


@dataclass
class Bars:
    open_time: np.ndarray
    open: np.ndarray
    high: np.ndarray
    low: np.ndarray
    close: np.ndarray
    volume: np.ndarray

    def __len__(self) -> int:
        return int(len(self.open_time))


def _as_utc_date(ms: int) -> date:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).date()


def latest_published_day(symbol: str = "BTCUSDT") -> date:
    """Newest UTC day whose daily futures kline file is on the archive."""
    today = datetime.now(timezone.utc).date()
    for back in range(0, 6):
        day = today - timedelta(days=back)
        url = f"{VISION}/{symbol}/1h/{symbol}-1h-{day:%Y-%m-%d}.zip"
        req = Request(url, method="HEAD", headers={"User-Agent": USER_AGENT})
        try:
            with urlopen(req, timeout=20) as resp:
                if resp.status == 200:
                    return day
        except HTTPError as exc:
            if exc.code == 404:
                continue
            raise
    raise RuntimeError("No recent Binance futures kline files were published")


def test_window(last_day: date | None = None) -> tuple[int, int, int]:
    """Return warmup_start_ms, test_start_ms, test_end_ms (end exclusive).

    The test is the 30 UTC days ending at the close of the last published day.
    """
    last_day = last_day or latest_published_day()
    end = datetime(last_day.year, last_day.month, last_day.day, tzinfo=timezone.utc) + timedelta(days=1)
    end_ms = int(end.timestamp() * 1000)
    start_ms = end_ms - 30 * 24 * 60 * 60 * 1000
    warmup_ms = start_ms - WARMUP_DAYS * 24 * 60 * 60 * 1000
    return warmup_ms, start_ms, end_ms


def period_window(period: str = DEFAULT_PERIOD, last_day: date | None = None) -> tuple[int, int, int]:
    """Warmup, start, end for a dropdown period. "1m" is exactly test_window()."""
    if period not in PERIODS:
        raise ValueError(f"Unknown period {period}")
    _, _, end_ms = test_window(last_day)
    meta = PERIODS[period]
    if meta.get("start"):
        start = datetime.strptime(meta["start"], "%Y-%m-%d").replace(tzinfo=timezone.utc)
        start_ms = int(start.timestamp() * 1000)
    else:
        start_ms = end_ms - int(meta["days"]) * 24 * 60 * 60 * 1000
    warmup_ms = start_ms - WARMUP_DAYS * 24 * 60 * 60 * 1000
    return warmup_ms, start_ms, end_ms


# Ranking default: one full year. The user explicitly opened this window,
# including the former 2026-07-01 to 2026-10-25 holdout, for Ranking only.
# Run and Start still use test_window() (last 30 published days).
RANKING_START = datetime(2025, 11, 25, tzinfo=timezone.utc)
RANKING_END = datetime(2026, 11, 25, tzinfo=timezone.utc)
RANKING_WINDOW_LABEL = (
    "2025-11-25 00:00 → 2026-11-25 00:00 UTC · 1 year · "
    "includes the former 2026-07-01 to 2026-10-25 holdout"
)


def ranking_window() -> tuple[int, int, int]:
    """Warmup + 1-year Ranking window [2025-11-25, 2026-11-25) UTC."""
    start_ms = int(RANKING_START.timestamp() * 1000)
    end_ms = int(RANKING_END.timestamp() * 1000)
    warmup_ms = start_ms - WARMUP_DAYS * 24 * 60 * 60 * 1000
    return warmup_ms, start_ms, end_ms


def _cache_path(symbol: str, day: date) -> Path:
    return CACHE / f"{symbol}-1m-{day:%Y-%m-%d}.csv"


def _download_day(symbol: str, day: date) -> Path:
    path = _cache_path(symbol, day)
    if path.exists() and path.stat().st_size > 0:
        return path
    url = f"{VISION}/{symbol}/1m/{symbol}-1m-{day:%Y-%m-%d}.zip"
    req = Request(url, headers={"User-Agent": USER_AGENT})
    payload = None
    last_error: Exception | None = None
    for attempt in range(3):
        try:
            with urlopen(req, timeout=60) as resp:
                payload = resp.read()
            break
        except HTTPError as exc:
            if exc.code == 404:
                raise RuntimeError(f"Missing futures klines {symbol} {day:%Y-%m-%d}: HTTP 404") from exc
            last_error = exc
            time.sleep(0.4 * (attempt + 1))
        except Exception as exc:  # noqa: BLE001 - network retries
            last_error = exc
            time.sleep(0.4 * (attempt + 1))
    if payload is None:
        raise RuntimeError(f"Failed to download {symbol} {day:%Y-%m-%d}: {last_error}")
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        name = next(n for n in archive.namelist() if n.endswith(".csv"))
        text = archive.read(name).decode("utf-8")
    CACHE.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".csv.tmp")
    tmp.write_text(text)
    tmp.replace(path)
    return path


def _read_day(path: Path) -> Bars:
    times, opens, highs, lows, closes, vols = [], [], [], [], [], []
    with path.open(newline="") as handle:
        reader = csv.reader(handle)
        for row in reader:
            if not row:
                continue
            head = row[0].strip()
            if not head or head[0] not in "0123456789":
                continue
            times.append(int(float(head)))
            opens.append(float(row[1]))
            highs.append(float(row[2]))
            lows.append(float(row[3]))
            closes.append(float(row[4]))
            vols.append(float(row[5]))
    return Bars(
        open_time=np.asarray(times, dtype=np.int64),
        open=np.asarray(opens, dtype=float),
        high=np.asarray(highs, dtype=float),
        low=np.asarray(lows, dtype=float),
        close=np.asarray(closes, dtype=float),
        volume=np.asarray(vols, dtype=float),
    )


def load_1m(symbol: str, start_ms: int, end_ms: int, skip_missing_days: bool = False) -> Bars:
    """1m bars covering [start_ms, end_ms). Cached on disk and in memory."""
    key = (symbol, start_ms, end_ms)
    with _LOCK:
        cached = _MEMO.get(key)
        if cached is not None:
            _MEMO.move_to_end(key)
            return cached
        for (c_sym, c_start, c_end), c_bars in _MEMO.items():
            if c_sym == symbol and c_start <= start_ms and c_end >= end_ms:
                _MEMO.move_to_end((c_sym, c_start, c_end))
                mask = (c_bars.open_time >= start_ms) & (c_bars.open_time < end_ms)
                return Bars(
                    open_time=c_bars.open_time[mask],
                    open=c_bars.open[mask],
                    high=c_bars.high[mask],
                    low=c_bars.low[mask],
                    close=c_bars.close[mask],
                    volume=c_bars.volume[mask],
                )

    start_day = _as_utc_date(start_ms)
    end_day = _as_utc_date(end_ms - 1)
    days = []
    day = start_day
    while day <= end_day:
        days.append(day)
        day += timedelta(days=1)

    missing = [d for d in days if not (_cache_path(symbol, d).exists() and _cache_path(symbol, d).stat().st_size > 0)]
    if missing:
        workers = min(12, len(missing))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(_download_day, symbol, d): d for d in missing}
            for future in as_completed(futures):
                day = futures[future]
                try:
                    future.result()
                except RuntimeError as exc:
                    if skip_missing_days and "HTTP 404" in str(exc):
                        continue
                    raise

    existing = [d for d in days if _cache_path(symbol, d).exists() and _cache_path(symbol, d).stat().st_size > 0]
    if skip_missing_days:
        days = existing
    if not days:
        raise RuntimeError(f"No 1m futures bars for {symbol}")

    parts = [_read_day(_cache_path(symbol, d)) for d in days]
    bars = Bars(
        open_time=np.concatenate([p.open_time for p in parts]),
        open=np.concatenate([p.open for p in parts]),
        high=np.concatenate([p.high for p in parts]),
        low=np.concatenate([p.low for p in parts]),
        close=np.concatenate([p.close for p in parts]),
        volume=np.concatenate([p.volume for p in parts]),
    )
    order = np.argsort(bars.open_time, kind="mergesort")
    bars = Bars(
        open_time=bars.open_time[order],
        open=bars.open[order],
        high=bars.high[order],
        low=bars.low[order],
        close=bars.close[order],
        volume=bars.volume[order],
    )
    # Drop duplicate minutes if a file ever overlaps.
    _, unique_idx = np.unique(bars.open_time, return_index=True)
    unique_idx.sort()
    if len(unique_idx) != len(bars):
        bars = Bars(
            open_time=bars.open_time[unique_idx],
            open=bars.open[unique_idx],
            high=bars.high[unique_idx],
            low=bars.low[unique_idx],
            close=bars.close[unique_idx],
            volume=bars.volume[unique_idx],
        )
    mask = (bars.open_time >= start_ms) & (bars.open_time < end_ms)
    bars = Bars(
        open_time=bars.open_time[mask],
        open=bars.open[mask],
        high=bars.high[mask],
        low=bars.low[mask],
        close=bars.close[mask],
        volume=bars.volume[mask],
    )
    if len(bars) == 0:
        raise RuntimeError(f"No 1m futures bars for {symbol}")
    with _LOCK:
        _MEMO[key] = bars
        _MEMO.move_to_end(key)
        while len(_MEMO) > _MEMO_MAX:
            _MEMO.popitem(last=False)
    return bars


def clear_memo() -> None:
    """Drop the in-memory 1m cache (the disk cache stays). Used between coins."""
    with _LOCK:
        _MEMO.clear()


def resample(bars: Bars, minutes: int) -> Bars:
    if minutes <= 1:
        return bars
    span = minutes * 60_000
    bucket = (bars.open_time // span) * span
    cuts = np.flatnonzero(np.diff(bucket)) + 1
    starts = np.r_[0, cuts]
    ends = np.r_[cuts, len(bucket)]
    ot, op, hi, lo, cl, vo = [], [], [], [], [], []
    for start, end in zip(starts, ends):
        if int(end - start) < minutes:
            continue
        ot.append(int(bucket[start]))
        op.append(float(bars.open[start]))
        hi.append(float(np.max(bars.high[start:end])))
        lo.append(float(np.min(bars.low[start:end])))
        cl.append(float(bars.close[end - 1]))
        vo.append(float(np.sum(bars.volume[start:end])))
    return Bars(
        open_time=np.asarray(ot, dtype=np.int64),
        open=np.asarray(op, dtype=float),
        high=np.asarray(hi, dtype=float),
        low=np.asarray(lo, dtype=float),
        close=np.asarray(cl, dtype=float),
        volume=np.asarray(vo, dtype=float),
    )


def align_asof(published_ms: np.ndarray, values: np.ndarray, target_ms: np.ndarray) -> np.ndarray:
    """Last value whose publication time is <= each target time."""
    published_ms = np.asarray(published_ms, dtype=np.int64)
    values = np.asarray(values)
    target_ms = np.asarray(target_ms, dtype=np.int64)
    idx = np.searchsorted(published_ms, target_ms, side="right") - 1
    out = np.zeros(len(target_ms), dtype=int)
    ok = idx >= 0
    out[ok] = values[idx[ok]]
    return out


TF_MINUTES = {"1m": 1, "5m": 5, "15m": 15, "30m": 30, "1h": 60, "4h": 240, "1d": 1440}


def load_tf(symbol: str, timeframe: str, start_ms: int, end_ms: int, skip_missing_days: bool = False) -> Bars:
    base = load_1m(symbol, start_ms, end_ms, skip_missing_days=skip_missing_days)
    return resample(base, TF_MINUTES[timeframe])
