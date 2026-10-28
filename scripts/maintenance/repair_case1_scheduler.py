"""Narrow, manual repair of existing Case 1 scheduler auth; no new jobs."""
import copy
import json
import os
from pathlib import Path
import sys
import time
import urllib.request
import urllib.error
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from configure_external_scheduler import jobs, select_existing, END
from collection_common import now

REPO = 'david26496/taoyuan-flight-delay-study'
GITHUB = 'https://api.github.com/repos/' + REPO
CRON = 'https://api.cron-job.org'
IDS = {8589253, 8589254, 8589255, 8589257, 8589258}

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None

def request(url, token, method='GET', payload=None):
    req = urllib.request.Request(url, method=method,
        data=json.dumps(payload).encode() if payload is not None else None,
        headers={'Authorization': 'Bearer ' + token, 'Accept': 'application/vnd.github+json',
                 'Content-Type': 'application/json', 'X-GitHub-Api-Version': '2026-03-10'})
    try:
        with urllib.request.build_opener(NoRedirect()).open(req, timeout=30) as r:
            body = r.read()
            return r.status, json.loads(body) if body else {}
    except urllib.error.HTTPError as exc:
        return exc.code, {}  # Never expose response/request/credential details.

def main():
    raise RuntimeError("Legacy TDX reactivation retired; use configure_collection_sources.py")
    key = os.getenv('CRON_JOB_API_KEY', '').strip()
    if not key or now() >= END:
        raise RuntimeError('missing scheduler API key or collection window ended')
    def cron(path, method='GET', payload=None):
        code, data = request(CRON + path, key, method, payload)
        if code != 200:
            raise RuntimeError('scheduler request failed: HTTP ' + str(code))
        time.sleep(.3)
        return data
    inventory = cron('/jobs')
    if inventory.get('someFailed') or not isinstance(inventory.get('jobs'), list):
        raise RuntimeError('incomplete scheduler inventory')
    planned = jobs('NOT_A_CREDENTIAL')
    originals = []
    for desired in planned:
        existing = select_existing(inventory['jobs'], desired)
        if not existing or existing['jobId'] not in IDS:
            raise RuntimeError('expected existing Case 1 job missing; no creation allowed')
        job = cron('/jobs/' + str(existing['jobId']))['jobDetails']
        if job['url'] != desired['url'] or job['schedule'] != desired['schedule'] or job['requestMethod'] != 1:
            raise RuntimeError('Case 1 schedule or endpoint differs; no changes made')
        body = json.loads(job['extendedData']['body'])
        if body != {'ref': 'main', 'inputs': {'trigger_source': 'cron-job.org'}}:
            raise RuntimeError('unexpected Case 1 dispatch body')
        originals.append(job)
    if {j['jobId'] for j in originals} != IDS:
        raise RuntimeError('unexpected job set')

    candidate = os.getenv('SCHEDULER_GITHUB_TOKEN', '').strip()
    def authorized(token):
        return bool(token) and all(request(GITHUB + '/actions/workflows/' + f, token)[0] == 200
                                  for f in ('collect_tdx.yml', 'collect_taoyuan.yml'))
    source = 'existing_repository_secret'
    if not authorized(candidate):
        source = 'existing_case2_job'
        matches = [j for j in inventory['jobs'] if j.get('url') == GITHUB + '/actions/workflows/case2_tdx_departure.yml/dispatches']
        if len(matches) != 1 or not matches[0]['enabled']:
            raise RuntimeError('unique active Case 2 reference job unavailable')
        reference = cron('/jobs/' + str(matches[0]['jobId']))['jobDetails']
        history = cron('/jobs/' + str(matches[0]['jobId']) + '/history').get('history', [])
        if not any(h.get('httpStatus') in (200, 204) and h.get('status') == 1 for h in history[:3]):
            raise RuntimeError('Case 2 reference has no recent successful dispatch')
        headers = reference['extendedData']['headers']
        auth = [v for k,v in headers.items() if k.lower() == 'authorization']
        if len(auth) != 1 or not auth[0].startswith('Bearer '):
            raise RuntimeError('reference authorization unavailable')
        candidate = auth[0][7:]
        if not authorized(candidate):
            raise RuntimeError('reference credential cannot access Case 1 workflows')
    print(json.dumps({'event':'preflight_passed','credential_source':source,'jobs':sorted(IDS)}), flush=True)
    for before in originals:
        extended = copy.deepcopy(before['extendedData'])
        auth_keys = [k for k in extended['headers'] if k.lower() == 'authorization']
        if len(auth_keys) != 1:
            raise RuntimeError('unexpected authorization header count')
        extended['headers'][auth_keys[0]] = 'Bearer ' + candidate
        # API supports partial updates. Preserve all schedule/body/non-auth fields.
        cron('/jobs/' + str(before['jobId']), 'PATCH', {'job':{'enabled':True,'extendedData':extended}})
        after = cron('/jobs/' + str(before['jobId']))['jobDetails']
        dynamic = {'nextExecution','lastExecution','lastStatus','lastDuration','sslCertExpiry'}
        for field in set(before) - dynamic - {'enabled','extendedData'}:
            if after.get(field) != before[field]:
                raise RuntimeError('unexpected readback change for job ' + str(before['jobId']) + ', field ' + field)
        if after.get('extendedData') != extended or after.get('enabled') is not True:
            raise RuntimeError('authentication/enabled readback failed')
        print(json.dumps({'event':'job_repaired','job_id':before['jobId'],'enabled':True,
                          'schedule_unchanged':True,'next_execution':after.get('nextExecution')}), flush=True)
    # Validate authorization to dispatch with the repaired credential, one per collector.
    # This is a repair validation run, not fabricated scheduled evidence.
    for filename in ('collect_tdx.yml', 'collect_taoyuan.yml'):
        code, data = request(GITHUB + '/actions/workflows/' + filename + '/dispatches', candidate, 'POST',
                             {'ref':'main','inputs':{'trigger_source':'manual'}})
        if code not in (200, 204):
            raise RuntimeError('repair validation dispatch failed: HTTP ' + str(code))
        print(json.dumps({'event':'repair_validation_dispatched','workflow':filename,
                          'http_status':code,'run_id':data.get('workflow_run_id')}), flush=True)
    print(json.dumps({'event':'repair_complete','case2_modified':False}), flush=True)

if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        print(str(exc) if isinstance(exc, RuntimeError) else 'Repair failed: ' + type(exc).__name__, file=sys.stderr)
        sys.exit(1)
