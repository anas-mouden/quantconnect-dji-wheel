from AlgorithmImports import *

class AlgorithmicWheelDJI(QCAlgorithm):
    def Initialize(self):
        # 1. Timeframe & Capital Allocation
        self.SetStartDate(2021, 1, 1)
        self.SetEndDate(2026, 1, 1)
        self.SetCash(1000000)  # $1,000,000 portfolio base capital

        # Define our Benchmark as the DJI rather than SP500
        self.SetBenchmark("DIA")

        # Define Margin Model to prevent default immediate forced liquidations
        self.Portfolio.MarginCallModel = MarginCallModel.Null

        # 2. Core Strategy Parameters
        self.target_put_delta = -0.30
        self.target_call_delta = 0.30
        self.rsi_period = 14
        self.max_allocation_per_stock = 0.30     # Max 30% of portfolio value per ticker
        self.max_portfolio_stock_cap = 3.0     # MAX 3x HOLDINGS RULE: Total stock holdings + put assignments <= 50%
        self.min_margin_buffer = 0.20            # Keep 20% margin cushion to prevent margin warnings
        self.iv_lookback_days = 252              # Standard 1-year window for IV Rank
        self.max_calls_per_stock = 2             # Max call cycles before liquidating underwater stock
        self.stop_loss_multiplier = 5        # Stop loss: buy back short puts if mark >= 5x entry premium

        # Asset tracking structures
        self.equity_symbols = {}                 # Maps ticker string -> Equity Symbol Object
        self.option_symbols = {}                 # Maps ticker string -> Option Symbol Object
        self.rsi_indicators = {}
        self.iv_history = {}
        self.stock_entry_date = {}
        self.call_count = {}

        # 3. Dynamic Universe via DIA ETF Constituents
        self.UniverseSettings.DataNormalizationMode = DataNormalizationMode.Raw  # Required for Options engine
        self.AddUniverse(self.Universe.ETF("DIA"))

        # 4. Daily Scheduled Execution (30 mins after Market Open)
        self.Schedule.On(
            self.DateRules.EveryDay("DIA"), 
            self.TimeRules.AfterMarketOpen("DIA", 30), 
            self.ExecuteWheelRoutine
        )

    def UniverseFunc(self, universe):
        """Filters options chain for 30-45 DTE and strike neighborhood."""
        return universe.IncludeWeeklys().Strikes(-5, 5).Expiration(timedelta(30), timedelta(45))

    def OnSecuritiesChanged(self, changes):
        """Dynamically configures Options chains, indicators, and rolling windows."""
        for added in changes.AddedSecurities:
            if added.Symbol.SecurityType == SecurityType.Equity and added.Symbol.Value != "DIA":
                ticker = added.Symbol.Value
                self.equity_symbols[ticker] = added.Symbol

                if ticker not in self.option_symbols:
                    option = self.AddOption(ticker)
                    option.SetFilter(self.UniverseFunc)
                    option.price_model = OptionPriceModels.binomial_cox_ross_rubinstein()

                    self.option_symbols[ticker] = option.Symbol
                    
                    self.rsi_indicators[ticker] = self.RSI(
                        added.Symbol, 
                        self.rsi_period, 
                        MovingAverageType.Wilders, 
                        Resolution.Daily
                    )
                    self.iv_history[ticker] = RollingWindow[float](self.iv_lookback_days)

        for removed in changes.RemovedSecurities:
            ticker = removed.Symbol.Value
            if ticker in self.rsi_indicators and not self.Securities[removed.Symbol].Holdings.Invested:
                self.rsi_indicators.pop(ticker, None)
                self.option_symbols.pop(ticker, None)
                self.equity_symbols.pop(ticker, None)
                self.iv_history.pop(ticker, None)

    def GetStockPrice(self, ticker):
        """Safely retrieves current price directly from Equity Symbol Object."""
        symbol = self.equity_symbols.get(ticker)
        if symbol and symbol in self.Securities:
            return self.Securities[symbol].Price
        return 0.0

    def OnOrderEvent(self, orderEvent):
        """Tracks stock assignment acquisition and cleans up state on position exit."""
        if orderEvent.Status == OrderStatus.Filled:
            security = self.Securities.get(orderEvent.Symbol)
            if security and security.Type == SecurityType.Equity:
                quantity = self.Portfolio[orderEvent.Symbol].Quantity

                # Position initialized (Assigned or Purchased)
                if quantity >= 100 and orderEvent.Symbol not in self.stock_entry_date:
                    self.stock_entry_date[orderEvent.Symbol] = self.Time
                    self.call_count[orderEvent.Symbol] = 0

                # Position closed (Called away or Liquidated)
                elif quantity == 0:
                    self.stock_entry_date.pop(orderEvent.Symbol, None)
                    self.call_count.pop(orderEvent.Symbol, None)

    def OnMarginCallWarning(self):
        """Cancels open orders or liquidates worst performing options on margin warning."""
        self.Warning(f"Margin Call Warning! Cash: {self.Portfolio.Cash}, Margin Remaining: {self.Portfolio.MarginRemaining}")
        
        open_orders = self.Transactions.GetOpenOrders()
        if open_orders:
            for order in open_orders:
                self.Transactions.CancelOrder(order.Id)
            return

        short_options = [h for h in self.Portfolio.Values if h.Symbol.SecurityType == SecurityType.Option and h.Quantity < 0]
        if short_options:
            short_options.sort(key=lambda x: x.UnrealizedProfit)
            for holding in short_options[:2]:
                self.Liquidate(holding.Symbol, "Proactive Margin Call Liquidation")

    def OnMarginCall(self, requests):
        return []

    def GetContractPrice(self, contract):
        """Calculates contract mid-price with fallback to available market prices."""
        if contract.BidPrice > 0 and contract.AskPrice > 0:
            return (contract.BidPrice + contract.AskPrice) / 2.0
        elif contract.LastPrice > 0:
            return contract.LastPrice
        elif contract.BidPrice > 0:
            return contract.BidPrice
        elif contract.AskPrice > 0:
            return contract.AskPrice
        return 0.0

    def ExecuteWheelRoutine(self):
        # 1. Manage early exits & hard stop loss on short options
        self.ManageShortOptionExits()

        # 2. Record daily ATM Implied Volatility
        for ticker in list(self.option_symbols.keys()):
            self.UpdateATMImpliedVolatility(ticker)

        # 3. Manage covered call writing & enforce 4-cycle baggage rule
        self.ManageCoveredCalls()

        # 4. HOLDINGS-BASED SAFETY & 50% STOCK HOLDINGS CAP
        total_portfolio = self.Portfolio.TotalPortfolioValue
        if total_portfolio <= 0:
            return

        # Check Portfolio Margin Cushion to prevent margin warnings
        margin_remaining = self.Portfolio.MarginRemaining
        if margin_remaining < (total_portfolio * self.min_margin_buffer):
            return  # Pause put selling if margin cushion drops below 20%

        # Calculate Current Stock Holdings Value
        current_stock_value = sum(
            h.HoldingsValue for h in self.Portfolio.Values 
            if h.Symbol.SecurityType == SecurityType.Equity and h.Invested
        )

        # Calculate Existing Short Put Notional Assignment Value
        current_put_notional = sum(
            abs(h.Quantity) * h.Symbol.ID.StrikePrice * 100 for h in self.Portfolio.Values 
            if h.Symbol.SecurityType == SecurityType.Option and h.Quantity < 0 and h.Symbol.ID.OptionRight == OptionRight.Put
        )

        # Calculate Pending Put Orders Notional Value
        open_orders = self.Transactions.GetOpenOrders()
        pending_put_notional = sum(
            abs(o.Quantity) * o.Symbol.ID.StrikePrice * 100 for o in open_orders 
            if o.Symbol.SecurityType == SecurityType.Option and o.Symbol.ID.OptionRight == OptionRight.Put
        )

        # TOTAL POTENTIAL STOCK EXPOSURE (Current Stock + Outstanding Puts + Pending Puts)
        total_potential_stock_exposure = current_stock_value + current_put_notional + pending_put_notional
        max_allowed_stock_exposure = total_portfolio * self.max_portfolio_stock_cap  # 50% Cap Rule

        # Room left for new put assignments before hitting 50% stock holdings limit
        available_stock_capacity = max_allowed_stock_exposure - total_potential_stock_exposure

        if available_stock_capacity <= 0:
            return

        put_candidates = []

        # 5. Signal Scanner: RSI < 35 AND IV Rank >= 40 (SMA RULE REMOVED)
        for ticker, rsi in self.rsi_indicators.items():
            if not rsi.IsReady:
                continue

            iv_rank = self.GetIVRank(ticker)

            if rsi.Current.Value < 35 and (iv_rank is None or iv_rank >= 40):
                best_contract = self.GetBestPutContract(ticker)
                if best_contract is not None:
                    put_candidates.append(best_contract)

        # 6. Rank candidates by yield per exposure
        put_candidates.sort(
            key=lambda x: self.GetContractPrice(x) / (x.Strike * 100) if x.Strike > 0 else 0, 
            reverse=True
        )

        # 7. Execute Cash-Secured Puts (Respecting 50% Portfolio Stock Cap & 10% Ticker Cap)
        for contract in put_candidates:
            contract_value = contract.Strike * 100
            ticker = contract.Symbol.Underlying.Value
            
            ticker_stock_val = self.Securities[ticker].Holdings.HoldingsValue if ticker in self.Securities and self.Securities[ticker].Invested else 0
            ticker_put_exp = sum(abs(h.Quantity) * h.Symbol.ID.StrikePrice * 100 for h in self.Portfolio.Values if h.Symbol.SecurityType == SecurityType.Option and h.Symbol.Underlying.Value == ticker and h.Quantity < 0 and h.Symbol.ID.OptionRight == OptionRight.Put)
            ticker_pending_exp = sum(abs(o.Quantity) * o.Symbol.ID.StrikePrice * 100 for o in open_orders if o.Symbol.SecurityType == SecurityType.Option and o.Symbol.Underlying.Value == ticker and o.Symbol.ID.OptionRight == OptionRight.Put)
            
            ticker_current_exp = ticker_stock_val + ticker_put_exp + ticker_pending_exp
            max_capital_for_this_stock = total_portfolio * self.max_allocation_per_stock
            
            available_for_this_ticker = max_capital_for_this_stock - ticker_current_exp
            usable_capital = min(available_stock_capacity, available_for_this_ticker)
            
            if usable_capital < contract_value:
                continue

            num_contracts = int(usable_capital // contract_value)

            if num_contracts >= 1:
                mid_price = round(self.GetContractPrice(contract), 2)
                if mid_price > 0:
                    self.LimitOrder(contract.Symbol, -num_contracts, mid_price)
                    order_exposure = contract_value * num_contracts
                    available_stock_capacity -= order_exposure

    def ManageShortOptionExits(self):
        """Buys back short options at 50% max profit, <= 7 DTE, or 3x Stop Loss."""
        for symbol, holding in list(self.Portfolio.items()):
            if holding.Symbol.SecurityType == SecurityType.Option and holding.Quantity < 0:
                entry_price = holding.AveragePrice
                current_price = holding.Price

                if entry_price > 0:
                    profit_pct = (entry_price - current_price) / entry_price
                    dte = (symbol.ID.Date - self.Time).days

                    # 1. Take Profit at 50%
                    # 2. Time Bailout at <= 7 DTE
                    # 3. Stop Loss at 3x Entry Premium (Prevents UNH-style runaway margin blowouts)
                    if profit_pct >= 0.50 or dte <= 7 or current_price >= (entry_price * self.stop_loss_multiplier):
                        self.Liquidate(symbol)

    def ManageCoveredCalls(self):
        """Monitors assigned stock lots: liquidates stock if 4 calls executed, else sells next call."""
        for symbol, holding in list(self.Portfolio.items()):
            if holding.Symbol.SecurityType == SecurityType.Equity and holding.Quantity >= 100:
                ticker = symbol.Value
                current_calls_sold = self.call_count.get(symbol, 0)

                if current_calls_sold >= self.max_calls_per_stock:
                    self.Liquidate(symbol)
                    self.call_count.pop(symbol, None)
                    self.stock_entry_date.pop(symbol, None)
                    continue

                has_active_call = any(
                    h.Symbol.SecurityType == SecurityType.Option and 
                    h.Symbol.Underlying == symbol and 
                    h.Quantity < 0 
                    for h in self.Portfolio.Values
                )

                if not has_active_call:
                    success = self.SellCoveredCall(ticker, holding)
                    if success:
                        self.call_count[symbol] = current_calls_sold + 1

    def SellCoveredCall(self, ticker, holding):
        """Sells ~0.30 Delta covered calls matching share volume via Midpoint Limit Orders."""
        option_symbol = self.option_symbols.get(ticker)
        if not option_symbol or not self.CurrentSlice or not self.CurrentSlice.OptionChains:
            return False

        chain = self.CurrentSlice.OptionChains.get(option_symbol)
        if not chain:
            return False

        cost_basis = holding.AveragePrice

        calls = [x for x in chain if x.Right == OptionRight.Call and x.Strike >= cost_basis]
        if not calls:
            calls = [x for x in chain if x.Right == OptionRight.Call]
            if not calls:
                return False

        target_calls = [c for c in calls if c.Greeks and c.Greeks.Delta is not None and 0.15 <= c.Greeks.Delta <= 0.45]
        
        if target_calls:
            best_call = sorted(target_calls, key=lambda x: abs(x.Greeks.Delta - 0.30))[0]
        else:
            underlying_price = self.GetStockPrice(ticker)
            best_call = min(calls, key=lambda x: abs(x.Strike - (underlying_price * 1.05)))

        if best_call:
            num_contracts = int(holding.Quantity // 100)
            if num_contracts >= 1:
                mid_price = round(self.GetContractPrice(best_call), 2)
                if mid_price > 0:
                    self.LimitOrder(best_call.Symbol, -num_contracts, mid_price)
                    return True

        return False

    def GetBestPutContract(self, ticker):
        """Selects ~-0.30 Delta Put contract with a ~5% OTM strike fallback."""
        option_symbol = self.option_symbols.get(ticker)
        if not option_symbol or not self.CurrentSlice or not self.CurrentSlice.OptionChains:
            return None

        chain = self.CurrentSlice.OptionChains.get(option_symbol)
        if not chain:
            return None

        puts = [x for x in chain if x.Right == OptionRight.Put]
        if not puts:
            return None

        target_puts = [p for p in puts if p.Greeks and p.Greeks.Delta is not None and -0.40 <= p.Greeks.Delta <= -0.20]
        if target_puts:
            return sorted(target_puts, key=lambda x: abs(x.Greeks.Delta - (-0.30)))[0]

        underlying_price = self.GetStockPrice(ticker)
        if underlying_price > 0:
            return min(puts, key=lambda x: abs(x.Strike - (underlying_price * 0.95)))

        return None

    def UpdateATMImpliedVolatility(self, ticker):
        """Records ATM Implied Volatility into rolling window."""
        option_symbol = self.option_symbols.get(ticker)
        if not option_symbol or not self.CurrentSlice or not self.CurrentSlice.OptionChains:
            return None

        chain = self.CurrentSlice.OptionChains.get(option_symbol)
        if not chain:
            return None

        underlying_price = self.GetStockPrice(ticker)
        if underlying_price <= 0:
            return None

        atm_contract = min(chain, key=lambda x: abs(x.Strike - underlying_price))

        if atm_contract and atm_contract.ImpliedVolatility is not None and atm_contract.ImpliedVolatility > 0:
            iv = atm_contract.ImpliedVolatility
            if ticker in self.iv_history:
                self.iv_history[ticker].Add(iv)
            return iv

        return None

    def GetIVRank(self, ticker):
        """Calculates 252-day IV Rank (0-100)."""
        window = self.iv_history.get(ticker)
        if not window or not window.IsReady:
            return None

        current_iv = window[0]
        iv_list = [x for x in window]
        min_iv = min(iv_list)
        max_iv = max(iv_list)

        if max_iv == min_iv:
            return 50.0

        return ((current_iv - min_iv) / (max_iv - min_iv)) * 100.0