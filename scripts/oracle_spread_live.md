# oracle_spread_live.py

Live aligned oracle spread monitor for BTC 15-min binary spread arbitrage.

Connects to BRTI (6-exchange CF Benchmarks replication) and Chainlink BTC/USD (Polymarket RTDS), aligns prices into 1-second bins, and continuously prints each aligned tick with spread, latency, and model outputs.

## Usage

```bash
python3 scripts/oracle_spread_live.py                 # full output (all models, every 10s)
python3 scripts/oracle_spread_live.py --adf 30        # model output every 30s
python3 scripts/oracle_spread_live.py --no-strikes    # disable strikes / Model A / Model C
```

## Flags

| Flag | Default | Description |
|------|---------|-------------|
| `--adf N` | 10 | Interval in seconds between ADF test + OU calibration + Model A/C output prints. |
| `--no-strikes` | off | Disable strike fetching, Model A probabilities, and Model C joint outcomes. |
| `--no-header` | off | Skip the column header on startup. |

## Output

### Per-tick line

Printed for every aligned tick (both BRTI and Chainlink reported in the same 1-second bin):

```
23:15:27  BRTI: $ 68,777.15  CL: $ 68,780.10  SPREAD: $   -2.95  Latency_BRTI:  100ms  Latency_Chainlink: 1236ms  #1
```

- **TIME**: UTC timestamp of the 1-second bin
- **BRTI**: BRTI estimate from 6-exchange order books (Kalshi settlement source)
- **CL**: Chainlink BTC/USD from Polymarket RTDS (PM settlement source)
- **SPREAD**: BRTI minus Chainlink
- **Latency_BRTI**: local receive time minus median exchange server timestamp (ms)
- **Latency_Chainlink**: local receive time minus Chainlink oracle measurement timestamp (ms)
- **#N**: tick counter

### Periodic model output

Printed every `--adf N` seconds (default 60s):

**ADF Test** (20-min rolling window) — tests if the spread is mean-reverting:
```
  ┌─ ADF TEST (84 obs, 79 raw, 6% filled, 83s window)
  │  Statistic:  -5.3719  p-value: 0.000004  → STATIONARY ✓
  │  Critical: 1%=-3.5127  5%=-2.8975  10%=-2.5859
```

**OU Parameters** (10-min rolling window) — calibrates the Ornstein-Uhlenbeck model:
```
  ├─ OU PARAMS (10-min window, 83 obs)
  │  θ (theta):    0.738523/s  (speed of reversion)
  │  μ (mu):      $+1.6609  (long-term equilibrium spread)
  │  σ (sigma):   $22.5333/√s  (spread volatility)
  │  Half-life:   0.9s  (0.0 min)
  │  AR(1): a=0.867307  b=0.477819  std(ε)=$16.2873
```

**Model A** — per-platform probability of BTC finishing above strike:
```
  ├─ MODEL A (σ_15m=0.002361, τ=0.876 = 13.1min left, 0DTE ATM (BTC-6APR26-69000-C))
  │  KS:  P(above $68,519) = 0.9538  P(below) = 0.0462  d2=+1.6828  (BRTI=$68,774.68)
  │  PM:  P(above $68,568) = 0.9213  P(below) = 0.0787  d2=+1.4139  (CL=$68,783.05)
```

**Model C** — joint outcome probabilities via calibrated Student's t-Copula:
```
  ├─ MODEL C (τ=+0.0267, ρ=+0.0419, ν=52.0, LL=0.0, 74 obs)
  │  Strat A (KS YES + PM NO):  WW=0.2338  WL=0.4579  LW=0.1136  LL=0.1947
  └─ Strat B (KS NO  + PM YES): WW=0.1947  WL=0.1136  LW=0.4579  LL=0.2338
```

- **τ**: Kendall's tau (rank correlation of oracle log-returns)
- **ρ**: copula correlation via Greiner's relation: ρ = sin(π/2 · τ)
- **ν**: degrees of freedom from 1D MLE (lower = fatter tails)
- **WW/WL/LW/LL**: probability of each outcome (should sum to 1.0)

## Data sources

| Feed | Protocol | Auth | Update rate |
|------|----------|------|-------------|
| BRTI (Coinbase, Kraken, Bitstamp, Gemini, Crypto.com, Bullish) | WebSocket L2 order books | Coinbase requires CDP key (optional, falls back to public WS) | Computed 1/sec |
| Chainlink BTC/USD | Polymarket RTDS WebSocket | None | ~1/sec |
| Deribit IV | REST polling | None | Every 5 min |
| Kalshi/PM strikes | REST | Optional (Kalshi auth improves rate limits) | On startup + every 15 min |

## Requirements

```bash
pip install aiohttp websockets numpy scipy statsmodels
```

Coinbase CDP key is optional. Without it, Coinbase exchange feed is disabled but BRTI still computes from the remaining 5 exchanges.
