# Add-ons: Gmail & Chrome Extension

## Gmail Add-on

A Gmail sidebar add-on that creates tjai entries from the email being viewed. The card offers three sections — **journal** (calendar entry, prefilled from detected event details), **bookmark**, and **memory** — each posting to `api/add-entry` with the corresponding `kind`.

### What It Does

- The contextual trigger is unconditional: the card renders for every open email
- Journal prefill from event detection, tried in order: `.ics` attachments, ICS in the raw MIME content, then a meeting-details parse of the subject/body (date/time, Zoom URL, Indico URL)
- Parses ICS VEVENT: summary, date/time, location, Zoom URL
- Handles Outlook/Exchange Windows timezone names (WINDOWS_TZ_ map), IANA names (validated via probe), and UTC; body-parsed times with no explicit timezone use one inferred from the sender's country-code TLD
- Displays time in Eastern with EST/EDT abbreviation
- Journal prefill format: `Title @ location [zoom](url) [indico](url)`, with `[gmail](permalink)` appended on submit; editable date (YYYYMMDD) and time (HHMM) fields
- Bookmark and memory sections take free text with `:tag` and `=context` tokens
- Gmail permalink via `GmailThread.getPermalink()` API

### Files

- `gmail_addon/Code.gs` — Apps Script code, manually pasted into the [Apps Script project](https://script.google.com/home/projects/18IPT5WjVYnsecm_j9Sv8LSsgbYi9hDM49Pjxc48rWH9tGNLbaN4Fq4jO/edit)
- `gmail_addon/appsscript.json` — Manifest (OAuth scopes: `gmail.addons.execute`, `gmail.readonly`, `script.external_request`)
- Server endpoint: `api/add-entry` in `tjai_app/views.py` (Bearer token auth)

### Setup

1. Create a Google Apps Script project at script.google.com
2. Paste contents of `Code.gs` and `appsscript.json`
3. From the editor, run `setApiKey("<key>")` once with the value from SysConfig `gmail_addon_api_key` (also in `~/.env` as `TJAI_GMAIL_ADDON_API_KEY`); the key is stored in the script's UserProperties
4. Deploy as test deployment (Gmail Add-on type)

Server endpoint accepts: `{kind, content, tags, context, source, event_date, event_time, all_day}`

---

## Chrome Extension (tj-getlink)

A Chrome extension for copying markdown links and saving bookmarks to tjai. Source code in the `tj-getlink/` directory at the tjrepo root (a sibling of `tjai/`).

### Features

- Copy page title + URL as markdown link `[Title](url)`
- Copy with clean URL (strips query params and fragments)
- Save to tjai as bookmark (kind `bookmark`, tagged `chrome`), with variants that also add a `readme` tag
- Detect events on the page and add them to the tjai calendar (see below)
- Curate picks from the current page — two buttons, documented in [picks-curate-page.md](picks-curate-page.md)
- `Ctrl+Shift+L` (`Cmd+Shift+L` on Mac) opens the popup

### How Bookmarking Works

- "Save to tjai" POSTs to `api/add-bookmark` with Bearer token auth
- API key prompted on first use, stored in `chrome.storage.sync`
- Uses browser tab title as bookmark title
- Server creates entry with content `[Title](url)`; a free-text note field is appended, with `:tag`, `@name`, and `=context` tokens parsed out and applied to the entry
- A bookmark whose URL already exists is updated in place — content replaced, tags added, archived status cleared — rather than duplicated
- New bookmarks are auto-tagged (`tagger.tag_bookmark`)

### Event Detection

When the popup opens, a content script probes the active page for a schema.org
`Event` in JSON-LD (`<script type="application/ld+json">`). If one with a `name`
and `startDate` is found, an event card and an **Add to tjai calendar** button
appear. There is no URL gate — any page advertising an `Event` is matched
(Indico, Squarespace calendars, Eventbrite, and the like); pages without one
stay silent. A zoom URL, if present on the page, is captured too.

"Add to tjai calendar" POSTs to `api/add-journal` (same Bearer token) with
`{title, event_timestamp, location, zoom_url, event_url, source: "web"}`,
creating a `journal` entry tagged `web` with `data.event_date` set. The start
time is parsed from the Event's ISO `startDate` and displayed in the app
timezone (fetched from `api/health`). The server's `event_url` field renders a
generic `[event](url)` back-link; the Indico-specific `indico_url` field
renders `[indico](url)`.

### Files

- `tj-getlink/manifest.json` — Manifest V3 (permissions: `activeTab`, `clipboardWrite`, `storage`, `scripting`)
- `tj-getlink/popup.html/js/css` — Extension popup UI and logic
- Server endpoints: `api/add-bookmark` (bookmarks) and `api/add-journal` (events) in `tjai_app/views.py` (same Bearer token as Gmail add-on)

### Setup

1. Chrome → `chrome://extensions/` → Enable Developer Mode
2. "Load unpacked" → select `tj-getlink/` directory
3. Click extension icon → "Save to tjai" → enter API key when prompted (same key as Gmail add-on, from SysConfig `gmail_addon_api_key`)
