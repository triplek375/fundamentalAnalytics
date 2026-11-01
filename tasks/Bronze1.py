import time
import requests
from bs4 import BeautifulSoup
import pandas as pd
from tasks.common import HEADERS, get_services, get_screener_folder_id, load_stocks_parquet, save_stocks_parquet

def scrape_screener_market():
    """Scrapes market industry pages from Screener.in and returns a deduplicated DataFrame of symbols."""
    base_url = 'https://www.screener.in'
    stocks_data = []

    print("Fetching market categories from Screener.in...")
    res = requests.get(base_url + '/market', headers=HEADERS, timeout=20)
    market = BeautifulSoup(res.text, 'html.parser')

    industry_links = [
        ele for ele in market.find_all('a', href=lambda href: href and href.startswith('/market/'))
        if ele.text.strip() and ele.text.strip() != 'None'
    ]

    for ind_idx, industry_ele in enumerate(industry_links, start=1):
        industry_name = industry_ele.text.strip()
        industry_url = base_url + industry_ele['href']
        print(f"[{ind_idx}/{len(industry_links)}] Scraping industry: {industry_name}")

        try:
            first_page_res = requests.get(industry_url, headers=HEADERS, timeout=20)
            first_page = BeautifulSoup(first_page_res.text, 'html.parser')
            page_info = first_page.find('div', {'class': 'sub', 'data-page-info': True})
            if page_info:
                total_pages = int(page_info.text.strip()[-1])
            else:
                total_pages = 1
        except Exception as e:
            print(f"Error reading first page of {industry_name}: {e}")
            total_pages = 1

        for p in range(1, total_pages + 1):
            time.sleep(0.5)
            page_url = f"{industry_url}?page={p}"
            try:
                page_res = requests.get(page_url, headers=HEADERS, timeout=20)
                current_page = BeautifulSoup(page_res.text, 'html.parser')
                for stock_ele in current_page.find_all('a', href=lambda href: href and href.startswith('/company/')):
                    stock_url = base_url + stock_ele['href']
                    parts = stock_url.split('/')
                    symbol = parts[4] if len(parts) > 4 else stock_ele.text.strip()
                    name = stock_ele.text.strip()

                    next_td = stock_ele.find_next('td')
                    ltp = float(next_td.text.replace(',', '')) if next_td and next_td.text.strip() else 0.0

                    pe_td = next_td.find_next('td') if next_td else None
                    pe_text = pe_td.text.strip().replace(',', '') if pe_td else ''
                    pe = float(pe_text) if pe_text else 0.0

                    stocks_data.append({
                        'Symbol': symbol,
                        'Name': name,
                        'LTP': ltp,
                        'PE': pe,
                        'URL': stock_url,
                        'Industry': industry_name
                    })
            except Exception as e:
                print(f"Error scraping {page_url}: {e}")

    if not stocks_data:
        print("No stock data extracted.")
        return pd.DataFrame()

    new_df = pd.DataFrame(stocks_data)

    # Deduplicate: give preference to non-consolidated over consolidated URL (matches PySpark orderBy order)
    new_df['is_consolidated'] = new_df['URL'].str.contains('consolidated', na=False).astype(int)
    new_df = new_df.sort_values(by=['Symbol', 'is_consolidated'], ascending=[True, True])
    new_df = new_df.drop_duplicates(subset=['Symbol'], keep='first').drop(columns=['is_consolidated'])
    print(f"Extracted {len(new_df)} unique stocks from Screener.in market categories.")
    return new_df

def merge_bronze1(existing_df, new_df):
    """Merges new scraped market symbols with the existing stocks DataFrame."""
    if existing_df.empty:
        return new_df

    # Set Symbol as index for efficient update
    merged = existing_df.copy()
    if 'Symbol' in merged.columns:
        merged.set_index('Symbol', inplace=True)

    new_indexed = new_df.set_index('Symbol')

    # Update columns present in new_df
    for col in new_indexed.columns:
        merged[col] = new_indexed[col].combine_first(merged[col]) if col in merged.columns else new_indexed[col]

    # Combine index to include any completely new symbols
    merged = new_indexed.combine_first(merged).reset_index()
    return merged

def run(df=None, drive_service=None, screener_folder_id=None):
    """Executes Bronze1 task."""
    print("=== Starting Task: Bronze1 (Market Stocks Ingestion) ===")
    if df is None:
        df = load_stocks_parquet(drive_service)

    new_df = scrape_screener_market()
    if new_df.empty:
        print("Bronze1 found no new data; keeping existing DataFrame.")
        return df if df is not None else pd.DataFrame()

    if df is None or df.empty:
        updated_df = new_df
    else:
        updated_df = merge_bronze1(df, new_df)

    print(f"Bronze1 finished. Total stocks in registry: {len(updated_df)}")
    return updated_df

if __name__ == '__main__':
    drive_service, gc = get_services()
    stocks_df = run(drive_service=drive_service)
    save_stocks_parquet(drive_service, df=stocks_df, gc=gc)
