"""Retire TDX cron triggers and provision only the official forecast collector."""
import argparse
import copy
import json
import os
import time
from datetime import datetime
from zoneinfo import ZoneInfo
from configure_external_scheduler import api, REPOSITORY
from collection_common import END
BASE='https://api.github.com/repos/'+REPOSITORY+'/actions/workflows/'
TDX_URLS={BASE+'collect_tdx.yml/dispatches',BASE+'case2_tdx_departure.yml/dispatches'}
TITLE='Taoyuan flightforecast hourly ['+REPOSITORY+']'

def desired_job(token):
    return {'title':TITLE,'url':BASE+'collect_flightforecast.yml/dispatches','enabled':True,
            'saveResponses':False,'requestMethod':1,'requestTimeout':30,'redirectSuccess':False,
            'schedule':{'timezone':'Asia/Taipei','expiresAt':int(END.strftime('%Y%m%d%H%M%S')),
                        'hours':[-1],'minutes':[12],'mdays':[-1],'months':[-1],'wdays':[-1]},
            'extendedData':{'headers':{'Authorization':'Bearer '+token,'Accept':'application/vnd.github+json',
                         'Content-Type':'application/json','X-GitHub-Api-Version':'2026-03-10'},
                         'body':json.dumps({'ref':'main','inputs':{'trigger_source':'cron-job.org'}})}}

def main():
    p=argparse.ArgumentParser();p.add_argument('mode',choices=['apply','status']);args=p.parse_args()
    key=os.getenv('CRON_JOB_API_KEY','').strip();token=os.getenv('SCHEDULER_GITHUB_TOKEN','').strip()
    if not key:raise RuntimeError('missing CRON_JOB_API_KEY')
    inventory=api('GET','/jobs',key)
    if inventory.get('someFailed') or not isinstance(inventory.get('jobs'),list):raise RuntimeError('incomplete job inventory')
    jobs=inventory['jobs'];desired=desired_job(token)
    matches=[j for j in jobs if j.get('title')==TITLE or j.get('url')==desired['url']]
    if len(matches)>1:raise RuntimeError('duplicate forecast jobs; no changes made')
    if matches and (matches[0]['title']!=TITLE or matches[0]['url']!=desired['url']):
        raise RuntimeError('conflicting forecast job identity')
    if args.mode=='apply':
        if not token or datetime.now(ZoneInfo('Asia/Taipei'))>=END:raise RuntimeError('missing scheduler credential or ended window')
        # Pause all existing TDX jobs for this repository, including November jobs.
        for old in jobs:
            if old.get('url') not in TDX_URLS:continue
            before=api('GET',f'/jobs/{old["jobId"]}',key)['jobDetails']
            api('PATCH',f'/jobs/{old["jobId"]}',key,{'job':{'enabled':False}})
            after=api('GET',f'/jobs/{old["jobId"]}',key)['jobDetails']
            if after.get('enabled') is not False:raise RuntimeError('TDX disable readback failed')
            for field in ['url','schedule','extendedData','title','requestMethod']:
                if after.get(field)!=before.get(field):raise RuntimeError('unintended TDX configuration change')
            print(json.dumps({'event':'tdx_cron_disabled','job_id':old['jobId'],'title':old['title'],'enabled':False}),flush=True)
            time.sleep(.3)
        if matches:
            jobid=matches[0]['jobId'];api('PATCH',f'/jobs/{jobid}',key,{'job':desired})
        else:
            jobid=api('PUT','/jobs',key,{'job':desired})['jobId']
        actual=api('GET',f'/jobs/{jobid}',key)['jobDetails']
        for field,value in desired.items():
            if actual.get(field)!=value:raise RuntimeError('forecast job readback mismatch: '+field)
        print(json.dumps({'event':'forecast_cron_ready','job_id':jobid,'enabled':True,
                          'schedule':actual['schedule'],'next_execution':actual.get('nextExecution')}),flush=True)
        final=api('GET','/jobs',key)['jobs']
        assert not any(j.get('enabled') and j.get('url') in TDX_URLS for j in final),'TDX job still active'
    else:
        for job in jobs:
            if job.get('url') not in TDX_URLS|{desired['url']}:continue
            history=api('GET',f'/jobs/{job["jobId"]}/history',key).get('history',[])
            print(json.dumps({'job_id':job['jobId'],'title':job['title'],'enabled':job['enabled'],
                              'schedule':job.get('schedule'),'next_execution':job.get('nextExecution'),
                              'history':[{k:h.get(k) for k in ['date','datePlanned','httpStatus','status']} for h in history[:3]]}),flush=True)

if __name__=='__main__':
    try:main()
    except Exception as exc:
        print(str(exc) if isinstance(exc,RuntimeError) else type(exc).__name__,file=__import__('sys').stderr)
        raise SystemExit(1)
