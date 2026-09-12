# The Algorithmic Wheel: DJI Implementation

A robust, systematic pipeline for harvesting options premium on the Dow Jones Industrial Average (DJI) using the QuantConnect platform. This project automates the classic "Wheel Strategy" (Cash-Secured Puts and Covered Calls) while layering in strict quantitative risk management and greek optimization.

## Core Strategy & Methodology

This algorithm moves beyond passive premium collection by dynamically managing capital and optimizing for yield-per-exposure. 

*   **Asset Universe:** 30 Constituents of the Dow Jones Industrial Average (DJI).
*   **Mean Reversion Entry:** Sells Cash-Secured Puts only when a stock's 14-day RSI drops below 35 (identifying short-term oversold conditions).
*   **Greek Optimization:** Targets options with 30-45 Days to Expiration (DTE) and a Delta of ±0.30.
*   **Yield Maximization:** Ranks passing option contracts by `Premium / (Strike * 100)` to ensure the highest return on tied-up capital.

## Risk & Position Management

*   **Capital Solvency:** Caps individual stock exposure at 10% of total equity and halts new entries if the total portfolio Delta exceeds safety thresholds.
*   **Liquidity Buffer:** Maintains a strict 10% cash reserve to prevent margin calls during broad market drawdowns.
*   **"Baggage" Management:** Actively tracks covered call cycles. If an assigned stock crashes and premiums dry up at the original cost basis, the algorithm forces a decision to either lower the strike price or liquidate the asset to free up capital.

## Getting Started (Local Development)

1.  **Install Lean CLI:** Run `pip install lean` in your terminal.
2.  **Authenticate:** Run `lean login` to connect to your QuantConnect account.
3.  **Sync Code:** Use `lean cloud push` or `lean cloud pull` to sync this repository with your QuantConnect web environment.