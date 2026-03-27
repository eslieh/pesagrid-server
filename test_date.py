from datetime import datetime
import calendar

def next_month(dt, target_day):
    month = dt.month + 1
    year = dt.year
    if month > 12:
        month = 1
        year += 1
    
    # Get max days in the new month
    _, max_days = calendar.monthrange(year, month)
    day = min(target_day, max_days)
    
    return dt.replace(year=year, month=month, day=day)

d1 = datetime(2025, 1, 31)
print(d1, "->", next_month(d1, 31))
d2 = datetime(2025, 2, 28)
print(d2, "->", next_month(d2, 31))
