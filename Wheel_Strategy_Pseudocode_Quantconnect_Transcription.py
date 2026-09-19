from AlgorithmImports import *

class AlgorithmicWheelDJI(QCAlgorithm):
    def Initialize(self):
        # 1. Project Timeframe & Capital Allocation
        self.SetStartDate(2023, 1, 1)
        self.SetEndDate(2025, 1, 1)
        self.SetCash(100000) # Defined capital of $100,000
        # 2. Strategy Parameters
        self.target_put_delta = -0.30
        self.target_call_delta = 0.30
        self.rsi_period = 14
        self.max_allocation_per_stock = 0.10  # Max 10% of portfolio per ticker
        self.liquidity_buffer = 0.10          # Keep 10% in cash

        # 3. Universe Definition (DJI Subset for testing limits)
        # In a live algorithm, this would encompass all 30 tickers.
        self.dji_tickers = ["AAPL",  # Apple
    "MSFT",  # Microsoft
    "JPM",   # JPMorgan Chase
    "V",     # Visa
    "WMT",   # Walmart
    "JNJ",   # Johnson & Johnson
    "PG",    # Procter & Gamble
    "AMGN",  # Amgen
    "AXP",   # American Express
    "BA",    # Boeing
    "CAT",   # Caterpillar
    "CRM",   # Salesforce
    "CSCO",  # Cisco
    "CVX",   # Chevron
    "DIS",   # Disney
    "GS",    # Goldman Sachs
    "HD",    # Home Depot
    "HON",   # Honeywell
    "IBM",   # IBM
    "INTC",  # Intel
    "KO",    # Coca-Cola
    "MCD",   # McDonald's
    "MMM",   # 3M
    "MRK",   # Merck
    "NKE",   # Nike
    "TRV",   # Travelers
    "UNH",   # UnitedHealth
    "VZ",    # Verizon
    "WBA"    # Walgreens Boots Alliance
]

        self.option_symbols = {}
        self.rsi_indicators = {}
        
        for ticker in self.dji_tickers:
            # Add Equity
            equity = self.AddEquity(ticker, Resolution.Minute)
            equity.SetDataNormalizationMode(DataNormalizationMode.Raw) # Options require Raw data
            
            # Add Options Universe & Filter
            option = self.AddOption(ticker)
            option.SetFilter(self.UniverseFunc)
            self.option_symbols[ticker] = option.Symbol
            
            # Initialize RSI for Mean Reversion
            self.rsi_indicators[ticker] = self.RSI(equity.Symbol, self.rsi_period, MovingAverageType.Wilders, Resolution.Daily)

            # Set Option Pricing Model to calculate Greeks (Delta, Theta, Vega)
            option.price_model = OptionPriceModels.binomial_cox_ross_rubinstein()

        # 4. Schedule the core loop to run daily, 30 minutes after market open
        self.Schedule.On(self.DateRules.EveryDay(self.dji_tickers[0]), 
                         self.TimeRules.AfterMarketOpen(self.dji_tickers[0], 30), 
                         self.ExecuteWheelRoutine)

    def UniverseFunc(self, universe):
        # 30-45 DTE Filter as defined in specifications
        return universe.IncludeWeeklys().Strikes(-5, 5).Expiration(timedelta(30), timedelta(45))

    def ExecuteWheelRoutine(self):
        # 1. Liquidity & Solvency Manager
        # Calculate true available cash minus our 10% required safety buffer
        required_buffer = self.Portfolio.TotalPortfolioValue * self.liquidity_buffer
        available_cash = self.Portfolio.Cash - required_buffer
        
        if available_cash <= 0:
            return # Halt new entries; liquidity threshold reached

        put_candidates = []

        # 2. Mean Reversion Scanner
        for ticker, rsi in self.rsi_indicators.items():
            if not rsi.IsReady:
                continue
            
            # Signal: RSI < 35 (Oversold)
            if rsi.Current.Value < 35:
                best_contract = self.GetBestPutContract(ticker)
                if best_contract is not None:
                    put_candidates.append(best_contract)

        # 3. Greek & Premium Optimization (Rank by Yield/Exposure)
        # Formula: Premium / (Strike * 100)
        put_candidates.sort(key=lambda x: x.BidPrice / (x.Strike * 100) if x.Strike > 0 else 0, reverse=True)

        # 4. Execute Puts
        for contract in put_candidates:
            capital_required = contract.Strike * 100
            portfolio_limit = self.Portfolio.TotalPortfolioValue * self.max_allocation_per_stock
            
            if capital_required <= available_cash and capital_required <= portfolio_limit:
                self.MarketOrder(contract.Symbol, -1) # Sell 1 Put
                available_cash -= capital_required
                
        # 5. Manage Active Covered Calls & "Baggage" Stocks
        self.ManageCoveredCalls()

    def GetBestPutContract(self, ticker):
        option_symbol = self.option_symbols[ticker]
        chain = self.CurrentSlice.OptionChains.get(option_symbol)
        
        if not chain: return None

        # Filter for Puts
        puts = [x for x in chain if x.Right == OptionRight.Put]
        if not puts: return None

        # Filter by Delta (Targeting near -0.30)
        # Note: Requires Greeks to be calculated by the engine
        target_puts = [p for p in puts if p.Greeks.Delta and -0.40 <= p.Greeks.Delta <= -0.20]
        
        if target_puts:
            # Sort to find the contract closest to exactly -0.30 Delta
            return sorted(target_puts, key=lambda x: abs(x.Greeks.Delta - (-0.30)))[0]
            
        return None

    def ManageCoveredCalls(self):
        # Iterate through portfolio checking for assigned 100-share lots
        for symbol, holding in self.Portfolio.items():
            if holding.Type == SecurityType.Equity and holding.Quantity >= 100:
                ticker = symbol.Value
                
                # Check if we already have an active short call against this stock
                has_active_call = any(x.Value.Type == SecurityType.Option and 
                                      x.Value.Symbol.Underlying == symbol and 
                                      x.Value.Quantity < 0 
                                      for x in self.Portfolio)
                
                if not has_active_call:
                    self.SellCoveredCall(ticker, holding.AveragePrice)

    def SellCoveredCall(self, ticker, cost_basis):
        option_symbol = self.option_symbols.get(ticker)
        if not option_symbol: return
        
        chain = self.CurrentSlice.OptionChains.get(option_symbol)
        if not chain: return
        
        # Ensure we only sell calls at or above our cost basis to prevent assigned capital loss
        calls = [x for x in chain if x.Right == OptionRight.Call and x.Strike >= cost_basis]
        if not calls: return
        
        # Target ~0.30 Delta for the Call
        target_calls = [c for c in calls if c.Greeks.Delta and 0.20 <= c.Greeks.Delta <= 0.40]
        
        if target_calls:
            best_call = sorted(target_calls, key=lambda x: abs(x.Greeks.Delta - 0.30))[0]
            self.MarketOrder(best_call.Symbol, -1) # Sell 1 Call