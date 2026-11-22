// GRIP viewer, Stitch + Entries: catalogue blocks on top of the tab (server: stitch.py).
//   - Stitch = Optimise with the "catalogue" method: rebuild the Bars box's passage out of the picked catalogue's
//     entries, shown like a run with one realisation. Two modes: position preserved (blocks only on the tab's own
//     strings and frets) and notes only (Stitch places each block where its notes fit).
//   - Entries = the same blocks for whatever realisation is on screen (an optimiser run, a segment of the lr-knob
//     sweep, an annotation), on that realisation's own strings and frets; on / off from the top bar.
// Either way: a thin box per block (a block that wraps onto the next line gets a box per line) with a badge
// (hover = its entry, click = open it in the catalogue view), and the notes no block holds in red (where the glue
// notes go). viewer.mjs calls setupStitch once at start, runStitch from the Optimise button, and asks for the marks.
import * as alphaTab from '/alphatab/alphaTab.mjs';

// What viewer.mjs shares, set once by setupStitch
let element, statusLabel, api, state, postJson, remember, recall, readSettings, sameSettings, renderView;
let confirmDiscardAnnotation, openRunView, openCatalogueEntry, currentBeatMap, buildBeatMap, currentCatalogue;
let shownPassage, shownPassageSettings;

const UNCOVERED_COLOUR = new alphaTab.model.Color(192, 57, 43);
// Room (px) between a block's notes and its box; the badge sits just above the box
const BOX_PADDING = 4;
const BADGE_RISE = 14;
// Entries answers kept in this page (dragging the lr-knob back and forth asks for the same tabs again)
const ENTRIES_KEPT = 64;

let mode = 'position';
let entriesOn = false;
const entriesAnswers = new Map();  // request key → the server's answer
let entriesPending = null;          // request key being asked for

/** Take what viewer.mjs shares and wire the controls. Returns { runStitch, colourUncovered, drawBlockBoxes, updateEntriesButton }. */
export function setupStitch(viewer) {
  ({ element, statusLabel, api, state, postJson, remember, recall, readSettings, sameSettings, renderView } = viewer);
  ({ confirmDiscardAnnotation, openRunView, openCatalogueEntry, currentBeatMap, buildBeatMap, currentCatalogue } = viewer);
  ({ shownPassage, shownPassageSettings } = viewer);
  mode = recall('stitchMode', 'position');
  markMode();
  for (const button of document.querySelectorAll('#stitch-mode button')) {
    button.onclick = () => { mode = button.dataset.mode; remember('stitchMode', mode); markMode(); };
  }
  element('stitch-members-only').checked = recall('stitchMembersOnly', '1') === '1';
  element('stitch-members-only').onchange = () => {
    remember('stitchMembersOnly', element('stitch-members-only').checked ? '1' : '0');
    if (entriesOn) renderView(true);
  };
  entriesOn = recall('entriesOn', '0') === '1';
  element('entries-toggle').onclick = () => {
    entriesOn = !entriesOn;
    remember('entriesOn', entriesOn ? '1' : '0');
    renderView(true);
  };
  return { runStitch, colourUncovered, drawBlockBoxes, updateEntriesButton };
}

function markMode() {
  for (const button of document.querySelectorAll('#stitch-mode button')) {
    button.setAttribute('aria-pressed', String(button.dataset.mode === mode));
  }
}

/** Stitch the Bars box's passage out of the picked catalogue and show it like a run. Keeps an annotation of the same passage. */
async function runStitch() {
  const settings = readSettings();
  if (!settings) return;
  const build = currentCatalogue();
  if (!build) {
    statusLabel.textContent = 'pick a catalogue first (top bar, left)';
    return;
  }
  const keepAnnotation = state.annotation && sameSettings(state.annotation.settings, settings);
  if (state.annotation && !keepAnnotation && !confirmDiscardAnnotation('Stitch a different passage')) return;
  const button = element('run-button');
  button.disabled = true;
  element('run-popover').hidden = true;
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

// ---- Entries: the blocks of whatever realisation is on screen ----

/** The Entries request for what is on screen ({ key, body }), or null if Entries can't apply (off, no catalogue,
 *  a Stitch run (it shows its own blocks), the catalogue player, or a passage with no GP file behind it). */
function entriesRequest() {
  if (!entriesOn || !currentCatalogue() || (state.run?.kind === 'stitch' && state.view?.kind === 'realisation')) return null;
  const shown = shownPassage();
  const settings = shown && state.view.kind !== 'phrase' ? shownPassageSettings() : null;
  if (!settings) return null;
  const choices = shown.choices.map((choice) => ({ passage_index: choice.passage_index, string: choice.string, fret: choice.fret }));
  const body = { ...settings, build: currentCatalogue(), members_only: element('stitch-members-only').checked, choices };
  return { key: JSON.stringify(body), body };
}

/** Ask the server for the Entries of what is on screen (once per tab); redraws when the answer comes, if it still applies. */
async function fetchEntries(request) {
  if (entriesPending === request.key) return;
  entriesPending = request.key;
  statusLabel.textContent = 'finding entries…';
  const reply = await postJson('/api/entries', request.body);
  if (entriesPending === request.key) entriesPending = null;
  if (reply.error) {
    statusLabel.textContent = `Entries failed: ${reply.error}`;
    return;
  }
  entriesAnswers.set(request.key, reply);
  if (entriesAnswers.size > ENTRIES_KEPT) entriesAnswers.delete(entriesAnswers.keys().next().value);
  if (entriesRequest()?.key !== request.key) return;  // the tab changed meanwhile
  statusLabel.textContent = `entries: ${reply.report.at(-1)}`;
  renderView(true);
}

/** The Entries button: on only while a catalogue is picked; pressed while Entries is on. */
function updateEntriesButton() {
  const button = element('entries-toggle');
  button.disabled = !currentCatalogue();
  button.setAttribute('aria-pressed', String(entriesOn && !button.disabled));
}

/** The blocks and uncovered notes to mark on the tab on screen: the open Stitch's, or the Entries of what is shown
 *  (null until the server answers; asking it is started here). */
function shownStitch() {
  if (state.view?.kind === 'realisation' && state.run?.kind === 'stitch') return state.run.stitch;
  const request = entriesRequest();
  if (!request) return null;
  const answer = entriesAnswers.get(request.key);
  if (!answer) fetchEntries(request);
  return answer ?? null;
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
