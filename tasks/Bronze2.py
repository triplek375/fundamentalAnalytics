import os
import time
from datetime import datetime
import requests
from bs4 import BeautifulSoup
import pandas as pd
from tasks.common import HEADERS, HTML_CACHE_DIR, get_services, get_screener_folder_id, load_stocks_parquet

def parse_upcoming_date(date_val):
    """Attempts to parse Screener's upcoming results date string."""
    if pd.isna(date_val) or not date_val:
        return None
    val_str = str(date_val).strip()
    if val_str.lower() in ("today", "tomorrow"):
        return datetime.now().date()
    for fmt in ("%d %B %Y", "%d %b %Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(val_str, fmt).date()
        except ValueError:
            pass
    return None

def get_stocks_to_fetch(df):
    """Identifies stocks needing HTML download based on date and missing fundamentals."""
    if df is None or df.empty:
        return []

    is_day_1 = (datetime.now().day == 1)
    today = datetime.now().date()

    candidates = []
    for _, row in df.iterrows():
        symbol = row.get('Symbol')
        url = row.get('URL')
        if not symbol or not url:
            continue

        if is_day_1:
            candidates.append({'Symbol': symbol, 'URL': url})
            continue

        upcoming_raw = str(row.get('Upcoming_Date', '')).strip().lower()
        if upcoming_raw in ("today", "tomorrow"):
            candidates.append({'Symbol': symbol, 'URL': url})
            continue

        parsed = parse_upcoming_date(row.get('Upcoming_Date'))
        if parsed and parsed <= today:
            candidates.append({'Symbol': symbol, 'URL': url})
            continue

        # If stock has never been parsed before (missing Reported_Upto)
        if pd.isna(row.get('Reported_Upto')) or not row.get('Reported_Upto'):
            candidates.append({'Symbol': symbol, 'URL': url})

    return candidates

def download_stock_html(stocks_list, output_dir=HTML_CACHE_DIR):
    """Downloads Screener.in company HTML pages to local cache directory."""
    os.makedirs(output_dir, exist_ok=True)
    total = len(stocks_list)
    print(f"Downloading HTML for {total} stocks into {output_dir}...")

    downloaded = []
    for index, stock in enumerate(stocks_list, start=1):
        symbol = stock['Symbol']
        url = stock['URL']
        file_path = os.path.join(output_dir, f"{symbol}.html")

        print(f"({index}/{total}) Fetching {symbol} -> {url}")
        try:
            res = requests.get(url, headers=HEADERS, timeout=20)
            if res.status_code == 200:
                with open(file_path, 'w', encoding='utf-8') as f:
                    f.write(res.text)
                downloaded.append(symbol)
            else:
                print(f"Failed to fetch {symbol}: HTTP {res.status_code}")
        except Exception as e:
            print(f"Error fetching {symbol}: {e}")

        time.sleep(0.5)

    print(f"Completed downloading {len(downloaded)} HTML files.")
    return downloaded

def run(df=None, drive_service=None, screener_folder_id=None):
    """Executes Bronze2 task."""
    print("=== Starting Task: Bronze2 (HTML Company Page Ingestion) ===")
    if df is None:
        df = load_stocks_parquet(drive_service)

    if df is None or df.empty:
        print("Bronze2: stocks registry is empty, nothing to download.")
        return []

    stocks_to_fetch = get_stocks_to_fetch(df)
    print(f"Bronze2: {len(stocks_to_fetch)} stocks queued for download.")
    downloaded_symbols = download_stock_html(stocks_to_fetch)
    return downloaded_symbols

if __name__ == '__main__':
    drive_service, gc = get_services()
    run(drive_service=drive_service)
