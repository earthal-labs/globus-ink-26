"""
Tests for kinematics.py: the sanity checks and worked examples from
docs/globus-logic.md. Run from anywhere with:
    python -m unittest discover -s tsup/tests -t tsup

Distributed under the GPL-3.0-or-later License. See LICENSE for details.
"""

import sys
import unittest
from math import radians, pi
from pathlib import Path

from numpy import array, eye
from numpy.testing import assert_allclose

# kinematics.py lives in tsup/, one level up from this tests/ package - add
# it to sys.path so `from kinematics import ...` resolves regardless of
# where this file is run from (tests/, tsup/, repo root, ...).
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from kinematics import (
    M, M_inv, wheel_rates, actual_omega, overdrive_rates,
    multiply, conjugate, rotate, from_axis_angle, normalize,
    latlon_to_body, shortest_arc,
)

SQRT2_2 = 2 ** 0.5 / 2


class TestWheelMatrix(unittest.TestCase):
    """M / M_inv - docs/globus-logic.md section 6.3."""

    def test_inverse(self):
        assert_allclose(M @ M_inv, eye(3), atol=1e-9)

    def test_pure_spin_all_wheels_equal(self):
        # omega = (0, 0, w): all three wheel speeds equal R*cos(alpha)*w
        v = M @ array([0, 0, 2.0])
        assert_allclose(v, [v[0]] * 3)

    def test_pure_tilt_ratio(self):
        # omega = (w, 0, 0): v proportional to (1, -0.5, -0.5)
        v = M @ array([2.0, 0, 0])
        assert_allclose(v / v[0], [1, -0.5, -0.5])


class TestWheelRates(unittest.TestCase):
    """wheel_rates / actual_omega - docs/globus-logic.md section 5.2."""

    def test_round_trip_close_to_original(self):
        ω = array([0.01, -0.02, 0.03])
        rates = wheel_rates(ω)
        self.assertTrue(str(rates.dtype).startswith("int"))
        # quantization means "close," not exact - see section 5.2
        assert_allclose(actual_omega(rates), ω, atol=1e-3)

    def test_zero_omega_gives_zero_rates(self):
        assert_allclose(wheel_rates(array([0, 0, 0])), [0, 0, 0])


class TestOverdriveRates(unittest.TestCase):
    def test_scales_and_preserves_sign(self):
        assert_allclose(overdrive_rates([10, -20, 0], scale=3, cap=1000), [30, -60, 0])

    def test_clamps_to_cap(self):
        assert_allclose(overdrive_rates([500, -500, 0], scale=3, cap=833), [833, -833, 0])

    def test_zeros_stay_zero(self):
        assert_allclose(overdrive_rates([0, 0, 0], scale=3), [0, 0, 0])

    def test_adaptive_boosts_tiny_rates_more_than_large(self):
        from kinematics import overdrive_scale
        small = overdrive_scale([20, -10, 0])
        large = overdrive_scale([250, -100, 50])
        self.assertGreater(small, large)
        self.assertAlmostEqual(large, 1.5, places=3)


class TestQuaternionHelpers(unittest.TestCase):
    """docs/globus-logic.md sections 2.2 and 2.4."""

    def test_q0_matches_worked_example(self):
        q0 = from_axis_angle(array([0, 1, 0]), -90)
        assert_allclose(q0, [SQRT2_2, 0, -SQRT2_2, 0], atol=1e-9)

    def test_q0_sends_gulf_of_guinea_to_zenith(self):
        q0 = from_axis_angle(array([0, 1, 0]), -90)
        assert_allclose(rotate(q0, array([1, 0, 0])), [0, 0, 1], atol=1e-9)

    def test_q0_sends_north_pole_to_minus_x(self):
        q0 = from_axis_angle(array([0, 1, 0]), -90)
        assert_allclose(rotate(q0, array([0, 0, 1])), [-1, 0, 0], atol=1e-9)

    def test_90_about_z_sends_x_to_y(self):
        q = from_axis_angle(array([0, 0, 1]), 90)
        assert_allclose(rotate(q, array([1, 0, 0])), [0, 1, 0], atol=1e-9)

    def test_conjugate_is_inverse_for_unit_quaternion(self):
        q = from_axis_angle(array([1, 1, 1]), 37)  # arbitrary axis/angle
        assert_allclose(multiply(q, conjugate(q)), [1, 0, 0, 0], atol=1e-9)

    def test_normalize_unit_vector_unchanged(self):
        v = array([1.0, 0, 0])
        assert_allclose(normalize(v), v)

    def test_normalize_scales_to_unit_length(self):
        n = normalize(array([3.0, 4.0, 0]))
        self.assertAlmostEqual(float((n ** 2).sum()) ** 0.5, 1.0)

    def test_normalize_zero_vector_unchanged(self):
        assert_allclose(normalize(array([0.0, 0.0, 0.0])), [0, 0, 0])


class TestLatLonToBody(unittest.TestCase):
    """docs/globus-logic.md section 2.3."""

    def test_origin(self):
        assert_allclose(latlon_to_body(0, 0), [1, 0, 0], atol=1e-9)

    def test_north_pole(self):
        assert_allclose(latlon_to_body(90, 0), [0, 0, 1], atol=1e-9)


class TestShortestArc(unittest.TestCase):
    """docs/globus-logic.md section 3."""

    def test_already_aligned(self):
        _, θ = shortest_arc(array([0, 0, 1]), array([0, 0, 1]))
        self.assertAlmostEqual(θ, 0.0)

    def test_quarter_turn(self):
        axis, θ = shortest_arc(array([1, 0, 0]), array([0, 0, 1]))
        self.assertAlmostEqual(θ, radians(90))
        self.assertAlmostEqual(float((axis ** 2).sum()) ** 0.5, 1.0)

    def test_nadir_returns_a_valid_axis_not_zero(self):
        axis, θ = shortest_arc(array([0, 0, -1]), array([0, 0, 1]))
        self.assertAlmostEqual(θ, radians(180))
        self.assertAlmostEqual(float((axis ** 2).sum()) ** 0.5, 1.0)


if __name__ == "__main__":
    unittest.main()
