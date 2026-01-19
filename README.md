# OGP Helper - Workforce Scheduling Tool

A Python-based scheduling tool that creates optimized daily and weekly schedules for 1-80 associates, maximizing on-floor coverage while respecting labor rules, role limits, availability, and capability restrictions.

## Features

### Core Scheduling
- **Smart Scheduling**: Generates optimized shift schedules that maximize floor coverage
- **Policy-Based Rules**: Configurable lunch, break, and shift policies
- **Role Management**: Supports multiple job roles with per-slot capacity limits
- **Constraint Handling**: Respects associate availability, eligibility, and hour limits
- **Validation Engine**: Single source of truth for all constraint checking
- **PDF Output**: Generates printable schedules with timelines and summaries

### Weekly Scheduling
- **Multi-Day Coordination**: Schedule across 7+ days with weekly hour tracking
- **Fairness Balancing**: Equitable distribution of hours across associates
- **Days-Off Patterns**: Enforce consecutive days off, weekend days, etc.
- **Weekly Hour Limits**: Automatic enforcement of 40-hour week caps

### Demand-Aware Optimization
- **Staffing Demand Curves**: Define target staffing levels by time slot
- **OR-Tools CP-SAT Solver**: Optimal schedule generation using constraint programming
- **Demand Matching**: Optimize schedules to match predicted demand
- **Demand Profiles**: Reusable patterns for weekdays, weekends, high-volume days
- **Demand Metrics**: Track how well schedules match demand

## Installation

```bash
# Clone the repository
git clone https://github.com/tigew/ogphelper
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

All commands support PDF output with the `--output` flag, generating professional printable schedules with timelines, coverage charts, and summary statistics.

### Daily Scheduling

```bash
# Run daily demo with 10 associates
ogphelper demo

# Run demo with 30 associates
ogphelper demo --count 30

# Generate PDF output
ogphelper demo --count 20 --output schedule.pdf
```

### Weekly Scheduling

```bash
# Run weekly demo (7 days, 10 associates)
ogphelper weekly-demo

# Customize associate count, days, and days-off pattern
ogphelper weekly-demo --count 20 --days 5 --pattern two_consecutive

# Generate PDF with multi-day timelines and weekly summary
ogphelper weekly-demo --count 15 --output weekly_schedule.pdf

# Different days-off patterns
ogphelper weekly-demo --pattern none              # No enforced pattern
ogphelper weekly-demo --pattern one_weekend_day   # At least one weekend day off
ogphelper weekly-demo --pattern every_other_day   # Work every other day max
```

### Schedule Variety and Shift Limits

```bash
# Control schedule variety (low, medium, high)
ogphelper weekly-demo --variety high              # Maximum diversity in shifts/days off
ogphelper weekly-demo --variety low               # More uniform schedules

# Set random seed for reproducible results
ogphelper weekly-demo --count 30 --seed 12345

# Limit associates per shift block (5 AM - 11 AM, 10 AM - 4 PM, 3 PM - 10 PM)
ogphelper weekly-demo --count 40 --morning-limit 15 --closing-limit 10
ogphelper weekly-demo --morning-limit 20 --day-limit 15 --closing-limit 15

# Combine variety, limits, and output
ogphelper weekly-demo \
  --count 50 \
  --variety high \
  --seed 42 \
  --morning-limit 20 \
  --closing-limit 15 \
  --output variety_schedule.pdf
```

### Realistic Mode (Production-Like Schedules)

Uses real-world shift start distribution (9@5AM, 7@6AM, 5@7AM, 2@8AM, 1@8:30AM, 5@9AM, 1@9:30AM, 3@10AM, 6@11AM, 3@1PM, 5@2PM):

```bash
# Standard realistic mode (47 associates matching real distribution)
ogphelper weekly-demo --realistic

# Scale distribution to different team sizes
ogphelper weekly-demo --realistic --count 80    # Scales up proportionally
ogphelper weekly-demo --realistic --count 20    # Scales down, drops low-count times
ogphelper weekly-demo --realistic --count 5     # Minimal team, 5 start times

# Realistic mode with PDF output
ogphelper weekly-demo --realistic --count 47 --output realistic_schedule.pdf

# Realistic mode produces full-time schedules:
#   - Avg Hours/Associate: ~35
#   - Avg Days/Associate: 5
#   - Each associate has exactly 2 days off built into their availability
```

### Demand-Aware Scheduling

```bash
# Run demand-aware demo with default settings
ogphelper demand-demo

# Use CP-SAT solver for optimal solutions
ogphelper demand-demo --solver cpsat --optimization match_demand

# High-volume demand profile with PDF output
ogphelper demand-demo --count 15 --profile high_volume --output demand_schedule.pdf

# Customize solver time limit and optimization mode
ogphelper demand-demo --solver cpsat --time-limit 60 --optimization minimize_undercoverage

# All demand-demo options
ogphelper demand-demo \
  --count 20 \
  --days 7 \
  --solver hybrid \
  --optimization balanced \
  --profile weekday \
  --time-limit 30 \
  --output schedule.pdf
```

### PDF Output Features

All generated PDFs include:
- **Timeline View**: Color-coded visual representation of each associate's shift
- **Shift Details**: Start/end times, lunch breaks (L), rest breaks (B)
- **Role Assignments**: Color-coded by job role (Picking, GMD/SM, Exception, Staging, Backroom)
- **Coverage Charts**: Hourly staffing levels across the day
- **Summary Statistics**: Total hours, coverage min/max/avg, role distribution
- **Weekly Summary** (for weekly/demand commands): Fairness metrics, hours distribution chart

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

| Role | Default Cap | Description |
|------|-------------|-------------|
| Picking | Unlimited | Primary overflow role |
| GMD/SM | 2 | General Merchandise Dispensing supervisor |
| Exception/SM | 2 | Exception handling supervisor |
| Staging | 2 | Order staging |
| Backroom | 8 | Backroom operations |
| S/R | 2 | Seasonal and Regulated items |

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
├── domain/                     # Data models and business rules
│   ├── models.py               # Associate, TimeSlot, Schedule, etc.
│   ├── policies.py             # Lunch, break, shift policies
│   └── demand.py               # Demand curves and profiles
├── scheduling/                 # Schedule generation
│   ├── candidate_generator.py  # Generate shift options
│   ├── heuristic_solver.py     # Greedy optimization
│   ├── scheduler.py            # Daily scheduling entry point
│   ├── weekly_scheduler.py     # Weekly scheduling
│   ├── cpsat_solver.py         # OR-Tools CP-SAT solver
│   └── demand_aware_scheduler.py  # Demand-aware scheduling
├── validation/                 # Constraint checking
│   └── validator.py            # Single source of truth
├── output/                     # Output generation
│   └── pdf_generator.py        # PDF schedules
└── cli.py                      # Command-line interface
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

### Operating Window Configuration

Configure the daily schedule boundaries:

```python
from ogphelper.domain.models import ScheduleRequest

request = ScheduleRequest(
    schedule_date=date.today(),
    associates=associates,
    day_start_minutes=300,   # 5:00 AM (minutes from midnight)
    day_end_minutes=1320,    # 10:00 PM
    slot_minutes=15,         # 15-minute time slots
)
```

### Shift Policy Configuration

Control minimum and maximum work hours:

```python
from ogphelper.domain.policies import DefaultShiftPolicy
from ogphelper.scheduling import Scheduler

# Custom shift bounds (in minutes)
shift_policy = DefaultShiftPolicy(
    min_work=180,    # 3 hours minimum shift
    max_work=540,    # 9 hours maximum shift
)

scheduler = Scheduler(shift_policy=shift_policy)
```

| Parameter | Default | Description |
|-----------|---------|-------------|
| `min_work` | 240 (4h) | Minimum work minutes per shift |
| `max_work` | 480 (8h) | Maximum work minutes per shift (excluding lunch) |

### Lunch Policy Configuration

Configure lunch break rules based on shift length:

```python
from ogphelper.domain.policies import DefaultLunchPolicy
from ogphelper.scheduling import Scheduler

lunch_policy = DefaultLunchPolicy(
    no_lunch_threshold=360,      # No lunch if working < 6 hours
    short_lunch_threshold=390,   # 30-min lunch if 6-6.5 hours
    short_lunch_duration=30,     # Duration of short lunch
    long_lunch_duration=60,      # Duration of long lunch (>= 6.5 hours)
)

scheduler = Scheduler(lunch_policy=lunch_policy)
```

| Parameter | Default | Description |
|-----------|---------|-------------|
| `no_lunch_threshold` | 360 (6h) | No lunch required below this work time |
| `short_lunch_threshold` | 390 (6.5h) | Short lunch for work time between thresholds |
| `short_lunch_duration` | 30 | Minutes for short lunch break |
| `long_lunch_duration` | 60 | Minutes for long lunch break |

### Break Policy Configuration

Configure rest break rules:

```python
from ogphelper.domain.policies import DefaultBreakPolicy
from ogphelper.scheduling import Scheduler

break_policy = DefaultBreakPolicy(
    one_break_threshold=300,   # 1 break if working 5+ hours
    two_break_threshold=480,   # 2 breaks if working 8+ hours
    break_duration=15,         # Minutes per break
)

scheduler = Scheduler(break_policy=break_policy)
```

| Parameter | Default | Description |
|-----------|---------|-------------|
| `one_break_threshold` | 300 (5h) | Work time requiring 1 break |
| `two_break_threshold` | 480 (8h) | Work time requiring 2 breaks |
| `break_duration` | 15 | Duration of each break in minutes |

### Role Caps Configuration

Limit how many associates can be assigned to each role per time slot:

```python
from ogphelper.domain.models import JobRole, ScheduleRequest

request = ScheduleRequest(
    schedule_date=date.today(),
    associates=associates,
    job_caps={
        JobRole.PICKING: 999,       # Unlimited (overflow role)
        JobRole.GMD_SM: 3,          # Max 3 per slot
        JobRole.EXCEPTION_SM: 3,    # Max 3 per slot
        JobRole.STAGING: 4,         # Max 4 per slot
        JobRole.BACKROOM: 12,       # Max 12 per slot
        JobRole.SR: 2,              # Max 2 per slot (Seasonal/Regulated)
    },
)
```

### Associate Configuration

Configure individual associate constraints:

```python
from ogphelper.domain.models import Associate, Availability, JobRole, Preference

associate = Associate(
    id="A001",
    name="Alice",
    availability={
        date.today(): Availability(start_slot=0, end_slot=68),      # Full day
        date.today() + timedelta(days=1): Availability.off_day(),   # Day off
    },
    max_minutes_per_day=480,           # 8 hours daily max
    max_minutes_per_week=2400,         # 40 hours weekly max
    supervisor_allowed_roles={JobRole.PICKING, JobRole.STAGING},  # Approved roles
    cannot_do_roles={JobRole.BACKROOM},                           # Physical restrictions
    role_preferences={
        JobRole.PICKING: Preference.PREFER,    # Soft preference
        JobRole.GMD_SM: Preference.AVOID,      # Soft avoidance
    },
)
```

| Constraint Type | Description |
|-----------------|-------------|
| `supervisor_allowed_roles` | Hard constraint - roles approved by supervisor |
| `cannot_do_roles` | Hard constraint - roles associate physically cannot do |
| `role_preferences` | Soft constraint - preferred/avoided roles (optimizer tries to respect) |

### Weekly Schedule Configuration

Configure multi-day scheduling with fairness balancing:

```python
from ogphelper.domain.models import (
    WeeklyScheduleRequest,
    DaysOffPattern,
    FairnessConfig,
)

request = WeeklyScheduleRequest(
    start_date=date.today(),
    end_date=date.today() + timedelta(days=6),
    associates=associates,
    days_off_pattern=DaysOffPattern.TWO_CONSECUTIVE,
    required_days_off=2,
    busy_days={date.today() + timedelta(days=5)},  # Saturday is busy
    fairness_config=FairnessConfig(
        target_weekly_minutes=2000,    # Target 33 hours/week
        min_weekly_minutes=1200,       # At least 20 hours/week
        max_hours_variance=120.0,      # Allow 2 hour variance between associates
        weight_hours_balance=0.7,      # 70% weight on hours fairness
        weight_days_balance=0.3,       # 30% weight on days worked fairness
    ),
)
```

| Days Off Pattern | Description |
|------------------|-------------|
| `NONE` | No pattern enforced |
| `TWO_CONSECUTIVE` | Two consecutive days off required |
| `ONE_WEEKEND_DAY` | At least one weekend day off |
| `EVERY_OTHER_DAY` | Cannot work more than every other day |

### Demand-Aware Scheduling

```python
from datetime import date, timedelta
from ogphelper.domain.demand import DemandCurve, DemandProfile, WeeklyDemand
from ogphelper.domain.models import WeeklyScheduleRequest, DaysOffPattern
from ogphelper.scheduling import (
    DemandAwareWeeklyScheduler,
    DemandAwareConfig,
    SolverType,
    SolverConfig,
    OptimizationMode,
)

# Create demand profiles
weekday_profile = DemandProfile.create_weekday_profile()
weekend_profile = DemandProfile.create_weekend_profile()

# Create weekly demand with automatic weekday/weekend patterns
start_date = date.today()
weekly_demand = WeeklyDemand.create_standard_week(
    start_date,
    weekday_profile=weekday_profile,
    weekend_profile=weekend_profile,
)

# Or create custom demand curves
custom_curve = DemandCurve.create_default(
    schedule_date=start_date,
    base_demand=5,      # Base staffing level
    peak_demand=12,     # Peak hour staffing
    peak_hours=(10, 14), # Peak from 10 AM to 2 PM
)

# Configure the solver
solver_config = SolverConfig(
    time_limit_seconds=60.0,            # CP-SAT time limit
    optimization_mode=OptimizationMode.BALANCED,  # Balance coverage and demand
    demand_weight=40,                   # Weight for demand matching
    coverage_weight=30,                 # Weight for coverage maximization
)

config = DemandAwareConfig(
    solver_type=SolverType.HYBRID,      # Try CP-SAT, fall back to heuristic
    solver_config=solver_config,
    weekly_demand=weekly_demand,
    track_demand_metrics=True,
)

# Create scheduler and generate
scheduler = DemandAwareWeeklyScheduler(config=config)

request = WeeklyScheduleRequest(
    start_date=start_date,
    end_date=start_date + timedelta(days=6),
    associates=associates,
    days_off_pattern=DaysOffPattern.TWO_CONSECUTIVE,
)

result = scheduler.generate_schedule(request, weekly_demand)

# Access results
print(f"Overall demand match: {result.overall_match_score:.1f}%")
print(f"Fairness score: {result.schedule.fairness_metrics.fairness_score:.1f}")

# Check daily demand metrics
for d, metrics in result.demand_metrics.items():
    print(f"{d}: {metrics.match_score:.1f}% match")
```

### Solver Options

| Solver Type | Description | Best For |
|-------------|-------------|----------|
| `heuristic` | Fast greedy algorithm | Quick schedules, large teams |
| `cpsat` | OR-Tools constraint programming | Optimal solutions, smaller teams |
| `hybrid` | Try CP-SAT, fall back to heuristic | Balanced approach (default) |

### Optimization Modes

| Mode | Description |
|------|-------------|
| `maximize_coverage` | Maximize total on-floor coverage |
| `match_demand` | Minimize difference from demand curve |
| `minimize_undercoverage` | Prioritize avoiding understaffing |
| `balanced` | Balance all objectives (default) |

## Future Enhancements
- [ ] Real-time schedule adjustments
- [ ] Machine learning demand prediction
- [ ] Multi-location scheduling
- [ ] Shift swapping and trade requests

## License

MIT License

## Contributing

Contributions welcome! Please ensure:
- Code follows PEP 8 (use `black` and `ruff`)
- All tests pass
- New features have tests
- Documentation is updated
