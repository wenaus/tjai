#!/usr/bin/env node
/**
 * Meeting-detail parsing tests for the Gmail add-on (gmail_addon/Code.gs).
 *
 * The add-on runs in Apps Script and cannot be exercised there without a
 * browser, so this loads Code.gs into node with the Apps Script globals
 * stubbed and drives the parsing functions directly. It covers the forms real
 * mail actually uses; a month written "Sept" went unrecognised for as long as
 * abbreviations were enumerated by hand, which failed the whole date parse and
 * left the card with no event.
 *
 *   node scripts/test_gmail_addon_parse.js
 */
const fs = require('fs');
const path = require('path');

const CODE = path.join(__dirname, '..', 'gmail_addon', 'Code.gs');

global.Utilities = {
  formatDate: (d, tz, fmt) => {
    const parts = new Intl.DateTimeFormat('en-CA', {
      timeZone: tz, year: 'numeric', month: '2-digit', day: '2-digit',
      hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false,
    }).formatToParts(d);
    const g = t => parts.find(x => x.type === t).value;
    return fmt.replace(/'([^']*)'/g, '$1')
      .replace('yyyy', g('year')).replace('MM', g('month')).replace('dd', g('day'))
      .replace('HH', g('hour') === '24' ? '00' : g('hour'))
      .replace('mm', g('minute')).replace('ss', g('second'));
  },
};
global.PropertiesService = { getUserProperties: () => ({ getProperty: () => '', setProperty() {} }) };
global.CardService = new Proxy({}, { get: () => new Proxy(function () {}, { get: () => () => ({}), apply: () => ({}) }) });
global.GmailApp = {};
global.UrlFetchApp = {};
global.Session = { getScriptTimeZone: () => 'America/New_York' };

eval(fs.readFileSync(CODE, 'utf8'));

let failed = 0;
const ET = 'America/New_York';

function eq(name, got, want) {
  const ok = got === want;
  if (!ok) failed++;
  console.log(`${ok ? 'PASS' : 'FAIL'}  ${name}` + (ok ? '' : `\n        got  ${got}\n        want ${want}`));
}

function whenET(text, year) {
  const r = parseDateTimeText_(text, year || 2026, null);
  if (!r) return 'NULL';
  return new Date(r.timestamp * 1000).toLocaleString('en-US',
    { timeZone: ET, year: 'numeric', month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' });
}

// Any prefix of three characters or more names the month.
for (const m of ['Sep', 'Sept', 'Septem', 'September', 'SEPT', 'Sept.', 'sep']) {
  eq(`month "${m}"`, whenET(`Friday, ${m} 11th at 1PM ET`), 'Sep 11, 2026, 1:00 PM');
}
for (const [m, want] of [['Jan', 'Jan'], ['Janu', 'Jan'], ['February', 'Feb'], ['Mar', 'Mar'],
                         ['May', 'May'], ['June', 'Jun'], ['Jul', 'Jul'], ['Augu', 'Aug'],
                         ['Oct', 'Oct'], ['Novem', 'Nov'], ['Dec', 'Dec']]) {
  eq(`month "${m}"`, whenET(`${m} 11 2026 at 1PM ET`), `${want} 11, 2026, 1:00 PM`);
}

// Two characters is ambiguous (Mar/May, Jun/Jul) and must not match.
for (const m of ['Ma', 'Ju', 'Se']) {
  eq(`ambiguous "${m}" rejected`, whenET(`Friday, ${m} 11th at 1PM ET`), 'NULL');
}

// Time forms.
for (const t of ['1PM ET', '1 PM ET', '1:00PM ET', '1:00 pm ET', '1:00 PM (ET)']) {
  eq(`time "${t}"`, whenET(`September 11, 2026 at ${t}`), 'Sep 11, 2026, 1:00 PM');
}

// The mail that exposed the month bug, end to end.
const subject = 'coordinator meeting Friday Sept 11th';
const body = 'Dear Coordinators: We will have a meeting this coming Friday, Sept 11th at 1PM ET:'
  + 'https://indico.bnl.gov/event/34045/ Regards,John';
const dt = extractDateTime_(subject, body, 2026, null);
eq('coordinator mail: date and time',
   dt ? new Date(dt.timestamp * 1000).toLocaleString('en-US',
     { timeZone: ET, weekday: 'long', month: 'long', day: 'numeric', hour: 'numeric', minute: '2-digit' }) : 'NULL',
   'Friday, September 11 at 1:00 PM');
eq('coordinator mail: indico url', extractIndicoUrl_(body), 'https://indico.bnl.gov/event/34045/');
eq('coordinator mail: title present', !!extractMeetingTitle_(subject, body), true);


// Mail shapes. The body scan takes the first date-and-time in the text, so a
// message's own headers used to win: a reply put the time it was sent on the
// calendar, a forward put the time the original was sent.
function meetingET(subject, body) {
  const r = extractDateTime_(subject, stripSignature_(body), 2026, null);
  if (!r) return 'NULL';
  return new Date(r.timestamp * 1000).toLocaleString('en-US',
    { timeZone: ET, weekday: 'short', month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' });
}

const WANT = 'Fri, Sep 11, 1:00 PM';

eq('reply: meeting inside the quoted original', meetingET('Re: coordinator meeting',
`Sounds good, see you then.

On Wed, Sep 9, 2026 at 11:23 AM John Lajoie <lajoie@iastate.edu> wrote:
> Dear Coordinators: We will have a meeting this coming Friday, Sept 11th at 1PM ET
> https://indico.bnl.gov/event/34045/`), WANT);

eq('reply: attribution wrapped across lines', meetingET('Re: coordinator meeting',
`Thanks.

On Wed, Sep 9, 2026 at 11:23 AM John Lajoie <
lajoie@iastate.edu> wrote:
> meeting this coming Friday, Sept 11th at 1PM ET`), WANT);

eq('forward: header block ignored', meetingET('Fwd: coordinator meeting',
`FYI

---------- Forwarded message ---------
From: John Lajoie <lajoie@iastate.edu>
Date: Tue, Sep 8, 2026 at 4:47 PM
Subject: coordinator meeting

We will have a meeting this coming Friday, Sept 11th at 1PM ET`), WANT);

// A lone "Date:" in prose is a meeting detail, not a header, and is preferred.
eq('announcement: labelled Date line still used', meetingET('EIC meeting',
`You are invited.

Date: Friday, September 11, 2026 at 1:00 PM ET
Venue: https://indico.bnl.gov/event/34045/`), WANT);

eq('zoom dial-in digits are not a time', meetingET('weekly sync',
`Meeting is Friday, Sep 11 at 1:00 PM ET.

Join Zoom Meeting https://zoom.us/j/81234567890?pwd=abc
Meeting ID: 812 3456 7890
One tap mobile +13017158592,,81234567890#`), WANT);


// Date and time need not sit adjacent, and a separator the combined patterns
// do not anticipate must not lose a date that is plainly present.
function whenFull(body, msgDate) {
  const r = extractDateTime_('meeting', stripMailHeaders_(body), 2026, null, msgDate || null);
  if (!r) return 'NULL';
  return new Date(r.timestamp * 1000).toLocaleString('en-US',
    { timeZone: ET, month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' });
}

const AT_ONE = 'Sep 11, 1:00 PM';
for (const [name, body] of [
  ['at separator',            'meeting September 11 @ 1pm ET'],
  ['dash separator',          'September 11 - 1:00 PM ET'],
  ['time before date',        'meeting at 1PM ET on Friday September 11'],
  ['separate lines',          'Date: Friday, September 11\nTime: 1:00 PM ET'],
  ['ISO date',                'meeting 2026-09-11 at 13:00 ET'],
  ['numeric date',            'meeting 9/11/2026 at 1:00 PM ET'],
  ['24-hour bare',            'meeting September 11 at 1300 ET'],
  ['indico arrow range',      '11 September 2026, 13:00 \u2192 14:00 (US/Eastern)'],
]) eq(name, whenFull(body), AT_ONE);

eq('noon', whenFull('meeting September 11 at noon ET'), 'Sep 11, 12:00 PM');

// A range carries its meridiem on the end; the start must not be read bare,
// and a day of the month must not be mistaken for the start of one.
eq('hyphen range keeps pm', whenFull('meeting September 11, 1:00-2:00 PM ET'), AT_ONE);
eq('en dash range keeps pm', whenFull('meeting September 11, 1:00 PM \u2013 2:00 PM ET'), AT_ONE);
eq('day number is not a range', whenFull('September 11 - 1:00 PM ET'), AT_ONE);
eq('bare range keeps pm', whenFull('meeting September 11 (Friday) 1-2 pm ET'), AT_ONE);
eq('bare range after day name', whenFull('Friday Sept 11, 1 - 2 PM ET'), AT_ONE);
eq('bare range across noon', whenFull('meeting September 11 12-1 pm ET'), 'Sep 11, 12:00 PM');
eq('bare range, actual mail',
   whenFull('Hi Torre,\nI haven\u2019t seen your vote yet. Will the time below works for you ?\n\nSept 23 (Wednesday) 3-4 pm EDT.\n\n(It seems rest of us can do this time)'),
   'Sep 23, 3:00 PM');

// Relative to the message date: Wednesday 9 September 2026.
const MSG = new Date(Date.UTC(2026, 8, 9, 14, 0));
for (const [name, body, want] of [
  ['this Friday',        'meeting this Friday at 1PM ET',        AT_ONE],
  ['this coming Friday', 'meeting this coming Friday at 1PM ET', AT_ONE],
  ['next Friday',        'meeting next Friday at 1PM ET',        AT_ONE],
  ['Friday the 11th',    'meeting Friday the 11th at 1PM ET',    AT_ONE],
  ['tomorrow',           'meeting tomorrow at 1PM ET',           'Sep 10, 1:00 PM'],
  ['today',              'meeting today at 1PM ET',              'Sep 9, 1:00 PM'],
  ['same weekday means next week', 'meeting Wednesday at 1PM ET', 'Sep 16, 1:00 PM'],
  ['day already past rolls forward', 'meeting the 3rd at 1PM ET', 'Oct 3, 1:00 PM'],
]) eq(`relative: ${name}`, whenFull(body, MSG), want);

// Without a message date there is nothing to anchor to, and inventing one
// would be worse than declining.
eq('relative declines without an anchor', whenFull('meeting this Friday at 1PM ET'), 'NULL');


// A date with no written year takes the message's year, except when that puts
// it well in the past: a December mail about a January meeting is next year's.
const DEC = new Date(Date.UTC(2026, 11, 15, 15, 0));
(function () {
  const r = extractDateTime_('meeting', 'meeting January 8 at 1PM ET', 2026, null, DEC);
  eq('December mail, January meeting rolls forward',
     r ? new Date(r.timestamp * 1000).toLocaleString('en-US', { timeZone: ET, year: 'numeric', month: 'short', day: 'numeric' }) : 'NULL',
     'Jan 8, 2027');
})();
eq('December mail, recent past stays this year',
   whenFull('minutes of the November 20 meeting at 1PM ET', DEC), 'Nov 20, 1:00 PM');

// The time pairs with the date in its own sentence, and a day name there beats
// a date elsewhere — a deadline must not lend its date to the meeting.
eq('deadline date does not pair with meeting time',
   whenFull('Abstract deadline is September 30. The meeting is Friday at 1PM ET.', MSG), AT_ONE);
eq('digest keeps its first meeting',
   whenFull('Monday Sep 14: call at 10:00 AM ET.\nFriday Sep 18: meeting at 1:00 PM ET.'), 'Sep 14, 10:00 AM');

console.log(failed ? `\n${failed} failed` : '\nall passed');
process.exit(failed ? 1 : 0);
