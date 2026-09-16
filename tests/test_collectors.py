"""Offline failure/boundary tests: no real credentials or research snapshots."""
import contextlib
from datetime import datetime, timedelta
import hashlib
import http.client
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch, Mock
import urllib.error

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import collection_common as common
import collect_taoyuan as taoyuan
import collect_tdx as tdx

AT = datetime(2026, 9, 17, 0, 17, tzinfo=common.TZ)
FEED = b"1,A,CI,\xa4\xa4\xb5\xd8,0001,A1,2026/09/17,00:00,2026/09/17,00:05,NRT,Narita,\xa4\xe9,ARRIVED,B777,,,,01,\r\n"
ROW = {"FlightDate": "2026-09-17", "FlightNumber": "123", "AirlineID": "CI",
       "DepartureAirportID": "NRT", "ArrivalAirportID": "TPE", "UpdateTime": "2026-09-17T00:10:00+08:00",
       "ActualArrivalTime": None, "CodeShare": "XX999", "future_field": "preserve me"}


def response(body=FEED, status=200, headers=None):
    result = Mock(status=status, headers=headers or {"Content-Type": "text/plain"})
    result.read.return_value = body
    result.__enter__ = Mock(return_value=result)
    result.__exit__ = Mock(return_value=False)
    return result


class CollectionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.addCleanup(self.temp.cleanup)
        for target, value in [("collection_common.ROOT", self.root), ("collect_taoyuan.ROOT", self.root)]:
            p = patch(target, value); p.start(); self.addCleanup(p.stop)
        self.clock = patch("collection_common.now", return_value=AT).start()
        self.addCleanup(patch.stopall)
        patch("collect_taoyuan.now", return_value=AT).start()
        patch("collection_common.time.sleep").start()
        self.output = io.StringIO()
        redirect = contextlib.redirect_stdout(self.output); redirect.__enter__()
        self.addCleanup(redirect.__exit__, None, None, None)

    def records(self):
        return [json.loads(p.read_text()) for p in self.root.glob("logs/*/*.json")]

    def test_window_boundaries(self):
        for at, expected in [(common.START-timedelta(microseconds=1), False), (common.START, True),
                             (common.RESEARCH_END, True), (common.END, True),
                             (common.END+timedelta(microseconds=1), False),
                             (datetime(2027,9,17,tzinfo=common.TZ), False)]:
            with self.subTest(at=at): self.assertEqual(common.in_window(at), expected)
        with self.assertRaises(ValueError):common.in_window(datetime(2026,9,17))

    def test_outside_window_does_not_touch_disk_or_network(self):
        self.clock.return_value = common.START-timedelta(seconds=1)
        with patch.object(common.OPENER,"open") as network:
            self.assertEqual(common.run_collector("tdx",tdx.collect),0)
        network.assert_not_called()
        self.assertEqual(list(self.root.iterdir()),[])

    def test_success_preserves_big5_crlf_and_hash(self):
        with patch.object(common.OPENER,"open",return_value=response()):
            self.assertEqual(common.run_collector("taoyuan",taoyuan.collect),0)
        p=self.root/"raw/taoyuan/a_flight_20260917_001700.txt"
        self.assertEqual(p.read_bytes(),FEED)
        saved=next(r for r in self.records() if r['event']=='snapshot_saved')
        self.assertEqual(saved['sha256'],hashlib.sha256(FEED).hexdigest())
        self.assertEqual(saved['response_size'],len(FEED))
        self.assertEqual(saved['http_status'],200)

    def test_html_even_with_http_200_is_not_saved(self):
        for body,headers in [(b'<html>error</html>',{}),(b'\xff\xfe<\x00h\x00t\x00m\x00l\x00>',{}),
                             (FEED,{'content-type':'text/html'}),(b'',{}),(b'not a feed',{})]:
            with self.subTest(body=body), self.assertRaises(common.CollectionError):taoyuan.validate(body,headers)
        with patch.object(common.OPENER,"open",return_value=response(b'<html>Oops</html>')):
            self.assertEqual(common.run_collector("taoyuan",taoyuan.collect),1)
        self.assertFalse((self.root/'raw').exists())

    def test_no_overwrite_even_on_collision(self):
        p=self.root/'raw/taoyuan/a_flight_20260917_001700.txt'
        common.atomic_new(p,b'original')
        with self.assertRaises(common.CollectionError):common.save_snapshot('taoyuan','a_flight','txt',FEED)
        self.assertEqual(p.read_bytes(),b'original')
        self.assertEqual(list(p.parent.glob('*.tmp')),[])

    def test_retry_backoff_and_timeout(self):
        errors=[urllib.error.HTTPError(taoyuan.SOURCE_URL,503,'Unavailable',{},io.BytesIO(b'error')) for _ in range(2)]
        with patch.object(common.OPENER,'open',side_effect=[*errors,response()]) as network:
            body,_=common.request_bytes(taoyuan.SOURCE_URL,common.RunLog('taoyuan'))
        self.assertEqual(body,FEED)
        self.assertEqual(network.call_count,3)
        self.assertEqual(network.call_args.kwargs['timeout'],30)
        self.assertEqual([x.args[0] for x in common.time.sleep.call_args_list],[2,4])

    def test_retry_exhaustion_and_unauthorized(self):
        for status,attempts in [(503,4),(429,4),(401,1),(404,1)]:
            with self.subTest(status=status):
                def fail(*args,**kwargs):raise urllib.error.HTTPError(taoyuan.SOURCE_URL,status,'error',{},io.BytesIO(b'failed'))
                with patch.object(common.OPENER,'open',side_effect=fail) as network:
                    self.assertEqual(common.run_collector('taoyuan',taoyuan.collect),1)
                self.assertEqual(network.call_count,attempts)
        self.assertFalse((self.root/'raw').exists())

    def test_incomplete_read_is_retried(self):
        incomplete=response();incomplete.read.side_effect=http.client.IncompleteRead(b'partial',100)
        with patch.object(common.OPENER,'open',side_effect=[incomplete,response()]) as network:
            common.request_bytes(taoyuan.SOURCE_URL,common.RunLog('taoyuan'))
        self.assertEqual(network.call_count,2)

    def test_content_length_mismatch_and_compression(self):
        with patch.object(common.OPENER,'open',return_value=response(headers={'Content-Length':'999'})) as network:
            with self.assertRaises(common.CollectionError):common.request_bytes(taoyuan.SOURCE_URL,common.RunLog('taoyuan'))
        self.assertEqual(network.call_count,4)
        with patch.object(common.OPENER,'open',return_value=response(headers={'Content-Encoding':'gzip'})):
            with self.assertRaises(common.CollectionError):common.request_bytes(taoyuan.SOURCE_URL,common.RunLog('taoyuan'))

    def test_final_taoyuan_snapshot_is_only_saved_once(self):
        patch('collect_taoyuan.now',return_value=common.RESEARCH_END).start()
        common.atomic_new(self.root/'raw/taoyuan/a_flight_20260922_000700.txt',FEED)
        with patch.object(common.OPENER,'open') as network:
            self.assertEqual(common.run_collector('taoyuan',taoyuan.collect),0)
        network.assert_not_called()

    def test_tdx_missing_secrets_fails_without_network(self):
        with patch.dict('os.environ',{},clear=True),patch.object(common.OPENER,'open') as network:
            self.assertEqual(common.run_collector('tdx',tdx.collect),1)
        network.assert_not_called()
        self.assertFalse((self.root/'raw').exists())

    def test_token_is_never_logged(self):
        secret='test-only-secret';token='test-only-bearer'
        with patch.dict('os.environ',{'TDX_CLIENT_ID':'test-id','TDX_CLIENT_SECRET':secret}),patch.object(common.OPENER,'open',return_value=response(json.dumps({'access_token':token,'token_type':'Bearer'}).encode())):
            self.assertEqual(tdx.access_token(common.RunLog('tdx')),token)
        all_logs=''.join(p.read_text() for p in self.root.glob('logs/*/*.json'))+self.output.getvalue()
        self.assertNotIn(secret,all_logs);self.assertNotIn(token,all_logs)
        self.assertIsNone(self.records()[0]['sha256'])

    def test_bad_token_response_does_not_leak_body(self):
        with patch.dict('os.environ',{'TDX_CLIENT_ID':'test-id','TDX_CLIENT_SECRET':'secret'}),patch.object(common.OPENER,'open',return_value=response(b'{"error":"sensitive-value"}')):
            self.assertEqual(common.run_collector('tdx',tdx.collect),1)
        self.assertNotIn('sensitive-value',self.output.getvalue())

    def test_tdx_pages_are_original_bytes_and_terminate_on_empty(self):
        raw=b' [\n'+json.dumps(ROW,ensure_ascii=False).encode()+b'\n] \r\n'
        calls=[]
        def save(source,prefix,ext,body):
            calls.append(body);return f'raw/tdx/page{len(calls)}.json',AT.isoformat()
        with patch.object(tdx,'access_token',return_value='test-token'),patch.object(tdx,'request_bytes',side_effect=[(raw,{}),(b' []\r\n',{})]) as network,patch.object(tdx,'save_snapshot',side_effect=save):
            self.assertEqual(common.run_collector('tdx',tdx.collect),0)
        self.assertEqual(calls,[raw,b' []\r\n'])
        self.assertIn('%24skip=1',network.call_args_list[1].args[0])
        self.assertEqual(network.call_count,2)
        self.assertTrue(any(r['event']=='collection_complete' for r in self.records()))

    def test_tdx_repeated_pages_fail_boundedly(self):
        raw=json.dumps([ROW]).encode()
        with patch.object(tdx,'access_token',return_value='test-token'),patch.object(tdx,'request_bytes',return_value=(raw,{})) as network:
            self.assertEqual(common.run_collector('tdx',tdx.collect),1)
        self.assertEqual(network.call_count,2)
        self.assertEqual(len(list(self.root.glob('raw/tdx/*.json'))),1)

    def test_tdx_nulls_and_unknown_fields_are_allowed(self):
        raw=json.dumps([ROW]).encode()
        self.assertEqual(tdx.validate(raw,{}),[ROW])
        for body in [b'{"error":"oops"}',b'null',b'[123]',b'[{"FlightNumber":"1"}]',b'invalid']:
            with self.subTest(body=body),self.assertRaises(common.CollectionError):tdx.validate(body,{})

    def test_redirects_are_not_followed(self):
        self.assertIsNone(common.NoRedirect().redirect_request(None,None,302,'',{},'https://example.com'))

    def test_window_closing_prevents_raw_write(self):
        self.clock.return_value=common.END+timedelta(seconds=1)
        with self.assertRaises(common.WindowClosed):common.save_snapshot('taoyuan','a_flight','txt',FEED)
        self.assertFalse((self.root/'raw').exists())

    def test_partial_collection_at_window_end_is_failure(self):
        def partial(log):
            log.snapshots.append('raw/tdx/existing-page.json')
            raise common.WindowClosed()
        self.assertEqual(common.run_collector('tdx',partial),1)

    def test_network_timeout_exhausts_retries(self):
        with patch.object(common.OPENER,'open',side_effect=TimeoutError()) as network:
            self.assertEqual(common.run_collector('taoyuan',taoyuan.collect),1)
        self.assertEqual(network.call_count,4)
        self.assertFalse((self.root/'raw').exists())


if __name__=='__main__':unittest.main()
