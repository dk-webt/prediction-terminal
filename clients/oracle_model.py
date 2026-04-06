"""
Quantitative models for BTC 15-min binary spread arbitrage.

Model A: Individual leg pricing (Modified Black-Scholes)
Model B: Oracle divergence (Ornstein-Uhlenbeck process)

Usage:
    from clients.oracle_model import SpreadAnalyzer, model_a_probability

    # Model A
    p = model_a_probability(S=67500, K=67400, tau=0.6, sigma_15m=0.00253)

    # Model B
    analyzer = SpreadAnalyzer(window_s=1200)
    analyzer.add_tick(bin_ts=1700000000, spread=1.23)
    adf = analyzer.compute_adf()
    ou = analyzer.compute_ou()
"""

import logging
import math
import numpy as np
from dataclasses import dataclass
from statsmodels.tsa.stattools import adfuller

log = logging.getLogger(__name__)

_SQRT2 = math.sqrt(2)


def _norm_cdf(x: float) -> float:
    """Standard normal CDF using math.erf (no scipy needed)."""
    return 0.5 * (1.0 + math.erf(x / _SQRT2))


def _norm_ppf(p: float) -> float:
    """Standard normal inverse CDF (quantile function). Rational approximation."""
    # Beasley-Springer-Moro algorithm
    if p <= 0:
        return -10.0
    if p >= 1:
        return 10.0
    if p == 0.5:
        return 0.0
    if p < 0.5:
        return -_norm_ppf(1.0 - p)

    # Rational approximation for 0.5 < p < 1
    t = math.sqrt(-2.0 * math.log(1.0 - p))
    # Abramowitz & Stegun 26.2.23 coefficients
    c0, c1, c2 = 2.515517, 0.802853, 0.010328
    d1, d2, d3 = 1.432788, 0.189269, 0.001308
    return t - (c0 + c1 * t + c2 * t * t) / (1.0 + d1 * t + d2 * t * t + d3 * t * t * t)


# ── Model A: Individual Leg Pricing (Modified Black-Scholes) ────────────────


@dataclass
class ModelAResult:
    """Probability of BTC finishing above/below the strike for one platform."""
    p_above: float       # P(BTC > K at expiry) — probability contract wins YES
    p_below: float       # 1 - p_above — probability contract wins NO
    d2: float            # the d2 statistic from Black-Scholes
    S: float             # oracle price used
    K: float             # strike price
    tau: float           # time remaining (fraction of 15-min window, 0-1)
    sigma_15m: float     # 15-min implied volatility (decimal)


def model_a_probability(
    S: float,
    K: float,
    tau: float,
    sigma_15m: float,
) -> ModelAResult | None:
    """
    Compute the probability of BTC finishing above the strike price
    using the d2 component of Black-Scholes (risk-free rate = 0).

    d2 = [ln(S/K) - (σ² * τ) / 2] / (σ * √τ)
    P(above) = N(d2)

    Args:
        S: current live oracle price (platform-specific: BRTI for KS, Chainlink for PM)
        K: contract strike price
        tau: time remaining as fraction of 15-min window (1.0 = full window, 0.0 = expiry)
        sigma_15m: 15-minute implied volatility (decimal, e.g. 0.00253)

    Returns ModelAResult or None if inputs are invalid.
    """
    if S <= 0 or K <= 0 or sigma_15m <= 0:
        return None
    if tau <= 0:
        # At expiry — deterministic
        return ModelAResult(
            p_above=1.0 if S > K else 0.0,
            p_below=0.0 if S > K else 1.0,
            d2=float('inf') if S > K else float('-inf'),
            S=S, K=K, tau=0.0, sigma_15m=sigma_15m,
        )

    sqrt_tau = math.sqrt(tau)
    sigma_sqrt_tau = sigma_15m * sqrt_tau

    d2 = (math.log(S / K) - (sigma_15m ** 2 * tau) / 2) / sigma_sqrt_tau
    p_above = _norm_cdf(d2)

    return ModelAResult(
        p_above=round(p_above, 6),
        p_below=round(1.0 - p_above, 6),
        d2=round(d2, 4),
        S=S, K=K, tau=round(tau, 4), sigma_15m=sigma_15m,
    )


def model_a_both_platforms(
    brti_price: float,
    chainlink_price: float,
    ks_strike: float,
    pm_strike: float,
    tau: float,
    sigma_15m: float,
) -> dict:
    """
    Compute Model A probabilities for both platforms.

    Returns dict with:
        kalshi: ModelAResult (using BRTI as oracle)
        polymarket: ModelAResult (using Chainlink as oracle)
    """
    return {
        "kalshi": model_a_probability(S=brti_price, K=ks_strike, tau=tau, sigma_15m=sigma_15m),
        "polymarket": model_a_probability(S=chainlink_price, K=pm_strike, tau=tau, sigma_15m=sigma_15m),
    }


# ── Model C: Joint Probability (Student's t-Copula) ────────────────────────


def _bvn_cdf(x: float, y: float, rho: float) -> float:
    """
    Bivariate standard normal CDF: P(X ≤ x, Y ≤ y) with correlation ρ.
    Uses Drezner & Wesolowsky (1990) with Gauss-Legendre quadrature.
    """
    if abs(rho) < 1e-12:
        return _norm_cdf(x) * _norm_cdf(y)
    if rho > 0.9999:
        return _norm_cdf(min(x, y))
    if rho < -0.9999:
        return max(0.0, _norm_cdf(x) + _norm_cdf(y) - 1.0)

    # Gauss-Legendre 20-point quadrature for accuracy
    # Abscissae and weights for [0, 1]
    GL_X = [
        0.0765265211334973, 0.2277858511416451, 0.3737060887154195,
        0.5108670019508271, 0.6360536807265150, 0.7463319064601508,
        0.8391169718222188, 0.9122344282513259, 0.9639719272779138,
        0.9931285991850949,
    ]
    GL_W = [
        0.1527533871307258, 0.1491729864726037, 0.1420961093183820,
        0.1316886384491766, 0.1181945319615184, 0.1019301198172404,
        0.0832767415767048, 0.0626720483341091, 0.0406014298003869,
        0.0176140071391521,
    ]

    # Compute using the identity:
    # Φ₂(x, y, ρ) = Φ(x)Φ(y) + ∫₀^ρ φ₂(x, y, t) dt
    # where φ₂ is the bivariate normal density differentiated wrt ρ
    # This integral is computed via Gauss-Legendre over [0, ρ]
    total = 0.0
    half_rho = rho / 2.0

    for i in range(10):
        for sign in (-1, 1):
            t = half_rho * (sign * GL_X[i]) + half_rho
            omt2 = 1.0 - t * t
            if omt2 <= 0:
                continue
            s = math.sqrt(omt2)
            # Integrand: (1/(2π√(1-t²))) * exp(-(x²-2txy+y²)/(2(1-t²)))
            arg = (x * x - 2 * t * x * y + y * y) / (2 * omt2)
            if arg < 50:  # avoid underflow
                total += GL_W[i] * math.exp(-arg) / s

    total *= half_rho / (2.0 * math.pi)
    result = _norm_cdf(x) * _norm_cdf(y) + total
    return max(0.0, min(1.0, result))


def _t_ppf(p: float, nu: float) -> float:
    """
    Student's t inverse CDF. Cornish-Fisher expansion for moderate nu,
    normal approximation for large nu.
    """
    if nu <= 0:
        return 0.0
    if p <= 0:
        return -1e10
    if p >= 1:
        return 1e10
    if p == 0.5:
        return 0.0

    z = _norm_ppf(p)

    if nu >= 100:
        return z

    # Cornish-Fisher expansion (Hill 1970)
    z2 = z * z
    g1 = (z2 + 1) / (4 * nu)
    g2 = ((5 * z2 + 16) * z2 + 3) / (96 * nu * nu)
    g3 = (((3 * z2 + 19) * z2 + 17) * z2 - 15) / (384 * nu ** 3)
    g4 = ((((79 * z2 + 776) * z2 + 1482) * z2 - 1920) * z2 - 945) / (92160 * nu ** 4)

    return z * (1 + g1 + g2 + g3 + g4)


@dataclass
class ModelCResult:
    """Joint outcome probabilities for a two-leg spread trade."""
    p_ww: float     # both legs win (max profit — price between strikes)
    p_wl: float     # KS leg wins, PM leg loses
    p_lw: float     # KS leg loses, PM leg wins
    p_ll: float     # both legs lose (trap zone — max loss)
    rho: float      # correlation used
    nu: float       # degrees of freedom used
    strategy: str   # "A" (KS YES + PM NO) or "B" (KS NO + PM YES)


def model_c_joint(
    p_ks_above: float,
    p_pm_above: float,
    rho: float,
    nu: float = 5.0,
    strategy: str = "A",
) -> ModelCResult:
    """
    Compute joint outcome probabilities using a Student's t-Copula.

    The copula transforms the marginal probabilities (from Model A) through
    the inverse t-CDF, computes the bivariate t joint probability, then
    maps back to the four outcome cells.

    Args:
        p_ks_above: P(BTC > KS strike) from Model A (Kalshi)
        p_pm_above: P(BTC > PM strike) from Model A (Polymarket)
        rho: correlation between BRTI and Chainlink returns
        nu: degrees of freedom for t-copula (lower = fatter tails, default 5)
        strategy: "A" (KS YES + PM NO) or "B" (KS NO + PM YES)

    Returns ModelCResult with the four outcome probabilities.
    """
    # Clamp inputs
    p_ks_above = max(1e-8, min(1 - 1e-8, p_ks_above))
    p_pm_above = max(1e-8, min(1 - 1e-8, p_pm_above))
    rho = max(-0.9999, min(0.9999, rho))

    # Transform marginals through inverse t-CDF
    q_ks = _t_ppf(p_ks_above, nu)
    q_pm = _t_ppf(p_pm_above, nu)

    # Bivariate t-CDF: P(T_ks ≤ q_ks, T_pm ≤ q_pm)
    # Approximated as bivariate normal CDF with adjusted correlation
    # For moderate nu (≥4), this is very accurate
    # The exact adjustment: scale by sqrt(nu/(nu-2)) cancels in the copula
    p_both_above = _bvn_cdf(q_ks, q_pm, rho)

    # Now compute the four cells using inclusion-exclusion
    # P(KS above AND PM above) = copula joint
    p_ks_below = 1.0 - p_ks_above
    p_pm_below = 1.0 - p_pm_above

    # P(KS above AND PM below) = P(KS above) - P(KS above AND PM above)
    p_ks_above_pm_below = max(0.0, p_ks_above - p_both_above)
    # P(KS below AND PM above) = P(PM above) - P(KS above AND PM above)
    p_ks_below_pm_above = max(0.0, p_pm_above - p_both_above)
    # P(KS below AND PM below) = 1 - all others
    p_both_below = max(0.0, 1.0 - p_both_above - p_ks_above_pm_below - p_ks_below_pm_above)

    # Map to strategy outcomes
    if strategy == "A":
        # Strategy A: Buy KS YES + Buy PM NO
        # KS wins when BRTI > KS_strike, PM wins when Chainlink < PM_strike
        p_ww = p_ks_above_pm_below   # KS above, PM below
        p_wl = p_both_above           # KS above, PM above (PM NO loses)
        p_lw = p_both_below           # KS below, PM below (KS YES loses)
        p_ll = p_ks_below_pm_above   # KS below, PM above (both lose)
    else:
        # Strategy B: Buy KS NO + Buy PM YES
        # KS wins when BRTI < KS_strike, PM wins when Chainlink > PM_strike
        p_ww = p_ks_below_pm_above   # KS below, PM above
        p_wl = p_both_below           # KS below, PM below (PM YES loses)
        p_lw = p_both_above           # KS above, PM above (KS NO loses)
        p_ll = p_ks_above_pm_below   # KS above, PM below (both lose)

    return ModelCResult(
        p_ww=round(p_ww, 6),
        p_wl=round(p_wl, 6),
        p_lw=round(p_lw, 6),
        p_ll=round(p_ll, 6),
        rho=round(rho, 4),
        nu=nu,
        strategy=strategy,
    )


def compute_rolling_correlation(
    brti_prices: np.ndarray,
    chainlink_prices: np.ndarray,
) -> float | None:
    """
    Compute rolling Pearson correlation of log-returns between BRTI and Chainlink.

    Args:
        brti_prices: array of BRTI prices (aligned, forward-filled)
        chainlink_prices: array of Chainlink prices (aligned, forward-filled)

    Returns correlation coefficient or None if insufficient data.
    """
    if len(brti_prices) < 20 or len(chainlink_prices) < 20:
        return None
    if len(brti_prices) != len(chainlink_prices):
        return None

    # Log returns
    brti_ret = np.diff(np.log(brti_prices))
    cl_ret = np.diff(np.log(chainlink_prices))

    # Remove any NaN/Inf
    valid = np.isfinite(brti_ret) & np.isfinite(cl_ret)
    brti_ret = brti_ret[valid]
    cl_ret = cl_ret[valid]

    if len(brti_ret) < 10:
        return None

    corr_matrix = np.corrcoef(brti_ret, cl_ret)
    rho = float(corr_matrix[0, 1])

    if not np.isfinite(rho):
        return None

    return rho


@dataclass
class CopulaCalibration:
    """Calibrated t-Copula parameters from Two-Step Inversion."""
    rho: float            # copula correlation (from Greiner's relation)
    nu: float             # degrees of freedom (from 1D MLE, capped at 30)
    kendall_tau: float    # Kendall's tau (rank correlation on sub-sampled returns)
    n_obs: int            # number of sub-sampled return pairs used
    log_likelihood: float # log-likelihood at optimal nu


def _kendall_tau(x: np.ndarray, y: np.ndarray) -> float:
    """
    Compute Kendall's tau (rank correlation) between two arrays.
    O(n²) simple implementation — sufficient for n ≤ 1200.
    """
    n = len(x)
    concordant = 0
    discordant = 0
    for i in range(n - 1):
        dx = x[i + 1:] - x[i]
        dy = y[i + 1:] - y[i]
        prod = dx * dy
        concordant += int(np.sum(prod > 0))
        discordant += int(np.sum(prod < 0))
    total = concordant + discordant
    if total == 0:
        return 0.0
    return (concordant - discordant) / total


def _t_copula_log_likelihood(
    u: np.ndarray,
    v: np.ndarray,
    rho: float,
    nu: float,
) -> float:
    """
    Log-likelihood of the bivariate t-copula for given (u, v, ρ, ν).

    u, v are uniform marginals (CDF-transformed observations).
    Uses the copula density:
      c(u,v) = f₂(t⁻¹(u), t⁻¹(v); ρ, ν) / (f₁(t⁻¹(u); ν) · f₁(t⁻¹(v); ν))
    where f₂ is bivariate t density and f₁ is univariate t density.
    """
    if nu <= 2 or abs(rho) >= 1:
        return -1e10

    # Transform to t-quantiles
    x = np.array([_t_ppf(float(ui), nu) for ui in u])
    y = np.array([_t_ppf(float(vi), nu) for vi in v])

    # Bivariate t-copula log-density (analytical form)
    # log c(u,v) = log Γ((ν+2)/2) + log Γ(ν/2) - 2·log Γ((ν+1)/2)
    #            - 0.5·log(1-ρ²)
    #            - ((ν+2)/2)·log(1 + (x²-2ρxy+y²)/(ν(1-ρ²)))
    #            + ((ν+1)/2)·log(1 + x²/ν)
    #            + ((ν+1)/2)·log(1 + y²/ν)

    r2 = rho * rho
    omr2 = 1.0 - r2

    # Constant terms (don't depend on x, y)
    lg1 = math.lgamma((nu + 2) / 2)
    lg2 = math.lgamma(nu / 2)
    lg3 = math.lgamma((nu + 1) / 2)
    const = lg1 + lg2 - 2 * lg3 - 0.5 * math.log(omr2)

    # Per-observation terms
    quad = (x * x - 2 * rho * x * y + y * y) / (nu * omr2)
    term1 = -((nu + 2) / 2) * np.log(1 + quad)
    term2 = ((nu + 1) / 2) * np.log(1 + x * x / nu)
    term3 = ((nu + 1) / 2) * np.log(1 + y * y / nu)

    ll = float(np.sum(const + term1 + term2 + term3))
    return ll if np.isfinite(ll) else -1e10


COPULA_SUBSAMPLE_S = 30    # downsample interval for copula calibration (seconds)
COPULA_MIN_RETURNS = 10    # minimum sub-sampled returns needed
COPULA_NU_CAP = 30         # cap ν to stay conservative on tails
COPULA_NU_DEFAULT = 4      # fallback ν if MLE fails to converge


def calibrate_copula(
    brti_prices: np.ndarray,
    chainlink_prices: np.ndarray,
    subsample_s: int = COPULA_SUBSAMPLE_S,
) -> CopulaCalibration | None:
    """
    Two-Step Inversion calibration for the t-Copula using sub-sampled returns.

    Instead of 1-second returns (microstructure noise) or price levels
    (collinearity), we downsample to 30s/60s returns that match the
    contract horizon and reveal true directional dependence.

    Step 1: Downsample 1s aligned prices to subsample_s intervals.
    Step 2: Compute log returns on the downsampled series.
    Step 3: Compute Kendall's tau on sub-sampled returns.
    Step 4: Invert via Greiner's relation: ρ = sin(π/2 · τ).
    Step 5: 1D MLE for ν on the sub-sampled pseudo-observations.
    Step 6: Safety cap ν at 30; default to 4 if MLE fails.

    Args:
        brti_prices: aligned BRTI price array (1s intervals)
        chainlink_prices: aligned Chainlink price array (1s intervals)
        subsample_s: downsample interval in seconds (default 30)

    Returns CopulaCalibration or None if insufficient data.
    """
    n_raw = len(brti_prices)
    if n_raw < subsample_s * 2 or len(brti_prices) != len(chainlink_prices):
        return None

    # Downsample: take every subsample_s-th price
    brti_ds = brti_prices[::subsample_s]
    cl_ds = chainlink_prices[::subsample_s]

    if len(brti_ds) < 3:
        return None

    # Log returns on downsampled series
    brti_ret = np.diff(np.log(brti_ds))
    cl_ret = np.diff(np.log(cl_ds))

    valid = np.isfinite(brti_ret) & np.isfinite(cl_ret)
    brti_ret = brti_ret[valid]
    cl_ret = cl_ret[valid]

    if len(brti_ret) < COPULA_MIN_RETURNS:
        return None

    # Step 3: Kendall's tau on sub-sampled returns
    tau = _kendall_tau(brti_ret, cl_ret)

    # Step 4: Greiner's relation
    rho = math.sin(math.pi / 2.0 * tau)
    rho = max(-0.9999, min(0.9999, rho))

    # Transform returns to uniform marginals via empirical CDF (pseudo-observations)
    n = len(brti_ret)
    u = (np.argsort(np.argsort(brti_ret)).astype(float) + 1) / (n + 1)
    v = (np.argsort(np.argsort(cl_ret)).astype(float) + 1) / (n + 1)

    # Step 5: 1D grid search for nu
    best_nu = float(COPULA_NU_DEFAULT)
    best_ll = -1e10

    for nu_candidate in [2.5, 3, 4, 5, 7, 10, 15, 20, 30]:
        ll = _t_copula_log_likelihood(u, v, rho, nu_candidate)
        if ll > best_ll:
            best_ll = ll
            best_nu = nu_candidate

    # Refine around best with finer grid
    lo = max(2.1, best_nu - 2)
    hi = min(COPULA_NU_CAP, best_nu + 2)
    for nu_candidate in np.linspace(lo, hi, 20):
        ll = _t_copula_log_likelihood(u, v, rho, float(nu_candidate))
        if ll > best_ll:
            best_ll = ll
            best_nu = float(nu_candidate)

    # Step 6: Safety cap
    if best_nu > COPULA_NU_CAP:
        best_nu = float(COPULA_NU_CAP)
    if best_ll <= -1e9:
        # MLE failed to converge — use conservative default
        best_nu = float(COPULA_NU_DEFAULT)

    return CopulaCalibration(
        rho=round(rho, 6),
        nu=round(best_nu, 1),
        kendall_tau=round(tau, 6),
        n_obs=n,
        log_likelihood=round(best_ll, 2),
    )


# ── Model B: Oracle Divergence (Ornstein-Uhlenbeck) ────────────────────────

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


# ── Model Orchestrator ─────────────────────────────────────────────────────


@dataclass
class ModelState:
    """Unified output of all models at a point in time."""
    # Model A — per-platform probabilities
    model_a_ks: ModelAResult | None
    model_a_pm: ModelAResult | None
    # Model B — spread analysis
    adf: ADFResult | None
    ou: OUParams | None
    # Model C — joint probabilities
    model_c_a: ModelCResult | None   # Strategy A (KS YES + PM NO)
    model_c_b: ModelCResult | None   # Strategy B (KS NO + PM YES)
    copula: CopulaCalibration | None
    # Inputs used
    sigma_15m: float | None
    tau: float | None                # time remaining (fraction of 15-min window)
    brti_price: float | None
    chainlink_price: float | None
    ks_strike: float | None
    pm_strike: float | None
    n_aligned_ticks: int


class ModelOrchestrator:
    """
    Owns all model state and computes Models A/B/C on demand.

    Feed it aligned ticks and strikes; call compute() to get a ModelState.
    Designed to be owned by BtcStreamManager and queried by ATE.

    Usage:
        orch = ModelOrchestrator()
        orch.on_aligned_tick(bin_ts, brti_price, chainlink_price, spread)
        orch.set_strikes(ks_strike, pm_strike, window_end_ts)
        orch.set_sigma(sigma_15m)
        state = orch.compute()
    """

    def __init__(self, adf_window_s: int = 1200, ou_window_s: int = 600):
        self._analyzer = SpreadAnalyzer(window_s=adf_window_s)
        self._ou_window_s = ou_window_s
        self._brti_prices: list[float] = []
        self._cl_prices: list[float] = []
        self._max_prices = adf_window_s  # match ADF window

        # External inputs (set by BtcStreamManager)
        self._sigma_15m: float | None = None
        self._ks_strike: float = 0.0
        self._pm_strike: float = 0.0
        self._window_end_ts: float = 0.0

        # Cached model outputs (recomputed on compute())
        self._last_state: ModelState | None = None
        self._last_compute_ts: float = 0.0

    def on_aligned_tick(self, bin_ts: int, brti_price: float,
                        chainlink_price: float, spread: float):
        """Feed a new aligned tick from OracleAlignmentBuffer."""
        self._analyzer.add_tick(bin_ts, spread)
        self._brti_prices.append(brti_price)
        self._cl_prices.append(chainlink_price)
        if len(self._brti_prices) > self._max_prices:
            self._brti_prices.pop(0)
            self._cl_prices.pop(0)

    def set_strikes(self, ks_strike: float, pm_strike: float,
                    window_end_ts: float):
        """Update strike prices and window end time (called on roll)."""
        self._ks_strike = ks_strike
        self._pm_strike = pm_strike
        self._window_end_ts = window_end_ts

    def set_sigma(self, sigma_15m: float | None):
        """Update the 15-min implied volatility from Deribit."""
        self._sigma_15m = sigma_15m

    def compute(self, now: float | None = None) -> ModelState:
        """
        Run all models and return unified state.

        Args:
            now: current epoch time (defaults to time.time())
        """
        import time as _time
        if now is None:
            now = _time.time()

        # Latest aligned prices
        brti = self._brti_prices[-1] if self._brti_prices else None
        cl = self._cl_prices[-1] if self._cl_prices else None

        # Tau (fraction of 15-min window remaining)
        tau = None
        if self._window_end_ts > 0:
            remaining = max(0, self._window_end_ts - now)
            tau = min(1.0, remaining / (15 * 60))

        # Model A
        model_a_ks = None
        model_a_pm = None
        if brti and cl and self._sigma_15m and tau is not None and tau > 0:
            if self._ks_strike > 0:
                model_a_ks = model_a_probability(
                    S=brti, K=self._ks_strike, tau=tau,
                    sigma_15m=self._sigma_15m,
                )
            if self._pm_strike > 0:
                model_a_pm = model_a_probability(
                    S=cl, K=self._pm_strike, tau=tau,
                    sigma_15m=self._sigma_15m,
                )

        # Model B
        adf = self._analyzer.compute_adf()
        ou = self._analyzer.compute_ou(window_s=self._ou_window_s)

        # Model C
        model_c_a = None
        model_c_b = None
        copula = None
        if model_a_ks and model_a_pm and len(self._brti_prices) >= 30:
            copula = calibrate_copula(
                np.array(self._brti_prices),
                np.array(self._cl_prices),
            )
            if copula:
                model_c_a = model_c_joint(
                    model_a_ks.p_above, model_a_pm.p_above,
                    copula.rho, copula.nu, strategy="A",
                )
                model_c_b = model_c_joint(
                    model_a_ks.p_above, model_a_pm.p_above,
                    copula.rho, copula.nu, strategy="B",
                )

        state = ModelState(
            model_a_ks=model_a_ks,
            model_a_pm=model_a_pm,
            adf=adf,
            ou=ou,
            model_c_a=model_c_a,
            model_c_b=model_c_b,
            copula=copula,
            sigma_15m=self._sigma_15m,
            tau=tau,
            brti_price=brti,
            chainlink_price=cl,
            ks_strike=self._ks_strike if self._ks_strike > 0 else None,
            pm_strike=self._pm_strike if self._pm_strike > 0 else None,
            n_aligned_ticks=len(self._brti_prices),
        )
        self._last_state = state
        self._last_compute_ts = now
        return state

    @property
    def last_state(self) -> ModelState | None:
        """Most recently computed state (None if compute() never called)."""
        return self._last_state

    @property
    def analyzer(self) -> SpreadAnalyzer:
        """Direct access to the SpreadAnalyzer for ADF/OU window info."""
        return self._analyzer
