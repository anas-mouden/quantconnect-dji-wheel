# The Algorithmic Wheel: DJI Implementation

A robust, systematic pipeline for harvesting options premium on the Dow Jones Industrial Average (DJI) using the QuantConnect platform. This project automates the classic "Wheel Strategy" (Cash-Secured Puts and Covered Calls) while layering in strict quantitative risk management and Greek optimization.

## Strategy Mechanics: "The Wheel"

The Wheel strategy is a two-phase cyclic process designed to generate consistent income from premiums while systematically acquiring and selling underlying assets. The primary profit driver is time decay (Theta) from short options contracts.

### Conceptual Workflow: The Premium Cycle

The diagram below illustrates the perpetual loop of selling volatility:

'''mermaid
graph TD
    A[Start: Cash in Account] -->|Identify Oversold DJI Stock| B(Phase 1: Sell Cash-Secured Put)
    B --> C{Expiration Date Reached}
    
    C -->|Stock Price > Strike| D[Put Expires Worthless]
    D -->|Keep Premium & Cash| A
    
    C -->|Stock Price < Strike| E[Assigned 100 Shares]
    E -->|Keep Premium & Shares| F(Phase 2: Sell Covered Call)
    
    F --> G{Expiration Date Reached}
    
    G -->|Stock Price < Call Strike| H[Call Expires Worthless]
    H -->|Keep Premium & Shares| F
    
    G -->|Stock Price > Call Strike| I[Shares Called Away / Sold]
    I -->|Keep Premium & Capital Gains| A
    
    classDef default fill:#1f2937,stroke:#3b82f6,stroke-width:2px,color:#fff;
    classDef highlight fill:#3b82f6,stroke:#1e3a8a,stroke-width:2px,color:#fff;
    class A,D,I highlight;'''

*Figure 1: Conceptual overview showing the transition between Cash-Secured Puts (Phase 1) and Covered Calls (Phase 2), highlighting the role of premiums and assignment risk.*

1.  **Phase 1 (Put-Write):** Sell an out-of-the-money (OTM) Put option. You collect an upfront cash premium. If the stock drops below the strike price at expiration, you are "assigned" 100 shares at that price.
2.  **Phase 2 (Call-Write):** Once you own 100 shares, you sell an OTM Call option against those shares. You collect another upfront cash premium. If the stock rises above the strike price at expiration, your shares are "called away," and you return to cash.

## Algorithmic Methodology & Pipeline

This algorithm dynamically manages capital and optimizes for yield-per-exposure rather than passively selling contracts. The process is fully automated within the QuantConnect LEAN engine.

### System Architecture and Development Phases

The diagram below outlines the main stages of development and the execution pipeline of the live algorithm:

'''mermaid
flowchart LR
    subgraph Data & Signal
    A[(DJI Universe)] --> B[Calculate 14-Day RSI]
    B -->|RSI < 35| C{Mean Reversion Trigger}
    end
    
    subgraph Options Greek Filter
    C --> D[Pull Options Chain 30-45 DTE]
    D --> E[Filter Delta ±0.30]
    E --> F[Rank by Yield/Exposure]
    end
    
    subgraph Risk Manager
    F --> G{Liquidity & Solvency Check}
    G -->|Pass| H[Execute Trade]
    G -->|Fail / Over-Exposed| I[Halt Entry]
    end
    
    subgraph Position Management
    H --> J[Monitor Covered Call Cycles]
    J -->|Stagnant/Crashed Stock| K[Lower Strike or Liquidate]
    end

    classDef dark fill:#2d3748,stroke:#4fd1c5,stroke-width:2px,color:#fff;
    class A,B,C,D,E,F,G,H,I,J,K dark;'''

*Figure 2: Architecture flow diagram showing the modular components of the system: Universe Selection, Signal Generation (Mean Reversion), Greek/DTE Optimization, Position/Risk Management, and the final automated execution loop.*

**Core Execution Parameters (Currently in Python/LEAN):**

*   **Universe:** 30 Constituents of the DJI.
*   **Mean Reversion Entry:** Sells Cash-Secured Puts only when a stock's 14-day RSI drops below 35 (identifying short-term oversold conditions and typically higher IV).
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