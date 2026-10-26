"""Post-recording editor: trim, A/B versions, re-clean, export - the raw take is never touched."""
from __future__ import annotations

import numpy as np
import pytest

sf = pytest.importorskip("soundfile")
from studio_utility import editor, voice  # noqa: E402

R = 48000


@pytest.fixture
def take(tmp_path):
    t = np.arange(R * 4) / R
    x = 0.3 * np.sin(2 * np.pi * 220 * t)[:, None].repeat(2, 1) + 0.01 * np.random.default_rng(0).normal(size=(len(t), 2))
    path = tmp_path / "2026-10-26_143005_MicA_DJIMic.wav"
    sf.write(path, x, R, subtype="PCM_24")
    return path


def test_load_picks_up_the_auto_cleaned_copy(take):
    assert editor.Take.load(take).cleaned is None
    sf.write(take.with_name(take.stem + "_voice.wav"), np.zeros((R * 4, 2)), R, subtype="PCM_24")
    t = editor.Take.load(take)
    assert t.cleaned is not None and not t.cleaned.any()


def test_opening_the_voice_file_names_exports_after_the_raw_take(take):
    v = take.with_name(take.stem + "_voice.wav")
    sf.write(v, np.zeros((R, 2)), R, subtype="PCM_24")
    t = editor.Take.load(v)
    assert t.raw_path() == take and t.export_path(False).name == take.name.replace(".wav", ".wav")


def test_trim_export_with_fades_and_original_untouched(take):
    before = take.read_bytes()
    t = editor.Take.load(take)
    t.set_trim(3.0, 1.0)  # dragged right to left
    assert (t.start, t.end) == (R, 3 * R) and t.trimmed
    out = t.export(cleaned=False)
    assert out.name == "2026-10-26_143005_MicA_DJIMic_edit.wav"
    y, r = sf.read(out, always_2d=True)
    assert (r, len(y), sf.info(out).subtype) == (R, 2 * R, "PCM_24")
    assert abs(y[0]).max() < 1e-3 and abs(y[-1]).max() < 1e-3  # faded, no click at the cut
    assert take.read_bytes() == before


def test_a_click_is_not_a_trim(take):
    t = editor.Take.load(take)
    t.set_trim(2.0, 2.01)
    assert not t.trimmed


def test_never_overwrites_the_raw_recording(take):
    t = editor.Take.load(take)
    with pytest.raises(ValueError):
        t.export(cleaned=False, path=take)


def test_peaks_cover_the_whole_take(take):
    p = editor.Take.load(take).peaks(500)
    assert p.shape == (500, 2) and (p[:, 0] <= p[:, 1]).all() and p[:, 1].max() == pytest.approx(0.31, abs=0.03)
    assert editor.Take.load(take).peaks(10_000_000 // R).shape[0] == 10_000_000 // R  # more columns than needed is fine


@pytest.mark.skipif(voice.pb is None, reason="pedalboard not installed")
def test_reclean_then_export_cleaned_hits_loudness(take):
    from studio_utility import advice
    t = editor.Take.load(take)
    used = t.clean(voice.VoiceSettings(engine="profile", denoise=0.5, gate=False, target_lufs=-16), None)
    assert used in ("profile", "rnnoise", "deepfilternet") and t.cleaned.shape == t.audio.shape
    y, r = sf.read(t.export(cleaned=True), always_2d=True)
    assert advice.lufs(y, r) == pytest.approx(-16, abs=1)
    assert t.export_path(True).name.endswith("_voice.wav")


def test_sync_ignores_editor_exports(tmp_path):
    from studio_utility import sync
    for name in ("2026-10-26_143005_MicA_X.wav", "2026-10-26_143005_MicA_X_edit.wav",
                 "2026-10-26_143005_MicA_X_voice_edit.wav"):
        sf.write(tmp_path / name, np.zeros((100, 1)), R)
    raws = [w for w in tmp_path.glob("*.wav") if not w.stem.endswith(("_voice", "_edit")) and sync.voice_start(w)]
    assert [w.name for w in raws] == ["2026-10-26_143005_MicA_X.wav"]


def test_cleaned_report_doesnt_blame_the_limiter(take):
    t = editor.Take.load(take)
    t.cleaned = (t.audio / abs(t.audio).max() * 10 ** (-2 / 20)).astype(np.float32)  # peak -2 dBFS, as after loudness
    assert not any(x.title.startswith("Very hot") for x in t.report(cleaned=True).tips)
    t.audio = t.cleaned
    assert any(x.title.startswith("Very hot") for x in t.report(cleaned=False).tips)  # raw: still a real warning


def test_a_second_trim_never_replaces_the_first(take):
    t = editor.Take.load(take)
    t.set_trim(0.1, 0.5)
    first = t.export(cleaned=False)
    t.set_trim(0.2, 0.6)
    second = t.export(cleaned=False)
    assert first != second and first.exists() and second.stem.endswith("_edit")
