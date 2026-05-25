import io
import json
import logging
import threading

import fitz  # PyMuPDF
from oauth2client.service_account import ServiceAccountCredentials
from pydrive2.auth import GoogleAuth
from pydrive2.drive import GoogleDrive

import config

logger = logging.getLogger("utils.drive_sync")

SCOPES = ['https://www.googleapis.com/auth/drive.readonly']

drive = None
_drive_lock = threading.Lock()


def _ensure_drive():
    """
    Initialize Google Drive client from GOOGLE_CREDENTIALS_JSON (Railway env / .env).
    Thread-safe: a lock prevents concurrent initialization when called from multiple threads
    (e.g. via asyncio.to_thread from concurrent requests).
    """
    global drive
    if drive is not None:
        return drive

    with _drive_lock:
        if drive is not None:
            return drive

        credentials_json = config.GOOGLE_CREDENTIALS_JSON
        if not credentials_json:
            logger.debug("GOOGLE_CREDENTIALS_JSON not set; Drive features disabled")
            return None

        logger.info("Loading Google Service Account credentials from GOOGLE_CREDENTIALS_JSON")

        try:
            gauth = GoogleAuth()
            creds = ServiceAccountCredentials.from_json_keyfile_dict(
                json.loads(credentials_json),
                SCOPES,  # type: ignore[arg-type]  # oauth2client accepts list[str] at runtime
            )
            gauth.credentials = creds
            drive = GoogleDrive(gauth)
            logger.info("Google Drive service account authentication successful")
            return drive
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse Google credentials JSON: {e}")
            return None
        except Exception as e:
            logger.error(f"Failed to initialize Google Drive client: {e}", exc_info=True)
            return None


def _sanitize_drive_query_keyword(keyword: str) -> str:
    """
    Sanitizes a keyword for use in Google Drive API query strings.

    Escapes backslashes and quotes to prevent query injection and manipulation.
    Backslashes must be escaped first (doubled) before escaping quotes, otherwise
    a trailing backslash would escape the closing quote in the query string.

    Args:
        keyword: User-provided keyword that may contain special characters

    Returns:
        Sanitized keyword safe for use in Drive API queries
    """
    if not keyword:
        return ""
    sanitized = keyword.replace("\\", "\\\\")
    sanitized = sanitized.replace("'", "\\'")
    sanitized = sanitized.replace('"', '\\"')
    return sanitized


def fetch_pdf_text_by_name(filename_keyword: str) -> str:
    """
    Find a PDF in Google Drive by filename keyword, download it, and return extracted text.

    Args:
        filename_keyword: Search keyword (sanitized before querying Drive)
    """
    gd = _ensure_drive()
    if gd is None:
        return "[Drive not configured: set GOOGLE_CREDENTIALS_JSON]"

    sanitized_keyword = _sanitize_drive_query_keyword(filename_keyword)
    query = f"title contains '{sanitized_keyword}' and mimeType = 'application/pdf' and trashed = false"
    logger.info(f"Searching Google Drive for: {filename_keyword}")
    file_list = gd.ListFile({'q': query}).GetList()

    if not file_list:
        logger.warning(f"No matching PDF found for keyword: {filename_keyword}")
        return "[No matching file found in Drive]"

    file = file_list[0]
    logger.info(f"Found PDF in Drive: {file['title']}")
    downloaded = file.GetContentIOBuffer()
    return extract_text_from_pdf(downloaded)


def extract_text_from_pdf(file_buffer: io.BytesIO) -> str:
    """Extracts text from a PDF file buffer using PyMuPDF."""
    text = ""
    try:
        with fitz.open(stream=file_buffer, filetype="pdf") as doc:
            for page in doc:
                text += page.get_text()  # type: ignore[attr-defined]
        return text.strip()
    except Exception as e:
        logger.error("Error extracting PDF text: %s", e, exc_info=True)
        return "[Error extracting PDF text]"
