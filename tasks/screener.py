import pandas as pd
import numpy as np
from tasks.common import (
    get_services, get_file_id, get_screener_folder_id,
    load_stocks_parquet, save_stocks_parquet
)

def calculate_wagr(values):
    """Calculates Weighted Average Growth Rate across financial periods."""
    valid_vals = [float(v) for v in values if v is not None and not pd.isna(v) and v != 0]
    if len(valid_vals) < 2:
        return None
    total_weighted_growth = 0.0
    total_weight = 0.0
    for i in range(len(valid_vals) - 1):
        growth = (valid_vals[i+1] - valid_vals[i]) / abs(valid_vals[i])
        weight = float((i + 1) ** 2)
        total_weighted_growth += (max(-1.0, min(growth, 1.0)) * weight)
        total_weight += weight
    return float((total_weighted_growth / total_weight) * 100.0) if total_weight > 0 else None

def compute_screener_metrics(df):
    """Computes Normal_Score, WAGR_Sales, WAGR_Profit, and PEG for each stock."""
    df = df.copy()

    all_cols = df.columns.tolist()
    sales_mar_cols = sorted([c for c in all_cols if c.startswith("Sales_Mar")])
    profit_mar_cols = sorted([c for c in all_cols if c.startswith("Profit_Mar")])

    sales_sequence = sales_mar_cols + ([c for c in ["Sales_TTM"] if c in all_cols])
    profit_sequence = profit_mar_cols + ([c for c in ["Profit_TTM"] if c in all_cols])

    # Normal Score: position between 52WH and 52WL
    if '52WH' in df.columns and '52WL' in df.columns and 'LTP' in df.columns:
        wh = pd.to_numeric(df['52WH'], errors='coerce')
        wl = pd.to_numeric(df['52WL'], errors='coerce')
        ltp = pd.to_numeric(df['LTP'], errors='coerce')
        diff = wh - wl
        df['Normal_Score'] = np.where((diff > 0) & wh.notna() & wl.notna() & ltp.notna(), 100.0 * (ltp - wl) / diff, np.nan)
    else:
        df['Normal_Score'] = np.nan

    # WAGR Sales & Profit
    if sales_sequence:
        df['WAGR_Sales'] = df[sales_sequence].apply(lambda row: calculate_wagr(row.values), axis=1)
    else:
        df['WAGR_Sales'] = np.nan

    if profit_sequence:
        df['WAGR_Profit'] = df[profit_sequence].apply(lambda row: calculate_wagr(row.values), axis=1)
    else:
        df['WAGR_Profit'] = np.nan

    # PEG: PE / WAGR_Profit
    if 'PE' in df.columns:
        pe = pd.to_numeric(df['PE'], errors='coerce')
        wagr_p = pd.to_numeric(df['WAGR_Profit'], errors='coerce')
        df['PEG'] = np.where(wagr_p.notna() & (wagr_p != 0) & pe.notna(), pe / wagr_p, np.nan)
    else:
        df['PEG'] = np.nan

    return df, profit_mar_cols

def filter_top_stocks(df, profit_mar_cols):
    """Filters stocks based on fundamental growth and valuation criteria."""
    # Coalesce Profit_TTM with latest Profit_Mar column
    if 'Profit_TTM' in df.columns:
        latest_profit = pd.to_numeric(df['Profit_TTM'], errors='coerce')
        if profit_mar_cols:
            latest_profit = latest_profit.combine_first(pd.to_numeric(df[profit_mar_cols[-1]], errors='coerce'))
    elif profit_mar_cols:
        latest_profit = pd.to_numeric(df[profit_mar_cols[-1]], errors='coerce')
    else:
        latest_profit = pd.Series(np.nan, index=df.index)

    npm = pd.to_numeric(df.get('NPM_TTM', pd.Series(np.nan, index=df.index)), errors='coerce')
    pe = pd.to_numeric(df.get('PE', pd.Series(np.nan, index=df.index)), errors='coerce')
    roce = pd.to_numeric(df.get('ROCE', pd.Series(np.nan, index=df.index)), errors='coerce')
    roe = pd.to_numeric(df.get('ROE', pd.Series(np.nan, index=df.index)), errors='coerce')
    wagr_s = pd.to_numeric(df.get('WAGR_Sales', pd.Series(np.nan, index=df.index)), errors='coerce')
    wagr_p = pd.to_numeric(df.get('WAGR_Profit', pd.Series(np.nan, index=df.index)), errors='coerce')
    peg = pd.to_numeric(df.get('PEG', pd.Series(np.nan, index=df.index)), errors='coerce')

    mask = (
        (npm > 10) &
        (pe < 100) &
        (latest_profit > 500) &
        (roce > 10) &
        (roe > 10) &
        ((wagr_s > 10) | wagr_s.isna()) &
        ((wagr_p > 10) | wagr_p.isna()) &
        ((peg < 2) | peg.isna())
    )

    filtered = df[mask].copy()
    filtered = filtered.sort_values(by='PEG', ascending=True)

    fixed_cols = ["Symbol", "Name", "URL", "PEG", "PE", "WAGR_Profit", "WAGR_Sales", "Normal_Score", "LTP"]
    other_cols = sorted([c for c in filtered.columns if c not in fixed_cols])
    ordered_cols = [c for c in fixed_cols if c in filtered.columns] + other_cols
    filtered = filtered[ordered_cols].fillna('')
    return filtered

def update_google_sheet(gc, filtered_df, sheet_title="Screener Tracker", worksheet_name="Stocks"):
    """Publishes filtered stocks DataFrame to Google Sheets."""
    print(f"Connecting to Google Sheet '{sheet_title}' -> worksheet '{worksheet_name}'...")
    spreadsheet = gc.open(sheet_title)

    try:
        worksheet = spreadsheet.worksheet(worksheet_name)
    except Exception:
        worksheet = spreadsheet.add_worksheet(title=worksheet_name, rows=1000, cols=50)

    worksheet.clear()
    header = filtered_df.columns.values.tolist()
    data = filtered_df.values.tolist()
    worksheet.update([header] + data)
    print(f"Successfully published {len(filtered_df)} candidate stocks to Google Sheet.")

def run(df=None, drive_service=None, gc=None, screener_folder_id=None):
    """Executes Screener task."""
    print("=== Starting Task: Screener (WAGR & Valuation Filtering) ===")
    if df is None:
        df = load_stocks_parquet(drive_service, gc=gc)

    if df is None or df.empty:
        print("Screener: stocks DataFrame is empty. Skipping.")
        return df

    calculated_df, profit_mar_cols = compute_screener_metrics(df)
    filtered_df = filter_top_stocks(calculated_df, profit_mar_cols)
    print(f"Screener: {len(filtered_df)} stocks matched criteria.")

    if gc:
        try:
            update_google_sheet(gc, filtered_df)
        except Exception as e:
            print(f"Error publishing to Google Sheet: {e}")

    save_stocks_parquet(drive_service, df=calculated_df, gc=gc)

    return calculated_df

if __name__ == '__main__':
    drive_service, gc = get_services()
    s_id = get_screener_folder_id(drive_service)
    run(drive_service=drive_service, gc=gc, screener_folder_id=s_id)
