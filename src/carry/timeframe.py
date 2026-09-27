"""Time in a question: "geçen hafta", "yesterday", "son 7 gün", "Eylül'de", "2026-09-26".

Recall ranks passages by what they say, so "what did we do last week" otherwise
matches any note that mentions a week. `parse` turns the time phrase into a date
range and returns the rest of the question; `row_date` reads the date a passage
belongs to from what the vault records (a dated heading, a dated file name, or the
created/date/updated fields). File modification times are not used: syncs and moves
rewrite them in bulk.
"""
import datetime as dt
from dataclasses import dataclass
import re

MONTHS = {
    'ocak': 1, 'şubat': 2, 'subat': 2, 'mart': 3, 'nisan': 4, 'mayıs': 5, 'mayis': 5, 'haziran': 6,
    'temmuz': 7, 'ağustos': 8, 'agustos': 8, 'eylül': 9, 'eylul': 9, 'ekim': 10, 'kasım': 11,
    'kasim': 11, 'aralık': 12, 'aralik': 12,
    'january': 1, 'february': 2, 'march': 3, 'april': 4, 'may': 5, 'june': 6, 'july': 7,
    'august': 8, 'september': 9, 'october': 10, 'november': 11, 'december': 12,
}
MONTH_NAMES_TR = ['', 'Ocak', 'Şubat', 'Mart', 'Nisan', 'Mayıs', 'Haziran', 'Temmuz', 'Ağustos',
                  'Eylül', 'Ekim', 'Kasım', 'Aralık']

# Words that carry no topic: when nothing else is left, the question asks what happened.
GENERIC = set('''
ne neler neydi nelerdi yaptık yaptım yaptın yaptınız yapıldı yaptik yaptim ettik ettim oldu olanlar olan
oldular konuştuk konustuk çalıştık calistik çalıştım ilerledik ilerleme özet özetle özeti özetler
bana bize göster listele hangi hangileri notlar notları notlarım not var mı mi mu mü ve ile ilgili
hakkında hakkinda da de ki bu şu o için icin neler var gelişme gelişmeler yenilik yenilikler durum
what did we i you do done happen happened happening work worked working on show list me my notes
summary summarize summarise any the a an in of about from during was were have has been get got
changes changed progress updates update
ay ayı ayında ayındaki ayda ayki boyunca içinde sırasında süresince günü günde haftası haftasında
karar kararlar kararı kararlarımız verdik verdim verildi aldık aldım alındı konuşuldu yapılanlar yaptıklarım
decide decided decision decisions make made took take
'''.split())

DATE_RE = re.compile(r'(\d{4})-(\d{2})-(\d{2})')


@dataclass(frozen=True)
class Timeframe:
    start: dt.date
    end: dt.date
    phrase: str      # the words that named the time, as written
    residual: str    # the question without them
    activity: bool   # nothing but time and generic words: "what happened then?"

    def contains(self, day):
        return day is not None and self.start <= day <= self.end

    def to_json(self):
        return dict(start=self.start.isoformat(), end=self.end.isoformat(), phrase=self.phrase,
                    residual=self.residual, activity=self.activity)


def _lower(text):
    return text.replace('İ', 'i').replace('I', 'ı').lower()


def _month_range(year, month):
    start = dt.date(year, month, 1)
    nxt = dt.date(year + (month == 12), month % 12 + 1, 1)
    return start, nxt - dt.timedelta(days=1)


def _rules(today):
    monday = today - dt.timedelta(days=today.weekday())
    first = today.replace(day=1)
    last_month_end = first - dt.timedelta(days=1)
    n = r'(\d{1,3}|bir|iki|üç|dört|beş|altı|yedi|on|one|two|three|four|five|six|seven|ten)'
    return [
        (rf'\b(?:son|geçen|önceki|last|past)\s+{n}\s+(?:gün\w*|days?)\b', lambda m: (today - dt.timedelta(days=_num(m.group(1)) - 1), today)),
        (rf'\b(?:son|geçen|önceki|last|past)\s+{n}\s+(?:hafta\w*|weeks?)\b', lambda m: (today - dt.timedelta(days=7 * _num(m.group(1)) - 1), today)),
        (rf'\b(?:son|geçen|önceki|last|past)\s+{n}\s+(?:ay\b\w*|aylık|months?)\b', lambda m: (today - dt.timedelta(days=30 * _num(m.group(1)) - 1), today)),
        (r'\b(?:son\s+(?:bir\s+)?hafta\w*|past\s+week|last\s+seven\s+days)\b', lambda m: (today - dt.timedelta(days=6), today)),
        (r'\b(?:son\s+günlerde|son\s+zamanlarda|lately|recently)\b', lambda m: (today - dt.timedelta(days=13), today)),
        (r'\b(?:evvelsi\s+gün\w*|önceki\s+gün\w*|day\s+before\s+yesterday)\b', lambda m: (today - dt.timedelta(days=2),) * 2),
        (r'\b(?:bugün\w*|today)\b', lambda m: (today, today)),
        (r'\b(?:dün\w*|yesterday)\b', lambda m: (today - dt.timedelta(days=1),) * 2),
        (r'\b(?:geçen|önceki)\s+hafta\w*|\b(?:last|previous)\s+week\b', lambda m: (monday - dt.timedelta(days=7), monday - dt.timedelta(days=1))),
        (r'\bbu\s+hafta\w*|\bthis\s+week\b', lambda m: (monday, today)),
        (r'\b(?:geçen|önceki)\s+ay(?:ki|ın|da|dan)?\b|\b(?:last|previous)\s+month\b', lambda m: _month_range(last_month_end.year, last_month_end.month)),
        (r'\bbu\s+ay(?:ki|ın|da)?\b|\bthis\s+month\b', lambda m: (first, today)),
        (r'\b(?:geçen|önceki)\s+(?:yıl|sene)\w*|\blast\s+year\b', lambda m: (dt.date(today.year - 1, 1, 1), dt.date(today.year - 1, 12, 31))),
        (r'\bbu\s+(?:yıl|sene)\w*|\bthis\s+year\b', lambda m: (dt.date(today.year, 1, 1), today)),
        (r'\b(\d{4})-(\d{2})-(\d{2})\b', lambda m: (dt.date(int(m.group(1)), int(m.group(2)), int(m.group(3))),) * 2),
        (r'\b(\d{1,2})\s+(' + '|'.join(MONTHS) + r')\w*(?:\s+(\d{4}))?', lambda m: _day(m, today)),
        (r'(?:\b(in|during|since)\s+)?\b(' + '|'.join(MONTHS) + r')(?:\'|’)?(\w*)(?:\s+(ay\w*))?(?:\s+(\d{4}))?\b', lambda m: _month(m, today)),
    ]


_WORD_NUM = dict(bir=1, iki=2, üç=3, dört=4, beş=5, altı=6, yedi=7, on=10,
                 one=1, two=2, three=3, four=4, five=5, six=6, seven=7, ten=10)


def _num(token):
    return int(token) if token.isdigit() else _WORD_NUM[token]


def _day(m, today):
    month = MONTHS[m.group(2)]
    year = int(m.group(3)) if m.group(3) else today.year
    day = dt.date(year, month, int(m.group(1)))
    if not m.group(3) and day > today:
        day = day.replace(year=year - 1)
    return day, day


# A month name alone is often the topic ("October milestone"); it names a time only with
# a year, a locative ending (Eylül'de, mayıs ayında) or "in/during".
LOCATIVE = ('de', 'da', 'te', 'ta', 'deki', 'daki', 'teki', 'taki', 'den', 'dan', 'ten', 'tan', 'ında', 'inde', 'unda', 'ünde')


def _month(m, today):
    prep, name, suffix, ay, year = m.groups()
    if not (year or prep or ay or suffix in LOCATIVE):
        raise ValueError('a bare month name is a topic, not a time')
    month = MONTHS[name]
    if year:
        return _month_range(int(year), month)
    year = today.year if month <= today.month else today.year - 1
    return _month_range(year, month)


def parse(query, today=None):
    """The date range a question names, or None."""
    today = today or dt.date.today()
    low = _lower(query)
    for pattern, span in _rules(today):
        m = re.search(pattern, low)
        if not m:
            continue
        try:
            start, end = span(m)
        except (ValueError, KeyError):
            continue
        if start > end:
            start, end = end, start
        residual = (low[:m.start()] + ' ' + low[m.end():])
        # Keep the original casing of the leftover words for lexical search.
        residual_original = (query[:m.start()] + ' ' + query[m.end():]) if len(query) == len(low) else residual
        words = [w for w in re.findall(r"[\w'’]+", residual) if not w.isdigit()]
        activity = all(w.strip("'’") in GENERIC for w in words)
        return Timeframe(start, end, query[m.start():m.end()] if len(query) == len(low) else m.group(0),
                         ' '.join(residual_original.split()), activity)
    return None


def _date(value):
    if isinstance(value, dt.datetime):
        return value.date()
    if isinstance(value, dt.date):
        return value
    m = DATE_RE.search(str(value or ''))
    if not m:
        return None
    try:
        return dt.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return None


def heading_date(heading):
    m = DATE_RE.match((heading or '').strip())
    return _date(m.group(0)) if m else None


def file_dates(path, metadata):
    """Every date the vault records for a file: its name, then created/date/updated."""
    dates = []
    name = path.rsplit('/', 1)[-1]
    d = _date(name) if DATE_RE.match(name) else None
    if d:
        dates.append(d)
    for key in ('created', 'date', 'updated'):
        d = _date((metadata or {}).get(key))
        if d and d not in dates:
            dates.append(d)
    return dates


def row_date(heading, path, metadata, frame=None):
    """The date a passage belongs to. A dated section speaks for itself; otherwise the
    file's dates count, and with a frame the one inside it is preferred."""
    d = heading_date(heading)
    if d:
        return d
    dates = file_dates(path, metadata)
    if frame is not None:
        inside = [x for x in dates if frame.contains(x)]
        if inside:
            return max(inside)
    return dates[0] if dates else None


def label(frame, language='Turkish'):
    """"14–20 Eylül 2026" style, for people."""
    s, e = frame.start, frame.end
    if language != 'Turkish':
        fmt = lambda d, y=True: d.strftime('%-d %B' + (' %Y' if y else ''))
    else:
        fmt = lambda d, y=True: f'{d.day} {MONTH_NAMES_TR[d.month]}' + (f' {d.year}' if y else '')
    if s == e:
        return fmt(s)
    if s.year == e.year and s.month == e.month:
        return f'{s.day}–{fmt(e)}'
    return f'{fmt(s, s.year != e.year)} – {fmt(e)}'
