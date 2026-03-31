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
