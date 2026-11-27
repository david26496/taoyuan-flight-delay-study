"""Import the user-confirmed trusted archive and audit saved forecast coverage."""
import argparse, collections, csv, hashlib, io, json, re, sys, zipfile
from datetime import date, timedelta
from pathlib import Path
import xml.etree.ElementTree as ET

NS={'t':'urn:oasis:names:tc:opendocument:xmlns:table:1.0','x':'urn:oasis:names:tc:opendocument:xmlns:text:1.0','o':'urn:oasis:names:tc:opendocument:xmlns:office:1.0'}
def sha(b):return hashlib.sha256(b).hexdigest()
def dump(p,x):p.write_text(json.dumps(x,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
def csvout(p,rows):
    if not rows:return
    with p.open('w',newline='',encoding='utf-8') as f:
        w=csv.DictWriter(f,fieldnames=list(rows[0]));w.writeheader();w.writerows(rows)
def dates(a,b):
    d=date.fromisoformat(a)
    while d<=date.fromisoformat(b):yield str(d);d+=timedelta(days=1)
def ods(body):
    with zipfile.ZipFile(io.BytesIO(body)) as z:
        assert z.testzip() is None
        tree=ET.fromstring(z.read('content.xml'))
    tables={}
    for tb in tree.findall('.//t:table',NS):
        rows=[]
        for row in tb.findall('t:table-row',NS):
            cells=[]
            for c in row:
                if c.tag.rsplit('}',1)[-1] not in ['table-cell','covered-table-cell']:continue
                text='\n'.join(''.join(p.itertext()) for p in c.findall('x:p',NS)) or c.get('{'+NS['o']+'}value','')
                n=int(c.get('{'+NS['t']+'}number-columns-repeated',1))
                if n>200:assert not text;n=0
                cells.extend([text]*n)
            n=int(row.get('{'+NS['t']+'}number-rows-repeated',1))
            if n>1000:assert not any(cells);n=0
            rows.extend([cells]*n)
        tables[tb.get('{'+NS['t']+'}name')]=rows
    return tables
def workbook(p,day):
    if p.suffix=='.ods':tables=ods(p.read_bytes())
    else:
        import xlrd
        b=xlrd.open_workbook(file_contents=p.read_bytes())
        tables={s.name:[s.row_values(i) for i in range(s.nrows)] for s in b.sheets()};b.release_resources()
    rows=[[str(v).strip() for v in row] for row in tables[day]]
    assert '航班運量整點人次預報表' in ' '.join(v for r in rows for v in r)
    header=next(i for i,r in enumerate(rows) if r.count('時間區間')==3)
    offsets=[i for i,v in enumerate(rows[header]) if v=='時間區間']
    hours={};values=[]
    for row in rows[header+1:]:
        if not row:continue
        m=re.fullmatch(r'(\d\d):00\s*~\s*\d\d:59',row[offsets[0]])
        if not m:continue
        hour=int(m[1]);assert hour not in hours
        nums=[]
        for off in offsets:
            assert re.fullmatch(rf'{hour:02}:00\s*~\s*{hour:02}:59',row[off])
            block=[float(v.replace(',','')) for v in row[off+1:off+6]]
            assert len(block)==5 and all(v>=0 and v.is_integer() for v in block)
            nums.extend(map(int,block))
        hours[hour]=nums
    assert set(hours)==set(range(24))
    for h in range(24):values.extend(hours[h])
    return sha(json.dumps(values,separators=(',',':')).encode())
def main():
    ap=argparse.ArgumentParser();ap.add_argument('--archive',type=Path,required=True);ap.add_argument('--repo',type=Path,default=Path('.'));ap.add_argument('--xlrd-dir',type=Path)
    args=ap.parse_args();root=args.repo.resolve()
    if args.xlrd_dir:sys.path.insert(0,str(args.xlrd_dir))
    target=root/'raw/taoyuan_supplemental/20261127';target.mkdir(exist_ok=True)
    valid=target/'valid';valid.mkdir(exist_ok=True)
    records=[];coverage=collections.defaultdict(lambda:dict(obs=0,ids=set(),files=set(),done=set()))
    with zipfile.ZipFile(args.archive) as z:
        assert z.testzip() is None
        for member in z.infolist():
            if member.is_dir():continue
            name=member.filename;assert Path(name).name==name and re.fullmatch(r'passenger_flights_202611\d\dT\d{6}\+0800.txt',name)
            body=z.read(member);text=body.decode('cp950');rows=list(csv.reader(text.splitlines()))
            assert rows and all(len(r)==20 for r in rows)
            output=valid/name
            if output.exists():assert output.read_bytes()==body
            else:output.write_bytes(body)
            bydate=collections.Counter()
            for raw in rows:
                r=[x.strip() for x in raw];day=date.fromisoformat(r[6].replace('/','-')).isoformat();bydate[day]+=1
                assert r[1] in ['A','D']
                key=(r[6],r[1],r[2],r[4],r[10],r[7]);v=coverage[day];v['obs']+=1;v['ids'].add(key);v['files'].add(name)
                if 'ARRIVED' in r[13] or 'DEPARTED' in r[13]:v['done'].add(key)
            records.append(dict(path=str(output.relative_to(root)),archive_member=name,size=len(body),sha256=sha(body),encoding='CP950',columns=20,observations=len(rows),scheduled_date_counts=dict(sorted(bydate.items())),
                nominal_snapshot_time=re.search(r'(202611\d\dT\d{6}\+0800)',name)[1],source_trust='USER_CONFIRMED_TRUSTED',collection_log_verified=False))
    flights=[dict(date=d,observations=v['obs'],strict_listing_keys=len(v['ids']),keys_with_completion_observation=len(v['done']),snapshot_files=len(v['files']),requested_window='2026-11-05'<=d<='2026-11-18') for d,v in sorted(coverage.items())]
    manifest=dict(source_archive_name=args.archive.name,source_archive_sha256=sha(args.archive.read_bytes()),source_trust='USER_CONFIRMED_TRUSTED',research_use_authorized=True,original_bytes_preserved=True,collection_timestamp_evidence='original filename; not independently corroborated by acquisition logs',files=records,
        snapshots=len(records),observations=sum(r['observations'] for r in records),unique_content=len({r['sha256'] for r in records}),missing_requested_scheduled_dates=[d for d in dates('2026-11-05','2026-11-18') if d not in coverage])
    dump(target/'manifest.json',manifest);csvout(target/'scheduled_date_coverage.csv',flights)
    (target/'README.md').write_text('''# 11 月補充航班快照（可信來源）

使用者於 2026-11-27 提供「給陳宇辰.zip」，並明確確認這批資料為可信來源。source_trust=USER_CONFIRMED_TRUSTED，research_use_authorized=true；後續研究可納入，不列為不可信或來源待審資料。

valid/ 完整保留 16 份 CP950 原始檔的檔名與 bytes，每筆均為 20 欄。未轉碼、修改內容、刪除共掛或跨快照重複。manifest.json 記錄 ZIP 與逐檔 SHA-256、大小、觀測數及表定日期覆蓋；scheduled_date_coverage.csv 為索引，不是新製作的航班真值或模型標籤。

檔名名義快照時間為 11/4–11/19，內容涵蓋 11/2–11/19，包含使用者要補的 11/5–11/18。快照時間與航班表定日期不同；不能以檔名日期篩掉跨日觀測。保留額外日期可支援邊界航班查核。嚴格身份鍵的統計仍包含共掛班號，不是唯一物理架次；完成觀測數亦不代表全部航班已有可靠無衝突標籤。

資料來源信任已由使用者確認。檔名時間另外保留為原檔提供的時間證據，沒有偽造自動 collector 日誌或 HTTP 取得紀錄。可信來源與預測截止前的實際可得性是不同問題，後續做事前預測仍按各欄位時間證據使用。

這次只新增可信來源與覆蓋索引，沒有更改既有 raw、採集流程、研究身份或模型。重現：以 Python 3.12 執行 scripts/audit_supplemental_20261127.py --archive <原 ZIP> --repo <repo> --xlrd-dir <xlrd 2.0.2 套件目錄>。
''',encoding='utf-8')
    audit=root/'docs/data-audits/20261127';audit.mkdir(parents=True,exist_ok=True)
    logged=collections.defaultdict(list);runs=collections.defaultdict(set)
    for p in (root/'logs/taoyuan_forecast').glob('*.json'):
        log=json.loads(p.read_text());at=log.get('collection_timestamp','')
        if log.get('success'):runs[at[:10]].add(at[11:13])
        for entry in log.get('files',[]):
            if entry.get('raw_file_path') and entry.get('sha256'):
                logged[entry['raw_file_path'],entry['sha256']].append(entry.get('collection_timestamp',at))
    files=[];errors=[];byday=collections.defaultdict(collections.Counter)
    for p in sorted((root/'raw/taoyuan_forecast').glob('*/*/*')):
        if p.suffix not in ['.xls','.ods']:continue
        relative=p.relative_to(root).as_posix();day=p.parts[-3];variant=p.parts[-2];digest=sha(p.read_bytes())
        try:
            assert digest==p.stem.split('__')[-1],'filename hash mismatch'
            semantic=workbook(p,day);match=logged[relative,digest]
            assert match,'no matching log path/hash'
            files.append(dict(path=relative,date=day,variant=variant,format=p.suffix[1:],sha256=digest,valid=True,hours=24,values=360,semantic_sha256=semantic,matching_log_entries=len(match),first_logged=min(match)))
            byday[day][variant+'_'+p.suffix[1:]]+=1
        except Exception as e:
            errors.append(dict(path=relative,error=type(e).__name__+': '+str(e)))
    all_days=sorted(p.name for p in (root/'raw/taoyuan_forecast').iterdir() if p.is_dir() and p.name!='index')
    daily=[]
    for d in dates(all_days[0],min(all_days[-1],'2026-11-27')):
        c=byday[d]
        daily.append(dict(date=d,**{k:c[k] for k in ['initial_xls','initial_ods','hourly_xls','hourly_ods']},daily_initial_present=bool(c['initial_xls'] or c['initial_ods']),daily_hourly_present=bool(c['hourly_xls'] or c['hourly_ods']),successful_collection_hours=len(runs[d]),missing_collection_hours=','.join(f'{h:02}' for h in range(24) if f'{h:02}' not in runs[d]),requested_window='2026-11-05'<=d<='2026-11-18'))
    selected=[r for r in daily if r['requested_window']]
    result=dict(base_commit=__import__('subprocess').check_output(['git','rev-parse','HEAD'],cwd=root,text=True).strip(),audit_date='2026-11-27',checked_report_dates=[all_days[0],all_days[-1]],validated_files=len(files),invalid_files=errors,
        missing_daily_initial=[r['date'] for r in daily if not r['daily_initial_present']],missing_daily_hourly=[r['date'] for r in daily if not r['daily_hourly_present']],
        requested_window=['2026-11-05','2026-11-18'],requested_missing_initial=[r['date'] for r in selected if not r['daily_initial_present']],requested_missing_hourly=[r['date'] for r in selected if not r['daily_hourly_present']],
        requested_collection_hour_counts={r['date']:r['successful_collection_hours'] for r in selected},
        daily_tables_are_not_proof_of_every_hour_snapshot=True,nov27_incomplete_calendar_day=True)
    dump(audit/'forecast_qa.json',result);csvout(audit/'forecast_daily_coverage.csv',daily);csvout(audit/'forecast_file_checks.csv',files)
    dump(audit/'forecast_invalid_files.json',errors)
    print(json.dumps(dict(imported_snapshots=len(records),observations=manifest['observations'],flight_missing_dates=manifest['missing_requested_scheduled_dates'],forecast=result),ensure_ascii=False,indent=2))
if __name__=='__main__':main()
