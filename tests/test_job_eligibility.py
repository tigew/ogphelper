"""Tests for job eligibility logic."""

import pytest

from ogphelper.domain.models import (
    Associate,
    JobRole,
    Preference,
)


class TestJobEligibility:
    """Tests for Associate job eligibility methods."""

    def test_can_do_role_when_allowed_and_no_restriction(self):
        """Associate can do role when allowed by supervisor and no restriction."""
        associate = Associate(
            id="A001",
            name="Test",
            supervisor_allowed_roles={JobRole.PICKING, JobRole.GMD_SM},
            cannot_do_roles=set(),
        )
        assert associate.can_do_role(JobRole.PICKING) is True
        assert associate.can_do_role(JobRole.GMD_SM) is True

    def test_cannot_do_role_when_not_allowed_by_supervisor(self):
        """Associate cannot do role not allowed by supervisor."""
        associate = Associate(
            id="A001",
            name="Test",
            supervisor_allowed_roles={JobRole.PICKING},  # Only picking allowed
            cannot_do_roles=set(),
        )
        assert associate.can_do_role(JobRole.GMD_SM) is False
        assert associate.can_do_role(JobRole.BACKROOM) is False

    def test_cannot_do_role_when_in_cannot_do_list(self):
        """Associate cannot do role in cannot_do list."""
        associate = Associate(
            id="A001",
            name="Test",
            supervisor_allowed_roles=set(JobRole),  # All allowed
            cannot_do_roles={JobRole.BACKROOM},  # But cannot do backroom
        )
        assert associate.can_do_role(JobRole.BACKROOM) is False
        assert associate.can_do_role(JobRole.PICKING) is True

    def test_cannot_do_overrides_supervisor_allowed(self):
        """cannot_do_roles takes precedence over supervisor_allowed_roles."""
        associate = Associate(
            id="A001",
            name="Test",
            supervisor_allowed_roles={JobRole.BACKROOM, JobRole.PICKING},
            cannot_do_roles={JobRole.BACKROOM},  # Physically cannot do
        )
        # Supervisor allows it, but associate physically cannot do it
        assert associate.can_do_role(JobRole.BACKROOM) is False
        assert associate.can_do_role(JobRole.PICKING) is True

    def test_eligible_roles_returns_allowed_minus_cannot_do(self):
        """eligible_roles should return allowed roles minus cannot_do roles."""
        associate = Associate(
            id="A001",
            name="Test",
            supervisor_allowed_roles={
                JobRole.PICKING,
                JobRole.GMD_SM,
                JobRole.BACKROOM,
            },
            cannot_do_roles={JobRole.BACKROOM},
        )
        eligible = associate.eligible_roles()
        assert eligible == {JobRole.PICKING, JobRole.GMD_SM}

    def test_eligible_roles_empty_when_all_restricted(self):
        """eligible_roles should be empty if all roles restricted."""
        associate = Associate(
            id="A001",
            name="Test",
            supervisor_allowed_roles={JobRole.BACKROOM},
            cannot_do_roles={JobRole.BACKROOM},
        )
        eligible = associate.eligible_roles()
        assert eligible == set()

    def test_get_preference_returns_default_neutral(self):
        """get_preference should return NEUTRAL when not specified."""
        associate = Associate(
            id="A001",
            name="Test",
            supervisor_allowed_roles=set(JobRole),
            role_preferences={},  # No preferences set
        )
        assert associate.get_preference(JobRole.PICKING) == Preference.NEUTRAL
        assert associate.get_preference(JobRole.BACKROOM) == Preference.NEUTRAL

    def test_get_preference_returns_set_preference(self):
        """get_preference should return the set preference."""
        associate = Associate(
            id="A001",
            name="Test",
            supervisor_allowed_roles=set(JobRole),
            role_preferences={
                JobRole.PICKING: Preference.PREFER,
                JobRole.BACKROOM: Preference.AVOID,
            },
        )
        assert associate.get_preference(JobRole.PICKING) == Preference.PREFER
        assert associate.get_preference(JobRole.BACKROOM) == Preference.AVOID
        assert associate.get_preference(JobRole.GMD_SM) == Preference.NEUTRAL

    def test_preference_does_not_affect_eligibility(self):
        """Preferences are soft constraints and don't affect eligibility."""
        associate = Associate(
            id="A001",
            name="Test",
            supervisor_allowed_roles=set(JobRole),
            cannot_do_roles=set(),
            role_preferences={
                JobRole.BACKROOM: Preference.AVOID,  # Prefers not to do
            },
        )
        # Can still do backroom even though they prefer not to
        assert associate.can_do_role(JobRole.BACKROOM) is True
        # But preference is recorded
        assert associate.get_preference(JobRole.BACKROOM) == Preference.AVOID


class TestPreferenceEnum:
    """Tests for Preference enum."""

    def test_preference_values(self):
        """Preference values should be ordered: AVOID < NEUTRAL < PREFER."""
        assert Preference.AVOID.value < Preference.NEUTRAL.value
        assert Preference.NEUTRAL.value < Preference.PREFER.value

    def test_preference_comparison(self):
        """Can compare preferences by value."""
        assert Preference.PREFER.value > Preference.AVOID.value


class TestJobRoleEnum:
    """Tests for JobRole enum."""

    def test_all_roles_defined(self):
        """All expected roles should be defined."""
        roles = set(JobRole)
        assert JobRole.PICKING in roles
        assert JobRole.GMD_SM in roles
        assert JobRole.EXCEPTION_SM in roles
        assert JobRole.STAGING in roles
        assert JobRole.BACKROOM in roles

    def test_role_count(self):
        """Should have exactly 5 roles."""
        assert len(JobRole) == 5
