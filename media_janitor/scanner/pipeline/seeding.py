from dataclasses import dataclass
from datetime import UTC, datetime, timedelta


@dataclass(frozen=True)
class SeedingReqs:
    """Seeding requirements to evaluate against"""

    min_days: int
    min_ratio: float


@dataclass(frozen=True)
class SeedingResult:
    """Outcome of a seeding evaluation"""

    met: bool
    end: datetime | None


def evaluate_seeding(
    completed_on: datetime | None,
    ratio: float | None,
    reqs: SeedingReqs,
    now: datetime | None = None,
) -> SeedingResult:
    """
    Evaluate whether a torrent has met its seeding requirements

    The requirements are met if the share ratio has reached min_ratio, or if the
    torrent has been seeding for at least min_days. Undefined inputs do not satisfy
    their requirements: a None completed_on means the time requirements cannot be met,
    and a None ratio (unreadable) means the ratio requirements cannot be met.

    :param completed_on: When the torrent finished downloading (when seeding starts)
    :param ratio: Torrent ratio
    :param reqs: Seeding requirements
    :param now: Optional, current timestamp (for test injection)
    """
    if now is None:
        now = datetime.now(tz=UTC)

    started = completed_on
    end = started + timedelta(days=reqs.min_days) if started is not None else None

    ratio_requirement = ratio is not None and ratio >= reqs.min_ratio
    time_requirement = end is not None and now >= end

    return SeedingResult(met=ratio_requirement or time_requirement, end=end)
