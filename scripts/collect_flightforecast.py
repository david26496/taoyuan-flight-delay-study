"""Archive official initial/hourly forecast XLS and ODS, preserving exact bytes."""
import argparse
from datetime import timedelta, date
import hashlib
import io
import json
from pathlib import Path
import re
import sys
import uuid
import xml.etree.ElementTree as ET
import zipfile
from urllib.parse import urlsplit
from collection_common import ROOT, END, CollectionError, atomic_new, in_window, now, request_bytes, reject_empty_or_html

PAGE = 'https://www.taoyuanairport.com.tw/flightforecast'
INDEX = 'https://www.taoyuanairport.com.tw/api/api/thirdparty/fos'
RAW = Path('raw/taoyuan_forecast')
LOGS = Path('logs/taoyuan_forecast')
FILE_RE = re.compile(r'^/uploads/fos/(20\d{2}_\d{2}_\d{2})(_update)?\.(xls|ods)$')
NS = {'table':'urn:oasis:names:tc:opendocument:xmlns:table:1.0',
      'text':'urn:oasis:names:tc:opendocument:xmlns:text:1.0'}

class Audit:
    def __init__(self, root):
        self.root = Path(root)
        self.id = uuid.uuid4().hex
        self.at = now()
        self.events = []
        self.files = []
        self.errors = []
    def emit(self, event, **fields):
        self.events.append(dict(event=event, collection_timestamp=now().isoformat(), **fields))
    def finish(self, success):
        record = dict(event='collection_complete' if success else 'collection_failed',
                      success=success, collection_timestamp=self.at.isoformat(), timezone='Asia/Taipei',
                      source='TAOYUAN_FLIGHTFORECAST', page_url=PAGE, index_url=INDEX,
                      run_id=self.id, github_run_id=__import__('os').getenv('GITHUB_RUN_ID'),
                      github_run_attempt=__import__('os').getenv('GITHUB_RUN_ATTEMPT'),
                      trigger_source=__import__('os').getenv('COLLECTION_TRIGGER','local'),
                      saved=sum(f['result']=='saved' for f in self.files),
                      unchanged=sum(f['result']=='unchanged' for f in self.files),
                      historical_cached=sum(f['result']=='historical_cached' for f in self.files),
                      files=self.files, errors=self.errors, requests=self.events)
        path = self.root / LOGS / f'{self.at:%Y%m%d_%H%M%S}_{self.id}.json'
        atomic_new(path, (json.dumps(record,ensure_ascii=False,sort_keys=True)+'\n').encode())
        print(json.dumps({k:v for k,v in record.items() if k not in ('files','requests')},ensure_ascii=False))

def links(body):
    try: obj=json.loads(body)
    except (ValueError,UnicodeError): raise CollectionError('invalid official forecast index') from None
    if not isinstance(obj,dict): raise CollectionError('index must be object')
    result=[];seen=set()
    for key,variant in [('original','initial'),('updates','hourly')]:
        rows=obj.get(key)
        if not isinstance(rows,list) or not rows: raise CollectionError('missing initial/hourly index lists')
        for row in rows:
            url=row.get('path','') if isinstance(row,dict) else ''
            parsed=urlsplit(url);match=FILE_RE.fullmatch(parsed.path)
            if parsed.scheme!='https' or parsed.netloc!='www.taoyuan-airport.com' or parsed.query or parsed.fragment or not match:
                raise CollectionError('unexpected forecast download link')
            if bool(match[2]) != (variant=='hourly') or row.get('extname')!='xls':
                raise CollectionError('forecast variant or format mismatch')
            report_date=date.fromisoformat(match[1].replace('_','-'))
            for ext in ['xls','ods']:
                # Both links are displayed on the official page; ODS shares the same stem.
                item={'url':url[:-3]+ext,'date':report_date.isoformat(),'variant':variant,'format':ext}
                if item['url'] not in seen:result.append(item);seen.add(item['url'])
    return result

def validate(body, headers, item):
    reject_empty_or_html(body,headers)
    if len(body)>10*1024*1024: raise CollectionError('forecast file unexpectedly large')
    try:
        if item['format']=='xls':
            import xlrd
            if not body.startswith(bytes.fromhex('d0cf11e0a1b11ae1')):raise ValueError('XLS signature')
            book=xlrd.open_workbook(file_contents=body)
            sheets=book.sheet_names()
            texts=[str(s.cell_value(r,c)) for s in book.sheets() for r in range(min(20,s.nrows)) for c in range(s.ncols)]
            dimensions=[{'name':s.name,'rows':s.nrows,'columns':s.ncols} for s in book.sheets()]
            book.release_resources()
        else:
            with zipfile.ZipFile(io.BytesIO(body)) as z:
                if sum(x.file_size for x in z.infolist())>20*1024*1024 or z.testzip() is not None:raise ValueError('ODS zip')
                # Official ODS files omit mimetype; validate actual spreadsheet XML instead.
                tree=ET.fromstring(z.read('content.xml'))
            tables=tree.findall('.//table:table',NS)
            sheets=[t.get('{'+NS['table']+'}name') for t in tables]
            texts=[''.join(p.itertext()) for p in tree.findall('.//text:p',NS)]
            dimensions=[{'name':name,'xml_rows':len(t.findall('table:table-row',NS))} for t,name in zip(tables,sheets)]
        text=' '.join(texts)
        if item['date'] not in sheets or '航班運量整點人次預報表' not in text:
            raise ValueError('wrong report date or title')
        generated=re.search(r'報表產出時間[：:]\s*(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2})',text)
        return {'sheets':dimensions,'report_generated_at':generated[1] if generated else None}
    except (ValueError,KeyError,ET.ParseError,zipfile.BadZipFile) as exc:
        raise CollectionError('invalid forecast workbook: '+type(exc).__name__) from None
    except ImportError:
        raise CollectionError('missing dependency: install requirements-flightforecast.txt') from None
    except Exception as exc:
        raise CollectionError('workbook parse failed: '+type(exc).__name__) from None

def publish(root,relative,body):
    path=root/relative
    if path.exists():
        if path.read_bytes()!=body:raise CollectionError('existing immutable content mismatch')
        return 'unchanged'
    try:atomic_new(path,body)
    except FileExistsError:
        if path.read_bytes()!=body:raise CollectionError('immutable publication collision')
        return 'unchanged'
    if hashlib.sha256(path.read_bytes()).digest()!=hashlib.sha256(body).digest():
        raise CollectionError('persisted SHA256 mismatch')
    return 'saved'

def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output-root',type=Path,default=ROOT)
    p.add_argument('--refresh-history',action='store_true',help='Recheck all published history instead of reusing already archived old reports')
    p.add_argument('--current-only',action='store_true',help='Staging smoke test: today and tomorrow only')
    args=p.parse_args(argv);audit=Audit(args.output_root.resolve())
    try:
        if not in_window(now()):raise CollectionError('outside existing study window, ending '+END.isoformat())
        body,headers=request_bytes(INDEX,audit,headers={'Referer':PAGE,'Accept':'application/json'})
        reject_empty_or_html(body,headers);items=links(body)
        digest=hashlib.sha256(body).hexdigest()
        audit.emit('index_saved',raw_file_path=(RAW/'index'/f'{digest}.json').as_posix(),sha256=digest,
                   result=publish(audit.root,RAW/'index'/f'{digest}.json',body))
        today=now().date()
        for item in items:
            if args.current_only and item['date']<today.isoformat():continue
            folder=RAW/item['date']/item['variant'];stem=Path(urlsplit(item['url']).path).stem
            cached=sorted((audit.root/folder).glob(stem+'__*.'+item['format']))
            if cached and not args.refresh_history and item['date']<(today-timedelta(days=1)).isoformat():
                audit.files.append(dict(**item,result='historical_cached',raw_file_paths=[x.relative_to(audit.root).as_posix() for x in cached]));continue
            try:
                original,h=request_bytes(item['url'],audit,headers={'Referer':PAGE})
                meta=validate(original,h,item);sha=hashlib.sha256(original).hexdigest()
                relative=folder/f'{stem}__{sha}.{item["format"]}'
                result=publish(audit.root,relative,original)
                audit.files.append(dict(**item,**meta,result=result,http_status=200,
                    content_type=h.get('content-type'),last_modified=h.get('last-modified'),
                    collection_timestamp=now().isoformat(),raw_file_path=relative.as_posix(),
                    file_size=len(original),sha256=sha))
            except (CollectionError,OSError,ValueError) as exc:
                audit.errors.append(dict(**item,error=str(exc) if isinstance(exc,CollectionError) else type(exc).__name__))
        audit.finish(not audit.errors)
        return int(bool(audit.errors))
    except (CollectionError,OSError,ValueError) as exc:
        audit.errors.append({'error':str(exc) if isinstance(exc,CollectionError) else type(exc).__name__})
        audit.finish(False);return 1

if __name__=='__main__':sys.exit(main())
