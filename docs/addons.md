# Add-ons: Gmail & Chrome Extension

## Gmail Add-on

A Gmail sidebar add-on that creates tjai entries from the email being viewed. The card offers four sections — **journal** (calendar entry, prefilled from detected event details), **bookmark**, and **memory**, each posting to `api/add-entry` with the corresponding `kind`, and **images**, which stashes the email's images as a capture (below).

### What It Does

- The contextual trigger is unconditional: the card renders for every open email
- Journal prefill from the open message's `.ics` attachments or inline `text/calendar` MIME parts, then a meeting-details parse of its subject/body (date/time, Zoom URL, Indico URL). No earlier messages are fetched.
- Parses ICS VEVENT: summary, date/time, location, Zoom URL
- Handles Outlook/Exchange Windows timezone names (WINDOWS_TZ_ map), IANA names (validated via probe), and UTC; body-parsed times with no explicit timezone use one inferred from the sender's country-code TLD
- Displays time in Eastern with EST/EDT abbreviation
- Journal prefill format: `Title @ location [zoom](url) [indico](url)`, with `[gmail](permalink)` appended on submit; editable date (YYYYMMDD) and time (HHMM) fields
- Bookmark and memory sections take free text with `:tag` and `=context` tokens
- Gmail permalink via `GmailThread.getPermalink()` API

### Files

- `gmail_addon/Code.gs` — Apps Script code, manually pasted into the [Apps Script project](https://script.google.com/home/projects/18IPT5WjVYnsecm_j9Sv8LSsgbYi9hDM49Pjxc48rWH9tGNLbaN4Fq4jO/edit)
- `gmail_addon/appsscript.json` — Manifest with the advanced Gmail v1 service (OAuth scopes: `gmail.addons.execute`, `gmail.readonly`, `script.external_request`)
- Server endpoint: `api/add-entry` in `tjai_app/views.py` (Bearer token auth)

### Setup

1. Create a Google Apps Script project at script.google.com
2. Paste contents of `Code.gs` and `appsscript.json`
3. From the editor, run `setApiKey("<key>")` once with the value from SysConfig `gmail_addon_api_key` (also in `~/.env` as `TJAI_GMAIL_ADDON_API_KEY`); the key is stored in the script's UserProperties
4. Deploy as test deployment (Gmail Add-on type)

Updates to message-part handling require pasting both `Code.gs` and
`appsscript.json`. The manifest enables the advanced Gmail service, which
fetches message structure and individual attachments separately. Apps Script
enables the Gmail API automatically for its default Cloud project; a standard
Cloud project requires enabling that API in the Cloud console. See Google's
[advanced service setup](https://developers.google.com/apps-script/guides/services/advanced).

Server endpoint accepts: `{kind, content, tags, context, source, event_date, event_time, all_day}`

### Stash images (captures)

The IMAGES section counts images in the open mail, inline pasted screenshots
included; images under 1 KB (tracking pixels, icons) are ignored. **Stash
images** posts those images as a multipart form to `api/add-capture` with the
subject, sender, Gmail permalink, and a note supporting `:tag` and `=context`.

Opening the card fetches only the current message's MIME structure and body.
Inline images are selected by Content-ID or Content-Location references in
the unquoted HTML. Gmail, Yahoo and Proton quote wrappers, HTML blockquotes,
and the Outlook reply boundary exclude quoted inline images; explicitly
attached image files remain selectable. Attached email messages are excluded.
Image attachment bodies are fetched individually when Stash is clicked. No
action enumerates the thread, compares earlier attachments, or downloads raw
MIME to look for calendar data. Calendar detection still handles inline and
attached ICS and the existing subject/body date forms within the open mail.

`scripts/test_gmail_addon_images.js` checks selection, card rendering and
stashing with 300 quoted image parts, verifies that opening the card fetches
no image attachment bodies, and checks inline and attached calendar prefill.
`scripts/test_gmail_addon_parse.js` covers the calendar text parser.

The server stores the files under `data/captures/YYYY-MM/<entry uuid>/NN-name.ext`; the data directory is excluded from the deploy rsync and included in the nightly backup. It creates one memory entry tagged `gmail` and `capture`, in the given context, whose content is the subject, sender, and permalink on line 1, the note, and one markdown image line per file with its absolute URL. Accepted types are PNG, JPEG, GIF, WebP, BMP, TIFF, and HEIC, at most 20 files of 25 MB each per stash.

The files are served at `/tjai/capture/<entry uuid>/<file>` as public reads: anyone with the URL, no credential. The entry page shows the images inline, and an AI session on any machine fetches them with a plain GET ([claude-integration.md](claude-integration.md#captures)).

The **Captures** page (`/tjai/captures/`, menu link) is a public read too. It lists every stash newest first with its images, rendered server-side with absolute image URLs so that a GET of the page is the list; a logged-in session also sees a Delete button per capture and one per image, and deletion requires that login. Deleting a capture removes its files and moves the entry to Trash; restoring the entry does not restore the files. Deleting an image removes that file and its line from the entry, and deleting the last image deletes the capture.

Files: `tjai_app/captures.py` (storage, listing, delete); the `api_add_capture`, `capture_file`, `captures_page`, and `api_capture_delete` views in `tjai_app/views.py`; `tjai_app/templates/tjai_app/captures.html`; `scripts/test_captures.py` (functionality test through Django's test client).

---

## Chrome Extension (tj-getlink)

A Chrome extension for copying markdown links and saving bookmarks to tjai. Source code in the `tj-getlink/` directory at the tjrepo root (a sibling of `tjai/`).

### Features

- Copy page title + URL as markdown link `[Title](url)`
- Each full-URL action shares one row with a **without suffix** variant that strips query parameters and fragments
- Optional **pin** and **top** checkboxes; **pin** is equivalent to adding `:pin` in the text field, while **top** also adds the saved entry to Capcom's ordered top pin group
- Save to tjai as bookmark (kind `bookmark`, tagged `chrome`), with variants that also add a `readme` tag
- Detect events on the page and add them to the tjai calendar (see below)
- Curate picks from the current page — two buttons, documented in [picks-curate-page.md](picks-curate-page.md)
- `Ctrl+Shift+L` (`Cmd+Shift+L` on Mac) opens the popup

### How Bookmarking Works

- "Save to tjai" POSTs to `api/add-bookmark` with Bearer token auth
- API key prompted on first use, stored in `chrome.storage.sync`
- Uses browser tab title as bookmark title
- Server creates entry with content `[Title](url)`; a free-text note field is appended, with `:tag`, `@name`, and `=context` tokens parsed out and applied to the entry
- Bookmark updates preserve existing tags. Checked pin controls add metadata without removing tags such as `:readme`
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
