"""Tests for scheduling policies."""

import pytest

from ogphelper.domain.policies import (
    DefaultBreakPolicy,
    DefaultLunchPolicy,
    DefaultShiftPolicy,
)


class TestDefaultShiftPolicy:
    """Tests for DefaultShiftPolicy."""

    def test_default_min_work_minutes(self):
        """Minimum work time should be 4 hours (240 min)."""
        policy = DefaultShiftPolicy()
        assert policy.min_work_minutes() == 240

    def test_default_max_work_minutes(self):
        """Maximum work time should be 8 hours (480 min)."""
        policy = DefaultShiftPolicy()
        assert policy.max_work_minutes() == 480

    def test_custom_shift_bounds(self):
        """Custom shift bounds should be respected."""
        policy = DefaultShiftPolicy(min_work=180, max_work=540)
        assert policy.min_work_minutes() == 180
        assert policy.max_work_minutes() == 540

    def test_is_valid_work_duration_at_minimum(self):
        """4 hours should be valid."""
        policy = DefaultShiftPolicy()
        assert policy.is_valid_work_duration(240) is True

    def test_is_valid_work_duration_at_maximum(self):
        """8 hours should be valid."""
        policy = DefaultShiftPolicy()
        assert policy.is_valid_work_duration(480) is True

    def test_is_valid_work_duration_below_minimum(self):
        """Below 4 hours should be invalid."""
        policy = DefaultShiftPolicy()
        assert policy.is_valid_work_duration(239) is False

    def test_is_valid_work_duration_above_maximum(self):
        """Above 8 hours should be invalid."""
        policy = DefaultShiftPolicy()
        assert policy.is_valid_work_duration(481) is False

    def test_is_valid_work_duration_mid_range(self):
        """6 hours should be valid."""
        policy = DefaultShiftPolicy()
        assert policy.is_valid_work_duration(360) is True


class TestDefaultLunchPolicy:
    """Tests for DefaultLunchPolicy."""

    def test_no_lunch_under_6_hours(self):
        """Work < 6 hours should get no lunch."""
        policy = DefaultLunchPolicy()
        assert policy.get_lunch_duration(0) == 0
        assert policy.get_lunch_duration(240) == 0  # 4 hours
        assert policy.get_lunch_duration(300) == 0  # 5 hours
        assert policy.get_lunch_duration(359) == 0  # Just under 6 hours

    def test_short_lunch_at_6_hours(self):
        """Work >= 6 hours and < 6.5 hours should get 30-min lunch."""
        policy = DefaultLunchPolicy()
        assert policy.get_lunch_duration(360) == 30  # Exactly 6 hours
        assert policy.get_lunch_duration(375) == 30  # 6.25 hours
        assert policy.get_lunch_duration(389) == 30  # Just under 6.5 hours

    def test_long_lunch_at_6_5_hours(self):
        """Work >= 6.5 hours should get 60-min lunch."""
        policy = DefaultLunchPolicy()
        assert policy.get_lunch_duration(390) == 60  # Exactly 6.5 hours
        assert policy.get_lunch_duration(420) == 60  # 7 hours
        assert policy.get_lunch_duration(480) == 60  # 8 hours

    def test_custom_lunch_thresholds(self):
        """Custom thresholds should be respected."""
        policy = DefaultLunchPolicy(
            no_lunch_threshold=300,
            short_lunch_threshold=360,
            short_lunch_duration=20,
            long_lunch_duration=45,
        )
        assert policy.get_lunch_duration(299) == 0
        assert policy.get_lunch_duration(300) == 20
        assert policy.get_lunch_duration(359) == 20
        assert policy.get_lunch_duration(360) == 45

    def test_lunch_window_normal_day(self):
        """Lunch window should be ±30 min on normal days."""
        policy = DefaultLunchPolicy()
        # 8-hour shift (slots 0-36 with 4-slot lunch)
        earliest, latest = policy.get_lunch_window(
            shift_start_slot=0,
            shift_end_slot=36,
            lunch_slots=4,
            is_busy_day=False,
            slot_minutes=15,
        )
        # Should have some flexibility
        assert earliest < latest
        # Should be roughly centered
        assert earliest >= 4  # At least 1 hour into shift
        assert latest <= 28  # At least 1 hour before end

    def test_lunch_window_busy_day(self):
        """Lunch window should be wider on busy days."""
        policy = DefaultLunchPolicy()
        normal_early, normal_late = policy.get_lunch_window(
            shift_start_slot=0,
            shift_end_slot=36,
            lunch_slots=4,
            is_busy_day=False,
            slot_minutes=15,
        )
        busy_early, busy_late = policy.get_lunch_window(
            shift_start_slot=0,
            shift_end_slot=36,
            lunch_slots=4,
            is_busy_day=True,
            slot_minutes=15,
        )
        # Busy day should have wider or equal window
        assert (busy_late - busy_early) >= (normal_late - normal_early)


class TestDefaultBreakPolicy:
    """Tests for DefaultBreakPolicy."""

    def test_no_breaks_under_5_hours(self):
        """Work < 5 hours should get no breaks."""
        policy = DefaultBreakPolicy()
        assert policy.get_break_count(0) == 0
        assert policy.get_break_count(240) == 0  # 4 hours
        assert policy.get_break_count(299) == 0  # Just under 5 hours

    def test_one_break_at_5_hours(self):
        """Work >= 5 hours and < 8 hours should get 1 break."""
        policy = DefaultBreakPolicy()
        assert policy.get_break_count(300) == 1  # 5 hours
        assert policy.get_break_count(360) == 1  # 6 hours
        assert policy.get_break_count(420) == 1  # 7 hours
        assert policy.get_break_count(479) == 1  # Just under 8 hours

    def test_two_breaks_at_8_hours(self):
        """Work >= 8 hours should get 2 breaks."""
        policy = DefaultBreakPolicy()
        assert policy.get_break_count(480) == 2  # 8 hours
        assert policy.get_break_count(500) == 2  # Over 8 hours

    def test_default_break_duration(self):
        """Default break duration should be 15 minutes."""
        policy = DefaultBreakPolicy()
        assert policy.get_break_duration() == 15

    def test_custom_break_duration(self):
        """Custom break duration should be respected."""
        policy = DefaultBreakPolicy(break_duration=20)
        assert policy.get_break_duration() == 20

    def test_custom_break_thresholds(self):
        """Custom thresholds should be respected."""
        policy = DefaultBreakPolicy(
            one_break_threshold=240,
            two_break_threshold=420,
        )
        assert policy.get_break_count(239) == 0
        assert policy.get_break_count(240) == 1
        assert policy.get_break_count(419) == 1
        assert policy.get_break_count(420) == 2

    def test_break_target_positions_one_break(self):
        """Single break should target midpoint."""
        policy = DefaultBreakPolicy()
        targets = policy.get_break_target_positions(
            work_start_slot=0,
            work_end_slot=24,  # 6-hour shift
            break_count=1,
            lunch_start_slot=None,
            lunch_end_slot=None,
            slot_minutes=15,
        )
        assert len(targets) == 1
        # Should be around midpoint (slot 12)
        assert 8 <= targets[0] <= 16

    def test_break_target_positions_two_breaks(self):
        """Two breaks should target 1/3 and 2/3 points."""
        policy = DefaultBreakPolicy()
        targets = policy.get_break_target_positions(
            work_start_slot=0,
            work_end_slot=32,  # 8-hour shift
            break_count=2,
            lunch_start_slot=None,
            lunch_end_slot=None,
            slot_minutes=15,
        )
        assert len(targets) == 2
        # First around 1/3 (slot 10-11)
        assert 6 <= targets[0] <= 14
        # Second around 2/3 (slot 21-22)
        assert 18 <= targets[1] <= 26

    def test_break_positions_avoid_lunch(self):
        """Breaks should not target lunch period."""
        policy = DefaultBreakPolicy()
        targets = policy.get_break_target_positions(
            work_start_slot=0,
            work_end_slot=36,
            break_count=2,
            lunch_start_slot=14,
            lunch_end_slot=18,
            slot_minutes=15,
        )
        assert len(targets) == 2
        # Neither target should be during lunch
        for target in targets:
            assert target < 14 or target >= 18
