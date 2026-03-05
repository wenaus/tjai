/**
 * tjai shared client-side utilities
 * Include via: <script src="/tjai/static/tjai/tjai-utils.js"></script>
 *
 * Date/time formatting is server-side (tjai_utils.py). This file is only
 * for things that genuinely need client-side computation (live timers).
 */

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
