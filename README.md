# Finance Pipeline & Portfolio Tracker

An automated serverless data pipeline and investment tracker running daily on **GitHub Actions**.

The pipeline scrapes fundamental financial data for Indian equities from [Screener.in](https://www.screener.in), calculates custom growth and valuation metrics (WAGR, PEG, Normal Score), ingests trade contract notes and dividend statements from **Zerodha**, computes realized/unrealized capital gains with annualized return (**XIRR**), and syncs reporting to **Google Sheets**.

Persistent state (`stocks.parquet`) is stored automatically in your Google Drive alongside your financial spreadsheets—requiring **zero external database or cloud compute costs**.

---

## Architecture Overview

```
                      ┌──────────────────────┐
                      │    Screener.in       │
                      └──────────┬───────────┘
                                 │
                 ┌───────────────┴───────────────┐
                 ▼                               ▼
          ┌─────────────┐                 ┌─────────────┐
          │   Bronze1   │                 │   Bronze2   │
          │ (Scrape     │                 │ (Fetch HTML │
          │  Companies) │                 │  Pages)     │
          └──────┬──────┘                 └──────┬──────┘
                 │                               │
                 │      stocks.parquet           ▼
                 └───►  (Google Drive)  ◄──┌─────────────┐
                                           │   Bronze3   │
                                           │ (Parse      │
                                           │  Metrics)   │
                                           └─────────────┘
                                                 │
                                                 ▼
                                          ┌─────────────┐
                                          │   screener  │
                                          │ (WAGR, PEG  │
                                          │  & Scores)  │
                                          └──────┬──────┘
                                                 │
   ┌───────────────────────┐                     ▼
   │  Google Drive Files   │             ┌───────────────┐
   │ (Zerodha Trades,      ├────────────►│    zerodha    │
   │  Dividends, Holdings) │             │ (XIRR & Gains)│
   └───────────────────────┘             └───────┬───────┘
                                                 │
                                                 ▼
                                        ┌─────────────────┐
                                        │  Google Sheets  │
                                        │  Dashboards     │
                                        └─────────────────┘
```

### Pipeline Tasks
1. **Bronze1 (`tasks/Bronze1.py`)**: Crawls market categories on Screener.in, extracts symbols, LTP, and P/E, deduplicates symbols, and merges them into the `stocks.parquet` database.
2. **Bronze2 (`tasks/Bronze2.py`)**: Identifies stocks due for fundamental updates (upcoming reporting dates or monthly refresh) and downloads company HTML pages into a temporary cache.
3. **Bronze3 (`tasks/Bronze3.py`)**: Parses fundamentals (Market Cap, P/E, 52W High/Low, ROCE, ROE), quarterly results, historical Profit & Loss, Net Profit Margins (NPM), and Foreign Institutional Investor (FII) holding changes.
4. **Screener (`tasks/screener.py`)**: Calculates Weighted Average Growth Rates (`WAGR_Sales`, `WAGR_Profit`), Price/Earnings to Growth (`PEG`), and Normalized Price Range scores. Filters top candidates and updates Google Sheets (**Screener Tracker** -> `Stocks`).
5. **Zerodha (`tasks/zerodha.py`)**: Ingests trade contract notes, dividend statements, and `holdings.csv` from Google Drive, calculates realized/unrealized capital gains with annualized return (`XIRR`), and updates the **Personal Finance** Google Sheet.

---

## Google Drive Setup & Permissions

Share the following with your Google Cloud Service Account email:

```
My Drive/
├── Screener Tracker                        (Google Sheet - shared directly with Service Account)
│   ├── Worksheet: Stocks                   (Filtered top investment candidates)
│   └── Worksheet: All Stocks               (Full 5,000+ stock database - auto-created)
└── Finance/                                (Folder shared with Service Account - can be multiple users)
    ├── Zerodha/
    │   ├── Dividend Statement/             (Zerodha dividend Excel files)
    │   ├── Contract Note/                  (Zerodha trade contract note Excel files)
    │   └── holdings.csv                    (Zerodha holdings export)
    └── Personal Finance                    (Google Sheet for portfolio tracking)
```

---

## GitHub Actions Setup

1. Open your repository on GitHub.
2. Navigate to **Settings** > **Secrets and variables** > **Actions**.
3. Click **New repository secret**.
4. Set the secret:
   - **Name**: `GOOGLE_KEY`
   - **Value**: Paste the full JSON content of your Google Cloud Service Account key file.
5. The pipeline is scheduled to run automatically every day at **05:00 AM IST** (`23:30 UTC`).
6. You can also trigger it manually at any time by going to the **Actions** tab > **Finance Daily Pipeline** > **Run workflow**.

---

## Running Locally

1. **Clone the repository**:
   ```bash
   git clone https://github.com/triplek375/Finance.git
   cd Finance
   ```

2. **Create a virtual environment and install dependencies**:
   ```bash
   python3 -m venv .venv
   source .venv/bin/activate
   pip install -r tasks/requirements.txt
   ```

3. **Configure your credentials**:
   Create a `.env` file in the root directory:
   ```bash
   GOOGLE_KEY='{"type": "service_account", "project_id": "...", ...}'
   ```
   *(Or set `GOOGLE_KEY_FILE=path/to/service_account.json`)*.

4. **Run the pipeline**:
   ```bash
   python run_pipeline.py
   ```

---

## License

This project is licensed under the [MIT License](LICENSE).
