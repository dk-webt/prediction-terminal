"""
Model B: Oracle Divergence — Ornstein-Uhlenbeck spread analysis.

Provides:
  - SpreadAnalyzer: forward-fills aligned ticks to uniform 1s intervals,
    runs rolling ADF test for stationarity, calibrates OU parameters.

Usage:
    from clients.oracle_model import SpreadAnalyzer

    analyzer = SpreadAnalyzer(window_s=1200)  # 20-min rolling window
    analyzer.add_tick(bin_ts=1700000000, spread=1.23)
    result = analyzer.compute_adf()
"""

import logging
import numpy as np
from dataclasses import dataclass
from statsmodels.tsa.stattools import adfuller

log = logging.getLogger(__name__)

ADF_PVALUE_THRESHOLD = 0.05  # reject null (random walk) if p < 0.05
MIN_TICKS_FOR_ADF = 60       # need at least 60 filled 1s observations
MIN_TICKS_FOR_OU = 60        # need at least 60 filled 1s observations for OU calibration
DELTA_T = 1.0                # time step in seconds (forward-filled to 1s intervals)


@dataclass
class OUParams:
    """Ornstein-Uhlenbeck parameters calibrated from AR(1) regression."""
    theta: float        # speed of reversion (1/s); higher = faster snap-back
    mu: float           # long-term equilibrium spread ($)
    sigma: float        # spread volatility ($/√s)
    half_life_s: float  # time for spread to revert 50% toward mu (seconds)
    b: float            # AR(1) slope coefficient (0 < b < 1 for mean reversion)
    a: float            # AR(1) intercept
    residual_std: float # std(epsilon) from the regression
    n_obs: int          # number of observations used


@dataclass
class ADFResult:
    """Result of the Augmented Dickey-Fuller test on the spread."""
    statistic: float       # ADF test statistic (more negative = stronger mean reversion)
    pvalue: float          # p-value; < 0.05 means stationary / mean-reverting
    is_stationary: bool    # True if p < ADF_PVALUE_THRESHOLD
    n_obs: int             # number of observations used (after forward-fill)
    n_raw: int             # number of raw (non-filled) ticks in the window
    fill_pct: float        # percentage of observations that were forward-filled
    critical_values: dict  # {'1%': x, '5%': x, '10%': x}
    spread_mean: float     # mean spread in window
    spread_std: float      # std of spread in window


class SpreadAnalyzer:
    """
    Maintains a rolling window of oracle spread data with forward-fill
    and computes the ADF test for stationarity.

    Forward-fill: raw aligned ticks arrive at irregular intervals (e.g.
    t=0, t=3, t=4). We fill to uniform 1-second spacing by carrying
    forward the last known spread value. This ensures equal delta-t
    for the ADF regression.
    """

    def __init__(self, window_s: int = 1200):
        """
        Args:
            window_s: rolling window size in seconds (default 1200 = 20 min)
        """
        self.window_s = window_s
        # Raw ticks: list of (bin_ts, spread) — kept sorted by bin_ts
        self._raw: list[tuple[int, float]] = []

    def add_tick(self, bin_ts: int, spread: float):
        """Add an aligned tick. Duplicate bin_ts overwrites the previous value."""
        if self._raw and bin_ts <= self._raw[-1][0]:
            # Overwrite if same bin, ignore if older
            if bin_ts == self._raw[-1][0]:
                self._raw[-1] = (bin_ts, spread)
            return
        self._raw.append((bin_ts, spread))
        self._trim()

    def _trim(self):
        """Remove ticks older than window_s from the latest tick."""
        if not self._raw:
            return
        cutoff = self._raw[-1][0] - self.window_s
        # Binary-ish trim — find first tick >= cutoff
        while self._raw and self._raw[0][0] < cutoff:
            self._raw.pop(0)

    def get_filled_series(self) -> tuple[np.ndarray, np.ndarray, int]:
        """
        Forward-fill raw ticks to uniform 1-second intervals.

        Returns:
            timestamps: array of epoch seconds (uniform 1s spacing)
            spreads: array of spread values (forward-filled)
            n_raw: count of raw ticks within the window
        """
        if len(self._raw) < 2:
            return np.array([]), np.array([]), len(self._raw)

        t_start = self._raw[0][0]
        t_end = self._raw[-1][0]
        n_seconds = t_end - t_start + 1

        timestamps = np.arange(t_start, t_end + 1, dtype=np.int64)
        spreads = np.empty(n_seconds, dtype=np.float64)

        # Forward-fill: walk through raw ticks, fill gaps
        raw_idx = 0
        current_spread = self._raw[0][1]

        for i, t in enumerate(timestamps):
            # Advance raw_idx to the tick at or just before t
            while raw_idx < len(self._raw) - 1 and self._raw[raw_idx + 1][0] <= t:
                raw_idx += 1
            if self._raw[raw_idx][0] <= t:
                current_spread = self._raw[raw_idx][1]
            spreads[i] = current_spread

        return timestamps, spreads, len(self._raw)

    def compute_adf(self) -> ADFResult | None:
        """
        Run ADF test on the forward-filled spread series.

        Returns ADFResult or None if insufficient data.
        """
        timestamps, spreads, n_raw = self.get_filled_series()

        if len(spreads) < MIN_TICKS_FOR_ADF:
            return None

        try:
            # maxlag=None lets statsmodels choose optimal lag via AIC
            adf_stat, pvalue, _usedlag, _nobs, critical_values, _icbest = adfuller(
                spreads, maxlag=None, autolag="AIC"
            )
        except Exception as e:
            log.warning("ADF computation failed: %s", e)
            return None

        fill_pct = (1.0 - n_raw / len(spreads)) * 100.0 if len(spreads) > 0 else 0.0

        return ADFResult(
            statistic=round(adf_stat, 4),
            pvalue=round(pvalue, 6),
            is_stationary=pvalue < ADF_PVALUE_THRESHOLD,
            n_obs=len(spreads),
            n_raw=n_raw,
            fill_pct=round(fill_pct, 1),
            critical_values={k: round(v, 4) for k, v in critical_values.items()},
            spread_mean=round(float(np.mean(spreads)), 4),
            spread_std=round(float(np.std(spreads)), 4),
        )

    def compute_ou(self, window_s: int = 600) -> OUParams | None:
        """
        Calibrate OU parameters via AR(1) regression on the spread.

        Regression: S(t) = a + b * S(t-1) + epsilon
        Then:
            theta = -ln(b) / Δt          (speed of reversion)
            mu    = a / (1 - b)           (long-term mean)
            sigma = std(e) * sqrt(-2*ln(b) / (Δt * (1 - b²)))
            half_life = ln(2) / theta

        Args:
            window_s: lookback window in seconds (default 600 = 10 min).
                      Uses the most recent window_s of filled data.

        Returns OUParams or None if insufficient data or b is out of range.
        """
        timestamps, spreads, n_raw = self.get_filled_series()

        if len(spreads) < MIN_TICKS_FOR_OU:
            return None

        # Use only the most recent window_s observations
        if len(spreads) > window_s:
            spreads = spreads[-window_s:]

        # AR(1) regression: S(t) = a + b * S(t-1) + epsilon
        y = spreads[1:]      # S(t)
        x = spreads[:-1]     # S(t-1)

        # OLS: [a, b] = (X^T X)^{-1} X^T y  where X = [1, S(t-1)]
        n = len(y)
        X = np.column_stack([np.ones(n), x])
        try:
            coeffs, residuals, _, _ = np.linalg.lstsq(X, y, rcond=None)
        except np.linalg.LinAlgError as e:
            log.warning("AR(1) regression failed: %s", e)
            return None

        a, b = coeffs[0], coeffs[1]

        # b must be in (0, 1) for mean reversion
        if b <= 0 or b >= 1:
            log.debug("AR(1) b=%.4f out of mean-reversion range (0,1)", b)
            return None

        # Residuals: epsilon = S(t) - (a + b * S(t-1))
        epsilon = y - (a + b * x)
        residual_std = float(np.std(epsilon, ddof=1))

        # OU parameters
        ln_b = np.log(b)
        theta = -ln_b / DELTA_T
        mu = a / (1.0 - b)
        half_life = np.log(2) / theta

        # sigma = std(epsilon) * sqrt(-2*ln(b) / (Δt * (1 - b²)))
        sigma = residual_std * np.sqrt(-2.0 * ln_b / (DELTA_T * (1.0 - b**2)))

        return OUParams(
            theta=round(theta, 6),
            mu=round(mu, 4),
            sigma=round(sigma, 4),
            half_life_s=round(half_life, 2),
            b=round(b, 6),
            a=round(a, 6),
            residual_std=round(residual_std, 4),
            n_obs=n,
        )

    @property
    def n_raw(self) -> int:
        return len(self._raw)

    @property
    def window_span_s(self) -> int:
        """Actual time span covered by raw data in seconds."""
        if len(self._raw) < 2:
            return 0
        return self._raw[-1][0] - self._raw[0][0]
