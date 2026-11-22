// GRIP viewer, catalogue view: browse one catalogue build without leaving it.
//   left:    interval paths (length, members only, search), most songs first, each with its best layout;
//            drag the pane wider to zoom its tabs
//   top:     the player: one real occurrence of the picked layout (rhythm, frets, bends, slides as in the
//            song), played on the shared tab + fretboard + transport; step through occurrences, or open the
//            whole song in the song view; move its shape along the neck or across strings (scratch only);
//            flag the occurrence's track as junk
//   below:   the catalogue explorer (toggled from the transport bar, drag to resize): the path's layouts,
//            its family one note away (drop / add a note at either end), and where the layout turns up
// Every small tab is drawn in full, never scrolled: it shrinks to fit when its space is too narrow.
// Layouts are drawn with their lowest fretted note on fret 12 (the catalogue keeps frets relative), except
// around an open string: that's a fixed spot, so its stretch is drawn at its real frets, found from the path.
// Interval paths read from the first note (as stored) or, by choice, from the lowest note.
// viewer.mjs calls setupCatalogueView once at start.

// What viewer.mjs shares, set once by setupCatalogueView
let element, remember, recall, statusLabel, openPhrase, setView, showPhrase, openFlagBox, showFlagButton;
// The guitar layouts are drawn on (the optimiser's, string 1 first): places the notes around an open string
let drawingTuning = [];

// The fret a layout's lowest fretted note is drawn on: neutral, mid-neck
const NEUTRAL_FRET = 12;
// Font sizes (px) of the small tabs: the paths pane's at its narrowest (it grows with the pane), the layout cards'
const PATH_TAB_FONT = 9;
const LAYOUT_TAB_FONT = 13;
const FAMILY_TAB_FONT = 9;
const PANE_MIN_WIDTH = 300;
const PANE_MAX_SHARE = 0.7;  // of the window
// Smallest heights (px) the explorer divider leaves the explorer and the player
const EXPLORER_MIN_HEIGHT = 80;
const PLAYER_MIN_HEIGHT = 180;
const FAMILY_TITLES = {
  drop_last: 'drop the last note', drop_first: 'drop the first note',
  add_end: 'add a note at the end', add_start: 'add a note at the start',
};

const catalogue = {
  build: null,          // the open build's folder (full path)
  summary: null,        // its summary: name, lengths, member line
  path: null,           // the picked path's detail: { path, layouts, family }
  entry: null,          // the picked layout's detail: { entry, family, occurrences }
  phrase: null,         // the occurrence in the player: { occurrence, index, total, passage, choices, positions }
  history: [],          // earlier picks to go back to: { interval_path, entry_id }
  shape: null,          // the player's shape as moved: { choices, pitches, fretShift, stringShift } (scratch)
};
let intervalMode = 'first';  // interval paths from the 'first' note (as stored) or the 'lowest' note

/**
 * Take what viewer.mjs shares and wire the filters, the pane divider, the player's stepper and the back button.
 *
 * Returns { openCatalogue, catalogueName, openBuild (the open build's folder), pickEntry (path, entry id) }.
 */
export function setupCatalogueView(viewer) {
  ({ element, remember, recall, statusLabel, openPhrase, setView, showPhrase, openFlagBox, showFlagButton } = viewer);
  drawingTuning = viewer.pageConfig.tuning;
  let searchTimer;
  element('catalogue-length').onchange = () => { remember('catalogueLength', element('catalogue-length').value); showPaths(1); };
  element('catalogue-members-only').checked = recall('catalogueMembersOnly', '1') === '1';
  element('catalogue-members-only').onchange = () => {
    remember('catalogueMembersOnly', element('catalogue-members-only').checked ? '1' : '0');
    showPaths(1);
  };
  element('catalogue-query').oninput = () => { clearTimeout(searchTimer); searchTimer = setTimeout(() => showPaths(1), 250); };
  element('catalogue-back').onclick = goBack;
  element('occurrence-previous').onclick = () => playOccurrence(catalogue.phrase.index - 1);
  element('occurrence-next').onclick = () => playOccurrence(catalogue.phrase.index + 1);
  element('occurrence-open-song').onclick = () => openPhrase(catalogue.phrase.occurrence);
  element('occurrence-flag').onclick = flagPlayingTrack;
  element('shape-fret-down').onclick = () => moveShape({ frets: -1 });
  element('shape-fret-up').onclick = () => moveShape({ frets: 1 });
  element('shape-string-up').onclick = () => moveShape({ strings: -1 });
  element('shape-string-down').onclick = () => moveShape({ strings: 1 });
  element('shape-reset').onclick = resetShape;
  for (const button of document.querySelectorAll('#interval-mode button')) button.onclick = () => setIntervalMode(button.dataset.intervalMode);
  intervalMode = recall('intervalMode', 'first');
  markIntervalMode();
  setupPaneDivider();
  setupExplorer();
  // Refit the small tabs whenever their space changes size
  const refit = new ResizeObserver(() => fitAllTabs());
  for (const id of ['catalogue-paths-pane', 'catalogue-explorer']) refit.observe(element(id));
  return {
    openCatalogue, catalogueName: () => catalogue.summary?.name ?? null, openBuild: () => catalogue.build,
    pickEntry: (intervalPath, entryId) => pickPath(intervalPath, entryId),
  };
}

// ---- Drawing layouts as small tabs, always in full ----

/** One layout token ('3:+2', '4:o', '2:h12', '1:/0', '2:0b') → { string, slideLanding, place, bent }. */
function parseLayoutToken(token) {
  const [stringText, placeText] = token.split(':');
  const slideLanding = placeText.startsWith('/');
  let place = placeText.replace(/^\//, '');
  const bent = place.endsWith('b');
  place = place.replace(/b$/, '');
  return { string: Number(stringText), slideLanding, place, bent };
}

const isRelativeFret = (place) => place !== 'o' && !place.startsWith('h');

/** One parsed token → [string number, text drawn on that string]; relative frets drawn from `lowestFret`'s spot. */
function tokenCell({ string, slideLanding, place, bent }, offset) {
  let fret;
  if (place === 'o') fret = '0';
  else if (place.startsWith('h')) fret = `<${place.slice(1)}>`;
  else fret = String(Number(place) + offset);
  return [string, (slideLanding ? '/' : '') + fret + (bent ? 'b' : '')];
}

/**
 * Where each token's relative frets are drawn from (fret = relative + offset), per stretch (the run start, then
 * each slide landing: frets and semitones restart there). A stretch holding an open string is drawn at its real
 * frets: the open string's pitch and the path's semitones place the stretch's first note, hence its first
 * fretted note's fret. Others (and any stretch that doesn't add up on the drawing tuning, e.g. a drop-D track)
 * use the neutral offset: the layout's lowest fretted note on fret 12.
 */
function stretchOffsets(tokens, intervalPath) {
  const semitones = intervalPath.split(' ').map((text) => Number(text.match(/-?\d+/)[0]));
  const relativeFrets = tokens.filter((token) => isRelativeFret(token.place)).map((token) => Number(token.place));
  const neutral = NEUTRAL_FRET - (relativeFrets.length ? Math.min(...relativeFrets) : 0);
  const stretchOf = [];
  tokens.forEach((token, index) => stretchOf.push(index === 0 ? 0 : stretchOf[index - 1] + (token.slideLanding ? 1 : 0)));
  const offsets = new Map();
  for (const stretch of new Set(stretchOf)) {
    const members = tokens.map((token, index) => index).filter((index) => stretchOf[index] === stretch);
    const opens = members.filter((index) => tokens[index].place === 'o');
    const firstFretted = members.find((index) => isRelativeFret(tokens[index].place));
    const openPitch = (index) => drawingTuning[tokens[index].string - 1];
    let offset = neutral;
    if (opens.length && firstFretted !== undefined && opens.every((index) => openPitch(index) !== undefined)
        && drawingTuning[tokens[firstFretted].string - 1] !== undefined) {
      // The stretch's first note's pitch, from each open string; they must agree
      const starts = new Set(opens.map((index) => openPitch(index) - semitones[index]));
      const start = [...starts][0];
      const anchorFret = start + semitones[firstFretted] - drawingTuning[tokens[firstFretted].string - 1];
      const frets = members.filter((index) => isRelativeFret(tokens[index].place)).map((index) => Number(tokens[index].place) + anchorFret);
      if (starts.size === 1 && frets.every((fret) => fret >= 1 && fret <= 24)) offset = anchorFret;
    }
    offsets.set(stretch, offset);
  }
  return tokens.map((token, index) => offsets.get(stretchOf[index]));
}

/** A layout as text tab lines, string 1 on top, 6 strings (7 if it uses string 7); see stretchOffsets for the frets. */
export function layoutTabLines(layout, intervalPath) {
  const tokens = layout.split(' ').map(parseLayoutToken);
  const offsets = stretchOffsets(tokens, intervalPath);
  const cells = tokens.map((token, index) => tokenCell(token, offsets[index]));
  const stringCount = Math.max(6, ...cells.map(([string]) => string));
  const widths = cells.map(([, text]) => text.length);
  const lines = [];
  for (let string = 1; string <= stringCount; string += 1) {
    const columns = cells.map(([cellString, text], index) => (cellString === string ? text : '').padEnd(widths[index], '-'));
    lines.push(`${string}|-${columns.join('-')}-|`);
  }
  return lines;
}

/** A small tab element; `baseFont` (px) is its size before fitting (see fitTab). */
// ---- How interval paths read ----

/** An interval path as shown: as stored (semitones from the first note), or from the lowest note. A slide
 *  restarts the count in the catalogue, so each stretch (start, then every slide landing) is lowered on its own.
 *  E.g. "0 -3 -5" → "5 2 0". */
function displayPath(intervalPath) {
  if (intervalMode === 'first') return intervalPath;
  const tokens = intervalPath.split(' ').map((text) => {
    const [, slide, semitones, marks] = text.match(/^(\/?)(-?\d+)(.*)$/);
    return { slide, semitones: Number(semitones), marks };
  });
  const stretchStarts = tokens.map((token, index) => index === 0 || token.slide === '/');
  let lowest = 0;
  return tokens.map((token, index) => {
    if (stretchStarts[index]) {
      const end = stretchStarts.indexOf(true, index + 1);
      lowest = Math.min(...tokens.slice(index, end === -1 ? undefined : end).map((each) => each.semitones));
    }
    return `${token.slide}${token.semitones - lowest}${token.marks}`;
  }).join(' ');
}

function setIntervalMode(mode) {
  intervalMode = mode;
  remember('intervalMode', mode);
  markIntervalMode();
  if (catalogue.build) {
    showPaths(1);
    if (catalogue.path) showDetail();
  }
}

function markIntervalMode() {
  for (const button of document.querySelectorAll('#interval-mode button')) {
    button.setAttribute('aria-pressed', String(button.dataset.intervalMode === intervalMode));
  }
}

/** A small tab element; `baseFont` (px) is its size before fitting (see fitTab). */
function tabElement(layout, intervalPath, className, baseFont) {
  const tab = Object.assign(document.createElement('pre'), { className: `mini-tab ${className}`, textContent: layoutTabLines(layout, intervalPath).join('\n') });
  tab.dataset.baseFont = String(baseFont);
  return tab;
}

/** Draw a tab at its base size (the paths pane's grows with the pane), shrunk just enough to fit its space. */
function fitTab(tab) {
  const room = tab.parentElement?.clientWidth ?? 0;
  if (room === 0) return;  // hidden: fitted once it shows (the resize observer calls again)
  const zoom = tab.closest('#catalogue-path-list') ? Math.max(1, element('catalogue-paths-pane').clientWidth / PANE_MIN_WIDTH) : 1;
  const base = Number(tab.dataset.baseFont) * zoom;
  tab.style.fontSize = `${base}px`;
  if (tab.scrollWidth > room) tab.style.fontSize = `${Math.floor((base * room * 10) / tab.scrollWidth) / 10}px`;
}

function fitAllTabs() {
  for (const tab of document.querySelectorAll('#catalogue-view .mini-tab')) fitTab(tab);
}

/** The paths pane's right edge: drag it wider to zoom the pane's tabs (today's width is the narrowest). */
function setupPaneDivider() {
  const pane = element('catalogue-paths-pane');
  const divider = element('catalogue-pane-divider');
  const setWidth = (width) => {
    pane.style.width = `${Math.min(Math.max(width, PANE_MIN_WIDTH), window.innerWidth * PANE_MAX_SHARE)}px`;
  };
  setWidth(Number(recall('cataloguePaneWidth', PANE_MIN_WIDTH)));
  let startX = null;
  let startWidth = null;
  divider.onpointerdown = (event) => {
    divider.setPointerCapture(event.pointerId);
    divider.classList.add('dragging');
    startX = event.clientX;
    startWidth = pane.offsetWidth;
  };
  divider.onpointermove = (event) => { if (startX !== null) setWidth(startWidth + event.clientX - startX); };
  divider.onpointerup = () => {
    if (startX === null) return;
    startX = null;
    divider.classList.remove('dragging');
    remember('cataloguePaneWidth', String(pane.offsetWidth));
  };
  divider.onpointercancel = divider.onpointerup;
}

/** The catalogue explorer under the player: on / off from the transport bar, its height set by dragging the bar
 *  above it (both remembered). */
function setupExplorer() {
  const explorer = element('catalogue-explorer');
  const divider = element('catalogue-explorer-divider');
  const setOpen = (open) => {
    document.body.classList.toggle('explorer-closed', !open);
    element('explorer-toggle').setAttribute('aria-pressed', String(open));
    remember('explorerOpen', open ? '1' : '0');
  };
  setOpen(recall('explorerOpen', '1') === '1');
  element('explorer-toggle').onclick = () => setOpen(document.body.classList.contains('explorer-closed'));
  const rememberedHeight = recall('explorerHeight', null);
  if (rememberedHeight) explorer.style.height = `${rememberedHeight}px`;
  let startY = null;
  let startHeight = null;
  divider.onpointerdown = (event) => {
    divider.setPointerCapture(event.pointerId);
    divider.classList.add('dragging');
    startY = event.clientY;
    startHeight = explorer.offsetHeight;
  };
  divider.onpointermove = (event) => {
    if (startY === null) return;
    const room = element('catalogue-detail').clientHeight - PLAYER_MIN_HEIGHT;
    const height = Math.min(Math.max(startHeight - (event.clientY - startY), EXPLORER_MIN_HEIGHT), Math.max(room, EXPLORER_MIN_HEIGHT));
    explorer.style.height = `${height}px`;
  };
  divider.onpointerup = () => {
    if (startY === null) return;
    startY = null;
    divider.classList.remove('dragging');
    remember('explorerHeight', String(explorer.offsetHeight));
  };
  divider.onpointercancel = divider.onpointerup;
}

// ---- Opening a build, the path list ----

async function fetchJson(route, params) {
  const response = await fetch(`${route}?${new URLSearchParams({ build: catalogue.build, ...params })}`);
  if (!response.ok) {
    statusLabel.textContent = `server: ${response.status} ${response.statusText}`;
    return null;
  }
  return response.json();
}

/**
 * Load a catalogue build into the catalogue view (the top bar's catalogue pick, or the Files tab). True if it opened.
 *
 * Args:
 *   folder: The build's folder (full path).
 *   showView: Switch to the catalogue view too (the Files tab); the top bar's pick stays where it is.
 */
async function openCatalogue(folder, showView = true) {
  const previousBuild = catalogue.build;
  catalogue.build = folder;
  const summary = await fetchJson('/api/catalogue', {});
  if (!summary) {
    catalogue.build = previousBuild;
    return false;
  }
  Object.assign(catalogue, { summary, path: null, entry: null, phrase: null, history: [] });
  fillLengthChoice();
  showPhrase(null);  // the last build's occurrence leaves the player
  if (showView) setView('catalogue');
  showDetail();
  showPlayer();
  await showPaths(1);
  return true;
}

/** Length choices with how many paths each holds; keeps the last length picked if this build has it. */
function fillLengthChoice() {
  const lengths = Object.entries(catalogue.summary.lengths);
  const options = lengths.map(([length, counts]) => new Option(`${length} notes · ${counts.paths} paths`, length));
  element('catalogue-length').replaceChildren(new Option('all lengths', ''), ...options);
  const wanted = recall('catalogueLength', '3');
  element('catalogue-length').value = lengths.some(([length]) => length === wanted) ? wanted : '';
  element('catalogue-line').textContent = `member = in ≥ ${catalogue.summary.member_min_songs} songs · ${catalogue.summary.songs ?? '?'} songs in the build`;
}

async function showPaths(page) {
  const result = await fetchJson('/api/catalogue/paths', {
    length: element('catalogue-length').value, members_only: element('catalogue-members-only').checked ? '1' : '0',
    query: element('catalogue-query').value, page,
  });
  if (!result) return;
  element('catalogue-path-count').textContent = `${result.total} interval paths`;
  element('catalogue-path-list').replaceChildren(...result.rows.map(pathRow));
  element('catalogue-path-list').scrollTop = 0;
  showPathNav(result);
  markPickedPath();
  fitAllTabs();
}

/** A path row: the path, its song count, and its best layout as a small tab. */
function pathRow(path) {
  const item = document.createElement('li');
  item.dataset.path = path.interval_path;
  const main = Object.assign(document.createElement('div'), { className: 'row-main' });
  main.append(
    Object.assign(document.createElement('span'), { className: 'row-name path-text', textContent: displayPath(path.interval_path) }),
    Object.assign(document.createElement('span'), { className: 'badge', textContent: `${path.song_count} songs` }),
  );
  const meta = Object.assign(document.createElement('div'), {
    className: 'row-meta',
    textContent: `${path.length} notes · ${path.layout_count} layout${path.layout_count === 1 ? '' : 's'}` + (path.member_layout_count < path.layout_count ? ` (${path.member_layout_count} members)` : ''),
  });
  item.append(main, tabElement(path.best_layout, path.interval_path, 'path-tab', PATH_TAB_FONT), meta);
  item.onclick = () => pickPath(path.interval_path);
  return item;
}

function showPathNav(result) {
  const nav = element('catalogue-path-nav');
  const previous = Object.assign(document.createElement('button'), { textContent: '◀', disabled: result.page <= 1 });
  const next = Object.assign(document.createElement('button'), { textContent: '▶', disabled: result.page >= result.page_count });
  previous.onclick = () => showPaths(result.page - 1);
  next.onclick = () => showPaths(result.page + 1);
  nav.replaceChildren(previous, ` page ${result.page} of ${result.page_count} `, next);
  nav.hidden = result.page_count <= 1;
}

function markPickedPath() {
  for (const item of element('catalogue-path-list').children) {
    item.classList.toggle('selected', item.dataset.path === catalogue.path?.path.interval_path);
  }
}

// ---- Picking a path or a layout; going back ----

/** Show a path and pick a layout of it (the given one, else its best), which plays its first occurrence.
 *  The current pick goes on the back stack unless going back. */
async function pickPath(intervalPath, entryId = null, rememberCurrent = true) {
  const detail = await fetchJson('/api/catalogue/path', { path: intervalPath });
  if (!detail) return;
  if (rememberCurrent && catalogue.path) {
    catalogue.history.push({ interval_path: catalogue.path.path.interval_path, entry_id: catalogue.entry?.entry.entry_id ?? null });
  }
  catalogue.path = detail;
  catalogue.entry = null;
  await pickEntry(entryId ?? detail.layouts[0].entry_id);
  markPickedPath();
}

/** Pick a layout: its family and where it turns up, and its first occurrence in the player. */
async function pickEntry(entryId) {
  const detail = await fetchJson('/api/catalogue/entry', { entry_id: entryId, page: 1 });
  if (!detail) return;
  catalogue.entry = detail;
  showDetail();
  await playOccurrence(0);
}

async function goBack() {
  const previous = catalogue.history.pop();
  if (previous) await pickPath(previous.interval_path, previous.entry_id, false);
}

// ---- The player ----

/** Load the picked layout's index-th occurrence (list order) into the player. */
async function playOccurrence(index) {
  const phrase = await fetchJson('/api/catalogue/occurrence', { entry_id: catalogue.entry.entry.entry_id, index });
  if (!phrase) return;
  catalogue.phrase = phrase;
  catalogue.shape = { choices: structuredClone(phrase.choices), pitches: phrase.passage.notes.map((note) => note.pitch),
                      fretShift: 0, stringShift: 0 };
  showPlayer();
  markPlayingOccurrence();
  playShape();
}

// ---- Moving the player's shape (scratch: never saved, reset by loading another occurrence) ----

/** Move the shape: `frets` along the neck, or `strings` across strings (−1 = towards string 1) keeping the
 *  notes' steps, so frets re-shape around the tuning's uneven gaps. Refused (with a message) if a note would
 *  leave the neck or the strings. */
function moveShape({ frets = 0, strings = 0 }) {
  const { passage } = catalogue.phrase;
  const tuning = passage.guitar.tuning;  // string 1 first
  const shape = catalogue.shape;
  const played = shape.choices.filter((choice) => choice.string !== null);
  // The notes' steps are kept: every pitch moves by one amount, set by the first played note keeping its fret
  const first = played[0];
  const pitchMove = strings ? tuning[first.string + strings - 1] - tuning[first.string - 1] : frets;
  const moved = shape.choices.map((choice, index) => {
    if (choice.string === null) return { ...choice };
    const string = choice.string + strings;
    if (string < 1 || string > tuning.length) return null;
    const pitch = shape.pitches[index] + pitchMove;
    return { ...choice, string, fret: pitch - tuning[string - 1] };
  });
  const offNeck = moved.some((choice, index) => choice === null || (shape.choices[index].string !== null
    && (choice.fret < 0 || choice.fret > passage.guitar.highest_fret)));
  if (offNeck) {
    element('shape-status').textContent = strings ? 'can\'t: a note would leave the strings or the neck' : 'can\'t: a note would leave the neck';
    element('shape-status').className = '';
    return;
  }
  shape.choices = moved;
  shape.pitches = shape.pitches.map((pitch) => (pitch === null ? null : pitch + pitchMove));
  shape.fretShift += strings ? 0 : frets;
  shape.stringShift += strings;
  playShape();
}

function resetShape() {
  const phrase = catalogue.phrase;
  catalogue.shape = { choices: structuredClone(phrase.choices), pitches: phrase.passage.notes.map((note) => note.pitch),
                      fretShift: 0, stringShift: 0 };
  playShape();
}

/** Draw and play the player's shape as moved, and say which layout of this path it now is, if any. */
function playShape() {
  const { passage, positions } = catalogue.phrase;
  const shape = catalogue.shape;
  const moved = shape.fretShift !== 0 || shape.stringShift !== 0;
  const notes = passage.notes.map((note, index) => ({ ...note, pitch: shape.pitches[index] }));
  const shownPassage = { ...passage, notes };
  showPhrase({
    passage: shownPassage, choices: shape.choices,
    positions: moved ? notes.map((note) => positionsOf(note.pitch, passage.guitar)) : positions,
    label: `${catalogue.entry.entry.layout} · ${passage.name}${moved ? ' (moved)' : ''}`,
  });
  showShapeStatus(shapeLayout(shownPassage, shape.choices), moved);
}

/** Every (string, fret) that sounds `pitch` on a guitar; none for a null symbol. */
function positionsOf(pitch, guitar) {
  if (pitch === null) return [];
  return guitar.tuning.map((open, index) => ({ string: index + 1, fret: pitch - open }))
    .filter((position) => position.fret >= 0 && position.fret <= guitar.highest_fret);
}

/** The catalogue layout text of a phrase's counted notes (grip.catalogue.run_keys: string:fret per note, frets
 *  relative to each stretch's first fretted note, "o" open, "h12" harmonic, "/" slide landing, "b" bent). */
function shapeLayout(passage, choices) {
  const counted = passage.notes.map((note, index) => index).filter((index) => passage.notes[index].counted && choices[index].string !== null);
  const kindOf = (index) => {
    if (passage.notes[index].catalogue_kind === 'harmonic' && choices[index].fret === catalogue.phrase.choices[index].fret) return 'harmonic';
    return choices[index].fret === 0 ? 'open' : 'fretted';
  };
  // Stretches: the run start, then every slide landing; frets count from each stretch's first fretted note
  const stretchOf = [];
  let stretch = 0;
  counted.forEach((index, position) => {
    if (position > 0 && passage.notes[index].articulations.includes('slide')) stretch += 1;
    stretchOf.push(stretch);
  });
  const anchorFret = {};
  counted.forEach((index, position) => {
    if (kindOf(index) === 'fretted' && !(stretchOf[position] in anchorFret)) anchorFret[stretchOf[position]] = choices[index].fret;
  });
  return counted.map((index, position) => {
    const choice = choices[index];
    const kind = kindOf(index);
    let place;
    if (kind === 'open') place = 'o';
    else if (kind === 'harmonic') place = `h${choice.fret}`;
    else {
      const relative = choice.fret - anchorFret[stretchOf[position]];
      place = relative > 0 ? `+${relative}` : String(relative);
    }
    const slide = position > 0 && passage.notes[index].articulations.includes('slide') ? '/' : '';
    const bent = passage.notes[index].articulations.includes('bend') ? 'b' : '';
    return `${choice.string}:${slide}${place}${bent}`;
  }).join(' ');
}

/** "moved +2 frets, 1 string up · = a layout in 3 songs" / "· not in the catalogue"; the matching card lights up. */
function showShapeStatus(layout, moved) {
  const match = catalogue.path?.layouts.find((entry) => entry.layout === layout);
  const shape = catalogue.shape;
  const parts = [];
  if (shape.fretShift) parts.push(`${shape.fretShift > 0 ? '+' : ''}${shape.fretShift} fret${Math.abs(shape.fretShift) === 1 ? '' : 's'}`);
  if (shape.stringShift) parts.push(`${Math.abs(shape.stringShift)} string${Math.abs(shape.stringShift) === 1 ? '' : 's'} ${shape.stringShift < 0 ? 'up' : 'down'}`);
  const status = element('shape-status');
  if (!moved) status.textContent = 'as played';
  else status.textContent = `moved ${parts.join(', ')} · ` + (match ? `= a layout of this path in ${match.song_count} songs` : 'not in the catalogue');
  status.className = moved && match ? 'match' : '';
  for (const card of element('catalogue-layouts').children) {
    card.classList.toggle('shape-match', moved && card.dataset.entryId === String(match?.entry_id));
  }
}

function showPlayer() {
  const phrase = catalogue.phrase;
  for (const id of ['shape-fret-down', 'shape-fret-up', 'shape-string-up', 'shape-string-down', 'shape-reset']) element(id).disabled = !phrase;
  if (!phrase) element('shape-status').textContent = '';
  element('occurrence-previous').disabled = !phrase || phrase.index <= 0;
  element('occurrence-next').disabled = !phrase || phrase.index >= phrase.total - 1;
  element('occurrence-open-song').disabled = !phrase;
  showFlagButton(element('occurrence-flag'), phrase?.occurrence.junk ? {} : null, Boolean(phrase));
  element('occurrence-label').textContent = phrase
    ? `occurrence ${phrase.index + 1} of ${phrase.total} · ${phrase.occurrence.path} · track ${phrase.occurrence.track_index}: ${phrase.occurrence.track_name} · bar ${phrase.occurrence.bar}`
    : 'Pick a layout to play one of its occurrences';
}

/** Flag (or un-flag) the track the player's occurrence comes from as junk. */
function flagPlayingTrack() {
  const occurrence = catalogue.phrase.occurrence;
  openFlagBox({
    gpPath: occurrence.gp_path, trackIndex: occurrence.track_index, trackCount: null,
    title: `${occurrence.path} · track ${occurrence.track_index}: ${occurrence.track_name}`,
    onChange: async (songFlags) => {
      occurrence.junk = String(occurrence.track_index) in songFlags;
      showPlayer();
      await showOccurrencePage(catalogue.entry.occurrences.page);
    },
  });
}

// ---- Middle and bottom: layouts, family, where it turns up ----

function showDetail() {
  const path = catalogue.path;
  element('catalogue-back').disabled = catalogue.history.length === 0;
  element('catalogue-detail').classList.toggle('empty', !path);
  if (!path) {
    element('catalogue-title').textContent = 'Pick an interval path on the left';
    for (const id of ['catalogue-family', 'catalogue-layouts', 'catalogue-occurrences']) element(id).replaceChildren();
    return;
  }
  const row = path.path;
  element('catalogue-title').textContent = `${displayPath(row.interval_path)}  ·  ${row.length} notes · ${row.song_count} songs · ${row.layout_count} layouts`;
  element('catalogue-layouts').replaceChildren(...path.layouts.map(layoutItem));
  showFamily();
  showOccurrences();
  fitAllTabs();
}

/** One layout card: its tab in full, songs, occurrences, fragment / below-the-line tags. Click picks it. */
function layoutItem(entry) {
  const item = Object.assign(document.createElement('div'), { className: 'layout-item' });
  item.dataset.entryId = String(entry.entry_id);
  if (!entry.is_member) item.classList.add('below-line');
  if (catalogue.entry?.entry.entry_id === entry.entry_id) item.classList.add('selected');
  const tags = [entry.is_fragment ? 'fragment' : null, entry.is_member ? null : 'below the line'].filter(Boolean);
  const stats = Object.assign(document.createElement('div'), { className: 'layout-stats' });
  stats.append(
    Object.assign(document.createElement('strong'), { textContent: `${entry.song_count} songs` }),
    Object.assign(document.createElement('span'), { textContent: `${entry.occurrence_count}×` }),
    ...tags.map((tag) => Object.assign(document.createElement('span'), { className: 'tag', textContent: tag })),
  );
  item.append(tabElement(entry.layout, entry.interval_path, 'layout-tab', LAYOUT_TAB_FONT), stats);
  item.onclick = () => {
    if (catalogue.entry?.entry.entry_id === entry.entry_id) return;
    catalogue.history.push({ interval_path: catalogue.path.path.interval_path, entry_id: catalogue.entry?.entry.entry_id ?? null });
    pickEntry(entry.entry_id);
  };
  return item;
}

/** The family strip: paths one note away, then the picked layout's own family (layouts inside it / containing it). */
function showFamily() {
  const blocks = [familyBlock('Related paths (one note away)', catalogue.path.family, familyPath)];
  if (catalogue.entry) blocks.push(familyBlock('Related layouts (one note away)', catalogue.entry.family, familyLayout));
  element('catalogue-family').replaceChildren(...blocks.flat());
}

function familyBlock(title, family, buildItem) {
  const heading = Object.assign(document.createElement('div'), { className: 'family-heading', textContent: title });
  const groups = Object.entries(FAMILY_TITLES).map(([key, label]) => {
    const group = Object.assign(document.createElement('div'), { className: 'family-group' });
    group.append(Object.assign(document.createElement('span'), { className: 'family-label', textContent: label }));
    const items = Object.assign(document.createElement('div'), { className: 'family-items' });
    if (!family[key].length) items.append(Object.assign(document.createElement('span'), { className: 'family-none', textContent: 'none' }));
    for (const member of family[key]) items.append(buildItem(member));
    group.append(items);
    return group;
  });
  return [heading, ...groups];
}

function familyPath(path) {
  const button = Object.assign(document.createElement('button'), { type: 'button', className: 'family-item' });
  if (path.member_layout_count === 0) button.classList.add('below-line');
  button.append(
    Object.assign(document.createElement('span'), { className: 'path-text', textContent: displayPath(path.interval_path) }),
    Object.assign(document.createElement('span'), { className: 'badge', textContent: `${path.song_count}` }),
  );
  button.title = `${path.song_count} songs · ${path.layout_count} layouts` + (path.member_layout_count ? '' : ' · below the member line');
  button.onclick = () => pickPath(path.interval_path);
  return button;
}

function familyLayout(entry) {
  const button = Object.assign(document.createElement('button'), { type: 'button', className: 'family-item layout' });
  if (!entry.is_member) button.classList.add('below-line');
  button.append(tabElement(entry.layout, entry.interval_path, 'family-tab', FAMILY_TAB_FONT),
    Object.assign(document.createElement('span'), { className: 'badge', textContent: `${entry.song_count}` }));
  button.title = `${displayPath(entry.interval_path)} · ${entry.song_count} songs`;
  button.onclick = () => pickPath(entry.interval_path, entry.entry_id);
  return button;
}

/** Where the picked layout turns up, grouped by song: click one to play it here; "song" opens the whole song. */
function showOccurrences() {
  const box = element('catalogue-occurrences');
  if (!catalogue.entry) {
    box.replaceChildren();
    return;
  }
  const { entry, occurrences } = catalogue.entry;
  const heading = Object.assign(document.createElement('div'), { className: 'family-heading' });
  heading.textContent = `Where it turns up · ${occurrences.total} times in ${entry.song_count} songs`;
  const list = Object.assign(document.createElement('ul'), { className: 'occurrence-list' });
  let lastSong = null;
  occurrences.rows.forEach((occurrence, rowIndex) => {
    const song = `${occurrence.path} · track ${occurrence.track_index}: ${occurrence.track_name}`;
    if (song !== lastSong) {
      const header = Object.assign(document.createElement('li'), { className: 'occurrence-song', textContent: song });
      if (occurrence.junk) header.append(Object.assign(document.createElement('span'), { className: 'badge junk', textContent: 'junk' }));
      list.append(header);
      lastSong = song;
    }
    const index = (occurrences.page - 1) * occurrences.page_size + rowIndex;
    const row = Object.assign(document.createElement('li'), { className: 'occurrence' });
    row.dataset.index = String(index);
    const play = Object.assign(document.createElement('span'), { textContent: `bar ${occurrence.bar} · note ${occurrence.line_position + 1}` });
    const open = Object.assign(document.createElement('button'), { type: 'button', className: 'occurrence-open', textContent: 'song', title: 'Open the whole song at this phrase (song view)' });
    open.onclick = (event) => { event.stopPropagation(); openPhrase(occurrence); };
    row.append(play, open);
    row.title = 'Play it in the player above';
    row.onclick = () => playOccurrence(index);
    list.append(row);
  });
  const nav = Object.assign(document.createElement('div'), { className: 'page-nav' });
  if (occurrences.page_count > 1) {
    const previous = Object.assign(document.createElement('button'), { textContent: '◀', disabled: occurrences.page <= 1 });
    const next = Object.assign(document.createElement('button'), { textContent: '▶', disabled: occurrences.page >= occurrences.page_count });
    previous.onclick = () => showOccurrencePage(occurrences.page - 1);
    next.onclick = () => showOccurrencePage(occurrences.page + 1);
    nav.append(previous, ` page ${occurrences.page} of ${occurrences.page_count} `, next);
  }
  box.replaceChildren(heading, list, nav);
  markPlayingOccurrence();
}

async function showOccurrencePage(page) {
  const detail = await fetchJson('/api/catalogue/entry', { entry_id: catalogue.entry.entry.entry_id, page });
  if (!detail) return;
  catalogue.entry = detail;
  showOccurrences();
}

function markPlayingOccurrence() {
  for (const row of element('catalogue-occurrences').querySelectorAll('.occurrence')) {
    row.classList.toggle('playing', Number(row.dataset.index) === catalogue.phrase?.index);
  }
}
