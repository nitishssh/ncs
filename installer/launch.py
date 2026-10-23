"""Entry point for the packaged app: double-click opens the Creator Panel.
`camctl --self-test` checks the bundle (all transports import, mock camera works) and exits."""
import sys


def self_test() -> int:
    from importlib import import_module

    from camctl import transports
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
    from camctl import overlays, sequences  # noqa: F401  (bundled modules)
    lines = ["self-test OK: mock camera works; transports: " + ", ".join(transports.REGISTRY)]
    lines += [f"  unavailable {f}" for f in failed]
    print("\n".join(lines))
    # Windows GUI builds have no console: also leave the result in ~/.camctl/self_test.txt
    from pathlib import Path
    out = Path.home() / ".camctl" / "self_test.txt"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines) + "\n")
    return 0


if __name__ == "__main__":
    if "--self-test" in sys.argv:
        sys.exit(self_test())
    from camctl.ui.app import run
    run()
