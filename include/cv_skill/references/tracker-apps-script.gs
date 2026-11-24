// Tracker web app for a Google Sheet used by jobserve-new-jobs and targeted-cv-from-master (tracker.py).
// It needs Drive access only for CV uploads and setup: PDFs go into folders below the folder that holds this sheet
// (or a folder id you give), and the script touches nothing outside those folders.
// Do not add the "only current doc" annotation (an @ tag in a comment): it would block Drive and break uploads.
//
// Setup: nothing to edit in this file. Either make a copy of your team's template sheet (the script comes with it),
// or paste this file (and references/appsscript.json) into Extensions > Apps Script of a new sheet. Then
// Deploy > New deployment > Web app > Deploy, authorise, and give the /exec URL to your agent, which runs
//   python3 <jobserve-new-jobs>/scripts/setup_google.py connect --url "<.../exec>"
// That claims the web app with a new random token (kept in this script's properties, never in the code), writes
// the header row, creates the CV folder and fills your local config. Full guide: references/google-setup.md.
// After replacing this file later: Deploy > Manage deployments > edit (pencil) > Version: New version > Deploy
// (keeps the same /exec URL). Lost or leaked token: run resetToken below, then setup_google.py rotate.
//
// Everything is POST so the token never appears in a URL. GET is refused.
// POST {action: "claim", token}                        -> stores the token if none is set yet (same token again: ok)
// POST {token, action: "info", tab}                    -> {ok, version, sheet_url, sheet_name, tab, header, drive,
//                                                          sharing, folder}
// POST {token, action: "setup", tab, fields, folder_name}-> header row (only on an empty tab) + folder next to the sheet
// POST {token, tab, action: "read"}                    -> the tab as CSV (values as displayed)
// POST {token, tab, fields, rows:[{code, col: value}]} -> updates only the cells sent, matched by the "code"
//      column; unknown codes are appended as new rows; missing columns are added at the end; the grid grows as
//      needed. Other cells, column order and formatting are left alone. Errors come back as {"error": "..."}.
// POST {token, action: "upload", root, folder: "CVs/2026-10-21", name: "X.pdf", key, suffix, data: <base64>}
//      -> {ok, id, url, bytes, name, root}. root (optional) is a Drive folder id to start from instead of the folder
//      that holds this sheet. Creates the folders below it; an earlier upload with the same
//      key (kept as the file description) in that folder goes to the bin; a different file with the same name gets
//      "-<suffix>" added. Only .pdf files.
// POST {token, action: "trash", root, folder, key}     -> bins the PDFs with that key in that folder (setup test file)
// POST {token, action: "lockdown"}                     -> sheet sharing set to private (named collaborators stay)

const SCRIPT_VERSION = 2;
const TOKEN = 'PASTE-YOUR-TOKEN-HERE';   // legacy only: leave as is; the token is claimed and kept in script properties

// Run once from the editor only if an upload says "You do not have permission to call DriveApp".
function authorize() {
  DriveApp.getFileById(SpreadsheetApp.getActive().getId()).getParents();
  Logger.log('Drive access OK');
}

// Run from the editor to forget the token (lost or leaked); then claim a new one with setup_google.py rotate.
function resetToken() {
  PropertiesService.getScriptProperties().deleteProperty('TOKEN');
  Logger.log('Token cleared: run setup_google.py rotate (or connect) now');
}

function token_() {
  const t = PropertiesService.getScriptProperties().getProperty('TOKEN');
  if (t) return t;
  return TOKEN && TOKEN !== 'PASTE-YOUR-TOKEN-HERE' ? TOKEN : '';
}

function sheet_(tab) {
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  return (tab && ss.getSheetByName(tab)) || ss.getSheets()[0];
}

function json_(o) {
  return ContentService.createTextOutput(JSON.stringify(o)).setMimeType(ContentService.MimeType.JSON);
}

function cell_(v) {
  v = String(v);
  return /[",\n\r]/.test(v) ? '"' + v.replace(/"/g, '""') + '"' : v;
}

// Values are entered as if typed (so "88%" and "2026-10-19" match typed/Table columns); no number formats are
// set, because Table columns refuse them. Anything that could be read as a formula is forced to plain text.
function safe_(v) {
  v = v == null ? '' : String(v);
  return /^[=+@]/.test(v) || /^-[^0-9.]/.test(v) ? "'" + v : v;
}

function fit_(sh, rows, cols) {
  if (cols > sh.getMaxColumns()) sh.insertColumnsAfter(sh.getMaxColumns(), cols - sh.getMaxColumns());
  if (rows > sh.getMaxRows()) sh.insertRowsAfter(sh.getMaxRows(), rows - sh.getMaxRows());
}

function header_(sh) {
  if (!sh.getLastRow()) return [];
  const h = sh.getRange(1, 1, 1, Math.max(sh.getLastColumn(), 1)).getDisplayValues()[0];
  while (h.length && h[h.length - 1] === '') h.pop();
  return h;
}

function doGet(e) {
  return json_({error: 'use POST'});
}

function read_(sh) {
  if (!sh.getLastRow()) return ContentService.createTextOutput('code\n').setMimeType(ContentService.MimeType.CSV);
  const vals = sh.getDataRange().getDisplayValues();
  return ContentService.createTextOutput(vals.map(r => r.map(cell_).join(',')).join('\n'))
    .setMimeType(ContentService.MimeType.CSV);
}

function doPost(e) {
  let body;
  try { body = JSON.parse(e.postData.contents); } catch (err) { return json_({error: 'bad json'}); }
  if (!body) return json_({error: 'bad json'});
  try {
    if (body.action === 'claim') return claim_(body);
    const tok = token_();
    if (!tok) return json_({error: 'not claimed yet: run setup_google.py connect', version: SCRIPT_VERSION});
    if (body.token !== tok) return json_({error: 'bad token'});
    if (body.action === 'info') return info_(body);
    if (body.action === 'setup') return setup_(body);
    if (body.action === 'read') return read_(sheet_(body.tab));
    if (body.action === 'upload') return upload_(body);
    if (body.action === 'trash') return trash_(body);
    if (body.action === 'lockdown') return lockdown_();
    return write_(body);
  } catch (err) {
    return json_({error: String(err)});
  }
}

function claim_(body) {
  const t = String(body.token || '');
  if (t.length < 24) return json_({error: 'token too short'});
  const lock = LockService.getScriptLock();
  lock.waitLock(30000);
  try {
    const cur = token_();
    if (cur && cur !== t) return json_({error: 'already claimed with another token (run resetToken in the Apps Script editor to start again)'});
    PropertiesService.getScriptProperties().setProperty('TOKEN', t);
    return json_({ok: true, version: SCRIPT_VERSION});
  } finally {
    lock.releaseLock();
  }
}

function info_(body) {
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  const sh = sheet_(body.tab);
  const out = {ok: true, version: SCRIPT_VERSION, sheet_url: ss.getUrl(), sheet_name: ss.getName(), tab: sh.getName(),
               header: header_(sh)};
  try {
    const f = DriveApp.getFileById(ss.getId());
    out.sharing = String(f.getSharingAccess());
    const p = f.getParents();
    out.folder = p.hasNext() ? p.next().getName() : 'My Drive';
    out.drive = true;
  } catch (err) {
    out.drive = false;
    out.drive_error = String(err).split(' For more information')[0];
  }
  return json_(out);
}

function name_(p) {
  p = String(p || '').trim();
  return p && p !== '.' && p !== '..' && p.length <= 100 && !/[\/\\]/.test(p) ? p : '';
}

// <root folder id, else the folder holding this sheet>/<parts...>; null when a part is missing and create is false
function dir_(root, parts, create) {
  let dir;
  if (root) dir = DriveApp.getFolderById(root);
  else {
    const home = DriveApp.getFileById(SpreadsheetApp.getActiveSpreadsheet().getId()).getParents();
    dir = home.hasNext() ? home.next() : DriveApp.getRootFolder();
  }
  for (const p of parts) {
    const it = dir.getFoldersByName(p);
    if (it.hasNext()) dir = it.next();
    else if (create) dir = dir.createFolder(p);
    else return null;
  }
  return dir;
}

function parts_(folder) {
  const parts = String(folder || '').split('/').map(s => s.trim()).filter(Boolean);
  return parts.every(p => name_(p)) ? parts : null;
}

function setup_(body) {
  const lock = LockService.getDocumentLock();
  lock.waitLock(30000);
  try {
    const sh = sheet_(body.tab);
    const fields = (body.fields || []).map(String).filter(Boolean);
    let wrote = false;
    if (!sh.getLastRow() && fields.length) {
      fit_(sh, 1, fields.length);
      sh.getRange(1, 1, 1, fields.length).setValues([fields.map(safe_)]);
      sh.setFrozenRows(1);
      wrote = true;
    }
    let folder = null;
    if (body.folder_name) {
      const n = name_(body.folder_name);
      if (!n) return json_({error: 'bad folder name'});
      const d = dir_('', [n], true);
      folder = {id: d.getId(), name: d.getName()};
    }
    return json_({ok: true, header_written: wrote, header: header_(sh), folder: folder});
  } finally {
    lock.releaseLock();
  }
}

function write_(body) {
  const lock = LockService.getDocumentLock();
  lock.waitLock(30000);
  try {
    const sh = sheet_(body.tab);
    const header = header_(sh);
    for (const f of body.fields || []) {
      if (header.indexOf(f) < 0) {
        header.push(f); fit_(sh, 1, header.length);
        sh.getRange(1, header.length).setValue(f);
      }
    }
    const codeCol = header.indexOf('code');
    if (codeCol < 0) return json_({error: 'no "code" column in row 1'});
    const last = sh.getLastRow();
    const codes = last > 1 ? sh.getRange(2, codeCol + 1, last - 1, 1).getDisplayValues().map(r => r[0]) : [];
    const at = {};
    codes.forEach((c, i) => { if (c && !(c in at)) at[c] = i + 2; });
    const fresh = (body.rows || []).filter(r => r.code && !at[r.code]).length;
    fit_(sh, Math.max(last, 1) + fresh, header.length);
    let next = Math.max(last, 1) + 1, added = 0, updated = 0;
    for (const row of body.rows || []) {
      const c = row.code;
      if (!c) continue;
      if (!at[c]) {
        const r = next++; at[c] = r; added++;
        sh.getRange(r, 1, 1, header.length).setValues([header.map(h => safe_(row[h]))]);
        continue;
      }
      updated++;
      for (const k in row) {
        const j = header.indexOf(k);
        if (j < 0 || k === 'code') continue;
        sh.getRange(at[c], j + 1).setValue(safe_(row[k]));
      }
    }
    SpreadsheetApp.flush();   // surface sheet errors here (as JSON), not after the reply
    return json_({ok: true, added: added, updated: updated});
  } finally {
    lock.releaseLock();
  }
}

// CV PDF -> <body.root folder, else the folder holding this sheet>/<body.folder>/<name>. Folder parts are names,
// never paths ("..", "/" refused).
function upload_(body) {
  const root = String(body.root || '');
  if (root && !/^[A-Za-z0-9_-]{10,100}$/.test(root)) return json_({error: 'bad root folder id'});
  const parts = parts_(body.folder);
  if (!parts) return json_({error: 'bad folder'});
  const name = String(body.name || '').replace(/[\\/:*?"<>|]/g, '-').trim();
  if (!/\.pdf$/i.test(name) || name.length > 200) return json_({error: 'only .pdf files can be uploaded'});
  const key = String(body.key || name);
  const bytes = Utilities.base64Decode(String(body.data || ''));
  if (!bytes.length) return json_({error: 'empty file'});
  const lock = LockService.getDocumentLock();
  lock.waitLock(30000);
  try {
    const base = dir_(root, [], true);
    const dir = dir_(root, parts, true);
    let clash = false;
    const files = dir.getFiles();
    while (files.hasNext()) {
      const f = files.next();
      if (f.isTrashed()) continue;
      if (f.getDescription() === key) f.setTrashed(true);       // rebuilt CV: replace the earlier upload
      else if (f.getName() === name) clash = true;              // another role, same file name
    }
    const suffix = String(body.suffix || key).replace(/[^A-Za-z0-9-]/g, '').slice(-24);
    const final = clash ? name.replace(/\.pdf$/i, '') + '-' + suffix + '.pdf' : name;
    const file = dir.createFile(Utilities.newBlob(bytes, 'application/pdf', final));
    file.setDescription(key);
    return json_({ok: true, id: file.getId(), url: file.getUrl(), bytes: bytes.length, name: final,
                  root: root ? base.getId() : ''});
  } finally {
    lock.releaseLock();
  }
}

// Bin the PDFs uploaded with this key in this folder (used to remove the setup test file). Nothing else is touched.
function trash_(body) {
  const root = String(body.root || '');
  if (root && !/^[A-Za-z0-9_-]{10,100}$/.test(root)) return json_({error: 'bad root folder id'});
  const parts = parts_(body.folder);
  if (!parts || !body.key) return json_({error: 'bad folder or key'});
  const dir = dir_(root, parts, false);
  let n = 0;
  if (dir) {
    const files = dir.getFiles();
    while (files.hasNext()) {
      const f = files.next();
      if (!f.isTrashed() && f.getDescription() === String(body.key) && /\.pdf$/i.test(f.getName())) { f.setTrashed(true); n++; }
    }
  }
  return json_({ok: true, trashed: n});
}

function lockdown_() {
  const f = DriveApp.getFileById(SpreadsheetApp.getActiveSpreadsheet().getId());
  f.setSharing(DriveApp.Access.PRIVATE, DriveApp.Permission.NONE);
  return json_({ok: true, sharing: String(f.getSharingAccess())});
}
