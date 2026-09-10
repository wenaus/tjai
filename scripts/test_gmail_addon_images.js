#!/usr/bin/env node
/**
 * Image-selection tests for the Gmail add-on (gmail_addon/Code.gs).
 *
 * A reply carries the quoted chain's inline images as its own attachments, so
 * "the images in this message" is the whole conversation's by the third reply:
 * four successive stashes of one thread wrote 6, 8, 9 and 10 files, the same
 * bytes over and over. These tests hold the line between what a mail
 * introduces and what a thread contains.
 *
 *   node scripts/test_gmail_addon_images.js
 */
const fs = require('fs');
const path = require('path');

const CODE = path.join(__dirname, '..', 'gmail_addon', 'Code.gs');

global.Utilities = { formatDate: () => '' };
global.PropertiesService = { getUserProperties: () => ({ getProperty: () => '', setProperty() {} }) };
global.CardService = new Proxy({}, { get: () => new Proxy(function () {}, { get: () => () => ({}), apply: () => ({}) }) });
global.GmailApp = {};
global.UrlFetchApp = {};
global.Session = { getScriptTimeZone: () => 'America/New_York' };

eval(fs.readFileSync(CODE, 'utf8'));

let failed = 0;

function eq(name, got, want) {
  const ok = String(got) === String(want);
  if (!ok) failed++;
  console.log(`  ${ok ? 'ok  ' : 'FAIL'} ${name}${ok ? '' : `  (got ${got}, want ${want})`}`);
}

/** An attachment as the add-on reads it: type, size and name are all it uses. */
function img(name, size, type) {
  return {
    getName: () => name,
    getSize: () => size,
    getContentType: () => type || 'image/png',
  };
}

/** A thread of messages, each carrying the images listed for it. */
function thread(spec) {
  const messages = spec.map((atts, i) => ({
    getId: () => 'm' + i,
    getAttachments: () => atts,
    getThread: () => ({ getMessages: () => messages }),
  }));
  return messages;
}

const a = img('image.png', 113610);
const b = img('image.png', 444856);
const c = img('shot.png', 184512);
const tiny = img('pixel.gif', 200, 'image/gif');   // below CAPTURE_MIN_BYTES
const pdf = { getName: () => 'agenda.pdf', getSize: () => 90000, getContentType: () => 'application/pdf' };

// The real shape: each reply repeats what came before and adds one.
const chain = thread([[a], [a, b], [a, b, c]]);

console.log('\nnew in this mail');
eq('first message introduces its own', newImageAttachments_(chain[0]).length, 1);
eq('reply introduces only the new one', newImageAttachments_(chain[2]).length, 1);
eq('and it is the new one', newImageAttachments_(chain[2])[0].getSize(), 184512);
eq('a reply adding nothing introduces nothing',
   newImageAttachments_(thread([[a, b], [a, b]])[1]).length, 0);

console.log('\nall from thread');
eq('every image once, in thread order', threadImageAttachments_(chain[2]).length, 3);
eq('order is first appearance', threadImageAttachments_(chain[2])[2].getSize(), 184512);
eq('a repeat across messages is not counted twice',
   threadImageAttachments_(thread([[a, b], [a, b], [a, b]])[0]).length, 2);

console.log('\nwhat is not an image');
eq('tracking pixels stay out',
   imageAttachments_({ getAttachments: () => [a, tiny] }).length, 1);
eq('other attachments stay out',
   imageAttachments_({ getAttachments: () => [a, pdf] }).length, 1);

console.log(failed ? `\n${failed} failed` : '\nall passed');
process.exit(failed ? 1 : 0);
