/**
 * tjai shared client-side utilities
 * Include via: <script src="/tjai/static/tjai/tjai-utils.js"></script>
 *
 * Date/time formatting is server-side (tjai_utils.py). This file is only
 * for things that genuinely need client-side computation (live timers).
 */

/**
 * HTML-escape a string (for use before linkifyContent).
 * @param {string} s - Raw text
 * @returns {string} HTML-safe string
 */
function escapeHtml(s) {
    if (!s) return '';
    var el = document.createElement('span');
    el.textContent = s;
    return el.innerHTML;
}

/**
 * Linkify content that has already been HTML-escaped.
 * Handles: [title](entry:uuid), [title](/path), [title](url),
 * [[wiki-links]] (@name, url, entry_id), bare URLs, :tags.
 * @param {string} content - HTML-escaped text
 * @returns {string} HTML with links and tag styling
 */
function linkifyContent(content) {
    var L = 'style="color:#90caf9"';
    // [title](entry:uuid) — internal entry links
    content = content.replace(/\[([^\]]+)\]\(entry:([^\)]+)\)/g,
        '<a href="/tjai/entry/$2/" ' + L + '>$1</a>');
    // [title](/path) — internal relative links
    content = content.replace(/\[([^\]]+)\]\((\/[^\)]+)\)/g,
        '<a href="$2" ' + L + '>$1</a>');
    // [title](url) — external markdown links
    content = content.replace(/\[([^\]]+)\]\((https?:\/\/[^\)]+)\)/g,
        '<a href="$2" target="_blank" rel="noopener" ' + L + '>$1</a>');
    // [[wiki-links]]: [[url]], [[@name]], or [[entry_id]]
    content = content.replace(/\[\[([^\]]+)\]\]/g, function(m, ref) {
        if (ref.startsWith('http://') || ref.startsWith('https://'))
            return '<a href="' + ref + '" target="_blank" ' + L + '>' + ref + '</a>';
        if (ref.startsWith('@'))
            return '<a href="/tjai/entry/?name=' + encodeURIComponent(ref.slice(1)) + '" ' + L + '>' + ref + '</a>';
        return '<a href="/tjai/entry/?entry_id=' + encodeURIComponent(ref) + '" ' + L + '>' + ref + '</a>';
    });
    // bare URLs (not already in href="...")
    content = content.replace(/(^|[^"'>])(https?:\/\/[^\s<]+)/g,
        '$1<a href="$2" target="_blank" ' + L + '>$2</a>');
    // :tags
    content = content.replace(/(:[a-zA-Z][a-zA-Z0-9_-]*)/g,
        '<span style="color:#9ccc65">$1</span>');
    return content;
}

function entryRefUrl(ref) {
    var uuidRe = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
    if (uuidRe.test(ref)) return '/tjai/entry/?uuid=' + encodeURIComponent(ref);
    return '/tjai/entry/?entry_id=' + encodeURIComponent(ref);
}

/**
 * Linkify explicit entry references in already-escaped log/API text.
 * Handles UUIDs and values following keys such as entry_id=, entry=, uuid=,
 * plus JSON-style "entry_id": "value" after HTML escaping.
 */
function linkifyEntryReferences(content) {
    var L = 'style="color:#90caf9"';
    var uuidRe = /\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b/ig;
    var keyedRe = /((?:entry_id|entry|uuid|current_entry|source_entry_id|result_entry_id)(?:&quot;)?\s*[:=]\s*(?:&quot;)?)([A-Za-z0-9][A-Za-z0-9_.:-]{2,})(?=(&quot;|[\s,)}\]]|$))/g;
    return content.split(/(<[^>]+>)/g).map(function(part) {
        if (part.startsWith('<')) return part;
        part = part.replace(uuidRe, function(ref) {
            return '<a href="' + entryRefUrl(ref) + '" ' + L + '>' + ref + '</a>';
        });
        return part.replace(keyedRe, function(m, prefix, ref, suffix) {
            if (/^https?:/i.test(ref)) return m;
            return prefix + '<a href="' + entryRefUrl(ref) + '" ' + L + '>' + ref + '</a>';
        });
    }).join('');
}

/**
 * Format a duration in seconds as human-readable string.
 * @param {number} sec - Duration in seconds
 * @returns {string} e.g. "45s", "12m", "3h 15m", "2d 5h"
 */
function fmtDuration(sec) {
    if (sec == null || isNaN(sec)) return '';
    sec = Math.abs(Math.floor(sec));
    if (sec < 60) return sec + 's';
    if (sec < 3600) return Math.floor(sec / 60) + 'm';
    if (sec < 86400) {
        var h = Math.floor(sec / 3600);
        var m = Math.floor((sec % 3600) / 60);
        return m > 0 ? h + 'h ' + m + 'm' : h + 'h';
    }
    var d = Math.floor(sec / 86400);
    var hr = Math.floor((sec % 86400) / 3600);
    return hr > 0 ? d + 'd ' + hr + 'h' : d + 'd';
}

/**
 * Ensure copy events put text/html on the clipboard.
 *
 * Chrome's default copy serialization does not always populate the text/html
 * channel — we observed it writing text/plain only for a copy from a
 * contenteditable=false rendered page. Without text/html the entry-editor
 * paste handler cannot reconstruct wiki-links or run Turndown, so links and
 * inline formatting are lost on paste. This handler mirrors the selection
 * into both channels so downstream pastes have structure to work with.
 *
 * Skips if another copy handler (e.g. the dashboard entry-aware copier)
 * already populated clipboardData.
 */
(function installClipboardHtmlCopyHandler() {
    document.addEventListener('copy', function(e) {
        try {
            if (e.clipboardData.types && e.clipboardData.types.length > 0) return;
            var sel = window.getSelection();
            if (!sel || sel.isCollapsed) return;
            var range = sel.getRangeAt(0);
            var frag = range.cloneContents();
            var div = document.createElement('div');
            div.appendChild(frag);
            // Absolutize relative <a href> to survive Chrome's clipboard
            // sanitizer, which blanks hrefs that aren't fully resolvable at
            // write time — this was dropping every tjai wiki-link href on
            // the OS clipboard.
            var anchors = div.querySelectorAll('a[href]');
            for (var i = 0; i < anchors.length; i++) {
                var h = anchors[i].getAttribute('href');
                if (h && /^\//.test(h)) {
                    anchors[i].setAttribute('href', location.origin + h);
                }
            }
            var html = div.innerHTML;
            if (!html) return;
            e.clipboardData.setData('text/html', html);
            e.clipboardData.setData('text/plain', sel.toString());
            e.preventDefault();
        } catch (err) { /* let the browser default run */ }
    });
})();
