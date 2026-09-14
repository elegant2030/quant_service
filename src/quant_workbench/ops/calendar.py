from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

CALENDAR_NAMES = {"us": "XNYS", "cn": "XSHG"}


def latest_session(market: str, as_of: date) -> date:
    try:
        import exchange_calendars as calendars
        import pandas as pd
    except ImportError as exc:
        raise RuntimeError("Install ops dependencies: pip install -e '.[ops]'") from exc
    if market not in CALENDAR_NAMES:
        raise ValueError(f"unsupported market: {market}")
    calendar = calendars.get_calendar(CALENDAR_NAMES[market])
    session = calendar.date_to_session(pd.Timestamp(as_of), direction="previous")
    return session.date()


def latest_completed_session(
    market: str,
    now: datetime | None = None,
    availability_delay: timedelta = timedelta(minutes=90),
) -> date:
    try:
        import exchange_calendars as calendars
        import pandas as pd
    except ImportError as exc:
        raise RuntimeError("Install ops dependencies: pip install -e '.[ops]'") from exc
    if market not in CALENDAR_NAMES:
        raise ValueError(f"unsupported market: {market}")
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    current = current.astimezone(timezone.utc)
    calendar = calendars.get_calendar(CALENDAR_NAMES[market])
    session = calendar.date_to_session(pd.Timestamp(current.date()), direction="previous")
    close = calendar.session_close(session).to_pydatetime().astimezone(timezone.utc)
    if current < close + availability_delay:
        session = calendar.previous_session(session)
    return session.date()
