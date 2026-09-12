#!/usr/bin/env node
/** Current-message image selection and capture; Gmail calls are stubbed.
 * node scripts/test_gmail_addon_images.js
 */
const fs = require('fs');
const path = require('path');
const assert = require('assert');
const CODE = path.join(__dirname, '..', 'gmail_addon', 'Code.gs');
const b64 = text => Buffer.from(text).toString('base64url');
let reads = [], downloads = [], uploads = [], cardText = [];
let activePayload;
const blobs = {};

global.Utilities = {
  formatDate: () => '20260911',
  base64DecodeWebSafe: text => Array.from(Buffer.from(text, 'base64url')),
  newBlob: (bytes, type, name) => ({
    getDataAsString: () => Buffer.from(bytes).toString('utf8'),
    bytes, type, name,
  }),
};
global.PropertiesService = { getUserProperties: () => ({ getProperty: () => 'key' }) };
const fluent = new Proxy({}, { get: (_, method) => (...args) => {
  if (method === 'setText') cardText.push(args[0]);
  return fluent;
} });
global.CardService = new Proxy({}, { get: () => () => fluent });
global.Session = { getScriptTimeZone: () => 'America/New_York' };
global.Gmail = { Users: { Messages: {
  get: (user, id, options) => {
    assert.equal(id, 'current');
    assert.equal(options.format, 'full');
    reads.push(id);
    return { id, payload: activePayload };
  },
  Attachments: { get: (user, id, attachmentId) => {
    assert.equal(id, 'current');
    downloads.push(attachmentId);
    assert.ok(blobs[attachmentId], 'unexpected attachment download: ' + attachmentId);
    return { data: b64(blobs[attachmentId]) };
  } },
} } };
const message = {
  getId: () => 'current', getSubject: () => 'Latest plot', getFrom: () => 'sender@example.org',
  getDate: () => new Date('2026-09-11T12:00:00Z'), getPlainBody: () => 'Here is the plot.',
  getThread: () => ({ getPermalink: () => 'https://mail.google.com/thread',
                     getMessages: () => { throw Error('Must not scan thread'); } }),
  getAttachments: () => { throw Error('Must not load all attachments'); },
  getRawContent: () => { throw Error('Must not load raw MIME'); },
};
global.GmailApp = { getMessageById: id => {
  assert.equal(id, 'current');
  return message;
} };
global.UrlFetchApp = { fetch: (url, options) => {
  uploads.push(options.payload);
  return { getResponseCode: () => 200,
           getContentText: () => JSON.stringify({ status: 'ok', files: 1, url: '/capture/result' }) };
} };
eval(fs.readFileSync(CODE, 'utf8'));
// Capture the trigger's selected count and calendar prefill without emulating
// every CardService widget; the image section is exercised separately below.
buildMainCard_ = (title, url, prefill, capInfo) => ({ title, url, prefill, capInfo });

function image(cid, disposition = 'inline', size = 2000) {
  return { partId: cid, mimeType: 'image/png', filename: 'image.png',
    headers: [{ name: 'Content-ID', value: '<' + cid + '>' },
              { name: 'Content-Disposition', value: disposition }],
    body: { size, attachmentId: cid } };
}
function mail(html, parts) {
  return { html, parts };
}
function payload(html, parts) {
  return { mimeType: 'multipart/mixed', parts: [
    { mimeType: 'text/html', body: { data: b64(html), size: html.length } }, ...parts,
  ] };
}
function ids(mail) { return currentImageParts_(mail).map(p => p.partId); }
const old = image('old'), fresh = image('fresh');
const freshHTML = '<div><img src="cid:fresh"></div>';
for (const quote of [
  '<div class="gmail_quote"><div><img src="cid:old"></div></div>',
  '<blockquote type="cite"><img src="cid:old"></blockquote>',
  '<div class="yahoo_quoted"><img src="cid:old"></div>',
  '<div id="divRplyFwdMsg">From: Sender</div><img src="cid:old">',
]) assert.deepEqual(ids(mail(freshHTML + quote, [old, fresh])), ['fresh']);
assert.deepEqual(ids(mail('<blockquote><img src="cid:old"></blockquote>' + freshHTML, [old, fresh])), ['fresh']);
assert.deepEqual(ids(mail('<div class="gmail_quote"><img src="cid:old"></div>', [old])), []);
assert.deepEqual(ids(mail(freshHTML + '<blockquote><img src="cid:fresh"></blockquote>', [fresh])), ['fresh']);
assert.deepEqual(ids(mail('', [image('file', 'attachment'), image('pixel', 'attachment', 200)])), ['file']);
assert.deepEqual(ids(mail('<img src="cid:fresh" title="a > b">', [fresh])), ['fresh']);
assert.deepEqual(ids(mail('<!-- <img src="cid:old"> -->' + freshHTML, [old, fresh])), ['fresh']);
console.log('PASS: quoted inline images excluded, new/reused inline images and attachments retained');

// A long quoted chain has many image parts, but no old message or image body
// is fetched to render the card. Only the chosen image downloads on Stash.
const oldParts = Array.from({ length: 300 }, (_, i) => image('old-' + i));
const html = freshHTML + '<div class="gmail_quote">' + oldParts.map(p => '<img src="cid:' + p.partId + '">').join('') + '</div>';
activePayload = payload(html, [...oldParts, fresh]);
const card = onGmailMessage({ gmail: { messageId: 'current' } })[0];
assert.equal(card.capInfo.count, 1);
assert.deepEqual(reads, ['current']);
assert.deepEqual(downloads, []);
buildCapturesSection_(card.capInfo, card.url);
assert.ok(cardText.includes('1 image in this mail'));
assert.ok(!cardText.some(t => /thread/.test(t)));
blobs.fresh = 'fresh image bytes';
stashCaptures({ commonEventObject: { parameters: { message_id: 'current' }, formInputs: {} } });
assert.deepEqual(downloads, ['fresh']);
assert.equal(uploads.length, 1);
assert.equal(Buffer.from(uploads[0].img0.bytes).toString(), blobs.fresh);
assert.ok(!uploads[0].img1);
console.log('PASS: 300 quoted images, one message read per action, zero image downloads on open, one on stash');

// Both inline calendar MIME and .ics attachments use just their own bodies.
const ics = 'BEGIN:VCALENDAR\r\nBEGIN:VEVENT\r\nSUMMARY:Meeting\r\nDTSTART:20260911T170000Z\r\nEND:VEVENT\r\nEND:VCALENDAR';
const calendar = { mimeType: 'text/calendar', body: { data: b64(ics) } };
activePayload = payload(freshHTML, [fresh, calendar]);
assert.ok(onGmailMessage({ gmail: { messageId: 'current' } })[0].prefill.content.includes('Meeting'));
assert.deepEqual(downloads, ['fresh']);
blobs.calendar = ics;
activePayload = payload(freshHTML, [fresh, { filename: 'invite.ics', mimeType: 'application/octet-stream',
                                         body: { attachmentId: 'calendar' } }]);
assert.ok(onGmailMessage({ gmail: { messageId: 'current' } })[0].prefill.content.includes('Meeting'));
assert.deepEqual(downloads, ['fresh', 'calendar']);
console.log('PASS: inline and attached calendar data still prefill without fetching image attachments');
console.log('All passed');
