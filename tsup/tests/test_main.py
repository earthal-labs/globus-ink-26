"""
Tests for main.py: the pieces that don't need live hardware or network.
main() itself (the infinite control loop against real serial/network) is
deliberately not exercised here - that's an integration concern, not a
unit-testable one. Run from anywhere with:
    python -m unittest discover -s tsup/tests -t tsup

Distributed under the GPL-3.0-or-later License. See LICENSE for details.
"""

import sys
import unittest
from math import degrees
from pathlib import Path
from queue import Queue
from tempfile import TemporaryDirectory
from unittest.mock import patch, MagicMock

from numpy import array
from numpy.testing import assert_allclose

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import main


def patch_attr(target, name, value):
    """patch.object(...).start(), registered for cleanup correctly - unlike
    patch.object(...).start().stop, since .start() returns the *patched-in
    value*, not the patcher, so .stop lives on a separate reference here."""
    patcher = patch.object(target, name, value)
    patcher.start()
    return patcher.stop


class TestLoadState(unittest.TestCase):
    def setUp(self):
        self.tmpdir = TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)
        state_path = Path(self.tmpdir.name) / "state.json"
        self.state_path = state_path
        self.addCleanup(patch_attr(main, "STATE_PATH", state_path))

    def test_missing_file_returns_none(self):
        self.assertIsNone(main.load_state())

    def test_empty_file_returns_none(self):
        self.state_path.write_text("")
        self.assertIsNone(main.load_state())

    def test_malformed_json_returns_none(self):
        self.state_path.write_text("{not valid json")
        self.assertIsNone(main.load_state())

    def test_wrong_shape_returns_none(self):
        self.state_path.write_text("[1, 2, 3]")  # 3 elements, not 4
        self.assertIsNone(main.load_state())

    def test_valid_state_loads(self):
        self.state_path.write_text("[0.5, 0.5, 0.5, 0.5]")
        assert_allclose(main.load_state(), [0.5, 0.5, 0.5, 0.5])


class TestSaveState(unittest.TestCase):
    def setUp(self):
        self.tmpdir = TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)
        # nested, non-existent parent - exercises the mkdir(parents=True)
        state_path = Path(self.tmpdir.name) / "nested" / "state.json"
        self.state_path = state_path
        self.addCleanup(patch_attr(main, "STATE_PATH", state_path))

    def test_creates_parent_directory_and_file(self):
        main.save_state(array([1.0, 0, 0, 0]))
        self.assertTrue(self.state_path.exists())

    def test_no_leftover_tmp_file(self):
        main.save_state(array([1.0, 0, 0, 0]))
        self.assertFalse(self.state_path.with_suffix(".tmp").exists())

    def test_round_trip_through_load_state(self):
        q = array([0.1, 0.2, 0.3, 0.4])
        main.save_state(q)
        assert_allclose(main.load_state(), q)


class TestAlignRitual(unittest.TestCase):
    def test_returns_q0_and_persists_it(self):
        with TemporaryDirectory() as tmp, \
             patch.object(main, "STATE_PATH", Path(tmp) / "state.json"), \
             patch("builtins.input", return_value=""), \
             patch("builtins.print"):
            q0 = main.align_ritual_returning_q0()
            persisted = main.load_state()  # still inside the STATE_PATH patch

        expected = main.from_axis_angle(array([0, 1, 0]), -90)
        assert_allclose(q0, expected, atol=1e-9)
        assert_allclose(persisted, expected, atol=1e-9)


class TestTleFilename(unittest.TestCase):
    def test_uses_norad_id(self):
        self.assertEqual(main.tle_filename(25544), "25544.tle")

    def test_different_ids_give_different_filenames(self):
        self.assertNotEqual(main.tle_filename(1), main.tle_filename(2))


class TestLoadCachedTle(unittest.TestCase):
    """The caching decision logic (sec. 7), against a mocked Loader - no
    real network or Celestrak dependency."""

    def setUp(self):
        self.mock_loader = MagicMock()
        self.addCleanup(patch_attr(main, "loader", self.mock_loader))
        self.tmpdir = TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)

    def test_missing_cache_fetches_with_reload(self):
        self.mock_loader.path_to.return_value = str(Path(self.tmpdir.name) / "missing.tle")
        self.mock_loader.tle_file.return_value = ["sat"]

        result = main.load_cached_tle(12345)

        self.assertEqual(result, "sat")
        self.assertTrue(self.mock_loader.tle_file.call_args.kwargs["reload"])

    def test_fresh_cache_skips_network(self):
        cache_file = Path(self.tmpdir.name) / "12345.tle"
        cache_file.write_text("cached")
        self.mock_loader.path_to.return_value = str(cache_file)
        self.mock_loader.days_old.return_value = 0.1  # well under the limit
        self.mock_loader.tle_file.return_value = ["sat"]

        result = main.load_cached_tle(12345)

        self.assertEqual(result, "sat")
        self.mock_loader.tle_file.assert_called_once_with("12345.tle")

    def test_stale_cache_refreshes_successfully(self):
        cache_file = Path(self.tmpdir.name) / "12345.tle"
        cache_file.write_text("cached")
        self.mock_loader.path_to.return_value = str(cache_file)
        self.mock_loader.days_old.return_value = 5.0  # over the limit
        self.mock_loader.tle_file.return_value = ["fresh"]

        self.assertEqual(main.load_cached_tle(12345), "fresh")

    def test_stale_cache_falls_back_when_refresh_fails(self):
        cache_file = Path(self.tmpdir.name) / "12345.tle"
        cache_file.write_text("cached")
        self.mock_loader.path_to.return_value = str(cache_file)
        self.mock_loader.days_old.return_value = 5.0

        def tle_file(*args, **kwargs):
            if kwargs.get("reload"):
                raise ConnectionError("network down")
            return ["stale"]

        self.mock_loader.tle_file.side_effect = tle_file

        self.assertEqual(main.load_cached_tle(12345), "stale")


class TestSubpointLatLon(unittest.TestCase):
    def test_extracts_degrees(self):
        satellite = MagicMock()
        geo = satellite.at.return_value.subpoint.return_value
        geo.latitude.degrees = 12.5
        geo.longitude.degrees = -45.0

        self.assertEqual(main.subpoint_latlon(satellite, "some-time"), (12.5, -45.0))


class TestCrosshairLatLon(unittest.TestCase):
    def test_identity_quaternion_is_the_north_pole(self):
        # latlon_to_body(90, 0) = (0, 0, 1) = ZHAT, so with no rotation
        # applied the crosshair sits exactly on the pole.
        lat, _ = main.crosshair_latlon(array([1.0, 0, 0, 0]))
        self.assertAlmostEqual(lat, 90.0, places=6)

    def test_q0_puts_gulf_of_guinea_under_the_crosshair(self):
        # Mirrors test_kinematics.py's test_q0_sends_gulf_of_guinea_to_zenith:
        # q0 is defined so (0, 0) sits under the crosshair at the home position.
        q0 = main.from_axis_angle(array([0, 1, 0]), -90)
        lat, lon = main.crosshair_latlon(q0)
        self.assertAlmostEqual(lat, 0.0, places=6)
        self.assertAlmostEqual(lon, 0.0, places=6)

    def test_round_trips_with_latlon_to_body(self):
        # Build a q that puts an arbitrary point under the crosshair, then
        # confirm crosshair_latlon recovers that same point.
        target_lat, target_lon = 33.0, -118.0
        p_b = main.latlon_to_body(target_lat, target_lon)
        axis, theta = main.shortest_arc(p_b, main.ZHAT)
        q = main.from_axis_angle(axis, degrees(theta))

        lat, lon = main.crosshair_latlon(q)

        self.assertAlmostEqual(lat, target_lat, places=4)
        self.assertAlmostEqual(lon, target_lon, places=4)


class TestNowUtc(unittest.TestCase):
    def test_delegates_to_shared_timescale(self):
        with patch.object(main.ts, "now", return_value="sentinel") as mock_now:
            self.assertEqual(main.now_utc(), "sentinel")
            mock_now.assert_called_once()


class TestApplyCommands(unittest.TestCase):
    """The bridge command-queue dispatch (sec. "vzor bridge" in main.py) -
    against a plain dict `state` and a real Queue, no bridge/socket needed."""

    def setUp(self):
        self.state = {
            "q": main.from_axis_angle(array([0, 1, 0]), -90),  # q0
            "mode": "AUTO",
            "satellite": "original-satellite",
            "satellite_name": "ISS",
            "manual_lat": 0.0,
            "manual_lon": 0.0,
        }

    def apply(self, *commands):
        q = Queue()
        for cmd in commands:
            q.put(cmd)
        main.apply_commands(q, self.state)

    def test_goto_sets_manual_target_and_switches_to_manual(self):
        self.apply(("GOTO", 12.0, 34.0))
        self.assertEqual(self.state["mode"], "MANUAL")
        self.assertEqual((self.state["manual_lat"], self.state["manual_lon"]), (12.0, 34.0))

    def test_mode_switch_to_manual_seeds_from_current_crosshair(self):
        # At q0, the crosshair sits at (0, 0) - see TestCrosshairLatLon.
        self.apply(("MODE", "MANUAL"))
        self.assertEqual(self.state["mode"], "MANUAL")
        self.assertAlmostEqual(self.state["manual_lat"], 0.0, places=6)
        self.assertAlmostEqual(self.state["manual_lon"], 0.0, places=6)

    def test_mode_switch_to_manual_does_not_reseed_if_already_manual(self):
        self.state["mode"] = "MANUAL"
        self.state["manual_lat"], self.state["manual_lon"] = 5.0, 6.0
        self.apply(("MODE", "MANUAL"))
        # Already MANUAL - reseeding here would stomp an in-flight target.
        self.assertEqual((self.state["manual_lat"], self.state["manual_lon"]), (5.0, 6.0))

    def test_mode_switch_to_auto(self):
        self.state["mode"] = "MANUAL"
        self.apply(("MODE", "AUTO"))
        self.assertEqual(self.state["mode"], "AUTO")

    def test_track_ready_switches_satellite_and_mode(self):
        self.state["mode"] = "MANUAL"
        self.apply(("TRACK_READY", "ISS", "new-satellite-object"))
        self.assertEqual(self.state["mode"], "AUTO")
        self.assertEqual(self.state["satellite"], "new-satellite-object")
        self.assertEqual(self.state["satellite_name"], "ISS")

    def test_track_error_does_not_change_state(self):
        with patch("builtins.print"):
            self.apply(("TRACK_ERROR", "unknown satellite"))
        self.assertEqual(self.state["satellite"], "original-satellite")

    def test_disconnected_does_not_change_state(self):
        with patch("builtins.print"):
            self.apply(("DISCONNECTED",))
        self.assertEqual(self.state["mode"], "AUTO")

    def test_drains_multiple_queued_commands_in_order(self):
        self.apply(("GOTO", 1.0, 2.0), ("GOTO", 3.0, 4.0))
        self.assertEqual((self.state["manual_lat"], self.state["manual_lon"]), (3.0, 4.0))


if __name__ == "__main__":
    unittest.main()
