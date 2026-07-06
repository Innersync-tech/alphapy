---
title: Google Cloud Credentials Setup
description: Create Google Cloud service account credentials for Alphapy.
---

# Google Cloud Credentials Setup Guide

Step-by-step guide for creating Google Cloud service account credentials for Alphapy.

## Overview

You need:
1. A Google Cloud Project (or create a new one)
2. A Service Account with Drive API access
3. A JSON key file for authentication

## Step 1: Google Cloud Project

### 1.1 Create a project (or use existing)

1. Go to [Google Cloud Console](https://console.cloud.google.com/)
2. Click the project dropdown (top, next to "Google Cloud")
3. Click **"NEW PROJECT"**
4. Fill in:
   - **Project name**: `alphapy-drive-integration` (or choose your own name)
   - **Organization**: Leave empty or select your organization
5. Click **"CREATE"**
6. Wait until the project is created
7. Select the new project in the dropdown

**Or use an existing project:**
- Select your existing project from the dropdown

**Note:** Write down your **Project ID** (not the name, but the ID like `drive-integration-456012`)

## Step 2: Enable Google Drive API

1. In Google Cloud Console, go to **"APIs & Services" > "Library"**
2. Search for **"Google Drive API"**
3. Click **"Google Drive API"**
4. Click **"ENABLE"**
5. Wait until the API is enabled

## Step 3: Create Service Account

1. Go to **"IAM & Admin" > "Service Accounts"**
2. Click **"+ CREATE SERVICE ACCOUNT"**
3. Fill in:
   - **Service account name**: `alphapy-drive-reader` (or choose your own name)
   - **Service account ID**: Auto-generated (e.g. `alphapy-drive-reader`)
   - **Description**: `Service account for Alphapy bot to read PDFs from Google Drive`
4. Click **"CREATE AND CONTINUE"**

## Step 4: Grant Permissions (optional)

For read-only Drive access, no extra IAM role is needed—we use API scopes only.

1. Leave **"Grant this service account access to project"** empty (not needed)
2. Click **"CONTINUE"**
3. Click **"DONE"**

## Step 5: Create JSON Key

1. In the **Service Accounts** list, click your new service account
2. Go to the **"KEYS"** tab
3. Click **"ADD KEY" > "Create new key"**
4. Select **"JSON"**
5. Click **"CREATE"**
6. A JSON file will be downloaded automatically

**Important:**
- Store this file securely—it contains your private key
- Never add it to git (already in `.gitignore`)
- If you lose it, you must create a new key

## Step 6: Grant Drive Access

The service account can access Google Cloud but not yet specific Drive files.

### Option A: Share Drive folder/file with service account

1. Open Google Drive
2. Right-click the folder/file you want to share
3. Click **"Share"**
4. Add the **service account email** (e.g. `alphapy-drive-reader@drive-integration-456012.iam.gserviceaccount.com`)
5. Grant **"Viewer"** permissions (read-only)
6. Click **"Send"**

**Finding the service account email:**
- Google Cloud Console > IAM & Admin > Service Accounts
- Click your service account
- The email is shown at the top (e.g. `alphapy-drive-reader@PROJECT_ID.iam.gserviceaccount.com`)

### Option B: Share entire Drive (not recommended)

If you want the service account to read all Drive files:
1. Share your entire Google Drive with the service account email
2. Grant "Viewer" permissions

**Note:** This gives access to all files—use only when necessary.

## Step 7: Configure in Alphapy

### For local development (.env)

1. Open the downloaded JSON file
2. Copy the **entire** JSON content
3. Open your `.env` file
4. Add (on a single line, without extra quotes):
   ```bash
   GOOGLE_CREDENTIALS_JSON={"type":"service_account","project_id":"...","private_key_id":"...","private_key":"...","client_email":"...","client_id":"...","auth_uri":"https://accounts.google.com/o/oauth2/auth","token_uri":"https://oauth2.googleapis.com/token","auth_provider_x509_cert_url":"https://www.googleapis.com/oauth2/v1/certs","client_x509_cert_url":"..."}
   ```

**Important:**
- Put the entire JSON on **one line**
- Do **not** add extra quotes around the JSON (`""` or `''`)
- Or use single quotes: `GOOGLE_CREDENTIALS_JSON='{...}'`

**Example:**
```bash
GOOGLE_CREDENTIALS_JSON={"type":"service_account","project_id":"drive-integration-456012","private_key_id":"abc123","private_key":"-----BEGIN PRIVATE KEY-----\n...\n-----END PRIVATE KEY-----\n","client_email":"alphapy-drive-reader@drive-integration-456012.iam.gserviceaccount.com","client_id":"123456789","auth_uri":"https://accounts.google.com/o/oauth2/auth","token_uri":"https://oauth2.googleapis.com/token","auth_provider_x509_cert_url":"https://www.googleapis.com/oauth2/v1/certs","client_x509_cert_url":"https://www.googleapis.com/robot/v1/metadata/x509/alphapy-drive-reader%40drive-integration-456012.iam.gserviceaccount.com"}
```

### For production (Railway)

1. Open your Alphapy service on [Railway](https://railway.app/) → **Variables**.
2. Add or update **`GOOGLE_CREDENTIALS_JSON`** with the full service account JSON on **one line** (same format as local `.env`).
3. Remove unused legacy variables if they are still set: `GOOGLE_PROJECT_ID`, `GOOGLE_SECRET_NAME`.
4. Redeploy the service after changing variables.

## Step 8: Test the configuration

1. Restart your bot
2. Check the logs for:
   ```
   Loading Google Service Account credentials from GOOGLE_CREDENTIALS_JSON
   Google Drive service account authentication successful
   Google Drive configuration verified and ready
   ```

3. Test with `/learn_topic` command:
   - Use a topic that has a PDF in Drive
   - Verify the bot can read the PDF

## Troubleshooting

### Error: "Permission denied" or "Access denied"
- **Solution**: Share the Drive folder/file with the service account email
- Check you are using the correct email (with `.iam.gserviceaccount.com`)

### Error: "Invalid credentials" or "JSON decode error"
- **Solution**: Verify the JSON is correctly formatted in `.env`
- Ensure the entire JSON is on one line
- Remove extra quotes at the beginning/end

### Error: "Drive API not enabled"
- **Solution**: Enable Google Drive API in Google Cloud Console
- Go to APIs & Services > Library > Google Drive API > ENABLE

### Error: "Service account not found"
- **Solution**: Check you are using the correct project
- Verify the service account exists in the project

## Security best practices

1. **Rotate keys regularly** (every 90 days recommended)
2. **Store the JSON only in Railway variables or local `.env`** — never in git
3. **Grant minimal permissions** (only "Viewer" on specific folders)
4. **Monitor access** via Cloud Audit Logs
5. **Delete old keys** when creating new ones

## Summary checklist

- [ ] Google Cloud Project created/selected
- [ ] Google Drive API enabled
- [ ] Service Account created
- [ ] JSON key downloaded
- [ ] Drive folder/file shared with service account email
- [ ] `GOOGLE_CREDENTIALS_JSON` set in `.env` (local) or Railway (production)
- [ ] Bot restarted and logs checked
- [ ] Tested with `/learn_topic` command

## Need help?

- [Google Cloud Service Accounts Documentation](https://cloud.google.com/iam/docs/service-accounts)
- [Google Drive API Documentation](https://developers.google.com/drive/api)
- [Alphapy Security Guide](../security/)
