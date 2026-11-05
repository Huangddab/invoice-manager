"""Mainland China workdays from published State Council holiday schedules."""

from calendar import monthrange
from datetime import date, timedelta
import json
import re


# State Council notices: https://www.gov.cn/zhengce/zhengceku/202411/content_6986383.htm
# and https://www.gov.cn/zhengce/content/202511/content_7047090.htm
# A weekend is a workday only when the notice explicitly schedules it as one.
SCHEDULES = {
    2025: {
        "holidays": (
            ("01-01", "01-01"), ("01-28", "02-04"),
            ("04-04", "04-06"), ("05-01", "05-05"),
            ("05-31", "06-02"), ("10-01", "10-08"),
        ),
        "work_weekends": ("01-26", "02-08", "04-27", "09-28", "10-11"),
    },
    2026: {
        "holidays": (
            ("01-01", "01-03"), ("02-15", "02-23"),
            ("04-04", "04-06"), ("05-01", "05-05"),
            ("06-19", "06-21"), ("09-25", "09-27"),
            ("10-01", "10-07"),
        ),
        "work_weekends": ("01-04", "02-14", "02-28", "05-09", "09-20", "10-10"),
    },
}


def previous_month(today=None):
    """Return the previous calendar month, including the January year boundary."""
    today = today or date.today()
    return (today.replace(day=1) - timedelta(days=1)).strftime('%Y-%m')


def schedule_for_year(year):
    if year in SCHEDULES:
        schedule = SCHEDULES[year]
        return {
            'holidays': [(date.fromisoformat(f'{year}-{start}'), date.fromisoformat(f'{year}-{end}'))
                         for start, end in schedule['holidays']],
            'work_weekends': {date.fromisoformat(f'{year}-{day}') for day in schedule['work_weekends']},
        }
    from invoice_manager import database
    path = database.DB_PATH.parent / 'holiday-schedules.json'
    try:
        schedules = json.loads(path.read_text(encoding='utf-8'))
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as error:
        raise ValueError(f'无法读取节假日安排 {path}：{error}') from error
    if not isinstance(schedules, dict):
        raise ValueError('节假日安排应是按年份索引的 JSON 对象')
    schedule = schedules.get(str(year))
    if schedule is None:
        return None
    if (not isinstance(schedule, dict) or not isinstance(schedule.get('holidays'), list)
            or not schedule['holidays'] or not isinstance(schedule.get('work_weekends'), list)):
        raise ValueError(f'{year} 年节假日安排需要 holidays 和 work_weekends 列表')
    try:
        holidays = []
        for interval in schedule['holidays']:
            if not isinstance(interval, list) or len(interval) != 2:
                raise ValueError('假期区间应包含开始和结束日期')
            start, end = (date.fromisoformat(f'{year}-{day}') for day in interval)
            if start > end:
                raise ValueError('假期开始日期晚于结束日期')
            holidays.append((start, end))
        work_weekends = {date.fromisoformat(f'{year}-{day}') for day in schedule['work_weekends']}
        if any(day.weekday() < 5 or any(start <= day <= end for start, end in holidays) for day in work_weekends):
            raise ValueError('调休上班日必须是非假期的周末')
    except (TypeError, ValueError) as error:
        raise ValueError(f'{year} 年节假日安排无效：{error}') from error
    return {'holidays': holidays, 'work_weekends': work_weekends}


def workdays_in_month(month):
    """Return scheduled workdays, or None when the year is not published here."""
    if not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", month):
        return None
    year, month_number = map(int, month.split("-"))
    schedule = schedule_for_year(year)
    if schedule is None:
        return None
    holidays = schedule['holidays']
    work_weekends = schedule['work_weekends']
    return sum(
        day in work_weekends or (day.weekday() < 5 and not any(start <= day <= end for start, end in holidays))
        for number in range(1, monthrange(year, month_number)[1] + 1)
        for day in (date(year, month_number, number),)
    )
