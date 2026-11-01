import csv
from datetime import datetime
from openpyxl import load_workbook
from pyxirr import xirr
import pandas as pd
from tasks.common import (
    get_services, get_folder_id, get_file_id, get_screener_folder_id,
    list_files_in_folder, download_file_content, load_stocks_parquet
)

def build_stocks_lookup(stocks_df):
    """Builds a symbol-to-metrics dictionary for appending company fundamentals."""
    if stocks_df is None or stocks_df.empty:
        return {}, []

    stocks_pdf = stocks_df.fillna('').copy()
    fixed_cols = ["Symbol", "Name", "URL", "PEG", "PE", "WAGR_Profit", "WAGR_Sales", "Normal_Score", "LTP"]
    all_cols = stocks_pdf.columns.tolist()
    other_cols = sorted([c for c in all_cols if c not in fixed_cols])
    ordered_cols = [c for c in fixed_cols if c in all_cols] + other_cols

    if 'Symbol' in stocks_pdf.columns:
        stocks_pdf.set_index('Symbol', inplace=True)
        stocks_lookup = stocks_pdf.to_dict('index')
    else:
        stocks_lookup = {}

    append_cols = [c for c in ordered_cols if c != 'Symbol']
    return stocks_lookup, append_cols

def parse_dividends(drive_service, dividend_folder_id):
    """Parses Zerodha dividend statement Excel files."""
    account = None
    dividend_statement = [['Symbol', 'Date', 'Net Amount', 'Year-Month']]

    for file in list_files_in_folder(drive_service, dividend_folder_id):
        if account is None and '-' in file['name']:
            account = file['name'].split('-')[1]

        file_content = download_file_content(drive_service, file['id'])
        workbook = load_workbook(file_content, data_only=True)
        if 'Equity Dividends' not in workbook.sheetnames:
            continue
        worksheet = workbook['Equity Dividends']
        found_header = False
        for row in worksheet.iter_rows(values_only=True):
            if not found_header:
                if 'Symbol' in row:
                    found_header = True
                continue
            if not row or len(row) < 7:
                continue
            if row[1] == 'Total Dividend Amount':
                break
            if row[1] is None or row[3] is None:
                continue

            symbol_cleaned = str(row[1]).replace('#', '').replace('6', '')
            date_str = str(row[3])
            net_amt = row[6]
            dividend_statement.append([
                symbol_cleaned,
                date_str,
                net_amt,
                date_str[:7]
            ])

    dividend_statement[1:] = sorted(
        dividend_statement[1:],
        key=lambda r: datetime.strptime(str(r[1]), "%Y-%m-%d") if str(r[1]) != 'Date' else datetime.min,
        reverse=True
    )
    return dividend_statement, account

def parse_contract_notes(drive_service, contract_folder_id):
    """Parses Zerodha trade contract note Excel files."""
    contract_note = [['Symbol', 'Date', 'Net Amount', 'Year-Month']]

    for file in list_files_in_folder(drive_service, contract_folder_id):
        file_content = download_file_content(drive_service, file['id'])
        workbook = load_workbook(file_content, data_only=True)
        for worksheet in workbook.worksheets:
            header_found = False
            skip_next_row = False
            try:
                sheet_date = datetime.strptime(worksheet.title, "%d-%m-%Y").strftime("%Y-%m-%d")
            except ValueError:
                continue

            for row in worksheet.iter_rows(values_only=True):
                if not header_found:
                    if row and len(row) > 0 and row[0] == 'Order No.':
                        header_found = True
                        skip_next_row = True
                    continue
                if skip_next_row:
                    skip_next_row = False
                    continue
                if not row or not row[0] or row[0] == 'PAY IN / PAY OUT OBLIGATION':
                    break
                if len(row) < 13:
                    continue

                symbol = str(row[4]).split('-')[0].strip() if row[4] else ''
                action = str(row[5]).strip().lower() if row[5] else ''
                net_val = row[11] if action == 'buy' else row[12]

                contract_note.append([
                    symbol,
                    sheet_date,
                    net_val,
                    sheet_date[:7]
                ])

    contract_note[1:] = sorted(
        contract_note[1:],
        key=lambda r: datetime.strptime(str(r[1]), "%Y-%m-%d") if str(r[1]) != 'Date' else datetime.min,
        reverse=True
    )
    return contract_note

def parse_holdings(drive_service, zerodha_folder_id):
    """Parses holdings.csv from Zerodha Google Drive folder."""
    holdings = [['Symbol', 'Quantity', 'Invested', 'Current']]
    holdings_file_id = get_file_id(drive_service, zerodha_folder_id, 'holdings.csv')
    csv_bytes = download_file_content(drive_service, holdings_file_id).read().decode('utf-8')
    csv_reader = csv.reader(csv_bytes.splitlines())
    header = next(csv_reader, None)

    for parts in csv_reader:
        if len(parts) >= 6:
            holdings.append([
                parts[0].replace('"', '').strip(),
                parts[1].strip(),
                parts[4].strip(),
                parts[5].strip()
            ])
    return holdings

def calculate_capital_gains(contract_note, holdings, stocks_lookup, append_cols):
    """Computes realised and unrealised capital gains with XIRR."""
    capital_gains = [['Symbol', 'Date', 'Invested', 'Unrealised', 'Realised', 'XIRR']]
    symbol_to_contracts = {}
    symbol_to_holdings = {}

    for contract in contract_note[1:]:
        symbol = contract[0]
        if symbol not in symbol_to_contracts:
            symbol_to_contracts[symbol] = []
        symbol_to_contracts[symbol].append(contract)

    for holding in holdings[1:]:
        symbol_to_holdings[holding[0]] = holding

    for symbol, contracts in symbol_to_contracts.items():
        holdings_invested = 0.0
        holdings_current = 0.0
        contracts_invested_held = 0.0
        contracts_invested_sold = 0.0
        contracts_sold = 0.0
        dates = []
        flows = []

        if symbol in symbol_to_holdings:
            try:
                holdings_invested = float(symbol_to_holdings[symbol][2])
                holdings_current = float(symbol_to_holdings[symbol][3])
            except (ValueError, TypeError):
                pass

        for c in contracts:
            try:
                amt = float(c[2])
                d_obj = datetime.strptime(c[1], "%Y-%m-%d").date()
            except (ValueError, TypeError):
                continue

            if amt < 0:
                if contracts_invested_held >= holdings_invested:
                    dates.append(d_obj)
                    flows.append(amt)
                    contracts_invested_sold += amt
                else:
                    contracts_invested_held += abs(amt)
                    unrealised = (abs(amt) / holdings_invested) * holdings_current if holdings_invested > 0 else 0.0
                    try:
                        xirr_val = xirr([d_obj, datetime.today().date()], [amt, unrealised])
                        xirr_per = round(xirr_val * 100.0, 2) if xirr_val is not None else None
                    except Exception:
                        xirr_per = None
                    capital_gains.append([symbol, c[1], amt, unrealised + amt, 0, xirr_per])
            else:
                dates.append(d_obj)
                flows.append(amt)
                contracts_sold += amt

        if contracts_sold > 0 and len(dates) > 0 and len(flows) > 0:
            realised = contracts_sold + contracts_invested_sold
            try:
                xirr_val = xirr(dates, flows)
                xirr_per = round(xirr_val * 100.0, 2) if xirr_val is not None else None
            except Exception:
                xirr_per = None
            capital_gains.append([symbol, None, contracts_invested_sold, 0, realised, xirr_per])

    ltcg_date = datetime.now().replace(year=datetime.now().year - 1).strftime("%Y-%m-%d")
    capital_gains.append(['LTCG', ltcg_date, None, None, None, None])

    capital_gains[1:] = sorted(
        capital_gains[1:],
        key=lambda r: (r[1] is not None, datetime.strptime(str(r[1]), "%Y-%m-%d") if r[1] else datetime.min),
        reverse=True
    )

    capital_gains[0].extend(append_cols)
    for row in capital_gains[1:]:
        sym_data = stocks_lookup.get(row[0], {})
        row.extend([sym_data.get(col, '') for col in append_cols])

    return capital_gains

def run(stocks_df=None, drive_service=None, gc=None):
    """Executes Zerodha portfolio and capital gains calculation."""
    print("=== Starting Task: Zerodha (Portfolio & Capital Gains Tracking) ===")
    if drive_service is None or gc is None:
        drive_service, gc = get_services()

    # Discover all Finance folders shared with or accessible to the service account
    users = []
    page_token = None
    folder_count = 0
    while True:
        response = drive_service.files().list(
            q="name = 'Finance' and mimeType = 'application/vnd.google-apps.folder' and trashed = false",
            fields="nextPageToken, files(id, name)",
            pageSize=100,
            pageToken=page_token,
            supportsAllDrives=True,
            includeItemsFromAllDrives=True
        ).execute()

        for finance_folder in response.get('files', []):
            folder_count += 1
            f_id = finance_folder['id']
            try:
                zerodha_id = get_folder_id(drive_service, f_id, 'Zerodha')
                personal = get_file_id(drive_service, f_id, 'Personal Finance')
                dividend_id = get_folder_id(drive_service, zerodha_id, 'Dividend Statement')
                contract_id = get_folder_id(drive_service, zerodha_id, 'Contract Note')
                users.append({
                    'user_id': f"User {len(users) + 1}",
                    'finance': f_id,
                    'zerodha': zerodha_id,
                    'dividend': dividend_id,
                    'contract': contract_id,
                    'personal': personal
                })
            except Exception as e:
                print(f"Skipping unconfigured folder (index {folder_count}): {e}")

        page_token = response.get('nextPageToken')
        if not page_token:
            break

    if not users:
        print("No valid Zerodha user folders found in Google Drive.")
        return

    print(f"Found {len(users)} valid 'Finance' user folder(s).")

    # Prepare stock fundamental lookup
    if stocks_df is None:
        try:
            stocks_df = load_stocks_parquet(drive_service, gc=gc)
        except Exception as e:
            print(f"Notice: Could not load stocks from Screener Tracker: {e}")

    stocks_lookup, append_cols = build_stocks_lookup(stocks_df)

    for idx, user in enumerate(users, start=1):
        user_tag = user['user_id']
        print(f"\n--- [{idx}/{len(users)}] Processing Zerodha portfolio for: {user_tag} ---")
        try:
            dividend_statement, _ = parse_dividends(drive_service, user['dividend'])
            print(f"[{user_tag}] Parsed {len(dividend_statement)-1} dividends.")

            contract_note = parse_contract_notes(drive_service, user['contract'])
            print(f"[{user_tag}] Parsed {len(contract_note)-1} trade contracts.")

            holdings = parse_holdings(drive_service, user['zerodha'])
            print(f"[{user_tag}] Parsed {len(holdings)-1} current holdings.")

            capital_gains = calculate_capital_gains(contract_note, holdings, stocks_lookup, append_cols)
            print(f"[{user_tag}] Calculated capital gains ({len(capital_gains)-1} rows).")

            doc = gc.open_by_key(user['personal'])
            sheet_to_data = {
                'Dividend Statement': dividend_statement,
                'Contract Note': contract_note,
                'Holdings': holdings,
                'Capital Gains': capital_gains
            }
            for sheet_name, data in sheet_to_data.items():
                try:
                    worksheet = doc.worksheet(sheet_name)
                except Exception:
                    worksheet = doc.add_worksheet(title=sheet_name, rows=1000, cols=50)
                worksheet.clear()
                worksheet.update(data, 'A1')
                print(f"[{user_tag}] Updated worksheet '{sheet_name}'.")
        except Exception as e:
            print(f"ERROR: Failed to process portfolio for {user_tag}: {e}")

    print("Zerodha task finished successfully.")

if __name__ == '__main__':
    run()
