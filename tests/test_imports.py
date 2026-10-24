from studio_utility.imports import ImportJournal


def test_import_journal_recovers_incomplete_transfers(tmp_path):
    journal = ImportJournal(tmp_path)
    destination = tmp_path / "2026-10-23"
    journal.update("/card/A.JPG", destination, "queued")
    journal.update("/card/B.MP4", destination, "downloading")
    journal.update("/card/C.JPG", destination, "completed")

    assert journal.pending() == [
        ("/card/A.JPG", destination),
        ("/card/B.MP4", destination),
    ]
    assert not journal.path.with_suffix(".tmp").exists()


def test_corrupt_import_journal_is_treated_as_empty(tmp_path):
    journal = ImportJournal(tmp_path)
    journal.path.write_text("not json")
    assert journal.pending() == []
