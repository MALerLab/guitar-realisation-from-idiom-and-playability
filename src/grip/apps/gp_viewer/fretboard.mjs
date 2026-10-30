// GRIP GP viewer, fretboard: the fretboard under the tab. While playing it marks the notes sounding
// (current, red) and the events around them (previous orange, next green, fading with distance);
// otherwise it marks the highlighted notes (orange). Also the text of the toolbar's tuning label.
//
// Two parts:
//   - Drawing: tuning, fret count and positions to mark in, SVG out. No access to the page.
//   - Page wiring: which positions to mark, from the page state and alphaTab's playing order, plus the
//     panel's toggle and previous / next boxes. viewer.mjs calls setupFretboard once.
//     It registers no alphaTab player listeners: those stay at the end of viewer.mjs (see there).
//
// A tuning is open-string MIDI pitches, string 1 (highest) first, as alphaTab, run files and manifests
// store it. A position is { string, fret }: string 1 = highest, fret 0 = open string.
// Colours and line widths live in viewer.css (the fretboard-* classes, one fretboard-mark-<kind> per mark kind).
import * as alphaTab from '/alphatab/alphaTab.mjs';

// =============================================================================================
// alphaTab ↔ GRIP
// =============================================================================================

/** A drawn alphaTab note's string number in GRIP's counting (1 = highest; alphaTab counts from the lowest). */
export function stringNumberOfNote(note) {
  return note.beat.voice.bar.staff.tuning.length - note.string + 1;
}

// =============================================================================================
// Drawing
// =============================================================================================

const NOTE_NAMES = ['C', 'Db', 'D', 'Eb', 'E', 'F', 'Gb', 'G', 'Ab', 'A', 'Bb', 'B'];
// Frets with one inlay dot, and with two
const SINGLE_DOT_FRETS = [3, 5, 7, 9, 15, 17, 19, 21];
const DOUBLE_DOT_FRETS = [12, 24];
// Drawing units (the SVG scales to the panel's width): space for the string names, one fret, between strings
const LABEL_WIDTH = 30;
const FRET_WIDTH = 40;
const STRING_GAP = 18;
const TOP_MARGIN = 10;
const BOTTOM_MARGIN = 20;
const RIGHT_MARGIN = 10;
// Marks: a dot in the fret, or (open string) a ring around the string name at the nut
const MARK_RADIUS = 6;
const OPEN_MARK_X = LABEL_WIDTH - 14;
const OPEN_MARK_RADIUS = 9;
const SVG_NAMESPACE = 'http://www.w3.org/2000/svg';

/** A pitch's note name without octave, flats for the black keys (e.g. 63 → "Eb"). */
function noteName(pitch) {
  return NOTE_NAMES[((pitch % 12) + 12) % 12];
}

/** A tuning as note names, lowest string first (e.g. "D A D G B E"). */
export function tuningText(tuning) {
  return [...tuning].reverse().map(noteName).join(' ');
}

/** One SVG element with its attributes (and text, if given). */
function svgElement(tag, attributes, text = null) {
  const node = document.createElementNS(SVG_NAMESPACE, tag);
  for (const [name, value] of Object.entries(attributes)) node.setAttribute(name, value);
  if (text !== null) node.textContent = text;
  return node;
}

/**
 * Draw a fretboard into `container`, replacing what is there: string 1 on top as in tab, each string
 * named by its open note at the nut, frets 1 to `highestFret` evenly spaced, inlay dots, and marks.
 *
 * Args:
 *   container: The element to draw into.
 *   options.tuning: Open-string MIDI pitches, string 1 first; one line is drawn per string.
 *   options.highestFret: How many frets the neck has.
 *   options.marks: Positions to mark, [{ string, fret, kind, opacity }]: kind picks the colour class
 *     fretboard-mark-<kind>; opacity 0–1, 1 if left out. Fainter marks are drawn first, so a stronger
 *     mark on the same position shows on top; positions off the neck are skipped.
 */
function drawFretboard(container, { tuning, highestFret, marks = [] }) {
  const stringCount = tuning.length;
  const nutX = LABEL_WIDTH;
  const endX = nutX + highestFret * FRET_WIDTH;
  const topY = TOP_MARGIN;
  const bottomY = topY + (stringCount - 1) * STRING_GAP;
  const fretCentreX = (fret) => nutX + (fret - 0.5) * FRET_WIDTH;
  const svg = svgElement('svg', {
    class: 'fretboard', viewBox: `0 0 ${endX + RIGHT_MARGIN} ${bottomY + BOTTOM_MARGIN}`,
    role: 'img', 'aria-label': `Fretboard: ${tuningText(tuning)}, ${highestFret} frets`,
  });

  // Wood, then the inlay dots on it
  svg.append(svgElement('rect', { class: 'fretboard-wood', x: nutX, y: topY, width: endX - nutX, height: bottomY - topY }));
  const middleY = (topY + bottomY) / 2;
  for (const fret of SINGLE_DOT_FRETS.filter((fret) => fret <= highestFret)) {
    svg.append(svgElement('circle', { class: 'fretboard-dot', cx: fretCentreX(fret), cy: middleY, r: 4 }));
  }
  for (const fret of DOUBLE_DOT_FRETS.filter((fret) => fret <= highestFret)) {
    for (const y of [topY + (bottomY - topY) / 4, topY + (3 * (bottomY - topY)) / 4]) {
      svg.append(svgElement('circle', { class: 'fretboard-dot', cx: fretCentreX(fret), cy: y, r: 4 }));
    }
  }

  // Nut and frets, with the dotted frets numbered underneath
  svg.append(svgElement('line', { class: 'fretboard-nut', x1: nutX, y1: topY, x2: nutX, y2: bottomY }));
  for (let fret = 1; fret <= highestFret; fret += 1) {
    const x = nutX + fret * FRET_WIDTH;
    svg.append(svgElement('line', { class: 'fretboard-fret', x1: x, y1: topY, x2: x, y2: bottomY }));
    if (SINGLE_DOT_FRETS.includes(fret) || DOUBLE_DOT_FRETS.includes(fret)) {
      svg.append(svgElement('text', { class: 'fretboard-fret-number', x: fretCentreX(fret), y: bottomY + 15 }, fret));
    }
  }

  // Strings, thicker towards the low ones, each named by its open note
  tuning.forEach((pitch, index) => {
    const y = topY + index * STRING_GAP;
    const width = 1 + (1.5 * index) / Math.max(stringCount - 1, 1);
    svg.append(svgElement('line', { class: 'fretboard-string', x1: nutX, y1: y, x2: endX, y2: y, 'stroke-width': width }));
    svg.append(svgElement('text', { class: 'fretboard-string-name', x: nutX - 8, y: y + 4 }, noteName(pitch)));
  });

  // Marks: faintest first, strongest on top
  const marksInDrawingOrder = [...marks].sort((a, b) => (a.opacity ?? 1) - (b.opacity ?? 1));
  for (const { string, fret, kind, opacity = 1 } of marksInDrawingOrder) {
    if (string < 1 || string > stringCount || fret < 0 || fret > highestFret) continue;
    const y = topY + (string - 1) * STRING_GAP;
    const colourClass = `fretboard-mark-${kind}`;
    if (fret === 0) {
      svg.append(svgElement('circle', { class: `fretboard-open-mark ${colourClass}`, cx: OPEN_MARK_X, cy: y, r: OPEN_MARK_RADIUS, 'stroke-opacity': opacity }));
    } else {
      svg.append(svgElement('circle', { class: `fretboard-mark ${colourClass}`, cx: fretCentreX(fret), cy: y, r: MARK_RADIUS, 'fill-opacity': opacity }));
    }
  }
  container.replaceChildren(svg);
}

// =============================================================================================
// Page wiring
// =============================================================================================

// What viewer.mjs shares, set once by setupFretboard
let element, api, state, pageConfig, shownPassage, currentBeatMap, remember, recall;

// Previous / next events fade with distance: the nearest at NEAREST_EVENT_OPACITY, the k-th at FURTHEST_EVENT_OPACITY
const NEAREST_EVENT_OPACITY = 0.6;
const FURTHEST_EVENT_OPACITY = 0.25;
// The previous / next boxes: their element ids, and the names their values are remembered under
const EVENT_COUNT_BOXES = {
  previous: { id: 'fretboard-previous', storageName: 'fretboardPrevious' },
  next: { id: 'fretboard-next', storageName: 'fretboardNext' },
};

// The marks last drawn, so playback position updates redraw only when they change
let drawnFretboardKey = null;
// The drawn track's notes in playing order (see buildTimeline), rebuilt when the player's tick cache changes
let timeline = null;

/**
 * Take what viewer.mjs shares and wire the panel's toggle and previous / next boxes (both remembered in this browser).
 *
 * Returns { updateFretboard, shownGuitar } for viewer.mjs.
 */
export function setupFretboard(viewer) {
  ({ element, api, state, pageConfig, shownPassage, currentBeatMap, remember, recall } = viewer);
  element('fretboard-panel').hidden = recall('fretboardShown', '0') !== '1';
  element('fretboard-toggle').onclick = () => {
    const opening = element('fretboard-panel').hidden;
    element('fretboard-panel').hidden = !opening;
    remember('fretboardShown', opening ? '1' : '0');
    updateFretboard();
  };
  for (const { id, storageName } of Object.values(EVENT_COUNT_BOXES)) {
    element(id).value = recall(storageName, element(id).value);
    element(id).oninput = () => {
      remember(storageName, element(id).value);
      updateFretboard();
    };
  }
  return { updateFretboard, shownGuitar };
}

/**
 * The guitar of what is shown: { tuning (string 1 first), highestFret }, or null (nothing open, or a
 * track without strings). The song view shows its track's own tuning; a passage shows its run's or
 * annotation's (tuning shift included).
 */
function shownGuitar() {
  const shown = shownPassage();
  let guitar = null;
  if (shown) {
    guitar = { tuning: shown.passage.guitar.tuning, highestFret: shown.passage.guitar.highest_fret };
  } else if (state.view?.kind === 'song') {
    const tuning = state.song.score.tracks[state.song.trackIndex].staves[0].tuning;
    guitar = { tuning, highestFret: pageConfig.highest_fret };
  }
  return guitar?.tuning.length ? guitar : null;
}

/** Redraw the fretboard (while its panel is open) with its marks at playback tick `tick`. */
function updateFretboard(tick = api.tickPosition) {
  const guitar = shownGuitar();
  const fretboard = element('fretboard');
  if (!guitar || element('fretboard-panel').hidden) {
    fretboard.replaceChildren();
    drawnFretboardKey = null;
    return;
  }
  const marks = fretboardMarks(tick);
  const key = JSON.stringify([guitar, marks]);
  if (key === drawnFretboardKey) return;
  drawnFretboardKey = key;
  drawFretboard(fretboard, { ...guitar, marks });
}

/** While playing: playback marks. Otherwise the highlighted notes, or (nothing highlighted) the picture where playback stopped. */
function fretboardMarks(tick) {
  if (api.playerState !== alphaTab.synth.PlayerState.Playing) {
    const selected = selectionPositions();
    if (selected) return selected.map((position) => ({ ...position, kind: 'selected' }));
  }
  return playbackMarks(tick);
}

// ---- What is highlighted ----

/** One drawn beat's notes as positions; a rest (or a null symbol) has none. */
function beatPositions(beat) {
  return beat.notes.map((note) => ({ string: stringNumberOfNote(note), fret: note.fret }));
}

/**
 * Positions of what is highlighted, or null if nothing is: a passage's selected notes, or the song
 * view's last click or drag (every beat between, all voices).
 */
function selectionPositions() {
  if (shownPassage()) {
    if (!state.selection.length) return null;
    const { firstBeatOfNote } = currentBeatMap();
    return state.selection.flatMap((index) => (firstBeatOfNote[index] ? beatPositions(firstBeatOfNote[index]) : []));
  }
  const dragged = state.draggedBeats;
  const track = api.tracks[0];
  if (state.view?.kind !== 'song' || !dragged || dragged.first.voice.bar.staff.track !== track) return null;
  const [from, to] = [dragged.first, dragged.last].map((beat) => beat.absoluteDisplayStart).sort((a, b) => a - b);
  const beats = track.staves[0].bars.flatMap((bar) => bar.voices.flatMap((voice) => voice.beats));
  const draggedOver = beats.filter((beat) => beat.absoluteDisplayStart >= from && beat.absoluteDisplayStart <= to);
  return draggedOver.flatMap(beatPositions);
}

// ---- What is playing ----

/**
 * Every note of a track in real playing order, all voices, repeats and jumps played out (from alphaTab's
 * tick cache, which lists each bar every time it plays).
 *
 * Returns { sounds, events }: sounds = each beat with notes { start, end, positions } (ticks), tied
 * continuations included; events = notes starting at the same moment, grouped { start, end, positions }
 * (a tied continuation is not a new event), in time order.
 */
function buildTimeline(tickCache, track) {
  const sounds = [];
  for (const playedBar of tickCache.masterBars) {
    for (const voice of track.staves[0].bars[playedBar.masterBar.index]?.voices ?? []) {
      for (const beat of voice.beats) {
        const positions = beatPositions(beat);
        if (!positions.length) continue;
        const start = playedBar.start + beat.playbackStart;
        const newNotes = beat.notes.some((note) => !note.isTieDestination);
        sounds.push({ start, end: start + beat.playbackDuration, positions, newNotes });
      }
    }
  }
  sounds.sort((a, b) => a.start - b.start);
  const events = [];
  for (const sound of sounds.filter((sound) => sound.newNotes)) {
    const last = events.at(-1);
    if (last?.start === sound.start) {
      last.positions.push(...sound.positions);
      last.end = Math.max(last.end, sound.end);
    } else {
      events.push({ start: sound.start, end: sound.end, positions: [...sound.positions] });
    }
  }
  return { sounds, events };
}

/** The drawn track's timeline, or null until the player has the drawn score's playing order. */
function currentTimeline() {
  const tickCache = api.tickCache;
  const track = api.tracks[0];
  if (!tickCache || !track || tickCache.masterBars[0]?.masterBar.score !== track.score) return null;
  if (timeline?.tickCache !== tickCache || timeline.track !== track) timeline = { tickCache, track, ...buildTimeline(tickCache, track) };
  return timeline;
}

/** Opacity of the event `distance` steps (1 = nearest) from now, out of `count` shown. */
function eventOpacity(distance, count) {
  if (count <= 1) return NEAREST_EVENT_OPACITY;
  return NEAREST_EVENT_OPACITY - ((NEAREST_EVENT_OPACITY - FURTHEST_EVENT_OPACITY) * (distance - 1)) / (count - 1);
}

/** A previous / next box's event count (0 = off). */
function eventCount(direction) {
  return Math.max(0, Math.floor(Number(element(EVENT_COUNT_BOXES[direction].id).value) || 0));
}

/** Marks for events in order of distance from now (nearest first), fading out: [{ string, fret, kind, opacity }]. */
function eventMarks(eventsNearestFirst, kind) {
  return eventsNearestFirst.flatMap((event, index) => event.positions.map((position) => (
    { ...position, kind, opacity: eventOpacity(index + 1, eventsNearestFirst.length) })));
}

/**
 * Marks for playback tick `tick`: current = every note sounding; previous and next = the events
 * (counts from the boxes) around the latest event started, fading with distance. The latest event
 * counts as previous once it has stopped sounding.
 */
function playbackMarks(tick) {
  const line = currentTimeline();
  if (!line) return [];
  const sounding = line.sounds.filter((sound) => sound.start <= tick && tick < sound.end).flatMap((sound) => sound.positions);
  let latest = -1;
  while (latest + 1 < line.events.length && line.events[latest + 1].start <= tick) latest += 1;
  const latestStillSounding = latest >= 0 && line.events[latest].end > tick;
  const lastFinished = latestStillSounding ? latest - 1 : latest;
  const previous = line.events.slice(Math.max(lastFinished - eventCount('previous') + 1, 0), lastFinished + 1);
  const next = line.events.slice(latest + 1, latest + 1 + eventCount('next'));
  return [
    ...eventMarks(previous.reverse(), 'previous'),
    ...eventMarks(next, 'next'),
    ...sounding.map((position) => ({ ...position, kind: 'current' })),
  ];
}
