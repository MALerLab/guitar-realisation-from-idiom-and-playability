// GRIP GP viewer, main script: alphaTab, the page state, drawing what is shown, the toolbar
// (tracks, realisations, Run, Edit) and opening songs (GP files) and runs.
// The other files each get what they need from here through one `viewer` object when the page starts:
//   annotate.mjs     note panel, selecting notes, the edit keys, the Save box
//   file_picker.mjs  path box, songs / tracks list
//   fretboard.mjs    the fretboard under the tab, and the tuning label's text
//   track_mixer.mjs  the open song's tracks: mute, solo, volume
// choices_to_tex.mjs turns a passage played one way into alphaTex for drawing.
import * as alphaTab from '/alphatab/alphaTab.mjs';
import { annotationStrokeLetter, choicesToTex } from '/choices_to_tex.mjs';
import { setupAnnotate } from '/annotate.mjs';
import { setupFilePicker } from '/file_picker.mjs';
import { setupFretboard, tuningText } from '/fretboard.mjs';
import { setupTrackMixer } from '/track_mixer.mjs';

const element = (id) => document.getElementById(id);
const statusLabel = element('status');
const trackSelect = element('track-select');
const realisationSelect = element('realisation-select');
const playPauseButton = element('play-pause');
const stopButton = element('stop');
const viewport = element('viewport');

// Encoding, soundfont, default k, the fretboard's fret count, the datasets and the reference folder come from the server config
const pageConfig = await (await fetch('/api/config')).json();
element('k-best').value = pageConfig.k_best;

const api = new alphaTab.AlphaTabApi(element('score'), {
  // Note bounds: needed for the boxes drawn around selected notes
  core: { fontDirectory: '/alphatab/font/', includeNoteBounds: true },
  importer: { encoding: pageConfig.text_encoding },
  notation: { fingeringMode: alphaTab.FingeringMode.SingleNoteEffectBand },
  player: {
    playerMode: alphaTab.PlayerMode.EnabledSynthesizer,
    soundFont: pageConfig.soundfont_url,
    scrollElement: viewport,
  },
});
// GP files are read here (not by api.load) so their track list is known before drawing
const fileSettings = new alphaTab.Settings();
fileSettings.importer.encoding = pageConfig.text_encoding;

// ---- Page state ----

const state = {
  song: null,         // the open song (GP file): { path, trackIndex, score, mixer (track_mixer.mjs: track index → sound settings) }
  run: null,          // the open optimiser run: { name, run, positions }
  annotation: null,   // { settings, passage, positions, choices, startChoices, startedFrom, dirty, report }
  view: null,         // what the score shows: { kind: 'song' } (song view) | { kind: 'realisation', rank } | { kind: 'annotation' }
  selection: [],      // selected passage note indices, in order (realisation + annotation views)
  draggedBeats: null, // the song view's last click or drag: { first, last } alphaTab beats
};

const CHANGED_NOTE_COLOUR = new alphaTab.model.Color(224, 90, 0);

// ---- alphaTab: player buttons, progress, errors ----

api.error.on((error) => { statusLabel.textContent = `alphaTab error: ${error.message ?? error}`; });
api.soundFontLoad.on((progress) => {
  statusLabel.textContent = `soundfont ${Math.round((100 * progress.loaded) / progress.total)}%`;
});
api.playerReady.on(() => {
  playPauseButton.disabled = false;
  stopButton.disabled = false;
  statusLabel.textContent = '';
});
api.playerStateChanged.on((event) => {
  playPauseButton.textContent = event.state === alphaTab.synth.PlayerState.Playing ? 'Pause' : 'Play';
});
playPauseButton.onclick = () => api.playPause();
stopButton.onclick = () => api.stop();

// ---- Small helpers ----

/** POST JSON to the server; answers the parsed reply, with an `error` field if the server refused. */
async function postJson(url, body) {
  const response = await fetch(url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
  const reply = await response.json().catch(() => ({}));
  if (!response.ok && !reply.error) reply.error = `server: ${response.status} ${response.statusText}`;
  return reply;
}

/** Remember a setting in this browser (ignored if the browser blocks storage). */
function remember(name, value) {
  try { localStorage.setItem(`grip.${name}`, value); } catch { /* storage blocked: not remembered */ }
}

/** A setting remembered in this browser, or `fallback` (never set, or storage blocked). */
function recall(name, fallback) {
  try { return localStorage.getItem(`grip.${name}`) ?? fallback; } catch { return fallback; }
}

/** Ask before throwing away unsaved annotation edits; true = go ahead. */
function confirmDiscardAnnotation(action) {
  return !state.annotation?.dirty || confirm(`The annotation has unsaved edits. ${action} anyway and lose them?`);
}

/** True if two passage settings name the same passage (file, track, bars, voice, tempo, tuning shift). */
function sameSettings(a, b) {
  if (!a || !b) return false;
  const key = (s) => JSON.stringify([s.gp, s.track, s.first_bar, s.last_bar, s.voice, s.tempo_override_bpm ?? null, s.tuning_shift]);
  return key(a) === key(b);
}

/** Passage settings from a run source ("bars": "17-28"); tuning shift worked out for old runs that stored a tuning override. */
function settingsFromSource(source) {
  if (!source?.gp) return null;
  const [firstBar, lastBar] = source.bars.split('-').map(Number);
  return {
    gp: source.gp, track: source.track, first_bar: firstBar, last_bar: lastBar, voice: source.voice,
    tempo_override_bpm: source.tempo_override_bpm ?? null, tuning_shift: source.tuning_shift ?? tuningShiftFromOverride(source),
  };
}

/** Old run files stored the whole tuning instead of a shift: recover the shift against the song's own tuning. */
function tuningShiftFromOverride(source) {
  const fileTuning = state.song?.score.tracks[source.track]?.staves[0].tuning;
  if (!source.tuning_override || !fileTuning) return 0;
  const differences = source.tuning_override.map((pitch, string) => pitch - fileTuning[string]);
  if (differences.every((difference) => difference === differences[0])) return differences[0];
  statusLabel.textContent = 'this run used a tuning override that is not a plain shift: check the tuning shift box';
  return 0;
}

/** The run bar's passage settings for the open GP file, or null (with a status message) if incomplete. */
function readSettings() {
  const number = (id) => (element(id).value === '' ? null : Number(element(id).value));
  const settings = {
    gp: state.song?.path, track: state.song?.trackIndex, first_bar: number('first-bar'), last_bar: number('last-bar'),
    voice: number('voice') ?? 0, tempo_override_bpm: number('tempo'), tuning_shift: number('tuning-shift') ?? 0,
  };
  if (!settings.gp) {
    statusLabel.textContent = 'open a GP file first';
    return null;
  }
  if (settings.first_bar === null || settings.last_bar === null) {
    statusLabel.textContent = 'drag over bars in the tab, or type the first and last bar';
    return null;
  }
  return settings;
}

/** Fill the run bar from passage settings (k too, when given). */
function fillRunBar(settings, kBest = null) {
  element('first-bar').value = settings?.first_bar ?? '';
  element('last-bar').value = settings?.last_bar ?? '';
  element('voice').value = settings?.voice ?? 0;
  element('tempo').value = settings?.tempo_override_bpm ?? '';
  element('tuning-shift').value = settings?.tuning_shift ?? 0;
  if (kBest) element('k-best').value = kBest;
}

/** The passage on screen with its choices and positions, or null while a GP file is shown. */
function shownPassage() {
  if (state.view?.kind === 'realisation') {
    const realisation = state.run.run.realisations[state.view.rank - 1];
    return { passage: state.run.run.passage, positions: state.run.positions, choices: realisation.choices, realisation, editable: false };
  }
  if (state.view?.kind === 'annotation') {
    const { passage, positions, choices } = state.annotation;
    return { passage, positions, choices, realisation: null, editable: true };
  }
  return null;
}

/** True if two choices for one note differ in string, fret, finger or stroke. */
function choicesDiffer(a, b) {
  return ['string', 'fret', 'finger', 'stroke'].some((field) => a[field] !== b[field]);
}

/** True if an annotation note differs from the draft it started as. */
function changedFromStart(index) {
  return choicesDiffer(state.annotation.choices[index], state.annotation.startChoices[index]);
}

// ---- Which beat is which passage note ----

/**
 * Map each beat of a drawn passage to its passage note. Every passage note starts exactly one beat:
 * a note's first piece, or a labelled rest (null symbol, or a note with no position yet). Tied
 * pieces belong to the note before; unlabelled rests (gaps) to none.
 */
function buildBeatMap(track) {
  const noteOfBeat = new Map();
  const beatsInOrder = [];
  const firstBeatOfNote = [];
  let current = null;
  for (const bar of track.staves[0].bars) {
    for (const beat of bar.voices[0].beats) {
      let index = null;
      if (!beat.isRest && beat.notes[0].isTieDestination) {
        index = current;
      } else if (!beat.isRest || beat.text) {
        index = firstBeatOfNote.length;
        firstBeatOfNote.push(beat);
        current = index;
      }
      noteOfBeat.set(beat.id, index);
      beatsInOrder.push(beat);
    }
  }
  return { noteOfBeat, beatsInOrder, firstBeatOfNote };
}

let beatMap = null;
let beatMapScore = null;
/** The beat map of the passage on screen (rebuilt when the drawn score changes). */
function currentBeatMap() {
  if (beatMapScore !== api.score) {
    beatMap = buildBeatMap(api.score.tracks[0]);
    beatMapScore = api.score;
  }
  return beatMap;
}

// ---- What the other page files get (see the top of this file) ----

const viewer = {
  element, statusLabel, api, state, pageConfig, postJson, remember, recall,
  renderView, shownPassage, choicesDiffer, changedFromStart, currentBeatMap, updateDropdowns, openFile, showTrack,
};
const fretboard = setupFretboard(viewer);
// annotate.mjs redraws the fretboard after each selection
viewer.updateFretboard = fretboard.updateFretboard;
const annotate = setupAnnotate(viewer);
const mixer = setupTrackMixer(viewer);
const filePicker = setupFilePicker(viewer);

// ---- Drawing ----

let pendingScrollTop = null;

/** Draw what state.view says: the GP file's track, or the passage played one way. Keeps the scroll position if asked. */
function renderView(keepScroll = false) {
  pendingScrollTop = keepScroll ? viewport.scrollTop : null;
  const shown = shownPassage();
  if (state.view?.kind === 'song') {
    api.renderScore(state.song.score, [state.song.trackIndex]);
  } else if (shown) {
    const editing = state.view.kind === 'annotation';
    const choices = editing
      ? shown.choices.map((choice, index) => ({ ...choice, stroke_letter: annotationStrokeLetter(shown.passage, shown.choices, index) }))
      : shown.choices;
    const trackName = editing ? `annotation (from ${state.annotation.startedFrom})` : `#${shown.realisation.rank} · cost ${shown.realisation.total_cost.toFixed(2)}`;
    const score = alphaTab.importer.ScoreLoader.loadAlphaTex(choicesToTex(shown.passage, choices, shown.passage.name, trackName), new alphaTab.Settings());
    if (editing) colourChangedNotes(score);
    api.renderScore(score, [0]);
  }
  updateTitle();
  updateTuningLabel();
  fretboard.updateFretboard();
  updateDropdowns();
  updateButtons();
  annotate.updatePanel();
  mixer.updateMixer();
  mixer.applyMixer();
}

/** Colour the fret numbers of annotation notes that differ from the starting draft. */
function colourChangedNotes(score) {
  const { firstBeatOfNote } = buildBeatMap(score.tracks[0]);
  firstBeatOfNote.forEach((beat, index) => {
    if (beat.isRest || !changedFromStart(index)) return;
    const style = new alphaTab.model.NoteStyle();
    style.colors.set(alphaTab.model.NoteSubElement.GuitarTabFretNumber, CHANGED_NOTE_COLOUR);
    beat.notes[0].style = style;
  });
}

api.renderStarted.on(() => element('selection-boxes').replaceChildren());
api.postRenderFinished.on(() => {
  if (pendingScrollTop !== null) viewport.scrollTop = pendingScrollTop;
  pendingScrollTop = null;
  annotate.drawSelectionBoxes();
});

function updateTitle() {
  const song = state.song?.score;
  const songTitle = song ? [song.artist, song.title].filter(Boolean).join(' — ') || '(untitled)' : null;
  const passageName = shownPassage()?.passage.name;
  element('song-title').textContent = (state.view?.kind === 'song' ? songTitle : passageName ?? songTitle) ?? 'No file open';
}

/** The toolbar's tuning label: the open strings of what is shown, lowest string first. */
function updateTuningLabel() {
  const guitar = fretboard.shownGuitar();
  element('tuning').textContent = guitar ? tuningText(guitar.tuning) : '';
}

/** Track dropdown = the song's tracks; realisation dropdown = the run's realisations + the annotation. "---" marks the one not shown. */
function updateDropdowns() {
  const tracks = state.song ? state.song.score.tracks.map((track) => new Option(`${track.index}: ${track.name}`, track.index)) : [];
  trackSelect.replaceChildren(new Option('---', ''), ...tracks);
  trackSelect.disabled = !state.song;
  trackSelect.value = state.view?.kind === 'song' ? String(state.song.trackIndex) : '';

  const options = [new Option('---', '')];
  for (const realisation of state.run?.run.realisations ?? []) {
    options.push(new Option(`#${realisation.rank} · cost ${realisation.total_cost.toFixed(2)}`, `rank:${realisation.rank}`));
  }
  if (state.annotation) {
    options.push(new Option(`✎ annotation${state.annotation.dirty ? ' (unsaved)' : ''} — from ${state.annotation.startedFrom}`, 'annotation'));
  }
  realisationSelect.replaceChildren(...options);
  realisationSelect.disabled = options.length === 1;
  if (state.view?.kind === 'realisation') realisationSelect.value = `rank:${state.view.rank}`;
  else if (state.view?.kind === 'annotation') realisationSelect.value = 'annotation';
  else realisationSelect.value = '';
}

function updateButtons() {
  element('run-button').disabled = !state.song;
  element('edit-button').disabled = !(state.view?.kind === 'song' || state.view?.kind === 'realisation');
  element('save-button').disabled = !state.annotation;
}

// ---- Opening songs (GP files) and runs ----

/** Fetch a GP file and read it (not drawn yet); false (with a status message) if that fails.
 *  A short path is looked up in the `searched` datasets; the song keeps the server's full path. */
async function loadSong(path, trackIndex, searched = []) {
  statusLabel.textContent = 'loading…';
  const response = await fetch(`/api/file?${new URLSearchParams({ path, datasets: searched.join(',') })}`);
  if (!response.ok) {
    statusLabel.textContent = `server: ${response.status} ${response.statusText}`;
    return false;
  }
  try {
    const score = alphaTab.importer.ScoreLoader.loadScoreFromBytes(new Uint8Array(await response.arrayBuffer()), fileSettings);
    const fullPath = decodeURIComponent(response.headers.get('X-GP-Path') ?? encodeURIComponent(path));
    state.song = { path: fullPath, trackIndex, score };
  } catch (error) {
    statusLabel.textContent = `alphaTab can't read this file: ${error.message ?? error}`;
    return false;
  }
  element('path-input').value = state.song.path;
  statusLabel.textContent = '';
  return true;
}

/** Open a GP file on one track; closes any run and annotation. True if it opened. */
async function openFile(path, trackIndex = 0, searched = []) {
  if (!confirmDiscardAnnotation('Open another file')) return false;
  api.stop();
  if (!(await loadSong(path, trackIndex, searched))) return false;
  Object.assign(state, { run: null, annotation: null, view: { kind: 'song' }, selection: [], draggedBeats: null });
  filePicker.clearListSelection();
  fillRunBar(null);
  renderView();
  return true;
}

/**
 * Fetch a run and its positions, open its GP file if needed, and show realisation #1. The page state
 * (annotation, selection) changes only once everything has loaded, so a failed load leaves the page as it was.
 *
 * Args:
 *   name: The run file's name.
 *   keepAnnotation: Keep the open annotation (a run on the same passage as it).
 */
async function showRun(name, keepAnnotation = false) {
  statusLabel.textContent = 'loading…';
  const response = await fetch(`/api/run?name=${encodeURIComponent(name)}`);
  if (!response.ok) {
    statusLabel.textContent = `server: ${response.status} ${response.statusText}`;
    return;
  }
  const run = await response.json();
  if (run.format?.name !== 'grip_optimiser_run') {
    statusLabel.textContent = `${name} is not an optimiser run file`;
    return;
  }
  // Every note's neck positions, from the optimiser's own candidate_positions on the server
  const positions = await (await fetch(`/api/positions?name=${encodeURIComponent(name)}`)).json();
  if (run.source.gp && state.song?.path !== run.source.gp) {
    if (!(await loadSong(run.source.gp, run.source.track))) return;
  } else if (!run.source.gp) {
    state.song = null;
  }
  if (state.song) state.song.trackIndex = run.source.track ?? state.song.trackIndex;
  statusLabel.textContent = '';
  filePicker.clearListSelection();
  if (!keepAnnotation) state.annotation = null;
  state.selection = [];
  state.run = { name, run, positions };
  fillRunBar(settingsFromSource(run.source), run.source.k_best);
  state.view = { kind: 'realisation', rank: 1 };
  renderView();
}

/** Fill the run picker with the server's run files, newest first. */
async function refreshRunList() {
  const names = await (await fetch('/api/runs')).json();
  element('run-select').replaceChildren(new Option(`Optimiser runs (${names.length})…`, ''), ...names.map((name) => new Option(name, name)));
}

element('run-select').onchange = async (event) => {
  const name = event.target.value;
  event.target.value = '';
  if (!name || !confirmDiscardAnnotation('Open another run')) return;
  api.stop();
  await showRun(name);
};
element('runs-refresh').onclick = refreshRunList;
refreshRunList();

// ---- Toolbar: dropdowns, Run, Edit, sidebar ----

/** Show one track of the open song in the song view (the track dropdown, or a click in the mixer). */
function showTrack(trackIndex) {
  state.song.trackIndex = trackIndex;
  state.view = { kind: 'song' };
  renderView();
}

trackSelect.onchange = () => {
  trackSelect.blur();
  if (trackSelect.value !== '') showTrack(Number(trackSelect.value));
};

realisationSelect.onchange = () => {
  realisationSelect.blur();
  const value = realisationSelect.value;
  if (value === '') return;
  state.view = value === 'annotation' ? { kind: 'annotation' } : { kind: 'realisation', rank: Number(value.split(':')[1]) };
  renderView();
};

/** Run the optimiser on the run bar's passage; the run is saved and opened. Keeps an annotation of the same passage. */
async function runOptimiser() {
  const settings = readSettings();
  if (!settings) return;
  const keepAnnotation = state.annotation && sameSettings(state.annotation.settings, settings);
  if (state.annotation && !keepAnnotation && !confirmDiscardAnnotation('Run on a different passage')) return;
  const runButton = element('run-button');
  runButton.disabled = true;
  const started = Date.now();
  const timer = setInterval(() => { statusLabel.textContent = `running optimiser… ${Math.round((Date.now() - started) / 1000)} s`; }, 500);
  const kBest = element('k-best').value;
  const reply = await postJson('/api/optimise', { ...settings, k_best: kBest === '' ? null : Number(kBest) });
  clearInterval(timer);
  runButton.disabled = false;
  if (reply.error) {
    statusLabel.textContent = `Run failed: ${reply.error}`;
    return;
  }
  api.stop();
  await refreshRunList();
  await showRun(reply.name, keepAnnotation);
}
element('run-button').onclick = runOptimiser;

/** Start an annotation from what is shown: a realisation (its choices) or the GP file (the tab's own marks). */
async function startEditing() {
  if (state.annotation && !confirm(`Start a new annotation? The current one${state.annotation.dirty ? ' has unsaved edits and' : ''} will be closed.`)) return;
  let annotation;
  if (state.view?.kind === 'realisation') {
    const realisation = state.run.run.realisations[state.view.rank - 1];
    const choices = realisation.choices.map((choice) => ({
      passage_index: choice.passage_index, string: choice.string, fret: choice.fret,
      finger: choice.string === null ? null : choice.finger, stroke: choice.string === null ? null : choice.stroke,
    }));
    annotation = {
      settings: settingsFromSource(state.run.run.source), passage: state.run.run.passage, positions: state.run.positions,
      choices, startedFrom: `rank ${realisation.rank} of ${state.run.name}`, report: [],
    };
  } else if (state.view?.kind === 'song') {
    const settings = readSettings();
    if (!settings) return;
    statusLabel.textContent = 'cutting the passage…';
    const reply = await postJson('/api/passage', settings);
    if (reply.error) {
      statusLabel.textContent = `Edit failed: ${reply.error}`;
      return;
    }
    statusLabel.textContent = '';
    annotation = {
      settings: settingsFromSource(reply.source), passage: reply.passage, positions: reply.positions,
      choices: reply.draft, startedFrom: 'source tab', report: reply.report,
    };
  } else {
    return;
  }
  state.annotation = { ...annotation, startChoices: structuredClone(annotation.choices), dirty: false };
  state.view = { kind: 'annotation' };
  state.selection = [];
  api.stop();
  renderView();
}
element('edit-button').onclick = startEditing;

// Warn before closing the page with unsaved annotation edits
window.addEventListener('beforeunload', (event) => {
  if (state.annotation?.dirty) event.preventDefault();
});

/** Hide or show the file panel; remembered in this browser. */
function setSidebarHidden(hidden) {
  document.body.classList.toggle('sidebar-hidden', hidden);
  remember('sidebarHidden', hidden ? '1' : '0');
  if (api.score) api.render();
}
element('sidebar-toggle').onclick = () => setSidebarHidden(!document.body.classList.contains('sidebar-hidden'));
document.body.classList.toggle('sidebar-hidden', recall('sidebarHidden', '0') === '1');

// The fretboard follows play / pause and the playback position. Kept last: alphaTab calls a new player
// listener at once, and updateFretboard needs everything above to exist by then. (No midiLoaded listener:
// in alphaTab 1.8.4 adding one loops forever in the worker player's loadedMidiInfo getter; the playing
// order is ready anyway once renderScore returns.)
api.playerStateChanged.on(() => fretboard.updateFretboard());
api.playerPositionChanged.on((event) => fretboard.updateFretboard(event.currentTick));

// ---- Start: empty note panel ----

annotate.updatePanel();
