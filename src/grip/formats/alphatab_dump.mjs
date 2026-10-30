// Read one Guitar Pro file with alphaTab and print one JSON line per note to stdout.
// Values are alphaTab's own, unconverted (string 1 = lowest, enums by name, capo-inclusive pitch).
// Every line also carries the song's starting tempo (bpm), the same on every line.
// All project conventions are applied on the Python side, in src/grip/formats/gp_parser.py.
//
// Usage: node src/grip/formats/alphatab_dump.mjs <path-to-gp-file>
import fs from 'node:fs';
import * as alphaTab from '@coderline/alphatab';

const model = alphaTab.model;

/**
 * Look up an alphaTab enum value's name, e.g. enumName(model.PickStroke, 2) -> "Down".
 */
function enumName(enumObject, value) {
    return enumObject[value];
}

/**
 * Collect the staff-level fields every note of that staff shares.
 */
function staffFields(track, staff) {
    return {
        trackIndex: track.index,
        staffIndex: staff.index,
        isPercussion: staff.isPercussion,
        capo: staff.capo,
        tuning: staff.tuning,
    };
}

/**
 * Collect the beat-level fields every note of that beat shares.
 */
function beatFields(bar, voice, beat) {
    return {
        barIndex: bar.index,
        voiceIndex: voice.index,
        onsetTick: beat.absolutePlaybackStart,
        durationTick: beat.playbackDuration,
        graceType: enumName(model.GraceType, beat.graceType),
        pickStroke: enumName(model.PickStroke, beat.pickStroke),
        beatVibrato: enumName(model.VibratoType, beat.vibrato),
        brushType: enumName(model.BrushType, beat.brushType),
        whammyBarType: enumName(model.WhammyType, beat.whammyBarType),
        rasgueado: enumName(model.Rasgueado, beat.rasgueado),
        fade: enumName(model.FadeType, beat.fade),
        golpe: enumName(model.GolpeType, beat.golpe),
        isTremolo: beat.isTremolo,
        tap: beat.tap,
        slap: beat.slap,
        pop: beat.pop,
        deadSlapped: beat.deadSlapped,
    };
}

/**
 * Collect the fields that belong to one note.
 */
function noteFields(note) {
    return {
        noteId: note.id,
        tieOriginNoteId: note.isTieDestination && note.tieOrigin ? note.tieOrigin.id : null,
        string: note.string,
        fret: note.fret,
        pitch: note.realValueWithoutHarmonic,
        isHammerPullOrigin: note.isHammerPullOrigin,
        isHammerPullDestination: note.isHammerPullDestination,
        slideInType: enumName(model.SlideInType, note.slideInType),
        slideOutType: enumName(model.SlideOutType, note.slideOutType),
        bendType: enumName(model.BendType, note.bendType),
        vibrato: enumName(model.VibratoType, note.vibrato),
        harmonicType: enumName(model.HarmonicType, note.harmonicType),
        accentuated: enumName(model.AccentuationType, note.accentuated),
        ornament: enumName(model.NoteOrnament, note.ornament),
        leftHandFinger: enumName(model.Fingers, note.leftHandFinger),
        rightHandFinger: enumName(model.Fingers, note.rightHandFinger),
        isPalmMute: note.isPalmMute,
        isLetRing: note.isLetRing,
        isStaccato: note.isStaccato,
        isDead: note.isDead,
        isGhost: note.isGhost,
        isTrill: note.isTrill,
        isLeftHandTapped: note.isLeftHandTapped,
    };
}

// Load the score; alphaTab picks the right importer (gp3–5, gpx, gp) from the bytes
const [inputPath] = process.argv.slice(2);
const score = alphaTab.importer.ScoreLoader.loadScoreFromBytes(
    new Uint8Array(fs.readFileSync(inputPath)),
    new alphaTab.Settings()
);

// Walk track → staff → bar → voice → beat → note, one output line per note
const lines = [];
for (const track of score.tracks) {
    for (const staff of track.staves) {
        const staffRow = staffFields(track, staff);
        for (const bar of staff.bars) {
            for (const voice of bar.voices) {
                for (const beat of voice.beats) {
                    const beatRow = beatFields(bar, voice, beat);
                    for (const note of beat.notes) {
                        lines.push(JSON.stringify({ startingTempo: score.tempo, ...staffRow, ...beatRow, ...noteFields(note) }));
                    }
                }
            }
        }
    }
}
process.stdout.write(lines.length > 0 ? lines.join('\n') + '\n' : '');
