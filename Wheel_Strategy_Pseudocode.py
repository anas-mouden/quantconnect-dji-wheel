# ==============================================================================
# ALGORITHM INITIALIZATION
# ==============================================================================
FUNCTION Initialize():
    Set Starting Capital to X
    Set Universe to DJI Constituents (30 stocks)
    Set Options Filter: 30 to 45 Days to Expiration (DTE)
    
    # Strategy Parameters
    Set Target_Put_Delta = -0.30
    Set Target_Call_Delta = 0.30
    Set RSI_Period = 14
    Set Max_Allocation_Per_Stock = 0.10 * X  # Max 10% capital per ticker
    Set Stagnant_Cycle_Limit = 4             # Max CC cycles before intervention

# ==============================================================================
# MAIN EVENT LOOP (Runs Daily)
# ==============================================================================
FUNCTION OnData(Data):
    # 1. Housekeeping: Update current state
    Update Portfolio States (Cash, Margin, Open Positions)
    
    # 2. Manage Existing Positions
    ManageCoveredCalls()
    ManageStagnantPositions()
    
    # 3. Scan for New Opportunities (If capital allows)
    Available_Capital = RiskManager_GetAvailableCapital()
    IF Available_Capital > 0:
        Candidates = ScanForPutCandidates(Universe)
        ExecutePuts(Candidates, Available_Capital)

# ==============================================================================
# RISK, LIQUIDITY & SOLVENCY MANAGER
# ==============================================================================
FUNCTION RiskManager_GetAvailableCapital():
    Calculate Total_Portfolio_Delta
    Calculate Current_Cash_Buffer
    
    # Solvency Check: Ensure we don't over-leverage
    IF Total_Portfolio_Delta > Max_Allowed_Portfolio_Delta:
        RETURN 0 # Halt new entries, we are too long/exposed
        
    # Liquidity Check: Always keep 10% cash free for assignments/margin requirements
    Required_Liquidity_Buffer = X * 0.10
    Available_Cash = Current_Cash - Required_Liquidity_Buffer
    
    RETURN Available_Cash

# ==============================================================================
# MEAN REVERSION & GREEK OPTIMIZATION
# ==============================================================================
FUNCTION ScanForPutCandidates(Universe):
    Valid_Candidates = []
    
    FOR Stock IN Universe:
        # 1. Mean Reversion Logic (Find oversold stocks)
        Current_RSI = CalculateRSI(Stock, RSI_Period)
        IF Current_RSI > 35: 
            CONTINUE # Skip, not oversold enough
            
        # 2. Greek & Premium Optimization
        Options_Chain = GetOptionsChain(Stock)
        Best_Contract = NULL
        Max_Yield_Per_Exposure = 0
        
        FOR Contract IN Options_Chain:
            IF DTE not between 30 and 45: CONTINUE
            IF Abs(Contract.Delta) not near Target_Put_Delta: CONTINUE
            
            # Maximize premium relative to capital required (Strike * 100)
            Capital_Required = Contract.Strike * 100
            Yield = Contract.Premium / Capital_Required
            
            IF Yield > Max_Yield_Per_Exposure:
                Max_Yield_Per_Exposure = Yield
                Best_Contract = Contract
                
        IF Best_Contract is NOT NULL:
            Add Best_Contract to Valid_Candidates
            
    # Sort by highest yield per exposure
    Sort Valid_Candidates BY Max_Yield_Per_Exposure DESC
    RETURN Valid_Candidates

# ==============================================================================
# POSITION MANAGEMENT & THE "BAGGAGE" SCENARIO
# ==============================================================================
FUNCTION ManageCoveredCalls():
    FOR EACH Assigned_Stock in Portfolio:
        IF Stock does NOT have an active Covered Call:
            # Attempt to write a call at or above Cost Basis
            Call_Contract = FindCall(Assigned_Stock, Target_Call_Delta, Strike >= Cost_Basis)
            
            IF Call_Contract is VALID:
                Sell Call_Contract
                Increment Stock.Call_Cycles_Written by 1

FUNCTION ManageStagnantPositions():
    FOR EACH Assigned_Stock in Portfolio:
        # The Baggage Condition: Stock price crashed, premiums at cost basis are $0.00
        IF Stock.Call_Cycles_Written >= Stagnant_Cycle_Limit OR Premium_At_Cost_Basis == 0:
            
            # Decision Tree for Dead Capital
            IF Market_Regime is Bullish AND Stock_Fundamentals are Strong:
                # Option A: Lower the strike. Accept selling a call BELOW cost basis 
                # to generate yield, risking a capital loss if assigned.
                Sell Call at (Current_Price + 1 Standard Deviation)
                
            ELSE:
                # Option B: Cut the loss to free up liquidity and solvency for better DJI candidates
                Liquidate Assigned_Stock
                Record Loss