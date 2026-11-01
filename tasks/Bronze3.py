import os
import glob
from bs4 import BeautifulSoup
import pandas as pd
from tasks.common import HTML_CACHE_DIR, get_services, get_screener_folder_id, load_stocks_parquet, save_stocks_parquet

def safe_float(val):
    """Safely converts string numbers with commas/percentages to float."""
    if val is None:
        return None
    val_clean = str(val).strip().replace(',', '').replace('%', '')
    if not val_clean:
        return None
    try:
        return float(val_clean)
    except ValueError:
        return None

def extract_fundamentals(soup, data):
    for ele in soup.find_all('span', {'class': 'name'}):
        name_text = ele.text.strip()
        if name_text == 'Market Cap':
            num_span = ele.find_next('span', {'class': 'number'})
            data['Market_Cap'] = safe_float(num_span.text) if num_span else None
        elif name_text == 'High / Low':
            nums = ele.find_all_next('span', {'class': 'number'})
            data['52WH'] = safe_float(nums[0].text) if len(nums) > 0 else None
            data['52WL'] = safe_float(nums[1].text) if len(nums) > 1 else None
        elif name_text == 'ROCE':
            num_span = ele.find_next('span', {'class': 'number'})
            data['ROCE'] = safe_float(num_span.text) if num_span else None
        elif name_text == 'ROE':
            num_span = ele.find_next('span', {'class': 'number'})
            data['ROE'] = safe_float(num_span.text) if num_span else None

def extract_quarterly_results(soup, data):
    section = soup.find('section', {'id': 'quarters'})
    if section:
        table = section.find('table', {'class': 'data-table responsive-text-nowrap'})
        if table:
            thead = table.find('thead')
            if thead:
                ths = thead.find_all('th')
                if ths:
                    data['Reported_Upto'] = ths[-1].text.strip()
        badge = section.find('span', {'class': 'badge'})
        if badge:
            strong = badge.find('strong')
            if strong:
                data['Upcoming_Date'] = strong.text.strip()

def extract_profit_loss(soup, data):
    section = soup.find('section', {'id': 'profit-loss'})
    if not section:
        return

    table = section.find('table', {'class': 'data-table responsive-text-nowrap'})
    if not table:
        return

    header_row = table.find('thead').find('tr') if table.find('thead') else None
    headers = []
    if header_row:
        headers = [th.text.strip().replace('\n', '').replace('\t', '').replace(' ', '') for th in header_row.find_all('th')[1:]]
        if not data.get('Reported_Upto') and headers:
            data['Reported_Upto'] = headers[-1]

    tbody = table.find('tbody')
    body_rows = tbody.find_all('tr') if tbody else []
    for row in body_rows:
        button = row.find('button')
        if button and 'onclick' in button.attrs:
            txt = button['onclick'].strip()
            row_type = txt[txt.find("('")+2:txt.find("',")]
        else:
            first_td = row.find('td')
            row_type = first_td.text.strip() if first_td else None

        prefix = None
        if row_type in ['Sales', 'Revenue']:
            prefix = 'Sales'
        elif row_type == 'Net Profit':
            prefix = 'Profit'

        if prefix:
            data_cols = row.find_all('td')[1:]
            for i, col_node in enumerate(data_cols):
                if i < len(headers):
                    key = headers[i]
                    val = safe_float(col_node.text)
                    if (len(key) == 7 and key[:3] == 'Mar') or key == 'TTM':
                        if val is not None:
                            data[f"{prefix}_{key}"] = val

def extract_margin_data(data):
    latest_period = None
    if data.get('Sales_TTM') is not None and data.get('Profit_TTM') is not None:
        latest_period = 'TTM'
    else:
        mar_periods = []
        for key in list(data.keys()):
            if key.startswith('Sales_Mar'):
                period = key.split('_')[1]
                year_str = period[3:]
                if year_str.isdigit() and f'Profit_{period}' in data:
                    mar_periods.append((int(year_str), period))
        if mar_periods:
            mar_periods.sort(reverse=True)
            latest_period = mar_periods[0][1]

    if latest_period:
        sales = data.get(f'Sales_{latest_period}')
        profit = data.get(f'Profit_{latest_period}')
        if sales and sales != 0 and profit is not None:
            data['NPM_TTM'] = round((profit / sales), 2) * 100

def extract_fii_data(soup, data):
    section = soup.find('section', {'id': 'shareholding'})
    if not section:
        return
    tbl = section.find('table', {'class': 'data-table'})
    if not tbl:
        return

    for row in tbl.find_all('tr'):
        button = row.find('button')
        if button and 'onclick' in button.attrs:
            txt = button['onclick'].strip()
            row_type = txt[txt.find("('")+2:txt.find("',")]
        else:
            first_td = row.find('td')
            row_type = first_td.text.strip() if first_td else None

        data_vals = [safe_float(c.text) for c in row.find_all('td')[1:] if c.text.strip()]
        data_vals = [v for v in data_vals if v is not None]

        if row_type == 'foreign_institutions' and len(data_vals) > 7:
            fii_ttm = round(sum(data_vals[-4:]) / 4, 2)
            fii_pttm = round(sum(data_vals[-8:-4]) / 4, 2)
            data['FII_TTM_PTTM'] = round(fii_ttm - fii_pttm, 2)

def parse_html_files(html_dir=HTML_CACHE_DIR):
    """Parses all .html files found in the cache directory."""
    html_files = glob.glob(os.path.join(html_dir, "*.html"))
    print(f"Parsing {len(html_files)} HTML files from {html_dir}...")

    parsed_records = []
    for filepath in html_files:
        symbol = os.path.splitext(os.path.basename(filepath))[0]
        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                soup = BeautifulSoup(f.read(), 'html.parser')

            record = {'Symbol': symbol}
            extract_fundamentals(soup, record)
            extract_quarterly_results(soup, record)
            extract_profit_loss(soup, record)
            extract_margin_data(record)
            extract_fii_data(soup, record)

            parsed_records.append(record)
        except Exception as e:
            print(f"Error parsing {filepath}: {e}")

    return pd.DataFrame(parsed_records)

def run(df=None, drive_service=None, screener_folder_id=None, html_dir=HTML_CACHE_DIR):
    """Executes Bronze3 task."""
    print("=== Starting Task: Bronze3 (HTML Parsing & Fundamentals Update) ===")
    if df is None:
        df = load_stocks_parquet(drive_service)

    parsed_df = parse_html_files(html_dir)
    if parsed_df.empty:
        print("Bronze3: No new HTML parsed. Retaining existing DataFrame.")
        return df if df is not None else pd.DataFrame()

    if df is None or df.empty:
        updated_df = parsed_df
    else:
        # Merge parsed metrics onto existing df by Symbol
        df_indexed = df.set_index('Symbol')
        parsed_indexed = parsed_df.set_index('Symbol')

        # Combine: update existing columns with parsed values, add any new columns
        for col in parsed_indexed.columns:
            if col in df_indexed.columns:
                df_indexed[col] = parsed_indexed[col].combine_first(df_indexed[col])
            else:
                df_indexed[col] = parsed_indexed[col]

        updated_df = parsed_indexed.combine_first(df_indexed).reset_index()

    print(f"Bronze3 finished. Total stocks in registry: {len(updated_df)}")
    return updated_df

if __name__ == '__main__':
    drive_service, gc = get_services()
    stocks_df = run(drive_service=drive_service)
    save_stocks_parquet(drive_service, df=stocks_df, gc=gc)
