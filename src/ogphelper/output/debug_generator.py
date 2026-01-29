"""Debug text output for schedule analysis.

This module creates text-based debug output to analyze:
- Lunch time distribution and staggering patterns
- Break time distribution
- Coverage gaps and clustering issues
"""

from collections import defaultdict
from datetime import time
from pathlib import Path
from typing import Optional, Union

from ogphelper.domain.models import Associate, DaySchedule


class DebugGenerator:
    """Generates debug text output for schedule analysis.

    Creates human-readable text files showing:
    - Per-associate lunch and break times
    - Distribution histograms
    - Staggering pattern analysis
    """

    def generate(
        self,
        schedule: DaySchedule,
        associates_map: dict[str, Associate],
        output_path: Union[str, Path],
    ) -> str:
        """Generate debug text output and save to file.

        Args:
            schedule: The day schedule to analyze.
            associates_map: Dict mapping associate IDs to Associate objects.
            output_path: Path to save the text file.

        Returns:
            The generated text content.
        """
        content = self._generate_content(schedule, associates_map)
        Path(output_path).write_text(content)
        return content

    def generate_to_string(
        self,
        schedule: DaySchedule,
        associates_map: dict[str, Associate],
    ) -> str:
        """Generate debug text output and return as string.

        Args:
            schedule: The day schedule to analyze.
            associates_map: Dict mapping associate IDs to Associate objects.

        Returns:
            The generated text content.
        """
        return self._generate_content(schedule, associates_map)

    def _generate_content(
        self,
        schedule: DaySchedule,
        associates_map: dict[str, Associate],
    ) -> str:
        """Generate the full debug content."""
        lines = []

        # Header
        lines.append("=" * 80)
        lines.append(f"SCHEDULE DEBUG OUTPUT - {schedule.schedule_date}")
        lines.append("=" * 80)
        lines.append("")

        # Basic stats
        lines.append(f"Total Associates: {len(schedule.assignments)}")
        lines.append(f"Slot Duration: {schedule.slot_minutes} minutes")
        lines.append(f"Day Window: {self._slot_to_time_str(schedule, 0)} - "
                     f"{self._slot_to_time_str(schedule, schedule.total_slots)}")
        lines.append("")

        # Sort assignments by shift start, then lunch start
        sorted_assignments = sorted(
            schedule.assignments.values(),
            key=lambda a: (a.shift_start_slot, a.lunch_block.start_slot if a.lunch_block else 999),
        )

        # Detailed per-associate view
        lines.append("-" * 80)
        lines.append("DETAILED ASSOCIATE SCHEDULE (sorted by shift start, then lunch start)")
        lines.append("-" * 80)
        lines.append(f"{'#':>3} {'Name':<20} {'Shift':^13} {'Lunch':^13} {'Lunch Gap':>10}")
        lines.append("-" * 80)

        prev_lunch_start = None
        for i, assignment in enumerate(sorted_assignments, 1):
            associate = associates_map.get(assignment.associate_id)
            name = (associate.name if associate else assignment.associate_id)[:20]

            shift_start = self._slot_to_time_str(schedule, assignment.shift_start_slot)
            shift_end = self._slot_to_time_str(schedule, assignment.shift_end_slot)
            shift_str = f"{shift_start}-{shift_end}"

            if assignment.lunch_block:
                lunch_start = self._slot_to_time_str(schedule, assignment.lunch_block.start_slot)
                lunch_end = self._slot_to_time_str(schedule, assignment.lunch_block.end_slot)
                lunch_str = f"{lunch_start}-{lunch_end}"

                # Calculate gap from previous lunch
                if prev_lunch_start is not None:
                    gap_slots = assignment.lunch_block.start_slot - prev_lunch_start
                    gap_minutes = gap_slots * schedule.slot_minutes
                    gap_str = f"{gap_minutes:+d} min"
                else:
                    gap_str = "-"

                prev_lunch_start = assignment.lunch_block.start_slot
            else:
                lunch_str = "No lunch"
                gap_str = "-"

            lines.append(f"{i:>3} {name:<20} {shift_str:^13} {lunch_str:^13} {gap_str:>10}")

        lines.append("")

        # Lunch time histogram
        lines.append("-" * 80)
        lines.append("LUNCH START TIME HISTOGRAM")
        lines.append("-" * 80)

        lunch_counts = defaultdict(int)
        for assignment in schedule.assignments.values():
            if assignment.lunch_block:
                lunch_counts[assignment.lunch_block.start_slot] += 1

        if lunch_counts:
            min_slot = min(lunch_counts.keys())
            max_slot = max(lunch_counts.keys())

            for slot in range(min_slot, max_slot + 1):
                count = lunch_counts.get(slot, 0)
                time_str = self._slot_to_time_str(schedule, slot)
                bar = "#" * count
                if count > 0:
                    lines.append(f"{time_str}: {bar} ({count})")
                else:
                    lines.append(f"{time_str}: .")

        lines.append("")

        # Lunch stagger analysis
        lines.append("-" * 80)
        lines.append("LUNCH STAGGER ANALYSIS")
        lines.append("-" * 80)

        # Group by shift start time
        by_shift_start = defaultdict(list)
        for assignment in schedule.assignments.values():
            by_shift_start[assignment.shift_start_slot].append(assignment)

        for shift_slot in sorted(by_shift_start.keys()):
            assignments = by_shift_start[shift_slot]
            shift_time = self._slot_to_time_str(schedule, shift_slot)
            lines.append(f"\nShift Start {shift_time} ({len(assignments)} associates):")

            # Sort by lunch start within this group
            with_lunch = [a for a in assignments if a.lunch_block]
            with_lunch.sort(key=lambda a: a.lunch_block.start_slot)

            if with_lunch:
                lunch_starts = [a.lunch_block.start_slot for a in with_lunch]
                unique_lunches = sorted(set(lunch_starts))

                # Count per lunch slot
                lunch_hist = defaultdict(int)
                for ls in lunch_starts:
                    lunch_hist[ls] += 1

                lines.append(f"  Lunch slots used: {len(unique_lunches)}")
                lines.append(f"  Lunch range: {self._slot_to_time_str(schedule, min(lunch_starts))} - "
                             f"{self._slot_to_time_str(schedule, max(lunch_starts))}")

                # Show distribution
                for slot in unique_lunches:
                    count = lunch_hist[slot]
                    time_str = self._slot_to_time_str(schedule, slot)
                    lines.append(f"    {time_str}: {count} associates")

                # Calculate gaps
                if len(unique_lunches) > 1:
                    gaps = []
                    for i in range(1, len(unique_lunches)):
                        gap = unique_lunches[i] - unique_lunches[i - 1]
                        gaps.append(gap * schedule.slot_minutes)
                    lines.append(f"  Gaps between unique lunch times (minutes): {gaps}")

        lines.append("")

        # Overall stagger quality metrics
        lines.append("-" * 80)
        lines.append("STAGGER QUALITY METRICS")
        lines.append("-" * 80)

        all_lunch_starts = sorted([
            a.lunch_block.start_slot
            for a in schedule.assignments.values()
            if a.lunch_block
        ])

        if len(all_lunch_starts) > 1:
            # Calculate consecutive gaps
            consecutive_gaps = []
            for i in range(1, len(all_lunch_starts)):
                gap = all_lunch_starts[i] - all_lunch_starts[i - 1]
                consecutive_gaps.append(gap * schedule.slot_minutes)

            lines.append(f"Total lunches scheduled: {len(all_lunch_starts)}")
            lines.append(f"Unique lunch start times: {len(set(all_lunch_starts))}")

            # Ideal: 15 min gaps between consecutive lunches
            ideal_gap = schedule.slot_minutes  # 15 min = 1 slot
            ideal_gaps = sum(1 for g in consecutive_gaps if g == ideal_gap)
            zero_gaps = sum(1 for g in consecutive_gaps if g == 0)
            large_gaps = sum(1 for g in consecutive_gaps if g > ideal_gap * 2)

            lines.append(f"\nConsecutive gap analysis:")
            lines.append(f"  Perfect 15-min gaps: {ideal_gaps} ({100*ideal_gaps/len(consecutive_gaps):.1f}%)")
            lines.append(f"  Same-time (0 gap): {zero_gaps} ({100*zero_gaps/len(consecutive_gaps):.1f}%)")
            lines.append(f"  Large gaps (>30 min): {large_gaps} ({100*large_gaps/len(consecutive_gaps):.1f}%)")

            # Distribution of gap sizes
            gap_dist = defaultdict(int)
            for g in consecutive_gaps:
                gap_dist[g] += 1

            lines.append(f"\nGap size distribution:")
            for gap_size in sorted(gap_dist.keys()):
                count = gap_dist[gap_size]
                lines.append(f"    {gap_size:>3} min: {count:>3} occurrences")

            # First N and last N analysis
            n = 10
            first_gaps = consecutive_gaps[:n] if len(consecutive_gaps) >= n else consecutive_gaps
            last_gaps = consecutive_gaps[-n:] if len(consecutive_gaps) >= n else consecutive_gaps

            lines.append(f"\nFirst {len(first_gaps)} consecutive gaps (minutes): {first_gaps}")
            lines.append(f"Last {len(last_gaps)} consecutive gaps (minutes): {last_gaps}")

            # Middle section
            if len(consecutive_gaps) > 2 * n:
                middle_start = len(consecutive_gaps) // 3
                middle_end = 2 * len(consecutive_gaps) // 3
                middle_gaps = consecutive_gaps[middle_start:middle_end]
                lines.append(f"Middle section gaps (#{middle_start}-{middle_end}): {middle_gaps[:20]}...")

        lines.append("")

        # Coverage analysis during lunch hours
        lines.append("-" * 80)
        lines.append("COVERAGE DURING PEAK LUNCH (9AM-2PM)")
        lines.append("-" * 80)

        # Convert 9AM-2PM to slots
        lunch_window_start = (9 * 60 - schedule.day_start_minutes) // schedule.slot_minutes
        lunch_window_end = (14 * 60 - schedule.day_start_minutes) // schedule.slot_minutes

        for slot in range(max(0, lunch_window_start), min(schedule.total_slots, lunch_window_end)):
            time_str = self._slot_to_time_str(schedule, slot)
            on_floor = schedule.get_coverage_at_slot(slot)
            on_lunch = len(schedule.get_on_lunch_at_slot(slot))

            bar = "█" * on_floor + "░" * on_lunch
            lines.append(f"{time_str}: {bar} (floor:{on_floor}, lunch:{on_lunch})")

        lines.append("")
        lines.append("=" * 80)
        lines.append("END OF DEBUG OUTPUT")
        lines.append("=" * 80)

        return "\n".join(lines)

    def _slot_to_time_str(self, schedule: DaySchedule, slot: int) -> str:
        """Convert slot to time string."""
        t = schedule.slot_to_time(slot)
        return t.strftime("%H:%M")
