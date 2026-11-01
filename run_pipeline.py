#!/usr/bin/env python3
"""
Main entry point for running the Finance Daily Pipeline.
Designed for execution on GitHub Actions scheduled runners or local machines.
"""

import os
import sys
import time
from datetime import datetime

from tasks.common import get_services, get_screener_folder_id, load_stocks_parquet
from tasks import Bronze1, Bronze2, Bronze3, screener, zerodha

def main():
    start_time = time.time()
    print(f"==================================================")
    print(f"Starting Finance Pipeline Run at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"==================================================")

    # 1. Authenticate with Google Services
    try:
        drive_service, gc = get_services()
        print("Google Drive & Sheets authenticated successfully.")
    except Exception as e:
        print(f"CRITICAL ERROR: Failed to connect to Google Services: {e}", file=sys.stderr)
        sys.exit(1)

    # 2. Load existing stocks database from Google Sheet / local cache
    stocks_df = load_stocks_parquet(drive_service, gc=gc)

    # 3. Bronze1: Ingest & deduplicate market category stocks
    try:
        t0 = time.time()
        stocks_df = Bronze1.run(df=stocks_df, drive_service=drive_service)
        print(f"Bronze1 completed in {round(time.time() - t0, 2)}s.")
    except Exception as e:
        print(f"ERROR in Bronze1: {e}", file=sys.stderr)

    # 4. Bronze2: Fetch HTML pages for stocks due for updates
    try:
        t0 = time.time()
        Bronze2.run(df=stocks_df)
        print(f"Bronze2 completed in {round(time.time() - t0, 2)}s.")
    except Exception as e:
        print(f"ERROR in Bronze2: {e}", file=sys.stderr)

    # 5. Bronze3: Parse fundamentals, quarters, P&L, margins, FII trends
    try:
        t0 = time.time()
        stocks_df = Bronze3.run(df=stocks_df)
        print(f"Bronze3 completed in {round(time.time() - t0, 2)}s.")
    except Exception as e:
        print(f"ERROR in Bronze3: {e}", file=sys.stderr)

    # 6. Screener: Compute WAGR, PEG, Normal_Score & Publish to Google Sheets
    try:
        t0 = time.time()
        stocks_df = screener.run(
            df=stocks_df,
            drive_service=drive_service,
            gc=gc
        )
        print(f"Screener completed in {round(time.time() - t0, 2)}s.")
    except Exception as e:
        print(f"ERROR in Screener: {e}", file=sys.stderr)

    # 7. Zerodha: Portfolio, contract notes, dividends, capital gains, XIRR
    try:
        t0 = time.time()
        zerodha.run(stocks_df=stocks_df, drive_service=drive_service, gc=gc)
        print(f"Zerodha completed in {round(time.time() - t0, 2)}s.")
    except Exception as e:
        print(f"ERROR in Zerodha: {e}", file=sys.stderr)

    total_duration = round(time.time() - start_time, 2)
    print(f"==================================================")
    print(f"Pipeline finished successfully in {total_duration}s.")
    print(f"==================================================")

if __name__ == '__main__':
    main()
