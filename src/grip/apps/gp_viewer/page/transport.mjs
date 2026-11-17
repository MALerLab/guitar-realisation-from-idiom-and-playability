// GRIP viewer, transport bar: back to start, bar back / forward, play / pause, stop, the scrub bar,
// bar + time position, loop and speed (both remembered in this browser), and their keys:
//   Space = play / pause · Home = back to the start · [ / ] = bar back / forward
// (Colemak-DH keeps these keys where QWERTY has them.) viewer.mjs calls setupTransport once.
import * as alphaTab from '/alphatab/alphaTab.mjs';

// What viewer.mjs shares, set once by setupTransport
let element, api, remember, recall;
// Within this many ticks of a bar's start, "bar back" goes to the bar before (a quarter note = 960 ticks)
const BAR_BACK_GRACE_TICKS = 480;
let scrubbing = false;

/**
 * Take what viewer.mjs shares and wire the buttons, scrub bar, loop, speed and keys.
 *
 * Returns { resetPosition } for viewer.mjs to call after drawing something new.
 */
export function setupTransport(viewer) {
  ({ element, api, remember, recall } = viewer);

  // Buttons
  element('play-pause').onclick = () => api.playPause();
  element('stop').onclick = () => api.stop();
  element('to-start').onclick = goToStart;
  element('bar-back').onclick = () => stepBar(-1);
  element('bar-forward').onclick = () => stepBar(1);

  // Loop + speed, remembered
  setLooping(recall('looping', '0') === '1');
  element('loop-toggle').onclick = () => setLooping(!api.isLooping);
  element('speed-select').value = recall('speed', '1');
  api.playbackSpeed = Number(element('speed-select').value);
  element('speed-select').onchange = (event) => {
    api.playbackSpeed = Number(event.target.value);
    remember('speed', event.target.value);
    event.target.blur();
  };

  // Scrub bar: shows where playback is; dragging it moves there on release
  const scrub = element('scrub');
  scrub.oninput = () => { scrubbing = true; showPosition(Number(scrub.value), Number(scrub.max)); };
  scrub.onchange = () => { api.tickPosition = Number(scrub.value); scrubbing = false; scrub.blur(); };

  // alphaTab events
  api.playerReady.on(() => {
    for (const id of ['play-pause', 'stop', 'to-start', 'bar-back', 'bar-forward', 'scrub']) element(id).disabled = false;
  });
  api.playerStateChanged.on((event) => {
    const playing = event.state === alphaTab.synth.PlayerState.Playing;
    element('play-pause').querySelector('use').setAttribute('href', playing ? '#icon-pause' : '#icon-play');
  });
  api.playerPositionChanged.on((event) => {
    if (scrubbing) return;
    scrub.max = Math.max(1, event.endTick);
    scrub.value = event.currentTick;
    showPosition(event.currentTick, event.endTick, event.currentTime, event.endTime);
  });

  setupKeys();
  return { resetPosition };
}

/** Show the start of whatever was just drawn. */
function resetPosition() {
  element('scrub').value = 0;
  showPosition(0, Number(element('scrub').max));
}

/** Loop on or off (loops the dragged bars if any, else everything). */
function setLooping(looping) {
  api.isLooping = looping;
  element('loop-toggle').setAttribute('aria-pressed', String(looping));
  remember('looping', looping ? '1' : '0');
}

/** Bar lookups in playback ticks (repeats played out), or [] before anything is drawn. */
function masterBarLookups() {
  return api.tickCache?.masterBars ?? [];
}

/** Index into masterBarLookups() of the bar playing at `tick`. */
function barAt(tick) {
  const bars = masterBarLookups();
  let low = 0;
  let high = bars.length - 1;
  while (low < high) {
    const middle = Math.ceil((low + high) / 2);
    if (bars[middle].start <= tick) low = middle;
    else high = middle - 1;
  }
  return low;
}

/** Back to the start of the dragged range, or of the piece. */
function goToStart() {
  api.tickPosition = api.playbackRange?.startTick ?? 0;
}

/** Move one bar back (to this bar's start first, unless already right at it) or forward. */
function stepBar(step) {
  const bars = masterBarLookups();
  if (!bars.length) return;
  const tick = api.tickPosition;
  let index = barAt(tick);
  if (step > 0) index = Math.min(index + 1, bars.length - 1);
  else if (tick - bars[index].start < BAR_BACK_GRACE_TICKS) index = Math.max(index - 1, 0);
  api.tickPosition = bars[index].start;
}

/** "bar 12 / 48" and "0:31 / 2:10" (time only while playing tells it; otherwise kept). */
function showPosition(tick, endTick, milliseconds = null, endMilliseconds = null) {
  const bars = masterBarLookups();
  if (bars.length) {
    const lastBarNumber = bars[bars.length - 1].masterBar.index + 1;
    element('position-label').textContent = `bar ${bars[barAt(tick)].masterBar.index + 1} / ${lastBarNumber}`;
  } else {
    element('position-label').textContent = 'bar – / –';
  }
  if (milliseconds !== null) element('time-label').textContent = `${clock(milliseconds)} / ${clock(endMilliseconds)}`;
}

/** Milliseconds as m:ss. */
function clock(milliseconds) {
  const seconds = Math.max(0, Math.floor(milliseconds / 1000));
  return `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, '0')}`;
}

/** Space, Home, [ and ]; ignored while typing in a box or with a modifier held. */
function setupKeys() {
  document.addEventListener('keydown', (event) => {
    if (event.target.closest('input, select, textarea, dialog') || event.ctrlKey || event.metaKey || event.altKey) return;
    const actions = { ' ': () => api.playPause(), Home: goToStart, '[': () => stepBar(-1), ']': () => stepBar(1) };
    const action = actions[event.key];
    if (!action || element('play-pause').disabled) return;
    event.preventDefault();  // Space would also scroll the page or press the focused button
    action();
  });
}
