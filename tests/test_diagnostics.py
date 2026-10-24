"""Connection checks and privacy-safe support reports."""
from studio_utility import diagnostics
from studio_utility.ui.connection import diagnose_connection


def test_connection_checks_are_actionable(monkeypatch):
    assert diagnose_connection("hub", "")[0][0] == "error"
    assert diagnose_connection("hub", "http://studio.local:8765/?token=x")[0][0] == "ok"
    assert diagnose_connection("video", "camera")[0][0] == "error"
    monkeypatch.delenv("STUDIO_UTILITY_EDSDK", raising=False)
    assert "STUDIO_UTILITY_EDSDK" in diagnose_connection("usb-edsdk", "")[0][1]


def test_support_report_contains_system_and_recent_log(tmp_path, monkeypatch):
    monkeypatch.setattr(diagnostics, "LOG_DIR", tmp_path / "logs")
    monkeypatch.setattr(diagnostics, "LOG_FILE", tmp_path / "logs" / "studio-utility.log")
    diagnostics.configure_logging()
    import logging
    logging.getLogger("studio_utility.test").warning("camera test failure")
    for handler in logging.getLogger("studio_utility").handlers:
        handler.flush()
    report = diagnostics.write_support_report(tmp_path / "support.txt").read_text()
    assert "Platform:" in report and "camera test failure" in report
