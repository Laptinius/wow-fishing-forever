import numpy as np

from wowfish.audio import AudioChunk
from wowfish.audio.clips import load_reference_clips
from wowfish.audio.detector import detector_for
from wowfish.audio.features import DEFAULT_MEL
from wowfish.config import AudioConfig


def test_gain_detects_quiet_splash_and_leaves_silence_quiet():
    rate = DEFAULT_MEL.sample_rate
    clip = next(iter(load_reference_clips(rate).values())) / 64
    silence = np.zeros(rate, dtype=np.float32)
    samples = np.concatenate([silence, clip, silence])
    detector = detector_for(AudioConfig(gain_db=36.0))
    events = []
    for start in range(0, len(samples), 1024):
        part = samples[start : start + 1024]
        events += detector.feed(AudioChunk(part, rate, (start + len(part)) / rate))
    assert len(events) == 1
    assert events[0].score >= 0.66


def test_default_detector_recognizes_each_splash_within_700ms():
    rate = DEFAULT_MEL.sample_rate
    silence = np.zeros(rate, dtype=np.float32)
    for name, clip in load_reference_clips(rate).items():
        detector = detector_for(AudioConfig())
        samples = np.concatenate([silence, clip, silence])
        heard = []
        first_detection = None
        for start in range(0, len(samples), 512):
            part = samples[start : start + 512]
            end = (start + len(part)) / rate
            events = detector.feed(AudioChunk(part, rate, end))
            if events and first_detection is None:
                first_detection = end
            heard.extend(events)
        assert [event.clip for event in heard] == [name]
        assert first_detection is not None and first_detection - 1 <= 0.7
