from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
import re

UTC = timezone.utc


def utcnow():
    return datetime.now(UTC)


def stamp(value=None):
    return (value or utcnow()).astimezone(UTC).isoformat(timespec="seconds")


def validate_schedule(daily_time, zone):
    if not re.fullmatch(r"(?:[01][0-9]|2[0-3]):[0-5][0-9]", daily_time or ""):
        raise ValueError("执行时间必须为 HH:MM，例如 08:30。")
    if len(zone) > 64:
        raise ValueError("时区名称过长。")
    try:
        ZoneInfo(zone)
    except (ZoneInfoNotFoundError, ValueError):
        raise ValueError("请输入有效时区，例如 Asia/Shanghai。") from None


def next_run(daily_time, zone, now=None):
    """Return the next strictly future wall-clock occurrence. DST gaps run after the jump."""
    validate_schedule(daily_time, zone)
    now = (now or utcnow()).astimezone(UTC)
    tz = ZoneInfo(zone)
    hour, minute = map(int, daily_time.split(":"))
    local_day = now.astimezone(tz).date()
    for offset in range(3):
        day = local_day + timedelta(days=offset)
        candidate = datetime(day.year, day.month, day.day, hour, minute, tzinfo=tz)
        candidate_utc = candidate.astimezone(UTC)
        if candidate_utc > now:
            return candidate_utc
    raise ValueError("无法计算下次执行时间。")


def display_time(value, zone):
    if not value:
        return "—"
    return datetime.fromisoformat(value).astimezone(ZoneInfo(zone)).strftime("%m-%d %H:%M:%S")
