# OGP Helper - Workforce Scheduling Tool

A Python-based scheduling tool that creates optimized daily schedules for 1-80 associates, maximizing on-floor coverage while respecting labor rules, role limits, availability, and capability restrictions.

## Features

- **Smart Scheduling**: Generates optimized shift schedules that maximize floor coverage
- **Policy-Based Rules**: Configurable lunch, break, and shift policies
- **Role Management**: Supports multiple job roles with per-slot capacity limits
- **Constraint Handling**: Respects associate availability, eligibility, and hour limits
- **Validation Engine**: Single source of truth for all constraint checking
- **PDF Output**: Generates printable schedules with timelines and summaries

## Installation

```bash
# Clone the repository
git clone <repository-url>
cd ogphelper

# Install in development mode
pip install -e ".[dev]"
```

## Quick Start

```python
from datetime import date
from ogphelper.domain.models import (
    Associate,
    Availability,
    JobRole,
    ScheduleRequest,
)
from ogphelper.scheduling import Scheduler
from ogphelper.validation import ScheduleValidator
from ogphelper.output import PDFGenerator

# Create associates
associates = [
    Associate(
        id="A001",
        name="Alice",
        availability={date.today(): Availability(start_slot=0, end_slot=68)},
        supervisor_allowed_roles=set(JobRole),
    ),
    # ... more associates
]

# Create schedule request
request = ScheduleRequest(
    schedule_date=date.today(),
    associates=associates,
)

# Generate schedule
scheduler = Scheduler()
schedule = scheduler.generate_schedule(request)

# Validate
validator = ScheduleValidator()
associates_map = {a.id: a for a in associates}
result = validator.validate(schedule, request, associates_map)

if result.is_valid:
    # Generate PDF
    generator = PDFGenerator()
    generator.generate(schedule, associates_map, "schedule.pdf")
```

## Command Line Interface

```bash
# Run demo with 10 associates
ogphelper demo

# Run demo with 30 associates
ogphelper demo --count 30

# Generate PDF output
ogphelper demo --count 20 --output schedule.pdf
```

## Core Concepts

### Operating Window

- Schedule day: **5:00 AM to 10:00 PM**
- Time slots: **15-minute intervals** (68 slots per day)

### Shift Rules

| Rule | Value |
|------|-------|
| Minimum work time | 4 hours |
| Maximum work time | 8 hours |
| Note | Lunch is NOT counted toward 8-hour max |

### Lunch Policy

| Work Duration | Lunch Duration |
|--------------|----------------|
| < 6 hours | No lunch |
| 6 - 6.5 hours | 30 minutes |
| >= 6.5 hours | 60 minutes |

Lunch timing is flexible (+/-30 min normally, +/-60 min on busy days) to optimize coverage.

### Break Policy

| Work Duration | Break Count |
|--------------|-------------|
| < 5 hours | 0 breaks |
| 5 - 8 hours | 1 break |
| >= 8 hours | 2 breaks |

Default break duration: 15 minutes each.

### Job Roles

| Role | Default Cap |
|------|-------------|
| Picking | Unlimited (overflow) |
| GMD/SM | 2 |
| Exception/SM | 2 |
| Staging | 2 |
| Backroom | 8 |

### Associate Constraints

Each associate has:
- **Availability**: Time window per day when they can work
- **Max hours**: Daily and weekly limits
- **Supervisor allowed roles**: Hard constraint - what roles the supervisor approves
- **Cannot-do roles**: Hard constraint - physical limitations
- **Preferences**: Soft constraint - prefer/avoid certain roles

## Architecture

```
src/ogphelper/
├── domain/           # Data models and business rules
│   ├── models.py     # Associate, TimeSlot, Schedule, etc.
│   └── policies.py   # Lunch, break, shift policies
├── scheduling/       # Schedule generation
│   ├── candidate_generator.py  # Generate shift options
│   ├── heuristic_solver.py     # Greedy optimization
│   └── scheduler.py            # Main entry point
├── validation/       # Constraint checking
│   └── validator.py  # Single source of truth
├── output/           # Output generation
│   └── pdf_generator.py  # PDF schedules
└── cli.py            # Command-line interface
```

## Scheduling Algorithm

The scheduler uses a greedy heuristic approach:

1. **Candidate Generation**: Generate all feasible shift options per associate
2. **Shift Selection**: Greedily select shifts that maximize coverage
3. **Lunch Placement**: Position lunches to minimize coverage gaps
4. **Break Placement**: Place breaks at optimal points (1/3, 2/3 of shift)
5. **Role Assignment**: Assign roles respecting caps and eligibility

## Validation

Every schedule is validated against:

- Shift within operating window (5 AM - 10 PM)
- Shift within associate availability
- Work time within min/max bounds (4-8 hours)
- Correct lunch duration for work time
- Correct break count and duration
- Job assignments respect eligibility
- Role caps not exceeded at any slot
- Daily/weekly hour limits not exceeded

## Testing

```bash
# Run all tests
pytest

# Run with coverage
pytest --cov=ogphelper

# Run specific test file
pytest tests/test_policies.py
```

## Configuration

### Custom Policies

```python
from ogphelper.domain.policies import (
    DefaultShiftPolicy,
    DefaultLunchPolicy,
    DefaultBreakPolicy,
)
from ogphelper.scheduling import Scheduler

# Custom shift bounds
shift_policy = DefaultShiftPolicy(min_work=180, max_work=540)

# Custom lunch thresholds
lunch_policy = DefaultLunchPolicy(
    no_lunch_threshold=300,  # 5 hours
    short_lunch_threshold=360,  # 6 hours
    short_lunch_duration=20,
    long_lunch_duration=45,
)

# Custom break settings
break_policy = DefaultBreakPolicy(
    one_break_threshold=240,  # 4 hours
    two_break_threshold=420,  # 7 hours
    break_duration=10,
)

scheduler = Scheduler(
    shift_policy=shift_policy,
    lunch_policy=lunch_policy,
    break_policy=break_policy,
)
```

### Custom Role Caps

```python
from ogphelper.domain.models import JobRole, ScheduleRequest

request = ScheduleRequest(
    schedule_date=date.today(),
    associates=associates,
    job_caps={
        JobRole.PICKING: 999,
        JobRole.GMD_SM: 3,      # Increased
        JobRole.EXCEPTION_SM: 3,
        JobRole.STAGING: 4,
        JobRole.BACKROOM: 12,   # Increased
    },
)
```

## Roadmap

### Phase 1: Daily Scheduling (Current)
- [x] Generate feasible shift options
- [x] Maximize coverage with heuristic solver
- [x] Place lunches and breaks optimally
- [x] Assign roles with cap enforcement
- [x] Full validation suite
- [x] PDF output

### Phase 2: Weekly Scheduling
- [ ] Multi-day scheduling
- [ ] Weekly hour enforcement
- [ ] Fairness balancing
- [ ] Days-off patterns

### Phase 3: Demand-Aware Optimization
- [ ] Staffing demand curves
- [ ] OR-Tools CP-SAT integration
- [ ] Demand matching optimization

## License

MIT License

## Contributing

Contributions welcome! Please ensure:
- Code follows PEP 8 (use `black` and `ruff`)
- All tests pass
- New features have tests
- Documentation is updated
