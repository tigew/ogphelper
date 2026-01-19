"""Command-line interface for OGP Helper scheduling tool."""

import argparse
import json
import random
import sys
from datetime import date, time, timedelta
from pathlib import Path
from typing import Optional

from ogphelper.domain.demand import (
    DemandCurve,
    DemandProfile,
    WeeklyDemand,
)
from ogphelper.domain.models import (
    Associate,
    Availability,
    DaysOffPattern,
    FairnessConfig,
    JobRole,
    Preference,
    ScheduleRequest,
    ShiftBlockConfig,
    ShiftBlockType,
    ShiftStartConfig,
    WeeklyScheduleRequest,
)
from ogphelper.output.pdf_generator import PDFGenerator
from ogphelper.scheduling.cpsat_solver import OptimizationMode, SolverConfig
from ogphelper.scheduling.demand_aware_scheduler import (
    DemandAwareConfig,
    DemandAwareWeeklyScheduler,
    SolverType,
)
from ogphelper.scheduling.scheduler import Scheduler
from ogphelper.scheduling.weekly_scheduler import WeeklyScheduler
from ogphelper.validation.validator import ScheduleValidator


def create_sample_associates(
    count: int = 10,
    schedule_dates: Optional[list[date]] = None,
    seed: Optional[int] = None,
    variety_level: str = "high",
) -> list[Associate]:
    """Create sample associates with diverse availability patterns for testing.

    Args:
        count: Number of associates to create.
        schedule_dates: List of dates for availability. If None, uses today only.
        seed: Random seed for reproducibility. If None, uses current time.
        variety_level: Level of variety - "low", "medium", or "high".
    """
    rng = random.Random(seed if seed is not None else 42)
    associates = []

    if schedule_dates is None:
        schedule_dates = [date.today()]

    # Sample names
    names = [
        "Alice", "Bob", "Carol", "David", "Eve", "Frank", "Grace", "Henry",
        "Ivy", "Jack", "Kate", "Leo", "Mia", "Noah", "Olivia", "Paul",
        "Quinn", "Rose", "Sam", "Tina", "Uma", "Victor", "Wendy", "Xavier",
        "Yara", "Zach", "Amy", "Ben", "Chloe", "Dan", "Emma", "Finn",
        "Gina", "Hugo", "Iris", "Jake", "Kim", "Luke", "Maya", "Nate",
    ]

    # Define diverse shift patterns (start_slot, end_slot, name)
    # Slots: 0=5AM, 4=6AM, 12=8AM, 20=10AM, 28=12PM, 36=2PM, 44=4PM, 52=6PM, 60=8PM, 68=10PM
    shift_patterns = [
        (0, 32, "early_short"),      # 5 AM - 1 PM (8 hrs)
        (0, 40, "early_long"),       # 5 AM - 3 PM (10 hrs)
        (4, 36, "morning"),          # 6 AM - 2 PM (8 hrs)
        (8, 40, "morning_flex"),     # 7 AM - 3 PM (8 hrs)
        (12, 44, "day_early"),       # 8 AM - 4 PM (8 hrs)
        (16, 48, "day_mid"),         # 9 AM - 5 PM (8 hrs)
        (20, 52, "day_late"),        # 10 AM - 6 PM (8 hrs)
        (24, 56, "swing_early"),     # 11 AM - 7 PM (8 hrs)
        (28, 60, "swing_mid"),       # 12 PM - 8 PM (8 hrs)
        (32, 64, "swing_late"),      # 1 PM - 9 PM (8 hrs)
        (36, 68, "closing_early"),   # 2 PM - 10 PM (8 hrs)
        (40, 68, "closing_mid"),     # 3 PM - 10 PM (7 hrs)
        (44, 68, "closing_late"),    # 4 PM - 10 PM (6 hrs)
        (0, 68, "full_day"),         # 5 AM - 10 PM (full availability)
        (12, 52, "school_hours"),    # 8 AM - 6 PM (school schedule)
        (0, 24, "early_only"),       # 5 AM - 11 AM (opener)
        (48, 68, "evening_only"),    # 5 PM - 10 PM (closer)
    ]

    # Days off patterns: (preferred_days_off, pattern_name)
    # Days: 0=Mon, 1=Tue, 2=Wed, 3=Thu, 4=Fri, 5=Sat, 6=Sun
    days_off_patterns = [
        ([5, 6], "weekend_off"),          # Sat-Sun off
        ([0, 1], "early_week_off"),        # Mon-Tue off
        ([1, 2], "tue_wed_off"),           # Tue-Wed off
        ([2, 3], "wed_thu_off"),           # Wed-Thu off
        ([3, 4], "thu_fri_off"),           # Thu-Fri off
        ([4, 5], "fri_sat_off"),           # Fri-Sat off
        ([6, 0], "sun_mon_off"),           # Sun-Mon off
        ([0, 3], "split_mon_thu"),         # Mon, Thu off
        ([2, 5], "split_wed_sat"),         # Wed, Sat off
        ([1, 4], "split_tue_fri"),         # Tue, Fri off
        ([0, 4], "bookend_mon_fri"),       # Mon, Fri off
        ([2, 6], "mid_late_week"),         # Wed, Sun off
        ([5,], "sat_only"),                # Sat off (works 6 days)
        ([6,], "sun_only"),                # Sun off (works 6 days)
        ([4,], "fri_only"),                # Fri off (works 6 days)
        ([], "no_fixed_off"),              # No fixed pattern (full availability)
    ]

    # Weekly hour targets: (max_daily, max_weekly, type_name)
    hour_targets = [
        (480, 2400, "full_time"),      # 8 hrs/day, 40 hrs/week
        (480, 2000, "full_time_cap"),  # 8 hrs/day, 33 hrs/week
        (360, 1800, "three_quarter"),  # 6 hrs/day, 30 hrs/week
        (480, 1600, "part_time_a"),    # 8 hrs/day, 26 hrs/week
        (360, 1200, "part_time_b"),    # 6 hrs/day, 20 hrs/week
        (480, 2400, "flex_full"),      # Standard full time
        (420, 2100, "moderate"),       # 7 hrs/day, 35 hrs/week
    ]

    # Role restrictions combinations
    role_restrictions = [
        (set(), "no_restrictions"),                           # Can do all
        ({JobRole.BACKROOM}, "no_backroom"),                  # No backroom
        ({JobRole.GMD_SM}, "no_gmd"),                         # No GMD
        ({JobRole.EXCEPTION_SM}, "no_exception"),             # No exception
        ({JobRole.STAGING}, "no_staging"),                    # No staging
        ({JobRole.BACKROOM, JobRole.GMD_SM}, "no_back_gmd"), # No backroom or GMD
        ({JobRole.STAGING, JobRole.BACKROOM}, "no_stg_back"),# No staging or backroom
        ({JobRole.GMD_SM, JobRole.EXCEPTION_SM}, "no_sm"),   # No supervisor roles
    ]

    # Role preference combinations
    preference_combos = [
        ({}, "neutral"),                                      # No preferences
        ({JobRole.PICKING: Preference.PREFER}, "prefer_picking"),
        ({JobRole.BACKROOM: Preference.PREFER}, "prefer_backroom"),
        ({JobRole.STAGING: Preference.PREFER}, "prefer_staging"),
        ({JobRole.BACKROOM: Preference.AVOID}, "avoid_backroom"),
        ({JobRole.STAGING: Preference.AVOID}, "avoid_staging"),
        ({JobRole.PICKING: Preference.AVOID}, "avoid_picking"),
        ({JobRole.GMD_SM: Preference.PREFER}, "prefer_gmd"),
        ({JobRole.EXCEPTION_SM: Preference.PREFER}, "prefer_exception"),
        ({JobRole.PICKING: Preference.PREFER, JobRole.BACKROOM: Preference.AVOID}, "pick_not_back"),
        ({JobRole.STAGING: Preference.PREFER, JobRole.PICKING: Preference.AVOID}, "stg_not_pick"),
        ({JobRole.GMD_SM: Preference.AVOID, JobRole.EXCEPTION_SM: Preference.AVOID}, "avoid_sm"),
        ({JobRole.BACKROOM: Preference.PREFER, JobRole.STAGING: Preference.PREFER}, "prefer_back_stg"),
    ]

    for i in range(count):
        name = names[i % len(names)]
        if i >= len(names):
            name = f"{name}{i // len(names) + 1}"

        # Select patterns with variety based on level
        if variety_level == "high":
            shift_pattern = rng.choice(shift_patterns)
            days_off_pattern = rng.choice(days_off_patterns)
            hour_target = rng.choice(hour_targets)
            role_restriction = rng.choice(role_restrictions)
            preference_combo = rng.choice(preference_combos)
        elif variety_level == "medium":
            # Less variety - use first half of options
            shift_pattern = rng.choice(shift_patterns[:10])
            days_off_pattern = rng.choice(days_off_patterns[:8])
            hour_target = rng.choice(hour_targets[:4])
            role_restriction = rng.choice(role_restrictions[:4])
            preference_combo = rng.choice(preference_combos[:6])
        else:  # low
            shift_pattern = shift_patterns[i % 5]
            days_off_pattern = days_off_patterns[i % 4]
            hour_target = hour_targets[i % 3]
            role_restriction = role_restrictions[i % 2]
            preference_combo = preference_combos[i % 3]

        start_slot, end_slot, _ = shift_pattern
        preferred_days_off, _ = days_off_pattern
        max_daily, max_weekly, _ = hour_target
        cannot_do, _ = role_restriction
        preferences, _ = preference_combo

        # Add some per-associate variation to shift times
        if variety_level in ("high", "medium"):
            start_jitter = rng.randint(-2, 2) * 2  # -4 to +4 slots (1 hour)
            end_jitter = rng.randint(-2, 2) * 2
            start_slot = max(0, min(60, start_slot + start_jitter))
            end_slot = max(start_slot + 16, min(68, end_slot + end_jitter))

        # Build availability for each date
        availability = {}
        for d in schedule_dates:
            weekday = d.weekday()

            # Check if this day should be off based on pattern
            is_day_off = weekday in preferred_days_off

            # Add some randomization to days off for high variety
            if variety_level == "high" and not is_day_off:
                # 15% chance of a random day off
                if rng.random() < 0.15:
                    is_day_off = True

            if is_day_off:
                availability[d] = Availability.off_day()
            else:
                # Add some daily variation for high variety
                if variety_level == "high" and rng.random() < 0.2:
                    # Vary the shift slightly for this day
                    day_start = max(0, start_slot + rng.randint(-4, 4))
                    day_end = max(day_start + 16, min(68, end_slot + rng.randint(-4, 4)))
                    availability[d] = Availability(start_slot=day_start, end_slot=day_end)
                else:
                    availability[d] = Availability(start_slot=start_slot, end_slot=end_slot)

        # All roles allowed by default, then apply restrictions
        allowed_roles = set(JobRole)

        associate = Associate(
            id=f"A{i + 1:03d}",
            name=name,
            availability=availability,
            max_minutes_per_day=max_daily,
            max_minutes_per_week=max_weekly,
            supervisor_allowed_roles=allowed_roles,
            cannot_do_roles=set(cannot_do),
            role_preferences=dict(preferences),
        )
        associates.append(associate)

    return associates


def create_realistic_associates(
    shift_start_configs: list[ShiftStartConfig],
    schedule_dates: list[date],
    seed: Optional[int] = None,
) -> list[Associate]:
    """Create associates that match a realistic shift start distribution.

    Each associate's availability is set to match one of the shift start times,
    with appropriate end times (8-hour shifts by default). Most associates
    work 5 days/week (full-time) with only 2 days off.

    Args:
        shift_start_configs: List of shift start configurations with target counts.
        schedule_dates: List of dates for availability.
        seed: Random seed for reproducibility.

    Returns:
        List of associates configured to match the distribution.
    """
    rng = random.Random(seed if seed is not None else 42)
    associates = []

    # Sample names
    names = [
        "Alice", "Bob", "Carol", "David", "Eve", "Frank", "Grace", "Henry",
        "Ivy", "Jack", "Kate", "Leo", "Mia", "Noah", "Olivia", "Paul",
        "Quinn", "Rose", "Sam", "Tina", "Uma", "Victor", "Wendy", "Xavier",
        "Yara", "Zach", "Amy", "Ben", "Chloe", "Dan", "Emma", "Finn",
        "Gina", "Hugo", "Iris", "Jake", "Kim", "Luke", "Maya", "Nate",
    ]

    # Days off patterns - exactly 2 days off per week for full-time
    # Shuffle the patterns to distribute days off across the week
    days_off_patterns = [
        [5, 6],  # Sat-Sun off
        [0, 1],  # Mon-Tue off
        [1, 2],  # Tue-Wed off
        [2, 3],  # Wed-Thu off
        [3, 4],  # Thu-Fri off
        [4, 5],  # Fri-Sat off
        [6, 0],  # Sun-Mon off
        [0, 3],  # Mon, Thu off (split)
        [2, 5],  # Wed, Sat off (split)
        [1, 4],  # Tue, Fri off (split)
        [0, 4],  # Mon, Fri off (split)
        [2, 6],  # Wed, Sun off (split)
        [1, 5],  # Tue, Sat off (split)
        [3, 6],  # Thu, Sun off (split)
    ]

    # Role restrictions for variety (most have no restrictions)
    role_restrictions = [
        set(),  # No restrictions (most common)
        set(),
        set(),
        set(),
        set(),
        {JobRole.BACKROOM},
        {JobRole.GMD_SM},
        {JobRole.STAGING},
        {JobRole.SR},
    ]

    # Role preferences for variety
    preference_combos = [
        {},  # Neutral (most common)
        {},
        {},
        {},
        {JobRole.PICKING: Preference.PREFER},
        {JobRole.BACKROOM: Preference.PREFER},
        {JobRole.STAGING: Preference.PREFER},
        {JobRole.BACKROOM: Preference.AVOID},
        {JobRole.SR: Preference.PREFER},
    ]

    associate_idx = 0

    # Create associates for each shift start time
    for cfg in shift_start_configs:
        for _ in range(cfg.target_count):
            name = names[associate_idx % len(names)]
            if associate_idx >= len(names):
                name = f"{name}{associate_idx // len(names) + 1}"

            # Calculate shift end (8 hours work + 1 hour lunch = 36 slots for 15-min slots)
            start_slot = cfg.start_slot
            end_slot = min(start_slot + 36, 68)  # Cap at 10 PM

            # For closers, extend availability to end of day
            if start_slot >= 36:  # 2 PM or later
                end_slot = 68  # Available until 10 PM

            # Select days off pattern - rotate through patterns to ensure coverage
            # Use index-based selection first, then randomize for duplicates
            # Skip days-off patterns for single-day schedules so all associates are available
            if len(schedule_dates) == 1:
                preferred_days_off = []  # No days off for single-day demo
            else:
                pattern_idx = associate_idx % len(days_off_patterns)
                preferred_days_off = days_off_patterns[pattern_idx]

            cannot_do = rng.choice(role_restrictions)
            preferences = rng.choice(preference_combos)

            # Build availability for each date - most days should be available
            availability = {}
            for d in schedule_dates:
                weekday = d.weekday()
                is_day_off = weekday in preferred_days_off

                if is_day_off:
                    availability[d] = Availability.off_day()
                else:
                    # Use exact start time to match shift start configs
                    availability[d] = Availability(
                        start_slot=start_slot, end_slot=end_slot
                    )

            allowed_roles = set(JobRole)

            associate = Associate(
                id=f"A{associate_idx + 1:03d}",
                name=name,
                availability=availability,
                max_minutes_per_day=480,  # 8 hours
                max_minutes_per_week=2400,  # 40 hours
                supervisor_allowed_roles=allowed_roles,
                cannot_do_roles=set(cannot_do),
                role_preferences=dict(preferences),
            )
            associates.append(associate)
            associate_idx += 1

    return associates


def run_demo(
    associate_count: int = 10,
    output_path: Optional[str] = None,
    realistic: bool = False,
    seed: Optional[int] = None,
) -> None:
    """Run a demo schedule generation.

    Args:
        associate_count: Number of associates to schedule.
        output_path: Optional PDF file path for output.
        realistic: Use realistic shift distribution.
        seed: Random seed for reproducibility.
    """
    schedule_date = date.today()

    # Create shift start configs for realistic mode
    if realistic:
        base_distribution = ShiftStartConfig.create_standard_distribution()
        if associate_count != 47:  # 47 is the standard total
            shift_start_configs = ShiftStartConfig.scale_distribution(
                base_distribution, associate_count
            )
        else:
            shift_start_configs = base_distribution

        # Create associates matching the distribution
        associates = create_realistic_associates(
            shift_start_configs, [schedule_date], seed=seed
        )
        associate_count = len(associates)

        print(f"Generating demo schedule for {associate_count} associates...")
        print(f"  Mode: REALISTIC (real shift distribution)")
        print(f"  Shift starts: ", end="")
        for cfg in shift_start_configs:
            print(f"{cfg.label}:{cfg.target_count} ", end="")
        print()
    else:
        print(f"Generating demo schedule for {associate_count} associates...")

        # Create sample associates
        associates = create_sample_associates(
            associate_count, [schedule_date], seed=seed
        )

    # Create schedule request
    request = ScheduleRequest(
        schedule_date=schedule_date,
        associates=associates,
        is_busy_day=False,
    )

    # Generate schedule
    scheduler = Scheduler()
    schedule, stats = scheduler.generate_schedule_with_stats(request)

    # Validate
    validator = ScheduleValidator()
    associates_map = {a.id: a for a in associates}
    result = validator.validate(schedule, request, associates_map)

    # Print results
    print(f"\nSchedule generated for {schedule.schedule_date}")
    print(f"  Scheduled: {stats['scheduled_associates']}/{stats['total_associates']} associates")
    print(f"  Total work hours: {stats['total_work_minutes'] / 60:.1f}")
    print(f"  Coverage: min={stats['min_coverage']}, max={stats['max_coverage']}, "
          f"avg={stats['avg_coverage']:.1f}")

    if result.is_valid:
        print("\n  Validation: PASSED")
    else:
        print(f"\n  Validation: FAILED ({len(result.errors)} errors)")
        for error in result.errors[:5]:
            print(f"    - {error}")
        if len(result.errors) > 5:
            print(f"    ... and {len(result.errors) - 5} more errors")

    # Generate PDF if requested
    if output_path:
        print(f"\nGenerating PDF: {output_path}")
        generator = PDFGenerator()
        generator.generate(schedule, associates_map, output_path)
        print("  PDF created successfully!")


def run_weekly_demo(
    associate_count: int = 10,
    days: int = 7,
    days_off_pattern: str = "two_consecutive",
    output_path: Optional[str] = None,
    variety_level: str = "high",
    seed: Optional[int] = None,
    morning_limit: Optional[int] = None,
    day_limit: Optional[int] = None,
    closing_limit: Optional[int] = None,
    realistic: bool = False,
) -> None:
    """Run a weekly schedule generation demo.

    Args:
        associate_count: Number of associates to schedule.
        days: Number of days to schedule (default 7 for a week).
        days_off_pattern: Pattern for days off (none, two_consecutive, one_weekend_day).
        output_path: Optional PDF file path for output.
        variety_level: Level of variety in schedules (low, medium, high).
        seed: Random seed for reproducibility.
        morning_limit: Max associates starting in morning block (5 AM - 11 AM).
        day_limit: Max associates starting in day block (10 AM - 4 PM).
        closing_limit: Max associates starting in closing block (3 PM - 10 PM).
        realistic: Use realistic shift distribution (47 associates standard).
    """
    # Generate date range
    start_date = date.today()
    # Adjust to start on Monday if scheduling a full week
    if days >= 7:
        days_until_monday = (7 - start_date.weekday()) % 7
        if days_until_monday > 0:
            start_date = start_date + timedelta(days=days_until_monday)
    end_date = start_date + timedelta(days=days - 1)

    schedule_dates = [start_date + timedelta(days=i) for i in range(days)]

    # Create shift start configs for realistic mode
    shift_start_configs = None
    if realistic:
        base_distribution = ShiftStartConfig.create_standard_distribution()
        if associate_count != 47:  # 47 is the standard total
            shift_start_configs = ShiftStartConfig.scale_distribution(
                base_distribution, associate_count
            )
        else:
            shift_start_configs = base_distribution

        # Create associates matching the distribution
        associates = create_realistic_associates(
            shift_start_configs, schedule_dates, seed=seed
        )
        associate_count = len(associates)

        print(f"Generating weekly schedule for {associate_count} associates over {days} days...")
        print(f"  Mode: REALISTIC (real shift distribution)")
        print(f"  Shift starts: ", end="")
        for cfg in shift_start_configs:
            print(f"{cfg.label}:{cfg.target_count} ", end="")
        print()
    else:
        print(f"Generating weekly schedule for {associate_count} associates over {days} days...")
        print(f"  Variety level: {variety_level}, Seed: {seed if seed else 'random'}")

        # Create sample associates with weekly availability
        associates = create_sample_associates(
            associate_count, schedule_dates, seed=seed, variety_level=variety_level
        )

    # Parse days-off pattern
    # In realistic mode, days off are already built into associate availability
    if realistic:
        pattern = DaysOffPattern.NONE
        required_days_off = 0
    else:
        pattern_map = {
            "none": DaysOffPattern.NONE,
            "two_consecutive": DaysOffPattern.TWO_CONSECUTIVE,
            "one_weekend_day": DaysOffPattern.ONE_WEEKEND_DAY,
            "every_other_day": DaysOffPattern.EVERY_OTHER_DAY,
        }
        pattern = pattern_map.get(days_off_pattern, DaysOffPattern.TWO_CONSECUTIVE)
        required_days_off = 2

    # Create shift block configurations if limits specified (only for non-realistic mode)
    shift_block_configs = None
    if not realistic and any(x is not None for x in [morning_limit, day_limit, closing_limit]):
        blocks = ShiftBlockConfig.create_default_blocks()
        shift_block_configs = []
        for block in blocks:
            if block.block_type == ShiftBlockType.MORNING and morning_limit is not None:
                block = ShiftBlockConfig(
                    block_type=block.block_type,
                    start_slot=block.start_slot,
                    end_slot=block.end_slot,
                    max_associates=morning_limit,
                    target_associates=morning_limit,
                )
            elif block.block_type == ShiftBlockType.DAY and day_limit is not None:
                block = ShiftBlockConfig(
                    block_type=block.block_type,
                    start_slot=block.start_slot,
                    end_slot=block.end_slot,
                    max_associates=day_limit,
                    target_associates=day_limit,
                )
            elif block.block_type == ShiftBlockType.CLOSING and closing_limit is not None:
                block = ShiftBlockConfig(
                    block_type=block.block_type,
                    start_slot=block.start_slot,
                    end_slot=block.end_slot,
                    max_associates=closing_limit,
                    target_associates=closing_limit,
                )
            shift_block_configs.append(block)

        print(f"  Shift block limits: morning={morning_limit or 'unlimited'}, "
              f"day={day_limit or 'unlimited'}, closing={closing_limit or 'unlimited'}")

    # Create weekly schedule request
    # Note: In realistic mode, shift_start_configs are used to create associates but
    # NOT passed as constraints to the scheduler, since the targets represent total
    # workforce distribution, not per-day constraints (associates have days off)
    # Also in realistic mode, use high max_hours_variance since "unfairness" is due
    # to actual availability constraints (built-in days off), not scheduler decisions
    request = WeeklyScheduleRequest(
        start_date=start_date,
        end_date=end_date,
        associates=associates,
        days_off_pattern=pattern,
        required_days_off=required_days_off,
        fairness_config=FairnessConfig(
            weight_hours_balance=0.7,
            weight_days_balance=0.3,
            max_hours_variance=960.0 if realistic else 120.0,  # 16h variance for realistic, 2h otherwise
        ),
        shift_block_configs=shift_block_configs,
        shift_start_configs=None if realistic else shift_start_configs,
    )

    # Generate schedule
    scheduler = WeeklyScheduler()
    schedule, stats = scheduler.generate_schedule_with_stats(request, step_slots=4)

    # Validate
    validator = ScheduleValidator()
    associates_map = {a.id: a for a in associates}
    result = validator.validate_weekly_schedule(schedule, request, associates_map)

    # Print results
    print(f"\n{'=' * 60}")
    print(f"Weekly Schedule: {start_date} to {end_date}")
    print(f"{'=' * 60}")
    print(f"  Total Associates: {stats['total_associates']}")
    print(f"  Total Shifts: {stats['total_shifts']}")
    print(f"  Total Work Hours: {stats['total_work_hours']:.1f}")
    print(f"  Avg Hours/Associate: {stats['avg_hours_per_associate']:.1f}")
    print(f"  Avg Days/Associate: {stats['avg_days_per_associate']:.1f}")

    # Print daily coverage summary
    print(f"\nDaily Coverage:")
    for d, coverage in sorted(stats.get('coverage_by_day', {}).items()):
        day_name = d.strftime("%A")[:3]
        print(f"  {d} ({day_name}): min={coverage['min']}, max={coverage['max']}, "
              f"avg={coverage['avg']:.1f}")

    # Print fairness metrics
    if stats['fairness_metrics']:
        metrics = stats['fairness_metrics']
        print(f"\nFairness Metrics:")
        print(f"  Avg Hours: {metrics.avg_hours:.1f}")
        print(f"  Std Dev: {metrics.hours_std_dev:.1f}")
        print(f"  Min Hours: {metrics.min_hours:.1f}")
        print(f"  Max Hours: {metrics.max_hours:.1f}")
        print(f"  Fairness Score: {metrics.fairness_score:.1f}/100")

    # Print validation results
    if result.is_valid:
        print(f"\nValidation: PASSED")
    else:
        print(f"\nValidation: FAILED ({len(result.errors)} errors)")
        for error in result.errors[:5]:
            print(f"    - {error}")
        if len(result.errors) > 5:
            print(f"    ... and {len(result.errors) - 5} more errors")

    if result.warnings:
        print(f"\nWarnings ({len(result.warnings)}):")
        for warning in result.warnings[:3]:
            print(f"    - {warning}")
        if len(result.warnings) > 3:
            print(f"    ... and {len(result.warnings) - 3} more warnings")

    # Print sample associate schedules
    print(f"\nSample Associate Schedules:")
    sample_associates = list(associates)[:3]
    for associate in sample_associates:
        days_worked = schedule.get_associate_days_worked(associate.id)
        hours = schedule.get_associate_weekly_minutes(associate.id) / 60.0
        days_off = schedule.get_associate_days_off(associate.id)
        days_off_str = ", ".join(d.strftime("%a") for d in sorted(days_off)[:3])
        if len(days_off) > 3:
            days_off_str += f", +{len(days_off) - 3} more"
        print(f"  {associate.name} ({associate.id}): {days_worked} days, "
              f"{hours:.1f}h, off: {days_off_str}")

    # Generate PDF if requested
    if output_path:
        print(f"\nGenerating PDF: {output_path}")
        generator = PDFGenerator()
        generator.generate_weekly(schedule, associates_map, output_path)
        print("  PDF created successfully!")


def run_demand_demo(
    associate_count: int = 10,
    days: int = 7,
    solver_type: str = "hybrid",
    optimization_mode: str = "balanced",
    time_limit: float = 30.0,
    demand_profile: str = "weekday",
    output_path: Optional[str] = None,
) -> None:
    """Run a demand-aware schedule generation demo.

    Args:
        associate_count: Number of associates to schedule.
        days: Number of days to schedule.
        solver_type: Solver to use (heuristic, cpsat, hybrid).
        optimization_mode: Optimization objective (maximize_coverage, match_demand, etc.).
        time_limit: CP-SAT solver time limit in seconds.
        demand_profile: Demand profile to use (weekday, weekend, high_volume).
        output_path: Optional PDF file path for output.
    """
    print(f"Generating demand-aware schedule for {associate_count} associates over {days} days...")
    print(f"  Solver: {solver_type}, Optimization: {optimization_mode}")

    # Generate date range
    start_date = date.today()
    if days >= 7:
        days_until_monday = (7 - start_date.weekday()) % 7
        if days_until_monday > 0:
            start_date = start_date + timedelta(days=days_until_monday)
    end_date = start_date + timedelta(days=days - 1)

    schedule_dates = [start_date + timedelta(days=i) for i in range(days)]

    # Create sample associates
    associates = create_sample_associates(associate_count, schedule_dates)

    # Create demand profiles
    profile_map = {
        "weekday": DemandProfile.create_weekday_profile(),
        "weekend": DemandProfile.create_weekend_profile(),
        "high_volume": DemandProfile.create_high_volume_profile(),
    }
    weekday_profile = profile_map.get(demand_profile, profile_map["weekday"])
    weekend_profile = profile_map.get("weekend", profile_map["weekend"])

    # Scale profiles based on associate count
    scale_factor = max(0.5, min(2.0, associate_count / 10.0))
    scaled_weekday = DemandProfile(
        name=weekday_profile.name,
        description=weekday_profile.description,
        hourly_pattern={
            h: max(1, int(v * scale_factor))
            for h, v in weekday_profile.hourly_pattern.items()
        },
        priority_windows=weekday_profile.priority_windows,
    )
    scaled_weekend = DemandProfile(
        name=weekend_profile.name,
        description=weekend_profile.description,
        hourly_pattern={
            h: max(1, int(v * scale_factor))
            for h, v in weekend_profile.hourly_pattern.items()
        },
        priority_windows=weekend_profile.priority_windows,
    )

    # Create weekly demand
    weekly_demand = WeeklyDemand.create_standard_week(
        start_date,
        weekday_profile=scaled_weekday,
        weekend_profile=scaled_weekend,
    )

    # Create request
    request = WeeklyScheduleRequest(
        start_date=start_date,
        end_date=end_date,
        associates=associates,
        days_off_pattern=DaysOffPattern.TWO_CONSECUTIVE,
        required_days_off=2,
        fairness_config=FairnessConfig(
            weight_hours_balance=0.7,
            weight_days_balance=0.3,
        ),
    )

    # Configure solver
    solver_config = SolverConfig(
        time_limit_seconds=time_limit,
        optimization_mode=OptimizationMode(optimization_mode),
    )

    config = DemandAwareConfig(
        solver_type=SolverType(solver_type),
        solver_config=solver_config,
        weekly_demand=weekly_demand,
        track_demand_metrics=True,
    )

    # Generate schedule
    scheduler = DemandAwareWeeklyScheduler(config=config)
    result = scheduler.generate_schedule(request, weekly_demand, step_slots=4)

    # Validate
    validator = ScheduleValidator()
    associates_map = {a.id: a for a in associates}
    validation_result = validator.validate_weekly_schedule(
        result.schedule, request, associates_map
    )

    # Print results
    print(f"\n{'=' * 60}")
    print(f"Demand-Aware Weekly Schedule: {start_date} to {end_date}")
    print(f"{'=' * 60}")

    summary = result.get_summary()
    print(f"  Total Shifts: {summary['total_shifts']}")
    print(f"  Overall Demand Match: {summary['overall_match_score']:.1f}%")
    if summary['fairness_score']:
        print(f"  Fairness Score: {summary['fairness_score']:.1f}/100")

    # Print daily demand matching
    print(f"\nDaily Demand Matching:")
    for d, metrics in sorted(result.demand_metrics.items()):
        day_name = d.strftime("%A")[:3]
        undercov = metrics.undercoverage_minutes
        match = metrics.match_score
        print(f"  {d} ({day_name}): {match:.1f}% match, "
              f"undercoverage: {undercov:.0f} min")

    # Print solver stats
    print(f"\nSolver Statistics:")
    for d, stats in sorted(result.solver_stats.items()):
        day_name = d.strftime("%a")
        solver_used = stats.get('used', stats.get('method', 'unknown'))
        solve_time = stats.get('solve_time', stats.get('cpsat_time', 0))
        if solve_time:
            print(f"  {d} ({day_name}): {solver_used}, {solve_time:.2f}s")
        else:
            print(f"  {d} ({day_name}): {solver_used}")

    # Print coverage summary
    print(f"\nDaily Coverage Summary:")
    for d in sorted(result.schedule.day_schedules.keys()):
        day_schedule = result.schedule.day_schedules[d]
        timeline = day_schedule.get_coverage_timeline()
        if timeline:
            day_name = d.strftime("%a")
            print(f"  {d} ({day_name}): min={min(timeline)}, max={max(timeline)}, "
                  f"avg={sum(timeline)/len(timeline):.1f}")

    # Print fairness metrics
    metrics = result.schedule.fairness_metrics
    if metrics:
        print(f"\nFairness Metrics:")
        print(f"  Avg Hours: {metrics.avg_hours:.1f}")
        print(f"  Std Dev: {metrics.hours_std_dev:.1f}")
        print(f"  Range: {metrics.min_hours:.1f} - {metrics.max_hours:.1f}")
        print(f"  Fairness Score: {metrics.fairness_score:.1f}/100")

    # Validation
    if validation_result.is_valid:
        print(f"\nValidation: PASSED")
    else:
        print(f"\nValidation: FAILED ({len(validation_result.errors)} errors)")
        for error in validation_result.errors[:5]:
            print(f"    - {error}")

    # Generate PDF if requested
    if output_path:
        print(f"\nGenerating PDF: {output_path}")
        generator = PDFGenerator()
        generator.generate_weekly(result.schedule, associates_map, output_path)
        print("  PDF created successfully!")


def main() -> int:
    """Main entry point for CLI."""
    parser = argparse.ArgumentParser(
        description="OGP Helper - Workforce Scheduling Tool",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s demo                       Run daily demo with 10 associates
  %(prog)s demo --count 30            Run daily demo with 30 associates
  %(prog)s demo --realistic           Use real shift distribution
  %(prog)s demo --realistic --count 50  Scale realistic distribution to 50
  %(prog)s demo --output sched.pdf    Generate PDF output

  %(prog)s weekly-demo                Run weekly demo with 10 associates
  %(prog)s weekly-demo --realistic    Use real shift distribution (47 associates)
  %(prog)s weekly-demo --realistic --count 80  Scale distribution to 80 associates
  %(prog)s weekly-demo --variety high Diverse random schedules
  %(prog)s weekly-demo --morning-limit 30 --closing-limit 20  Limit shifts per block

  %(prog)s demand-demo                Run demand-aware demo
  %(prog)s demand-demo --solver cpsat Use CP-SAT solver
  %(prog)s demand-demo --profile high_volume  High-volume demand
        """,
    )

    subparsers = parser.add_subparsers(dest="command", help="Command to run")

    # Demo command
    demo_parser = subparsers.add_parser("demo", help="Run daily demo schedule generation")
    demo_parser.add_argument(
        "--count", "-c",
        type=int,
        default=10,
        help="Number of associates to generate (default: 10)",
    )
    demo_parser.add_argument(
        "--output", "-o",
        type=str,
        help="Output PDF file path",
    )
    demo_parser.add_argument(
        "--realistic", "-r",
        action="store_true",
        help="Use realistic shift distribution",
    )
    demo_parser.add_argument(
        "--seed", "-S",
        type=int,
        help="Random seed for reproducibility",
    )

    # Weekly demo command
    weekly_parser = subparsers.add_parser(
        "weekly-demo",
        help="Run weekly demo schedule generation"
    )
    weekly_parser.add_argument(
        "--count", "-c",
        type=int,
        default=10,
        help="Number of associates to generate (default: 10)",
    )
    weekly_parser.add_argument(
        "--days", "-d",
        type=int,
        default=7,
        help="Number of days to schedule (default: 7)",
    )
    weekly_parser.add_argument(
        "--pattern", "-p",
        type=str,
        default="two_consecutive",
        choices=["none", "two_consecutive", "one_weekend_day", "every_other_day"],
        help="Days-off pattern (default: two_consecutive)",
    )
    weekly_parser.add_argument(
        "--output", "-o",
        type=str,
        help="Output PDF file path",
    )
    weekly_parser.add_argument(
        "--variety", "-v",
        type=str,
        default="high",
        choices=["low", "medium", "high"],
        help="Variety level for associates (default: high)",
    )
    weekly_parser.add_argument(
        "--seed", "-S",
        type=int,
        help="Random seed for reproducibility",
    )
    weekly_parser.add_argument(
        "--morning-limit",
        type=int,
        help="Max associates starting in morning block (5 AM - 11 AM)",
    )
    weekly_parser.add_argument(
        "--day-limit",
        type=int,
        help="Max associates starting in day block (10 AM - 4 PM)",
    )
    weekly_parser.add_argument(
        "--closing-limit",
        type=int,
        help="Max associates starting in closing block (3 PM - 10 PM)",
    )
    weekly_parser.add_argument(
        "--realistic", "-r",
        action="store_true",
        help="Use realistic shift distribution (9@5AM, 7@6AM, 5@7AM, 2@8AM, 1@8:30AM, 5@9AM, 1@9:30AM, 3@10AM, 6@11AM, 3@1PM, 5@2PM)",
    )

    # Demand-aware demo command
    demand_parser = subparsers.add_parser(
        "demand-demo",
        help="Run demand-aware schedule generation"
    )
    demand_parser.add_argument(
        "--count", "-c",
        type=int,
        default=10,
        help="Number of associates to generate (default: 10)",
    )
    demand_parser.add_argument(
        "--days", "-d",
        type=int,
        default=7,
        help="Number of days to schedule (default: 7)",
    )
    demand_parser.add_argument(
        "--solver", "-s",
        type=str,
        default="hybrid",
        choices=["heuristic", "cpsat", "hybrid"],
        help="Solver type: heuristic (fast), cpsat (optimal), hybrid (default)",
    )
    demand_parser.add_argument(
        "--optimization", "-O",
        type=str,
        default="balanced",
        choices=["maximize_coverage", "match_demand", "minimize_undercoverage", "balanced"],
        help="Optimization mode (default: balanced)",
    )
    demand_parser.add_argument(
        "--time-limit", "-t",
        type=float,
        default=30.0,
        help="CP-SAT solver time limit in seconds (default: 30)",
    )
    demand_parser.add_argument(
        "--profile", "-P",
        type=str,
        default="weekday",
        choices=["weekday", "weekend", "high_volume"],
        help="Demand profile to use (default: weekday)",
    )
    demand_parser.add_argument(
        "--output", "-o",
        type=str,
        help="Output PDF file path",
    )

    args = parser.parse_args()

    if args.command == "demo":
        run_demo(args.count, args.output, args.realistic, args.seed)
        return 0
    elif args.command == "weekly-demo":
        run_weekly_demo(
            args.count,
            args.days,
            args.pattern,
            args.output,
            args.variety,
            args.seed,
            args.morning_limit,
            args.day_limit,
            args.closing_limit,
            args.realistic,
        )
        return 0
    elif args.command == "demand-demo":
        run_demand_demo(
            args.count,
            args.days,
            args.solver,
            args.optimization,
            args.time_limit,
            args.profile,
            args.output,
        )
        return 0
    else:
        parser.print_help()
        return 1


if __name__ == "__main__":
    sys.exit(main())
