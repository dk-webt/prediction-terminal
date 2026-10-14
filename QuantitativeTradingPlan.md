# **Quantitative Trading Plan: 15-Minute BTC Binary Spread Arbitrage**

**Objective:** To systematically execute cross-platform spread trades (Kalshi vs. Polymarket) on 15-minute Bitcoin contracts, generating positive Expected Value ($EV$) by exploiting oracle divergence, strike misalignments, and statistical mispricing, while strictly managing the risk of Maximum Loss (the "LL" Trap Zone).

## **1\. Core Executable Strategies**

The system only executes two synthetic spread strategies, constructed using Cash-or-Nothing Digital Options (binary contracts).

* **Strategy A:** Buy Kalshi YES \+ Buy Polymarket NO  
  * **Optimal Condition:** $S\_p \> S\_k$ (Polymarket strike is higher) or $L\_k \> L\_p$ (Kalshi oracle runs higher).  
* **Strategy B:** Buy Kalshi NO \+ Buy Polymarket YES  
  * **Optimal Condition:** $S\_k \> S\_p$ (Kalshi strike is higher) and $L\_p \> L\_k$.

**Outcome Matrix:**

* **WW (Double Win):** Max Profit. Price lands between strikes.  
* **WL / LW (Single Win):** Baseline. One leg wins, one leg loses. Profit/Loss depends on premium cost.  
* **LL (Double Loss):** Max Loss (The Trap Zone). Price breaks against both positions.

## **2\. The Mathematical Framework**

The decision engine relies on four distinct quantitative models to price the assets, map the spread, calculate joint probabilities, and determine execution viability.

### **Model A: Individual Leg Pricing (Modified Black-Scholes)**

Before comparing platforms, the system must calculate the "true" probability of each individual contract winning, assuming Bitcoin follows Geometric Brownian Motion (GBM). Because the timeframe is extremely short (15 minutes), the risk-free rate is zeroed out.

We use the $d\_2$ component of Black-Scholes to find the probability of the live price finishing above the strike:

$$P(L \> S) \= \\Phi(d\_2)$$$$d\_2 \= \\frac{\\ln(L / S)}{\\sigma \\sqrt{t}}$$

* $L$**:** Current live oracle price (platform-specific).  
* $S$**:** Contract strike price.  
* $t$**:** Time remaining to expiry (as a fraction of the 15-minute window).  
* $\\Phi$**:** Cumulative distribution function (CDF) of the standard normal distribution.  
* $\\sigma$**:** 15-minute implied volatility of BTC.  
  * **Calibration (Realized Proxy Method):** Instead of using circular binary pricing to find implied volatility, the system uses high-frequency historical data as a proxy. It calculates the standard deviation of 1-minute percentage returns ($\\sigma\_{1m}$) over a rolling 2-hour window, then scales it to a 15-minute window:  
    $$\\sigma\_{15m} \= \\sigma\_{1m} \\times \\sqrt{15}$$

### **Model B: Oracle Divergence (Ornstein-Uhlenbeck Process)**

The spread between Kalshi's oracle and Polymarket's oracle ($L\_k \- L\_p$) is not random; it is mean-reverting. We model this using the Ornstein-Uhlenbeck (OU) stochastic differential equation to predict where the spread will be at expiry.

$$dx\_t \= \\theta (\\mu \- x\_t)dt \+ \\sigma\_s dW\_t$$

**Model Calibration & Proof:**

1. **Proof of Mean Reversion (ADF Test):** Before executing, the system runs an Augmented Dickey-Fuller (ADF) test on the historical spread ($x$). If the p-value is $\> 0.05$, the spread is acting as a random walk and trading is halted. If $\< 0.05$, the spread is stationary and tradeable.  
2. **Computing** $\\theta$ **(Speed of Reversion):** We run an AR(1) Linear Regression on the spread data: $\\Delta x \= a \+ b(x\_{t-1}) \+ \\epsilon$.  
   * $\\theta \= \-b / \\Delta t$  
   * **Half-Life:** We calculate exactly how long the spread takes to close by 50% using $t\_{1/2} \= \\ln(2) / \\theta$. If the half-life is longer than the time remaining to expiry, the trade is rejected.  
3. **Computing** $\\sigma\_s$ **(Spread Volatility):** The standard deviation of the residuals ($\\epsilon$) from the linear regression gives us the pure noise/volatility of the spread.  
   * $\\sigma\_s \= \\text{Standard Deviation}(\\epsilon) / \\sqrt{\\Delta t}$

### **Model C: Joint Probability (Student's t-Copula)**

Because the outcomes on Kalshi and Polymarket are highly correlated but not perfectly identical, calculating the exact probability of hitting the **LL** (Max Loss) requires a Copula. We specifically use a **Student's t-Copula** because standard Gaussian copulas fail to account for "fat tails" (extreme crypto market flash crashes where both oracles gap down simultaneously).

Let $U\_k$ be the CDF of the Kalshi outcome, and $U\_p$ be the CDF of the Polymarket outcome. The t-Copula $C$ binds them using two parameters: correlation ($\\rho$) and degrees of freedom ($\\nu$).

$$P(\\text{Joint Outcome}) \= C\_{\\rho, \\nu}(U\_k, U\_p)$$

**Model Calibration (Two-Step Inversion Method):** To ensure high-frequency computational stability and avoid non-convergence issues common in simultaneous 2D MLE, the copula is calibrated using a pseudo-likelihood approach:

1. **Step 1 (Compute Rank Correlation):** Compute Kendall's Tau ($\\tau$) on the historical rolling window of the oracle feeds to capture the rank dependence.  
2. **Step 2 (Fix the Correlation Parameter):** Invert $\\tau$ to find the copula correlation matrix ($\\rho$) using Greiner's relation. This locks in the correlation deterministically.  
   $$\\rho \= \\sin\\left(\\frac{\\pi}{2} \\tau\\right)$$  
3. **Step 3 (1D Optimization for Tails):** With $\\rho$ fixed, perform a highly stable one-dimensional Maximum Likelihood Estimation (MLE) to find the Degrees of Freedom ($\\nu$) that maximizes the likelihood of the observed tail events.

*The output of this calibrated copula isolates the exact mathematical probability of the price landing in the Trap Zone.*

### **Model D: Execution Logic (Friction-Adjusted EV)**

The system only triggers a trade if the Expected Value is strictly positive after all real-world friction is applied.

$$EV \= \[P(WW) \\cdot (2 \- Cost \- F\_k \- F\_p)\] \+ \[P(WL) \\cdot (1 \- Cost \- F\_k \- F\_p)\] \- \[P(LL) \\cdot (Cost \+ F\_k \+ F\_p)\]$$

* $Cost$**:** Total premium paid for both contracts ($C\_k \+ C\_p$).  
* $F\_k$ **/** $F\_p$**:** Platform fees (Taker fees, dynamic scaling).

**Execution Rule:** Trigger API buy orders if and only if $EV \> \\text{Minimum Alpha Threshold}$ (e.g., $0.02$, to account for slippage and model error).

## **3\. Required Data Inputs (System Architecture)**

To run this model live, the algorithm requires the following real-time data streams:

1. **High-Frequency Oracle Data:** Millisecond-level tick data for LMAX Digital (Kalshi) and the specific oracle used by Polymarket (e.g., Pyth/Binance).  
2. **Live Order Book Depth (L2):** To calculate real combined premium costs *including* slippage, not just the top-of-book price.  
3. **Dynamic Fee API:** Real-time fee polling, particularly for Polymarket's dynamic AMM taker fees.  
4. **Rolling Volatility:** A continuous calculation of Bitcoin's $\\sigma$ over rolling 15-minute windows.

## **4\. Risk Management Parameters**

* **Avoid the "0.75 Trap":** Do not buy low-premium combinations unless the strike gap ($S\_k \- S\_p$) is mathematically wide enough to contain the current volatility ($\\sigma$) given the time to expiry ($t$). Low premiums usually indicate a highly probable **LL** scenario.  
* **Position Sizing:** Use a simultaneous-bet Kelly Criterion. Because **LL** represents a 100% loss of deployed capital for that specific trade, position sizes must be kept strictly to a fractional percentage of total bankroll to survive inevitable correlation breakdowns or flash crashes.

