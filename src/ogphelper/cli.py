"""Command-line interface for OGP Helper scheduling tool."""

import argparse
import json
import sys
from datetime import date, time
from pathlib import Path
from typing import Optional

from ogphelper.domain.models import (
    Associate,
    Availability,
    JobRole,
    Preference,
    ScheduleRequest,
)
from ogphelper.output.pdf_generator import PDFGenerator
from ogphelper.scheduling.scheduler import Scheduler
from ogphelper.validation.validator import ScheduleValidator


def create_sample_associates(count: int = 10) -> list[Associate]:
    """Create sample associates for testing."""
    associates = []

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
            availability={date.today(): avail},
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


def main() -> int:
    """Main entry point for CLI."""
    parser = argparse.ArgumentParser(
        description="OGP Helper - Workforce Scheduling Tool",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s demo                    Run demo with 10 associates
  %(prog)s demo --count 30         Run demo with 30 associates
  %(prog)s demo --output sched.pdf Generate PDF output
        """,
    )

    subparsers = parser.add_subparsers(dest="command", help="Command to run")

    # Demo command
    demo_parser = subparsers.add_parser("demo", help="Run demo schedule generation")
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

    args = parser.parse_args()

    if args.command == "demo":
        run_demo(args.count, args.output)
        return 0
    else:
        parser.print_help()
        return 1


if __name__ == "__main__":
    sys.exit(main())
