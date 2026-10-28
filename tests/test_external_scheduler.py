import contextlib
from datetime import datetime, timedelta
import io
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import configure_external_scheduler as scheduler


class ExternalSchedulerTests(unittest.TestCase):
    def test_exact_study_schedule_and_expiration(self):
        start=datetime(2026,10,1,tzinfo=ZoneInfo('Asia/Taipei'))
        for job,expected_count,expected_last in zip(scheduler.jobs('test-only'),
                [104,20,1],['2026-10-31T18:07:00+08:00','2026-11-05T18:07:00+08:00','2026-11-06T00:07:00+08:00']):
            matches=[];at=start;s=job['schedule']
            while at<start+timedelta(days=40):
                if (int(at.strftime('%Y%m%d%H%M%S'))<=s['expiresAt'] and at.month in s['months']
                    and at.day in s['mdays'] and at.minute in s['minutes']
                    and (s['hours']==[-1] or at.hour in s['hours'])):matches.append(at)
                at+=timedelta(minutes=1)
            self.assertEqual(len(matches),expected_count)
            self.assertEqual(matches[-1].isoformat(),expected_last)
            self.assertEqual(s['timezone'],'Asia/Taipei')

    def test_only_dispatch_to_selected_repository(self):
        for job in scheduler.jobs('test-only'):
            self.assertTrue(job['url'].startswith('https://api.github.com/repos/'+scheduler.REPOSITORY+'/actions/workflows/'))
            self.assertEqual(job['requestMethod'],1)
            self.assertFalse(job['saveResponses'])
            self.assertEqual(json.loads(job['extendedData']['body']),{'ref':'main','inputs':{'trigger_source':'cron-job.org'}})

    def test_no_mutation_of_unrelated_or_ambiguous_jobs(self):
        desired=scheduler.jobs('test-only')[0]
        self.assertIsNone(scheduler.select_existing([{'title':'unrelated','url':desired['url']}],desired))
        with self.assertRaises(RuntimeError):scheduler.select_existing([desired,desired],desired)
        with self.assertRaises(RuntimeError):scheduler.select_existing([{'title':desired['title'],'url':'https://example.com'}],desired)

    def test_plan_does_not_read_secrets_or_call_network(self):
        output=io.StringIO()
        with patch.object(sys,'argv',['configure_external_scheduler.py','plan']),patch.object(scheduler,'api') as network,patch.dict('os.environ',{'SCHEDULER_GITHUB_TOKEN':'secret-value'}),contextlib.redirect_stdout(output):
            scheduler.main()
        network.assert_not_called()
        self.assertNotIn('secret-value',output.getvalue())
        self.assertNotIn('Authorization',output.getvalue())
        self.assertEqual(len(output.getvalue().splitlines()),3)
        self.assertNotIn('collect_tdx.yml',output.getvalue())


if __name__=='__main__':unittest.main()
