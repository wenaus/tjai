# Markdown rendering

tjai renders entry and PR/commit-body Markdown to HTML server-side. The logic exists
in two deliberately duplicated copies, each carrying a pointer comment to the other:

- `tjai_app/views.py` — `_render_markdown` + `_linkify_rendered_html`, used for every
  entry and page render.
- `scripts/md_render.py` — a Django-free copy (`render_markdown`, `linkify_rendered_html`,
  `render_body`, `render_inline`) for standalone cron scripts, notably `section_dev.py`,
  which builds the dev-activity page and cannot import the views helpers without
  bootstrapping Django.

Keep the two in sync when the rendering policy changes.

## Pipeline (`_render_markdown`)

1. `_fix_md_list_spacing` — repair AI-generated list spacing and hard-wrapped items.
2. `markdown.markdown(..., extensions=[nl2br, tables, fenced_code], tab_length=2)`.
3. `_neutralize_raw_html_hazards`, run before and after — escape `<title>`, `<script>`,
   `<style>`, and similar raw-text tags in content, so a stray tag in an external PR
   body cannot swallow the rest of the page render.
4. `_render_text_fences` — see below.
5. `_sanitize_rendered_html` — nh3 parses the resulting HTML and removes active
   elements, event-handler attributes and unsafe URL schemes. Ordinary formatting,
   images, tables, details/summary, code classes and math wrappers survive.
   Inline styles are limited to text alignment, colors, font weight/style and
   whitespace. Stored Markdown is unchanged. Both render copies use this policy.

Bare URLs are linkified by `_linkify_rendered_html`, which walks the rendered HTML and
skips `<a>`, `<code>`, `<pre>`, `<script>`, `<style>` so existing anchors and code are
left intact.

## Diary newlines

Diary entries (`context` `diary`, entry_id `diary-*`) are saved with a markdown hard
break — two trailing spaces — on every line that is followed by another prose line,
so a single newline renders as a line break under the default `md` format.
`_diary_hard_breaks` in `views.py` applies this in `api_entry_save`, after the
save-time trailing-whitespace strip; list items, headers, table rows, rules, raw HTML
and fenced code are left alone. The breaks are invisible in the editor and on the
page, and the pass is idempotent across saves. An entry saved before the pass existed
keeps its original text until its next save. `scripts/test_markdown_render.py` covers
the sweep.

## Code blocks

Fenced code blocks render to `<pre><code class="language-XXX">` and are syntax-highlighted
by Prism in the browser (`_prism.html`; grammars: markup, bash, python, c, cpp, json, yaml, sql).
Prism rebuilds each block's `innerHTML` from its `textContent`, so HTML injected into a
highlighted block on the server does not survive the highlight pass.

## `text` fences are prose, not code

A ```` ```text ```` fence holds prose — an email, a PR or commit body — where the writer
wants a monospace block but live links. Markdown does not parse link syntax inside any
fence, and `_linkify_rendered_html` skips `<pre>`/`<code>`, so links inside a fence would
otherwise be dead. `_render_text_fences` handles the `text` language specifically:

- strips the `language-text` class, so Prism ignores the block (there is no `text`
  grammar regardless) and server-injected anchors survive;
- converts `[text](url)` links and bare URLs inside the block to anchors;
- rewrites the wrapper to `<pre class="text-fence">`, preserving the monospace box.

Other languages are left untouched. `.text-fence` styling is defined in `_prism.html`
for pages that include that partial (e.g. the diary) and inherited from the existing
`.content pre` / `.body-text pre` boxes on the entry-detail and dev-activity pages.

## LaTeX math

Entries may contain inline `\(...\)` and display `\[...\]` LaTeX (research reports
routinely carry physics notation). The `pymdownx.arithmatex` extension (generic
mode) is part of the default extension set in both render copies and is appended
to the explicit `md_exts` lists in views; it carries the LaTeX through the
markdown pass intact, wrapped in `.arithmatex` spans/divs. Without it, markdown
eats `\(` as an escaped paren and the math renders as gunk.

`_katex.html` typesets those elements in the browser with KaTeX, vendored at
`static/tjai/vendor/katex/<version>/` (same convention as Prism). The partial is
included by the entry-detail, entry-public, research-detail, daily-synopsis, and
diary templates. KaTeX glyphs inherit the page text color, so dark mode needs no
special styling; failed parses leave the LaTeX source visible.

`scripts/test_markdown_render.py` covers rendering behavior, including the math
pass-through. `scripts/test_md_render.py` exercises the standalone copy's
`text`-fence handling.
