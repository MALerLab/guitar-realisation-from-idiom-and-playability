// GRIP viewer, piano roll: a MIDI file in the song view (alphaTab can't read MIDI). Rows of bars, one bar of notes
// per column block, bar numbers on the bar lines; click or drag over bars (shift-click spans) to fill the Bars box,
// and the playhead follows playback. Playback itself is alphaTab's: viewer.mjs draws the whole file as a tab placed
// lowest fret first, hidden under the roll, so the transport bar, loop and speed work as for any song.
// viewer.mjs calls setupPianoRoll once at start and draw() after every drawing.

// What viewer.mjs shares, set once by setupPianoRoll
let element, state, api, statusLabel, currentBeatMap, updateFretboard;

const SVG_NAMESPACE = 'http://www.w3.org/2000/svg';
const BARS_PER_ROW = 4;
// Heights (px): one semitone, the bar-number band over each row, the gap between rows; width kept for pitch labels
const SEMITONE_HEIGHT = 5;
const BAR_LABEL_HEIGHT = 14;
const ROW_GAP = 14;
const LABEL_WIDTH = 34;
// Semitones of room above and below the file's highest and lowest note
const PITCH_MARGIN = 1;
const NOTE_NAMES = ['C', 'C#', 'D', 'D#', 'E', 'F', 'F#', 'G', 'G#', 'A', 'A#', 'B'];

let dragStartBar = null;  // where a press started, while the button is held
let dragEndBar = null;    // the bar the pointer is over during that press
let anchorBar = null;     // the last bar pressed (shift-click spans from it)
let layout = null;  // the last drawing's geometry: { ticksPerBar, rowWidth, rowHeight, lowest, highest }

/** Take what viewer.mjs shares; follow playback with the playhead. Returns { draw, shown, markChosenBars }. */
export function setupPianoRoll(viewer) {
  ({ element, state, api, statusLabel, currentBeatMap, updateFretboard } = viewer);
  api.playedBeatChanged.on((beat) => movePlayhead(beat));
  // A press ends wherever the button comes up (even off the roll): only then do the bars become the playback range
  document.addEventListener('pointerup', finishDrag);
  return { draw, shown, markChosenBars };
}

/** True while the song view shows a MIDI file. */
function shown() {
  return state.view?.kind === 'song' && state.song?.kind === 'midi';
}

function svgElement(tag, attributes, text = null) {
  const node = document.createElementNS(SVG_NAMESPACE, tag);
  for (const [name, value] of Object.entries(attributes)) node.setAttribute(name, value);
  if (text !== null) node.textContent = text;
  return node;
}

/** The Bars box's bars as numbers, or null if it isn't filled. */
function chosenBars() {
  const first = Number(element('first-bar').value);
  const last = Number(element('last-bar').value);
  return first && last ? [first, last] : null;
}

/** Where a row starts (y, px) and where a tick sits in it (x, px). */
function rowTop(row) {
  return row * (layout.rowHeight + ROW_GAP);
}
function tickX(tick, row) {
  return LABEL_WIDTH + ((tick - row * BARS_PER_ROW * layout.ticksPerBar) / (BARS_PER_ROW * layout.ticksPerBar)) * layout.rowWidth;
}

/** Draw the roll (or empty it when the song view doesn't show a MIDI file). */
function draw() {
  const container = element('piano-roll');
  if (!shown()) {
    container.replaceChildren();
    return;
  }
  const { notes, ticks_per_bar: ticksPerBar, bar_count: barCount } = state.song.midi;
  const pitches = notes.map(([, , pitch]) => pitch);
  const lowest = Math.min(...pitches) - PITCH_MARGIN;
  const highest = Math.max(...pitches) + PITCH_MARGIN;
  const width = container.clientWidth || 800;
  layout = {
    ticksPerBar, lowest, highest, rowWidth: width - LABEL_WIDTH - 4,
    rowHeight: BAR_LABEL_HEIGHT + (highest - lowest + 1) * SEMITONE_HEIGHT,
  };
  const rows = Math.ceil(barCount / BARS_PER_ROW);
  const svg = svgElement('svg', { width, height: rows * (layout.rowHeight + ROW_GAP) });
  const chosen = chosenBars();
  const pitchY = (pitch, row) => rowTop(row) + BAR_LABEL_HEIGHT + (highest - pitch) * SEMITONE_HEIGHT;

  for (let row = 0; row < rows; row += 1) {
    const top = rowTop(row) + BAR_LABEL_HEIGHT;
    svg.append(svgElement('rect', { x: LABEL_WIDTH, y: top, width: layout.rowWidth, height: layout.rowHeight - BAR_LABEL_HEIGHT, class: 'roll-row' }));
    // A faint line + label on every C
    for (let pitch = lowest; pitch <= highest; pitch += 1) {
      if (pitch % 12 !== 0) continue;
      const y = pitchY(pitch, row) + SEMITONE_HEIGHT / 2;
      svg.append(svgElement('line', { x1: LABEL_WIDTH, x2: LABEL_WIDTH + layout.rowWidth, y1: y, y2: y, class: 'roll-octave' }));
      svg.append(svgElement('text', { x: 0, y: y + 3, class: 'roll-pitch' }, `${NOTE_NAMES[pitch % 12]}${Math.floor(pitch / 12) - 1}`));
    }
    // Bars: shading if chosen, a click / drag target, the bar line and its number
    for (let slot = 0; slot < BARS_PER_ROW; slot += 1) {
      const bar = row * BARS_PER_ROW + slot + 1;
      if (bar > barCount) break;
      const x = tickX((bar - 1) * ticksPerBar, row);
      const barWidth = layout.rowWidth / BARS_PER_ROW;
      const inChosen = chosen && bar >= chosen[0] && bar <= chosen[1];
      const target = svgElement('rect', { x, y: rowTop(row), width: barWidth, height: layout.rowHeight, class: `roll-bar${inChosen ? ' chosen' : ''}` });
      target.dataset.bar = String(bar);
      target.addEventListener('pointerdown', (event) => barPressed(bar, event));
      target.addEventListener('pointerenter', () => dragOver(bar));
      svg.append(target);
      svg.append(svgElement('line', { x1: x, x2: x, y1: top, y2: rowTop(row) + layout.rowHeight, class: 'roll-barline' }));
      svg.append(svgElement('text', { x: x + 3, y: rowTop(row) + BAR_LABEL_HEIGHT - 3, class: 'roll-bar-number' }, String(bar)));
    }
  }
  // Notes (a note running past its row's end goes on in the next row)
  for (const [onset, end, pitch] of notes) {
    for (let row = Math.floor(onset / (BARS_PER_ROW * ticksPerBar)); row < rows; row += 1) {
      const rowStart = row * BARS_PER_ROW * ticksPerBar;
      const rowEnd = rowStart + BARS_PER_ROW * ticksPerBar;
      if (onset >= rowEnd) continue;
      if (end <= rowStart) break;
      const x1 = tickX(Math.max(onset, rowStart), row);
      const x2 = tickX(Math.min(end, rowEnd), row);
      svg.append(svgElement('rect', { x: x1, y: pitchY(pitch, row), width: Math.max(x2 - x1 - 1, 1), height: SEMITONE_HEIGHT - 1, rx: 1, class: 'roll-note' }));
    }
  }
  svg.append(svgElement('line', { id: 'roll-playhead', x1: 0, x2: 0, y1: 0, y2: 0, class: 'roll-playhead' }));
  container.replaceChildren(svg);
}

/** A bar pressed: start a drag there, or span from the last bar pressed (shift). */
function barPressed(bar, event) {
  event.preventDefault();
  if (event.shiftKey && anchorBar !== null) {
    chooseBars(anchorBar, bar);
    return;
  }
  dragStartBar = bar;
  dragEndBar = bar;
  anchorBar = bar;
  fillBarsBox(bar, bar);
}

/** The pointer over a bar: while a press is held, the Bars box follows it (no playback change yet). */
function dragOver(bar) {
  if (dragStartBar === null) return;
  dragEndBar = bar;
  fillBarsBox(dragStartBar, bar);
}

/** The button comes up after a press on the roll: those bars become the choice. */
function finishDrag() {
  if (dragStartBar === null) return;
  const [start, end] = [dragStartBar, dragEndBar];
  dragStartBar = null;
  chooseBars(start, end);
}

/** Bars a..b (either order) in the Bars box and shaded on the roll. */
function fillBarsBox(a, b) {
  const [first, last] = [a, b].sort((x, y) => x - y);
  element('first-bar').value = first;
  element('last-bar').value = last;
  markChosenBars();
}

/** Choose bars a..b: the Bars box, the shading, and the playback range (the playhead jumps to its start). */
function chooseBars(a, b) {
  fillBarsBox(a, b);
  const [first, last] = [a, b].sort((x, y) => x - y);
  statusLabel.textContent = `bars ${first}–${last} chosen (drag, or shift-click another bar to span to it)`;
  setPlaybackRange(first, last);
}

/** Shade the Bars box's bars on the roll, in place (redrawing would swap the elements under the pointer). */
function markChosenBars() {
  const chosen = chosenBars();
  for (const target of element('piano-roll').querySelectorAll('.roll-bar')) {
    const bar = Number(target.dataset.bar);
    target.classList.toggle('chosen', Boolean(chosen && bar >= chosen[0] && bar <= chosen[1]));
  }
}

/** The hidden playback tab's beats from bar `first`'s first note to bar `last`'s last note as alphaTab's playback range. */
function setPlaybackRange(first, last) {
  if (!api.score || !api.tickCache) return;
  const { ticks_per_bar: ticksPerBar, passage } = state.song.midi;
  const { firstBeatOfNote } = currentBeatMap();
  const inBars = passage.notes.map((note, index) => index)
    .filter((index) => {
      const bar = Math.floor(passage.notes[index].onset_tick / ticksPerBar) + 1;
      return bar >= first && bar <= last && firstBeatOfNote[index];
    });
  if (!inBars.length) return;
  const startBeat = firstBeatOfNote[inBars[0]];
  const endBeat = firstBeatOfNote[inBars.at(-1)];
  const startTick = api.tickCache.getBeatStart(startBeat);
  api.highlightPlaybackRange(startBeat, endBeat);
  api.playbackRange = { startTick, endTick: api.tickCache.getBeatStart(endBeat) + endBeat.playbackDuration };
  api.tickPosition = startTick;
  state.draggedBeats = { first: startBeat, last: endBeat };
  updateFretboard();
}

/** Move the playhead to the note a playing beat starts (kept in view). */
function movePlayhead(beat) {
  const playhead = document.getElementById('roll-playhead');
  if (!shown() || !playhead || !layout || !beat) return;
  const { noteOfBeat } = currentBeatMap();
  const index = noteOfBeat.get(beat.id);
  if (index === null || index === undefined) return;
  const tick = state.song.midi.passage.notes[index].onset_tick;
  const row = Math.floor(tick / (BARS_PER_ROW * layout.ticksPerBar));
  const x = tickX(tick, row);
  for (const [name, value] of Object.entries({ x1: x, x2: x, y1: rowTop(row) + BAR_LABEL_HEIGHT, y2: rowTop(row) + layout.rowHeight })) {
    playhead.setAttribute(name, value);
  }
  playhead.scrollIntoView({ block: 'nearest' });
}
