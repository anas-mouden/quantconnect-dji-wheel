# Algorithmic Wheel Options Strategy on DJI

Automated options premium harvesting pipeline targeting Dow Jones Industrial Average (DJI) constituents using QuantConnect.

## Key Strategy Parameters
- **Asset Universe:** Dow Jones Industrial Average (DJI)
- **Put Entry:** 30–45 DTE at 0.20–0.30 Delta
- **Call Entry:** 30–45 DTE at 0.30 Delta (post-assignment)
- **Volatility Filter:** IV Rank > 30%
- **Platform:** QuantConnect (Python)