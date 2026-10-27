"""Independent posting windows for buyers and listings, in the WIB calendar."""
from calendar import monthrange
from datetime import date, datetime, time, timedelta
from typing import Literal
from zoneinfo import ZoneInfo

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, model_validator

WIB = ZoneInfo('Asia/Jakarta')


def date_filter(date_from, date_to, time_from='00:00', time_to='23:59'):
    import re
    if any(not re.fullmatch(r'\d{2}:\d{2}', v) for v in (time_from, time_to)):
        raise HTTPException(400, 'Invalid time; use HH:MM.')
    try:
        lower = datetime.combine(date.fromisoformat(date_from), time.fromisoformat(time_from)) if date_from else None
        upper = datetime.combine(date.fromisoformat(date_to), time.fromisoformat(time_to)) if date_to else None
    except ValueError:
        raise HTTPException(400, 'Invalid date or time.')
    if lower and upper and lower > upper:
        raise HTTPException(400, 'The start of the range cannot be after its end.')
    clause, params = '', []
    if lower:
        clause += ' AND r.sent_at >= %s'
        params.append(lower)
    if upper:
        clause += ' AND r.sent_at < %s'
        params.append(upper + timedelta(minutes=1))
    return clause, params


class MatchingPeriods(BaseModel):
    # Legacy dates still apply to the source when its named window is empty.
    date_from: str = ''
    date_to: str = ''
    time_from: str = '00:00'
    time_to: str = '23:59'
    buyer_date_from: str = ''
    buyer_date_to: str = ''
    listing_date_from: str = ''
    listing_date_to: str = ''

    def windows(self, direction):
        return matching_windows(direction, **self.model_dump(include=set(MatchingPeriods.model_fields)))


def matching_windows(direction, date_from='', date_to='', time_from='00:00', time_to='23:59',
                     buyer_date_from='', buyer_date_to='', listing_date_from='', listing_date_to=''):
    buyer = (buyer_date_from, buyer_date_to)
    listing = (listing_date_from, listing_date_to)
    source, target = (buyer, listing) if direction == 'buyer' else (listing, buyer)
    source = source if any(source) else (date_from, date_to)
    return date_filter(*source, time_from, time_to), date_filter(*target)


def shift_month(day, months):
    index = day.year * 12 + day.month - 1 + months
    year, month = divmod(index, 12)
    return date(year, month + 1, min(day.day, monthrange(year, month + 1)[1]))


class RelativePeriod(BaseModel):
    """Workflow windows are resolved once when the job is queued, never by the worker."""
    model_config = ConfigDict(extra='forbid')
    mode: Literal['all', 'today', 'last_days', 'last_months', 'previous_month', 'custom'] = 'all'
    amount: int = Field(default=1, ge=1, le=1200)
    date_from: str = ''
    date_to: str = ''

    @model_validator(mode='after')
    def validate_custom(self):
        if self.mode == 'custom':
            if not self.date_from or not self.date_to:
                raise ValueError('A custom period needs a start and an end date.')
            date_filter(self.date_from, self.date_to)
        elif self.date_from or self.date_to:
            raise ValueError('Specific dates are only allowed in custom mode.')
        return self

    def resolve(self, today):
        if self.mode == 'all':
            return '', ''
        if self.mode == 'custom':
            return self.date_from, self.date_to
        if self.mode == 'today':
            start = end = today
        elif self.mode == 'last_days':
            start, end = today - timedelta(days=self.amount - 1), today
        elif self.mode == 'last_months':
            start, end = shift_month(today, -self.amount), today
        else:
            end = today.replace(day=1) - timedelta(days=1)
            start = end.replace(day=1)
        return start.isoformat(), end.isoformat()
