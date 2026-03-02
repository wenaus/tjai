# Add-ons: Gmail & Chrome Extension

## Gmail Add-on

A Gmail sidebar add-on that detects calendar invite emails (.ics attachments) and creates tjai journal entries with one click.

### What It Does

- Contextual trigger fires when viewing an email with .ics attachments
- Parses ICS VEVENT: summary, date/time, location, Zoom URL
- Handles Outlook/Exchange Windows timezone names (WINDOWS_TZ_ map), IANA names (validated via probe), and UTC
- Displays time in Eastern with EST/EDT abbreviation
- Creates journal entry: `Title [Zoom](url) [Gmail](permalink)`
- Gmail permalink via `GmailThread.getPermalink()` API

### Files

- `gmail_addon/Code.gs` — Apps Script code, manually pasted into the [Apps Script project](https://script.google.com/home/projects/18IPT5WjVYnsecm_j9Sv8LSsgbYi9hDM49Pjxc48rWH9tGNLbaN4Fq4jO/edit)
- `gmail_addon/appsscript.json` — Manifest (OAuth scope: `gmail.readonly`)
- Server endpoint: `api/add-journal` in `tjai_app/views.py` (Bearer token auth)

### Setup

1. Create a Google Apps Script project at script.google.com
2. Paste contents of `Code.gs` and `appsscript.json`
3. In `setApiKey()`, replace `REPLACE_WITH_ACTUAL_KEY` with the value from SysConfig `gmail_addon_api_key` (also in `~/.env` as `TJAI_GMAIL_ADDON_API_KEY`)
4. Run `setApiKey` once from the editor
5. Deploy as test deployment (Gmail Add-on type)

Server endpoint accepts: `{title, event_timestamp, zoom_url, gmail_url, location}`

---

## Chrome Extension (tj-getlink)

A Chrome extension for copying markdown links and saving bookmarks to tjai. Source code in the `tj-getlink/` directory.

### Features

- Copy page title + URL as markdown link `[Title](url)`
- Copy with clean URL (strips query params and fragments)
- Save to tjai as bookmark (kind `bookmark`, tagged `chrome`)

### How Bookmarking Works

- "Save to tjai" POSTs to `api/add-bookmark` with Bearer token auth
- API key prompted on first use, stored in `chrome.storage.sync`
- Uses browser tab title as bookmark title
- Server creates entry with content `[Title](url)`

### Files

- `tj-getlink/manifest.json` — Manifest V3 (permissions: `activeTab`, `clipboardWrite`, `storage`)
- `tj-getlink/popup.html/js/css` — Extension popup UI and logic
- Server endpoint: `api/add-bookmark` in `tjai_app/views.py` (same Bearer token as Gmail add-on)

### Setup

1. Chrome → `chrome://extensions/` → Enable Developer Mode
2. "Load unpacked" → select `tj-getlink/` directory
3. Click extension icon → "Save to tjai" → enter API key when prompted (same key as Gmail add-on, from SysConfig `gmail_addon_api_key`)
