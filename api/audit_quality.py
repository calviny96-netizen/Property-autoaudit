"""Repeatable local evidence audit; review raw requirements, not score alone.

Example: python audit_quality.py --search caesar --output /tmp/caesar-audit.json
The output contains private source messages; do not publish it.
"""
import argparse,json,time,statistics
from pathlib import Path
from db import connect
from matching_rules import HOT_MIN_SCORE,assess_pair,prepare_document
from location_index import load_index
from workspace import workspace


def audit(search):
    timings=[]
    for _ in range(3):
        started=time.perf_counter();page=workspace(direction='property',search=search)
        timings.append(round((time.perf_counter()-started)*1000,1))
    with connect() as c:
        settings=c.execute("SELECT * FROM xm.match_settings WHERE company_id='xm'").fetchone()
        index=load_index(c)
        properties=c.execute("""SELECT d.*,r.raw_text FROM xm.documents d JOIN xm.raw_messages r ON r.id=d.raw_message_id
          WHERE d.company_id='xm' AND d.active AND d.document_type='property_listing'
          AND (d.normalized_text ILIKE %s OR coalesce(d.contact_name,'') ILIKE %s)""",('%'+search+'%',)*2).fetchall()
        ids=[r['id'] for r in properties]
        members=c.execute('SELECT group_id FROM xm.document_group_members WHERE document_id=ANY(%s)',(ids,)).fetchall()
        group_ids=list({r['group_id'] for r in members})
        matches=c.execute('''SELECT m.*,p.raw_text property_text,b.raw_text buyer_text
          FROM xm.group_matches gm JOIN xm.matches m ON m.id=gm.match_id
          JOIN xm.documents pd ON pd.id=m.property_listing_id JOIN xm.raw_messages p ON p.id=pd.raw_message_id
          JOIN xm.documents bd ON bd.id=m.buyer_request_id JOIN xm.raw_messages b ON b.id=bd.raw_message_id
          WHERE gm.property_group_id=ANY(%s)''',(group_ids,)).fetchall()
        doc_ids=list({x for m in matches for x in (m['buyer_request_id'],m['property_listing_id'])})
        docs={r['id']:prepare_document(r,index) for r in c.execute('SELECT d.*,r.raw_text FROM xm.documents d JOIN xm.raw_messages r ON r.id=d.raw_message_id WHERE d.id=ANY(%s)',(doc_ids,)).fetchall()}
    inconsistent=[]
    for m in matches:
        assessed=assess_pair(docs[m['buyer_request_id']],docs[m['property_listing_id']],settings,float(m['semantic_score']),index)
        if m['status']=='hot' and (not assessed or assessed['score']<HOT_MIN_SCORE or assessed.get('status','hot')!='hot'):inconsistent.append(str(m['id']))
    return {'search':search,'unique_properties':len(group_ids),'matching_pairs':len(matches),
            'hot':sum(m['status']=='hot' for m in matches),'warm':sum(m['status']=='warm' for m in matches),
            'custom':sum(m['status']=='custom' for m in matches),
            'inconsistent_hot':inconsistent,'search_ms':timings,'search_median_ms':statistics.median(timings),
            'pairs':matches}

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--search',default='caesar');parser.add_argument('--output',required=True)
    args=parser.parse_args();result=audit(args.search)
    Path(args.output).write_text(json.dumps(result,default=str,ensure_ascii=False,indent=2))
    print(json.dumps({k:v for k,v in result.items() if k!='pairs'},ensure_ascii=False))
    raise SystemExit(1 if result['inconsistent_hot'] else 0)
