// GRIP GP viewer, annotate side: the note panel, selecting notes (click, drag, arrows), the edit
// keys and buttons, and the Save box. viewer.mjs calls setupAnnotate once at start.
import { annotationStrokeLetter } from '/choices_to_tex.mjs';
import { stringNumberOfNote } from '/fretboard.mjs';

// What viewer.mjs shares (page state, alphaTab, drawing), set once by setupAnnotate
let element, statusLabel, api, state, pageConfig, postJson;
let renderView, shownPassage, choicesDiffer, changedFromStart, currentBeatMap, updateDropdowns, updateFretboard;
let notePanel;

// Stroke letters as run files write them → names; the run file's H is shown as z
const STROKE_NAMES = { D: 'down', U: 'up', h: 'hammer-on', p: 'pull-off', s: 'slide', t: 'left-hand tap', H: 'hammer-on from nowhere' };
const SHOWN_STROKE_LETTER = { H: 'z' };
const FINGER_NAMES = ['open', 'index', 'middle', 'ring', 'pinky'];
const PITCH_NAMES = ['C', 'C#', 'D', 'D#', 'E', 'F', 'F#', 'G', 'G#', 'A', 'A#', 'B'];
// Annotate-mode keys (not case-sensitive; Colemak-DH doubles: f = down, p = up, w = no pick)
const EDIT_KEYS = {
  d: { stroke: 'down' }, f: { stroke: 'down' },
  u: { stroke: 'up' }, p: { stroke: 'up' },
  n: { stroke: 'none' }, w: { stroke: 'none' },
  x: { finger: null, stroke: null },
  1: { finger: 1 }, 2: { finger: 2 }, 3: { finger: 3 }, 4: { finger: 4 }, 5: { finger: 0 },
  arrowup: { move: -1 }, arrowdown: { move: 1 },
};

/**
 * Take what viewer.mjs shares and wire up selecting, the edit keys and the Save box.
 *
 * Returns { updatePanel, drawSelectionBoxes } for viewer.mjs to call after each drawing.
 */
export function setupAnnotate(viewer) {
  ({ element, statusLabel, api, state, pageConfig, postJson } = viewer);
  ({ renderView, shownPassage, choicesDiffer, changedFromStart, currentBeatMap, updateDropdowns, updateFretboard } = viewer);
  notePanel = element('note-panel');
  setupSelecting();
  setupEditKeys();
  setupSaveBox();
  return { updatePanel, drawSelectionBoxes };
}

// ---- Boxes around the selected notes ----

/** The drawn box of a passage note's first beat: its note head, or the whole beat for a rest (bounds from alphaTab's layout). */
function noteBounds(beat, beatBounds) {
  if (beat.isRest) return beatBounds.visualBounds;
  const headBounds = beatBounds.notes?.find((bounds) => bounds.note === beat.notes[0])?.noteHeadBounds;
  return headBounds ?? beatBounds.visualBounds;
}

/** Selection boxes: one around each selected note on the score. */
function drawSelectionBoxes() {
  const boxes = element('selection-boxes');
  boxes.replaceChildren();
  if (!shownPassage() || !state.selection.length) return;
  const lookup = api.renderer.boundsLookup;
  const { firstBeatOfNote } = currentBeatMap();
  const score = element('score');
  for (const index of state.selection) {
    const beat = firstBeatOfNote[index];
    const beatBounds = beat && lookup?.findBeat(beat);
    if (!beatBounds) continue;
    const bounds = noteBounds(beat, beatBounds);
    const box = document.createElement('div');
    box.className = 'selection-box';
    Object.assign(box.style, {
      left: `${score.offsetLeft + bounds.x - 3}px`, top: `${score.offsetTop + bounds.y - 3}px`,
      width: `${bounds.w + 6}px`, height: `${bounds.h + 6}px`,
    });
    boxes.append(box);
  }
}

// ---- Note panel ----

/** Replace the panel with one grey line. */
function showPanelHint(text) {
  const hint = document.createElement('p');
  hint.className = 'hint';
  hint.textContent = text;
  notePanel.replaceChildren(hint);
}

/** One <dl> of label → value rows. */
function definitionList(rows) {
  const list = document.createElement('dl');
  for (const [label, value] of rows) {
    const term = document.createElement('dt');
    term.textContent = label;
    const description = document.createElement('dd');
    description.textContent = value;
    list.append(term, description);
  }
  return list;
}

function panelHeading(text) {
  const heading = document.createElement('h3');
  heading.textContent = text;
  return heading;
}

/** A row of buttons; each item is [label, onClick, isChosen]. */
function buttonRow(items) {
  const row = document.createElement('div');
  row.className = 'buttons';
  for (const [label, onClick, chosen] of items) {
    const button = document.createElement('button');
    button.textContent = label;
    if (chosen) button.className = 'chosen';
    button.onclick = onClick;
    row.append(button);
  }
  return row;
}

function fingerText(finger) {
  return finger === null || finger === undefined ? "don't care" : `${finger} (${FINGER_NAMES[finger]})`;
}

function strokeText(letter) {
  return letter ? `${SHOWN_STROKE_LETTER[letter] ?? letter} (${STROKE_NAMES[letter] ?? letter})` : "don't care";
}

function pitchText(pitch) {
  return `${PITCH_NAMES[pitch % 12]}${Math.floor(pitch / 12) - 1} (MIDI ${pitch})`;
}

/** Fill the panel for what is shown and selected. */
function updatePanel() {
  const shown = shownPassage();
  if (!state.view) return showPanelHint('Open a GP file (left) or an optimiser run.');
  if (state.view.kind === 'song') {
    return showPanelHint('Drag over bars to fill the bar boxes, then Run optimiser (search) or Edit (annotate, starting from this tab).');
  }
  const blocks = [];
  if (state.selection.length === 1) {
    blocks.push(...singleNoteBlocks(shown, state.selection[0]));
  } else if (state.selection.length > 1) {
    const title = document.createElement('h2');
    title.textContent = `${state.selection.length} notes selected (notes ${state.selection[0] + 1}–${state.selection.at(-1) + 1})`;
    blocks.push(title);
    if (shown.editable) blocks.push(...editButtons(null));
  } else {
    const hint = document.createElement('p');
    hint.className = 'hint';
    hint.textContent = shown.editable ? 'Click a note, or drag over several.' : 'Click a note to see its details. Edit starts an annotation from this realisation.';
    blocks.push(hint);
  }
  if (shown.editable) {
    for (const line of state.annotation.report ?? []) {
      const report = document.createElement('p');
      report.className = 'warning';
      report.textContent = line;
      blocks.push(report);
    }
    const keys = document.createElement('div');
    keys.className = 'keys';
    keys.textContent = 'Keys: d/f down · u/p up · n/w no pick · x don\'t care (finger + stroke) · 1–4 finger · 5 open · ↑/↓ string · ←/→ next note · Esc clear · drag to select several';
    blocks.push(keys);
  }
  notePanel.replaceChildren(...blocks);
}

/** Panel blocks for one note: the passage note, the choice shown, and (annotating) the edit buttons. */
function singleNoteBlocks(shown, index) {
  const note = shown.passage.notes[index];
  const choice = shown.choices[index];
  const title = document.createElement('h2');
  title.textContent = `Note ${index + 1} of ${shown.passage.notes.length}`;
  if (note.pitch === null) {
    return [title, definitionList([['null symbol', note.null_reason]])];
  }
  const noteRows = [['pitch', pitchText(note.pitch)], ['onset', `${note.onset_seconds.toFixed(3)} s`]];
  if (note.articulations.length) noteRows.push(['source marks', note.articulations.join(', ')]);

  const letter = shown.editable ? annotationStrokeLetter(shown.passage, shown.choices, index) : choice.stroke_letter;
  const choiceRows = [
    ['string', choice.string ?? 'none yet'], ['fret', choice.fret ?? 'none yet'],
    ['finger', fingerText(choice.finger)], ['stroke', strokeText(letter)],
  ];
  if (!shown.editable) choiceRows.push(['hand', choice.hand]);
  if (shown.editable && changedFromStart(index)) {
    const start = state.annotation.startChoices[index];
    choiceRows.push(['started as', `string ${start.string ?? '–'} · fret ${start.fret ?? '–'} · finger ${start.finger ?? 'x'} · ${start.stroke ?? 'x'}`]);
  }
  const blocks = [title, definitionList(noteRows), panelHeading(shown.editable ? 'Annotation' : `Realisation #${shown.realisation.rank}`), definitionList(choiceRows)];

  // Position options: plain list when viewing, buttons when annotating
  const positions = shown.positions[index];
  blocks.push(panelHeading(`Position options (${positions.length})`));
  if (shown.editable) {
    blocks.push(buttonRow(positions.map((position) => [
      `string ${position.string} · fret ${position.fret}`,
      () => applyEdit({ position }),
      position.string === choice.string && position.fret === choice.fret,
    ])));
    blocks.push(...editButtons(choice));
  } else {
    const optionList = document.createElement('ul');
    for (const position of positions) {
      const item = document.createElement('li');
      item.textContent = `string ${position.string} · fret ${position.fret}`;
      if (position.string === choice.string && position.fret === choice.fret) item.className = 'chosen';
      optionList.append(item);
    }
    blocks.push(optionList);
  }
  const mismatch = drawnNoteMismatch(index, choice);
  if (mismatch) blocks.push(mismatch);
  return blocks;
}

/** Finger + stroke buttons; `choice` marks the current values (null when several notes are selected). */
function editButtons(choice) {
  const fingerItems = [[1, '1 index'], [2, '2 middle'], [3, '3 ring'], [4, '4 pinky'], [0, '5 open'], [null, "x don't care"]]
    .map(([finger, label]) => [label, () => applyEdit({ finger }), choice !== null && choice.finger === finger]);
  const strokeItems = [['down', 'd/f down'], ['up', 'u/p up'], ['none', 'n/w no pick'], [null, "x don't care"]]
    .map(([stroke, label]) => [label, () => applyEdit({ stroke }), choice !== null && choice.stroke === stroke]);
  return [panelHeading('Finger'), buttonRow(fingerItems), panelHeading('Stroke'), buttonRow(strokeItems)];
}

/** A red warning if the drawn note isn't where the choices say (a mapping bug), else null. */
function drawnNoteMismatch(index, choice) {
  const beat = currentBeatMap().firstBeatOfNote[index];
  if (!beat || beat.isRest || choice.string === null) return null;
  const drawn = beat.notes[0];
  const drawnString = stringNumberOfNote(drawn);
  if (drawn.fret === choice.fret && drawnString === choice.string) return null;
  const warning = document.createElement('p');
  warning.className = 'warning';
  warning.textContent = `Mismatch: drawn string ${drawnString} fret ${drawn.fret}, but the choices say string ${choice.string} fret ${choice.fret}.`;
  return warning;
}

// ---- Selecting: click, drag, arrows ----

/** Select passage notes (sorted, playable or null symbols alike) and redraw panel, boxes and fretboard. */
function select(indices) {
  state.selection = [...new Set(indices)].sort((a, b) => a - b);
  updatePanel();
  drawSelectionBoxes();
  updateFretboard();
}

/** Click and drag on the tab: a press, moves, and a release, each on a beat. */
function setupSelecting() {
  let dragStartBeat = null;
  let dragEndBeat = null;
  api.beatMouseDown.on((beat) => { dragStartBeat = beat; dragEndBeat = beat; });
  api.beatMouseMove.on((beat) => { if (dragStartBeat) dragEndBeat = beat; });
  api.beatMouseUp.on((beat) => {
    if (!dragStartBeat) return;
    finishDrag(dragStartBeat, beat ?? dragEndBeat);
    dragStartBeat = null;
  });
}

/** A click or drag from beat `a` to beat `b`: fills the bar boxes and marks the beats on the fretboard (song view), or selects notes (passage). */
function finishDrag(a, b) {
  if (state.view?.kind === 'song') {
    const bars = [a, b].map((beat) => beat.voice.bar.index + 1).sort((x, y) => x - y);
    element('first-bar').value = bars[0];
    element('last-bar').value = bars[1];
    statusLabel.textContent = `bars ${bars[0]}–${bars[1]} chosen`;
    state.draggedBeats = { first: a, last: b };
    updateFretboard();
    return;
  }
  if (!shownPassage()) return;
  const { noteOfBeat, beatsInOrder } = currentBeatMap();
  const [from, to] = [beatsInOrder.indexOf(a), beatsInOrder.indexOf(b)].sort((x, y) => x - y);
  if (from < 0) return;
  const indices = beatsInOrder.slice(from, to + 1).map((beat) => noteOfBeat.get(beat.id)).filter((index) => index !== null);
  select(indices);
}

/** Move the selection to the previous / next note with a pitch. */
function stepSelection(step) {
  const notes = shownPassage().passage.notes;
  // Start from the selection's last note (going right) or first (going left); nothing selected = just off the passage's end
  let index;
  if (state.selection.length) index = step > 0 ? state.selection.at(-1) : state.selection[0];
  else index = step > 0 ? -1 : notes.length;
  do { index += step; } while (index >= 0 && index < notes.length && notes[index].pitch === null);
  if (index < 0 || index >= notes.length) return;
  select([index]);
  element('selection-boxes').firstChild?.scrollIntoView({ block: 'nearest' });
}

// ---- Editing the annotation ----

/** The position one string up (step −1, towards string 1) or down (+1) from `choice`, or null. */
function neighbouringPosition(positions, choice, step) {
  if (choice.string === null) return positions[0] ?? null;
  const candidates = positions.filter((position) => (step < 0 ? position.string < choice.string : position.string > choice.string));
  candidates.sort((x, y) => (step < 0 ? y.string - x.string : x.string - y.string));
  return candidates[0] ?? null;
}

/**
 * Where a string move (step −1 towards string 1, +1 away) takes each selected note: passage index → position.
 * The selection moves as one block: null (with a status message) if any note has no string that way.
 */
function blockMoveTargets(step) {
  const { passage, positions, choices } = state.annotation;
  const targets = new Map();
  for (const index of state.selection) {
    if (passage.notes[index].pitch === null) continue;
    const position = neighbouringPosition(positions[index], choices[index], step);
    if (!position) {
      statusLabel.textContent = `nothing moved: note ${index + 1} has no string ${step < 0 ? 'above (towards string 1)' : 'below (away from string 1)'}`;
      return null;
    }
    targets.set(index, position);
  }
  return targets;
}

/**
 * Apply one change to every selected note: { position }, { move: ±1 }, { finger }, { stroke } (or finger + stroke).
 * A move shifts the whole selection one string or nothing (see blockMoveTargets), and moved notes' fingers
 * become don't care (the shape changes across strings 3–2). Fingers must fit the fret (0 = open only on
 * fret 0); notes where a finger can't apply are skipped and counted.
 */
function applyEdit(change) {
  if (state.view?.kind !== 'annotation') return;
  const { passage, choices } = state.annotation;
  const moveTargets = 'move' in change ? blockMoveTargets(change.move) : null;
  if ('move' in change && !moveTargets) return;
  let changed = 0;
  let skipped = 0;
  for (const index of state.selection) {
    if (passage.notes[index].pitch === null) continue;
    const choice = { ...choices[index] };
    const position = moveTargets ? moveTargets.get(index) : change.position ?? null;
    if (position) {
      choice.string = position.string;
      choice.fret = position.fret;
      // An open string is always finger 0; a moved note, or one leaving an open string, gets don't care
      if (choice.fret === 0) choice.finger = 0;
      else if (moveTargets || choice.finger === 0) choice.finger = null;
    }
    if ('finger' in change) {
      const fits = change.finger === null || (choice.fret !== null && (change.finger === 0) === (choice.fret === 0));
      if (fits) choice.finger = change.finger;
      else skipped += 1;
    }
    if ('stroke' in change) choice.stroke = change.stroke;
    if (choicesDiffer(choice, choices[index])) {
      choices[index] = choice;
      changed += 1;
    }
  }
  statusLabel.textContent = skipped ? `${skipped} note(s) skipped (finger doesn't fit the fret: 5 = open strings only)` : '';
  if (!changed) return;
  state.annotation.dirty = true;
  renderView(true);
}

/** The annotate keys (EDIT_KEYS), ←/→ to step through notes and Esc to clear the selection. */
function setupEditKeys() {
  document.addEventListener('keydown', (event) => {
    if (event.target.closest('input, select, textarea, dialog') || event.ctrlKey || event.metaKey || event.altKey) return;
    const shown = shownPassage();
    if (!shown) return;
    const key = event.key.toLowerCase();
    if (key === 'arrowleft' || key === 'arrowright') {
      event.preventDefault();
      stepSelection(key === 'arrowright' ? 1 : -1);
    } else if (key === 'escape') {
      select([]);
    } else if (key in EDIT_KEYS) {
      if (!shown.editable) {
        statusLabel.textContent = 'press Edit to start annotating from this realisation';
        return;
      }
      event.preventDefault();
      applyEdit(EDIT_KEYS[key]);
    }
  });
}

// ---- Save: the page's own save box (the browser's Save dialog can only see the desktop's disk, not sym8's) ----

let saveDialog;
let saveCheckTimer;
// Jae chose "Overwrite" for the name in the box; any edit to the name clears it
let saveOverwrite = false;
// The name "New version" fills in, from the last check
let saveNewVersionName = null;
// Counts save checks, so a slow, older answer can't overwrite a newer one
let saveCheckNumber = 0;

/** The request that saves (or, with dryRun, only checks) the annotation as its own file; an empty name asks for the suggested one. */
function saveRequest(dryRun) {
  return {
    file_name: element('save-file-name').value.trim(), settings: state.annotation.settings, dry_run: dryRun, overwrite: saveOverwrite,
    annotation: { started_from: state.annotation.startedFrom, comment: element('save-comment').value.trim() || null, choices: state.annotation.choices },
  };
}

/** Ask the server what Save would do with this file name, and show it (plus the overwrite / new version choice if taken). */
async function checkSave() {
  const checkNumber = ++saveCheckNumber;
  element('save-summary').textContent = 'checking…';
  element('save-error').textContent = '';
  element('save-choice').hidden = true;
  element('save-confirm').disabled = true;
  const reply = await postJson('/api/reference', saveRequest(true));
  if (checkNumber !== saveCheckNumber) return;  // a newer check started meanwhile
  if (reply.error) {
    element('save-summary').textContent = '';
    element('save-error').textContent = reply.error;
    return;
  }
  if (!element('save-file-name').value.trim()) element('save-file-name').value = reply.file_name;
  saveNewVersionName = reply.new_version_name;
  const existing = `${reply.existing_passage ?? '?'}, saved ${reply.existing_saved?.replace('T', ' ') ?? '?'}`;
  let summary, nameFree = true;
  if (!reply.exists) {
    summary = 'New file.';
  } else if (!reply.same_track) {
    summary = `This name already holds another track (${existing}), so it can't be overwritten. Save as a new version (${reply.new_version_name})?`;
    nameFree = false;
  } else if (saveOverwrite) {
    summary = `Overwrites the existing file (${existing}).`;
  } else {
    summary = `A realisation with this name already exists (${existing}). Overwrite it, or create a new version (${reply.new_version_name})?`;
    nameFree = false;
  }
  element('save-summary').textContent = summary;
  element('save-choice').hidden = nameFree;
  element('save-overwrite').hidden = !reply.same_track;
  if (reply.problems.length) element('save-error').textContent = `can't save yet — ${reply.problems.join('; ')}`;
  element('save-confirm').textContent = reply.exists && saveOverwrite ? 'Overwrite' : 'Save';
  element('save-confirm').disabled = !nameFree || reply.problems.length > 0;
}

/** The Save button, the box's name field and its Overwrite / New version / Cancel / Save buttons. */
function setupSaveBox() {
  saveDialog = element('save-dialog');
  element('save-button').onclick = () => {
    if (!state.annotation.settings) {
      statusLabel.textContent = 'this annotation has no GP source (lick run): it can\'t be saved as a reference passage';
      return;
    }
    element('save-folder').textContent = `Folder: ${pageConfig.reference_set_folder}`;
    element('save-file-name').value = '';
    element('save-comment').value = '';
    saveOverwrite = false;
    saveDialog.showModal();
    checkSave();
  };
  element('save-file-name').oninput = () => {
    saveOverwrite = false;
    clearTimeout(saveCheckTimer);
    saveCheckTimer = setTimeout(checkSave, 400);
  };
  element('save-overwrite').onclick = () => { saveOverwrite = true; checkSave(); };
  element('save-new-version').onclick = () => {
    saveOverwrite = false;
    element('save-file-name').value = saveNewVersionName;
    checkSave();
  };
  element('save-cancel').onclick = () => saveDialog.close();
  element('save-form').onsubmit = async (event) => {
    event.preventDefault();
    element('save-confirm').disabled = true;
    const reply = await postJson('/api/reference', saveRequest(false));
    if (reply.error) {
      element('save-error').textContent = reply.error;
      element('save-confirm').disabled = false;
      return;
    }
    saveDialog.close();
    state.annotation.dirty = false;
    statusLabel.textContent = `${reply.overwritten ? 'overwrote' : 'saved'} ${reply.file_name}`;
    updateDropdowns();
  };
}
