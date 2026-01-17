"""Command-line interface for OGP Helper scheduling tool."""

import argparse
import json
import sys
from datetime import date, time, timedelta
from pathlib import Path
from typing import Optional

from ogphelper.domain.models import (
    Associate,
    Availability,
    DaysOffPattern,
    FairnessConfig,
    JobRole,
    Preference,
    ScheduleRequest,
    WeeklyScheduleRequest,
)
from ogphelper.output.pdf_generator import PDFGenerator
from ogphelper.scheduling.scheduler import Scheduler
from ogphelper.scheduling.weekly_scheduler import WeeklyScheduler
from ogphelper.validation.validator import ScheduleValidator


def create_sample_associates(
    count: int = 10,
    schedule_dates: Optional[list[date]] = None,
) -> list[Associate]:
    """Create sample associates for testing.

    Args:
        count: Number of associates to create.
        schedule_dates: List of dates for availability. If None, uses today only.
    """
    associates = []

    if schedule_dates is None:
        schedule_dates = [date.today()]

    # Default availability: 5 AM to 10 PM (full day)
    full_day = Availability(start_slot=0, end_slot=68)  # 68 slots = 17 hours

    # Sample names
    names = [
        "Alice", "Bob", "Carol", "David", "Eve", "Frank", "Grace", "Henry",
        "Ivy", "Jack", "Kate", "Leo", "Mia", "Noah", "Olivia", "Paul",
        "Quinn", "Rose", "Sam", "Tina", "Uma", "Victor", "Wendy", "Xavier",
        "Yara", "Zach", "Amy", "Ben", "Chloe", "Dan", "Emma", "Finn",
        "Gina", "Hugo", "Iris", "Jake", "Kim", "Luke", "Maya", "Nate",
    ]

    for i in range(count):
        name = names[i % len(names)]
        if i >= len(names):
            name = f"{name}{i // len(names) + 1}"

        # Build availability for each date
        availability = {}
        for d in schedule_dates:
            # Vary availability slightly
            if i % 5 == 0:
                # Early shift: 5 AM - 3 PM
                avail = Availability(start_slot=0, end_slot=40)
            elif i % 5 == 1:
                # Mid shift: 8 AM - 6 PM
                avail = Availability(start_slot=12, end_slot=52)
            elif i % 5 == 2:
                # Late shift: 12 PM - 10 PM
                avail = Availability(start_slot=28, end_slot=68)
            else:
                # Full day
                avail = full_day

            # Some associates have specific days off
            if i % 6 == 0 and d.weekday() == 0:  # Mondays off for some
                avail = Availability.off_day()
            if i % 8 == 0 and d.weekday() == 4:  # Fridays off for some
                avail = Availability.off_day()

            availability[d] = avail

        # All roles allowed by default
        allowed_roles = set(JobRole)

        # Some associates have restrictions
        cannot_do = set()
        if i % 7 == 0:
            cannot_do.add(JobRole.BACKROOM)
        if i % 11 == 0:
            cannot_do.add(JobRole.GMD_SM)

        # Set preferences
        preferences = {}
        if i % 3 == 0:
            preferences[JobRole.PICKING] = Preference.PREFER
        if i % 4 == 0:
            preferences[JobRole.BACKROOM] = Preference.AVOID

        associate = Associate(
            id=f"A{i + 1:03d}",
            name=name,
            availability=availability,
            max_minutes_per_day=480,  # 8 hours
            max_minutes_per_week=2400,  # 40 hours
            supervisor_allowed_roles=allowed_roles,
            cannot_do_roles=cannot_do,
            role_preferences=preferences,
        )
        associates.append(associate)

    return associates


def run_demo(associate_count: int = 10, output_path: Optional[str] = None) -> None:
    """Run a demo schedule generation."""
    print(f"Generating demo schedule for {associate_count} associates...")

    # Create sample associates
    associates = create_sample_associates(associate_count)

    # Create schedule request
    request = ScheduleRequest(
        schedule_date=date.today(),
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
) -> None:
    """Run a weekly schedule generation demo.

    Args:
        associate_count: Number of associates to schedule.
        days: Number of days to schedule (default 7 for a week).
        days_off_pattern: Pattern for days off (none, two_consecutive, one_weekend_day).
    """
    print(f"Generating weekly schedule for {associate_count} associates over {days} days...")

    # Generate date range
    start_date = date.today()
    # Adjust to start on Monday if scheduling a full week
    if days >= 7:
        days_until_monday = (7 - start_date.weekday()) % 7
        if days_until_monday > 0:
            start_date = start_date + timedelta(days=days_until_monday)
    end_date = start_date + timedelta(days=days - 1)

    schedule_dates = [start_date + timedelta(days=i) for i in range(days)]

    # Create sample associates with weekly availability
    associates = create_sample_associates(associate_count, schedule_dates)

    # Parse days-off pattern
    pattern_map = {
        "none": DaysOffPattern.NONE,
        "two_consecutive": DaysOffPattern.TWO_CONSECUTIVE,
        "one_weekend_day": DaysOffPattern.ONE_WEEKEND_DAY,
        "every_other_day": DaysOffPattern.EVERY_OTHER_DAY,
    }
    pattern = pattern_map.get(days_off_pattern, DaysOffPattern.TWO_CONSECUTIVE)

    # Create weekly schedule request
    request = WeeklyScheduleRequest(
        start_date=start_date,
        end_date=end_date,
        associates=associates,
        days_off_pattern=pattern,
        required_days_off=2,
        fairness_config=FairnessConfig(
            weight_hours_balance=0.7,
            weight_days_balance=0.3,
            max_hours_variance=120.0,  # 2 hours variance allowed
        ),
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


def main() -> int:
    """Main entry point for CLI."""
    parser = argparse.ArgumentParser(
        description="OGP Helper - Workforce Scheduling Tool",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s demo                       Run daily demo with 10 associates
  %(prog)s demo --count 30            Run daily demo with 30 associates
  %(prog)s demo --output sched.pdf    Generate PDF output

  %(prog)s weekly-demo                Run weekly demo with 10 associates
  %(prog)s weekly-demo --count 20     Run weekly demo with 20 associates
  %(prog)s weekly-demo --days 5       Generate 5-day schedule
  %(prog)s weekly-demo --pattern none Disable days-off pattern
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

    # Weekly demo command
    weekly_parser = subparsers.add_parser(
        "weekly-demo",
        help="Run weekly demo schedule generation (Phase 2)"
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

    args = parser.parse_args()

    if args.command == "demo":
        run_demo(args.count, args.output)
        return 0
    elif args.command == "weekly-demo":
        run_weekly_demo(args.count, args.days, args.pattern)
        return 0
    else:
        parser.print_help()
        return 1


if __name__ == "__main__":
    sys.exit(main())
