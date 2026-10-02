# Clio developer app: 5-minute setup

Goal: a Clio developer application with read-only permissions, and four lines in `.env`. Because the permissions are read-only, the access token itself cannot write to Clio.

Sources: https://docs.developers.clio.com/api-docs/clio-manage/applications/ (fields), https://docs.developers.clio.com/api-docs/clio-manage/permissions/ (read vs write), https://docs.developers.clio.com/handbook/getting-started/building-private-apps/ (private apps). Items marked (unverified) are not in the docs; the first screen that disagrees wins.

## Before you start

- Log in with the account that owns the Sapini matter (the team's Clio Manage account). The app can only read data the authorizing user can see.
- Note which host the browser is on after login: `app.clio.com` (US), `eu.app.clio.com`, `ca.app.clio.com`, `au.app.clio.com`. That host is `CLIO_BASE_URL` below.
- Developer-portal access with that account is assumed. Docs mention a free developer account and note private apps need a paid account, not EasyStart pricing. Whether the hackathon account qualifies is (unverified); if the portal refuses you, that blocks everything, so resolve it first.

## Steps

1. Open https://developers.clio.com/apps/new (docs). If it asks, sign in with the Clio account above. Fallback: `https://<your-host>/settings/developer_applications` (a secondary source; unverified).
2. Fill in the form:
   - **Name:** any, for example `Corro (read-only)`. It appears on the approval page.
   - **Website URL:** any URL you control or `https://example.com`. Required (docs); not checked by us.
   - **Redirect URIs:** enter both, one per line or as separate entries (the form accepts "one or more", docs):
     - `http://127.0.0.1:8000/oauth/callback`
     - `http://localhost:8000/oauth/callback`
     Docs do not say whether plain `http` or loopback addresses are allowed (unverified). Registering both costs nothing and lets us use whichever the portal accepts. If both are rejected, use `https://app.clio.com/oauth/approval` (documented for desktop apps), and paste the code by hand by hand.
   - **App permissions:** choose **Read** (read-only, never Read/Write) on every resource the list offers. The app reads these: Matters, Contacts, Custom Fields (and field sets), Notes, Communications, Tasks, Calendar Entries, Documents (and folders, versions), Activities (carries expenses), Users, Practice Areas / Matter Stages. The portal's exact labels are (unverified): tick Read on anything that maps to those, leave Write unticked on everything. If a resource only offers Read/Write as one choice, pick the lower one and tell the Manager.
   - **Description, icons, support URL, deauthorization callback:** leave blank (optional, docs).
   - **Developer Terms of Service:** accept (required, docs).
3. Save. The app page shows the **App Key** and the **App Secret** (the app key is the OAuth `client_id`, the secret is `client_secret`). The docs page does not say where on the page they sit (unverified); look on the app's detail screen. Copy both now.
4. Permissions freeze at authorization: if you later change them, the app must be re-authorized (docs). Get them right now.

## `.env`

Copy `.env.example` to `.env` (gitignored) and set these, with your real values:

```
CLIO_CLIENT_ID=<App Key>
CLIO_CLIENT_SECRET=<App Secret>
CLIO_REDIRECT_URI=http://127.0.0.1:8000/oauth/callback
CLIO_BASE_URL=https://app.clio.com
```

- `CLIO_REDIRECT_URI` must be character-for-character one of the URIs registered in step 2 (scheme, host, port, no trailing slash difference). If the portal rejected the `127.0.0.1` form, use the `localhost` one, or the `oauth/approval` URL.
- `CLIO_BASE_URL`: use the regional host you noted (for example `https://eu.app.clio.com`).
- Never paste the secret into chat, a commit or a log.

## After that

- Once `.env` is filled, run the one-time authorization, which opens a Clio consent page listing the read permissions; accept it with the same account.
- Check that the consent page lists read access only. A screenshot of the app's permissions screen is also the evidence for the judges' "Clio is input only" rule.
- If authorization fails with a redirect-URI error, the registered and `.env` values differ; fix the `.env` first.
