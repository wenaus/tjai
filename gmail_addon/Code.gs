/**
 * tjai Gmail Add-on: Add calendar invites to tjai as journal entries.
 *
 * Shows a card in the Gmail sidebar when viewing emails with .ics attachments
 * or meeting details in the email subject/body.
 * The user clicks "Add to tjai" to create a journal entry on etaverse.com.
 *
 * AI: After editing this file, give the user the Apps Script editor URL to paste it:
 * https://script.google.com/home/projects/18IPT5WjVYnsecm_j9Sv8LSsgbYi9hDM49Pjxc48rWH9tGNLbaN4Fq4jO/edit
 */

var TJAI_API_URL = 'https://etaverse.com/tjai/api/add-journal';
var TJAI_ENTRY_URL = 'https://etaverse.com/tjai/api/add-entry';
var DEFAULT_TIMEZONE = 'America/New_York';

// Country-code TLD → default timezone for sender (when no explicit tz in text).
// Only single-timezone countries. Multi-tz countries (US, CA, AU, BR, RU) omitted.
var COUNTRY_TLD_TZ_ = {
  'ch': 'Europe/Zurich',
  'fr': 'Europe/Paris',
  'uk': 'Europe/London',
  'de': 'Europe/Berlin',
  'it': 'Europe/Rome',
  'es': 'Europe/Madrid',
  'nl': 'Europe/Amsterdam',
  'be': 'Europe/Brussels',
  'se': 'Europe/Stockholm',
  'no': 'Europe/Oslo',
  'dk': 'Europe/Copenhagen',
  'pl': 'Europe/Warsaw',
  'cz': 'Europe/Prague',
  'at': 'Europe/Vienna',
  'jp': 'Asia/Tokyo',
  'kr': 'Asia/Seoul',
  'in': 'Asia/Kolkata',
  'il': 'Asia/Jerusalem',
  'nz': 'Pacific/Auckland',
};

/**
 * Extract default timezone from sender email domain's country TLD.
 * Returns IANA timezone or DEFAULT_TIMEZONE if no match.
 */
function senderTimezone_(fromEmail) {
  if (!fromEmail) return DEFAULT_TIMEZONE;
  var match = fromEmail.match(/@[^>]*\.([a-z]{2})(?:\s*>?\s*)$/i);
  if (match && COUNTRY_TLD_TZ_[match[1].toLowerCase()]) {
    return COUNTRY_TLD_TZ_[match[1].toLowerCase()];
  }
  return DEFAULT_TIMEZONE;
}

// Microsoft Windows timezone names → IANA. This is a finite, documented set
// (https://learn.microsoft.com/en-us/windows-hardware/manufacture/desktop/default-time-zones).
// Outlook/Exchange calendar invites use these in VTIMEZONE TZID fields.
var WINDOWS_TZ_ = {
  // North America
  'Eastern Standard Time': 'America/New_York',
  'Eastern Daylight Time': 'America/New_York',
  'US Eastern Standard Time': 'America/Indiana/Indianapolis',
  'Central Standard Time': 'America/Chicago',
  'Central Daylight Time': 'America/Chicago',
  'Mountain Standard Time': 'America/Denver',
  'Mountain Daylight Time': 'America/Denver',
  'US Mountain Standard Time': 'America/Phoenix',
  'Pacific Standard Time': 'America/Los_Angeles',
  'Pacific Daylight Time': 'America/Los_Angeles',
  'Alaskan Standard Time': 'America/Anchorage',
  'Hawaiian Standard Time': 'Pacific/Honolulu',
  'Newfoundland Standard Time': 'America/St_Johns',
  'Atlantic Standard Time': 'America/Halifax',
  'Canada Central Standard Time': 'America/Regina',
  'Pacific Standard Time (Mexico)': 'America/Tijuana',
  'Mountain Standard Time (Mexico)': 'America/Chihuahua',
  'Central Standard Time (Mexico)': 'America/Mexico_City',
  // Central/South America
  'SA Pacific Standard Time': 'America/Bogota',
  'SA Eastern Standard Time': 'America/Cayenne',
  'SA Western Standard Time': 'America/La_Paz',
  'E. South America Standard Time': 'America/Sao_Paulo',
  'Central America Standard Time': 'America/Guatemala',
  'Venezuela Standard Time': 'America/Caracas',
  'Argentina Standard Time': 'America/Buenos_Aires',
  'Montevideo Standard Time': 'America/Montevideo',
  // Europe
  'GMT Standard Time': 'Europe/London',
  'Greenwich Standard Time': 'Atlantic/Reykjavik',
  'W. Europe Standard Time': 'Europe/Berlin',
  'Central Europe Standard Time': 'Europe/Budapest',
  'Central European Standard Time': 'Europe/Warsaw',
  'Romance Standard Time': 'Europe/Paris',
  'E. Europe Standard Time': 'Europe/Bucharest',
  'FLE Standard Time': 'Europe/Kiev',
  'GTB Standard Time': 'Europe/Athens',
  'Russian Standard Time': 'Europe/Moscow',
  'Turkey Standard Time': 'Europe/Istanbul',
  'Belarus Standard Time': 'Europe/Minsk',
  // Middle East
  'Israel Standard Time': 'Asia/Jerusalem',
  'Jordan Standard Time': 'Asia/Amman',
  'Middle East Standard Time': 'Asia/Beirut',
  'Arabian Standard Time': 'Asia/Dubai',
  'Arab Standard Time': 'Asia/Riyadh',
  'Iran Standard Time': 'Asia/Tehran',
  // Asia
  'India Standard Time': 'Asia/Kolkata',
  'Sri Lanka Standard Time': 'Asia/Colombo',
  'Nepal Standard Time': 'Asia/Kathmandu',
  'Central Asia Standard Time': 'Asia/Almaty',
  'West Asia Standard Time': 'Asia/Tashkent',
  'Bangladesh Standard Time': 'Asia/Dhaka',
  'SE Asia Standard Time': 'Asia/Bangkok',
  'China Standard Time': 'Asia/Shanghai',
  'Singapore Standard Time': 'Asia/Singapore',
  'Taipei Standard Time': 'Asia/Taipei',
  'W. Australia Standard Time': 'Australia/Perth',
  'Tokyo Standard Time': 'Asia/Tokyo',
  'Korea Standard Time': 'Asia/Seoul',
  'Afghanistan Standard Time': 'Asia/Kabul',
  'Pakistan Standard Time': 'Asia/Karachi',
  'Myanmar Standard Time': 'Asia/Rangoon',
  // Australia / Pacific
  'AUS Eastern Standard Time': 'Australia/Sydney',
  'AUS Central Standard Time': 'Australia/Darwin',
  'Cen. Australia Standard Time': 'Australia/Adelaide',
  'E. Australia Standard Time': 'Australia/Brisbane',
  'Tasmania Standard Time': 'Australia/Hobart',
  'New Zealand Standard Time': 'Pacific/Auckland',
  'Fiji Standard Time': 'Pacific/Fiji',
  'Tonga Standard Time': 'Pacific/Tongatapu',
  'Samoa Standard Time': 'Pacific/Apia',
  // Africa
  'E. Africa Standard Time': 'Africa/Nairobi',
  'South Africa Standard Time': 'Africa/Johannesburg',
  'W. Central Africa Standard Time': 'Africa/Lagos',
  'Egypt Standard Time': 'Africa/Cairo',
  'Morocco Standard Time': 'Africa/Casablanca',
  // Atlantic
  'Azores Standard Time': 'Atlantic/Azores',
  'Cape Verde Standard Time': 'Atlantic/Cape_Verde',
  // UTC
  'UTC': 'Etc/UTC',
  'Coordinated Universal Time': 'Etc/UTC',
  'GMT': 'Etc/GMT'
};

// Month name → JS month index (0-based).
var MONTHS_ = {
  'january': 0, 'february': 1, 'march': 2, 'april': 3, 'may': 4, 'june': 5,
  'july': 6, 'august': 7, 'september': 8, 'october': 9, 'november': 10, 'december': 11,
  'jan': 0, 'feb': 1, 'mar': 2, 'apr': 3, 'jun': 5, 'jul': 6, 'aug': 7,
  'sep': 8, 'oct': 9, 'nov': 10, 'dec': 11
};

// Common timezone abbreviations → IANA.
var TZ_ABBREV_ = {
  'EST': 'America/New_York', 'EDT': 'America/New_York', 'ET': 'America/New_York',
  'CST': 'America/Chicago', 'CDT': 'America/Chicago', 'CT': 'America/Chicago',
  'MST': 'America/Denver', 'MDT': 'America/Denver', 'MT': 'America/Denver',
  'PST': 'America/Los_Angeles', 'PDT': 'America/Los_Angeles', 'PT': 'America/Los_Angeles',
  'UTC': 'Etc/UTC', 'GMT': 'Etc/GMT',
  'CET': 'Europe/Zurich', 'CEST': 'Europe/Zurich'
};

var MONTH_PAT_ = 'January|February|March|April|May|June|July|August|September|October|November|December|Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sep|Oct|Nov|Dec';


function getApiKey_() {
  return PropertiesService.getUserProperties().getProperty('TJAI_API_KEY');
}


// ============================================================
// Extraction functions for body-parse fallback (no ICS)
// ============================================================

/**
 * Strip email signature from plain text body.
 * Signatures start with "-- \n" (RFC 3676) or "\n--\n" (common variant).
 * Also catches "---------- Forwarded message" boundaries.
 * Returns body text up to (not including) the signature.
 */
function stripSignature_(text) {
  if (!text) return text;
  // RFC 3676 sig separator: "-- " on its own line (with trailing space)
  // Common variant: "--" on its own line (no trailing space)
  // Handle both \n and \r\n line endings
  var match = text.match(/\r?\n-- ?\r?\n/);
  if (match) return text.substring(0, match.index);
  return text;
}

/**
 * Extract the best Zoom URL from text (location or description).
 * Recognizes zoom.us, *.zoom.us, zoomgov.com, *.zoomgov.com.
 * Preserves full query string including ?pwd= password parameter.
 */
function extractZoomUrl_(text) {
  if (!text) return null;
  var regex = /https?:\/\/[a-zA-Z0-9.-]*(?:zoom\.us|zoomgov\.com)\/[^\s<>"\\]+/gi;
  var matches = text.match(regex);
  if (!matches) return null;
  // Clean Proofpoint URL Defense tracking suffixes (BNL email security)
  // Pattern: __;!!<tracking>$ or __;!!<tracking>$  appended to URLs
  for (var i = 0; i < matches.length; i++) {
    matches[i] = matches[i].replace(/__;!![^]*$/, '');
  }
  // Prefer the URL with query params (has the password)
  for (var i = 0; i < matches.length; i++) {
    if (matches[i].indexOf('?') !== -1) return matches[i];
  }
  return matches[0];
}


/**
 * Extract the first Indico event URL from text.
 * Matches https://indico.DOMAIN/event/ID/ and https://indico.DOMAIN/e/ID
 */
function extractIndicoUrl_(text) {
  if (!text) return null;
  var regex = /https?:\/\/indico\.[a-zA-Z0-9.-]+\/(?:event|e)\/\d+\/?/gi;
  var matches = text.match(regex);
  return matches ? matches[0] : null;
}


/**
 * Extract meeting title. Tries subject first, body as backup.
 * From subject: strips [[list tags]], Re:/Fwd:, and trailing date/time.
 * From body: looks for a line starting with "Subject:" or "Meeting:".
 */
function extractMeetingTitle_(subject, body) {
  if (subject) {
    // Strip mailing list tags like [[List Name]]
    var title = subject.replace(/\[\[[^\]]*\]\]\s*/g, '');
    // Strip Re: Fwd: Fw: prefixes
    title = title.replace(/^(?:Re|Fwd|Fw)\s*:\s*/gi, '');
    // Strip trailing date/time: ", Month DD..." or " Month 24th at ..."
    var trailRegex = new RegExp(',\\s*(?:' + MONTH_PAT_ + ')\\s+\\d.*$', 'i');
    var stripped = title.replace(trailRegex, '');
    if (stripped === title) {
      var trailRegex2 = new RegExp('\\s+(?:' + MONTH_PAT_ + ')\\s+\\d{1,2}(?:st|nd|rd|th)?\\s.*$', 'i');
      stripped = title.replace(trailRegex2, '');
    }
    // European date order: " - Monday 2 March..." or " - 2 March..."
    if (stripped === title) {
      var trailEu = new RegExp('[\\s-]+(?:(?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday)[,\\s-]+)?\\d{1,2}(?:st|nd|rd|th)?\\s+(?:' + MONTH_PAT_ + ')\\b.*$', 'i');
      stripped = title.replace(trailEu, '');
    }
    title = stripped;
    // Strip trailing prepositions left after date removal ("... on", "... for")
    title = title.replace(/\s+(?:on|for|at|from|-+)\s*$/i, '');
    title = title.trim();
    if (title) return title;
  }

  // Body fallback: look for labeled lines
  if (body) {
    var lines = body.split(/\n/);
    for (var i = 0; i < lines.length; i++) {
      var m = lines[i].match(/^\s*(?:Subject|Meeting|Title)\s*:\s*(.+)/i);
      if (m) {
        var t = m[1].trim();
        if (t) return t;
      }
    }
  }

  return null;
}


/**
 * Extract date and time from text. Tries subject first, body as backup.
 * Looks for patterns like "February 25, 11:00 a.m. (EST)" or
 * "Wednesday, February 25, at 11:00 a.m. (EST)".
 * Returns {timestamp, displayDate, displayTime, tzInfo} or null.
 */
function extractDateTime_(subject, body, msgYear, senderTz) {
  var defaultTz = senderTz || DEFAULT_TIMEZONE;
  // Try subject first
  var result = parseDateTimeText_(subject, msgYear, defaultTz);
  if (result) return result;

  // Body: prefer lines with "Date" label
  if (body) {
    var lines = body.split(/\n/);
    for (var i = 0; i < lines.length; i++) {
      if (/^\s*Date/i.test(lines[i])) {
        result = parseDateTimeText_(lines[i], msgYear, defaultTz);
        if (result) return result;
      }
    }
    // Fall back to any line in body
    result = parseDateTimeText_(body, msgYear, defaultTz);
  }

  return result;
}


/**
 * Parse a date+time from a text string.
 * Handles: [Dayname, ]Month DD[, YYYY][,] [at ]H[:MM] a.m./p.m. [(TZ)]
 * Returns {timestamp, displayDate, displayTime, tzInfo} or null.
 */
function parseDateTimeText_(text, fallbackYear, defaultTz) {
  if (!text) return null;
  defaultTz = defaultTz || DEFAULT_TIMEZONE;

  var regex = new RegExp(
    '(?:(?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday),?\\s*)?' +
    '(' + MONTH_PAT_ + ')' +                              // (1) month
    '\\s+(\\d{1,2})(?:st|nd|rd|th)?' +                       // (2) day + optional ordinal
    '(?:,?\\s*(\\d{4}))?' +                                // (3) optional year
    ',?\\s+(?:(?:at|from)\\s+)?' +                           // separator
    '(\\d{1,2})(?::(\\d{2}))?\\s*' +                       // (4) hour (5) min
    '(a\\.?m\\.?|p\\.?m\\.?|AM|PM)' +                     // (6) am/pm
    '(?:\\s*\\(?(E[SD]T|C[SD]T|M[SD]T|P[SD]T|ET|CT|MT|PT|UTC|GMT|CES?T)\\)?)?',  // (7) tz
    'i'
  );

  var match = text.match(regex);

  // European date order fallback: [Dayname] DD[ordinal] Month [YYYY] ... H[:MM] am/pm [(TZ)]
  if (!match) {
    var euRegex = new RegExp(
      '(?:(?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday),?\\s*)?' +
      '(\\d{1,2})(?:st|nd|rd|th)?\\s+' +                      // (1) day + optional ordinal
      '(' + MONTH_PAT_ + ')' +                                 // (2) month
      '(?:,?\\s*(\\d{4}))?' +                                  // (3) optional year
      '(?:[,\\s\\-]+|\\s+(?:(?:at|from)\\s+))' +                // separator (comma, dash, or "at")
      '(\\d{1,2})(?::(\\d{2}))?\\s*' +                         // (4) hour (5) min
      '(a\\.?m\\.?|p\\.?m\\.?|AM|PM)' +                       // (6) am/pm
      '(?:\\s*\\(?(E[SD]T|C[SD]T|M[SD]T|P[SD]T|ET|CT|MT|PT|UTC|GMT|CES?T)\\)?)?',  // (7) tz
      'i'
    );
    match = text.match(euRegex);
    if (match) {
      // Swap day/month groups to normalize to same layout as US regex
      var dayStr = match[1];
      match[1] = match[2];  // month
      match[2] = dayStr;    // day
    }
  }

  // 24-hour time: EU date order: [Dayname] DD Month [YYYY] [at ]HH:MM [(TZ)]
  var is24h = false;
  if (!match) {
    var eu24Regex = new RegExp(
      '(?:(?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday),?\\s*)?' +
      '(\\d{1,2})(?:st|nd|rd|th)?\\s+' +                      // (1) day
      '(' + MONTH_PAT_ + ')' +                                 // (2) month
      '(?:,?\\s*(\\d{4}))?' +                                  // (3) optional year
      '(?:[,\\s\\-]+|\\s+(?:(?:at|from)\\s+))' +                // separator
      '(\\d{1,2}):(\\d{2})' +                                  // (4) hour (5) min — colon required
      '(?:\\s*\\(?(E[SD]T|C[SD]T|M[SD]T|P[SD]T|ET|CT|MT|PT|UTC|GMT|CES?T)\\)?)?',  // (6) tz
      'i'
    );
    match = text.match(eu24Regex);
    if (match) {
      var dayStr24 = match[1];
      match[1] = match[2];  // month
      match[2] = dayStr24;  // day
      is24h = true;
    }
  }

  // 24-hour time: US date order: [Dayname, ]Month DD[, YYYY] [at ]HH:MM [(TZ)]
  if (!match) {
    var us24Regex = new RegExp(
      '(?:(?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday),?\\s*)?' +
      '(' + MONTH_PAT_ + ')' +                                 // (1) month
      '\\s+(\\d{1,2})(?:st|nd|rd|th)?' +                       // (2) day
      '(?:,?\\s*(\\d{4}))?' +                                  // (3) optional year
      ',?\\s+(?:(?:at|from)\\s+)?' +                             // separator
      '(\\d{1,2}):(\\d{2})' +                                  // (4) hour (5) min — colon required
      '(?:\\s*\\(?(E[SD]T|C[SD]T|M[SD]T|P[SD]T|ET|CT|MT|PT|UTC|GMT|CES?T)\\)?)?',  // (6) tz
      'i'
    );
    match = text.match(us24Regex);
    if (match) is24h = true;
  }

  // Numeric date: [Dayname] [(]MM.DD[.YYYY][)] [at ]H[:MM] AM/PM [(TZ)]
  // Handles "Monday (03.02) at 11 AM ET", "03/02 at 2:30 PM", "3-2-2026 at 10am"
  var isNumeric = false;
  if (!match) {
    var numRegex = new RegExp(
      '(?:(?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday),?\\s*)?' +
      '\\(?' +                                                   // optional opening paren
      '(\\d{1,2})[./\\-](\\d{1,2})' +                           // (1) MM (2) DD
      '(?:[./\\-](\\d{2,4}))?' +                                // (3) optional year
      '\\)?' +                                                   // optional closing paren
      '(?:[,\\s]+|\\s+)(?:(?:at|from)\\s+)?' +                    // separator
      '(\\d{1,2})(?::(\\d{2}))?\\s*' +                           // (4) hour (5) min
      '(a\\.?m\\.?|p\\.?m\\.?|AM|PM)' +                         // (6) am/pm
      '(?:\\s*\\(?(E[SD]T|C[SD]T|M[SD]T|P[SD]T|ET|CT|MT|PT|UTC|GMT|CES?T)\\)?)?',  // (7) tz
      'i'
    );
    match = text.match(numRegex);
    if (match) isNumeric = true;
  }

  // Numeric date with 24-hour time: [Dayname] [(]MM.DD[.YYYY][)] [at ]HH:MM [(TZ)]
  if (!match) {
    var num24Regex = new RegExp(
      '(?:(?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday),?\\s*)?' +
      '\\(?' +                                                   // optional opening paren
      '(\\d{1,2})[./\\-](\\d{1,2})' +                           // (1) MM (2) DD
      '(?:[./\\-](\\d{2,4}))?' +                                // (3) optional year
      '\\)?' +                                                   // optional closing paren
      '(?:[,\\s]+|\\s+)(?:(?:at|from)\\s+)?' +                    // separator
      '(\\d{1,2}):(\\d{2})' +                                   // (4) hour (5) min — colon required
      '(?:\\s*\\(?(E[SD]T|C[SD]T|M[SD]T|P[SD]T|ET|CT|MT|PT|UTC|GMT|CES?T)\\)?)?',  // (6) tz
      'i'
    );
    match = text.match(num24Regex);
    if (match) { isNumeric = true; is24h = true; }
  }

  if (!match) return null;

  // Check for IANA timezone path near the matched time (e.g., "Europe/Zurich", "America/New_York")
  var ianaMatch = text.match(/\b(Africa|America|Antarctica|Asia|Atlantic|Australia|Europe|Indian|Pacific)\/[A-Za-z_]+(?:\/[A-Za-z_]+)?\b/);
  var ianaTz = ianaMatch ? ianaMatch[0] : null;

  // Numeric date: month and day are already numeric, no MONTHS_ lookup needed
  if (isNumeric) {
    var numMonth = parseInt(match[1]) - 1;  // 0-indexed
    var numDay = parseInt(match[2]);
    var numYear = match[3] ? parseInt(match[3]) : fallbackYear;
    if (numYear < 100) numYear += 2000;  // handle 2-digit year
    if (numMonth < 0 || numMonth > 11 || numDay < 1 || numDay > 31) return null;

    var numHour = parseInt(match[4]);
    var numMinute = parseInt(match[5] || '0');

    if (!is24h) {
      var numAmpm = match[6].replace(/\./g, '').toLowerCase();
      if (numAmpm === 'pm' && numHour < 12) numHour += 12;
      if (numAmpm === 'am' && numHour === 12) numHour = 0;
    }

    var numTzGroup = is24h ? match[6] : match[7];
    var numTzName = defaultTz;
    if (numTzGroup && TZ_ABBREV_[numTzGroup.toUpperCase()]) {
      numTzName = TZ_ABBREV_[numTzGroup.toUpperCase()];
    } else if (ianaTz) {
      numTzName = ianaTz;
    }

    var numTimestamp = dateInTimezone_(numYear, numMonth, numDay, numHour, numMinute, numTzName);
    var numDisplayD = new Date(numTimestamp * 1000);
    return {
      timestamp: numTimestamp,
      displayDate: Utilities.formatDate(numDisplayD, DEFAULT_TIMEZONE, 'EEE MMM d, yyyy'),
      displayTime: Utilities.formatDate(numDisplayD, DEFAULT_TIMEZONE, 'HH:mm'),
      tzInfo: Utilities.formatDate(numDisplayD, DEFAULT_TIMEZONE, 'z')
    };
  }

  var month = MONTHS_[match[1].toLowerCase()];
  if (month === undefined) return null;

  var day = parseInt(match[2]);
  var year = match[3] ? parseInt(match[3]) : fallbackYear;
  var hour = parseInt(match[4]);
  var minute = parseInt(match[5] || '0');

  if (!is24h) {
    var ampm = match[6].replace(/\./g, '').toLowerCase();
    if (ampm === 'pm' && hour < 12) hour += 12;
    if (ampm === 'am' && hour === 12) hour = 0;
  }

  // Resolve timezone: explicit in text > IANA path > sender-based default
  var tzGroup = is24h ? match[6] : match[7];
  var tzName = defaultTz;
  if (tzGroup && TZ_ABBREV_[tzGroup.toUpperCase()]) {
    tzName = TZ_ABBREV_[tzGroup.toUpperCase()];
  } else if (ianaTz) {
    tzName = ianaTz;
  }

  var timestamp = dateInTimezone_(year, month, day, hour, minute, tzName);
  var displayD = new Date(timestamp * 1000);
  return {
    timestamp: timestamp,
    displayDate: Utilities.formatDate(displayD, DEFAULT_TIMEZONE, 'EEE MMM d, yyyy'),
    displayTime: Utilities.formatDate(displayD, DEFAULT_TIMEZONE, 'HH:mm'),
    tzInfo: Utilities.formatDate(displayD, DEFAULT_TIMEZONE, 'z')
  };
}


// ============================================================
// All-day event extraction (date without time)
// ============================================================

/**
 * Extract a date (no time) from subject, then body.
 * Returns {timestamp (noon), displayDate} or null.
 */
function extractDateOnly_(subject, body, fallbackYear) {
  var result = parseDateOnly_(subject, fallbackYear);
  if (result) return result;

  if (body) {
    var lines = body.split(/\n/);
    // Prefer lines with "Date" label
    for (var i = 0; i < lines.length; i++) {
      if (/^\s*Date/i.test(lines[i])) {
        result = parseDateOnly_(lines[i], fallbackYear);
        if (result) return result;
      }
    }
    result = parseDateOnly_(body, fallbackYear);
  }
  return result;
}


/**
 * Parse a date (no time required) from text.
 * Handles: Month DD[, YYYY] / DD Month [YYYY]
 * Returns {timestamp (noon Eastern), displayDate} or null.
 */
function parseDateOnly_(text, fallbackYear) {
  if (!text) return null;

  var month, day, year;

  // US order: [Dayname, ]Month DD[, YYYY]
  var usRegex = new RegExp(
    '(?:(?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday),?\\s*)?' +
    '(' + MONTH_PAT_ + ')' +
    '\\s+(\\d{1,2})(?:st|nd|rd|th)?' +
    '(?:,?\\s*(\\d{4}))?',
    'i'
  );
  var match = text.match(usRegex);
  if (match) {
    month = MONTHS_[match[1].toLowerCase()];
    if (month !== undefined) {
      day = parseInt(match[2]);
      year = match[3] ? parseInt(match[3]) : fallbackYear;
      return buildDateOnlyResult_(year, month, day);
    }
  }

  // EU order: [Dayname, ]DD Month [YYYY]
  var euRegex = new RegExp(
    '(?:(?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday),?\\s*)?' +
    '(\\d{1,2})(?:st|nd|rd|th)?\\s+' +
    '(' + MONTH_PAT_ + ')' +
    '(?:,?\\s*(\\d{4}))?',
    'i'
  );
  match = text.match(euRegex);
  if (match) {
    month = MONTHS_[match[2].toLowerCase()];
    if (month !== undefined) {
      day = parseInt(match[1]);
      year = match[3] ? parseInt(match[3]) : fallbackYear;
      return buildDateOnlyResult_(year, month, day);
    }
  }

  return null;
}


/**
 * Build an all-day date result with timestamp at noon Eastern.
 */
function buildDateOnlyResult_(year, month, day) {
  var timestamp = dateInTimezone_(year, month, day, 12, 0, DEFAULT_TIMEZONE);
  var displayD = new Date(timestamp * 1000);
  return {
    timestamp: timestamp,
    displayDate: Utilities.formatDate(displayD, DEFAULT_TIMEZONE, 'EEE MMM d, yyyy')
  };
}


// ============================================================
// ICS extraction
// ============================================================

/**
 * Extract ICS text from raw MIME content.
 * Google Calendar sends invites as inline text/calendar MIME parts
 * that GmailApp.getAttachments() does not return.
 */
function extractICSFromRaw_(rawContent) {
  var match = rawContent.match(/BEGIN:VCALENDAR[\s\S]*?END:VCALENDAR/);
  return match ? match[0] : null;
}


// ============================================================
// Main trigger and card building
// ============================================================

function buildDiagCard_(info) {
  var section = CardService.newCardSection();
  for (var i = 0; i < info.length; i++) {
    section.addWidget(CardService.newDecoratedText().setText(info[i]));
  }
  return CardService.newCardBuilder()
    .setHeader(CardService.newCardHeader().setTitle('tjai diag'))
    .addSection(section)
    .build();
}


/**
 * Contextual trigger: called when user opens an email.
 */
function onGmailMessage(e) {
  var diag = [];
  try {
    var messageId = e.gmail.messageId;
    var message = GmailApp.getMessageById(messageId);
    var thread = message.getThread();
    var messages = thread.getMessages();

    diag.push('Thread messages: ' + messages.length);

    // Search backwards (most recent first) for a message with ICS data
    var icsTexts = [];
    for (var m = messages.length - 1; m >= 0; m--) {
      var msg = messages[m];
      var attachments = msg.getAttachments();
      var icsAttachments = attachments.filter(function(att) {
        return att.getName().toLowerCase().endsWith('.ics') ||
               att.getContentType().indexOf('text/calendar') !== -1;
      });

      if (icsAttachments.length > 0) {
        diag.push('ICS found in msg[' + m + '] via attachment (' + icsAttachments.length + ')');
        for (var i = 0; i < icsAttachments.length; i++) {
          icsTexts.push(icsAttachments[i].getDataAsString());
        }
        break;
      }

      var rawContent = msg.getRawContent();
      var icsFromRaw = extractICSFromRaw_(rawContent);
      if (icsFromRaw) {
        diag.push('ICS found in msg[' + m + '] via raw MIME (' + icsFromRaw.length + ' chars)');
        icsTexts.push(icsFromRaw);
        break;
      }
    }

    diag.push('ICS texts: ' + icsTexts.length);

    var gmailUrl = thread.getPermalink();

    // Try to extract event info for journal prefill
    var prefill = null;  // {content, date, time}

    // --- ICS path ---
    if (icsTexts.length > 0) {
      for (var i = 0; i < icsTexts.length; i++) {
        var events = parseICS_(icsTexts[i]);
        if (events.length > 0) {
          prefill = eventToPrefill_(events[0]);
          diag.push('Prefill from ICS: ' + prefill.content);
          break;
        }
      }
    }

    // --- Body-parse fallback ---
    if (!prefill) {
      diag.push('Trying body parse');
      var tryMsgs = [message];
      if (messages.length > 1 && messages[0].getId() !== message.getId()) {
        tryMsgs.push(messages[0]);
      }

      for (var t = 0; t < tryMsgs.length; t++) {
        var tryMsg = tryMsgs[t];
        var subject = tryMsg.getSubject();
        var body = tryMsg.getPlainBody();
        var msgYear = tryMsg.getDate().getFullYear();
        var senderTz = senderTimezone_(tryMsg.getFrom());
        var bodyNoSig = stripSignature_(body);

        var title = extractMeetingTitle_(subject, bodyNoSig);
        if (!title) { diag.push('msg[' + t + ']: no title'); continue; }

        var dt = extractDateTime_(subject, bodyNoSig, msgYear, senderTz);
        if (dt) {
          var zoomUrl = extractZoomUrl_(bodyNoSig) || extractZoomUrl_(subject);
          var indicoUrl = extractIndicoUrl_(bodyNoSig) || extractIndicoUrl_(subject);
          prefill = eventToPrefill_({
            summary: title, timestamp: dt.timestamp,
            displayTime: dt.displayTime, zoomUrl: zoomUrl, indicoUrl: indicoUrl
          });
          diag.push('Prefill from body: ' + prefill.content);
          break;
        }

        // All-day fallback: date only + Indico
        var indicoUrl2 = extractIndicoUrl_(bodyNoSig) || extractIndicoUrl_(subject);
        if (indicoUrl2) {
          var dateOnly = extractDateOnly_(subject, bodyNoSig, msgYear);
          if (dateOnly) {
            prefill = eventToPrefill_({
              summary: title, timestamp: dateOnly.timestamp,
              displayTime: 'All day', indicoUrl: indicoUrl2
            });
            diag.push('Prefill from allday: ' + prefill.content);
            break;
          }
        }
      }
    }

    // Even if full event detection failed, grab any zoom/indico URLs for the journal prefill
    if (!prefill) {
      var scanBody = stripSignature_(message.getPlainBody());
      var scanSubj = message.getSubject() || '';
      var foundZoom = extractZoomUrl_(scanBody) || extractZoomUrl_(scanSubj);
      var foundIndico = extractIndicoUrl_(scanBody) || extractIndicoUrl_(scanSubj);
      if (foundZoom || foundIndico) {
        var parts = [];
        if (foundZoom) parts.push('[zoom](' + foundZoom + ')');
        if (foundIndico) parts.push('[indico](' + foundIndico + ')');
        prefill = { content: parts.join(' '), date: Utilities.formatDate(new Date(), DEFAULT_TIMEZONE, 'yyyyMMdd'), time: '' };
      }
    }

    // Build the single card with all sections
    var subj = message.getSubject() || '';
    var cleanTitle = subj.replace(/\[\[[^\]]*\]\]\s*/g, '').replace(/^(?:Re|Fwd|Fw)\s*:\s*/gi, '').trim();
    return [buildMainCard_(cleanTitle || subj, gmailUrl, prefill)];
  } catch (err) {
    diag.push('ERROR: ' + err.message);
    return [buildDiagCard_(diag)];
  }
}


/**
 * Convert a detected event object into journal prefill data.
 */
function eventToPrefill_(ev) {
  var parts = [ev.summary];
  if (ev.location && ev.location !== ev.zoomUrl) parts.push('@ ' + ev.location);
  if (ev.zoomUrl) parts.push('[zoom](' + ev.zoomUrl + ')');
  if (ev.indicoUrl) parts.push('[indico](' + ev.indicoUrl + ')');

  // Convert timestamp to YYYYMMDD and HHMM in local timezone
  var d = new Date(ev.timestamp * 1000);
  var date = Utilities.formatDate(d, DEFAULT_TIMEZONE, 'yyyyMMdd');
  var time = '';
  if (ev.displayTime && ev.displayTime !== 'All day') {
    time = Utilities.formatDate(d, DEFAULT_TIMEZONE, 'HHmm');
  }

  return { content: parts.join(' '), date: date, time: time };
}


/**
 * Parse ICS text and extract VEVENT fields.
 */
function parseICS_(icsText) {
  var events = [];
  // Unfold long lines (RFC 5545: continuation with leading space/tab)
  icsText = icsText.replace(/\r?\n[ \t]/g, '');

  var blocks = icsText.split('BEGIN:VEVENT');
  for (var i = 1; i < blocks.length; i++) {
    var block = blocks[i].split('END:VEVENT')[0];
    var ev = {};

    ev.summary = getICSField_(block, 'SUMMARY');
    ev.location = getICSField_(block, 'LOCATION');
    ev.description = getICSField_(block, 'DESCRIPTION');

    // Extract Zoom URL from both fields; prefer the longer (more complete) one
    var zoomLoc = extractZoomUrl_(ev.location);
    var zoomDesc = extractZoomUrl_(ev.description);
    if (zoomLoc && zoomDesc) {
      ev.zoomUrl = zoomDesc.length > zoomLoc.length ? zoomDesc : zoomLoc;
    } else {
      ev.zoomUrl = zoomLoc || zoomDesc;
    }

    // Extract Indico URL from description or location
    ev.indicoUrl = extractIndicoUrl_(ev.description) || extractIndicoUrl_(ev.location);

    var dtRaw = getICSFieldRaw_(block, 'DTSTART');
    if (dtRaw) {
      var parsed = parseICSDateTime_(dtRaw.params, dtRaw.value);
      ev.timestamp = parsed.timestamp;
      ev.displayDate = parsed.displayDate;
      ev.displayTime = parsed.displayTime;
    }

    if (ev.summary && ev.timestamp) {
      if (parsed.tzWarning) ev.tzWarning = parsed.tzWarning;
      if (parsed.tzInfo) ev.tzInfo = parsed.tzInfo;
      events.push(ev);
    }
  }
  return events;
}


/**
 * Extract a field value from an ICS block.
 * Handles FIELDNAME;params:value format.
 */
function getICSField_(block, fieldName) {
  var regex = new RegExp('^' + fieldName + '(?:;[^:]*)?:(.*)$', 'm');
  var match = block.match(regex);
  if (!match) return null;
  return match[1].replace(/\\n/g, '\n').replace(/\\,/g, ',').replace(/\\\\/g, '\\').trim();
}


/**
 * Extract field with params preserved (for DTSTART timezone handling).
 * Returns {params: "TZID=...", value: "20260215T143000"} or null.
 */
function getICSFieldRaw_(block, fieldName) {
  var regex = new RegExp('^' + fieldName + '(?:;([^:]*))?:(.*)$', 'm');
  var match = block.match(regex);
  if (!match) return null;
  return { params: match[1] || '', value: match[2].trim() };
}


/**
 * Resolve a TZID to an IANA timezone name.
 * Handles: IANA names (pass through), Windows names (map), unrecognized (throw).
 */
function resolveTimezone_(tzName) {
  // Check Windows timezone map first
  if (WINDOWS_TZ_[tzName]) {
    return WINDOWS_TZ_[tzName];
  }

  // Might be an IANA name already -- validate via probe.
  // Utilities.formatDate silently treats unrecognized names as UTC (no error thrown).
  // Detect by probing: format a known date in both UTC and the claimed timezone.
  // If results are identical and it's not actually UTC, the name is unrecognized.
  var probe = new Date(Date.UTC(2026, 6, 1, 12, 0, 0));  // July 1 noon UTC
  var probeUtc = Utilities.formatDate(probe, 'UTC', "yyyy-MM-dd'T'HH:mm:ss");
  var probeTz = Utilities.formatDate(probe, tzName, "yyyy-MM-dd'T'HH:mm:ss");
  if (probeUtc === probeTz && tzName !== 'UTC' && tzName !== 'GMT' &&
      tzName !== 'Etc/UTC' && tzName !== 'Etc/GMT') {
    throw new Error('Unrecognized timezone: ' + tzName);
  }

  return tzName;
}


/**
 * Parse ICS datetime with timezone handling.
 * Returns {timestamp, displayDate, displayTime, tzWarning?}.
 */
function parseICSDateTime_(params, value) {
  var result = { timestamp: null, displayDate: '', displayTime: 'All day' };

  value = value.replace(/\s+$/, '');
  if (value.length < 8) return result;

  var year = parseInt(value.substring(0, 4));
  var month = parseInt(value.substring(4, 6)) - 1;
  var day = parseInt(value.substring(6, 8));

  var hasTime = value.length >= 15 && value.charAt(8) === 'T';
  var hour = 12, minute = 0;

  if (hasTime) {
    hour = parseInt(value.substring(9, 11));
    minute = parseInt(value.substring(11, 13));
  }

  // Determine the timezone and create a Date
  var isUTC = value.endsWith('Z');

  if (isUTC) {
    // UTC time
    var d = new Date(Date.UTC(year, month, day, hour, minute, 0));
    result.timestamp = d.getTime() / 1000;
  } else if (params && params.indexOf('TZID=') !== -1) {
    // Explicit timezone
    var tzMatch = params.match(/TZID=([^;:]+)/);
    var rawTzName = tzMatch ? tzMatch[1].replace(/^"(.*)"$/, '$1') : DEFAULT_TIMEZONE;
    try {
      var resolvedTz = resolveTimezone_(rawTzName);
      result.timestamp = dateInTimezone_(year, month, day, hour, minute, resolvedTz);
    } catch (e) {
      result.tzWarning = 'Unknown timezone "' + rawTzName + '", using Eastern';
      result.timestamp = dateInTimezone_(year, month, day, hour, minute, DEFAULT_TIMEZONE);
    }
  } else {
    // No timezone specified -- assume Eastern
    result.timestamp = dateInTimezone_(year, month, day, hour, minute, DEFAULT_TIMEZONE);
  }

  // Display date/time in Eastern
  var displayD = new Date(result.timestamp * 1000);
  result.displayDate = Utilities.formatDate(displayD, DEFAULT_TIMEZONE, 'EEE MMM d, yyyy');
  result.tzInfo = Utilities.formatDate(displayD, DEFAULT_TIMEZONE, 'z');

  if (hasTime) {
    result.displayTime = Utilities.formatDate(displayD, DEFAULT_TIMEZONE, 'HH:mm');
  }

  return result;
}


/**
 * Convert a local datetime in a given IANA timezone to a Unix timestamp.
 * tzName must be a valid IANA name (use resolveTimezone_ first).
 */
function dateInTimezone_(year, month, day, hour, minute, tzName) {
  // Create a date at the given wall-clock time in UTC first.
  var utcDate = new Date(Date.UTC(year, month, day, hour, minute, 0));

  // Format in both UTC and target timezone to find the offset
  var utcFormatted = Utilities.formatDate(utcDate, 'UTC', "yyyy-MM-dd'T'HH:mm:ss");
  var tzFormatted = Utilities.formatDate(utcDate, tzName, "yyyy-MM-dd'T'HH:mm:ss");

  // Parse both back to get the offset in ms
  var utcMs = parseDateStr_(utcFormatted);
  var tzMs = parseDateStr_(tzFormatted);
  var offsetMs = tzMs - utcMs;

  // The actual UTC time = wall clock time - offset
  var actualUtcMs = utcDate.getTime() - offsetMs;
  return actualUtcMs / 1000;
}


/**
 * Parse "yyyy-MM-ddTHH:mm:ss" to milliseconds (treating as UTC).
 */
function parseDateStr_(str) {
  var parts = str.split('T');
  var dateParts = parts[0].split('-');
  var timeParts = parts[1].split(':');
  return Date.UTC(
    parseInt(dateParts[0]), parseInt(dateParts[1]) - 1, parseInt(dateParts[2]),
    parseInt(timeParts[0]), parseInt(timeParts[1]), parseInt(timeParts[2])
  );
}


function pad_(n) {
  return n < 10 ? '0' + n : '' + n;
}


/**
 * Build a Card for one event, with memory section at bottom.
 */
/**
 * Build the main card with all three sections: journal, bookmark, memory.
 * journalPrefill is optional {content, date, time} from auto-detected events.
 */
function buildMainCard_(emailTitle, gmailUrl, journalPrefill) {
  var header = CardService.newCardHeader()
    .setTitle('tjai');

  return CardService.newCardBuilder()
    .setHeader(header)
    .addSection(buildJournalSection_(gmailUrl, journalPrefill))
    .addSection(buildBookmarkSection_(emailTitle, gmailUrl))
    .addSection(buildMemorySection_(gmailUrl))
    .build();
}


/**
 * Journal entry section — prefilled from auto-detection or empty with today's date.
 */
function buildJournalSection_(gmailUrl, prefill) {
  var section = CardService.newCardSection()
    .setHeader('JOURNAL ENTRY');

  section.addWidget(
    CardService.newTextInput()
      .setFieldName('jrn_content')
      .setTitle('Content')
      .setValue(prefill ? prefill.content : '')
      .setMultiline(true)
  );

  var today = Utilities.formatDate(new Date(), DEFAULT_TIMEZONE, 'yyyyMMdd');
  section.addWidget(
    CardService.newTextInput()
      .setFieldName('jrn_date')
      .setTitle('Date')
      .setHint('YYYYMMDD')
      .setValue(prefill ? prefill.date : today)
  );

  section.addWidget(
    CardService.newTextInput()
      .setFieldName('jrn_time')
      .setTitle('Time (optional)')
      .setHint('HHMM (e.g. 1430)')
      .setValue(prefill ? prefill.time : '')
  );

  section.addWidget(
    CardService.newTextInput()
      .setFieldName('jrn_tags')
      .setTitle('Tags, context')
      .setHint(':tag =context')
  );

  var action = CardService.newAction()
    .setFunctionName('addJournal')
    .setParameters({ gmail_url: gmailUrl || '' });

  section.addWidget(
    CardService.newTextButton()
      .setText('Add journal entry')
      .setOnClickAction(action)
  );

  return section;
}


/**
 * Bookmark section — title prefilled from email subject.
 */
function buildBookmarkSection_(title, gmailUrl) {
  var section = CardService.newCardSection()
    .setHeader('BOOKMARK');

  section.addWidget(
    CardService.newTextInput()
      .setFieldName('bm_title')
      .setTitle('Title')
      .setValue(title)
  );

  section.addWidget(
    CardService.newTextInput()
      .setFieldName('bm_tags')
      .setTitle('Tags, context')
      .setHint(':tag =context')
  );

  var action = CardService.newAction()
    .setFunctionName('addBookmark')
    .setParameters({ gmail_url: gmailUrl || '' });

  section.addWidget(
    CardService.newTextButton()
      .setText('Save bookmark')
      .setOnClickAction(action)
  );

  return section;
}


/**
 * Memory section — free-form note.
 */
function buildMemorySection_(gmailUrl) {
  var section = CardService.newCardSection()
    .setHeader('MEMORY');

  section.addWidget(
    CardService.newTextInput()
      .setFieldName('mem_content')
      .setTitle('Content')
      .setHint('note text :tag =context')
      .setMultiline(true)
  );

  var action = CardService.newAction()
    .setFunctionName('addMemory')
    .setParameters({ gmail_url: gmailUrl || '' });

  section.addWidget(
    CardService.newTextButton()
      .setText('Add memory')
      .setOnClickAction(action)
  );

  return section;
}


/**
 * Parse memory/tag input: extract :tags and =context from text.
 * "some note :physics =epic" → {content: "some note", tags: ["physics"], context: "epic"}
 */
function parseTagsFromText_(text) {
  var context = null;
  var tags = [];

  // Extract =context (take last one if multiple)
  text = text.replace(/\s+=(\S+)/g, function(_, c) { context = c; return ''; });
  // Extract :tags
  text = text.replace(/\s+:(\S+)/g, function(_, t) { tags.push(t); return ''; });
  // Also handle at start of string
  text = text.replace(/^=(\S+)\s*/g, function(_, c) { context = c; return ''; });
  text = text.replace(/^:(\S+)\s*/g, function(_, t) { tags.push(t); return ''; });

  return { content: text.trim(), tags: tags, context: context };
}


/**
 * Action handler: save email as tjai bookmark.
 */
function addBookmark(e) {
  var params = e.commonEventObject.parameters;
  var formInputs = e.commonEventObject.formInputs || {};
  var apiKey = getApiKey_();
  if (!apiKey) {
    return CardService.newActionResponseBuilder()
      .setNotification(CardService.newNotification().setText('API key not set. Run setApiKey first.'))
      .build();
  }

  var title = (formInputs.bm_title && formInputs.bm_title.stringInputs.value[0]) || '';
  var tagsRaw = (formInputs.bm_tags && formInputs.bm_tags.stringInputs.value[0]) || '';
  var gmailUrl = params.gmail_url || '';

  if (!title.trim()) {
    return CardService.newActionResponseBuilder()
      .setNotification(CardService.newNotification().setText('Title is required'))
      .build();
  }

  // Parse =context from tags field
  var parsed = parseTagsFromText_(' ' + tagsRaw);  // leading space so regex matches
  var content = '[' + title.trim() + '](' + gmailUrl + ')';

  var payload = {
    kind: 'bookmark',
    content: content,
    tags: parsed.tags.join(','),
    context: parsed.context || '',
    source: 'gmail'
  };

  return postToTjai_(TJAI_ENTRY_URL, payload);
}


/**
 * Action handler: create a free-form journal entry.
 */
function addJournal(e) {
  var params = e.commonEventObject.parameters;
  var formInputs = e.commonEventObject.formInputs || {};
  var apiKey = getApiKey_();
  if (!apiKey) {
    return CardService.newActionResponseBuilder()
      .setNotification(CardService.newNotification().setText('API key not set. Run setApiKey first.'))
      .build();
  }

  var content = (formInputs.jrn_content && formInputs.jrn_content.stringInputs.value[0]) || '';
  if (!content.trim()) {
    return CardService.newActionResponseBuilder()
      .setNotification(CardService.newNotification().setText('Content is required'))
      .build();
  }

  var dateStr = (formInputs.jrn_date && formInputs.jrn_date.stringInputs.value[0]) || '';
  if (!dateStr.match(/^\d{8}$/)) {
    return CardService.newActionResponseBuilder()
      .setNotification(CardService.newNotification().setText('Date must be YYYYMMDD'))
      .build();
  }

  var timeStr = (formInputs.jrn_time && formInputs.jrn_time.stringInputs.value[0]) || '';
  timeStr = timeStr.trim();
  if (timeStr && !timeStr.match(/^\d{4}$/)) {
    return CardService.newActionResponseBuilder()
      .setNotification(CardService.newNotification().setText('Time must be HHMM (e.g. 1430)'))
      .build();
  }

  var tagsRaw = (formInputs.jrn_tags && formInputs.jrn_tags.stringInputs.value[0]) || '';
  var gmailUrl = params.gmail_url || '';

  // Append gmail link to content
  var fullContent = content.trim();
  if (gmailUrl) {
    fullContent += ' [gmail](' + gmailUrl + ')';
  }

  var parsed = parseTagsFromText_(' ' + tagsRaw);

  var payload = {
    kind: 'journal',
    content: fullContent,
    event_date: dateStr,
    event_time: timeStr,
    tags: parsed.tags.join(','),
    context: parsed.context || '',
    source: 'gmail'
  };

  return postToTjai_(TJAI_ENTRY_URL, payload);
}


/**
 * Action handler: add a memory note with gmail link.
 */
function addMemory(e) {
  var params = e.commonEventObject.parameters;
  var formInputs = e.commonEventObject.formInputs || {};
  var apiKey = getApiKey_();
  if (!apiKey) {
    return CardService.newActionResponseBuilder()
      .setNotification(CardService.newNotification().setText('API key not set. Run setApiKey first.'))
      .build();
  }

  var raw = (formInputs.mem_content && formInputs.mem_content.stringInputs.value[0]) || '';
  if (!raw.trim()) {
    return CardService.newActionResponseBuilder()
      .setNotification(CardService.newNotification().setText('Enter some text first'))
      .build();
  }

  var parsed = parseTagsFromText_(raw);
  var gmailUrl = params.gmail_url || '';

  // Append gmail link to content
  var fullContent = parsed.content;
  if (gmailUrl) {
    fullContent += ' [gmail](' + gmailUrl + ')';
  }

  var payload = {
    kind: 'memory',
    content: fullContent,
    tags: parsed.tags.join(','),
    context: parsed.context || '',
    source: 'gmail'
  };

  return postToTjai_(TJAI_ENTRY_URL, payload);
}


/**
 * Shared helper: POST payload to a tjai API endpoint.
 */
function postToTjai_(url, payload) {
  var apiKey = getApiKey_();
  var options = {
    method: 'post',
    contentType: 'application/json',
    headers: { 'Authorization': 'Bearer ' + apiKey },
    payload: JSON.stringify(payload),
    muteHttpExceptions: true
  };

  var response = UrlFetchApp.fetch(url, options);
  var code = response.getResponseCode();
  var message;
  try {
    var body = JSON.parse(response.getContentText());
    if (code === 200 && body.status === 'ok') {
      message = 'Added: ' + body.content;
    } else {
      message = 'Error: ' + (body.error || 'HTTP ' + code);
    }
  } catch (err) {
    message = 'Error: HTTP ' + code + ' (non-JSON response)';
  }

  return CardService.newActionResponseBuilder()
    .setNotification(CardService.newNotification().setText(message))
    .build();
}


/**
 * Utility: set API key in user properties. Already done, key is stored.
 * Only needed again if user properties are cleared.
 * Key source: tjai SysConfig DB (key='gmail_addon_api_key')
 * or ~/.env TJAI_GMAIL_ADDON_API_KEY.
 */
function setApiKey(key) {
  if (!key) throw new Error('Usage: setApiKey("your-key-here")');
  PropertiesService.getUserProperties().setProperty('TJAI_API_KEY', key);
  Logger.log('API key set.');
}
