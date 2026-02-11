/**
 * tjai Gmail Add-on: Add calendar invites to tjai as journal entries.
 *
 * Shows a card in the Gmail sidebar when viewing emails with .ics attachments.
 * The user clicks "Add to tjai" to create a journal entry on etaverse.com.
 */

var TJAI_API_URL = 'https://etaverse.com/tjai/api/add-journal';
var DEFAULT_TIMEZONE = 'America/New_York';

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


function getApiKey_() {
  return PropertiesService.getUserProperties().getProperty('TJAI_API_KEY');
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
  // Prefer the URL with query params (has the password)
  for (var i = 0; i < matches.length; i++) {
    if (matches[i].indexOf('?') !== -1) return matches[i];
  }
  return matches[0];
}


/**
 * Contextual trigger: called when user opens an email.
 */
function onGmailMessage(e) {
  var messageId = e.gmail.messageId;
  var message = GmailApp.getMessageById(messageId);
  var attachments = message.getAttachments();

  var icsAttachments = attachments.filter(function(att) {
    return att.getName().toLowerCase().endsWith('.ics') ||
           att.getContentType().indexOf('text/calendar') !== -1;
  });

  if (icsAttachments.length === 0) {
    return null;
  }

  var gmailUrl = message.getThread().getPermalink();
  var cards = [];
  for (var i = 0; i < icsAttachments.length; i++) {
    var icsText = icsAttachments[i].getDataAsString();
    var events = parseICS_(icsText);
    for (var j = 0; j < events.length; j++) {
      cards.push(buildEventCard_(events[j], gmailUrl));
    }
  }

  return cards;
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
 * Build a Card for one event.
 */
function buildEventCard_(ev, gmailUrl) {
  var header = CardService.newCardHeader()
    .setTitle('Calendar Invite')
    .setSubtitle(ev.summary);

  var section = CardService.newCardSection();

  section.addWidget(
    CardService.newDecoratedText()
      .setTopLabel('Event')
      .setText(ev.summary)
  );

  section.addWidget(
    CardService.newDecoratedText()
      .setTopLabel('Date')
      .setText(ev.displayDate)
  );

  section.addWidget(
    CardService.newDecoratedText()
      .setTopLabel('Time')
      .setText(ev.displayTime + (ev.tzInfo ? ' ' + ev.tzInfo : ''))
  );

  if (ev.zoomUrl) {
    section.addWidget(
      CardService.newDecoratedText()
        .setTopLabel('Zoom')
        .setText(ev.zoomUrl)
    );
  }

  if (ev.location && ev.location !== ev.zoomUrl) {
    section.addWidget(
      CardService.newDecoratedText()
        .setTopLabel('Location')
        .setText(ev.location)
    );
  }

  if (ev.tzWarning) {
    section.addWidget(
      CardService.newDecoratedText()
        .setTopLabel('Warning')
        .setText(ev.tzWarning)
    );
  }

  var action = CardService.newAction()
    .setFunctionName('addToTjai')
    .setParameters({
      title: ev.summary,
      event_timestamp: String(ev.timestamp),
      zoom_url: ev.zoomUrl || '',
      location: (ev.location && ev.location !== ev.zoomUrl) ? ev.location : '',
      gmail_url: gmailUrl || ''
    });

  section.addWidget(
    CardService.newTextButton()
      .setText('Add to tjai')
      .setOnClickAction(action)
  );

  return CardService.newCardBuilder()
    .setHeader(header)
    .addSection(section)
    .build();
}


/**
 * Action handler: POST event to tjai server.
 */
function addToTjai(e) {
  var params = e.commonEventObject.parameters;
  var apiKey = getApiKey_();

  if (!apiKey) {
    return CardService.newActionResponseBuilder()
      .setNotification(
        CardService.newNotification().setText('API key not set. Run setApiKey first.')
      )
      .build();
  }

  var payload = {
    title: params.title,
    event_timestamp: parseFloat(params.event_timestamp),
    zoom_url: params.zoom_url || '',
    location: params.location || '',
    gmail_url: params.gmail_url || ''
  };

  var options = {
    method: 'post',
    contentType: 'application/json',
    headers: { 'Authorization': 'Bearer ' + apiKey },
    payload: JSON.stringify(payload),
    muteHttpExceptions: true
  };

  var response = UrlFetchApp.fetch(TJAI_API_URL, options);
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
    .setNotification(
      CardService.newNotification().setText(message)
    )
    .build();
}


/**
 * Utility: set API key in user properties.
 * Run once from the Apps Script editor: Run > setApiKey
 */
function setApiKey() {
  PropertiesService.getUserProperties().setProperty(
    'TJAI_API_KEY',
    'REPLACE_WITH_ACTUAL_KEY'
  );
  Logger.log('API key set.');
}
