// GRIP viewer, Entries: which catalogue entries the tab on screen is made of, drawn on top of it.
//   - a tab the chooser made (Optimise or the Optimiser view with a catalogue picked): the entries it played
//   - any other tab (a run without the chooser, an annotation): the entries of the picked catalogue it is made of,
//     on its own strings and frets (server: stitch.py, /api/entries)
// A thin box per block (a block that wraps onto the next line gets a box per line) with a badge (hover = its entry,
// click = open it in the catalogue view), and the notes no block holds in red (where the glue notes go).
// viewer.mjs calls setupStitch once at start and asks for the marks after every drawing.
import * as alphaTab from '/alphatab/alphaTab.mjs';

// What viewer.mjs shares, set once by setupStitch
let element, statusLabel, api, state, postJson, remember, recall, renderView;
let openCatalogueEntry, currentBeatMap, buildBeatMap, currentCatalogue, shownPassage, shownPassageSettings;

const UNCOVERED_COLOUR = new alphaTab.model.Color(192, 57, 43);
// Room (px) between a block's notes and its box; the badge sits just above the box
const BOX_PADDING = 4;
const BADGE_RISE = 14;
// Entries answers kept in this page (dragging the lr-knob back and forth asks for the same tabs again)
const ENTRIES_KEPT = 64;

let entriesOn = false;
const entriesAnswers = new Map();  // request key → the server's answer
let entriesPending = null;          // request key being asked for

/** Take what viewer.mjs shares and wire the controls. Returns { colourUncovered, drawBlockBoxes, updateEntriesButton, membersOnly }. */
export function setupStitch(viewer) {
  ({ element, statusLabel, api, state, postJson, remember, recall, renderView } = viewer);
  ({ openCatalogueEntry, currentBeatMap, buildBeatMap, currentCatalogue, shownPassage, shownPassageSettings } = viewer);
  element('menu-members-only').checked = recall('menuMembersOnly', '1') === '1';
  element('menu-members-only').onchange = () => {
    remember('menuMembersOnly', element('menu-members-only').checked ? '1' : '0');
    if (entriesOn) renderView(true);
  };
  entriesOn = recall('entriesOn', '0') === '1';
  element('entries-toggle').onclick = () => {
    entriesOn = !entriesOn;
    remember('entriesOn', entriesOn ? '1' : '0');
    renderView(true);
  };
  return { colourUncovered, drawBlockBoxes, updateEntriesButton, membersOnly };
}

/** The "members only" tick: the chooser's menu and Entries use only entries past the membership line. */
function membersOnly() {
  return element('menu-members-only').checked;
}

// ---- The chooser's own entries ----

/** What the chooser ran with for the tab on screen ({ build, menu_items, … }), or null (no chooser behind it). */
function chooserOfShown() {
  if (state.view?.kind === 'realisation') return state.run?.chooser ?? null;
  if (state.view?.kind === 'segment') return state.sweep?.chooser ?? null;
  return null;
}

/** The chooser's blocks for the tab on screen, the loose notes as the uncovered ones; null if no chooser made it. */
function chooserBlocks() {
  const chooser = chooserOfShown();
  const shown = shownPassage();
  if (!chooser || !shown?.realisation) return null;
  const blocks = shown.realisation.blocks ?? [];
  const covered = new Set(blocks.flatMap((block) => block.passage_indices));
  const uncovered = shown.passage.notes.map((note, index) => index).filter((index) => shown.passage.notes[index].pitch !== null && !covered.has(index));
  return { build: chooser.build, blocks, uncovered };
}

// ---- Entries of any other tab ----

/** The Entries request for what is on screen ({ key, body }), or null if it can't apply (no catalogue, the catalogue
 *  player, or a passage with no GP file behind it). */
function entriesRequest() {
  if (!currentCatalogue()) return null;
  const shown = shownPassage();
  const settings = shown && state.view.kind !== 'phrase' ? shownPassageSettings() : null;
  if (!settings) return null;
  const choices = shown.choices.map((choice) => ({ passage_index: choice.passage_index, string: choice.string, fret: choice.fret }));
  const body = { ...settings, build: currentCatalogue(), members_only: membersOnly(), choices };
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
  if (!entriesOn || entriesRequest()?.key !== request.key) return;  // the tab changed meanwhile
  statusLabel.textContent = `entries: ${reply.report.at(-1)}`;
  renderView(true);
}

/** The Entries button: on while a catalogue is picked or the tab on screen came from the chooser; pressed while on. */
function updateEntriesButton() {
  const button = element('entries-toggle');
  button.disabled = !currentCatalogue() && !chooserOfShown();
  button.setAttribute('aria-pressed', String(entriesOn && !button.disabled));
}

/** The blocks and uncovered notes to mark on the tab on screen (null = none): the chooser's own while it made the
 *  tab, else the Entries of the picked catalogue (null until the server answers; asking it is started here). */
function shownStitch() {
  if (!entriesOn) return null;
  const fromChooser = chooserBlocks();
  if (fromChooser) return fromChooser;
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
