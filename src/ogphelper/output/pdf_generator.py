"""PDF generation for schedule output.

This module creates printable PDF schedules showing:
- Per-associate timelines with shifts, lunches, breaks, and roles
- Coverage summaries
- Role distribution overviews
"""

from datetime import date, time
from io import BytesIO
from pathlib import Path
from typing import Optional, Union

from ogphelper.domain.models import (
    Associate,
    DaySchedule,
    JobRole,
    ShiftAssignment,
    WeeklySchedule,
)

# Color definitions (RGB tuples, 0-1 scale)
COLORS = {
    JobRole.PICKING: (0.4, 0.7, 0.4),  # Green
    JobRole.GMD_SM: (0.4, 0.4, 0.8),  # Blue
    JobRole.EXCEPTION_SM: (0.8, 0.6, 0.2),  # Orange
    JobRole.STAGING: (0.7, 0.4, 0.7),  # Purple
    JobRole.BACKROOM: (0.6, 0.6, 0.6),  # Gray
    JobRole.SR: (0.2, 0.7, 0.7),  # Teal/Cyan - distinct from other roles
    "lunch": (1.0, 0.9, 0.5),  # Yellow
    "break": (0.9, 0.7, 0.7),  # Light red/pink
    "off_shift": (0.95, 0.95, 0.95),  # Light gray
}


class PDFGenerator:
    """Generates printable PDF schedules.

    The generator creates professional-looking schedule PDFs that include:
    - Individual associate timelines
    - Shift, lunch, break, and role assignments visualized
    - Summary pages with coverage statistics

    Example:
        >>> generator = PDFGenerator()
        >>> generator.generate(schedule, associates_map, "schedule.pdf")
    """

    def __init__(
        self,
        page_width: float = 792,  # Letter landscape width (11")
        page_height: float = 612,  # Letter landscape height (8.5")
        margin: float = 36,  # 0.5 inch margins
    ):
        self.page_width = page_width
        self.page_height = page_height
        self.margin = margin

    def generate(
        self,
        schedule: DaySchedule,
        associates_map: dict[str, Associate],
        output_path: Union[str, Path],
        include_summary: bool = True,
    ) -> None:
        """Generate PDF schedule and save to file.

        Args:
            schedule: The day schedule to render.
            associates_map: Dict mapping associate IDs to Associate objects.
            output_path: Path to save the PDF.
            include_summary: Whether to include summary pages.
        """
        try:
            from reportlab.lib.pagesizes import letter, landscape
            from reportlab.lib.units import inch
            from reportlab.pdfgen import canvas
        except ImportError:
            raise ImportError(
                "reportlab is required for PDF generation. "
                "Install with: pip install reportlab"
            )

        c = canvas.Canvas(str(output_path), pagesize=landscape(letter))

        # Generate schedule pages
        self._draw_schedule_pages(c, schedule, associates_map)

        # Generate summary page if requested
        if include_summary:
            self._draw_summary_page(c, schedule, associates_map)

        c.save()

    def generate_to_buffer(
        self,
        schedule: DaySchedule,
        associates_map: dict[str, Associate],
        include_summary: bool = True,
    ) -> BytesIO:
        """Generate PDF and return as bytes buffer.

        Args:
            schedule: The day schedule to render.
            associates_map: Dict mapping associate IDs to Associate objects.
            include_summary: Whether to include summary pages.

        Returns:
            BytesIO buffer containing PDF data.
        """
        try:
            from reportlab.lib.pagesizes import letter, landscape
            from reportlab.pdfgen import canvas
        except ImportError:
            raise ImportError(
                "reportlab is required for PDF generation. "
                "Install with: pip install reportlab"
            )

        buffer = BytesIO()
        c = canvas.Canvas(buffer, pagesize=landscape(letter))

        self._draw_schedule_pages(c, schedule, associates_map)

        if include_summary:
            self._draw_summary_page(c, schedule, associates_map)

        c.save()
        buffer.seek(0)
        return buffer

    def generate_weekly(
        self,
        schedule: WeeklySchedule,
        associates_map: dict[str, Associate],
        output_path: Union[str, Path],
        include_summary: bool = True,
    ) -> None:
        """Generate PDF for a weekly schedule and save to file.

        Creates a multi-page PDF with:
        - Individual day schedule pages (same visual fidelity as daily)
        - Daily coverage timelines with associate assignments
        - Weekly summary page with coverage across all days
        - Fairness metrics and distribution charts

        Args:
            schedule: The weekly schedule to render.
            associates_map: Dict mapping associate IDs to Associate objects.
            output_path: Path to save the PDF.
            include_summary: Whether to include weekly summary page.
        """
        try:
            from reportlab.lib.pagesizes import letter, landscape
            from reportlab.pdfgen import canvas
        except ImportError:
            raise ImportError(
                "reportlab is required for PDF generation. "
                "Install with: pip install reportlab"
            )

        c = canvas.Canvas(str(output_path), pagesize=landscape(letter))

        # Generate pages for each day
        for d in sorted(schedule.day_schedules.keys()):
            day_schedule = schedule.day_schedules[d]
            self._draw_schedule_pages(c, day_schedule, associates_map)

        # Generate weekly summary page if requested
        if include_summary:
            self._draw_weekly_summary_page(c, schedule, associates_map)

        c.save()

    def generate_weekly_to_buffer(
        self,
        schedule: WeeklySchedule,
        associates_map: dict[str, Associate],
        include_summary: bool = True,
    ) -> BytesIO:
        """Generate weekly PDF and return as bytes buffer.

        Args:
            schedule: The weekly schedule to render.
            associates_map: Dict mapping associate IDs to Associate objects.
            include_summary: Whether to include weekly summary page.

        Returns:
            BytesIO buffer containing PDF data.
        """
        try:
            from reportlab.lib.pagesizes import letter, landscape
            from reportlab.pdfgen import canvas
        except ImportError:
            raise ImportError(
                "reportlab is required for PDF generation. "
                "Install with: pip install reportlab"
            )

        buffer = BytesIO()
        c = canvas.Canvas(buffer, pagesize=landscape(letter))

        # Generate pages for each day
        for d in sorted(schedule.day_schedules.keys()):
            day_schedule = schedule.day_schedules[d]
            self._draw_schedule_pages(c, day_schedule, associates_map)

        if include_summary:
            self._draw_weekly_summary_page(c, schedule, associates_map)

        c.save()
        buffer.seek(0)
        return buffer

    def _draw_weekly_summary_page(
        self,
        c,
        schedule: WeeklySchedule,
        associates_map: dict[str, Associate],
    ) -> None:
        """Draw weekly summary page with coverage and fairness statistics."""
        from reportlab.lib.units import inch

        # Header
        c.setFont("Helvetica-Bold", 16)
        c.drawString(
            self.margin,
            self.page_height - self.margin - 20,
            f"Weekly Schedule Summary - {schedule.start_date.strftime('%b %d')} to "
            f"{schedule.end_date.strftime('%b %d, %Y')}",
        )

        y = self.page_height - self.margin - 60

        # Weekly overview stats
        c.setFont("Helvetica-Bold", 12)
        c.drawString(self.margin, y, "Weekly Overview")
        y -= 20

        summary = schedule.get_weekly_summary()
        c.setFont("Helvetica", 10)
        stats = [
            f"Total Days Scheduled: {summary['days_scheduled']}",
            f"Total Shifts: {summary['total_shifts']}",
            f"Total Work Hours: {summary['total_work_hours']:.1f}",
        ]

        for stat in stats:
            c.drawString(self.margin + 20, y, stat)
            y -= 15

        # Daily coverage summary table
        y -= 15
        c.setFont("Helvetica-Bold", 12)
        c.drawString(self.margin, y, "Daily Coverage Summary")
        y -= 15

        # Table header
        c.setFont("Helvetica-Bold", 9)
        cols = [self.margin + 20, self.margin + 120, self.margin + 180,
                self.margin + 240, self.margin + 300]
        c.drawString(cols[0], y, "Date")
        c.drawString(cols[1], y, "Day")
        c.drawString(cols[2], y, "Min")
        c.drawString(cols[3], y, "Max")
        c.drawString(cols[4], y, "Avg")
        y -= 3
        c.line(self.margin + 20, y, self.margin + 360, y)
        y -= 12

        c.setFont("Helvetica", 9)
        coverage_by_day = summary.get('coverage_by_day', {})
        for d in sorted(schedule.day_schedules.keys()):
            coverage = coverage_by_day.get(d, {"min": 0, "max": 0, "avg": 0})
            day_name = d.strftime("%A")[:3]
            c.drawString(cols[0], y, d.strftime("%m/%d"))
            c.drawString(cols[1], y, day_name)
            c.drawString(cols[2], y, str(coverage['min']))
            c.drawString(cols[3], y, str(coverage['max']))
            c.drawString(cols[4], y, f"{coverage['avg']:.1f}")
            y -= 12

        # Fairness metrics
        if schedule.fairness_metrics:
            y -= 20
            c.setFont("Helvetica-Bold", 12)
            c.drawString(self.margin, y, "Fairness Metrics")
            y -= 20

            metrics = schedule.fairness_metrics
            c.setFont("Helvetica", 10)
            fairness_stats = [
                f"Average Hours per Associate: {metrics.avg_hours:.1f}",
                f"Hours Standard Deviation: {metrics.hours_std_dev:.1f}",
                f"Hours Range: {metrics.min_hours:.1f} - {metrics.max_hours:.1f}",
                f"Fairness Score: {metrics.fairness_score:.1f}/100",
            ]

            for stat in fairness_stats:
                c.drawString(self.margin + 20, y, stat)
                y -= 15

            # Hours distribution visualization
            if metrics.hours_per_associate:
                y -= 15
                c.setFont("Helvetica-Bold", 10)
                c.drawString(self.margin, y, "Hours Distribution by Associate")
                y -= 10

                self._draw_hours_distribution_chart(
                    c, metrics.hours_per_associate, associates_map,
                    self.margin, y - 120, 500, 110
                )

        # Legend
        self._draw_legend(c, self.margin, self.margin + 10)

        c.showPage()

    def _draw_hours_distribution_chart(
        self,
        c,
        hours_per_associate: dict[str, float],
        associates_map: dict[str, Associate],
        x: float,
        y: float,
        width: float,
        height: float,
    ) -> None:
        """Draw a horizontal bar chart showing hours per associate."""
        if not hours_per_associate:
            return

        # Sort by hours descending
        sorted_items = sorted(
            hours_per_associate.items(),
            key=lambda item: item[1],
            reverse=True
        )

        # Limit to top 15 associates if there are many
        display_items = sorted_items[:15]
        max_hours = max(h for _, h in display_items) if display_items else 1
        if max_hours == 0:
            max_hours = 1  # Prevent division by zero

        bar_height = min(12, height / len(display_items) - 2)
        chart_width = width - 80  # Leave room for names

        c.setFont("Helvetica", 7)

        for i, (assoc_id, hours) in enumerate(display_items):
            bar_y = y + height - (i + 1) * (bar_height + 2)
            bar_width = (hours / max_hours) * chart_width

            # Draw name
            associate = associates_map.get(assoc_id)
            name = associate.name if associate else assoc_id
            c.drawString(x, bar_y + 2, name[:10])

            # Draw bar
            c.setFillColorRGB(0.4, 0.6, 0.8)
            c.rect(x + 70, bar_y, bar_width, bar_height, fill=1, stroke=0)

            # Draw hours label
            c.setFillColorRGB(0, 0, 0)
            c.drawString(x + 75 + bar_width, bar_y + 2, f"{hours:.1f}h")

        if len(sorted_items) > 15:
            c.drawString(x, y + 2, f"... and {len(sorted_items) - 15} more associates")

    def _draw_schedule_pages(
        self,
        c,
        schedule: DaySchedule,
        associates_map: dict[str, Associate],
    ) -> None:
        """Draw main schedule pages with associate timelines."""
        from reportlab.lib.units import inch

        # Sort assignments by start time, then by name
        sorted_assignments = sorted(
            schedule.assignments.values(),
            key=lambda a: (a.shift_start_slot, associates_map.get(a.associate_id, Associate(id="", name="")).name),
        )

        # Calculate how many associates fit per page
        row_height = 24
        header_height = 60
        footer_height = 40
        usable_height = self.page_height - 2 * self.margin - header_height - footer_height
        rows_per_page = int(usable_height / row_height)

        # Timeline dimensions
        timeline_left = self.margin + 120  # Space for names
        timeline_right = self.page_width - self.margin - 20
        timeline_width = timeline_right - timeline_left

        # Generate pages
        for page_start in range(0, len(sorted_assignments), rows_per_page):
            page_assignments = sorted_assignments[page_start : page_start + rows_per_page]

            # Draw header
            self._draw_header(c, schedule, header_height)

            # Draw time axis
            self._draw_time_axis(
                c,
                schedule,
                timeline_left,
                self.page_height - self.margin - header_height - 20,
                timeline_width,
            )

            # Draw each associate row
            y = self.page_height - self.margin - header_height - 30
            for assignment in page_assignments:
                y -= row_height
                self._draw_assignment_row(
                    c,
                    assignment,
                    associates_map,
                    schedule,
                    timeline_left,
                    timeline_width,
                    y,
                    row_height - 4,
                )

            # Draw legend
            self._draw_legend(c, self.margin, self.margin + 10)

            # Draw page number
            page_num = (page_start // rows_per_page) + 1
            total_pages = (len(sorted_assignments) + rows_per_page - 1) // rows_per_page
            c.setFont("Helvetica", 9)
            c.drawCentredString(
                self.page_width / 2,
                self.margin - 10,
                f"Page {page_num} of {total_pages}",
            )

            c.showPage()

    def _draw_header(self, c, schedule: DaySchedule, header_height: float) -> None:
        """Draw page header with date and title."""
        c.setFont("Helvetica-Bold", 16)
        c.drawString(
            self.margin,
            self.page_height - self.margin - 20,
            f"Daily Schedule - {schedule.schedule_date.strftime('%A, %B %d, %Y')}",
        )

        c.setFont("Helvetica", 10)
        c.drawString(
            self.margin,
            self.page_height - self.margin - 35,
            f"Total Associates Scheduled: {len(schedule.assignments)}",
        )

    def _draw_time_axis(
        self,
        c,
        schedule: DaySchedule,
        x: float,
        y: float,
        width: float,
    ) -> None:
        """Draw time axis with hour markers."""
        total_slots = schedule.total_slots
        slot_width = width / total_slots

        c.setFont("Helvetica", 8)
        c.setStrokeColorRGB(0.7, 0.7, 0.7)

        # Draw hour markers
        for slot in range(0, total_slots + 1, 4):  # Every hour (4 x 15-min slots)
            slot_x = x + slot * slot_width
            t = schedule.slot_to_time(slot)

            # Draw tick
            c.line(slot_x, y, slot_x, y - 5)

            # Draw label
            if slot < total_slots:
                label = t.strftime("%I%p").lstrip("0").lower()
                c.drawCentredString(slot_x, y + 5, label)

    def _draw_assignment_row(
        self,
        c,
        assignment: ShiftAssignment,
        associates_map: dict[str, Associate],
        schedule: DaySchedule,
        timeline_x: float,
        timeline_width: float,
        y: float,
        height: float,
    ) -> None:
        """Draw a single associate's schedule row."""
        total_slots = schedule.total_slots
        slot_width = timeline_width / total_slots

        associate = associates_map.get(assignment.associate_id)
        name = associate.name if associate else assignment.associate_id

        # Draw name
        c.setFont("Helvetica", 9)
        c.drawString(self.margin, y + height / 2 - 3, name[:18])

        # Draw shift time
        start_time = schedule.slot_to_time(assignment.shift_start_slot)
        end_time = schedule.slot_to_time(assignment.shift_end_slot)
        time_str = f"{start_time.strftime('%H:%M')}-{end_time.strftime('%H:%M')}"
        c.setFont("Helvetica", 7)
        c.drawString(self.margin, y + height / 2 - 10, time_str)

        # Draw background for off-shift time
        c.setFillColorRGB(*COLORS["off_shift"])
        c.rect(timeline_x, y, timeline_width, height, fill=1, stroke=0)

        # Draw job assignments
        for job_assignment in assignment.job_assignments:
            block = job_assignment.block
            bx = timeline_x + block.start_slot * slot_width
            bw = (block.end_slot - block.start_slot) * slot_width

            color = COLORS.get(job_assignment.role, (0.5, 0.5, 0.5))
            c.setFillColorRGB(*color)
            c.rect(bx, y, bw, height, fill=1, stroke=0)

        # Draw lunch block
        if assignment.lunch_block:
            block = assignment.lunch_block
            bx = timeline_x + block.start_slot * slot_width
            bw = (block.end_slot - block.start_slot) * slot_width

            c.setFillColorRGB(*COLORS["lunch"])
            c.rect(bx, y, bw, height, fill=1, stroke=0)

            # Draw "L" label
            c.setFillColorRGB(0, 0, 0)
            c.setFont("Helvetica-Bold", 7)
            c.drawCentredString(bx + bw / 2, y + height / 2 - 3, "L")

        # Draw break blocks
        for break_block in assignment.break_blocks:
            bx = timeline_x + break_block.start_slot * slot_width
            bw = (break_block.end_slot - break_block.start_slot) * slot_width

            c.setFillColorRGB(*COLORS["break"])
            c.rect(bx, y, bw, height, fill=1, stroke=0)

            # Draw "B" label
            c.setFillColorRGB(0, 0, 0)
            c.setFont("Helvetica-Bold", 6)
            c.drawCentredString(bx + bw / 2, y + height / 2 - 3, "B")

        # Draw border around shift
        shift_x = timeline_x + assignment.shift_start_slot * slot_width
        shift_w = (assignment.shift_end_slot - assignment.shift_start_slot) * slot_width
        c.setStrokeColorRGB(0.3, 0.3, 0.3)
        c.setLineWidth(0.5)
        c.rect(shift_x, y, shift_w, height, fill=0, stroke=1)

    def _draw_legend(self, c, x: float, y: float) -> None:
        """Draw legend for colors."""
        c.setFont("Helvetica-Bold", 8)
        c.drawString(x, y, "Legend:")

        items = [
            (JobRole.PICKING, "Picking"),
            (JobRole.GMD_SM, "GMD/SM"),
            (JobRole.EXCEPTION_SM, "Exception"),
            (JobRole.STAGING, "Staging"),
            (JobRole.BACKROOM, "Backroom"),
            (JobRole.SR, "SR"),
            ("lunch", "Lunch"),
            ("break", "Break"),
        ]

        c.setFont("Helvetica", 7)
        current_x = x + 45

        for key, label in items:
            color = COLORS.get(key, (0.5, 0.5, 0.5))
            c.setFillColorRGB(*color)
            c.rect(current_x, y - 2, 12, 10, fill=1, stroke=1)
            c.setFillColorRGB(0, 0, 0)
            c.drawString(current_x + 15, y, label)
            current_x += 70

    def _draw_summary_page(
        self,
        c,
        schedule: DaySchedule,
        associates_map: dict[str, Associate],
    ) -> None:
        """Draw summary page with coverage statistics."""
        from reportlab.lib.units import inch

        # Header
        c.setFont("Helvetica-Bold", 16)
        c.drawString(
            self.margin,
            self.page_height - self.margin - 20,
            f"Schedule Summary - {schedule.schedule_date.strftime('%A, %B %d, %Y')}",
        )

        y = self.page_height - self.margin - 60

        # Basic stats
        c.setFont("Helvetica-Bold", 12)
        c.drawString(self.margin, y, "Overview")
        y -= 20

        c.setFont("Helvetica", 10)
        stats = [
            f"Total Associates Scheduled: {len(schedule.assignments)}",
            f"Operating Window: {schedule.slot_to_time(0).strftime('%H:%M')} - "
            f"{schedule.slot_to_time(schedule.total_slots).strftime('%H:%M')}",
        ]

        total_work = sum(a.work_minutes for a in schedule.assignments.values())
        total_lunch = sum(a.lunch_minutes for a in schedule.assignments.values())
        total_break = sum(a.break_minutes for a in schedule.assignments.values())

        stats.extend([
            f"Total Work Hours: {total_work / 60:.1f}",
            f"Total Lunch Hours: {total_lunch / 60:.1f}",
            f"Total Break Hours: {total_break / 60:.1f}",
        ])

        for stat in stats:
            c.drawString(self.margin + 20, y, stat)
            y -= 15

        # Coverage chart
        y -= 20
        c.setFont("Helvetica-Bold", 12)
        c.drawString(self.margin, y, "Hourly Coverage")
        y -= 10

        coverage = schedule.get_coverage_timeline()
        self._draw_coverage_chart(c, coverage, schedule, self.margin, y - 150, 400, 140)

        # Role distribution
        y -= 180
        c.setFont("Helvetica-Bold", 12)
        c.drawString(self.margin, y, "Role Distribution by Hour")
        y -= 15

        c.setFont("Helvetica", 9)

        # Roles that also count as pickers
        picker_roles = {JobRole.PICKING, JobRole.GMD_SM, JobRole.EXCEPTION_SM, JobRole.SR}

        # Store coverage data for combined calculation
        role_coverages = {}

        for role in JobRole:
            role_coverage = [
                schedule.get_role_coverage_at_slot(slot, role)
                for slot in range(schedule.total_slots)
            ]
            role_coverages[role] = role_coverage
            max_count = max(role_coverage) if role_coverage else 0
            avg_count = sum(role_coverage) / len(role_coverage) if role_coverage else 0

            c.setFillColorRGB(*COLORS.get(role, (0.5, 0.5, 0.5)))
            c.rect(self.margin + 20, y - 2, 10, 10, fill=1, stroke=1)
            c.setFillColorRGB(0, 0, 0)

            # Mark roles that also count as pickers
            picker_note = " *" if role in picker_roles and role != JobRole.PICKING else ""
            c.drawString(
                self.margin + 35, y,
                f"{role.value}: max {max_count}, avg {avg_count:.1f}{picker_note}"
            )
            y -= 15

        # Calculate combined picking capacity (Picking + GMD/SM + Exception/SM + S/R)
        y -= 10
        c.setFont("Helvetica-Bold", 9)
        combined_coverage = [
            sum(role_coverages[role][slot] for role in picker_roles)
            for slot in range(schedule.total_slots)
        ]
        combined_max = max(combined_coverage) if combined_coverage else 0
        combined_avg = sum(combined_coverage) / len(combined_coverage) if combined_coverage else 0

        c.setFillColorRGB(0.3, 0.6, 0.3)  # Darker green for combined
        c.rect(self.margin + 20, y - 2, 10, 10, fill=1, stroke=1)
        c.setFillColorRGB(0, 0, 0)
        c.drawString(
            self.margin + 35, y,
            f"Total Picking Capacity: max {combined_max}, avg {combined_avg:.1f}"
        )
        y -= 15

        # Notes section
        y -= 15
        c.setFont("Helvetica-Bold", 10)
        c.drawString(self.margin, y, "Notes:")
        y -= 15
        c.setFont("Helvetica", 9)
        c.drawString(
            self.margin + 20, y,
            "* GMD/SM, Exception/SM, and S/R also count as Pickers (included in Total Picking Capacity)."
        )

        c.showPage()

    def _draw_coverage_chart(
        self,
        c,
        coverage: list[int],
        schedule: DaySchedule,
        x: float,
        y: float,
        width: float,
        height: float,
    ) -> None:
        """Draw a simple bar chart of coverage over time."""
        if not coverage:
            return

        max_coverage = max(coverage) or 1
        bar_width = width / len(coverage)

        # Draw axes
        c.setStrokeColorRGB(0, 0, 0)
        c.setLineWidth(1)
        c.line(x, y, x, y + height)  # Y axis
        c.line(x, y, x + width, y)  # X axis

        # Draw bars (sample every 4 slots = 1 hour)
        c.setFillColorRGB(0.4, 0.6, 0.8)
        for i in range(0, len(coverage), 4):
            avg = sum(coverage[i : i + 4]) / min(4, len(coverage) - i)
            bar_height = (avg / max_coverage) * height
            bar_x = x + (i / len(coverage)) * width
            bar_w = (4 / len(coverage)) * width
            c.rect(bar_x, y, bar_w - 1, bar_height, fill=1, stroke=0)

        # Draw Y axis labels
        c.setFont("Helvetica", 7)
        c.drawRightString(x - 5, y, "0")
        c.drawRightString(x - 5, y + height - 5, str(max_coverage))

        # Draw X axis labels (hours)
        for slot in range(0, len(coverage) + 1, 4):
            t = schedule.slot_to_time(slot)
            label_x = x + (slot / len(coverage)) * width
            c.drawCentredString(label_x, y - 12, t.strftime("%H"))
