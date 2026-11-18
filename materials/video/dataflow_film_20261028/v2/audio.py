"""Original procedural ambient score. No recordings or downloaded music."""
from pathlib import Path
import wave
import numpy as np

RATE = 48000
DURATION = 130
rng = np.random.default_rng(3576)
audio = np.zeros((RATE * DURATION, 2), np.float32)

def add(t0, duration, frequencies, gain, kind='pad', pan=0):
    start = int(t0 * RATE)
    n = min(int(duration * RATE), len(audio) - start)
    if n <= 0:
        return
    t = np.arange(n, dtype=np.float32) / RATE
    env = np.minimum(t / (1.7 if kind == 'pad' else .008), 1)
    env *= np.minimum((n / RATE - t) / (2.5 if kind == 'pad' else .25), 1)
    if kind != 'pad':
        env *= np.exp(-t * 3.4)
    signal = np.zeros(n, np.float32)
    for freq in frequencies:
        phase = 2 * np.pi * freq * t
        signal += np.sin(phase) + .18 * np.sin(phase * 2.002) + .035 * np.sin(phase * 3)
    signal *= env * gain / max(len(frequencies), 1)
    for channel, balance in enumerate([(1 - pan) ** .5, (1 + pan) ** .5]):
        audio[start:start+n, channel] += signal * balance

chords = [[110, 164.8138, 220, 261.6256, 329.6276],
          [87.3071, 130.8128, 174.6141, 220, 261.6256],
          [130.8128, 195.9977, 261.6256, 329.6276, 391.9954],
          [97.9989, 146.8324, 195.9977, 293.6648, 391.9954]]
for i in range(13):
    add(i * 10, 13, chords[i % 4], .12, pan=(-.25 if i % 2 else .25))

notes = [440, 659.255, 783.991, 523.251, 659.255, 391.995, 587.330, 659.255]
for i, t0 in enumerate(np.arange(3.75, 126, 1.25)):
    add(float(t0), 2.4, [notes[i % len(notes)]], .028 if i % 4 else .045,
        kind='pluck', pan=.55 * np.sin(i * 1.3))
    if i % 2 == 0:
        add(float(t0), .35, [55], .08, kind='pluck')

# Soft broadband sweeps at the camera transitions, filtered before mixing.
for transition in [10, 23, 35, 47, 59, 71, 83, 97, 107, 116, 125]:
    dur = 1.8
    n = int(dur * RATE)
    u = np.arange(n) / RATE
    noise = rng.normal(size=n).astype(np.float32)
    noise = np.convolve(noise, np.ones(45) / 45, mode='same')
    env = np.sin(np.pi * u / dur) ** 3
    start = int((transition - 1.2) * RATE)
    for c in range(2):
        audio[start:start+n, c] += noise * env * .13
    add(transition, 2, [220, 440, 659.255], .045, kind='pluck')

time = np.arange(len(audio)) / RATE
fade = np.minimum(time / 3, 1) * np.minimum((DURATION - time) / 4, 1)
audio *= fade[:, None]
peak = float(np.max(np.abs(audio)))
audio *= .48 / max(peak, .48)
out = Path(__file__).with_name('soundtrack.wav')
with wave.open(str(out), 'wb') as f:
    f.setnchannels(2)
    f.setsampwidth(2)
    f.setframerate(RATE)
    f.writeframes((audio * 32767).astype('<i2').tobytes())
print({'output': str(out), 'seconds': DURATION, 'peak': float(np.max(np.abs(audio))), 'original': True})
