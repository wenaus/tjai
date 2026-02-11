/**
 * tjai Gmail Add-on: Add calendar invites to tjai as journal entries.
 *
 * Shows a card in the Gmail sidebar when viewing emails with .ics attachments.
 * The user clicks "Add to tjai" to create a journal entry on etaverse.com.
 */

var TJAI_API_URL = 'https://etaverse.com/tjai/api/add-journal';
var DEFAULT_TIMEZONE = 'America/New_York';


function getApiKey_() {
  return PropertiesService.getUserProperties().getProperty('TJAI_API_KEY');
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
           att.getContentType() === 'text/calendar';
  });

  if (icsAttachments.length === 0) {
    return null;
  }

  var cards = [];
  for (var i = 0; i < icsAttachments.length; i++) {
    var icsText = icsAttachments[i].getDataAsString();
    var events = parseICS_(icsText);
    for (var j = 0; j < events.length; j++) {
      cards.push(buildEventCard_(events[j]));
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
  icsText = icsText.replace(/\r\n[ \t]/g, '');

  var blocks = icsText.split('BEGIN:VEVENT');
  for (var i = 1; i < blocks.length; i++) {
    var block = blocks[i].split('END:VEVENT')[0];
    var ev = {};

    ev.summary = getICSField_(block, 'SUMMARY');
    ev.location = getICSField_(block, 'LOCATION');
    ev.description = getICSField_(block, 'DESCRIPTION');

    var dtRaw = getICSFieldRaw_(block, 'DTSTART');
    if (dtRaw) {
      var parsed = parseICSDateTime_(dtRaw.params, dtRaw.value);
      ev.timestamp = parsed.timestamp;
      ev.displayDate = parsed.displayDate;
      ev.displayTime = parsed.displayTime;
    }

    if (ev.summary && ev.timestamp) {
      if (parsed.tzWarning) ev.tzWarning = parsed.tzWarning;
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
 * Parse ICS datetime with timezone handling.
 * Returns {timestamp: Unix seconds, displayDate: "Sat Feb 15, 2026", displayTime: "14:30"}.
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
    result.displayTime = pad_(hour) + ':' + pad_(minute);
  }

  // Determine the timezone and create a Date
  var isUTC = value.endsWith('Z');

  if (isUTC) {
    // UTC time
    var d = new Date(Date.UTC(year, month, day, hour, minute, 0));
    result.timestamp = d.getTime() / 1000;
  } else if (params && params.indexOf('TZID=') !== -1) {
    // Explicit timezone -- pass TZID directly to Apps Script (supports all IANA names).
    // If the name is unrecognized, report it and fall back to Eastern.
    var tzMatch = params.match(/TZID=([^;:]+)/);
    var tzName = tzMatch ? tzMatch[1].replace(/^"(.*)"$/, '$1') : DEFAULT_TIMEZONE;
    try {
      result.timestamp = dateInTimezone_(year, month, day, hour, minute, tzName);
    } catch (e) {
      result.tzWarning = 'Unknown timezone "' + tzName + '", using Eastern';
      result.timestamp = dateInTimezone_(year, month, day, hour, minute, DEFAULT_TIMEZONE);
    }
  } else {
    // No timezone specified -- assume Eastern
    result.timestamp = dateInTimezone_(year, month, day, hour, minute, DEFAULT_TIMEZONE);
  }

  // Display date in Eastern
  var displayD = new Date(result.timestamp * 1000);
  var formatted = Utilities.formatDate(displayD, DEFAULT_TIMEZONE, 'EEE MMM d, yyyy');
  result.displayDate = formatted;

  if (hasTime) {
    result.displayTime = Utilities.formatDate(displayD, DEFAULT_TIMEZONE, 'HH:mm');
  }

  return result;
}


/**
 * Convert a local datetime in a given timezone to a Unix timestamp.
 * Uses Apps Script's Utilities.formatDate for timezone-aware formatting,
 * and constructs the Date via ISO string parsing.
 */
function dateInTimezone_(year, month, day, hour, minute, tzName) {
  // Build an ISO string for the target date/time
  var isoStr = year + '-' + pad_(month + 1) + '-' + pad_(day) + 'T' +
               pad_(hour) + ':' + pad_(minute) + ':00';

  // Use Utilities.newDate which respects timezone via the session timezone,
  // but we need a different approach. Create a temporary Date and use
  // Utilities to compute the offset.

  // Approach: use the ScriptApp timezone trick.
  // Create a date at the given wall-clock time in UTC first.
  var utcDate = new Date(Date.UTC(year, month, day, hour, minute, 0));

  // Format that UTC date as if it were in the target timezone to find the offset
  var utcFormatted = Utilities.formatDate(utcDate, 'UTC', "yyyy-MM-dd'T'HH:mm:ss");
  var tzFormatted = Utilities.formatDate(utcDate, tzName, "yyyy-MM-dd'T'HH:mm:ss");

  // Parse both back to get the offset in ms
  var utcMs = parseDateStr_(utcFormatted);
  var tzMs = parseDateStr_(tzFormatted);
  var offsetMs = tzMs - utcMs;  // positive if tz is ahead of UTC

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
function buildEventCard_(ev) {
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
      .setText(ev.displayTime)
  );

  if (ev.location) {
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
      location: ev.location || ''
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
    location: params.location || ''
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
  var body = JSON.parse(response.getContentText());

  var message;
  if (code === 200 && body.status === 'ok') {
    message = 'Added: ' + body.content;
  } else {
    message = 'Error: ' + (body.error || 'HTTP ' + code);
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
