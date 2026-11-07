import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
import seaborn as sns
import yfinance as yf

# Set plot style
sns.set_theme(style="darkgrid")
plt.rcParams["figure.figsize"] = (14, 7)
plt.rcParams["font.sans-serif"] = "DejaVu Sans"

# ---------------------------------------------------------
# 1. Load and Process Portfolio Trade Data
# ---------------------------------------------------------
csv_filename = "Virtual Red Rhinoceros_trades.csv"

# Load trade log
df = pd.read_csv(csv_filename)

# Parse exit dates and calculate Net P&L (P&L minus fees)
df["Exit Date"] = pd.to_datetime(df["Exit Time"]).dt.tz_localize(None).dt.date
df["Net_PnL"] = df["P&L"] - df["Fees"]

# Aggregate daily Net P&L
daily_pnl = df.groupby("Exit Date")["Net_PnL"].sum().reset_index()
daily_pnl["Exit Date"] = pd.to_datetime(daily_pnl["Exit Date"])
daily_pnl.set_index("Exit Date", inplace=True)

# Define business day date range matching trade activity
start_date = daily_pnl.index.min()
end_date = daily_pnl.index.max()
full_bday_idx = pd.date_range(start=start_date, end=end_date, freq="B")

# Reindex daily PnL across all business days (fill missing days with 0 PnL)
portfolio = daily_pnl.reindex(full_bday_idx).fillna({"Net_PnL": 0.0})

# Set initial equity to calculate percentage returns (Adjust if known)
INITIAL_CAPITAL = 1_000_000.0  
portfolio["Equity"] = INITIAL_CAPITAL + portfolio["Net_PnL"].cumsum()
portfolio["Strategy_Return"] = portfolio["Equity"].pct_change().fillna(0.0)

# ---------------------------------------------------------
# 2. Download S&P 500 (SPY) Benchmark Data
# ---------------------------------------------------------
print(f"Fetching SPY data from {start_date.date()} to {end_date.date()}...")
spy_data = yf.download("SPY", start=start_date, end=end_date)

# Calculate daily price returns
if isinstance(spy_data.columns, pd.MultiIndex):
    spy_returns = spy_data["Close"]["SPY"].pct_change().rename("SPY_Return")
else:
    spy_returns = spy_data["Close"].pct_change().rename("SPY_Return")

# Align timezone index format
spy_returns.index = pd.to_datetime(spy_returns.index).tz_localize(None)

# Combine strategy and benchmark returns into a single DataFrame
combined = pd.concat([portfolio["Strategy_Return"], spy_returns], axis=1).dropna()

# ---------------------------------------------------------
# 3. Calculate Rolling Correlations
# ---------------------------------------------------------
WINDOW_SHORT = 30   # 30-day (1-month) rolling window
WINDOW_MID = 60     # 60-day (3-month) rolling window
WINDOW_LONG = 90    # 90-day (quarterly) rolling window

combined["Corr_30D"] = combined["Strategy_Return"].rolling(window=WINDOW_SHORT).corr(combined["SPY_Return"])
combined["Corr_60D"] = combined["Strategy_Return"].rolling(window=WINDOW_MID).corr(combined["SPY_Return"])
combined["Corr_90D"] = combined["Strategy_Return"].rolling(window=WINDOW_LONG).corr(combined["SPY_Return"])

# Calculate overall mean correlation across full time series
mean_corr_60d = combined["Corr_60D"].mean()

# ---------------------------------------------------------
# 4. Plot Rolling Correlation Time Series
# ---------------------------------------------------------
fig, ax = plt.subplots(figsize=(14, 6), dpi=150)

# Plot primary 60-Day rolling correlation trend
ax.plot(
    combined.index, 
    combined["Corr_60D"], 
    color="#1f77b4", 
    linewidth=2.0, 
    label=f"60-Day Rolling Correlation (Mean: {mean_corr_60d:.2f})"
)

# Plot secondary 30-Day and 90-Day correlation windows for sensitivity comparison
ax.plot(
    combined.index, 
    combined["Corr_30D"], 
    color="#ff7f0e", 
    linewidth=1.0, 
    alpha=0.4, 
    linestyle="--", 
    label="30-Day Rolling Window"
)
ax.plot(
    combined.index, 
    combined["Corr_90D"], 
    color="#2ca02c", 
    linewidth=1.5, 
    alpha=0.7, 
    linestyle=":", 
    label="90-Day Rolling Window"
)

# Reference lines & shaded areas
ax.axhline(0, color="black", linestyle="-", linewidth=0.8, alpha=0.7)
ax.axhline(mean_corr_60d, color="#d62728", linestyle="--", linewidth=1.2, label=f"Average (60D): {mean_corr_60d:.2f}")

# Fill positive vs negative correlation shading
ax.fill_between(
    combined.index, 
    combined["Corr_60D"], 
    0, 
    where=(combined["Corr_60D"] >= 0), 
    color="#1f77b4", 
    alpha=0.15, 
    interpolate=True
)
ax.fill_between(
    combined.index, 
    combined["Corr_60D"], 
    0, 
    where=(combined["Corr_60D"] < 0), 
    color="#d62728", 
    alpha=0.15, 
    interpolate=True
)

# Formatting
ax.set_title("Rolling Correlation: Portfolio Strategy vs. S&P 500 (SPY)", fontsize=14, fontweight="bold", pad=15)
ax.set_ylabel("Pearson Correlation Coefficient (r)", fontsize=11)
ax.set_xlabel("Date", fontsize=11)
ax.set_ylim(-1.0, 1.0)
ax.legend(loc="upper right", frameon=True, facecolor="white", edgecolor="none")

plt.tight_layout()
plt.show()