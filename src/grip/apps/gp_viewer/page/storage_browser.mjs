// GRIP viewer, storage browser (the sidebar's Files tab): the newest files anywhere in storage, and the
// storage folder as on disk (breadcrumbs, subfolders, files newest first). Shows only what the viewer
// opens: GP files, optimiser runs, existing-tool runs, catalogue builds. viewer.mjs calls setupStorageBrowser once at start.

// What viewer.mjs shares, set once by setupStorageBrowser
let element, openFile, showRunFile, openCatalogue, remember, recall, onOpened;
// The folder shown, relative to the storage root ("" = the root)
let shownFolder = '';

const KIND_LABELS = { gp: 'GP', optimiser_run: 'GRIP run', existing_tool_run: 'tool run', catalogue: 'catalogue' };

/**
 * Take what viewer.mjs shares, show the recent list and the last folder visited.
 *
 * Returns { refresh } for viewer.mjs to call when a new file may exist (e.g. after a run).
 */
export function setupStorageBrowser(viewer) {
  ({ element, openFile, remember, recall } = viewer);
  showRunFile = viewer.showRunFile;
  openCatalogue = viewer.openCatalogue;
  onOpened = viewer.onFileOpened;
  element('storage-refresh').onclick = (event) => {
    event.preventDefault();  // inside <summary>: don't fold the section
    refresh();
  };
  element('recent-section').open = recall('recentOpen', '1') === '1';
  element('recent-section').ontoggle = () => remember('recentOpen', element('recent-section').open ? '1' : '0');
  setupDivider();
  showFolder(recall('storageFolder', ''));
  showRecent();
  return { refresh };
}

// Smallest heights (px) dragging may leave the recent list and the storage folders
const MIN_RECENT_HEIGHT = 60;
const MIN_STORAGE_HEIGHT = 120;

/** The divider under Recent: drag it to make Recent taller or shorter; the height is remembered. */
function setupDivider() {
  const divider = element('recent-divider');
  const recentList = element('recent-list');
  const remembered = recall('recentHeight', null);
  if (remembered) recentList.style.height = `${remembered}px`;
  let startY = null;
  let startHeight = null;
  divider.onpointerdown = (event) => {
    divider.setPointerCapture(event.pointerId);  // keep getting moves even off the divider
    divider.classList.add('dragging');
    startY = event.clientY;
    startHeight = recentList.offsetHeight;
  };
  divider.onpointermove = (event) => {
    if (startY === null) return;
    const room = element('storage-tab').clientHeight - MIN_STORAGE_HEIGHT;
    const height = Math.min(Math.max(startHeight + event.clientY - startY, MIN_RECENT_HEIGHT), Math.max(room, MIN_RECENT_HEIGHT));
    recentList.style.height = `${height}px`;
  };
  divider.onpointerup = () => {
    if (startY === null) return;
    startY = null;
    divider.classList.remove('dragging');
    remember('recentHeight', String(recentList.offsetHeight));
  };
  divider.onpointercancel = divider.onpointerup;
}

/** Look at the storage folder again: recent list + the folder shown. */
function refresh() {
  showRecent();
  showFolder(shownFolder);
}

/** GET a JSON route; null (with nothing shown) if the server refuses. */
async function fetchJson(url) {
  const response = await fetch(url);
  return response.ok ? response.json() : null;
}

async function showRecent() {
  const files = (await fetchJson('/api/recent')) ?? [];
  element('recent-list').replaceChildren(...(files.length ? files.map((file) => fileRow(file, true)) : [emptyRow('No results yet')]));
}

/** Show one folder; an unknown or emptied folder falls back to the root. */
async function showFolder(folder) {
  let listing = await fetchJson(`/api/storage?${new URLSearchParams({ folder })}`);
  if (!listing && folder !== '') listing = await fetchJson('/api/storage?folder=');
  if (!listing) {
    element('storage-list').replaceChildren(emptyRow('Storage folder not readable'));
    return;
  }
  shownFolder = listing.folder;
  remember('storageFolder', shownFolder);
  showCrumbs(listing);
  const rows = [
    ...listing.folders.map((name) => folderRow(name)),
    ...listing.files.map((file) => fileRow(file, false)),
  ];
  element('storage-list').replaceChildren(...(rows.length ? rows : [emptyRow('Nothing to open here')]));
  element('storage-list').scrollTop = 0;
}

/** storage / optimiser_runs / v1.1: each part but the last goes back up to it. */
function showCrumbs(listing) {
  const parts = listing.folder ? listing.folder.split('/') : [];
  const crumbs = [crumbButton('storage', '', parts.length === 0)];
  parts.forEach((part, index) => {
    crumbs.push(Object.assign(document.createElement('span'), { className: 'separator', textContent: '/' }));
    crumbs.push(crumbButton(part, parts.slice(0, index + 1).join('/'), index === parts.length - 1));
  });
  element('storage-crumbs').replaceChildren(...crumbs);
  element('storage-crumbs').title = listing.root + (listing.folder ? `/${listing.folder}` : '');
}

function crumbButton(label, folder, current) {
  const button = Object.assign(document.createElement('button'), { type: 'button', textContent: label });
  if (!current) button.onclick = () => showFolder(folder);
  return button;
}

function folderRow(name) {
  const item = document.createElement('li');
  item.className = 'folder-row';
  item.append(rowMain(name));
  item.onclick = () => showFolder(shownFolder ? `${shownFolder}/${name}` : name);
  return item;
}

/** A file row: name + kind badge, then when it changed (and, in the recent list, its folder). Click opens it. */
function fileRow(file, showFolderName) {
  const item = document.createElement('li');
  item.title = file.path;
  const meta = [file.modified ? timeAgo(file.modified) : null, showFolderName ? file.folder : null].filter(Boolean).join(' · ');
  item.append(rowMain(file.name, file.kind));
  if (meta) item.append(Object.assign(document.createElement('div'), { className: 'row-meta', textContent: meta }));
  item.onclick = async () => {
    const openers = { gp: (path) => openFile(path, 0), catalogue: openCatalogue };
    const opened = await (openers[file.kind] ?? showRunFile)(file.path);
    if (!opened) return;
    for (const selected of document.querySelectorAll('.file-list li.selected')) selected.classList.remove('selected');
    item.classList.add('selected');
    onOpened();
  };
  return item;
}

function rowMain(name, kind = null) {
  const main = Object.assign(document.createElement('div'), { className: 'row-main' });
  main.append(Object.assign(document.createElement('span'), { className: 'row-name', textContent: name }));
  if (kind) main.append(Object.assign(document.createElement('span'), { className: `badge ${kind}`, textContent: KIND_LABELS[kind] ?? kind }));
  return main;
}

function emptyRow(text) {
  return Object.assign(document.createElement('li'), { className: 'list-empty', textContent: text });
}

/** "just now", "5 min ago", "3 h ago", "2 d ago", then the date (yyyy-mm-dd). */
function timeAgo(secondsSinceEpoch) {
  const seconds = Date.now() / 1000 - secondsSinceEpoch;
  if (seconds < 60) return 'just now';
  if (seconds < 3600) return `${Math.floor(seconds / 60)} min ago`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)} h ago`;
  if (seconds < 7 * 86400) return `${Math.floor(seconds / 86400)} d ago`;
  return new Date(secondsSinceEpoch * 1000).toLocaleDateString('sv-SE');  // sv-SE writes yyyy-mm-dd
}
