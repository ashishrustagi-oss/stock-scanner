"""
Dhan API data layer — shared by all trading strategies.

Replaces yfinance for price data fetching. Uses Dhan's own historical
and intraday APIs, which are:
  - Real-time (same feed as live orders)
  - No rate limiting issues
  - No 60-day yfinance limitation for 15-min data (Dhan gives 5 trading days
    of intraday data, enough for ST(10,3) calculation)
  - Consistent with order execution (same security_id, same exchange)

All functions return a pandas DataFrame with lowercase OHLCV columns:
  open, high, low, close, volume
with a DatetimeIndex, ready for supertrend.py calculations.

Usage:
  from dhan_data import DhanData
  dd = DhanData(dhan_client)
  daily_df  = dd.get_daily(symbol, days=365)
  weekly_df = dd.get_weekly(symbol, weeks=104)
  intra_df  = dd.get_intraday(symbol, interval=15, days=5)
"""

import datetime
import logging

import pandas as pd
from dhanhq import dhanhq

logger = logging.getLogger(__name__)

# Security ID cache — shared across all DhanData instances in a run
_SECURITY_ID_CACHE: dict[str, str] = {}


_ltp_debug_logged = False


class DhanData:
    """
    Data fetcher using Dhan API.
    Instantiate once per trade cycle with the active dhanhq client.
    """

    INSTRUMENT = "EQUITY"
    EXCHANGE   = "NSE_EQ"

    def __init__(self, client: dhanhq):
        self.client = client

    def get_security_id(self, symbol: str) -> str | None:
        """Returns Dhan's numeric security_id for a given NSE symbol."""
        global _SECURITY_ID_CACHE

        if symbol in _SECURITY_ID_CACHE:
            return _SECURITY_ID_CACHE[symbol]

        if not _SECURITY_ID_CACHE:
            try:
                import csv, io, urllib.request
                url = dhanhq.COMPACT_CSV_URL
                with urllib.request.urlopen(url, timeout=30) as resp:
                    content = resp.read().decode("utf-8")
                    reader = csv.DictReader(io.StringIO(content))
                    for row in reader:
                        if row.get("SEM_EXM_EXCH_ID") == "NSE":
                            sym = row.get("SEM_TRADING_SYMBOL", "").upper().strip()
                            sid = row.get("SEM_SMST_SECURITY_ID", "").strip()
                            if sym and sid:
                                _SECURITY_ID_CACHE[sym] = sid
                logger.info("dhan_data: loaded %d NSE security IDs", len(_SECURITY_ID_CACHE))
            except Exception as exc:
                logger.error("dhan_data: security list load failed: %s", exc)
                return None

        sid = _SECURITY_ID_CACHE.get(symbol)
        if not sid:
            logger.debug("dhan_data: no security_id for %s", symbol)
        return sid

    def _parse_response(self, resp: dict, symbol: str) -> pd.DataFrame | None:
        """
        Parses Dhan historical/intraday API response into a clean OHLCV DataFrame.
        Dhan returns: {"data": {"open": [...], "high": [...], "low": [...],
                                "close": [...], "volume": [...], "timestamp": [...]}}
        """
        try:
            data = resp.get("data", {})
            if not data:
                logger.debug("dhan_data: empty response for %s: %s", symbol, resp)
                return None

            timestamps = data.get("timestamp", [])
            if not timestamps:
                return None

            df = pd.DataFrame({
                "open":   data.get("open",   []),
                "high":   data.get("high",   []),
                "low":    data.get("low",    []),
                "close":  data.get("close",  []),
                "volume": data.get("volume", []),
            }, index=pd.to_datetime(timestamps, unit="s", utc=True).tz_convert("Asia/Kolkata"))

            df = df.sort_index()
            df = df[df["close"] > 0]   # remove zero/invalid rows
            return df

        except Exception as exc:
            logger.error("dhan_data: parse error for %s: %s", symbol, exc)
            return None

    def get_daily(self, symbol: str, days: int = 400) -> pd.DataFrame | None:
        """
        Fetches daily OHLCV data for the past `days` calendar days.
        400 days gives ~280 trading days — enough for ST(10,3) + buffer.
        """
        sid = self.get_security_id(symbol)
        if not sid:
            return None

        to_date   = datetime.date.today().strftime("%Y-%m-%d")
        from_date = (datetime.date.today() - datetime.timedelta(days=days)).strftime("%Y-%m-%d")

        try:
            resp = self.client.historical_daily_data(
                security_id=sid,
                exchange_segment=self.EXCHANGE,
                instrument_type=self.INSTRUMENT,
                from_date=from_date,
                to_date=to_date,
            )
            df = self._parse_response(resp, symbol)
            if df is not None:
                logger.debug("dhan_data: daily %s — %d bars", symbol, len(df))
            return df
        except Exception as exc:
            logger.error("dhan_data: get_daily(%s) failed: %s", symbol, exc)
            return None

    def get_weekly(self, symbol: str, weeks: int = 104) -> pd.DataFrame | None:
        """
        Fetches weekly OHLCV by resampling daily data.
        Weekly data is derived from daily to ensure consistency and avoid
        separate API calls.
        """
        daily_df = self.get_daily(symbol, days=weeks * 7 + 30)
        if daily_df is None or len(daily_df) < 10:
            return None

        try:
            weekly_df = daily_df.resample("W").agg({
                "open":   "first",
                "high":   "max",
                "low":    "min",
                "close":  "last",
                "volume": "sum",
            }).dropna(subset=["close"])
            weekly_df = weekly_df[weekly_df["close"] > 0]
            logger.debug("dhan_data: weekly %s — %d bars", symbol, len(weekly_df))
            return weekly_df
        except Exception as exc:
            logger.error("dhan_data: get_weekly(%s) failed: %s", symbol, exc)
            return None

    def get_intraday(self, symbol: str, interval: int = 15,
                     days: int = 5) -> pd.DataFrame | None:
        """
        Fetches intraday OHLCV data.
        interval: 1, 5, 15, 25, or 60 minutes (Dhan supported values)
        days: number of trading days back (Dhan supports last 5 trading days)
        """
        sid = self.get_security_id(symbol)
        if not sid:
            return None

        to_date   = datetime.date.today().strftime("%Y-%m-%d")
        from_date = (datetime.date.today() - datetime.timedelta(days=days + 4)).strftime("%Y-%m-%d")

        try:
            resp = self.client.intraday_minute_data(
                security_id=sid,
                exchange_segment=self.EXCHANGE,
                instrument_type=self.INSTRUMENT,
                from_date=from_date,
                to_date=to_date,
                interval=interval,
            )
            df = self._parse_response(resp, symbol)
            if df is not None:
                logger.debug("dhan_data: intraday %s %dmin — %d bars", symbol, interval, len(df))
            return df
        except Exception as exc:
            logger.error("dhan_data: get_intraday(%s, %dmin) failed: %s", symbol, interval, exc)
            return None

    def get_ltp(self, symbol: str) -> float | None:
        """Returns last traded price for a single NSE symbol."""
        sid = self.get_security_id(symbol)
        if not sid:
            logger.warning("dhan_data: get_ltp(%s) — no security_id found", symbol)
            return None
        try:
            resp = self.client.ticker_data({"NSE_EQ": [int(sid)]})

            # Defensive: under load, Dhan's API can return a plain error
            # string (e.g. a rate-limit message) instead of the expected
            # JSON structure. Catch that explicitly rather than letting
            # `.get()` on a string raise a confusing AttributeError.
            if not isinstance(resp, dict):
                logger.warning(
                    "dhan_data: get_ltp(%s) — non-dict response (likely rate-limited): %r",
                    symbol, resp,
                )
                return None

            # The actual response is double-nested: resp["data"]["data"]["NSE_EQ"],
            # not resp["data"]["NSE_EQ"] as originally assumed. Try the real
            # (nested) shape first, fall back to the flatter one in case the
            # API's response shape varies by SDK version.
            outer_data = resp.get("data", {})
            if isinstance(outer_data, dict) and "data" in outer_data:
                nse_eq = outer_data.get("data", {}).get("NSE_EQ", {})
            else:
                nse_eq = outer_data.get("NSE_EQ", {}) if isinstance(outer_data, dict) else {}

            item = nse_eq.get(str(sid)) or nse_eq.get(sid) or {}
            ltp = item.get("last_price") or item.get("LTP")
            if not ltp:
                global _ltp_debug_logged
                if not _ltp_debug_logged:
                    logger.warning(
                        "dhan_data: get_ltp(%s) — no price in response. sid=%s raw_resp=%s "
                        "(further no-price failures this run will log a short message only)",
                        symbol, sid, resp,
                    )
                    _ltp_debug_logged = True
                else:
                    logger.warning("dhan_data: get_ltp(%s) — no price in response (sid=%s)", symbol, sid)
            return float(ltp) if ltp else None
        except Exception as exc:
            logger.warning("dhan_data: get_ltp(%s) failed: %s", symbol, exc)
            return None
