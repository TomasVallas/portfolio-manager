# Portfolio Manager

Desktop app for tracking Nordnet deposits, trades and dividends together with loan drawdowns and payments. It values the portfolio with live prices and shows how much of the money in Nordnet is student loan and how much is your own.

## Pages

Pick a page from the top menu:

- **Overview:** portfolio value in euros (in dollars underneath), today's change, total return, funding (student loan / own money / gifted shares), loan balance and net worth. Below that: the allocation treemap, today's movers and the performance chart (1D, 1W, 1M, 3M, 1Y, All).
- **Portfolio:** holdings in US dollars: average price, live price, today's move, value, cost, return and weight. Accounts and income/costs are shown in euros. The Binance & Nordea section holds assets outside Nordnet (XRP, NORDEA GLOBAL E) as quantity and average cost; they count in the total value, allocation and movers.
- **Transactions:** the new entry form and every transaction. Click a row to edit it. A delete can be undone.
- **Loans:** student loan vs. own money, what would be left if you paid the loan off now, loan balances and loan entries.
- **Notes:** free text for plans.
- **Settings:** ticker symbols, live prices on/off, accounts, data file, CSV export and the fallback exchange rate.

In the **allocation treemap** a tile's size is its share of the portfolio and its colour is today's price move. Green means up and red means down, and a stronger colour means a bigger move. Clicking a tile opens the stock's TradingView chart in the browser.

## Performance chart

- **1D:** today's value in 5-minute steps. The dashed line is yesterday's close.
- **1W:** the last five trading days in 30-minute steps. The dashed step line is the money you put in, so deposits are not counted as returns.
- **1M–All:** daily value since your first entry, over the stacked contributions (own money, gifted shares, student loan).

## Entries

- **Nordnet:** deposit, buy, sell, dividend, withdrawal, transfer between accounts, fee and shares in (shares moved in from elsewhere, e.g. a gift).
- **Loan:** drawdown, capitalized interest, payment (principal, interest and fees) and Kela's credit.
- **Deposits:** each deposit records how much of it is student loan. The rest counts as your own money.
- **Trades:** quantity, price, currency, fees and the EUR amount, as in Nordnet. If the EUR amount is not known yet (the trade only shows on the Trades tab), it is estimated and marked with ≈. The estimate uses the EUR/USD rate at the moment of the trade (found from the stock's 5-minute prices) plus Nordnet's 0.25% exchange fee, which is usually within a few euros. Fill in the exact amount once Nordnet shows it.
- **Gifted shares:** enter the cost per share as the market value on the day you received the gift. In Finnish taxation that value is the acquisition cost of gifted shares.

## Prices

Prices come from [Yahoo Finance](https://finance.yahoo.com) in the background while the app is open:

| What | How often |
|---|---|
| Prices on screen | every second (Yahoo's real-time price stream) |
| Prices if the stream is unavailable | every 60 seconds |
| Yesterday's close and the 1D chart's 5-minute prices | every 60 seconds |
| 1W chart | every 5 minutes, only while 1W is selected |
| Daily history for 1M–All | every 6 hours |
| A failed request | retried after 15 seconds |

- The Live indicator shows the time of the latest streamed price. "Live (60 s)" means the app has fallen back to fetching every minute.
- **Today's change:** shares held since yesterday move from yesterday's close; shares bought today move from their purchase price, and today's trading fees are subtracted, so it is the real gain or loss of the day.
- US exchanges are real time. Other exchanges can be delayed about 15 minutes.
- Click the Live indicator or "Refresh prices" to fetch prices right away.
- Euro values use the live EUR/USD rate.
- Ticker symbols (e.g. NVDA, TSM, SOFF.OL) are found automatically from the security name. You can correct them under Settings.
- The latest prices and the price history are saved in `data/prices.json`, so they are shown even without a connection.

**Why not TradingView?** TradingView has no public API for price data, and its widgets only work on web pages. The app therefore only opens TradingView charts in the browser.

## Saving

Older versions kept the data in `tiedot/`. On first start the app copies it to `data/`; the old folder is left untouched.


Everything is saved automatically after each change to `data/portfolio.json`. The first save of each day copies the previous version to `data/backups` (the last 30 days are kept).

`data/` is in `.gitignore`, so account numbers and amounts never end up in version control.

To open another data file:

```bash
python portfolio_manager.py --data path/to/other.json
```

## Install and run

You need Python 3.8 or newer with Tkinter. Pillow 8.2+ is optional, but without it the graphics are rougher. No other libraries are needed.

```bash
pip install pillow
```

```bash
python portfolio_manager.py
```

On Windows you can also double-click `Start.bat`. Ctrl+N opens a new entry.

### Linux

```bash
sudo apt install python3-tk python3-pil python3-pil.imagetk
```

```bash
./start.sh
```

## Files

| File | Contents |
|---|---|
| `portfolio_manager.py` | the app: top bar, pages and the entry form |
| `accounting.py` | data model, saving and calculations (no UI) |
| `prices.py` | prices and symbol search from Yahoo Finance, TradingView links |
| `widgets.py` | themes, menu, treemap, chart and other UI parts |

## Calculation rules

- **Contributed:** deposits − withdrawals + the cost basis of shares moved in. Transfers between your own accounts do not change it.
- **Student loan / own money:** student loan parts of deposits − student loan parts of withdrawals. The rest is your own money.
- **Cost basis (EUR):** Nordnet's EUR amount, including fees and currency exchange. Sales use the average cost, and the difference is a realized gain or loss.
- **Average price and cost (USD):** what you paid in dollars including fees, with each fee converted at that trade's own exchange rate. This is the same as Nordnet's average price (keskikurssi). The dollar return is the live value minus this cost.
- **Estimate:** a missing EUR amount uses the latest confirmed trade's rate in the same currency (includes the exchange fee), or the fallback rate.
- **Portfolio value:** shares × live price × exchange rate + cash. A security without a price is valued at cost.
- **History:** each day's holdings × that day's close. Weekends use the previous close.
- **Loan balance:** drawdowns + capitalized interest − principal repaid − Kela credit.
- **Net worth:** portfolio value − loan balances.

## Disclaimer

The app is a personal bookkeeping tool, not investment advice. Prices come from an unofficial source, so check amounts and tax details with Nordnet and your bank.
