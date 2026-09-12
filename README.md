# The Algorithmic Wheel: DJI Implementation

A robust, systematic pipeline for harvesting options premium on the Dow Jones Industrial Average (DJI) using the QuantConnect platform. This project automates the classic "Wheel Strategy" (Cash-Secured Puts and Covered Calls) while layering in strict quantitative risk management and Greek optimization.

## Strategy Mechanics: "The Wheel"

The Wheel strategy is a two-phase cyclic process designed to generate consistent income from premiums while systematically acquiring and selling underlying assets. The primary profit driver is time decay (Theta) from short options contracts.

### Conceptual Workflow: The Premium Cycle

The diagram below illustrates the perpetual loop of selling volatility:

```mermaid
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
    class A,D,I highlight;