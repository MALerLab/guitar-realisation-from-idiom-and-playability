// GRIP viewer, junk flags: the flag box for one track (or every track of a song) with an optional note, used
// from the song view's top bar and the catalogue player. "Junk" is the one catch-all reason: anything that
// doesn't look right. Flags are saved on the server (one file on storage) for the corpus clean-up to read.
// viewer.mjs calls setupFlags once at start.

// What viewer.mjs shares, set once by setupFlags
let element, postJson;
// The box's current target: { gpPath, trackIndex, trackCount (null = no "every track" choice), title, onChange }
let target = null;

/**
 * Take what viewer.mjs shares and wire the flag box's buttons.
 *
 * Returns { openFlagBox, fetchSongFlags, showFlagButton }.
 */
export function setupFlags(viewer) {
  ({ element, postJson } = viewer);
  element('flag-cancel').onclick = () => element('flag-dialog').close();
  element('flag-remove').onclick = () => saveFlag(false);
  element('flag-form').onsubmit = (event) => {
    event.preventDefault();
    saveFlag(true);
  };
  return { openFlagBox, fetchSongFlags, showFlagButton };
}

/** The flagged tracks of one GP file: { track index (as text) → flag }; {} if the server can't say. */
async function fetchSongFlags(gpPath) {
  const response = await fetch(`/api/flags?${new URLSearchParams({ gp_path: gpPath })}`);
  return response.ok ? (await response.json()).tracks : {};
}

/** Show a junk button's state: red "⚑ Junk ✓" when its track is flagged (the note in its tooltip). */
function showFlagButton(button, flag, enabled) {
  button.disabled = !enabled;
  button.classList.toggle('flagged', Boolean(flag));
  button.textContent = flag ? '⚑ Junk ✓' : '⚑ Junk';
  button.title = flag ? `Flagged as junk${flag.note ? `: ${flag.note}` : ''} (click to change or remove)` : 'Flag this track as junk (corpus clean-up)';
}

/**
 * Open the flag box for one track.
 *
 * Args:
 *   flagTarget: { gpPath, trackIndex, trackCount (number of tracks for the "every track" choice, or null),
 *     title (shown in the box), onChange (called with the file's flags after a save) }.
 */
async function openFlagBox(flagTarget) {
  target = flagTarget;
  const flags = await fetchSongFlags(target.gpPath);
  const current = flags[String(target.trackIndex)];
  element('flag-target').textContent = target.title;
  element('flag-note').value = current?.note ?? '';
  element('flag-all').checked = false;
  element('flag-all-label').hidden = !target.trackCount || target.trackCount < 2;
  element('flag-remove').disabled = !current && !Object.keys(flags).length;
  element('flag-confirm').textContent = current ? 'Update flag' : 'Flag as junk';
  element('flag-error').textContent = '';
  element('flag-dialog').showModal();
  element('flag-note').focus();
}

/** Flag (or clear) the target track, or every track of its song when ticked. */
async function saveFlag(flag) {
  const tracks = element('flag-all').checked
    ? Array.from({ length: target.trackCount }, (_, index) => index)
    : [target.trackIndex];
  const reply = await postJson('/api/flags', { gp_path: target.gpPath, tracks, flag, note: element('flag-note').value.trim() });
  if (reply.error) {
    element('flag-error').textContent = reply.error;
    return;
  }
  element('flag-dialog').close();
  target.onChange?.(reply.tracks);
}
