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
        self.max_allocation_per_stock = 0.50     # Max allocation of portfolio value per ticker
        self.max_portfolio_stock_cap = 3.0       # Max total stock holdings + put assignments cap
        self.min_margin_buffer = 0.20            # Keep 20% margin cushion to prevent margin warnings
        self.iv_lookback_days = 252              # Standard 1-year trading window for IV Rank
        self.max_calls_per_stock = 2             # Max call cycles before liquidating underwater stock
        self.stop_loss_multiplier = 5            # Stop loss: buy back short options if mark >= 5x entry premium

        # VIX 50-Day SMA Hysteresis Filter Parameters
        self.vix_symbol = self.AddData(CBOE, "VIX", Resolution.Daily).Symbol
        self.vix_sma = self.SMA(self.vix_symbol, 15, Resolution.Daily)
        self.vix_upper_threshold = 30.0          # Pause new puts when VIX 50 SMA > 25
        self.vix_lower_threshold = 20.0          # Resume new puts when VIX 50 SMA < 20
        self.wheel_active = True                 # Current regime state

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
                    option.PriceModel = OptionPriceModels.BinomialCoxRossRubinstein()

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

            # A covered call that actually FILLED (sold to open) counts as one completed cycle
            elif (security and security.Type == SecurityType.Option
                  and orderEvent.Symbol.ID.OptionRight == OptionRight.Call
                  and orderEvent.FillQuantity < 0):
                underlying = orderEvent.Symbol.Underlying
                if underlying in self.call_count:
                    self.call_count[underlying] += 1

    def OnMarginCallWarning(self):
        """Cancels open orders or liquidates worst performing options on margin warning."""
        self.Warning(f"Margin Call Warning! Cash: {self.Portfolio.Cash}, Margin Remaining: {self.Portfolio.MarginRemaining}")

        open_orders = self.Transactions.GetOpenOrders()
        if open_orders:
            for order in open_orders:
                self.Transactions.CancelOrder(order.Id)
            return

        short_options = [
            h for h in self.Portfolio.Values
            if h.Symbol.SecurityType == SecurityType.Option and h.Quantity < 0
        ]
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

    def IsVixRegimeSafe(self) -> bool:
        """Evaluates the 20/25 VIX 50-day SMA hysteresis band and updates strategy state."""
        if not self.vix_sma.IsReady:
            return False

        current_vix_sma = self.vix_sma.Current.Value

        # Trigger Pause: Active -> Paused when VIX 50 SMA breaks above 25
        if self.wheel_active and current_vix_sma > self.vix_upper_threshold:
            self.wheel_active = False
            self.Debug(
                f"[{self.Time}] PAUSED: VIX 50 SMA broke above {self.vix_upper_threshold} ({current_vix_sma:.2f})."
            )

        # Trigger Resume: Paused -> Active only after VIX 50 SMA crosses below 20
        elif not self.wheel_active and current_vix_sma < self.vix_lower_threshold:
            self.wheel_active = True
            self.Debug(
                f"[{self.Time}] RESUMED: VIX 50 SMA crossed below {self.vix_lower_threshold} ({current_vix_sma:.2f})."
            )

        # Plot VIX SMA and thresholds on the backtest chart
        self.Plot("VIX Risk Filter", "VIX 50 SMA", current_vix_sma)
        self.Plot("VIX Risk Filter", "Pause Threshold (25)", self.vix_upper_threshold)
        self.Plot("VIX Risk Filter", "Resume Threshold (20)", self.vix_lower_threshold)

        return self.wheel_active

    def ExecuteWheelRoutine(self):
        # 1. Manage early exits & hard stop loss on short options
        self.ManageShortOptionExits()

        # 2. Record daily ATM Implied Volatility
        for ticker in list(self.option_symbols.keys()):
            self.UpdateATMImpliedVolatility(ticker)

        # 3. Manage covered call writing & enforce max-cycle liquidation rule
        self.ManageCoveredCalls()

        # 4. VIX HYSTERESIS GATE: Block new Cash-Secured Puts if VIX 50 SMA > 25 (until < 20)
        if not self.IsVixRegimeSafe():
            return

        # 5. HOLDINGS-BASED SAFETY & PORTFOLIO EXPOSURE CAP
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
            if h.Symbol.SecurityType == SecurityType.Option
            and h.Quantity < 0
            and h.Symbol.ID.OptionRight == OptionRight.Put
        )

        # Calculate Pending Put Orders Notional Value
        open_orders = self.Transactions.GetOpenOrders()
        pending_put_notional = sum(
            abs(o.Quantity) * o.Symbol.ID.StrikePrice * 100 for o in open_orders
            if o.Symbol.SecurityType == SecurityType.Option
            and o.Symbol.ID.OptionRight == OptionRight.Put
        )

        # TOTAL POTENTIAL STOCK EXPOSURE (Current Stock + Outstanding Puts + Pending Puts)
        total_potential_stock_exposure = current_stock_value + current_put_notional + pending_put_notional
        max_allowed_stock_exposure = total_portfolio * self.max_portfolio_stock_cap

        # Room left for new put assignments before hitting portfolio limit
        available_stock_capacity = max_allowed_stock_exposure - total_potential_stock_exposure

        if available_stock_capacity <= 0:
            return

        put_candidates = []

        # 6. Signal Scanner: RSI < 35 AND IV Rank >= 40
        for ticker, rsi in self.rsi_indicators.items():
            if not rsi.IsReady:
                continue

            iv_rank = self.GetIVRank(ticker)

            if rsi.Current.Value < 35 and (iv_rank is None or iv_rank >= 40):
                best_contract = self.GetBestPutContract(ticker)
                if best_contract is not None:
                    put_candidates.append(best_contract)

        # 7. Rank candidates by yield per exposure
        put_candidates.sort(
            key=lambda x: self.GetContractPrice(x) / (x.Strike * 100) if x.Strike > 0 else 0,
            reverse=True
        )

        # 8. Execute Cash-Secured Puts (Respecting Portfolio Cap & Per-Ticker Cap)
        for contract in put_candidates:
            contract_value = contract.Strike * 100
            ticker = contract.Symbol.Underlying.Value

            ticker_stock_val = (
                self.Securities[ticker].Holdings.HoldingsValue
                if ticker in self.Securities and self.Securities[ticker].Invested
                else 0
            )
            ticker_put_exp = sum(
                abs(h.Quantity) * h.Symbol.ID.StrikePrice * 100
                for h in self.Portfolio.Values
                if h.Symbol.SecurityType == SecurityType.Option
                and h.Symbol.Underlying.Value == ticker
                and h.Quantity < 0
                and h.Symbol.ID.OptionRight == OptionRight.Put
            )
            ticker_pending_exp = sum(
                abs(o.Quantity) * o.Symbol.ID.StrikePrice * 100
                for o in open_orders
                if o.Symbol.SecurityType == SecurityType.Option
                and o.Symbol.Underlying.Value == ticker
                and o.Symbol.ID.OptionRight == OptionRight.Put
            )

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
        """Buys back short options at 50% max profit, <= 7 DTE, or stop-loss multiple."""
        for symbol, holding in list(self.Portfolio.items()):
            if holding.Symbol.SecurityType == SecurityType.Option and holding.Quantity < 0:
                entry_price = holding.AveragePrice
                current_price = holding.Price

                if entry_price > 0:
                    profit_pct = (entry_price - current_price) / entry_price
                    dte = (symbol.ID.Date - self.Time).days

                    # 1. Take Profit at 50%
                    # 2. Time Bailout at <= 7 DTE
                    # 3. Stop Loss at multiplier * Entry Premium
                    if profit_pct >= 0.50 or dte <= 7 or current_price >= (entry_price * self.stop_loss_multiplier):
                        self.Liquidate(symbol)

    def ManageCoveredCalls(self):
        """Monitors assigned stock lots: sells a call when none is open; liquidates the stock only
        after max_calls_per_stock calls have filled and no call is open or pending."""
        open_orders = self.Transactions.GetOpenOrders()
        for symbol, holding in list(self.Portfolio.items()):
            if holding.Symbol.SecurityType == SecurityType.Equity and holding.Quantity >= 100:
                ticker = symbol.Value

                # Only short CALLS count (a short put on the same stock must not block a covered call),
                # and a pending call order counts so we never stack duplicate call orders.
                has_active_call = any(
                    h.Symbol.SecurityType == SecurityType.Option and
                    h.Symbol.Underlying == symbol and
                    h.Symbol.ID.OptionRight == OptionRight.Call and
                    h.Quantity < 0
                    for h in self.Portfolio.Values
                ) or any(
                    o.Symbol.SecurityType == SecurityType.Option and
                    o.Symbol.Underlying == symbol and
                    o.Symbol.ID.OptionRight == OptionRight.Call
                    for o in open_orders
                )
                if has_active_call:
                    continue

                # Never liquidate the shares while a call is still open (that would leave it uncovered)
                if self.call_count.get(symbol, 0) >= self.max_calls_per_stock:
                    self.Liquidate(symbol)
                    self.call_count.pop(symbol, None)
                    self.stock_entry_date.pop(symbol, None)
                    continue

                self.SellCoveredCall(ticker, holding)

    def SellCoveredCall(self, ticker, holding):
        """Sells ~0.30 Delta covered calls matching share volume via Midpoint Limit Orders."""
        option_symbol = self.option_symbols.get(ticker)
        if not option_symbol or not self.CurrentSlice or not self.CurrentSlice.OptionChains:
            return False

        chain = self.CurrentSlice.OptionChains.get(option_symbol)
        if not chain:
            return False

        cost_basis = holding.AveragePrice

        # Only sell calls at or above cost basis; wait for a better strike rather than lock in a loss
        calls = [x for x in chain if x.Right == OptionRight.Call and x.Strike >= cost_basis]
        if not calls:
            return False

        target_calls = [
            c for c in calls
            if c.Greeks and c.Greeks.Delta is not None and 0.15 <= c.Greeks.Delta <= 0.45
        ]

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

        target_puts = [
            p for p in puts
            if p.Greeks and p.Greeks.Delta is not None and -0.40 <= p.Greeks.Delta <= -0.20
        ]
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