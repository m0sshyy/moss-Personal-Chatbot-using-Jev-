/**
 * Gmail receipts for Moss (README, "Gmail receipts").
 *
 * Lives in your own Google account at script.google.com. Every hour it looks for new
 * receipt-like emails, sends each one to Moss, and puts the "Moss" label on it so it's
 * only sent once. Moss decides what was really bought (promos, top-ups and transfers
 * to yourself are skipped) and tells you in Telegram.
 *
 * Setup: paste your address and secret from /siri below, then run setup() once.
 */
const ADDRESS = 'PASTE THE ADDRESS FROM /siri';
const SECRET = 'PASTE THE SECRET FROM /siri';

// Gmail search for emails that might be receipts. Gmail sorts most receipts into "Purchases".
const SEARCH = '(category:purchases OR subject:(receipt OR resit OR order OR invoice OR payment ' +
               'OR transfer OR pembayaran OR pemindahan OR transaction)) ' +
               '-category:promotions -category:social newer_than:2d -label:Moss';

function checkReceipts() {
  const label = GmailApp.getUserLabelByName('Moss') || GmailApp.createLabel('Moss');
  for (const thread of GmailApp.search(SEARCH, 0, 20)) {
    for (const message of thread.getMessages()) {
      const response = UrlFetchApp.fetch(ADDRESS, {
        method: 'post',
        contentType: 'application/json',
        headers: {'X-Shortcut-Secret': SECRET},
        muteHttpExceptions: true,
        payload: JSON.stringify({
          action: 'receipt',
          id: message.getId(),
          from: message.getFrom(),
          subject: message.getSubject(),
          date: Utilities.formatDate(message.getDate(), 'Asia/Kuala_Lumpur', 'yyyy-MM-dd HH:mm'),
          text: message.getPlainBody().slice(0, 8000),
        }),
      });
      console.log(message.getSubject() + ' -> ' + response.getContentText());
      if (response.getResponseCode() !== 200) return;  // try again next hour
    }
    thread.addLabel(label);
  }
}

/** Run this once: checks now, then every hour by itself. */
function setup() {
  ScriptApp.getProjectTriggers().forEach(trigger => ScriptApp.deleteTrigger(trigger));
  ScriptApp.newTrigger('checkReceipts').timeBased().everyHours(1).create();
  checkReceipts();
}
