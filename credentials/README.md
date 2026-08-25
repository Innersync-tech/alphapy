# Credentials Directory

This folder is gitignored and exists so local secrets never land in the repo.

**Google Drive is no longer used.** The Drive client, `GOOGLE_CREDENTIALS_JSON`, and `docs/GOOGLE_CREDENTIALS_SETUP.md` were removed in the ballast cut (#375). `/learn_topic` reads only `data/prompts/*.md` and `*.txt`.

Do not put service-account JSON here expecting the bot to load it.

## Security

- Never commit credentials, `.env`, or key files.
- If a secret was ever committed, rotate it immediately (it may exist in git history).
