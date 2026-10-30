// GRIP GP viewer, file picker: the path box, the songs / tracks switch, one box per dataset, and the
// searchable list (manifest rows from the server, one page at a time). viewer.mjs calls setupFilePicker once at start.

// What viewer.mjs shares, set once by setupFilePicker
let element, pageConfig, openFile, remember, recall;
// Dataset name → its label, for the list rows
let datasetLabels;
// What the list shows: { kind: 'songs' | 'tracks', filters, page, countText(result) }; paging re-asks with another page
let listRequest = null;
// The page controls above and below the list: { previous, next, pageBox, pageCount } each
const pageNavs = [];

/**
 * Take what viewer.mjs shares, wire the path box, switch, filters and page controls, and show the first page.
 *
 * Returns { clearListSelection } for viewer.mjs to call when something else is opened.
 */
export function setupFilePicker(viewer) {
  ({ element, pageConfig, openFile, remember, recall } = viewer);
  datasetLabels = Object.fromEntries(pageConfig.datasets.map(({ name, label }) => [name, label]));

  // A path with a folder opens directly; a bare file name is looked up first (openByFileName)
  element('path-form').onsubmit = (event) => {
    event.preventDefault();
    const path = element('path-input').value.trim();
    if (!path) return;
    if (path.includes('/')) openFile(path, 0, tickedDatasets());
    else openByFileName(path);
  };

  // Songs / tracks switch, remembered in this browser
  setListMode(recall('listMode', 'tracks'));
  for (const radio of document.querySelectorAll('input[name=list-mode]')) {
    radio.onchange = () => {
      remember('listMode', listMode());
      searchFromFirstPage();
    };
  }

  // Re-search shortly after typing stops, and on every toggle
  let searchTimer;
  element('search-input').oninput = () => { clearTimeout(searchTimer); searchTimer = setTimeout(searchFromFirstPage, 250); };
  element('vocal-only').onchange = searchFromFirstPage;
  buildDatasetBoxes();
  for (const id of ['page-nav-top', 'page-nav-bottom']) pageNavs.push(buildPageNav(element(id)));
  searchFromFirstPage();
  return { clearListSelection };
}

/** Un-highlight the list row last opened (another file or a run was opened some other way). */
function clearListSelection() {
  element('browse-list').querySelector('li.selected')?.classList.remove('selected');
}

// ---- Filters ----

/** 'songs' or 'tracks', from the switch. */
function listMode() {
  return document.querySelector('input[name=list-mode]:checked')?.value ?? 'tracks';
}

function setListMode(mode) {
  const radio = document.querySelector(`input[name=list-mode][value=${mode}]`);
  if (radio) radio.checked = true;
}

/** Names of the datasets whose boxes are ticked. */
function tickedDatasets() {
  return [...document.querySelectorAll('#dataset-boxes input:checked')].map((box) => box.value);
}

/** One ticked box per dataset in the server config. */
function buildDatasetBoxes() {
  element('dataset-boxes').replaceChildren(...pageConfig.datasets.map(({ name, label }) => {
    const box = Object.assign(document.createElement('input'), { type: 'checkbox', value: name, checked: true });
    box.onchange = searchFromFirstPage;
    const boxLabel = document.createElement('label');
    boxLabel.append(box, ` ${label}`);
    const row = document.createElement('div');
    row.append(boxLabel);
    return row;
  }));
}

/** List the songs or tracks matching the search box + boxes, from page 1. */
function searchFromFirstPage() {
  const kind = listMode();
  showList({
    kind, page: 1,
    filters: { query: element('search-input').value, vocal_or_melody_only: element('vocal-only').checked ? '1' : '0' },
    countText: (result) => `${result.total} ${kind}`,
  });
}

// ---- The list and its pages ----

/** One track row: song, track, dataset + path, and a warning if alphaTab is known to refuse the file. Click opens that track. */
function buildTrackRow(row) {
  return buildListRow(row, [
    ['song', `${row.artist ?? '?'} — ${row.title_display ?? '?'}`],
    ['track', `track ${row.track_index}: ${row.track_name} · ${row.guitar_or_bass}`],
  ], row.track_index);
}

/** One song row: song, how many guitar / bass tracks, dataset + path. Click opens its first guitar track. */
function buildSongRow(row) {
  return buildListRow(row, [
    ['song', `${row.artist ?? '?'} — ${row.title_display ?? '?'}`],
    ['track', `${row.track_count} guitar / bass track${row.track_count === 1 ? '' : 's'} · opens track ${row.first_guitar_track}`],
  ], row.first_guitar_track);
}

/** A list row from its text lines, plus the dataset + path line and any unreadable warning; click opens `trackIndex`. */
function buildListRow(row, lines, trackIndex) {
  const item = document.createElement('li');
  lines.push(['file-path', `${datasetLabels[row.dataset] ?? row.dataset} · ${row.path}`]);
  if (row.parse_error) lines.push(['unreadable', `alphaTab can't read this file: ${row.parse_error}`]);
  for (const [className, text] of lines) {
    const line = document.createElement('div');
    line.className = className;
    line.textContent = text;
    item.append(line);
  }
  item.onclick = async () => {
    if (await openFile(row.gp_path, trackIndex)) item.classList.add('selected');
  };
  return item;
}

/** Ask the server for one page of songs or tracks in the ticked datasets (see server.search_songs / search_tracks). */
async function fetchList(kind, filters, page) {
  const params = new URLSearchParams({ datasets: tickedDatasets().join(','), page, ...filters });
  return (await fetch(`/api/${kind}?${params}`)).json();
}

/** Fetch and show one page of `request` (see listRequest); its count line and both page controls follow. */
async function showList(request) {
  listRequest = request;
  const result = await fetchList(request.kind, request.filters, request.page);
  if (listRequest !== request) return;  // a newer search started meanwhile
  request.page = result.page;
  element('result-count').textContent = request.countText(result);
  element('browse-list').replaceChildren(...result.rows.map(request.kind === 'songs' ? buildSongRow : buildTrackRow));
  element('browse-list').scrollTop = 0;
  for (const nav of pageNavs) updatePageNav(nav, result);
}

/** Show another page of the current list. */
function goToPage(page) {
  if (listRequest) showList({ ...listRequest, page });
}

/** Fill `nav` with page controls: ◀, "page [n] of N", ▶; returns them for updatePageNav. */
function buildPageNav(nav) {
  const previous = Object.assign(document.createElement('button'), { textContent: '◀', title: 'Previous page' });
  const next = Object.assign(document.createElement('button'), { textContent: '▶', title: 'Next page' });
  const pageBox = Object.assign(document.createElement('input'), { type: 'number', min: 1, title: 'Go to page' });
  const pageCount = document.createElement('span');
  previous.onclick = () => goToPage(listRequest.page - 1);
  next.onclick = () => goToPage(listRequest.page + 1);
  pageBox.onchange = () => goToPage(Number(pageBox.value) || 1);
  nav.replaceChildren(previous, 'page ', pageBox, pageCount, next);
  return { previous, next, pageBox, pageCount };
}

/** Set one set of page controls to a server answer's page and page count. */
function updatePageNav(nav, result) {
  nav.pageBox.value = result.page;
  nav.pageBox.max = result.page_count;
  nav.pageCount.textContent = ` of ${result.page_count}`;
  nav.previous.disabled = result.page <= 1;
  nav.next.disabled = result.page >= result.page_count;
}

// ---- Path box: bare file names ----

/**
 * The path box holds a bare file name (with or without extension): open the one song in the ticked
 * datasets called that (on its first guitar track), or list several to click. No such song: try it
 * as a path (a file directly in a dataset folder), which reports the server's "no file" message if
 * that fails too.
 */
async function openByFileName(fileName) {
  const result = await fetchList('songs', { file_name: fileName }, 1);
  if (result.total === 1) {
    openFile(result.rows[0].gp_path, result.rows[0].first_guitar_track);
  } else if (result.total === 0) {
    openFile(fileName, 0, tickedDatasets());
  } else {
    showList({
      kind: 'songs', page: 1, filters: { file_name: fileName },
      countText: (answer) => `${answer.total} songs named "${fileName}": click one`,
    });
  }
}
