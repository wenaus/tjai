#!/usr/bin/env python3
"""Django-free Markdown rendering for standalone scripts (cron, etc.).

A deliberate copy of the markdown helpers in tjai_app/views.py
(`_render_markdown`, `_fix_md_list_spacing`, `_neutralize_raw_html_hazards`,
`_linkify_rendered_html`). The originals live inside the Django-heavy views
module and can't be imported without bootstrapping Django; standalone scripts
(e.g. section_dev.py, run from cron with no app env) need the same safe
rendering without that side effect. DRY is broken on purpose here — keep this
in sync with views.py if the rendering policy changes.

Public API:
    render_markdown(text, extensions=None) -> html   (list-fix + md + hazard neutralize)
    linkify_rendered_html(html) -> html              (linkify bare URLs in text nodes)
    render_body(text) -> html                        (render_markdown + linkify; block content)
    render_inline(text) -> html                      (single-line render, wrapping <p> stripped)
"""
import re
from html import escape as html_escape, unescape as html_unescape

_LIST_RE = re.compile(r'[-*+] |\d+\. ')
_LIST_MARKER_RE = re.compile(r'^(\s*)([-*+] |\d+\. )')
_BLOCK_RE = re.compile(r'^(\s*)([-*+] |\d+\. |#{1,6} |```)')


def _deindent_paragraph_list_blocks(lines):
    """Normalize indented list blocks that start after paragraph text."""
    result = []
    i = 0
    while i < len(lines):
        line = lines[i]
        marker = _LIST_MARKER_RE.match(line)
        if (marker and marker.group(1) and i > 0
                and lines[i - 1].strip()
                and not _LIST_RE.match(lines[i - 1].lstrip())):
            base_indent = len(marker.group(1))
            if result and result[-1].strip():
                result.append('')
            while i < len(lines):
                block_line = lines[i]
                if not block_line.strip():
                    result.append(block_line)
                    i += 1
                    continue
                cur_indent = len(block_line) - len(block_line.lstrip())
                if cur_indent < base_indent:
                    break
                result.append(block_line[base_indent:])
                i += 1
            continue
        result.append(line)
        i += 1
    return result


def _fix_md_list_spacing(text):
    """Fix two common markdown list issues:

    1. Insert blank line before list items that follow a non-list, non-blank
       line (AI-generated markdown often omits this).
    2. Rejoin broken continuation lines — when a list item's text was
       hard-wrapped and the continuation starts at column 0 (or below the
       list content indent), the markdown parser loses nesting context.
    """
    lines = _deindent_paragraph_list_blocks(text.split('\n'))
    result = []
    for i, line in enumerate(lines):
        # Rejoin broken list continuations: non-blank, non-block line whose
        # indent is less than the previous list item's content column.
        if (i > 0 and line.strip() and result
                and not _BLOCK_RE.match(line)):
            prev = result[-1]
            pm = re.match(r'^(\s*)([-*+] |\d+\. )', prev)
            if pm:
                content_col = len(pm.group(1)) + len(pm.group(2))
                cur_indent = len(line) - len(line.lstrip())
                if cur_indent < content_col:
                    result[-1] = prev + ' ' + line.strip()
                    continue

        if (i > 0
                and _LIST_RE.match(line.lstrip())
                and lines[i - 1].strip()
                and not _LIST_RE.match(lines[i - 1].lstrip())):
            result.append('')
        result.append(line)
    return '\n'.join(result)


# HTML elements that put the parser into rcdata / raw-text / plaintext mode,
# swallowing all following page content until their (often absent) close tag.
# A literal '<title>' in content — e.g. from a PR/commit "PR #<N>: <title>" —
# would otherwise truncate the rest of the rendered page mid-render. None of
# these tags have any legitimate place in rendered content, so escaping them is
# side-effect-free across every render path.
_RAW_HTML_HAZARD_TAGS = {
    'html', 'head', 'body', 'base', 'link', 'meta', 'title', 'script',
    'style', 'textarea', 'iframe', 'noscript', 'noembed', 'noframes',
    'xmp', 'plaintext',
}
_RAW_HTML_HAZARD_PREFIXES = {
    tag[:n]
    for tag in _RAW_HTML_HAZARD_TAGS
    for n in range(3, len(tag) + 1)
}
_RAW_HTML_TAG_RE = re.compile(r'</?([A-Za-z][A-Za-z0-9:-]*)(?:\s[^>\n]*)?>?', re.IGNORECASE)


def _neutralize_raw_html_hazards(text):
    def repl(match):
        tag = match.group(1).lower()
        if tag not in _RAW_HTML_HAZARD_PREFIXES:
            return match.group(0)
        return match.group(0).replace('<', '&lt;').replace('>', '&gt;')
    return _RAW_HTML_TAG_RE.sub(repl, text)


_HTML_CLEANER = None


def _sanitize_rendered_html(html):
    """Allow formatted content while removing active HTML; sync with views.py."""
    import nh3
    global _HTML_CLEANER
    if _HTML_CLEANER is None:
        attrs = {tag: set(values) for tag, values in nh3.ALLOWED_ATTRIBUTES.items()}
        attrs.setdefault('*', set()).update({'class', 'id', 'style'})
        attrs.setdefault('a', set()).add('target')
        _HTML_CLEANER = nh3.Cleaner(
            tags=nh3.ALLOWED_TAGS | {'details', 'summary'}, attributes=attrs,
            link_rel=None,
            filter_style_properties={'text-align', 'color', 'background-color',
                                     'font-weight', 'font-style', 'white-space'},
        )
    return _HTML_CLEANER.clean(html)


def render_markdown(text, extensions=None):
    """Unified render: list-spacing fix + markdown + hazard-tag neutralization.
    Use this instead of markdown.markdown() directly so every render path gets
    the same safety post-processing."""
    import markdown
    if not text:
        return ''
    exts = extensions if extensions is not None else [
        'nl2br', 'tables', 'fenced_code', 'pymdownx.arithmatex']
    # Kept in sync with tjai_app.views._render_markdown — arithmatex
    # (generic) carries \(...\) LaTeX through markdown for KaTeX to typeset.
    cfgs = {'pymdownx.arithmatex': {'generic': True}} if 'pymdownx.arithmatex' in exts else {}
    safe_text = _neutralize_raw_html_hazards(text)
    html = markdown.markdown(_fix_md_list_spacing(safe_text), extensions=exts,
                             extension_configs=cfgs, tab_length=2)
    html = _neutralize_raw_html_hazards(html)
    return _sanitize_rendered_html(_render_text_fences(html))


_BARE_URL_RE = re.compile(r'https?://[^\s<]+')
_HTML_TAG_RE = re.compile(r'(<[^>]+>)')
_LINKIFY_SKIP_TAGS = {'a', 'code', 'pre', 'script', 'style'}


def linkify_rendered_html(html):
    """Linkify bare URLs in rendered HTML text nodes, leaving existing anchors,
    code blocks, and tag attributes untouched."""
    if not html:
        return ''
    parts = _HTML_TAG_RE.split(html)
    stack = []
    out = []

    def linkify_text(text):
        def repl(match):
            url = match.group(0)
            suffix = ''
            while url and url[-1] in '.,;:!?':
                suffix = url[-1] + suffix
                url = url[:-1]
            href = html_escape(html_unescape(url), quote=True)
            return f'<a target="_blank" href="{href}">{url}</a>{suffix}'
        return _BARE_URL_RE.sub(repl, text)

    for part in parts:
        if not part:
            continue
        if part.startswith('<'):
            close = re.match(r'</\s*([A-Za-z0-9:-]+)', part)
            if close:
                tag = close.group(1).lower()
                for i in range(len(stack) - 1, -1, -1):
                    if stack[i] == tag:
                        del stack[i:]
                        break
                out.append(part)
                continue
            open_tag = re.match(r'<\s*([A-Za-z0-9:-]+)\b', part)
            if open_tag and not part.rstrip().endswith('/>'):
                stack.append(open_tag.group(1).lower())
            out.append(part)
        elif any(tag in _LINKIFY_SKIP_TAGS for tag in stack):
            out.append(part)
        else:
            out.append(linkify_text(part))
    return ''.join(out)


# A ```text``` fence is prose (emails, PR/commit bodies), not code: keep the
# monospace box but make links live. markdown emits <pre><code class="language-text">
# and never parses link syntax inside a fence; Prism would also re-render the block
# from its textContent, discarding any anchors injected server-side. So drop the
# language class (Prism then ignores the block), turn [text](url) and bare URLs into
# anchors, and restyle through the .text-fence class. Real code fences (```python,
# ```bash, …) are left untouched. Keep in sync with tjai_app/views.py.
_TEXT_FENCE_RE = re.compile(r'<pre><code class="language-text">(.*?)</code></pre>', re.DOTALL)
_MD_LINK_RE = re.compile(r'\[([^\]\n]+)\]\((https?://[^)\s]+)\)')


def _linkify_text_fence_content(content):
    def md_link(m):
        href = html_escape(html_unescape(m.group(2)), quote=True)
        return f'<a target="_blank" href="{href}">{m.group(1)}</a>'
    # Convert explicit [disp](url) links first, then linkify remaining bare URLs
    # (the tag-aware linkifier leaves the anchors just created alone).
    return linkify_rendered_html(_MD_LINK_RE.sub(md_link, content))


def _render_text_fences(html):
    def repl(m):
        return ('<pre class="text-fence"><code>'
                + _linkify_text_fence_content(m.group(1))
                + '</code></pre>')
    return _TEXT_FENCE_RE.sub(repl, html)


def render_body(text):
    """Render block markdown content (PR/commit body) to safe, linkified HTML."""
    return linkify_rendered_html(render_markdown(text))


_WRAP_P_RE = re.compile(r'^<p>(.*)</p>$', re.DOTALL)


def render_inline(text):
    """Render a single line of markdown, stripping the wrapping <p> so the
    result can sit inline (e.g. inside a <summary>)."""
    html = render_markdown(text)
    m = _WRAP_P_RE.match(html.strip())
    if m:
        html = m.group(1)
    return linkify_rendered_html(html)
