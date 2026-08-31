---
layout: page
title: Privacy Policy
permalink: /privacy-policy/
---

# Innersync • Alphapy Bot Privacy Policy

_Last updated: 2026-08-31_

This Privacy Policy explains how the Innersync • Alphapy Discord Bot (“**Innersync • Alphapy**”, “**Bot**”, “**we**”, “**us**”) collects, uses, and protects information when it operates on your Discord server and when you use our pricing and information website. By installing or interacting with the Bot, or by visiting our websites, you consent to the practices described here. If you do not agree with this Policy, do not use the Bot or our websites.

**Scope.** This Policy applies to (1) the Innersync • Alphapy Discord Bot and its related services, and (2) our pricing and information website at **pricing.alphapy.innersync.tech** (the "**Pricing Site**"). Visitors to the Pricing Site are covered by this Policy in addition to the Bot users described below.

> **At a glance:** Innersync • Alphapy stores the minimum data required to deliver its reminder, ticketing, analytics, and AI-assisted features. We rely primarily on legitimate interest to process interaction data, keep it only as long as needed, and provide GDPR-friendly export and deletion options.

## 1. Who is responsible?

The Innersync • Alphapy Bot is maintained by the project owner reachable at `support@innersync.tech`. This individual acts as the data controller for personal data processed by hosted instances of the Bot. For users in Belgium, this falls under the Belgian Data Protection Act (Wet van 30 juli 2018 betreffende de bescherming van natuurlijke personen met betrekking tot de verwerking van persoonsgegevens). If you self-host the Bot, you become the controller for that deployment and must adapt this Policy accordingly.

## 2. What data we collect

Depending on the features you enable, the Bot may process the following categories of data:

- **Discord account identifiers**: User IDs, usernames, discriminator/tag, avatar URLs, and guild (server) IDs. Needed to identify who triggered a command, schedule reminders, or manage access control.
- **Server configuration data**: Channel IDs, role IDs, preference flags, and other settings stored via `/config` or environment variables.
- **Reminder content**: Event titles, dates, times, recurrence schedules, linked message URLs, and channel IDs captured from embeds or manual `/add_reminder` commands.
- **Interaction content**:
  - Growth check-in prompts and user responses;
  - Learning requests (`/learn_topic`) and ticket conversation summaries;
  - AI caption inputs and generated outputs (for context and troubleshooting);
  - **`/agent` session turns** while a session is open, **session metadata**, and **optional distilled insights** if you opt in to learning in the Innersync App.
- **App Inner Barz / Grok chat** (the Innersync App journal chat) is **not** stored by the Bot. That history lives in the App account and is described in the [App Privacy Policy](https://app.innersync.tech/privacy) (same controller, different store).
- **Ticketing data**: Ticket channel IDs, message excerpts, AI summaries, user and staff actions, and status updates.
- **Logging and audit events**: Internal logs for errors, status updates, and admin actions posted to designated log channels. Guild log channels may record that guild's GDPR agreement acceptance. Innersync `/link`, `/unlink`, and `/delete_my_data` confirmations are posted only to the Innersync home guild log channel (Discord user mention and id only).
- **Support communications**: Emails or GitHub issues you send us about the Bot.
- **Premium subscription data**: For paying users, we store subscription tier, status, activation date, and guild assignment. Payment information (credit card details, billing addresses) is processed exclusively by our payment provider (Lemon Squeezy) and never stored on our systems.
- **Email address**: Optionally collected during the onboarding flow when your guild administrator has enabled the email question step. Used solely for communication related to your onboarding or support. Not shared with third parties beyond hosting infrastructure. You may leave this field blank if it is marked optional.

**Data related to the Pricing Site (pricing.alphapy.innersync.tech):**

- **Cookie and consent preferences (stored locally in your browser):** The Pricing Site may store the following keys in your browser’s local storage to remember your choices and avoid showing the cookie banner repeatedly: `alphapy-cookie-consent` (e.g. "accepted" or "essential-only"), `alphapy-consent-version`, `alphapy-analytics-consent`, and `alphapy-consent-date`. These values are not sent to our servers and do not identify you to us; they are used only to respect your consent choice and to manage the cookie banner on your device.
- **Contact:** If you email us at `support@innersync.tech` (for example from a link on the Pricing Site or the Cookie Policy page), we process your email address, subject line, and message content as part of our support correspondence. This processing is described in this Policy and is based on legitimate interest or, where applicable, your consent.

We do not intentionally collect sensitive categories of personal data, but users may include such information in free-form text fields. Server administrators should discourage sharing sensitive details and may redact them using the deletion options described below.

## 3. How we use the data

We use the data listed above to:

- Deliver requested features (e.g., send reminders, respond to commands, store ticket history);
- Provide AI-driven outputs by forwarding prompts to model providers such as Grok;
- Process and manage premium subscriptions (tier activation, feature gating, billing support);
- Monitor performance, detect abuse, and debug issues;
- Send audit logs to staff-only channels that you configure;
- Comply with legal obligations and enforce the Terms of Service.

## 3a. Cookies and similar technologies (Pricing Site)

On the Pricing Site we use cookies and similar storage for:

- **Essential:** Operation of the site and management of your cookie/consent choices (e.g. so we do not show the banner again unnecessarily).
- **Optional (analytics, payment):** Only after you choose "Accept all" in the cookie banner, we may use optional cookies or similar technologies for analytics or, in the future, for payment flows.

For a detailed list of cookies and storage used on the Pricing Site, see our **Cookie Policy** at [pricing.alphapy.innersync.tech/cookie-policy](https://pricing.alphapy.innersync.tech/cookie-policy).

## 4. Legal bases (GDPR)

For data subjects in the European Economic Area, United Kingdom, or Belgium, we rely on the following legal bases:

- **Legitimate interest** for operating community tools (reminders, tickets, analytics) and ensuring platform security; for essential operation of the Pricing Site and cookie/consent management; and for handling support emails (e.g. to `support@innersync.tech`);
- **Consent** for optional AI features when server administrators explain and enable them for their community, and for optional cookies/analytics on the Pricing Site when you choose "Accept all";
- **Performance of contract** where processing is necessary to perform our agreement with you (e.g. premium subscriptions);
- **Legal obligation** if we must retain data to comply with tax, regulatory, or court requirements.

## 5. Sharing and disclosure

We do not sell or rent your data. We may share data with:

- **Discord** when responding to events and sending messages;
- **Grok or other AI providers** when you enable Grok/AI-powered commands, limited to the prompt and necessary metadata;
- **Hosting and infrastructure providers** that store databases, logs, or backups;
- **Legal authorities** if required to comply with applicable laws, court orders, or government requests.

**Payments.** Premium subscriptions for the Bot are processed by our payment provider **Lemon Squeezy**. We share with them only the data necessary to complete the transaction (e.g. email, account identifiers, subscription tier). Payment details (card numbers, billing address) are handled directly by Lemon Squeezy and are not stored by us. Lemon Squeezy’s privacy policy applies to that processing: [Lemon Squeezy Privacy Policy](https://www.lemonsqueezy.com/privacy). If we introduce a payment or checkout flow on the Pricing Site in the future, we will name the payment provider here, state which data we share with them (e.g. payment details, email, name), for what purpose (performance of the payment), and where their privacy policy can be found.

Each provider processes data under their own terms and policies. We aim to choose partners that meet GDPR adequacy standards or enter into appropriate transfer agreements.

## 6. Data retention

- Reminder entries persist until the scheduled job completes or an admin deletes them.
- Ticket transcripts and AI summaries remain until manually removed.
- Growth check-in responses are stored for follow-up unless deleted.
- **`/agent` session turns:** removed when you end the session (`/agent end`) or when that session is removed from App history. There is no 7-day chat TTL.
- **`/agent` session metadata / opted-in insights:** kept until you delete that history row, reset learning, use `/delete_my_data`, or App GDPR purge — not the same clock as turns.
- **Prompts sent to Grok / X.AI:** outside our retention clock; we cannot recall a copy already sent to the model provider.
- Premium subscription records (tier, status, guild assignment) are kept for the duration of active subscriptions plus 7 years for Belgian tax compliance requirements (Belgian Income Tax Code - Wetboek van inkomstenbelastingen). After this period, premium subscription records are automatically anonymized or deleted, unless an ongoing procedure requires otherwise.
- Audit logs in Discord channels follow Discord’s retention unless you purge them.
- Backups (if enabled) are kept for up to 30 days before automatic deletion.
- **Pricing Site – consent preferences:** Stored in your browser until you clear them or withdraw consent; we do not retain these on our servers.
- **Pricing Site – support emails:** Retained in line with our general retention for correspondence (support communications), as described above and in our internal policies.

You can request deletion sooner at any time using the methods below.

## 7. Your rights and controls

If you are located in the EU/EEA, UK, or other regions with similar rights, you may have the following rights regarding your personal data:

- Access, export, or receive a copy of the data (`/export_onboarding` and other exports);
- Request correction of inaccurate data;
- Object to or restrict certain processing;
- Request deletion of all your Railway database records via the `/delete_my_data` Discord command (available in any server running Alphapy). This deletes your bot data only; your Discord account is governed by Discord's own privacy policy and is not affected.
- Withdraw consent for optional AI features and for optional cookies on the Pricing Site;
- Data portability, where applicable.

These rights apply to all processing described in this Policy, including processing related to your use of the Pricing Site and support emails. To exercise a right, contact the data controller at `support@innersync.tech` or use the available Bot commands where applicable. We will respond within 30 days where legally required. For Belgian residents, response times follow GBA guidelines (30-60 days maximum).

For Belgian users: complaints can also be filed with the Gegevensbeschermingsautoriteit (GBA) via https://www.gegevensbeschermingsautoriteit.be.

## 8. Data deletion workflow

**Self-service (recommended):** Use the `/delete_my_data` slash command in any server running Alphapy. The command shows a detailed list of what will be deleted and requires explicit confirmation (two-step: read + confirm button). Deletion covers all Railway PostgreSQL records linked to your Discord user ID — including onboarding responses, tickets, audit logs, AI usage records, reflections, and consent records. Anonymisation is applied to reminders and custom commands you created. Your Discord account and Supabase/web account are **not** affected. Premium subscription records are retained for 7 years as required by Belgian tax law.

**By email:** Server administrators can also delete reminders, tickets, or check-in records via bot commands or database tools. Individuals may additionally request manual deletion by emailing the controller at `support@innersync.tech` with their Discord username and ID. We may take reasonable steps to verify identity before fulfilling requests.

## 9. Security practices

We implement technical and organizational measures, including:

- Using role-restricted log channels and environment-based secrets management;
- Limiting database access to authorized maintainers;
- Encrypting connections to the database and third-party APIs using TLS;
- Monitoring dependencies and applying patches promptly.

No system is fully secure, so please notify us immediately if you discover a vulnerability or suspect unauthorized access.

## 10. International transfers

Your data may be processed in countries different from where you reside, including the European Union and the United States. When transferring personal data internationally, we rely on adequacy decisions or Standard Contractual Clauses where required by law. For Belgian users, international transfers comply with Belgian requirements under the Data Protection Act and GBA guidelines.

## 11. Children’s data

The Bot is not intended for individuals under 13 (or the minimum age required in their jurisdiction to use Discord). We do not knowingly collect personal information from children. If you believe a child has provided data, contact us to remove it.

## 12. Changes to this Policy

We may update this Policy to reflect changes in the Bot or legal requirements. We will adjust the “Last updated” date and, when material changes occur, announce them via the GitHub repository or official communication channels. Continued use signifies acceptance of the updated Policy.

## 13. Contact

For questions, data requests, or concerns about this Policy, email `support@innersync.tech` or open an issue on the Github repository.

## 14. Disclaimer

This Privacy Policy is provided for informational purposes and does not constitute legal advice. While we strive to comply with all applicable privacy laws, users should consult with qualified legal professionals for specific concerns about their personal data.

---

By installing or using Innersync • Alphapy, you acknowledge that you have read and understood this Privacy Policy.

---

**Legal information:** For company details (enterprise number, registered office, contact), see [Legal Information](/legal/).
