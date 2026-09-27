"""Time in a question: parsing the range and serving only passages dated inside it."""
import datetime as dt
import unittest

from _support import WorkspaceCase

from carry.recall import recall
from carry.timeframe import label, parse, row_date

SUNDAY = dt.date(2026, 9, 27)


class ParseTest(unittest.TestCase):
    def span(self, q):
        f = parse(q, SUNDAY)
        return f and (f.start.isoformat(), f.end.isoformat(), f.activity)

    def test_relative_weeks_days_and_months(self):
        self.assertEqual(self.span('geçen hafta ne yaptık'), ('2026-09-14', '2026-09-20', True))
        self.assertEqual(self.span('What did we do last week?'), ('2026-09-14', '2026-09-20', True))
        self.assertEqual(self.span('bu hafta neler oldu'), ('2026-09-21', '2026-09-27', True))
        self.assertEqual(self.span('son 7 günde ne yaptım'), ('2026-09-21', '2026-09-27', True))
        self.assertEqual(self.span('dün'), ('2026-09-26', '2026-09-26', True))
        self.assertEqual(self.span('geçen ayki kararlar'), ('2026-08-01', '2026-08-31', True))  # decisions of a month: a listing

    def test_named_months_and_dates(self):
        self.assertEqual(self.span("Eylül'de Jev hakkında ne konuştuk"), ('2026-09-01', '2026-09-30', False))
        self.assertEqual(self.span('mayıs ayında'), ('2026-05-01', '2026-05-31', True))
        self.assertEqual(self.span("ekim'deki notlar"), ('2025-10-01', '2025-10-31', True))  # future month → last year
        self.assertIsNone(self.span('October milestone'))  # a bare month is the topic
        self.assertEqual(self.span('15 ağustos toplantısı'), ('2026-08-15', '2026-08-15', False))
        self.assertEqual(self.span('2026-09-26 ne yaptık'), ('2026-09-26', '2026-09-26', True))

    def test_no_time_no_frame_and_no_false_months(self):
        for q in ('Carry paketleme durumu', 'ayrıca bir şey', 'what may happen next', 'the March release plan'):
            self.assertIsNone(parse(q, SUNDAY), q)

    def test_topic_words_survive_as_the_residual(self):
        f = parse('Geçen hafta Carry hakkında ne karar verdik?', SUNDAY)
        self.assertFalse(f.activity)
        self.assertEqual(f.residual, 'Carry hakkında ne karar verdik?')

    def test_passage_dates_prefer_the_section_then_the_file(self):
        f = parse('geçen hafta', SUNDAY)
        self.assertEqual(row_date('2026-09-15 — sprint', 'notes/X.md', {'created': '2026-06-01'}, f), dt.date(2026, 9, 15))
        self.assertEqual(row_date('Plan', 'log/2026-09-16 — x.md', {}, f), dt.date(2026, 9, 16))
        self.assertEqual(row_date('Plan', 'notes/X.md', {'created': '2026-06-01', 'updated': '2026-09-18'}, f), dt.date(2026, 9, 18))
        self.assertIsNone(row_date('Plan', 'notes/X.md', {}, f))

    def test_labels(self):
        self.assertEqual(label(parse('geçen hafta', SUNDAY)), '14–20 Eylül 2026')
        self.assertEqual(label(parse('dün', SUNDAY)), '26 Eylül 2026')


class TimeframeRecallTest(WorkspaceCase):
    def setUp(self):
        super().setUp()
        self.dated('2026-06-10 — June sync', 'We shipped the June importer and reviewed the week.', folder='log')
        self.dated('2026-09-15 — Launch prep', 'Decided the launch date is October 15. Reviewed the week plan.', folder='log')
        self.dated('2026-09-18 — Pricing', 'Agreed on the pricing tiers for the launch.', folder='log')
        self.dated('2026-09-24 — Retro', 'Retro of the launch week.', folder='log')
        self.build()

    def dated(self, name, body, folder):
        path = self.corpus / folder / (name + '.md')
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f'---\ntype: output\nsummary: "{body}"\n---\n# {name}\n{body}\n', encoding='utf-8')

    def test_what_happened_last_week_lists_only_that_week_newest_first(self):
        r = recall(self.workspace, 'geçen hafta ne yaptık', today=SUNDAY)
        self.assertEqual(r['status'], 'evidence')
        self.assertEqual([e['date'] for e in r['evidence']], ['2026-09-18', '2026-09-15'])
        self.assertEqual(r['diagnostics']['timeframe']['documents'], 2)
        self.assertTrue(r['diagnostics']['timeframe']['listing'])

    def test_a_topic_with_a_time_puts_that_week_first_but_keeps_the_rest(self):
        r = recall(self.workspace, 'last week launch date', today=SUNDAY)
        paths = [e['path'] for e in r['evidence']]
        self.assertEqual(r['evidence'][0]['date'], '2026-09-15')
        self.assertTrue(r['evidence'][0]['in_range'])
        inside = [e['in_range'] for e in r['evidence']]
        self.assertEqual(inside, sorted(inside, reverse=True))  # all inside before any outside
        self.assertIn('log/2026-09-15 — Launch prep.md', paths)

    def test_the_time_of_an_event_does_not_hide_a_note_written_later(self):
        # "in June" is when the importer shipped; the note about it was written in September.
        path = self.corpus / 'notes' / 'Importer.md'
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('---\ntype: thing\ncreated: 2026-09-20\n---\n# Importer\nThe importer billing doubled in June because of retries.\n', encoding='utf-8')
        self.build()
        r = recall(self.workspace, 'why did the importer billing double in June', today=SUNDAY)
        self.assertIn('notes/Importer.md', [e['path'] for e in r['evidence']])

    def test_an_empty_range_says_so(self):
        r = recall(self.workspace, 'geçen yıl ne yaptık', today=SUNDAY)
        self.assertEqual(r['status'], 'no_evidence')
        self.assertEqual(r['diagnostics']['timeframe']['documents'], 0)

    def test_questions_without_time_are_unchanged_and_dated(self):
        r = recall(self.workspace, 'pricing tiers', today=SUNDAY)
        self.assertNotIn('timeframe', r['diagnostics'])
        self.assertEqual(r['evidence'][0]['date'], '2026-09-18')


if __name__ == '__main__':
    unittest.main()
