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
            d2=99.0 if S > K else -99.0,  # capped to avoid JSON infinity
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


# ── Model D: Execution Logic (Friction-Adjusted EV) ────────────────────────

MODEL_D_MIN_ALPHA = 0.02    # minimum EV threshold ($) to trigger trade
MODEL_D_MIN_TIME_S = 59     # reject if < 59s to expiry
KALSHI_FEE_RATE = 0.07      # Kalshi fee: 0.07 * C * P * (1-P), C=contracts


@dataclass
class ModelDResult:
    """Friction-adjusted expected value and trade recommendation."""
    # Per-strategy EV
    ev_a_raw: float          # EV Strategy A without fees (cost only)
    ev_b_raw: float          # EV Strategy B without fees (cost only)
    ev_a: float              # EV for Strategy A (friction-adjusted)
    ev_b: float              # EV for Strategy B (friction-adjusted)
    # Cost breakdown per strategy
    cost_a: float            # total premium Strategy A (C_k + C_p)
    cost_b: float            # total premium Strategy B
    fee_ks_a: float          # Kalshi fee for Strategy A: 0.07*P*(1-P)
    fee_ks_b: float          # Kalshi fee for Strategy B: 0.07*P*(1-P)
    fee_pm_a: float          # PM fee for Strategy A
    fee_pm_b: float          # PM fee for Strategy B
    # Decision
    chosen: str | None       # "A", "B", or None (no trade)
    ev: float                # EV of best strategy
    # Gate checks
    gates: dict              # {name: (passed: bool, reason: str)}
    all_gates_passed: bool   # True only if ALL gates pass


@dataclass
class StrategyPrices:
    """Ask prices for a specific strategy."""
    ks_ask: float     # Kalshi leg ask price
    pm_ask: float     # Polymarket leg ask price
    ks_side: str      # "yes" or "no"
    pm_side: str      # "up" or "down"


def model_d_ev(
    model_c: ModelCResult,
    cost: float,
    fee_ks: float,
    fee_pm: float,
) -> float:
    """
    Compute friction-adjusted expected value for one strategy.

    EV = P(WW) × (2 - Cost - F_k - F_p)    # both legs win → $2 payout
       + P(WL) × (1 - Cost - F_k - F_p)    # one leg wins → $1 payout
       + P(LW) × (1 - Cost - F_k - F_p)    # other leg wins → $1 payout
       - P(LL) × (Cost + F_k + F_p)         # both lose → lose premium + fees
    """
    total_friction = cost + fee_ks + fee_pm

    ev = (model_c.p_ww * (2.0 - total_friction)
          + model_c.p_wl * (1.0 - total_friction)
          + model_c.p_lw * (1.0 - total_friction)
          - model_c.p_ll * total_friction)

    return ev


def model_d_execute(
    model_c_a: ModelCResult | None,
    model_c_b: ModelCResult | None,
    prices_a: StrategyPrices,
    prices_b: StrategyPrices,
    fee_pm_bps: float,
    adf: 'ADFResult | None',
    ou: 'OUParams | None',
    tau: float | None,
    min_alpha: float = MODEL_D_MIN_ALPHA,
    oracle_stale: bool = False,
    prices_stale: bool = False,
    sigma_stale: bool = False,
) -> ModelDResult:
    """
    Full Model D execution decision with gate checks.

    Args:
        model_c_a: Model C output for Strategy A
        model_c_b: Model C output for Strategy B
        prices_a: ask prices for Strategy A (KS YES + PM NO/DOWN)
        prices_b: ask prices for Strategy B (KS NO + PM UP)
        fee_pm_bps: Polymarket fee rate in basis points (e.g. 200 = 2%)
        adf: ADF test result from Model B
        ou: OU parameters from Model B
        tau: time remaining as fraction of 15-min window (0-1)
        min_alpha: minimum EV threshold to trigger (default $0.02)
        oracle_stale: True if aligned oracle ticks are stale
        prices_stale: True if ask prices are stale
        sigma_stale: True if Deribit IV is stale
    """
    gates = {}

    # Gate 0: Data freshness
    if oracle_stale:
        gates["oracle_fresh"] = (False, "oracle data stale (>10s)")
    else:
        gates["oracle_fresh"] = (True, "ok")
    if prices_stale:
        gates["prices_fresh"] = (False, "ask prices stale (>15s)")
    else:
        gates["prices_fresh"] = (True, "ok")
    if sigma_stale:
        gates["sigma_fresh"] = (False, "Deribit IV stale (>10min)")
    else:
        gates["sigma_fresh"] = (True, "ok")

    # Gate 1: ADF stationarity
    if adf is None:
        gates["adf"] = (False, "no ADF result")
    elif not adf.is_stationary:
        gates["adf"] = (False, f"p={adf.pvalue:.4f} > 0.05")
    else:
        gates["adf"] = (True, f"p={adf.pvalue:.4f}")

    # Gate 2: Half-life < time remaining
    if ou is None or tau is None:
        gates["half_life"] = (False, "no OU params or tau")
    else:
        time_remaining_s = tau * 15 * 60
        if ou.half_life_s >= time_remaining_s and time_remaining_s > 0:
            gates["half_life"] = (False, f"{ou.half_life_s:.1f}s >= {time_remaining_s:.0f}s remaining")
        else:
            gates["half_life"] = (True, f"{ou.half_life_s:.1f}s < {time_remaining_s:.0f}s remaining")

    # Gate 3: Time remaining > 59s
    if tau is None:
        gates["time"] = (False, "no tau")
    else:
        time_remaining_s = tau * 15 * 60
        if time_remaining_s < MODEL_D_MIN_TIME_S:
            gates["time"] = (False, f"{time_remaining_s:.0f}s < {MODEL_D_MIN_TIME_S}s")
        else:
            gates["time"] = (True, f"{time_remaining_s:.0f}s remaining")

    # Gate 4: Model C available
    if model_c_a is None or model_c_b is None:
        gates["model_c"] = (False, "no copula output")
    else:
        gates["model_c"] = (True, "ok")

    # Compute fees
    # Kalshi: Fee = 0.07 * C * P * (1-P), C=1 contract, P=ask price
    fee_ks_a = KALSHI_FEE_RATE * prices_a.ks_ask * (1.0 - prices_a.ks_ask)
    fee_ks_b = KALSHI_FEE_RATE * prices_b.ks_ask * (1.0 - prices_b.ks_ask)
    # PM: fee = ask * (fee_bps / 10000)
    fee_pm_rate = fee_pm_bps / 10000.0 if fee_pm_bps > 0 else 0.02
    fee_pm_a = prices_a.pm_ask * fee_pm_rate
    fee_pm_b = prices_b.pm_ask * fee_pm_rate

    # Compute EV for both strategies
    cost_a = prices_a.ks_ask + prices_a.pm_ask
    cost_b = prices_b.ks_ask + prices_b.pm_ask

    ev_a_raw = -999.0
    ev_b_raw = -999.0
    ev_a = -999.0
    ev_b = -999.0
    if model_c_a and cost_a > 0:
        ev_a_raw = model_d_ev(model_c_a, cost_a, 0.0, 0.0)
        ev_a = model_d_ev(model_c_a, cost_a, fee_ks_a, fee_pm_a)
    if model_c_b and cost_b > 0:
        ev_b_raw = model_d_ev(model_c_b, cost_b, 0.0, 0.0)
        ev_b = model_d_ev(model_c_b, cost_b, fee_ks_b, fee_pm_b)

    # Gate 5: EV threshold
    best_ev = max(ev_a, ev_b)
    if best_ev < min_alpha:
        gates["ev"] = (False, f"${best_ev:.4f} < ${min_alpha:.2f}")
    else:
        gates["ev"] = (True, f"${best_ev:.4f} >= ${min_alpha:.2f}")

    all_passed = all(passed for passed, _ in gates.values())

    # Choose strategy
    chosen = None
    if all_passed:
        chosen = "A" if ev_a >= ev_b else "B"

    return ModelDResult(
        ev_a_raw=round(ev_a_raw, 6),
        ev_b_raw=round(ev_b_raw, 6),
        ev_a=round(ev_a, 6),
        ev_b=round(ev_b, 6),
        cost_a=round(cost_a, 4),
        cost_b=round(cost_b, 4),
        fee_ks_a=round(fee_ks_a, 4),
        fee_ks_b=round(fee_ks_b, 4),
        fee_pm_a=round(fee_pm_a, 4),
        fee_pm_b=round(fee_pm_b, 4),
        chosen=chosen,
        ev=round(best_ev, 6),
        gates=gates,
        all_gates_passed=all_passed,
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
            is_stationary=bool(pvalue < ADF_PVALUE_THRESHOLD),
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
    # Model D — execution decision
    model_d: ModelDResult | None
    # Inputs used
    sigma_15m: float | None
    tau: float | None                # time remaining (fraction of 15-min window)
    brti_price: float | None
    chainlink_price: float | None
    ks_strike: float | None
    pm_strike: float | None
    n_aligned_ticks: int
    # Staleness
    oracle_stale: bool           # True if aligned ticks stopped flowing
    oracle_age_s: float | None   # seconds since last aligned tick
    prices_stale: bool           # True if ask prices are old
    prices_age_s: float | None   # seconds since prices were updated
    sigma_stale: bool            # True if Deribit IV is old
    sigma_age_s: float | None    # seconds since Deribit IV was fetched

    @staticmethod
    def _safe_float(v: float | None) -> float | None:
        """Clamp inf/nan to None for JSON safety."""
        if v is None:
            return None
        if not math.isfinite(v):
            return None
        return v

    def to_dict(self) -> dict:
        """Serialize to JSON-safe dict for frontend consumption."""
        sf = self._safe_float
        d: dict = {
            "n_aligned_ticks": self.n_aligned_ticks,
            "sigma_15m": self.sigma_15m,
            "tau": round(self.tau, 4) if self.tau is not None else None,
            "tau_min": round(self.tau * 15, 1) if self.tau is not None else None,
            "brti_price": self.brti_price,
            "chainlink_price": self.chainlink_price,
            "ks_strike": self.ks_strike,
            "pm_strike": self.pm_strike,
            "staleness": {
                "oracle_stale": self.oracle_stale,
                "oracle_age_s": round(self.oracle_age_s, 1) if self.oracle_age_s is not None else None,
                "prices_stale": self.prices_stale,
                "prices_age_s": round(self.prices_age_s, 1) if self.prices_age_s is not None else None,
                "sigma_stale": self.sigma_stale,
                "sigma_age_s": round(self.sigma_age_s, 1) if self.sigma_age_s is not None else None,
            },
        }
        # Model A
        if self.model_a_ks:
            d["model_a_ks"] = {"p_above": sf(self.model_a_ks.p_above), "p_below": sf(self.model_a_ks.p_below), "d2": sf(self.model_a_ks.d2)}
        if self.model_a_pm:
            d["model_a_pm"] = {"p_above": sf(self.model_a_pm.p_above), "p_below": sf(self.model_a_pm.p_below), "d2": sf(self.model_a_pm.d2)}
        # Model B
        if self.adf:
            d["adf"] = {"statistic": sf(self.adf.statistic), "pvalue": sf(self.adf.pvalue), "is_stationary": bool(self.adf.is_stationary), "n_obs": int(self.adf.n_obs)}
        if self.ou:
            d["ou"] = {"theta": sf(self.ou.theta), "mu": sf(self.ou.mu), "sigma": sf(self.ou.sigma), "half_life_s": sf(self.ou.half_life_s)}
        # Model C
        if self.copula:
            d["copula"] = {"rho": sf(self.copula.rho), "nu": sf(self.copula.nu), "kendall_tau": sf(self.copula.kendall_tau), "n_obs": int(self.copula.n_obs)}
        if self.model_c_a:
            d["model_c_a"] = {"p_ww": sf(self.model_c_a.p_ww), "p_wl": sf(self.model_c_a.p_wl), "p_lw": sf(self.model_c_a.p_lw), "p_ll": sf(self.model_c_a.p_ll)}
        if self.model_c_b:
            d["model_c_b"] = {"p_ww": sf(self.model_c_b.p_ww), "p_wl": sf(self.model_c_b.p_wl), "p_lw": sf(self.model_c_b.p_lw), "p_ll": sf(self.model_c_b.p_ll)}
        # Model D
        if self.model_d:
            md = self.model_d
            gates_dict = {k: {"passed": bool(v[0]), "reason": str(v[1])} for k, v in md.gates.items()}
            d["model_d"] = {
                "ev_a_raw": sf(md.ev_a_raw), "ev_b_raw": sf(md.ev_b_raw),
                "ev_a": sf(md.ev_a), "ev_b": sf(md.ev_b), "chosen": md.chosen, "ev": sf(md.ev),
                "cost_a": sf(md.cost_a), "cost_b": sf(md.cost_b),
                "fee_ks_a": sf(md.fee_ks_a), "fee_ks_b": sf(md.fee_ks_b),
                "fee_pm_a": sf(md.fee_pm_a), "fee_pm_b": sf(md.fee_pm_b),
                "gates": gates_dict, "all_gates_passed": bool(md.all_gates_passed),
            }
        return d


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

    # Staleness thresholds
    ORACLE_STALE_S = 10.0    # aligned ticks older than 10s = stale
    PRICES_STALE_S = 15.0    # ask prices older than 15s = stale
    SIGMA_STALE_S = 600.0    # Deribit IV older than 10 min = stale

    def __init__(self, adf_window_s: int = 1200, ou_window_s: int = 600):
        self._analyzer = SpreadAnalyzer(window_s=adf_window_s)
        self._ou_window_s = ou_window_s
        self._brti_prices: list[float] = []
        self._cl_prices: list[float] = []
        self._max_prices = adf_window_s  # match ADF window

        # External inputs (set by BtcStreamManager)
        self._sigma_15m: float | None = None
        self._sigma_updated: float = 0.0     # time.time() when sigma was last set
        self._ks_strike: float = 0.0
        self._pm_strike: float = 0.0
        self._window_end_ts: float = 0.0

        # Ask prices for Model D (updated by BtcStreamManager on each snapshot)
        self._ks_yes_ask: float = 0.0
        self._ks_no_ask: float = 0.0
        self._pm_up_ask: float = 0.0
        self._pm_down_ask: float = 0.0
        self._pm_fee_bps: float = 200.0  # default 2%, updated from token metadata
        self._prices_updated: float = 0.0    # time.time() when prices were last set

        # Aligned tick freshness
        self._last_tick_ts: float = 0.0      # time.time() when last tick arrived

        # Cached model outputs (recomputed on compute())
        self._last_state: ModelState | None = None
        self._last_compute_ts: float = 0.0

    def on_aligned_tick(self, bin_ts: int, brti_price: float,
                        chainlink_price: float, spread: float):
        """Feed a new aligned tick from OracleAlignmentBuffer."""
        import time as _time
        self._analyzer.add_tick(bin_ts, spread)
        self._brti_prices.append(brti_price)
        self._cl_prices.append(chainlink_price)
        if len(self._brti_prices) > self._max_prices:
            self._brti_prices.pop(0)
            self._cl_prices.pop(0)
        self._last_tick_ts = _time.time()

    def set_strikes(self, ks_strike: float, pm_strike: float,
                    window_end_ts: float):
        """Update strike prices and window end time (called on roll)."""
        self._ks_strike = ks_strike
        self._pm_strike = pm_strike
        self._window_end_ts = window_end_ts

    def set_sigma(self, sigma_15m: float | None):
        """Update the 15-min implied volatility from Deribit."""
        import time as _time
        self._sigma_15m = sigma_15m
        if sigma_15m is not None:
            self._sigma_updated = _time.time()

    def set_prices(self, ks_yes_ask: float, ks_no_ask: float,
                   pm_up_ask: float, pm_down_ask: float,
                   pm_fee_bps: float = 200.0):
        """Update live ask prices and PM fee rate for Model D."""
        import time as _time
        self._ks_yes_ask = ks_yes_ask
        self._ks_no_ask = ks_no_ask
        self._pm_up_ask = pm_up_ask
        self._pm_down_ask = pm_down_ask
        self._pm_fee_bps = pm_fee_bps
        self._prices_updated = _time.time()

    def compute(self, now: float | None = None) -> ModelState:
        """
        Run all models and return unified state.

        Args:
            now: current epoch time (defaults to time.time())
        """
        import time as _time
        if now is None:
            now = _time.time()

        # Staleness checks
        oracle_age = (now - self._last_tick_ts) if self._last_tick_ts > 0 else None
        oracle_stale = oracle_age is None or oracle_age > self.ORACLE_STALE_S
        prices_age = (now - self._prices_updated) if self._prices_updated > 0 else None
        prices_stale = prices_age is None or prices_age > self.PRICES_STALE_S
        sigma_age = (now - self._sigma_updated) if self._sigma_updated > 0 else None
        sigma_stale = sigma_age is None or sigma_age > self.SIGMA_STALE_S

        # Latest aligned prices
        brti = self._brti_prices[-1] if self._brti_prices else None
        cl = self._cl_prices[-1] if self._cl_prices else None

        # Tau (fraction of 15-min window remaining)
        tau = None
        if self._window_end_ts > 0:
            remaining = max(0, self._window_end_ts - now)
            tau = min(1.0, remaining / (15 * 60))

        # Model A — skip if oracle or sigma is stale
        model_a_ks = None
        model_a_pm = None
        if brti and cl and self._sigma_15m and tau is not None and tau > 0:
            if not oracle_stale and not sigma_stale:
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
        n_prices = len(self._brti_prices)
        if not model_a_ks or not model_a_pm:
            log.debug("Model C skipped: model_a_ks=%s model_a_pm=%s oracle_stale=%s sigma_stale=%s tau=%s pm_strike=%s",
                       model_a_ks is not None, model_a_pm is not None, oracle_stale, sigma_stale, tau, self._pm_strike)
        elif n_prices < 30:
            log.debug("Model C skipped: only %d prices (need 30)", n_prices)
        if model_a_ks and model_a_pm and n_prices >= 30:
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

        # Model D — execution decision (includes staleness gate)
        model_d_result = None
        if model_c_a and model_c_b:
            prices_a = StrategyPrices(
                ks_ask=self._ks_yes_ask, pm_ask=self._pm_down_ask,
                ks_side="yes", pm_side="down",
            )
            prices_b = StrategyPrices(
                ks_ask=self._ks_no_ask, pm_ask=self._pm_up_ask,
                ks_side="no", pm_side="up",
            )
            model_d_result = model_d_execute(
                model_c_a=model_c_a, model_c_b=model_c_b,
                prices_a=prices_a, prices_b=prices_b,
                fee_pm_bps=self._pm_fee_bps,
                adf=adf, ou=ou, tau=tau,
                oracle_stale=oracle_stale,
                prices_stale=prices_stale,
                sigma_stale=sigma_stale,
            )

        state = ModelState(
            model_a_ks=model_a_ks,
            model_a_pm=model_a_pm,
            adf=adf,
            ou=ou,
            model_c_a=model_c_a,
            model_c_b=model_c_b,
            copula=copula,
            model_d=model_d_result,
            sigma_15m=self._sigma_15m,
            tau=tau,
            brti_price=brti,
            chainlink_price=cl,
            ks_strike=self._ks_strike if self._ks_strike > 0 else None,
            pm_strike=self._pm_strike if self._pm_strike > 0 else None,
            n_aligned_ticks=len(self._brti_prices),
            oracle_stale=oracle_stale,
            oracle_age_s=oracle_age,
            prices_stale=prices_stale,
            prices_age_s=prices_age,
            sigma_stale=sigma_stale,
            sigma_age_s=sigma_age,
        )
        self._last_state = state
        self._last_compute_ts = now
        return state

    def compute_model_a_fast(self) -> dict | None:
        """
        Compute only Model A (cheap) for real-time updates between full computes.
        Returns a dict with model_a_ks and model_a_pm, or None if inputs missing.
        """
        import time as _time
        now = _time.time()

        brti = self._brti_prices[-1] if self._brti_prices else None
        cl = self._cl_prices[-1] if self._cl_prices else None
        if not brti or not cl or not self._sigma_15m:
            return None
        if self._window_end_ts <= 0:
            return None

        remaining = max(0, self._window_end_ts - now)
        tau = min(1.0, remaining / (15 * 60))
        if tau <= 0:
            return None

        # Staleness check
        oracle_age = (now - self._last_tick_ts) if self._last_tick_ts > 0 else None
        if oracle_age is None or oracle_age > self.ORACLE_STALE_S:
            return None
        sigma_age = (now - self._sigma_updated) if self._sigma_updated > 0 else None
        if sigma_age is None or sigma_age > self.SIGMA_STALE_S:
            return None

        sf = ModelState._safe_float
        result = {"tau": round(tau, 4), "tau_min": round(tau * 15, 1)}

        if self._ks_strike > 0:
            r = model_a_probability(S=brti, K=self._ks_strike, tau=tau, sigma_15m=self._sigma_15m)
            if r:
                result["model_a_ks"] = {"p_above": sf(r.p_above), "p_below": sf(r.p_below), "d2": sf(r.d2)}

        if self._pm_strike > 0:
            r = model_a_probability(S=cl, K=self._pm_strike, tau=tau, sigma_15m=self._sigma_15m)
            if r:
                result["model_a_pm"] = {"p_above": sf(r.p_above), "p_below": sf(r.p_below), "d2": sf(r.d2)}

        return result

    @property
    def last_state(self) -> ModelState | None:
        """Most recently computed state (None if compute() never called)."""
        return self._last_state

    @property
    def analyzer(self) -> SpreadAnalyzer:
        """Direct access to the SpreadAnalyzer for ADF/OU window info."""
        return self._analyzer
