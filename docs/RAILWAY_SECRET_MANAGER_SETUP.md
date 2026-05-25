---
title: Railway Secret Manager Setup
description: Configure Google credentials via Railway Secret Manager for Alphapy.
---

# Railway Secret Manager Setup Guide

Step-by-step guide for configuring Google Cloud Secret Manager on Railway.

## Overview

This guide helps you store Google Drive credentials securely in Secret Manager and use them from Railway, instead of storing credentials directly in Railway environment variables.

## Prerequisites

- Google Cloud Project with Secret Manager API enabled
- Railway account with access to your project
- Service account credentials JSON (for Google Drive)

## Step 1: Create secret in Google Cloud Secret Manager

### 1.1 Via Google Cloud Console

1. Go to [Google Cloud Console](https://console.cloud.google.com/)
2. Select your project (or create a new one)
3. Go to **"Security" > "Secret Manager"**
4. Click **"+ CREATE SECRET"**
5. Fill in:
   - **Name**: `alphapy-google-credentials` (or use `GOOGLE_SECRET_NAME` if you want a different name)
   - **Secret value**: Paste your full service account credentials JSON
   - **Version**: Leave as "Automatic"
6. Click **"CREATE SECRET"**

### 1.2 Via gcloud CLI (alternative)

```bash
# Ensure you're logged in
gcloud auth login

# Set your project
gcloud config set project YOUR_PROJECT_ID

# Create the secret
echo -n '{"type":"service_account","project_id":"...","private_key_id":"...","private_key":"...","client_email":"...","client_id":"...","auth_uri":"https://accounts.google.com/o/oauth2/auth","token_uri":"https://oauth2.googleapis.com/token","auth_provider_x509_cert_url":"https://www.googleapis.com/oauth2/v1/certs","client_x509_cert_url":"..."}' | \
  gcloud secrets create alphapy-google-credentials \
  --data-file=- \
  --project=YOUR_PROJECT_ID
```

**Important:** Replace `YOUR_PROJECT_ID` with your actual GCP project ID.

## Step 2: Grant Railway service account access

Railway uses a service account to access external services. There are two ways to configure this:

### Option A: Railway's service account (recommended for Railway-hosted)

If Railway uses a service account, you must grant it access:

1. **Find Railway's service account email**:
   - Go to Railway dashboard → your project → Settings → Variables
   - Check if there is a `RAILWAY_SERVICE_ACCOUNT_EMAIL` or similar variable
   - Or use the default Railway service account (usually something like `railway@railway.iam.gserviceaccount.com`)

2. **Grant access via gcloud CLI**:
   ```bash
   gcloud secrets add-iam-policy-binding alphapy-google-credentials \
     --member="serviceAccount:RAILWAY_SERVICE_ACCOUNT_EMAIL@PROJECT_ID.iam.gserviceaccount.com" \
     --role="roles/secretmanager.secretAccessor" \
     --project=YOUR_PROJECT_ID
   ```

3. **Or via Google Cloud Console**:
   - Go to Secret Manager → click on `alphapy-google-credentials`
   - Click the **"PERMISSIONS"** tab
   - Click **"ADD PRINCIPAL"**
   - Enter Railway service account email
   - Select role: **"Secret Manager Secret Accessor"**
   - Click **"SAVE"**

### Option B: Application Default Credentials (ADC) — for local testing

For local development you can use Application Default Credentials:

```bash
# Login with your personal account
gcloud auth application-default login

# This creates credentials that are used automatically
```

**Note:** This works only locally. For Railway you must use Option A.

### Option C: Service account key for Railway (if Option A doesn't work)

If Railway does not have a service account, you can create a service account key and use it as a Railway secret:

1. **Create service account**:
   ```bash
   gcloud iam service-accounts create railway-secret-accessor \
     --display-name="Railway Secret Accessor" \
     --project=YOUR_PROJECT_ID
   ```

2. **Grant access to Secret Manager**:
   ```bash
   gcloud secrets add-iam-policy-binding alphapy-google-credentials \
     --member="serviceAccount:railway-secret-accessor@YOUR_PROJECT_ID.iam.gserviceaccount.com" \
     --role="roles/secretmanager.secretAccessor" \
     --project=YOUR_PROJECT_ID
   ```

3. **Create service account key**:
   ```bash
   gcloud iam service-accounts keys create railway-key.json \
     --iam-account=railway-secret-accessor@YOUR_PROJECT_ID.iam.gserviceaccount.com
   ```

4. **Set key as Railway environment variable**:
   - Go to Railway → your project → Variables
   - Add: `GOOGLE_APPLICATION_CREDENTIALS_JSON` with the contents of `railway-key.json`
   - **Note:** This is only for authenticating with Secret Manager, not for Drive credentials themselves

## Step 3: Configure environment variables on Railway

### 3.1 Via Railway dashboard

**Important:** The variables you see (such as `RAILWAY_PUBLIC_DOMAIN`, `RAILWAY_PROJECT_ID`, etc.) are **system variables** that Railway provides automatically. You must add **custom variables** for Secret Manager.

**Steps:**

1. Go to your Railway project
2. Click your **service** (where Alphapy runs)—not "Variables" in the project menu
3. Go to the **"Variables"** tab in your service (not the system variables)
4. Click **"+ New Variable"** or **"Add Variable"**
5. Add the following **custom environment variables**:

```bash
# Required for Secret Manager
GOOGLE_PROJECT_ID=your-gcp-project-id

# Optional (if you use a different secret name)
# Default is "alphapy-google-credentials" if not set
GOOGLE_SECRET_NAME=alphapy-google-credentials
```

**Where to find this?**
- Railway Dashboard → Your Project → Your Service (e.g. "alphapy-bot") → **Variables** tab
- This is a different section from the system variables shown above
- Your custom variables appear at the bottom of the list, after the system variables

**Tip:** You can use `RAILWAY_PROJECT_ID` and `RAILWAY_SERVICE_NAME` for logging/debugging, but they are not required for Secret Manager configuration.

### 3.2 Remove old credentials (optional but recommended)

If you have `GOOGLE_CREDENTIALS_JSON` in Railway, you can remove it now:
- The code automatically uses Secret Manager when `GOOGLE_PROJECT_ID` is set
- This improves security because credentials are no longer in Railway environment variables

**Note:** Only remove `GOOGLE_CREDENTIALS_JSON` when you're sure Secret Manager works!

### 3.3 Via Railway CLI (alternative)

```bash
# Install Railway CLI if you don't have it
npm i -g @railway/cli

# Login
railway login

# Link to your project
railway link

# Set environment variables
railway variables set GOOGLE_PROJECT_ID=your-gcp-project-id
railway variables set GOOGLE_SECRET_NAME=alphapy-google-credentials
```

## Step 4: Verify setup

### 4.1 Check logs

After deployment, check Railway logs for:

```
🔐 Attempting to load Google credentials from Secret Manager (secret: alphapy-google-credentials)
✅ Loaded Google credentials from Secret Manager
✅ Google Drive service account authentication successful
```

If you see these messages, Secret Manager is working correctly!

### 4.2 Test Drive functionality

Test that Google Drive features work:
- Use a command that uses Drive (e.g. `/learn_topic` with Drive content)
- Check logs for errors

### 4.3 Troubleshooting

**Error: "Permission denied" or "Access denied"**
- Check that Railway service account has access to the secret
- Verify IAM permissions in GCP Console

**Error: "Secret not found"**
- Check that secret name matches `GOOGLE_SECRET_NAME`
- Verify the secret exists in the correct project

**Error: "Project not found"**
- Check that `GOOGLE_PROJECT_ID` is correct
- Verify the project exists and Secret Manager API is enabled

**Fallback to environment variable**
- If Secret Manager fails, the code falls back to `GOOGLE_CREDENTIALS_JSON`
- Check logs for: "Loading Google Service Account credentials from environment variable"
- This means Secret Manager is not working but fallback is

## Step 5: Enable Secret Manager API (if needed)

If you get errors about API not being enabled:

```bash
# Enable Secret Manager API
gcloud services enable secretmanager.googleapis.com --project=YOUR_PROJECT_ID
```

Or via Console:
1. Go to **"APIs & Services" > "Library"**
2. Search for "Secret Manager API"
3. Click **"ENABLE"**

## Security best practices

After setup, follow these best practices:

1. **Rotate credentials regularly**:
   - Create new service account key
   - Update secret in Secret Manager
   - Cache is automatically invalidated after 1 hour

2. **Monitor access**:
   - Check Cloud Audit Logs for Secret Manager access
   - Alert on unexpected access patterns

3. **Least privilege**:
   - Railway service account has only `secretmanager.secretAccessor` role
   - No admin or other permissions

4. **Backup**:
   - Store service account key securely (not in git!)
   - Document rotation procedure

## Summary checklist

- [ ] Secret created in Secret Manager (`alphapy-google-credentials`)
- [ ] Railway service account has `roles/secretmanager.secretAccessor` on secret
- [ ] Secret Manager API enabled in GCP project
- [ ] `GOOGLE_PROJECT_ID` set in Railway environment variables
- [ ] `GOOGLE_SECRET_NAME` set (optional, default works too)
- [ ] Deployment done and logs checked
- [ ] Drive functionality tested
- [ ] Old `GOOGLE_CREDENTIALS_JSON` removed (after verification)

## Need help?

- Check [docs/SECURITY.md](SECURITY.md) for general security best practices
- Check Railway logs for specific errors
- Verify GCP IAM permissions in Console
