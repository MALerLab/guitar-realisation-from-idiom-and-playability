// Read one Guitar Pro file with alphaTab and print one JSON line per staff: track-level facts only, no notes.
// Values are alphaTab's own, unconverted (tuning highest string first, capo as stored in the file).
// Used by src/grip/data/build_gp_manifest.py; the note-level counterpart is src/grip/formats/alphatab_dump.mjs.
//
// Usage: node src/grip/data/alphatab_track_metadata_dump.mjs <path-to-gp-file> <text-encoding>
// <text-encoding>: how stored text (track names) is decoded, e.g. windows-1252; gp3–5 files do not record it
import fs from 'node:fs';
import * as alphaTab from '@coderline/alphatab';

const model = alphaTab.model;

/**
 * True if the note starts a sound, i.e. is not the continuation of a tie.
 * Matches gp_parser.py: a tie with no origin note ("dangling") still counts as a note.
 */
function isPlayedNote(note) {
    return !(note.isTieDestination && note.tieOrigin);
}

/**
 * Count played notes, and beats with a marked pick stroke, over every bar and voice of one staff.
 */
function countStaffEvents(staff) {
    let noteCount = 0;
    let pickStrokeBeatCount = 0;
    for (const bar of staff.bars) {
        for (const voice of bar.voices) {
            for (const beat of voice.beats) {
                noteCount += beat.notes.filter(isPlayedNote).length;
                // A beat (e.g. a strummed chord) counts once, however many notes it holds
                if (beat.notes.length > 0 && beat.pickStroke !== model.PickStroke.None) {
                    pickStrokeBeatCount += 1;
                }
            }
        }
    }
    return { noteCount, pickStrokeBeatCount };
}

// Load the score; alphaTab picks the right importer (gp3–5, gpx, gp) from the bytes
const [inputPath, textEncoding] = process.argv.slice(2);
const settings = new alphaTab.Settings();
settings.importer.encoding = textEncoding;
const score = alphaTab.importer.ScoreLoader.loadScoreFromBytes(new Uint8Array(fs.readFileSync(inputPath)), settings);

// One output line per staff, including staves with no notes
const lines = [];
for (const track of score.tracks) {
    for (const staff of track.staves) {
        lines.push(
            JSON.stringify({
                trackIndex: track.index,
                staffIndex: staff.index,
                trackName: track.name,
                midiProgram: track.playbackInfo.program,
                isPercussion: staff.isPercussion,
                tuning: staff.tuning,
                capo: staff.capo,
                ...countStaffEvents(staff),
            })
        );
    }
}
process.stdout.write(lines.length > 0 ? lines.join('\n') + '\n' : '');
