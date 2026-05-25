# Credentials Directory

Store private service credentials locally in this folder (for example `credentials.json` for Google Drive). **Never commit real credential files** — this directory is in `.gitignore`.

## Google Drive (service account)

Alphapy loads the service account from the **`GOOGLE_CREDENTIALS_JSON`** environment variable (Railway or `.env`), not from Secret Manager or from files at runtime.

### Local development

1. Download the service account JSON key from Google Cloud Console.
2. Put the file here if you want (e.g. `credentials/credentials.json`) for your own reference only.
3. Set the env var (one line, minified JSON):

   ```bash
   # In .env
   GOOGLE_CREDENTIALS_JSON={"type":"service_account",...}

   # Or from file
   GOOGLE_CREDENTIALS_JSON=$(cat credentials/credentials.json | jq -c)
   ```

### Production (Railway)

1. In Railway → your Alphapy service → **Variables**, set `GOOGLE_CREDENTIALS_JSON` to the full JSON string (single line).
2. Remove legacy variables if still present: `GOOGLE_PROJECT_ID`, `GOOGLE_SECRET_NAME` (no longer used).

Setup guide: [docs/GOOGLE_CREDENTIALS_SETUP.md](../docs/GOOGLE_CREDENTIALS_SETUP.md).  
Security: [docs/SECURITY.md](../docs/SECURITY.md).

## Security

- Never commit credentials to git.
- Rotate keys in GCP and update `GOOGLE_CREDENTIALS_JSON`, then redeploy.
- If a key was ever committed, rotate it immediately (it may exist in git history).
