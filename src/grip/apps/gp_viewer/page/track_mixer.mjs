// GRIP GP viewer, track mixer: the open song's tracks, each with mute, solo and a volume slider, and
// its name to click to show that track. The sound settings apply in the song view only;
// realisation and annotation views play their one passage track at full volume.
// alphaTab keeps mute / solo / volume on the synthesizer's channels from one score to the next, so
// applyMixer first puts back every channel it changed before, then sets what the shown view needs.
// (Not alphaTab's resetChannelStates: that also wipes the transpositions it applies when loading a song.)
// viewer.mjs calls setupTrackMixer once.

// What viewer.mjs shares, set once by setupTrackMixer
let element, api, state, showTrack;

const FULL_VOLUME = { mute: false, solo: false, volume: 1 };
// Synthesizer channels the mixer has changed, to put back on the next applyMixer
const changedChannels = new Set();

/**
 * Take what viewer.mjs shares.
 *
 * Returns { updateMixer, applyMixer } for viewer.mjs to call after each drawing.
 */
export function setupTrackMixer(viewer) {
  ({ element, api, state, showTrack } = viewer);
  return { updateMixer, applyMixer };
}

/** The open song's sound settings for track `index`: { mute, solo, volume } (volume 0–1, 1 = the file's own level). */
function trackSetting(index) {
  state.song.mixer ??= new Map();
  if (!state.song.mixer.has(index)) state.song.mixer.set(index, { ...FULL_VOLUME });
  return state.song.mixer.get(index);
}

/** One row per track of the open song, the shown track highlighted; the panel hides while no song is open. */
function updateMixer() {
  element('mixer-panel').hidden = !state.song;
  element('mixer-rows').replaceChildren(...(state.song?.score.tracks ?? []).map(buildMixerRow));
}

/** A track's row: its name (click shows it), M (mute) and S (solo) toggles, and a volume slider. */
function buildMixerRow(track) {
  const setting = trackSetting(track.index);
  const row = document.createElement('div');
  row.className = 'mixer-row';
  if (state.view?.kind === 'song' && state.song.trackIndex === track.index) row.classList.add('shown');

  const name = Object.assign(document.createElement('button'), {
    className: 'mixer-track-name', textContent: `${track.index}: ${track.name}`, title: 'Show this track',
  });
  name.onclick = () => showTrack(track.index);
  const toggle = (label, field, title) => {
    const button = Object.assign(document.createElement('button'), { textContent: label, title });
    button.classList.toggle('on', setting[field]);
    button.onclick = () => {
      setting[field] = !setting[field];
      button.classList.toggle('on', setting[field]);
      applyMixer();
    };
    return button;
  };
  const volume = Object.assign(document.createElement('input'), {
    type: 'range', min: 0, max: 100, value: Math.round(setting.volume * 100), title: 'Volume',
  });
  volume.oninput = () => {
    setting.volume = Number(volume.value) / 100;
    applyMixer();
  };
  row.append(name, toggle('M', 'mute', 'Mute'), toggle('S', 'solo', 'Solo'), volume);
  return row;
}

/** One synthesizer channel's mute, solo and volume. */
function setChannel(player, channel, { mute, solo, volume }) {
  player.setChannelMute(channel, mute);
  player.setChannelSolo(channel, solo);
  player.setChannelVolume(channel, volume);
}

/** Put the shown view's sound settings on the synthesizer: the song's mixer in the song view, else full volume. */
function applyMixer() {
  const player = api.player;
  if (!player) return;
  for (const channel of changedChannels) setChannel(player, channel, FULL_VOLUME);
  changedChannels.clear();
  if (state.view?.kind !== 'song' || api.score !== state.song?.score) return;
  for (const track of api.score.tracks) {
    const setting = trackSetting(track.index);
    if (!setting.mute && !setting.solo && setting.volume === 1) continue;
    // Each track plays on two synthesizer channels (alphaTab's primary and secondary)
    for (const channel of [track.playbackInfo.primaryChannel, track.playbackInfo.secondaryChannel]) {
      setChannel(player, channel, setting);
      changedChannels.add(channel);
    }
  }
}
