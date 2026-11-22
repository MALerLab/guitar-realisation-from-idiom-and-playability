// The Optimiser view: an lr-knob sweep on the bars picked in the song view, shown as
//   - the lr-knob: a log-scale slider, one band per segment (stretch of the lr-knob with one cheapest tab), a tick at
//     every flip; dragging only swaps between the sweep's segments, nothing reruns
//   - the cost strip under the tab: what each hand paid per note for the tab shown (picking terms × the lr-knob)
//   - the knob form (right column), built from whatever the cost config holds: edits are overrides sent with
//     the next lr-knob sweep (never written to configs/), and can be saved as a preset
// Keys: , / . (also < / >) = previous / next tab along the lr-knob (same spots on Colemak-DH).

let element, statusLabel, api, state, postJson, renderView, readSettings, sameSettings, currentMode, catalogueRequest;
let drawSelectionBoxes, updateFretboard;

// The slider's resolution (positions from one end of the lr-knob to the other)
const SLIDER_STEPS = 1000;
// Knob edits wait this long for more edits before the lr-knob sweep reruns
const KNOB_RERUN_DELAY_MS = 400;
// Cost strip: height (px), the width kept for its row labels (px), and the share of note costs that fit
// unclipped (a few dear notes, e.g. the no-go zone, would otherwise flatten the rest)
const STRIP_HEIGHT = 96;
const STRIP_LABEL_WIDTH = 92;
const STRIP_UNCLIPPED_SHARE = 0.95;
const SVG_NAMESPACE = 'http://www.w3.org/2000/svg';

let knobs = null;          // the cost config: { path, version, values }
let overrides = {};        // edited knobs, nested like the cost config
let newestSweep = 0;       // id of the newest lr-knob sweep asked for: replies to older ones are dropped
let rerunTimer = null;
let emptyText = 'Pick bars in the Song view (drag over them), then come back here.';
let stripScale = 1;        // the cost that fills half the strip's height (fixed per lr-knob sweep)
let hoveredNote = null;

/** Wire up the lr-knob, the cost strip and the knob form. Returns what viewer.mjs calls. */
export function setupOptimiserView(viewer) {
  ({ element, statusLabel, api, state, postJson, renderView, readSettings, sameSettings, currentMode, catalogueRequest } = viewer);
  ({ drawSelectionBoxes, updateFretboard } = viewer);
  element('lr-knob-slider').oninput = () => setLrKnob(fractionToLrKnob(Number(element('lr-knob-slider').value) / SLIDER_STEPS));
  // Let go of the slider: focus goes back, so Space plays and , / . step again
  element('lr-knob-slider').onchange = () => element('lr-knob-slider').blur();
  element('lr-knob-reset').onclick = () => setLrKnob(state.sweep.lr_knob.default);
  setupStepKeys();
  setupCostStrip();
  loadKnobs();
  return { enter, viewOfSweep, updateView, segmentLabel, emptyHint };
}

// ---- The lr-knob sweep ----

/** Entering the view: run an lr-knob sweep on the Bars box's passage, unless the last one was on the same bars with
 *  the same catalogue (the chooser's). */
async function enter() {
  const settings = readSettings();  // says in the status line what's missing
  if (!settings) {
    emptyText = 'Pick bars in the Song view (drag over them), then come back here.';
    renderView();
    return;
  }
  if (state.sweep && sameSettings(state.sweep.settings, settings)
      && state.sweep.catalogueKey === JSON.stringify(catalogueRequest())) return;
  await runSweep(settings);
}

/** The view to show for the current lr-knob sweep (null = none yet). */
function viewOfSweep() {
  return state.sweep ? { kind: 'segment', index: segmentIndexAt(state.sweep.lrKnobValue) } : null;
}

/**
 * Ask the server for an lr-knob sweep of the passage with the knob edits, and show it. A rerun on the same
 * passage keeps the tab on screen until the answer comes and keeps the lr-knob where it was.
 */
async function runSweep(settings) {
  const sweepId = ++newestSweep;
  const samePassage = state.sweep && sameSettings(state.sweep.settings, settings);
  if (!samePassage) {
    emptyText = 'Running the lr-knob sweep…';
    state.sweep = null;
    if (currentMode() === 'optimiser') {
      state.view = null;
      renderView();
    }
  }
  const started = Date.now();
  const seconds = () => Math.round((Date.now() - started) / 100) / 10;
  const timer = setInterval(() => { statusLabel.textContent = `lr-knob sweep… ${Math.round(seconds())} s`; }, 500);
  const catalogue = catalogueRequest();
  const reply = await postJson('/api/optimiser/lr_knob_sweep', { ...settings, ...catalogue, overrides: structuredClone(overrides) });
  clearInterval(timer);
  if (sweepId !== newestSweep) return;
  if (reply.error) {
    statusLabel.textContent = `Lr-knob sweep failed: ${reply.error}`;
    emptyText = `The lr-knob sweep failed: ${reply.error}`;
    if (currentMode() === 'optimiser') renderView(true);
    return;
  }
  const tabs = reply.segments.length;
  emptyText = 'Pick bars in the Song view (drag over them), then come back here.';
  statusLabel.textContent = `lr-knob sweep: ${tabs} tab${tabs === 1 ? '' : 's'} in ${seconds()} s`;
  const lrKnobValue = samePassage ? clamp(state.sweep.lrKnobValue, reply.lr_knob.min, reply.lr_knob.max) : reply.lr_knob.default;
  state.sweep = { ...reply, settings, lrKnobValue, catalogueKey: JSON.stringify(catalogue) };
  stripScale = costStripScale();
  if (currentMode() !== 'optimiser') return;
  state.view = viewOfSweep();
  if (!samePassage) state.selection = [];
  api.stop();
  renderView(samePassage);
}

/** The empty tab's hint in this view. */
function emptyHint() {
  return emptyText;
}

/** The tab's name over the score: which tab along the lr-knob, and its stretch of the lr-knob. */
function segmentLabel(index) {
  const { segments } = state.sweep;
  const segment = segments[index];
  return `lr-knob sweep · tab ${index + 1} of ${segments.length} · lr-knob ${formatLrKnob(segment.lr_knob_from)}–${formatLrKnob(segment.lr_knob_to)}`;
}

/** Redraw the lr-knob and the cost strip (viewer.mjs calls this after every drawing). */
function updateView() {
  drawLrKnob();
  drawCostStrip();
}

// ---- The lr-knob ----

/** Where an lr-knob value sits along the slider, 0 → 1 (log or linear, as the sweep says). */
function lrKnobToFraction(value) {
  const { min, max, scale } = state.sweep.lr_knob;
  return scale === 'log' ? Math.log(value / min) / Math.log(max / min) : (value - min) / (max - min);
}

/** The lr-knob value at a point along the slider, 0 → 1. */
function fractionToLrKnob(fraction) {
  const { min, max, scale } = state.sweep.lr_knob;
  return scale === 'log' ? min * (max / min) ** fraction : min + fraction * (max - min);
}

/** The segment holding an lr-knob value: the first one if it sits on a flip. */
function segmentIndexAt(value) {
  const index = state.sweep.segments.findIndex((segment) => value <= segment.lr_knob_to);
  return index === -1 ? state.sweep.segments.length - 1 : index;
}

function clamp(value, low, high) {
  return Math.min(Math.max(value, low), high);
}

function formatLrKnob(value) {
  return value >= 10 ? value.toFixed(1) : value.toFixed(2);
}

/** Turn the lr-knob: a new segment draws its tab; inside the same segment only the cost strip changes. */
function setLrKnob(value) {
  if (!state.sweep || state.view?.kind !== 'segment') return;
  state.sweep.lrKnobValue = clamp(value, state.sweep.lr_knob.min, state.sweep.lr_knob.max);
  const index = segmentIndexAt(state.sweep.lrKnobValue);
  if (index !== state.view.index) {
    state.view = { kind: 'segment', index };
    renderView(true);  // the same passage: selection and scroll stay
  } else {
    updateView();
  }
}

/** Jump to the middle of the previous / next segment along the lr-knob. */
function stepSegment(step) {
  const index = state.view.index + step;
  const segment = state.sweep.segments[index];
  if (!segment) return;
  setLrKnob(fractionToLrKnob((lrKnobToFraction(segment.lr_knob_from) + lrKnobToFraction(segment.lr_knob_to)) / 2));
}

/** , / . (and < / >): previous / next tab along the lr-knob, in the Optimiser view while a tab shows. */
function setupStepKeys() {
  document.addEventListener('keydown', (event) => {
    if (event.target.closest('input, select, textarea, dialog') || event.ctrlKey || event.metaKey || event.altKey) return;
    const step = { ',': -1, '<': -1, '.': 1, '>': 1 }[event.key];
    if (!step || currentMode() !== 'optimiser' || state.view?.kind !== 'segment') return;
    event.preventDefault();
    stepSegment(step);
  });
}

/** The lr-knob bar: one band per segment (the shown one lit), a tick at every flip, the default's mark, the readout. */
function drawLrKnob() {
  const sweep = state.sweep;
  const live = Boolean(sweep) && state.view?.kind === 'segment';
  element('lr-knob-slider').disabled = !live;
  element('lr-knob-reset').disabled = !live;
  if (!sweep) {
    element('lr-knob-segments').replaceChildren();
    element('lr-knob-value').textContent = '';
    element('lr-knob-tab-count').textContent = emptyText === 'Running the lr-knob sweep…' ? 'running the lr-knob sweep…' : '';
    element('lr-knob-default-mark').hidden = true;
    return;
  }
  const shown = segmentIndexAt(sweep.lrKnobValue);
  const bands = sweep.segments.map((segment, index) => {
    const band = document.createElement('div');
    band.className = 'lr-knob-band';
    band.classList.toggle('odd', index % 2 === 1);
    band.classList.toggle('current', index === shown);
    band.classList.toggle('flip', index > 0);
    const left = lrKnobToFraction(segment.lr_knob_from);
    Object.assign(band.style, { left: `${100 * left}%`, width: `${100 * (lrKnobToFraction(segment.lr_knob_to) - left)}%` });
    band.title = `tab ${index + 1}: lr-knob ${formatLrKnob(segment.lr_knob_from)}–${formatLrKnob(segment.lr_knob_to)}`;
    return band;
  });
  element('lr-knob-segments').replaceChildren(...bands);
  const mark = element('lr-knob-default-mark');
  mark.hidden = false;
  mark.style.left = `calc(8px + (100% - 16px) * ${lrKnobToFraction(sweep.lr_knob.default)})`;
  element('lr-knob-slider').value = String(Math.round(SLIDER_STEPS * lrKnobToFraction(sweep.lrKnobValue)));
  element('lr-knob-value').textContent = `lr-knob ${formatLrKnob(sweep.lrKnobValue)}`;
  const tabs = sweep.segments.length;
  element('lr-knob-tab-count').textContent = `tab ${shown + 1} of ${tabs} (${tabs} distinct tab${tabs === 1 ? '' : 's'})`;
}

// ---- The cost strip ----

/** Each passage note's cost on one realisation, split by hand, + the chooser's glue (null for a note with no move:
 *  chords, rests). */
function noteCosts(realisation) {
  const moveOfNote = new Map(realisation.moves.map((move) => [move.passage_index, move]));
  return state.sweep.passage.notes.map((_, index) => {
    const move = moveOfNote.get(index);
    if (!move) return null;
    const cost = { fretting: 0, picking: 0, glue: 0, terms: move.terms };
    // Glue belongs to neither hand; a term the sweep doesn't tag counts for the fretting hand (it tags every term it charges)
    for (const [term, value] of Object.entries(move.terms)) {
      const group = state.sweep.terms[term];
      cost[group === 'picking' || group === 'glue' ? group : 'fretting'] += value;
    }
    return cost;
  });
}

/** The cost that fills half the strip: fixed per lr-knob sweep, so turning the lr-knob keeps the bars comparable. */
function costStripScale() {
  const values = [];
  for (const segment of state.sweep.segments) {
    const middleLrKnob = fractionToLrKnob((lrKnobToFraction(segment.lr_knob_from) + lrKnobToFraction(segment.lr_knob_to)) / 2);
    for (const cost of noteCosts(segment.realisation)) {
      if (cost?.fretting > 0) values.push(cost.fretting);
      if (cost?.picking > 0) values.push(cost.picking * middleLrKnob);
    }
  }
  if (!values.length) return 1;
  values.sort((a, b) => a - b);
  return values[Math.min(values.length - 1, Math.floor(STRIP_UNCLIPPED_SHARE * values.length))];
}

function svgElement(tag, attributes, text = null) {
  const node = document.createElementNS(SVG_NAMESPACE, tag);
  for (const [name, value] of Object.entries(attributes)) node.setAttribute(name, value);
  if (text !== null) node.textContent = text;
  return node;
}

/** The totals line and one column per passage note: fretting cost up, picking cost × lr-knob down. */
function drawCostStrip() {
  const chart = element('cost-strip-chart');
  if (currentMode() !== 'optimiser' || state.view?.kind !== 'segment') {
    chart.replaceChildren();
    element('cost-strip-totals').replaceChildren();
    return;
  }
  const lrKnob = state.sweep.lrKnobValue;
  const costs = noteCosts(state.sweep.segments[state.view.index].realisation);
  drawTotals(costs, lrKnob);

  const width = chart.clientWidth || 600;
  const middle = STRIP_HEIGHT / 2;
  const reach = middle - 4;
  const columnWidth = (width - STRIP_LABEL_WIDTH) / Math.max(costs.length, 1);
  const svg = svgElement('svg', { viewBox: `0 0 ${width} ${STRIP_HEIGHT}`, width, height: STRIP_HEIGHT });
  svg.append(
    svgElement('text', { x: 0, y: middle - 8, class: 'strip-label fretting' }, 'fretting'),
    svgElement('text', { x: 0, y: middle + 16, class: 'strip-label picking' }, 'picking × lr-knob'),
    svgElement('line', { x1: STRIP_LABEL_WIDTH, x2: width, y1: middle, y2: middle, class: 'strip-axis' }),
  );
  costs.forEach((cost, index) => {
    const x = STRIP_LABEL_WIDTH + index * columnWidth;
    const selected = state.selection.includes(index);
    const column = svgElement('rect', {
      x, y: 0, width: columnWidth, height: STRIP_HEIGHT,
      class: `strip-column${selected ? ' selected' : ''}${index === hoveredNote ? ' hovered' : ''}`,
    });
    column.dataset.note = String(index);
    svg.append(column);
    if (!cost) return;
    const barWidth = Math.max(columnWidth * 0.7, 1);
    const barX = x + (columnWidth - barWidth) / 2;
    for (const [hand, value, direction] of [['fretting', cost.fretting, -1], ['picking', cost.picking * lrKnob, 1]]) {
      if (value <= 0) continue;
      const height = Math.max(reach * Math.min(value / stripScale, 1), 1);
      const y = direction < 0 ? middle - height : middle;
      svg.append(svgElement('rect', { x: barX, y, width: barWidth, height, class: `strip-bar ${hand}` }));
      // Past the scale: a mark on the clipped end
      if (value > stripScale) {
        const tipY = direction < 0 ? middle - reach - 2 : middle + reach + 2;
        svg.append(svgElement('rect', { x: barX, y: tipY - 1.5, width: barWidth, height: 3, class: 'strip-clipped' }));
      }
    }
  });
  chart.replaceChildren(svg);
}

/** The line over the strip: each hand's total for the tab shown, the chooser's glue, and the total the lr-knob
 *  makes of them (the strip's bars show the hands only). */
function drawTotals(costs, lrKnob) {
  const fretting = costs.reduce((sum, cost) => sum + (cost?.fretting ?? 0), 0);
  const picking = costs.reduce((sum, cost) => sum + (cost?.picking ?? 0), 0);
  const glue = costs.reduce((sum, cost) => sum + (cost?.glue ?? 0), 0);
  const part = (text, className = null) => {
    const span = document.createElement('span');
    span.textContent = text;
    if (className) span.className = className;
    return span;
  };
  element('cost-strip-totals').replaceChildren(
    part('fretting '), part(fretting.toFixed(2), 'fretting'),
    part(` · picking ${picking.toFixed(2)} × lr-knob ${formatLrKnob(lrKnob)} = `), part((picking * lrKnob).toFixed(2), 'picking'),
    ...(state.sweep.chooser ? [part(` · glue ${glue.toFixed(2)} (${(state.sweep.segments[state.view.index].realisation.blocks ?? []).length} entries)`)] : []),
    part(` · total ${(fretting + picking * lrKnob + glue).toFixed(2)}`),
    part('   hover a note for its costs, click to mark it on the tab', 'hint'),
  );
}

/** One note's costs, term by term (only the terms it paid), for the strip's hover box. */
function noteCostText(index) {
  const realisation = state.sweep.segments[state.view.index].realisation;
  const cost = noteCosts(realisation)[index];
  const choice = realisation.choices[index];
  const where = choice?.string === null || choice?.string === undefined ? 'no position' : `string ${choice.string} fret ${choice.fret}`;
  const lines = [`note ${index + 1} · ${where}${choice?.finger ? ` · finger ${choice.finger}` : ''}`];
  if (!cost) return [...lines, 'no cost (not a single picked or fretted note)'].join('\n');
  const paid = (hand) => Object.entries(cost.terms)
    .filter(([term, value]) => value !== 0 && state.sweep.terms[term] !== 'glue'
      && (state.sweep.terms[term] === 'picking') === (hand === 'picking'))
    .map(([term, value]) => `${term} ${value.toFixed(2)}`).join(', ') || 'nothing';
  const lrKnob = state.sweep.lrKnobValue;
  lines.push(`fretting ${cost.fretting.toFixed(2)}: ${paid('fretting')}`);
  lines.push(`picking ${cost.picking.toFixed(2)} × lr-knob ${formatLrKnob(lrKnob)} = ${(cost.picking * lrKnob).toFixed(2)}: ${paid('picking')}`);
  if (cost.glue > 0) lines.push(`glue ${cost.glue.toFixed(2)}: a cut between catalogue entries`);
  return lines.join('\n');
}

/** Hover a column: its costs in a box; click it: mark the note on the tab and the fretboard. */
function setupCostStrip() {
  const chart = element('cost-strip-chart');
  const tip = element('cost-strip-tip');
  const noteUnder = (event) => {
    const note = event.target.closest?.('.strip-column')?.dataset.note;
    return note === undefined ? null : Number(note);
  };
  chart.addEventListener('pointermove', (event) => {
    const index = noteUnder(event);
    if (index !== hoveredNote) {
      hoveredNote = index;
      drawCostStrip();
    }
    tip.hidden = index === null;
    if (index === null) return;
    tip.textContent = noteCostText(index);
    const strip = element('cost-strip').getBoundingClientRect();
    const left = Math.min(event.clientX - strip.left + 12, strip.width - tip.offsetWidth - 4);
    Object.assign(tip.style, { left: `${Math.max(left, 4)}px`, bottom: `${strip.bottom - event.clientY + 12}px` });
  });
  chart.addEventListener('pointerleave', () => {
    hoveredNote = null;
    tip.hidden = true;
    drawCostStrip();
  });
  chart.addEventListener('click', (event) => {
    const index = noteUnder(event);
    if (index === null) return;
    state.selection = [index];
    drawSelectionBoxes();
    updateFretboard();
    drawCostStrip();
  });
  // Notes picked on the tab (click, drag, arrow keys) light up in the strip too
  api.beatMouseUp.on(() => { if (currentMode() === 'optimiser') drawCostStrip(); });
  document.addEventListener('keyup', () => { if (currentMode() === 'optimiser') drawCostStrip(); });
  new ResizeObserver(() => drawCostStrip()).observe(chart);
}

// ---- The knob form ----

/** Fetch the cost config and build the form from it. */
async function loadKnobs() {
  const response = await fetch('/api/optimiser/cost_knobs');
  const reply = await response.json().catch(() => ({}));
  if (!response.ok) {
    element('knob-panel').textContent = `Cost knobs unavailable: ${reply.error ?? response.statusText}`;
    return;
  }
  knobs = reply;
  buildKnobPanel();
}

/** A knob's value in a nested config (undefined if missing). */
function valueAt(values, path) {
  return path.reduce((node, name) => node?.[name], values);
}

/** Put a knob edit in the overrides, or take it out (undefined); empty groups go too. */
function setOverride(path, value) {
  const parents = [overrides];
  for (const name of path.slice(0, -1)) parents.push(parents.at(-1)[name] ??= {});
  const last = path.at(-1);
  if (value === undefined) delete parents.at(-1)[last];
  else parents.at(-1)[last] = value;
  for (let depth = path.length - 1; depth > 0; depth -= 1) {
    if (Object.keys(parents[depth]).length === 0) delete parents[depth - 1][path[depth - 1]];
  }
}

/** The form: a group per section of the cost config, a row per number or switch (text values are left out). */
function buildKnobPanel() {
  const panel = element('knob-panel');
  const title = document.createElement('h2');
  title.textContent = 'Cost knobs';
  const source = document.createElement('p');
  source.className = 'hint';
  source.textContent = `${knobs.path} · an edit reruns the lr-knob sweep; configs/ never changes`;
  const tree = document.createElement('div');
  tree.className = 'knob-tree';
  tree.append(...knobRows(knobs.values, []));

  const resetAll = document.createElement('button');
  resetAll.id = 'knob-reset-all';
  resetAll.textContent = 'Reset all';
  resetAll.onclick = () => {
    overrides = {};
    for (const row of tree.querySelectorAll('.knob-row')) row.resetKnob(false);
    knobsChanged();
  };
  const presetTitle = document.createElement('h3');
  presetTitle.textContent = 'Save the edits as a preset';
  const presetRow = document.createElement('form');
  presetRow.className = 'preset-row';
  presetRow.innerHTML = '<input type="text" id="preset-name" placeholder="preset name" spellcheck="false">'
    + '<button type="submit" id="preset-save">Save</button>';
  presetRow.onsubmit = (event) => {
    event.preventDefault();
    savePreset();
  };
  const presetStatus = document.createElement('p');
  presetStatus.id = 'preset-status';
  presetStatus.className = 'hint';
  panel.replaceChildren(title, source, tree, resetAll, presetTitle, presetRow, presetStatus);
  updateKnobButtons();
}

/** Rows for one group of the cost config: nested groups, then number / switch knobs. */
function knobRows(values, path) {
  const rows = [];
  for (const [name, value] of Object.entries(values)) {
    const knobPath = [...path, name];
    if (value && typeof value === 'object' && !Array.isArray(value)) {
      const group = document.createElement('div');
      group.className = 'knob-group';
      const heading = document.createElement('div');
      heading.className = 'knob-group-name';
      heading.textContent = name;
      const children = document.createElement('div');
      children.className = 'knob-children';
      children.append(...knobRows(value, knobPath));
      group.append(heading, children);
      if (children.childElementCount) rows.push(group);
    } else if (typeof value === 'number' || typeof value === 'boolean') {
      rows.push(knobRow(name, value, knobPath));
    }
  }
  return rows;
}

/** One knob: its name, an input (number box or switch), and a reset button while it differs from the config. */
function knobRow(name, configValue, path) {
  const row = document.createElement('div');
  row.className = 'knob-row';
  const label = document.createElement('label');
  label.textContent = name;
  label.title = `${path.join('.')} (config: ${configValue})`;
  const input = document.createElement('input');
  const isSwitch = typeof configValue === 'boolean';
  input.type = isSwitch ? 'checkbox' : 'number';
  if (!isSwitch) input.step = 'any';
  label.htmlFor = input.id = `knob-${path.join('-')}`;
  const reset = document.createElement('button');
  reset.type = 'button';
  reset.className = 'knob-reset';
  reset.textContent = '↺';
  reset.title = `back to the config's ${configValue}`;

  const show = (value) => {
    if (isSwitch) input.checked = value;
    else input.value = String(value);
    row.classList.toggle('edited', value !== configValue);
  };
  row.resetKnob = (rerun = true) => {
    show(configValue);
    setOverride(path, undefined);
    if (rerun) knobsChanged();
  };
  input.onchange = () => {
    let value = input.checked;
    if (!isSwitch) {
      value = input.value.trim() === '' ? configValue : Number(input.value);
      if (!Number.isFinite(value)) {
        statusLabel.textContent = `${path.join('.')}: not a number`;
        value = valueAt(overrides, path) ?? configValue;
      }
    }
    show(value);
    setOverride(path, value === configValue ? undefined : value);
    knobsChanged();
  };
  reset.onclick = () => row.resetKnob();
  show(configValue);
  row.append(label, input, reset);
  return row;
}

/** After any knob edit: the buttons follow, and the lr-knob sweep reruns once the edits settle. */
function knobsChanged() {
  updateKnobButtons();
  clearTimeout(rerunTimer);
  rerunTimer = setTimeout(() => {
    const settings = state.sweep?.settings ?? (currentMode() === 'optimiser' ? readSettings() : null);
    if (settings) runSweep(settings);
  }, KNOB_RERUN_DELAY_MS);
}

function updateKnobButtons() {
  const edited = Object.keys(overrides).length > 0;
  element('knob-reset-all').disabled = !edited;
  element('preset-save').disabled = !edited;
}

/** Save the knob edits under the typed name (the server never overwrites a preset). */
async function savePreset() {
  const status = element('preset-status');
  const reply = await postJson('/api/optimiser/preset', { name: element('preset-name').value.trim(), overrides });
  status.classList.toggle('warning', Boolean(reply.error));
  status.textContent = reply.error ? reply.error : `saved: ${reply.path}`;
}
