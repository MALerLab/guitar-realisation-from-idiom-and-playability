// GRIP viewer, Stitch: rebuild some bars out of one catalogue build's entries (server: stitch.py),
// shown like a run with one realisation. On top of the tab: a thin box per block (a block that wraps onto the
// next line gets a box per line) with a badge (hover = its entry, click = open it in the catalogue view), and
// the notes no block holds in red (where the glue notes go). Two modes: position preserved (blocks only on the
// tab's own strings and frets) and notes only (Stitch places each block where its notes fit).
// The pop-up's bar boxes are twins of the Optimise pop-up's: one passage, typed or dragged in either.
// viewer.mjs calls setupStitch once at start; it opens the pop-up (openPopover) and asks for the marks.
import * as alphaTab from '/alphatab/alphaTab.mjs';

// What viewer.mjs shares, set once by setupStitch
let element, statusLabel, api, state, postJson, remember, recall, readSettings, sameSettings;
let confirmDiscardAnnotation, openRunView, openCatalogueEntry, currentBeatMap, buildBeatMap;

const UNCOVERED_COLOUR = new alphaTab.model.Color(192, 57, 43);
// Room (px) between a block's notes and its box; the badge sits just above the box
const BOX_PADDING = 4;
const BADGE_RISE = 14;

// Stitch pop-up box → its twin in the Optimise pop-up (the one readSettings reads)
const TWIN_BOXES = {
  'stitch-first-bar': 'first-bar', 'stitch-last-bar': 'last-bar', 'stitch-voice': 'voice', 'stitch-tuning-shift': 'tuning-shift',
};

let mode = 'position';

/** Take what viewer.mjs shares and wire the pop-up's controls. Returns { openPopover, colourUncovered, drawBlockBoxes }. */
export function setupStitch(viewer) {
  ({ element, statusLabel, api, state, postJson, remember, recall, readSettings, sameSettings } = viewer);
  ({ confirmDiscardAnnotation, openRunView, openCatalogueEntry, currentBeatMap, buildBeatMap } = viewer);
  mode = recall('stitchMode', 'position');
  markMode();
  for (const button of document.querySelectorAll('#stitch-mode button')) {
    button.onclick = () => { mode = button.dataset.mode; remember('stitchMode', mode); markMode(); };
  }
  element('stitch-members-only').checked = recall('stitchMembersOnly', '1') === '1';
  element('stitch-members-only').onchange = () => remember('stitchMembersOnly', element('stitch-members-only').checked ? '1' : '0');
  element('stitch-build').onchange = () => remember('stitchBuild', element('stitch-build').value);
  element('stitch-button').onclick = stitch;
  for (const [id, twin] of Object.entries(TWIN_BOXES)) element(id).oninput = () => { element(twin).value = element(id).value; };
  return { openPopover, colourUncovered, drawBlockBoxes };
}

function markMode() {
  for (const button of document.querySelectorAll('#stitch-mode button')) {
    button.setAttribute('aria-pressed', String(button.dataset.mode === mode));
  }
}

/** Fill the pop-up as it opens: the bar boxes from their twins, and the catalogue builds (newest first, the last one picked kept). */
async function openPopover() {
  for (const [id, twin] of Object.entries(TWIN_BOXES)) element(id).value = element(twin).value;
  const response = await fetch('/api/catalogue/builds');
  if (!response.ok) {
    statusLabel.textContent = `server: ${response.status} ${response.statusText}`;
    return;
  }
  const builds = await response.json();
  const select = element('stitch-build');
  // Shown as <folder under the storage root>/<build>, e.g. catalogue/bpe/261118_goat_test
  select.replaceChildren(...builds.map((build) => new Option(`${build.folder}/${build.name}`, build.path)));
  const wanted = recall('stitchBuild', '');
  if (builds.some((build) => build.path === wanted)) select.value = wanted;
}

/** Stitch the bar boxes' passage out of the picked build and show it like a run. Keeps an annotation of the same passage. */
async function stitch() {
  const settings = readSettings();
  if (!settings) return;
  const build = element('stitch-build').value;
  if (!build) {
    statusLabel.textContent = 'no catalogue build in the storage folder';
    return;
  }
  const keepAnnotation = state.annotation && sameSettings(state.annotation.settings, settings);
  if (state.annotation && !keepAnnotation && !confirmDiscardAnnotation('Stitch a different passage')) return;
  const button = element('stitch-button');
  button.disabled = true;
  element('stitch-popover').hidden = true;
  statusLabel.textContent = 'stitching…';
  const reply = await postJson('/api/stitch', { ...settings, build, mode, members_only: element('stitch-members-only').checked });
  button.disabled = false;
  if (reply.error) {
    statusLabel.textContent = `Stitch failed: ${reply.error}`;
    return;
  }
  api.stop();
  await openRunView({ ...reply, path: null }, keepAnnotation);
  statusLabel.textContent = reply.report.at(-1) ?? '';
}

/** The open Stitch's blocks and uncovered notes while its realisation is shown, else null. */
function shownStitch() {
  return state.view?.kind === 'realisation' && state.run?.kind === 'stitch' ? state.run.stitch : null;
}

/** Colour the fret numbers of the notes no block holds (called on the score before it is drawn). */
function colourUncovered(score) {
  const stitchResult = shownStitch();
  if (!stitchResult) return;
  const { firstBeatOfNote } = buildBeatMap(score.tracks[0]);
  for (const index of stitchResult.uncovered) {
    const beat = firstBeatOfNote[index];
    if (!beat || beat.isRest) continue;
    const style = new alphaTab.model.NoteStyle();
    style.colors.set(alphaTab.model.NoteSubElement.GuitarTabFretNumber, UNCOVERED_COLOUR);
    beat.notes[0].style = style;
  }
}

/** The drawn box of one beat: its note head, or the whole beat for a rest. */
function beatRectangle(beat, beatBounds) {
  if (beat.isRest) return beatBounds.visualBounds;
  return beatBounds.notes?.find((bounds) => bounds.note === beat.notes[0])?.noteHeadBounds ?? beatBounds.visualBounds;
}

/** One thin box per block and per line it sits on, with a badge on its first box (after each drawing). */
function drawBlockBoxes() {
  const container = element('stitch-boxes');
  container.replaceChildren();
  const stitchResult = shownStitch();
  const lookup = api.renderer.boundsLookup;
  if (!stitchResult || !lookup) return;
  const { beatsInOrder, noteOfBeat } = currentBeatMap();
  const score = element('score');
  for (const block of stitchResult.blocks) {
    // Rectangles of the block's beats (tied pieces included), grouped by the line (staff system) they sit on
    const lines = new Map();
    for (const beat of beatsInOrder) {
      const index = noteOfBeat.get(beat.id);
      if (index === null || index === undefined || index < block.passage_first || index > block.passage_last) continue;
      const beatBounds = lookup.findBeat(beat);
      if (!beatBounds) continue;
      const line = beatBounds.barBounds?.masterBarBounds?.staffSystemBounds ?? Math.round(beatBounds.visualBounds.y);
      if (!lines.has(line)) lines.set(line, []);
      lines.get(line).push(beatRectangle(beat, beatBounds));
    }
    let first = true;
    for (const rectangles of lines.values()) {
      const left = Math.min(...rectangles.map((r) => r.x)) - BOX_PADDING;
      const top = Math.min(...rectangles.map((r) => r.y)) - BOX_PADDING;
      const right = Math.max(...rectangles.map((r) => r.x + r.w)) + BOX_PADDING;
      const bottom = Math.max(...rectangles.map((r) => r.y + r.h)) + BOX_PADDING;
      const box = document.createElement('div');
      box.className = 'stitch-box';
      Object.assign(box.style, {
        left: `${score.offsetLeft + left}px`, top: `${score.offsetTop + top}px`, width: `${right - left}px`, height: `${bottom - top}px`,
      });
      container.append(box);
      if (first) container.append(blockBadge(block, stitchResult.build, score.offsetLeft + left, score.offsetTop + top - BADGE_RISE));
      first = false;
    }
  }
}

/** A block's badge: its song count; hover shows the entry, click opens it in the catalogue view. */
function blockBadge(block, build, left, top) {
  const badge = document.createElement('div');
  badge.className = 'stitch-badge';
  badge.textContent = String(block.song_count);
  badge.title = `${block.length} notes · in ${block.song_count} songs · entry ${block.entry_id}\n`
    + `interval path ${block.interval_path}\nlayout ${block.layout}\nclick: open it in the catalogue view`;
  Object.assign(badge.style, { left: `${left}px`, top: `${top}px` });
  badge.onclick = () => openCatalogueEntry(build, block.interval_path, block.entry_id);
  return badge;
}
