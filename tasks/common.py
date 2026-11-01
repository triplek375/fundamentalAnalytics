import os
import io
import json
import base64
import tempfile
import pandas as pd
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload, MediaIoBaseUpload
import gspread
from dotenv import load_dotenv

# Load local .env if available
load_dotenv()

HTML_CACHE_DIR = os.environ.get("STOCKS_HTML_DIR", os.path.join(tempfile.gettempdir(), "stocks_html"))
LOCAL_STOCKS_PARQUET = os.environ.get("LOCAL_STOCKS_PARQUET", os.path.join(tempfile.gettempdir(), "stocks.parquet"))
HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/115.0.0.0 Safari/537.36'
}

def parse_service_account_info(raw_str):
    """Robustly parses service account credentials from JSON string, file path, base64, or quoted strings."""
    if not raw_str or not raw_str.strip():
        raise ValueError("GOOGLE_KEY environment variable is empty or not set. Please provide service account JSON.")

    s = raw_str.strip()

    # Strip UTF-8 BOM if present
    s = s.lstrip('\ufeff')

    # Detect unresolved GitHub Actions variable syntax
    if s.startswith("${{") or s.startswith("$"):
        raise ValueError(
            "GOOGLE_KEY contains unresolved GitHub Actions syntax ('${{ ... }}'). "
            "Please ensure the secret is added to GitHub under Settings -> Secrets and variables -> Actions -> Repository secrets."
        )

    # Strip surrounding single or double quotes if whole string was quoted
    if (s.startswith("'") and s.endswith("'")) or (s.startswith('"') and s.endswith('"') and not s.startswith('{"')):
        s = s[1:-1].strip()

    # Check if raw_str is a file path pointing to a credentials JSON file
    if os.path.exists(s):
        with open(s, "r", encoding="utf-8") as f:
            s = f.read().strip()

    # Check if string is base64 encoded JSON
    if not s.startswith("{"):
        try:
            decoded = base64.b64decode(s).decode("utf-8").strip()
            if decoded.startswith("{"):
                s = decoded
        except Exception:
            pass

    try:
        info = json.loads(s)
        if not isinstance(info, dict):
            raise ValueError(f"Parsed JSON is not an object/dictionary, got {type(info).__name__}")
        return info
    except json.JSONDecodeError as e:
        raise ValueError(
            f"Failed to parse GOOGLE_KEY as JSON (length: {len(s)} chars). "
            "Please ensure the repository secret GOOGLE_KEY contains the valid raw JSON text "
            "from your Google Cloud Service Account key file."
        ) from None

def get_google_credentials():
    """Retrieves Google service account credentials from environment variables or file."""
    key_str = os.environ.get("GOOGLE_KEY")
    if not key_str and os.environ.get("GOOGLE_KEY_FILE"):
        with open(os.environ["GOOGLE_KEY_FILE"], "r", encoding="utf-8") as f:
            key_str = f.read()

    if not key_str:
        # Fallback for Databricks environment if dbutils is available
        try:
            from pyspark.dbutils import DBUtils
            from pyspark.sql import SparkSession
            spark = SparkSession.builder.getOrCreate()
            dbutils = DBUtils(spark)
            key_str = dbutils.secrets.get(scope="google", key="GOOGLE_KEY")
        except Exception:
            pass

    info = parse_service_account_info(key_str)
    scopes = [
        'https://www.googleapis.com/auth/spreadsheets',
        'https://www.googleapis.com/auth/drive'
    ]
    return Credentials.from_service_account_info(info, scopes=scopes)

def get_services():
    """Initializes and returns Google Drive and gspread client services."""
    creds = get_google_credentials()
    drive_service = build('drive', 'v3', credentials=creds)
    gc = gspread.authorize(creds)
    return drive_service, gc

def get_folder_id(drive_service, parent_id, folder_name):
    """Finds a folder ID by name within a parent folder."""
    query = f"'{parent_id}' in parents and name = '{folder_name}' and mimeType = 'application/vnd.google-apps.folder' and trashed = false"
    results = drive_service.files().list(
        q=query,
        fields="files(id, name)",
        supportsAllDrives=True,
        includeItemsFromAllDrives=True
    ).execute()
    files = results.get('files', [])
    if not files:
        raise FileNotFoundError(f"Folder '{folder_name}' not found inside parent {parent_id}")
    return files[0]['id']

def get_file_id(drive_service, parent_id, file_name):
    """Finds a file ID by name within a parent folder."""
    query = f"'{parent_id}' in parents and name = '{file_name}' and trashed = false"
    results = drive_service.files().list(
        q=query,
        fields="files(id, name)",
        supportsAllDrives=True,
        includeItemsFromAllDrives=True
    ).execute()
    files = results.get('files', [])
    if not files:
        raise FileNotFoundError(f"File '{file_name}' not found inside parent {parent_id}")
    return files[0]['id']

def list_files_in_folder(drive_service, folder_id):
    """Returns a list of files in a folder."""
    query = f"'{folder_id}' in parents and trashed = false"
    results = drive_service.files().list(
        q=query,
        fields="files(id, name)",
        supportsAllDrives=True,
        includeItemsFromAllDrives=True
    ).execute()
    return results.get('files', [])

def download_file_content(drive_service, file_id):
    """Downloads file content to a BytesIO object."""
    request = drive_service.files().get_media(fileId=file_id)
    fh = io.BytesIO()
    downloader = MediaIoBaseDownload(fh, request)
    done = False
    while done is False:
        status, done = downloader.next_chunk()
    fh.seek(0)
    return fh

def upload_or_update_file(drive_service, parent_id, file_name, content_bytes, mime_type='application/octet-stream'):
    """Uploads a new file or updates an existing file with the given name in a folder."""
    query = f"'{parent_id}' in parents and name = '{file_name}' and trashed = false"
    res = drive_service.files().list(
        q=query,
        fields="files(id, name)",
        supportsAllDrives=True,
        includeItemsFromAllDrives=True
    ).execute()
    files = res.get('files', [])
    media = MediaIoBaseUpload(io.BytesIO(content_bytes), mimetype=mime_type, resumable=True)
    if files:
        file_id = files[0]['id']
        drive_service.files().update(
            fileId=file_id,
            media_body=media,
            supportsAllDrives=True
        ).execute()
        return file_id
    else:
        file_metadata = {'name': file_name, 'parents': [parent_id]}
        created = drive_service.files().create(
            body=file_metadata,
            media_body=media,
            fields='id',
            supportsAllDrives=True
        ).execute()
        return created.get('id')

def get_screener_folder_id(drive_service):
    """Optionally finds the 'Screener' folder in Google Drive if it exists, otherwise returns None."""
    try:
        response = drive_service.files().list(
            q="name = 'Screener' and mimeType = 'application/vnd.google-apps.folder' and trashed = false",
            fields="files(id, name)",
            supportsAllDrives=True,
            includeItemsFromAllDrives=True
        ).execute()
        files = response.get('files', [])
        return files[0]['id'] if files else None
    except Exception:
        return None

def get_finance_folder_id(drive_service):
    """Finds the main 'Finance' root folder in Google Drive."""
    response = drive_service.files().list(
        q="name = 'Finance' and mimeType = 'application/vnd.google-apps.folder' and trashed = false",
        fields="files(id, name)",
        supportsAllDrives=True,
        includeItemsFromAllDrives=True
    ).execute()
    files = response.get('files', [])
    if not files:
        raise FileNotFoundError("Could not find root 'Finance' folder in Google Drive.")
    return files[0]['id']

LOCAL_STOCKS_PARQUET = os.environ.get("LOCAL_STOCKS_PARQUET", os.path.join(tempfile.gettempdir(), "stocks.parquet"))

def load_stocks_parquet(drive_service=None, folder_id=None, force_drive=False, gc=None):
    """Loads stocks DataFrame from local runner disk if available, else from Google Sheet 'All Stocks'."""
    # 1. First check local runner disk cache
    if not force_drive and os.path.exists(LOCAL_STOCKS_PARQUET):
        try:
            df = pd.read_parquet(LOCAL_STOCKS_PARQUET)
            if not df.empty:
                print(f"Loaded stocks cache from local disk ({len(df)} rows).")
                return df
        except Exception as e:
            print(f"Warning: Failed to load local stocks.parquet ({e}), falling back to Google Sheets.")

    # 2. Fallback to Google Sheet 'Screener Tracker' -> 'All Stocks'
    if gc is None and drive_service:
        try:
            _, gc = get_services()
        except Exception:
            pass

    if gc:
        print("Checking Google Sheet 'Screener Tracker' -> 'All Stocks' for persistent stocks...")
        try:
            spreadsheet = gc.open('Screener Tracker')

            try:
                worksheet = spreadsheet.worksheet('All Stocks')
                all_values = worksheet.get_all_values()
                if all_values and len(all_values) > 1:
                    df = pd.DataFrame(all_values[1:], columns=all_values[0])
                    for c in df.columns:
                        if c not in ['Symbol', 'Name', 'URL', 'Industry', 'Reported_Upto', 'Upcoming_Date']:
                            df[c] = pd.to_numeric(df[c], errors='ignore')
                    try:
                        df.to_parquet(LOCAL_STOCKS_PARQUET, index=False)
                    except Exception:
                        pass
                    print(f"Successfully loaded {len(df)} stocks from Google Sheet 'All Stocks'.")
                    return df
            except Exception:
                print("Worksheet 'All Stocks' does not exist yet. Starting with a fresh DataFrame.")
        except Exception as e:
            print(f"Notice: Could not load from Google Sheet: {e}. Starting fresh.")

    return pd.DataFrame()

def save_stocks_parquet(drive_service=None, folder_id=None, df=None, upload_drive=True, gc=None):
    """Caches stocks locally on disk and persists to Google Sheet 'Screener Tracker' -> 'All Stocks'."""
    if df is None or df.empty:
        return

    # 1. Always cache locally on runner disk
    try:
        df.to_parquet(LOCAL_STOCKS_PARQUET, index=False)
        print(f"Cached stocks.parquet ({len(df)} rows) locally.")
    except Exception as e:
        print(f"Warning: Failed to cache stocks.parquet locally: {e}")

    # 2. Persist to Google Sheet 'All Stocks'
    if upload_drive:
        if gc is None and drive_service:
            try:
                _, gc = get_services()
            except Exception:
                pass

        if gc:
            print(f"Syncing {len(df)} stocks to Google Sheet 'Screener Tracker' -> 'All Stocks'...")
            try:
                spreadsheet = gc.open('Screener Tracker')

                try:
                    worksheet = spreadsheet.worksheet('All Stocks')
                except Exception:
                    worksheet = spreadsheet.add_worksheet(title='All Stocks', rows=len(df)+10, cols=len(df.columns)+5)

                worksheet.clear()
                header = df.columns.tolist()
                clean_df = df.fillna('')
                data = [header] + clean_df.values.tolist()
                worksheet.update(data, 'A1')
                print(f"Successfully saved {len(df)} stocks to Google Sheet 'All Stocks'.")
            except Exception as e:
                print(f"Warning: Could not save to Google Sheet 'All Stocks': {e}")
