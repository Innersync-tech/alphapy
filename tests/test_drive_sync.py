"""Tests for Google Drive sync (GOOGLE_CREDENTIALS_JSON only)."""

import json
from unittest.mock import MagicMock, patch

import utils.drive_sync
from utils.drive_sync import _ensure_drive


def _sample_credentials_json() -> str:
    return json.dumps({
        "type": "service_account",
        "project_id": "test-project",
        "private_key_id": "test-key-id",
        "private_key": "-----BEGIN PRIVATE KEY-----\ntest-key\n-----END PRIVATE KEY-----\n",
        "client_email": "test@test-project.iam.gserviceaccount.com",
        "client_id": "123456789",
        "auth_uri": "https://accounts.google.com/o/oauth2/auth",
        "token_uri": "https://oauth2.googleapis.com/token",
    })


class TestDriveSync:
    def setup_method(self) -> None:
        utils.drive_sync.drive = None

    def teardown_method(self) -> None:
        utils.drive_sync.drive = None

    def test_ensure_drive_uses_env_json(self) -> None:
        creds = _sample_credentials_json()
        with patch("utils.drive_sync.config") as mock_config:
            mock_config.GOOGLE_CREDENTIALS_JSON = creds
            with patch("utils.drive_sync.GoogleAuth") as mock_auth:
                mock_gauth = MagicMock()
                mock_auth.return_value = mock_gauth
                with patch("utils.drive_sync.ServiceAccountCredentials") as mock_creds:
                    mock_cred_obj = MagicMock()
                    mock_creds.from_json_keyfile_dict.return_value = mock_cred_obj
                    with patch("utils.drive_sync.GoogleDrive") as mock_drive_class:
                        mock_drive_class.return_value = MagicMock()
                        result = _ensure_drive()
                        assert result is not None
                        mock_creds.from_json_keyfile_dict.assert_called_once()

    def test_no_credentials_returns_none(self) -> None:
        with patch("utils.drive_sync.config") as mock_config:
            mock_config.GOOGLE_CREDENTIALS_JSON = None
            assert _ensure_drive() is None

    def test_invalid_json_returns_none(self) -> None:
        with patch("utils.drive_sync.config") as mock_config:
            mock_config.GOOGLE_CREDENTIALS_JSON = "not-json"
            assert _ensure_drive() is None
