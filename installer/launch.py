"""Entry point for the packaged app: double-click opens the Creator Panel.
`studio-utility --self-test` checks the bundle (all transports import, mock camera works) and exits."""
import sys


def feature_checks() -> list[tuple[str, str]]:
    """Exercise each bundled feature for real, so a missing library or binary shows up here, not in use."""
    import subprocess
    import tempfile
    from pathlib import Path

    import numpy as np
    out = []

    def check(name, fn):
        try:
            out.append((name, fn() or "ok"))
        except Exception as e:
            out.append((name, f"FAILED {type(e).__name__}: {e}"))

    def audio():
        from studio_utility import audio as a
        ins = a.input_devices()
        return f"ok ({len(ins)} inputs)"

    def wav_and_voice():
        import soundfile as sf

        from studio_utility import voice
        d = Path(tempfile.mkdtemp())
        x = (np.random.default_rng(0).normal(0, 0.05, (48000, 2))).astype(np.float32)
        sf.write(d / "t.wav", x, 48000, subtype="PCM_24")
        voice.clean_file(d / "t.wav", voice.PRESETS["Podcast"], x[:4800])
        voice.LiveVoice(voice.PRESETS["Clean voice"])(x[:1024])

    def ffmpeg():
        from studio_utility import sync
        exe = sync.ffmpeg_exe()
        v = subprocess.run([exe, "-version"], capture_output=True, text=True, check=True).stdout.split()[2]
        return f"ok (ffmpeg {v})"

    def hub_client():
        from studio_utility.transports.hub import parse_link
        assert parse_link("http://10.11.0.2:8765/?token=x") == ("http://10.11.0.2:8765", "x")

    def lut():
        from studio_utility.lut import clog3_to_rec709
        assert clog3_to_rec709(9).shape == (9, 9, 9, 3)

    for name, fn in (("audio", audio), ("voice chain + WAV", wav_and_voice), ("voice sync ffmpeg", ffmpeg),
                     ("hub client", hub_client), ("Canon Log LUT", lut)):
        check(name, fn)
    return out


def self_test() -> int:
    from importlib import import_module

    from studio_utility import transports
    failed = []
    for kind, (module, cls) in transports.REGISTRY.items():
        try:
            getattr(import_module(module), cls)
        except Exception as e:  # optional deps (gphoto2, EDSDK library) may be absent; report, don't fail
            failed.append(f"{kind}: {type(e).__name__}: {e}")
    t = transports.load("mock")
    t.connect()
    t.shoot()
    assert t.liveview_frame()[:2] == b"\xff\xd8"
    from studio_utility import overlays, sequences  # noqa: F401  (bundled modules)
    checks = feature_checks()
    lines = ["self-test OK: mock camera works; transports: " + ", ".join(transports.REGISTRY)]
    lines += [f"  {name}: {result}" for name, result in checks]
    lines += [f"  unavailable {f}" for f in failed]
    print("\n".join(lines))
    # Windows GUI builds have no console: also leave the result in ~/.studio-utility/self_test.txt
    from pathlib import Path
    out = Path.home() / ".studio-utility" / "self_test.txt"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines) + "\n")
    return 1 if any("FAILED" in r for _, r in checks) else 0


if __name__ == "__main__":
    if "--self-test" in sys.argv:
        sys.exit(self_test())
    from studio_utility.ui.app import run
    run()
