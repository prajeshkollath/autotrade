from datetime import date, timedelta


EXPIRY_WEEKDAY = {
    "NIFTY": 3,      # Thursday
    "BANKNIFTY": 2,  # Wednesday
    "SENSEX": 3,     # Thursday
}

MONTH_EXPIRY_WEEKDAY = {
    "NIFTY": 3,
    "BANKNIFTY": 2,
    "SENSEX": 3,
}


def _next_weekday(d: date, weekday: int) -> date:
    """Return the next occurrence of weekday (0=Mon) on or after d."""
    days_ahead = weekday - d.weekday()
    if days_ahead < 0:
        days_ahead += 7
    return d + timedelta(days=days_ahead)


def _last_weekday_of_month(year: int, month: int, weekday: int) -> date:
    """Return the last occurrence of weekday in the given month."""
    if month == 12:
        next_month = date(year + 1, 1, 1)
    else:
        next_month = date(year, month + 1, 1)
    last_day = next_month - timedelta(days=1)
    days_back = (last_day.weekday() - weekday) % 7
    return last_day - timedelta(days=days_back)


def get_expiry_date(ts_date: date, expiry_flag: str, expiry_code: int, underlying: str) -> date:
    """
    Given a timestamp date, expiry_flag (WEEK/MONTH), expiry_code (1/2/3),
    and underlying, return the actual expiry date for that contract.
    """
    weekday = EXPIRY_WEEKDAY.get(underlying, 3)

    if expiry_flag == "WEEK":
        # Current week expiry = next weekday on or after today
        current_expiry = _next_weekday(ts_date, weekday)
        # expiry_code=1 is current, 2 is +1 week, 3 is +2 weeks
        return current_expiry + timedelta(weeks=expiry_code - 1)

    elif expiry_flag == "MONTH":
        # Current month expiry = last weekday of current month
        current_expiry = _last_weekday_of_month(ts_date.year, ts_date.month, weekday)
        if ts_date > current_expiry:
            # Already past this month's expiry, move to next month
            if ts_date.month == 12:
                current_expiry = _last_weekday_of_month(ts_date.year + 1, 1, weekday)
            else:
                current_expiry = _last_weekday_of_month(ts_date.year, ts_date.month + 1, weekday)
        # expiry_code=1 is current month, 2 is next month, 3 is month after
        for _ in range(expiry_code - 1):
            y, m = current_expiry.year, current_expiry.month
            if m == 12:
                current_expiry = _last_weekday_of_month(y + 1, 1, weekday)
            else:
                current_expiry = _last_weekday_of_month(y, m + 1, weekday)
        return current_expiry

    raise ValueError(f"Unknown expiry_flag: {expiry_flag}")
