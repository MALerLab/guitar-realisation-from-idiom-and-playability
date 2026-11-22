// GRIP viewer, main script: alphaTab, the page state, drawing what is shown, the top bar
// (catalogue pick, tracks, Bars box, Optimise pop-up, realisations, Edit), the sidebar and drawers, and opening
// songs (GP files) and runs.
// The other files each get what they need from here through one `viewer` object when the page starts:
//   annotate.mjs         note panel, selecting notes, the edit keys, the Save box
//   catalogue_view.mjs   the catalogue view: interval paths, layouts, their family, where they turn up
//   file_picker.mjs      path box, the Datasets tab (songs / tracks search)
//   flags.mjs            junk flags: the flag box (song view's top bar, catalogue player)
//   optimiser_view.mjs   the Optimiser view: the lr-knob (an lr-knob sweep's segments), the cost strip, the knob form, presets
//   piano_roll.mjs       a MIDI file in the song view: the piano roll, picking bars on it, the playhead
//   stitch.mjs           the Entries overlay: block boxes + uncovered notes (the chooser's, or any tab's)
//   storage_browser.mjs  the Files tab: recent files + the storage folder as on disk
//   transport.mjs        the transport bar: play / pause, bars, scrub, loop, speed, their keys
//   fretboard.mjs        the fretboard drawer, and the tuning label's text
//   track_mixer.mjs      the open song's tracks: mute, solo, volume
// choices_to_tex.mjs turns a passage played one way into alphaTex for drawing.
import * as alphaTab from '/alphatab/alphaTab.mjs';
import { annotationStrokeLetter, choicesToTex } from '/choices_to_tex.mjs';
import { setupAnnotate } from '/annotate.mjs';
import { setupCatalogueView } from '/catalogue_view.mjs';
import { setupFilePicker } from '/file_picker.mjs';
import { setupFlags } from '/flags.mjs';
import { setupFretboard, tuningText } from '/fretboard.mjs';
import { setupOptimiserView } from '/optimiser_view.mjs';
import { setupPianoRoll } from '/piano_roll.mjs';
import { setupStitch } from '/stitch.mjs';
import { setupStorageBrowser } from '/storage_browser.mjs';
import { setupTrackMixer } from '/track_mixer.mjs';
import { setupTransport } from '/transport.mjs';

const element = (id) => document.getElementById(id);
const statusLabel = element('status');
const trackSelect = element('track-select');
const realisationSelect = element('realisation-select');
const viewport = element('viewport');

// Encoding, soundfont, default k, the fretboard's fret count, the datasets, the storage folder and the reference folder come from the server config
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
  song: null,         // the open song: a GP file { path, trackIndex, score, mixer (track_mixer.mjs: track index → sound settings) },
                      //   or a MIDI file { kind: 'midi', path, trackIndex: 0, score (its hidden playback tab), midi (/api/midi) }
  songFlags: {},      // the open song's junk flags: track index (as text) → flag
  run: null,          // the open run (optimiser, existing tool or stitch): { path, name, kind, label, run: { source, passage, realisations }, positions,
                      //   stitch (kind 'stitch' only: { build, mode, members_only, blocks, uncovered }) }
  annotation: null,   // { settings, passage, positions, choices, startChoices, startedFrom, dirty, report }
  phrase: null,       // the catalogue player's occurrence: { passage, choices, positions, label }
  sweep: null,        // the Optimiser view's lr-knob sweep: { settings, overrides, lr_knob, terms, segments, passage, positions, lrKnobValue }
  songSideView: null, // the song view's `view`, kept while the catalogue or Optimiser view shows (they share one tab)
  view: null,         // what the score shows: { kind: 'song' } | { kind: 'realisation', rank } | { kind: 'annotation' }
                      //   | { kind: 'phrase' } (catalogue view: the player's occurrence)
                      //   | { kind: 'segment', index } (Optimiser view: one segment of the lr-knob sweep)
  selection: [],      // selected passage note indices, in order (realisation + annotation views)
  draggedBeats: null, // the song view's last click or drag: { first, last } alphaTab beats
};

const CHANGED_NOTE_COLOUR = new alphaTab.model.Color(224, 90, 0);

// ---- alphaTab: progress, errors (the transport bar is transport.mjs) ----

api.error.on((error) => { statusLabel.textContent = `alphaTab error: ${error.message ?? error}`; });
api.soundFontLoad.on((progress) => {
  statusLabel.textContent = `soundfont ${Math.round((100 * progress.loaded) / progress.total)}%`;
});
api.playerReady.on(() => { statusLabel.textContent = ''; });

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
  const key = (s) => JSON.stringify([s.gp ?? s.midi, s.track, s.first_bar, s.last_bar, s.voice, s.tempo_override_bpm ?? null, s.tuning_shift]);
  return key(a) === key(b);
}

/** Passage settings from a run source ("bars": "17-28"); tuning shift worked out for old runs that stored a tuning override. */
function settingsFromSource(source) {
  if (source?.midi) {
    const [firstBar, lastBar] = source.bars ? source.bars.split('-').map(Number) : [null, null];
    return { midi: source.midi, first_bar: firstBar, last_bar: lastBar, tempo_override_bpm: source.tempo_override_bpm ?? null,
             tuning_shift: source.tuning_shift ?? 0 };
  }
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
  const common = {
    first_bar: number('first-bar'), last_bar: number('last-bar'), tempo_override_bpm: number('tempo'),
    tuning_shift: number('tuning-shift') ?? 0,
  };
  const settings = state.song?.kind === 'midi'
    ? { midi: state.song.path, ...common }
    : { gp: state.song?.path, track: state.song?.trackIndex, voice: number('voice') ?? 0, ...common };
  if (!settings.gp && !settings.midi) {
    statusLabel.textContent = 'open a GP or MIDI file first';
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
  if (state.view?.kind === 'phrase') {
    const { passage, positions, choices, label } = state.phrase;
    return { passage, positions, choices, realisation: null, editable: false, label };
  }
  if (state.view?.kind === 'segment') {
    const { passage, positions, segments } = state.sweep;
    const realisation = segments[state.view.index].realisation;
    return { passage, positions, choices: realisation.choices, realisation, editable: false, label: optimiserView.segmentLabel(state.view.index) };
  }
  return null;
}

/** Passage settings of the passage on screen (a run's, the annotation's, the lr-knob sweep's), or null
 *  (the song itself, the catalogue player, or a run with no GP file behind it). */
function shownPassageSettings() {
  if (state.view?.kind === 'realisation') return settingsFromSource(state.run.run.source);
  if (state.view?.kind === 'annotation') return state.annotation.settings;
  if (state.view?.kind === 'segment') return state.sweep.settings;
  return null;
}

/** The catalogue picked in the top bar (its folder, full path), or null. */
function currentCatalogue() {
  return element('catalogue-select').value || null;
}

/** What Optimise and the Optimiser view send for the chooser: the picked catalogue (null = the hand models alone). */
function catalogueRequest() {
  return { catalogue: currentCatalogue(), members_only: stitch.membersOnly() };
}

/** Which view shows: 'song', 'catalogue' or 'optimiser'. */
function currentMode() {
  if (document.body.classList.contains('catalogue-mode')) return 'catalogue';
  if (document.body.classList.contains('optimiser-mode')) return 'optimiser';
  return 'song';
}

/** True while the catalogue view shows. */
function inCatalogueMode() {
  return currentMode() === 'catalogue';
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
  showRunFile, onFileOpened, openCatalogue, openPhrase, setView, showPhrase, currentMode,
  readSettings, sameSettings, confirmDiscardAnnotation, openRunView, openCatalogueEntry, buildBeatMap,
  shownPassageSettings, currentCatalogue, catalogueRequest,
};
const flags = setupFlags(viewer);
// The catalogue player flags tracks too
Object.assign(viewer, { openFlagBox: flags.openFlagBox, showFlagButton: flags.showFlagButton });
const fretboard = setupFretboard(viewer);
// annotate.mjs redraws the fretboard after each selection
viewer.updateFretboard = fretboard.updateFretboard;
const annotate = setupAnnotate(viewer);
// The Optimiser view's cost strip marks the selected note on the tab
viewer.drawSelectionBoxes = annotate.drawSelectionBoxes;
const mixer = setupTrackMixer(viewer);
const transport = setupTransport(viewer);
const filePicker = setupFilePicker(viewer);
const catalogueView = setupCatalogueView(viewer);
const stitch = setupStitch(viewer);
const optimiserView = setupOptimiserView(viewer);
const pianoRoll = setupPianoRoll(viewer);
const storageBrowser = setupStorageBrowser(viewer);

// ---- Drawing ----

let pendingScrollTop = null;

/** Draw what state.view says: the GP file's track, or the passage played one way. Keeps the scroll position if asked. */
function renderView(keepScroll = false) {
  pendingScrollTop = keepScroll ? viewport.scrollTop : null;
  // Panels first: alphaTab lays the tab out to the width left once the right column shows or hides
  updatePanels();
  const shown = shownPassage();
  if (state.view?.kind === 'song') {
    api.renderScore(state.song.score, [state.song.trackIndex]);
  } else if (shown) {
    const editing = state.view.kind === 'annotation';
    const choices = editing
      ? shown.choices.map((choice, index) => ({ ...choice, stroke_letter: annotationStrokeLetter(shown.passage, shown.choices, index) }))
      : shown.choices;
    let trackName;
    if (editing) trackName = `annotation (from ${state.annotation.startedFrom})`;
    else if (state.view.kind === 'phrase' || state.view.kind === 'segment') trackName = shown.label;
    else trackName = realisationLabel(shown.realisation);
    const score = alphaTab.importer.ScoreLoader.loadAlphaTex(choicesToTex(shown.passage, choices, shown.passage.name, trackName), new alphaTab.Settings());
    if (editing) colourChangedNotes(score);
    else stitch.colourUncovered(score);
    api.renderScore(score, [0]);
  }
  if (!keepScroll) transport.resetPosition();
  updateTitle();
  updateTuningLabel();
  fretboard.updateFretboard();
  updateDropdowns();
  updateButtons();
  annotate.updatePanel();
  optimiserView.updateView();
  pianoRoll.draw();
  mixer.updateMixer();
  mixer.applyMixer();
}

/** What shows around the tab: the empty-state hint until something is drawn, the note panel while a
 *  passage (realisation / annotation) is shown, the knob form in the Optimiser view, the mixer's "no song" line. */
function updatePanels() {
  const mode = currentMode();
  document.body.classList.toggle('has-score', state.view !== null);
  const midiSong = mode === 'song' && state.view?.kind === 'song' && state.song?.kind === 'midi';
  document.body.classList.toggle('midi-song', midiSong);
  // The hidden playback tab mustn't scroll the page; the piano roll keeps its own playhead in view
  const scrollMode = midiSong ? alphaTab.ScrollMode.Off : alphaTab.ScrollMode.Continuous;
  if (api.settings.player.scrollMode !== scrollMode) {
    api.settings.player.scrollMode = scrollMode;
    api.updateSettings();
  }
  document.body.classList.toggle('details-open', (shownPassage() !== null && mode === 'song') || mode === 'optimiser');
  element('mixer-empty').hidden = Boolean(state.song);
  const hints = {
    catalogue: 'Pick an interval path on the left to play one of its occurrences here.',
    optimiser: optimiserView.emptyHint(),
    song: 'Pick a file on the left: recent runs, the storage folders, or the datasets.',
  };
  element('empty-state').querySelector('strong').textContent = mode === 'optimiser' ? 'No passage yet' : 'Nothing open yet';
  element('empty-state').querySelector('span').textContent = hints[mode];
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

api.renderStarted.on(() => {
  element('selection-boxes').replaceChildren();
  element('stitch-boxes').replaceChildren();
});
api.postRenderFinished.on(() => {
  if (pendingScrollTop !== null) viewport.scrollTop = pendingScrollTop;
  pendingScrollTop = null;
  annotate.drawSelectionBoxes();
  stitch.drawBlockBoxes();
});

/** A realisation's name in the dropdown and over the tab: the run's label, its rank and cost (a stitch has neither). */
function realisationLabel(realisation) {
  if (state.run.kind === 'stitch') return state.run.label;
  return `${state.run.label} #${realisation.rank} · cost ${realisation.total_cost.toFixed(2)}`;
}

function updateTitle() {
  if (inCatalogueMode()) return;
  const song = state.song?.score;
  const songTitle = song ? [song.artist, song.title].filter(Boolean).join(' — ') || '(untitled)' : null;
  const passageName = shownPassage() && state.run && state.view.kind === 'realisation'
    ? `${shownPassage().passage.name} · ${state.run.label}` : shownPassage()?.passage.name;
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
    options.push(new Option(realisationLabel(realisation), `rank:${realisation.rank}`));
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
  for (const id of ['run-button', 'run-toggle']) element(id).disabled = !state.song;
  element('view-switch').querySelector('[data-view=optimiser]').disabled = !state.song;
  stitch.updateEntriesButton();
  if (!state.song) closePopovers();
  const midiSong = state.song?.kind === 'midi';
  // Annotating and junk flags need a GP tab's track
  element('edit-button').disabled = midiSong || !(state.view?.kind === 'song' || state.view?.kind === 'realisation');
  element('save-button').disabled = !state.annotation;
  flags.showFlagButton(element('flag-button'), state.songFlags[String(state.song?.trackIndex)], state.view?.kind === 'song' && !midiSong);
}

/** The song view's junk button: flag the shown track, or every track of the song. */
element('flag-button').onclick = () => {
  const { score, trackIndex, path } = state.song;
  const songTitle = [score.artist, score.title].filter(Boolean).join(' — ') || path.split('/').at(-1);
  flags.openFlagBox({
    gpPath: path, trackIndex, trackCount: score.tracks.length,
    title: `${songTitle} · track ${trackIndex}: ${score.tracks[trackIndex].name}`,
    onChange: (songFlags) => {
      state.songFlags = songFlags;
      updateButtons();
      filePicker.refresh();
      storageBrowser.refresh();
    },
  });
};

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
    state.songFlags = await flags.fetchSongFlags(fullPath);
  } catch (error) {
    statusLabel.textContent = `alphaTab can't read this file: ${error.message ?? error}`;
    return false;
  }
  element('path-input').value = state.song.path;
  statusLabel.textContent = '';
  return true;
}

/** True if a path names a MIDI file (by its extension). */
function isMidiPath(path) {
  return pageConfig.midi_extensions.some((extension) => path.toLowerCase().endsWith(extension));
}

/** Fetch a MIDI file and make it the open song: its notes for the piano roll, and the whole file as a tab placed
 *  lowest fret first for playback; false (with a status message) if that fails. */
async function loadMidiSong(path) {
  statusLabel.textContent = 'loading…';
  const response = await fetch(`/api/midi?${new URLSearchParams({ path })}`);
  if (!response.ok) {
    statusLabel.textContent = `server: ${response.status} ${response.statusText}`;
    return false;
  }
  const midi = await response.json();
  const choices = midi.passage.notes.map((note, index) => {
    const lowest = midi.positions[index].reduce((best, position) => (!best || position.fret < best.fret ? position : best), null);
    return { passage_index: index, string: lowest?.string ?? null, fret: lowest?.fret ?? null, finger: 0, stroke_letter: null, hand: null };
  });
  const tex = choicesToTex(midi.passage, choices, midi.name, 'MIDI (played back on the lowest frets)');
  state.song = { kind: 'midi', path: midi.path, trackIndex: 0, score: alphaTab.importer.ScoreLoader.loadAlphaTex(tex, new alphaTab.Settings()), midi };
  state.songFlags = {};
  element('path-input').value = midi.path;
  statusLabel.textContent = '';
  return true;
}

/** Open a GP file on one track, or a MIDI file (piano roll); closes any run and annotation. True if it opened. */
async function openFile(path, trackIndex = 0, searched = []) {
  if (!confirmDiscardAnnotation('Open another file')) return false;
  if (currentMode() !== 'song') setView('song', false);
  api.stop();
  if (!(await (isMidiPath(path) ? loadMidiSong(path) : loadSong(path, trackIndex, searched)))) return false;
  Object.assign(state, { run: null, annotation: null, view: { kind: 'song' }, selection: [], draggedBeats: null });
  filePicker.clearListSelection();
  fillRunBar(null);
  renderView();
  return true;
}

/**
 * Fetch a run (optimiser or existing tool, with every note's positions), open its GP file if needed, and
 * show realisation #1. The page state (annotation, selection) changes only once everything has loaded, so
 * a failed load leaves the page as it was. True if it opened.
 *
 * Args:
 *   path: The run file's full path.
 *   keepAnnotation: Keep the open annotation (a run on the same passage as it).
 */
async function showRun(path, keepAnnotation = false) {
  if (currentMode() !== 'song') setView('song', false);
  statusLabel.textContent = 'loading…';
  const response = await fetch(`/api/run?${new URLSearchParams({ path })}`);
  if (!response.ok) {
    statusLabel.textContent = `server: ${response.status} ${response.statusText}`;
    return false;
  }
  return openRunView(await response.json(), keepAnnotation);
}

/**
 * Show a run view (from /api/run, or a stitch from /api/stitch): open its GP file if needed and show realisation #1.
 * True if it opened.
 *
 * Args:
 *   view: { path (null for a stitch), name, kind, label, source, passage, realisations, positions, stitch? }.
 *   keepAnnotation: Keep the open annotation (a run on the same passage as it).
 */
async function openRunView(view, keepAnnotation = false) {
  const source = view.source;
  if (source.gp && state.song?.path !== source.gp) {
    if (!(await loadSong(source.gp, source.track))) return false;
  } else if (source.midi && state.song?.path !== source.midi) {
    if (!(await loadMidiSong(source.midi))) return false;
  } else if (!source.gp && !source.midi) {
    state.song = null;
  }
  if (state.song) state.song.trackIndex = source.track ?? state.song.trackIndex;
  statusLabel.textContent = '';
  filePicker.clearListSelection();
  if (!keepAnnotation) state.annotation = null;
  state.selection = [];
  state.run = {
    path: view.path, name: view.name, kind: view.kind, label: view.label,
    run: { source, passage: view.passage, realisations: view.realisations }, positions: view.positions,
    stitch: view.stitch ?? null,
  };
  fillRunBar(settingsFromSource(source), source.k_best);
  state.view = { kind: 'realisation', rank: 1 };
  renderView();
  return true;
}

/** Open a run file from the sidebar (asks first if an annotation has unsaved edits). True if it opened. */
async function showRunFile(path) {
  if (!confirmDiscardAnnotation('Open another run')) return false;
  api.stop();
  return showRun(path);
}

// ---- Top bar: dropdowns, Run pop-up, Edit ----

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

/** Run the optimiser (hand models) on the Bars box's passage; the run is saved and opened. Keeps an annotation of the same passage. */
async function runOptimiser() {
  const settings = readSettings();
  if (!settings) return;
  const keepAnnotation = state.annotation && sameSettings(state.annotation.settings, settings);
  if (state.annotation && !keepAnnotation && !confirmDiscardAnnotation('Run on a different passage')) return;
  const runButton = element('run-button');
  runButton.disabled = true;
  element('run-popover').hidden = true;
  const started = Date.now();
  const timer = setInterval(() => { statusLabel.textContent = `running optimiser… ${Math.round((Date.now() - started) / 1000)} s`; }, 500);
  const kBest = element('k-best').value;
  const reply = await postJson('/api/optimise', { ...settings, ...catalogueRequest(), k_best: kBest === '' ? null : Number(kBest) });
  clearInterval(timer);
  runButton.disabled = false;
  if (reply.error) {
    statusLabel.textContent = `Run failed: ${reply.error}`;
    return;
  }
  api.stop();
  storageBrowser.refresh();
  await showRun(reply.path, keepAnnotation);
}
/** The pop-up's line on what Optimise will use: the hand models, + the chooser on the picked catalogue. */
function updateChooserLine() {
  const catalogue = element('catalogue-select').selectedOptions[0];
  element('chooser-line').textContent = currentCatalogue()
    ? `hand models + chooser on ${catalogue.textContent}`
    : 'hand models alone (pick a catalogue at the left of the top bar for the chooser)';
}
element('run-button').onclick = runOptimiser;

// The Optimise pop-up opens under its button; Esc or a click elsewhere closes it
function closePopovers() {
  element('run-popover').hidden = true;
}
element('run-toggle').onclick = () => {
  const opening = element('run-popover').hidden;
  closePopovers();
  element('run-popover').hidden = !opening;
  if (opening) updateChooserLine();
};

// A new passage typed in the Bars box while the Optimiser view shows: sweep it
for (const id of ['first-bar', 'last-bar']) {
  element(id).addEventListener('change', () => { if (currentMode() === 'optimiser') optimiserView.enter(); });
  element(id).addEventListener('input', () => pianoRoll.markChosenBars());  // the roll shades the bars typed
}
document.addEventListener('pointerdown', (event) => {
  if (!event.target.closest('.popover-anchor')) closePopovers();
});
document.addEventListener('keydown', (event) => {
  if (event.key === 'Escape') closePopovers();
});

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

// ---- Song view / catalogue view / Optimiser view ----

/**
 * Show the song view ('song'), the catalogue view ('catalogue') or the Optimiser view ('optimiser'). They share
 * one tab, fretboard and transport: the tab and fretboard move into the catalogue's player slot and back, and
 * each view keeps what it showed. Entering the Optimiser view runs an lr-knob sweep on the song view's bars if needed.
 *
 * Args:
 *   view: 'song', 'catalogue' or 'optimiser'.
 *   redraw: Draw the view's own score now (false when the caller draws something new straight after).
 */
function setView(view, redraw = true) {
  const from = currentMode();
  if (view !== from) {
    if (from === 'song') state.songSideView = state.view;
    if (view === 'song') {
      state.view = state.songSideView;
      state.songSideView = null;
    } else if (view === 'catalogue') {
      state.view = state.phrase ? { kind: 'phrase' } : null;
    } else {
      state.view = optimiserView.viewOfSweep();
    }
    state.selection = [];
    api.stop();
    document.body.classList.toggle('catalogue-mode', view === 'catalogue');
    document.body.classList.toggle('optimiser-mode', view === 'optimiser');
    if (view === 'catalogue') element('catalogue-player-slot').append(viewport, element('fretboard-panel'));
    else element('main').append(element('lr-knob-bar'), viewport, element('cost-strip'), element('fretboard-panel'));
  }
  for (const button of document.querySelectorAll('#view-switch button')) {
    button.setAttribute('aria-pressed', String(button.dataset.view === view));
  }
  // The mixer is for songs (the player's phrase and the sweep's tab are one track); the explorer only exists in the catalogue view
  element('mixer-toggle').disabled = view !== 'song';
  element('explorer-toggle').disabled = view !== 'catalogue';
  if (view === 'catalogue') element('song-title').textContent = catalogueView.catalogueName() ?? 'Catalogue';
  if (redraw) renderView();
  if (view === 'optimiser' && view !== from) optimiserView.enter();
}

/** Play a catalogue occurrence in the catalogue view's player, or empty it with null (catalogue_view.mjs calls this). */
function showPhrase(phrase) {
  state.phrase = phrase;
  if (!inCatalogueMode()) return;
  api.stop();
  state.view = phrase ? { kind: 'phrase' } : null;
  state.selection = [];
  renderView();
}
for (const button of document.querySelectorAll('#view-switch button')) button.onclick = () => setView(button.dataset.view);

/**
 * Load a catalogue build: the Catalogue switch, Optimise (catalogue) and Entries use it from then on. True if it opened.
 *
 * Args:
 *   folder: The build's folder (full path).
 *   showView: Switch to the catalogue view too (opened from the Files tab); the top bar's pick stays where it is.
 */
async function openCatalogue(folder, showView = true) {
  const opened = await catalogueView.openCatalogue(folder, showView);
  if (opened) {
    await fillCatalogueChoice(folder);
    remember('catalogue', folder);
    element('view-switch').querySelector('[data-view=catalogue]').disabled = false;
    catalogueChanged();
  }
  return opened;
}

/** The top bar's catalogue pick: every build in the storage folder (newest first), `picked` chosen. */
async function fillCatalogueChoice(picked = currentCatalogue()) {
  const response = await fetch('/api/catalogue/builds');
  const builds = response.ok ? await response.json() : [];
  const select = element('catalogue-select');
  // Shown as <folder under the storage root>/<build>, e.g. catalogue/bpe/261118_goat_test
  select.replaceChildren(new Option('no catalogue', ''), ...builds.map((build) => new Option(`${build.folder}/${build.name}`, build.path)));
  select.value = builds.some((build) => build.path === picked) ? picked : '';
  select.classList.toggle('empty', !select.value);
}

element('catalogue-select').onchange = async () => {
  const select = element('catalogue-select');
  select.blur();
  select.classList.toggle('empty', !select.value);
  if (select.value) {
    await openCatalogue(select.value, false);
    return;
  }
  // No catalogue: the catalogue view, Optimise (catalogue) and Entries switch off
  remember('catalogue', '');
  element('view-switch').querySelector('[data-view=catalogue]').disabled = true;
  if (currentMode() === 'catalogue') setView('song');
  catalogueChanged();
};

/** After the catalogue pick changes: Entries follow it, the Optimiser view sweeps again with the chooser on it. */
function catalogueChanged() {
  updateChooserLine();
  renderView(true);
  if (currentMode() === 'optimiser') optimiserView.enter();
}
// New builds show up once the pointer comes over the pick (refilling while it is open would close it)
element('catalogue-select').addEventListener('pointerenter', () => fillCatalogueChoice());

/** Open one entry of a catalogue build in the catalogue view (a Stitch block's badge): the build first if it isn't open. */
async function openCatalogueEntry(folder, intervalPath, entryId) {
  if (catalogueView.openBuild() !== folder && !(await openCatalogue(folder))) return;
  setView('catalogue');
  await catalogueView.pickEntry(intervalPath, entryId);
}

/**
 * Open the song an occurrence comes from, in the tab view, and mark its phrase: from its first note's
 * beat to its last note's, set as the playback range (loop on = it repeats) with the cursor at its start.
 *
 * Args:
 *   occurrence: { gp_path, track_index, first_bar_index, first_onset_tick, last_bar_index, last_onset_tick }
 *     (onset ticks = alphaTab's absolutePlaybackStart of those beats).
 */
async function openPhrase(occurrence) {
  setView('song', false);  // the song is drawn fresh below
  // Wait for the drawing of this song (its beats need a layout before they can be marked); earlier drawings don't count
  let drawn;
  const scoreBefore = api.score;
  const whenDrawn = new Promise((resolve) => {
    drawn = () => { if (api.score !== scoreBefore && api.score === state.song?.score) resolve(); };
  });
  api.postRenderFinished.on(drawn);
  const opened = await openFile(occurrence.gp_path, occurrence.track_index);
  if (opened) await whenDrawn;
  api.postRenderFinished.off(drawn);
  if (!opened) return;
  const track = api.score.tracks[occurrence.track_index];
  const startBeat = beatAt(track, occurrence.first_bar_index, occurrence.first_onset_tick);
  const endBeat = beatAt(track, occurrence.last_bar_index, occurrence.last_onset_tick);
  if (!startBeat || !endBeat || !api.tickCache) {
    statusLabel.textContent = `couldn't find that phrase's notes (bar ${occurrence.first_bar_index + 1})`;
    return;
  }
  const startTick = api.tickCache.getBeatStart(startBeat);
  api.highlightPlaybackRange(startBeat, endBeat);
  api.playbackRange = { startTick, endTick: api.tickCache.getBeatStart(endBeat) + endBeat.playbackDuration };
  api.tickPosition = startTick;
  api.scrollToCursor();
  statusLabel.textContent = `phrase at bar ${occurrence.first_bar_index + 1} marked: play (or loop) plays just that`;
}

/** The beat of a track that starts at `onsetTick` in bar `barIndex` (a note, not a rest), or null. */
function beatAt(track, barIndex, onsetTick) {
  const bar = track?.staves[0].bars[barIndex];
  for (const voice of bar?.voices ?? []) {
    const beat = voice.beats.find((candidate) => !candidate.isRest && candidate.absolutePlaybackStart === onsetTick);
    if (beat) return beat;
  }
  return null;
}

// ---- Sidebar, its tabs, the mixer drawer ----

/** Hide or show the sidebar; remembered in this browser. */
function setSidebarHidden(hidden) {
  document.body.classList.toggle('sidebar-hidden', hidden);
  remember('sidebarHidden', hidden ? '1' : '0');
  if (api.score) api.render();
}
element('sidebar-toggle').onclick = () => setSidebarHidden(!document.body.classList.contains('sidebar-hidden'));
document.body.classList.toggle('sidebar-hidden', recall('sidebarHidden', '0') === '1');

/** A file opened from the sidebar: tuck the sidebar away so the tab gets the room (☰ brings it back). */
function onFileOpened() {
  setSidebarHidden(true);
}

/** Show one sidebar tab ('storage' = Files, 'datasets'); remembered. */
function showSidebarTab(name) {
  for (const tab of document.querySelectorAll('.tabs .tab')) tab.setAttribute('aria-selected', String(tab.dataset.tab === name));
  element('storage-tab').hidden = name !== 'storage';
  element('datasets-tab').hidden = name !== 'datasets';
  remember('sidebarTab', name);
}
for (const tab of document.querySelectorAll('.tabs .tab')) tab.onclick = () => showSidebarTab(tab.dataset.tab);
showSidebarTab(recall('sidebarTab', 'storage'));

/** Hide or show the right column (note panel, mixer); remembered. */
function setRightColumnHidden(hidden) {
  document.body.classList.toggle('right-collapsed', hidden);
  element('right-column-toggle').setAttribute('aria-pressed', String(!hidden));
  remember('rightColumnHidden', hidden ? '1' : '0');
  if (api.score) api.render();  // the tab's width changed
}
element('right-column-toggle').onclick = () => setRightColumnHidden(!document.body.classList.contains('right-collapsed'));
document.body.classList.toggle('right-collapsed', recall('rightColumnHidden', '0') === '1');
element('right-column-toggle').setAttribute('aria-pressed', String(recall('rightColumnHidden', '0') !== '1'));

// Smallest side panel width (px), and the share of the window a side panel may take at most
const SIDE_PANEL_MIN_WIDTH = 200;
const SIDE_PANEL_MAX_SHARE = 0.6;

/**
 * Drag a side panel's inner edge to set its width (remembered); the tab is laid out again once the drag ends.
 *
 * Args:
 *   panel: The panel element.
 *   divider: The edge to drag (sits between the panel and the tab).
 *   grows: +1 if dragging right widens the panel (left sidebar), -1 if dragging left does (right column).
 *   name: Name the width is remembered under.
 */
function setupSideDivider(panel, divider, grows, name) {
  const setWidth = (width) => {
    panel.style.width = `${Math.min(Math.max(width, SIDE_PANEL_MIN_WIDTH), window.innerWidth * SIDE_PANEL_MAX_SHARE)}px`;
  };
  const remembered = recall(name, null);
  if (remembered) setWidth(Number(remembered));
  let startX = null;
  let startWidth = null;
  divider.onpointerdown = (event) => {
    divider.setPointerCapture(event.pointerId);
    divider.classList.add('dragging');
    startX = event.clientX;
    startWidth = panel.offsetWidth;
  };
  divider.onpointermove = (event) => { if (startX !== null) setWidth(startWidth + grows * (event.clientX - startX)); };
  divider.onpointerup = () => {
    if (startX === null) return;
    startX = null;
    divider.classList.remove('dragging');
    remember(name, String(panel.offsetWidth));
    if (api.score) api.render();
  };
  divider.onpointercancel = divider.onpointerup;
}
setupSideDivider(element('sidebar'), element('sidebar-divider'), 1, 'sidebarWidth');
setupSideDivider(element('right-column'), element('right-column-divider'), -1, 'rightColumnWidth');

/** Open or close the mixer drawer; remembered. */
function setMixerOpen(open) {
  document.body.classList.toggle('mixer-open', open);
  element('mixer-toggle').setAttribute('aria-pressed', String(open));
  remember('mixerOpen', open ? '1' : '0');
  if (api.score) api.render();  // the tab's width changed
}
element('mixer-toggle').onclick = () => {
  const opening = !document.body.classList.contains('mixer-open');
  // Opening the mixer brings a hidden right column back
  if (opening && document.body.classList.contains('right-collapsed')) setRightColumnHidden(false);
  setMixerOpen(opening);
};
setMixerOpen(recall('mixerOpen', '0') === '1');

// A clicked button gives focus back, so Space always means play / pause
document.addEventListener('click', (event) => event.target.closest('button')?.blur());

// The fretboard follows play / pause and the playback position. Kept last: alphaTab calls a new player
// listener at once, and updateFretboard needs everything above to exist by then. (No midiLoaded listener:
// in alphaTab 1.8.4 adding one loops forever in the worker player's loadedMidiInfo getter; the playing
// order is ready anyway once renderScore returns.)
api.playerStateChanged.on(() => fretboard.updateFretboard());
api.playerPositionChanged.on((event) => fretboard.updateFretboard(event.currentTick));

// ---- Start: empty note panel, the last catalogue picked ----

annotate.updatePanel();
updatePanels();
await fillCatalogueChoice(recall('catalogue', ''));
if (currentCatalogue()) await openCatalogue(currentCatalogue(), false);
