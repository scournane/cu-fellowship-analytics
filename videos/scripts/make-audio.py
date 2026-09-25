"""Synthesise every sound the walkthrough videos use.

All of it is generated here from sine waves and noise, so there is no sample
licence to track and the files can be rebuilt byte-for-byte:

    python videos/scripts/make-audio.py      # needs numpy

Writes to videos/public/audio/.
"""

from __future__ import annotations

import wave
from pathlib import Path

import numpy as np

SR = 44100
OUT = Path(__file__).resolve().parent.parent / "public" / "audio"
rng = np.random.default_rng(7)


def t(seconds: float) -> np.ndarray:
    return np.arange(int(SR * seconds)) / SR


def env(n: int, attack: float, decay: float) -> np.ndarray:
    """Linear attack, exponential decay (decay = time constant in seconds)."""
    x = np.arange(n) / SR
    a = np.clip(x / max(attack, 1e-4), 0, 1)
    return a * np.exp(-np.maximum(x - attack, 0) / decay)


def lowpass(x: np.ndarray, cutoff: float) -> np.ndarray:
    alpha = 1 - np.exp(-2 * np.pi * cutoff / SR)
    y = np.empty_like(x)
    acc = 0.0
    for i, v in enumerate(x):
        acc += alpha * (v - acc)
        y[i] = acc
    return y


def write(name: str, x: np.ndarray, peak: float = 0.9) -> None:
    x = x / (np.max(np.abs(x)) or 1) * peak
    fade = min(len(x), int(SR * 0.005))
    x[-fade:] *= np.linspace(1, 0, fade)
    data = (x * 32767).astype("<i2").tobytes()
    OUT.mkdir(parents=True, exist_ok=True)
    with wave.open(str(OUT / name), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(data)
    print(f"wrote {name}  {len(x) / SR:.2f}s")


# --- sound effects ----------------------------------------------------------

def pop() -> np.ndarray:
    x = t(0.12)
    f = 900 * np.exp(-x * 30) + 380
    return np.sin(2 * np.pi * np.cumsum(f) / SR) * env(len(x), 0.002, 0.035)


def click() -> np.ndarray:
    x = t(0.06)
    tick = rng.normal(size=len(x)) * env(len(x), 0.0005, 0.004)
    thump = np.sin(2 * np.pi * 180 * x) * env(len(x), 0.001, 0.012)
    return lowpass(tick, 5000) * 0.8 + thump * 0.6


def key() -> np.ndarray:
    x = t(0.04)
    return lowpass(rng.normal(size=len(x)), 3500) * env(len(x), 0.0005, 0.006)


def whoosh() -> np.ndarray:
    x = t(0.45)
    noise = rng.normal(size=len(x))
    swell = np.sin(np.pi * np.clip(x / 0.45, 0, 1)) ** 2
    # sweep the filter by crossfading two bands
    low, high = lowpass(noise, 700), lowpass(noise, 2600)
    mix = np.clip(x / 0.45, 0, 1)
    return (low * (1 - mix) + high * mix) * swell


def bell(freq: float, seconds: float, decay: float) -> np.ndarray:
    x = t(seconds)
    partials = [(1, 1.0), (2.0, 0.45), (2.76, 0.25), (5.4, 0.08)]
    out = sum(a * np.sin(2 * np.pi * freq * r * x) * np.exp(-x * r / decay) for r, a in partials)
    return out * env(len(x), 0.002, 10)


def ding() -> np.ndarray:
    return bell(1318.5, 1.4, 0.55)  # E6 — the mascot is a desk bell


def success() -> np.ndarray:
    # C6 → E6 → G6, a quick "that's right" arpeggio
    x = np.zeros(int(SR * 0.9))
    for i, f in enumerate((1046.5, 1318.5, 1568.0)):
        start = int(SR * 0.075 * i)
        b = bell(f, 0.9 - 0.075 * i, 0.3)
        x[start:start + len(b)] += b * (0.8 + 0.1 * i)
    return x


def error() -> np.ndarray:
    x = np.zeros(int(SR * 0.42))
    for start in (0.0, 0.2):
        seg = t(0.16)
        tone = np.sign(np.sin(2 * np.pi * 196 * seg)) * 0.5 + np.sin(2 * np.pi * 185 * seg)
        s = int(SR * start)
        x[s:s + len(seg)] += lowpass(tone, 1200) * env(len(seg), 0.004, 0.08)
    return x


def notify() -> np.ndarray:
    # two soft marimba notes, up a fourth
    x = np.zeros(int(SR * 0.6))
    for i, f in enumerate((880.0, 1174.7)):
        b = marimba(f, 0.45)
        s = int(SR * 0.11 * i)
        x[s:s + len(b)] += b
    return x


# --- music ------------------------------------------------------------------

def marimba(freq: float, seconds: float) -> np.ndarray:
    x = t(seconds)
    body = np.sin(2 * np.pi * freq * x) + 0.25 * np.sin(2 * np.pi * freq * 4 * x) * np.exp(-x * 40)
    return body * env(len(x), 0.002, 0.16)


def pluck(freq: float, seconds: float) -> np.ndarray:
    """Karplus-Strong: a soft ukulele-ish pluck."""
    n = int(SR * seconds)
    period = int(SR / freq)
    buf = rng.uniform(-1, 1, period)
    out = np.empty(n)
    for i in range(n):
        v = buf[i % period]
        out[i] = v
        buf[i % period] = 0.996 * 0.5 * (v + buf[(i + 1) % period])
    return lowpass(out, 3200)


def kick() -> np.ndarray:
    x = t(0.25)
    f = 120 * np.exp(-x * 25) + 45
    return np.sin(2 * np.pi * np.cumsum(f) / SR) * env(len(x), 0.001, 0.09)


def shaker() -> np.ndarray:
    x = t(0.07)
    n = rng.normal(size=len(x))
    return (n - lowpass(n, 6000)) * env(len(x), 0.004, 0.02)


def note(name: str) -> float:
    names = {"C": -9, "D": -7, "E": -5, "F": -4, "G": -2, "A": 0, "B": 2}
    letter, octave = name[0], int(name[-1])
    semis = names[letter] + (1 if "#" in name else 0) + 12 * (octave - 4)
    return 440.0 * 2 ** (semis / 12)


def music(bars: int = 32, bpm: float = 112) -> np.ndarray:
    beat = 60 / bpm
    total = bars * 4 * beat
    mix = np.zeros(int(SR * (total + 1)))

    def place(sig: np.ndarray, at: float, gain: float) -> None:
        s = int(SR * at)
        e = min(len(mix), s + len(sig))
        mix[s:e] += sig[: e - s] * gain

    # I – V – vi – IV in C, a bright classroom loop
    chords = [
        ["C4", "E4", "G4"], ["G3", "B3", "D4"], ["A3", "C4", "E4"], ["F3", "A3", "C4"],
    ]
    bass = ["C3", "G2", "A2", "F2"]
    # two melodic phrases so the loop does not feel like a four-bar ring tone
    melody_a = ["E5", "G5", "E5", "D5", "D5", "B4", "D5", "G5",
                "C5", "E5", "A5", "G5", "F5", "A5", "G5", "E5"]
    melody_b = ["G5", "E5", "C5", "E5", "D5", "G4", "B4", "D5",
                "E5", "C5", "A4", "C5", "A4", "C5", "F5", "E5"]

    for bar in range(bars):
        start = bar * 4 * beat
        chord = chords[bar % 4]
        section = (bar // 8) % 4  # intro-ish, full, breakdown, full
        # strummed pluck chord on beats 1 and 3, off-beat on 2&
        for hit in (0, 1.5, 2):
            for k, n in enumerate(chord):
                place(pluck(note(n), beat * 1.6), start + hit * beat + k * 0.012, 0.16)
        place(marimba(note(bass[bar % 4]), beat * 1.8), start, 0.55)
        place(marimba(note(bass[bar % 4]), beat * 1.2), start + 2.5 * beat, 0.35)
        # drums: soft kick on 1 and 3, shaker on 8ths
        if section != 2:
            for b in (0, 2):
                place(kick(), start + b * beat, 0.5)
        for e8 in range(8):
            place(shaker(), start + e8 * beat / 2, 0.10 if e8 % 2 else 0.05)
        # melody, eighth notes, only in the "full" sections
        if section in (1, 3):
            mel = melody_a if (bar // 4) % 2 == 0 else melody_b
            for i in range(4):
                n = mel[(bar % 4) * 4 + i]
                place(marimba(note(n), beat * 0.9), start + i * beat, 0.30)
                place(marimba(note(n) * 2, beat * 0.5), start + i * beat, 0.05)

    mix = mix[: int(SR * total)]
    # gentle fade at both ends so <Audio loop> joins cleanly
    fade = int(SR * 0.02)
    mix[:fade] *= np.linspace(0, 1, fade)
    mix[-fade:] *= np.linspace(1, 0, fade)
    return mix


if __name__ == "__main__":
    write("pop.wav", pop(), 0.8)
    write("click.wav", click(), 0.8)
    write("key.wav", key(), 0.5)
    write("whoosh.wav", whoosh(), 0.6)
    write("ding.wav", ding(), 0.8)
    write("success.wav", success(), 0.8)
    write("error.wav", error(), 0.6)
    write("notify.wav", notify(), 0.7)
    write("music.wav", music(), 0.8)
