"""Integration tests — exercise the full `_run()` workflow with mocked I/O.

These tests stitch the real classes together and verify the orchestration
inside `_run()`: missing-chapter computation, scratch recovery, orphan
cleanup, dry-run, import polling, and the trigger_scan fallback.

Patterns borrowed from matriarch-vy/tests/test_integration.py, adapted to
the violetscans-specific class names.
"""

import tempfile
import time
import zipfile
from pathlib import Path
from unittest.mock import Mock, patch

import main


# ─────────────────────────── helpers ───────────────────────────────────────


def _create_fake_cbz(path: Path, name: str) -> Path:
    """Drop a real (1-page) CBZ into `path` with the given filename."""
    path.mkdir(parents=True, exist_ok=True)
    cbz = path / name
    with zipfile.ZipFile(cbz, "w") as z:
        z.writestr("page_001.jpg", b"fake")
    return cbz


def _make_download_side_effect(scratch_path: Path):
    """Side-effect that simulates VioletScansScraper.download_chapter:
    create the expected CBZ on disk, return True."""

    def _download(chapter, output_path, *args, **kwargs):
        output_path.mkdir(parents=True, exist_ok=True)
        cbz = output_path / f"Chapter {main._chapter_str(chapter)}.cbz"
        with zipfile.ZipFile(cbz, "w") as z:
            z.writestr("page_001.jpg", b"fake")
        return True

    return _download


def _import_and_move(series_id, file_paths, *args, **kwargs):
    """Side-effect that simulates Komga's async import — moves files away."""
    for f in file_paths:
        p = Path(f)
        if p.exists():
            p.unlink()
    return True


def _import_fail(series_id, file_paths, *args, **kwargs):
    return False


def _build_clients_and_scratch(tmp_dir: str):
    """Build a real KomgaAPIClient + VioletScansScraper + ScratchFileManager
    with their network methods unstubbed (we patch them per-test)."""
    scratch_path = Path(tmp_dir) / "violetscans-test"
    scratch_path.mkdir(parents=True)
    komga = main.KomgaAPIClient("http://komga.example.com", "test-key", test_mode=False)
    scraper = main.VioletScansScraper(
        "https://violetscans.org/comics/x/", test_mode=False
    )
    manager = main.ScratchFileManager(scratch_path, test_mode=False)
    return komga, scraper, manager, scratch_path


# ─────────────────────────── _run() scenarios ──────────────────────────────


class TestRunNoOps:
    def test_series_not_found_logs_and_returns(self):
        """When Komga can't find the series, _run exits without further action."""
        with tempfile.TemporaryDirectory() as tmp:
            komga, scraper, mgr, scratch = _build_clients_and_scratch(tmp)
            with patch.object(komga, "get_series_id", return_value=None):
                # Should not raise, should not attempt to fetch chapters.
                main._run(komga, scraper, mgr, scratch, "Test", "lib", False)

    def test_no_missing_chapters_skips_download(self):
        """If Komga already has every available chapter, no download runs."""
        with tempfile.TemporaryDirectory() as tmp:
            komga, scraper, mgr, scratch = _build_clients_and_scratch(tmp)
            with (
                patch.object(komga, "get_series_id", return_value="s-1"),
                patch.object(komga, "get_existing_books", return_value=[1.0, 2.0, 3.0]),
                patch.object(scraper, "get_all_chapters", return_value=[1.0, 2.0, 3.0]),
                patch.object(scraper, "download_chapter") as dl,
                patch.object(komga, "import_books") as imp,
            ):
                main._run(komga, scraper, mgr, scratch, "Test", "lib", False)

            dl.assert_not_called()
            imp.assert_not_called()


class TestRunDryRun:
    def test_dry_run_does_not_download_or_import(self):
        with tempfile.TemporaryDirectory() as tmp:
            komga, scraper, mgr, scratch = _build_clients_and_scratch(tmp)
            with (
                patch.object(komga, "get_series_id", return_value="s-1"),
                patch.object(komga, "get_existing_books", return_value=[2.0]),
                patch.object(scraper, "get_all_chapters", return_value=[1.0, 2.0, 3.0]),
                patch.object(scraper, "download_chapter") as dl,
                patch.object(komga, "import_books") as imp,
            ):
                main._run(komga, scraper, mgr, scratch, "Test", "lib", True)

            dl.assert_not_called()
            imp.assert_not_called()


class TestRunHappyPath:
    def test_downloads_and_imports_missing_chapters(self):
        with tempfile.TemporaryDirectory() as tmp:
            komga, scraper, mgr, scratch = _build_clients_and_scratch(tmp)
            with (
                patch.object(komga, "get_series_id", return_value="s-1"),
                patch.object(komga, "get_existing_books", return_value=[1.0]),
                patch.object(scraper, "get_all_chapters", return_value=[1.0, 2.0]),
                patch.object(
                    scraper,
                    "download_chapter",
                    side_effect=_make_download_side_effect(scratch),
                ),
                patch.object(
                    komga, "import_books", side_effect=_import_and_move
                ) as imp,
                patch("time.sleep"),
            ):
                main._run(komga, scraper, mgr, scratch, "Test", "lib", False)

            imp.assert_called_once()
            series_id, files = imp.call_args[0][:2]
            assert series_id == "s-1"
            assert len(files) == 1
            assert files[0].name == "Chapter 2.cbz"

    def test_downloads_only_truly_missing_chapters(self):
        """Avoids re-downloading chapters that are already in Komga."""
        with tempfile.TemporaryDirectory() as tmp:
            komga, scraper, mgr, scratch = _build_clients_and_scratch(tmp)
            with (
                patch.object(komga, "get_series_id", return_value="s-1"),
                patch.object(komga, "get_existing_books", return_value=[1.0, 2.0]),
                patch.object(
                    scraper, "get_all_chapters", return_value=[1.0, 2.0, 3.0, 4.0]
                ),
                patch.object(
                    scraper,
                    "download_chapter",
                    side_effect=_make_download_side_effect(scratch),
                ) as dl,
                patch.object(komga, "import_books", side_effect=_import_and_move),
                patch("time.sleep"),
            ):
                main._run(komga, scraper, mgr, scratch, "Test", "lib", False)

            downloaded = sorted(c.args[0] for c in dl.call_args_list)
            assert downloaded == [3.0, 4.0]


class TestRunRecovery:
    def test_recovers_cbz_from_previous_run(self):
        """A CBZ left behind by a failed previous import is re-imported, not re-downloaded."""
        with tempfile.TemporaryDirectory() as tmp:
            komga, scraper, mgr, scratch = _build_clients_and_scratch(tmp)
            _create_fake_cbz(scratch, "Chapter 5.cbz")

            with (
                patch.object(komga, "get_series_id", return_value="s-1"),
                patch.object(komga, "get_existing_books", return_value=[]),
                patch.object(scraper, "get_all_chapters", return_value=[5.0, 6.0]),
                patch.object(
                    scraper,
                    "download_chapter",
                    side_effect=_make_download_side_effect(scratch),
                ) as dl,
                patch.object(
                    komga, "import_books", side_effect=_import_and_move
                ) as imp,
                patch("time.sleep"),
            ):
                main._run(komga, scraper, mgr, scratch, "Test", "lib", False)

            # Chapter 5 should NOT be re-downloaded (it was recovered).
            downloaded = [c.args[0] for c in dl.call_args_list]
            assert 5.0 not in downloaded
            # But both should be imported.
            assert imp.call_count == 1
            assert len(imp.call_args[0][1]) == 2

    def test_cleans_up_orphaned_scratch_already_in_komga(self):
        """A leftover CBZ for a chapter that's now in Komga gets deleted."""
        with tempfile.TemporaryDirectory() as tmp:
            komga, scraper, mgr, scratch = _build_clients_and_scratch(tmp)
            orphan = _create_fake_cbz(scratch, "Chapter 5.cbz")

            with (
                patch.object(komga, "get_series_id", return_value="s-1"),
                patch.object(komga, "get_existing_books", return_value=[5.0]),
                patch.object(scraper, "get_all_chapters", return_value=[5.0, 6.0]),
                patch.object(
                    scraper,
                    "download_chapter",
                    side_effect=_make_download_side_effect(scratch),
                ),
                patch.object(komga, "import_books", side_effect=_import_and_move),
                patch("time.sleep"),
            ):
                main._run(komga, scraper, mgr, scratch, "Test", "lib", False)

            assert not orphan.exists(), (
                "orphaned scratch file should be cleaned up before further work"
            )


class TestRunErrors:
    def test_continues_when_individual_download_fails(self):
        """Failed download doesn't crash _run — other chapters still proceed."""
        with tempfile.TemporaryDirectory() as tmp:
            komga, scraper, mgr, scratch = _build_clients_and_scratch(tmp)

            def download_some(chapter, output_path, *args, **kwargs):
                if chapter == 2.0:
                    return False  # simulate failure
                return _make_download_side_effect(scratch)(chapter, output_path)

            with (
                patch.object(komga, "get_series_id", return_value="s-1"),
                patch.object(komga, "get_existing_books", return_value=[]),
                patch.object(scraper, "get_all_chapters", return_value=[1.0, 2.0, 3.0]),
                patch.object(scraper, "download_chapter", side_effect=download_some),
                patch.object(
                    komga, "import_books", side_effect=_import_and_move
                ) as imp,
                patch("time.sleep"),
            ):
                main._run(komga, scraper, mgr, scratch, "Test", "lib", False)

            # Only successful downloads imported: 1.0 and 3.0
            imported_files = imp.call_args[0][1]
            assert len(imported_files) == 2
            assert {f.name for f in imported_files} == {
                "Chapter 1.cbz",
                "Chapter 3.cbz",
            }

    def test_exception_in_get_series_id_is_caught(self):
        """Top-level exception handling: a thrown Komga error is logged, not raised."""
        with tempfile.TemporaryDirectory() as tmp:
            komga, scraper, mgr, scratch = _build_clients_and_scratch(tmp)
            with patch.object(
                komga, "get_series_id", side_effect=Exception("Komga down")
            ):
                # Must not raise.
                main._run(komga, scraper, mgr, scratch, "Test", "lib", False)


class TestRunImportFallback:
    def test_falls_back_to_scan_when_import_fails(self):
        """Import API failure triggers a library scan as a degraded fallback."""
        with tempfile.TemporaryDirectory() as tmp:
            komga, scraper, mgr, scratch = _build_clients_and_scratch(tmp)
            with (
                patch.object(komga, "get_series_id", return_value="s-1"),
                patch.object(komga, "get_existing_books", return_value=[]),
                patch.object(scraper, "get_all_chapters", return_value=[1.0]),
                patch.object(
                    scraper,
                    "download_chapter",
                    side_effect=_make_download_side_effect(scratch),
                ),
                patch.object(komga, "import_books", side_effect=_import_fail),
                patch.object(komga, "trigger_scan", return_value=True) as scan,
            ):
                main._run(komga, scraper, mgr, scratch, "Test", "lib", False)

            scan.assert_called_once_with("lib")

    def test_no_scan_when_import_succeeds(self):
        with tempfile.TemporaryDirectory() as tmp:
            komga, scraper, mgr, scratch = _build_clients_and_scratch(tmp)
            with (
                patch.object(komga, "get_series_id", return_value="s-1"),
                patch.object(komga, "get_existing_books", return_value=[]),
                patch.object(scraper, "get_all_chapters", return_value=[1.0]),
                patch.object(
                    scraper,
                    "download_chapter",
                    side_effect=_make_download_side_effect(scratch),
                ),
                patch.object(komga, "import_books", side_effect=_import_and_move),
                patch.object(komga, "trigger_scan") as scan,
                patch("time.sleep"),
            ):
                main._run(komga, scraper, mgr, scratch, "Test", "lib", False)

            scan.assert_not_called()


class TestRunImportPolling:
    """The post-import polling loop has a 300s deadline. Patching `time.time`
    lets us simulate timeout behavior without sleeping in tests."""

    def test_polls_until_files_moved(self):
        with tempfile.TemporaryDirectory() as tmp:
            komga, scraper, mgr, scratch = _build_clients_and_scratch(tmp)
            with (
                patch.object(komga, "get_series_id", return_value="s-1"),
                patch.object(komga, "get_existing_books", return_value=[]),
                patch.object(scraper, "get_all_chapters", return_value=[1.0]),
                patch.object(
                    scraper,
                    "download_chapter",
                    side_effect=_make_download_side_effect(scratch),
                ),
                patch.object(komga, "import_books", side_effect=_import_and_move),
                patch("time.sleep") as sleep_mock,
            ):
                main._run(komga, scraper, mgr, scratch, "Test", "lib", False)

            # File moved immediately by _import_and_move's eager unlink, so
            # the polling loop exits on first iteration. At least one sleep
            # may or may not happen depending on loop ordering.
            # The crucial assertion: the workflow completed without timing out.
            assert sleep_mock.call_count <= 100, (
                "polling should have terminated quickly when files moved"
            )

    def test_polling_times_out_when_files_never_move(self):
        """If Komga never moves the file, we hit the 300s deadline and stop."""
        with tempfile.TemporaryDirectory() as tmp:
            komga, scraper, mgr, scratch = _build_clients_and_scratch(tmp)

            # Stuck import: returns True but doesn't move files.
            def stuck_import(series_id, file_paths, *args, **kwargs):
                return True

            # Advance fake time aggressively past the 300s deadline.
            fake_clock = [0.0]

            def fake_time():
                fake_clock[0] += 100
                return fake_clock[0]

            with (
                patch.object(komga, "get_series_id", return_value="s-1"),
                patch.object(komga, "get_existing_books", return_value=[]),
                patch.object(scraper, "get_all_chapters", return_value=[1.0]),
                patch.object(
                    scraper,
                    "download_chapter",
                    side_effect=_make_download_side_effect(scratch),
                ),
                patch.object(komga, "import_books", side_effect=stuck_import),
                patch("time.sleep"),
                patch("time.time", side_effect=fake_time),
            ):
                # Must not hang.
                main._run(komga, scraper, mgr, scratch, "Test", "lib", False)

            # File is still in scratch (Komga never moved it).
            assert (scratch / "Chapter 1.cbz").exists()


# ─────────────────────── full main() integration ───────────────────────────


class TestMainFullWorkflow:
    """End-to-end through main() with env-based config (no secret mount)."""

    def test_main_runs_workflow_with_new_chapters(self, monkeypatch):
        with tempfile.TemporaryDirectory() as tmp:
            monkeypatch.setenv("SERIES_NAME", "Test Series")
            monkeypatch.setenv("VIOLET_URL", "https://violetscans.org/comics/x/")
            monkeypatch.setenv("KOMGA_API_KEY", "test-key")
            monkeypatch.setenv("KOMGA_API_URL", "http://komga.example.com")
            monkeypatch.setenv("SCRATCH_PATH", tmp)

            scratch = (
                Path(tmp) / "violetscans"
            )  # SCRATCH_SUBDIR defaults to 'violetscans' when no secret name

            with (
                patch.object(main.KomgaAPIClient, "get_series_id", return_value="s-1"),
                patch.object(
                    main.KomgaAPIClient, "get_existing_books", return_value=[1.0]
                ),
                patch.object(
                    main.VioletScansScraper, "get_all_chapters", return_value=[1.0, 2.0]
                ),
                patch.object(
                    main.VioletScansScraper,
                    "download_chapter",
                    side_effect=_make_download_side_effect(scratch),
                ),
                patch.object(
                    main.KomgaAPIClient, "import_books", side_effect=_import_and_move
                ) as imp,
                patch("time.sleep"),
            ):
                result = main.main()

            assert result["status"] == "success"
            assert result["series"] == "Test Series"
            imp.assert_called_once()

    def test_main_uses_secret_name_as_scratch_subdir(self, monkeypatch, tmp_path):
        """When a secret is mounted, the scratch subdir derives from its name —
        this is what prevents concurrent series from colliding."""
        # Seed a mounted secret.
        base = tmp_path / "secrets" / "fission"
        (base / "alpha-series").mkdir(parents=True)
        for k, v in [
            ("SERIES_NAME", "Alpha"),
            ("VIOLET_URL", "https://violetscans.org/comics/alpha/"),
            ("KOMGA_API_KEY", "k"),
            ("KOMGA_API_URL", "http://komga"),
        ]:
            (base / "alpha-series" / k).write_text(v)

        monkeypatch.setattr(main, "SECRET_BASE_DIR", base)

        scratch_root = tmp_path / "scratch"
        scratch_root.mkdir()
        monkeypatch.setenv("SCRATCH_PATH", str(scratch_root))

        with (
            patch.object(main.KomgaAPIClient, "get_series_id", return_value="s-1"),
            patch.object(main.KomgaAPIClient, "get_existing_books", return_value=[1.0]),
            patch.object(
                main.VioletScansScraper, "get_all_chapters", return_value=[1.0]
            ),
            patch("time.sleep"),
        ):
            result = main.main()

        assert result["secret_name"] == "alpha-series"
        # The scratch subdir is created based on the discovered secret name.
        assert (scratch_root / "alpha-series").exists()


# ─────────────────────────── batching tests ────────────────────────────────


class TestRunBatching:
    """Verify that import happens in batches of `batch_size` so a single
    invocation can make incremental progress on large backlogs.
    """

    def test_batches_imports_when_backlog_exceeds_batch_size(self):
        """13 missing chapters with batch_size=5 → 3 import calls (5+5+3)."""
        with tempfile.TemporaryDirectory() as tmp:
            komga, scraper, mgr, scratch = _build_clients_and_scratch(tmp)
            all_chapters = list(range(1, 14))  # 13 chapters
            with patch.object(komga, "get_series_id", return_value="s-1"), \
                 patch.object(komga, "get_existing_books", return_value=[]), \
                 patch.object(scraper, "get_all_chapters",
                              return_value=[float(c) for c in all_chapters]), \
                 patch.object(scraper, "download_chapter",
                              side_effect=_make_download_side_effect(scratch)), \
                 patch.object(komga, "import_books",
                              side_effect=_import_and_move) as imp, \
                 patch("time.sleep"):
                main._run(komga, scraper, mgr, scratch, "Test", "lib", False,
                          batch_size=5)

            assert imp.call_count == 3, \
                f"expected 3 import batches (5+5+3), got {imp.call_count}"
            # Inspect sizes of each batch.
            batch_sizes = [len(call.args[1]) for call in imp.call_args_list]
            assert batch_sizes == [5, 5, 3], batch_sizes

    def test_single_batch_when_backlog_fits(self):
        """3 missing chapters with batch_size=5 → 1 import call."""
        with tempfile.TemporaryDirectory() as tmp:
            komga, scraper, mgr, scratch = _build_clients_and_scratch(tmp)
            with patch.object(komga, "get_series_id", return_value="s-1"), \
                 patch.object(komga, "get_existing_books", return_value=[]), \
                 patch.object(scraper, "get_all_chapters",
                              return_value=[1.0, 2.0, 3.0]), \
                 patch.object(scraper, "download_chapter",
                              side_effect=_make_download_side_effect(scratch)), \
                 patch.object(komga, "import_books",
                              side_effect=_import_and_move) as imp, \
                 patch("time.sleep"):
                main._run(komga, scraper, mgr, scratch, "Test", "lib", False,
                          batch_size=5)

            assert imp.call_count == 1
            assert len(imp.call_args.args[1]) == 3

    def test_recovered_files_flushed_before_downloads(self):
        """If we have recovered files >= batch_size and new downloads, the
        recovered files get imported FIRST in their own batch.

        This means a re-run after a failed import doesn't get blocked behind
        new downloads — Komga sees the cached work immediately.
        """
        with tempfile.TemporaryDirectory() as tmp:
            komga, scraper, mgr, scratch = _build_clients_and_scratch(tmp)
            # Seed 5 recovered files.
            for c in [1.0, 2.0, 3.0, 4.0, 5.0]:
                _create_fake_cbz(scratch, f"Chapter {int(c)}.cbz")

            with patch.object(komga, "get_series_id", return_value="s-1"), \
                 patch.object(komga, "get_existing_books", return_value=[]), \
                 patch.object(scraper, "get_all_chapters",
                              return_value=[1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0]), \
                 patch.object(scraper, "download_chapter",
                              side_effect=_make_download_side_effect(scratch)), \
                 patch.object(komga, "import_books",
                              side_effect=_import_and_move) as imp, \
                 patch("time.sleep"):
                main._run(komga, scraper, mgr, scratch, "Test", "lib", False,
                          batch_size=5)

            # Expected: 1 batch of 5 (recovered), 1 batch of 2 (new downloads).
            batch_sizes = [len(call.args[1]) for call in imp.call_args_list]
            assert batch_sizes == [5, 2], batch_sizes
            # First batch must be the recovered ones (chapters 1-5).
            first_batch_files = imp.call_args_list[0].args[1]
            first_batch_chapters = sorted(int(f.stem.split()[1]) for f in first_batch_files)
            assert first_batch_chapters == [1, 2, 3, 4, 5]

    def test_recovered_files_only_flush_when_no_new_downloads(self):
        """If we recovered N < batch_size files and there are no new downloads,
        flush the partial batch anyway — otherwise we'd never import them."""
        with tempfile.TemporaryDirectory() as tmp:
            komga, scraper, mgr, scratch = _build_clients_and_scratch(tmp)
            _create_fake_cbz(scratch, "Chapter 1.cbz")
            _create_fake_cbz(scratch, "Chapter 2.cbz")

            with patch.object(komga, "get_series_id", return_value="s-1"), \
                 patch.object(komga, "get_existing_books", return_value=[]), \
                 patch.object(scraper, "get_all_chapters",
                              return_value=[1.0, 2.0]), \
                 patch.object(scraper, "download_chapter") as dl, \
                 patch.object(komga, "import_books",
                              side_effect=_import_and_move) as imp, \
                 patch("time.sleep"):
                main._run(komga, scraper, mgr, scratch, "Test", "lib", False,
                          batch_size=5)

            dl.assert_not_called()
            assert imp.call_count == 1
            assert len(imp.call_args.args[1]) == 2

    def test_max_downloads_per_run_caps_work(self):
        """When max_downloads_per_run is set, only that many fresh downloads
        happen per invocation. Remaining missing chapters wait for next run."""
        with tempfile.TemporaryDirectory() as tmp:
            komga, scraper, mgr, scratch = _build_clients_and_scratch(tmp)
            with patch.object(komga, "get_series_id", return_value="s-1"), \
                 patch.object(komga, "get_existing_books", return_value=[]), \
                 patch.object(scraper, "get_all_chapters",
                              return_value=[float(c) for c in range(1, 21)]), \
                 patch.object(scraper, "download_chapter",
                              side_effect=_make_download_side_effect(scratch)) as dl, \
                 patch.object(komga, "import_books",
                              side_effect=_import_and_move), \
                 patch("time.sleep"):
                main._run(komga, scraper, mgr, scratch, "Test", "lib", False,
                          batch_size=5, max_downloads_per_run=7)

            # Only 7 downloads should have been attempted, despite 20 missing.
            assert dl.call_count == 7
            downloaded = sorted(c.args[0] for c in dl.call_args_list)
            assert downloaded == [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0]

    def test_max_downloads_does_not_cap_recovered_imports(self):
        """Cap applies to downloads, NOT to already-recovered files.

        Critical: if a previous run downloaded 50 chapters but failed to
        import them, we should NOT artificially block them on next run just
        because max_downloads_per_run=5.
        """
        with tempfile.TemporaryDirectory() as tmp:
            komga, scraper, mgr, scratch = _build_clients_and_scratch(tmp)
            # Seed 10 recovered files.
            for c in range(1, 11):
                _create_fake_cbz(scratch, f"Chapter {c}.cbz")

            with patch.object(komga, "get_series_id", return_value="s-1"), \
                 patch.object(komga, "get_existing_books", return_value=[]), \
                 patch.object(scraper, "get_all_chapters",
                              return_value=[float(c) for c in range(1, 16)]), \
                 patch.object(scraper, "download_chapter",
                              side_effect=_make_download_side_effect(scratch)) as dl, \
                 patch.object(komga, "import_books",
                              side_effect=_import_and_move) as imp, \
                 patch("time.sleep"):
                main._run(komga, scraper, mgr, scratch, "Test", "lib", False,
                          batch_size=5, max_downloads_per_run=2)

            # Downloads capped at 2.
            assert dl.call_count == 2
            # All 10 recovered chapters should have been imported (in 2 batches of 5)
            # plus 1 batch of 2 fresh downloads = 3 total import calls.
            total_imported = sum(len(c.args[1]) for c in imp.call_args_list)
            assert total_imported == 12, \
                f"expected 10 recovered + 2 downloaded = 12 imported, got {total_imported}"

    def test_pending_files_carry_over_to_next_run(self):
        """If Komga doesn't move a batch within the polling timeout, those
        files remain in scratch (no exception, no retry within same run).
        Next invocation's recovery logic picks them up."""

        def stuck_import(series_id, file_paths, *args, **kwargs):
            # Accept the import but don't actually move the files.
            return True

        with tempfile.TemporaryDirectory() as tmp:
            komga, scraper, mgr, scratch = _build_clients_and_scratch(tmp)

            # Patch time.time to advance past the 300s deadline quickly.
            fake_clock = [0.0]
            def fake_time():
                fake_clock[0] += 100
                return fake_clock[0]

            with patch.object(komga, "get_series_id", return_value="s-1"), \
                 patch.object(komga, "get_existing_books", return_value=[]), \
                 patch.object(scraper, "get_all_chapters",
                              return_value=[1.0, 2.0]), \
                 patch.object(scraper, "download_chapter",
                              side_effect=_make_download_side_effect(scratch)), \
                 patch.object(komga, "import_books", side_effect=stuck_import), \
                 patch("time.sleep"), \
                 patch("time.time", side_effect=fake_time):
                main._run(komga, scraper, mgr, scratch, "Test", "lib", False,
                          batch_size=5)

            # The files are still in scratch (Komga never moved them).
            assert (scratch / "Chapter 1.cbz").exists()
            assert (scratch / "Chapter 2.cbz").exists()
