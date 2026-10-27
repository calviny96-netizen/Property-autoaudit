import os,unittest,uuid
from pathlib import Path
from unittest.mock import patch
from test_workspace import connect_test_db

@unittest.skipUnless(os.getenv('XM_TEST_DATABASE_URL'),'requires isolated XM_TEST_DATABASE_URL')
class ProcessingPipelineRegression(unittest.TestCase):
 def test_native_reindex_grouped_matching_and_cache(self):
  import reindex,matcher,workspace
  from workspace_cache import sources
  job=uuid.uuid4();tag='pipeline-'+str(job)
  with connect_test_db() as c:
   c.execute(Path(__file__).with_name('schema.sql').read_text())
   # This pipeline fixture tests unfiltered matching. The legacy workspace's
   # seeded office keyword is now enforced by automatic matching as well.
   preferences=c.execute("SELECT search_terms,matching_mode FROM xm.app_preferences WHERE company_id=current_setting('xm.workspace_id')").fetchone()
   c.execute("UPDATE xm.app_preferences SET search_terms='{}',matching_mode='company' WHERE company_id=current_setting('xm.workspace_id')")
   c.execute("INSERT INTO xm.imports(id,agent_name,file_name,file_path,file_sha256) VALUES(%s,%s,'test','test',%s)",(job,tag,tag))
   for i,text in enumerate(['Buyer request rumah Surabaya Barat LT 100 Budget 2 M']*2 + ['Dijual rumah Surabaya Barat LT 100 Harga 1,8 M']*2 + ['Dijual gudang Surabaya Barat LT 100 Harga 1,8 M']):
    raw=uuid.uuid4()
    c.execute("""INSERT INTO xm.raw_messages(id,import_id,agent_name,author,chat_id,chat_name,message_position,sent_at,raw_text,message_hash,classification)
     VALUES(%s,%s,%s,%s,%s,'test',%s,'2026-09-07',%s,%s,'ignored')""",(raw,job,tag,tag,str(raw),i,text,str(raw)))
   c.commit()
  try:
   with patch.object(reindex,'connect',connect_test_db),patch.object(reindex,'upsert',lambda points:len(points)),patch.object(reindex,'_request',lambda *a,**k:None):
    self.assertEqual(reindex.reindex_documents(),5)
   with patch.object(matcher,'connect',connect_test_db),patch.object(matcher,'qdrant_query',lambda *a,**k:[]):
    self.assertEqual(matcher.recompute_matches(),1)
   with patch.object(workspace,'connect',connect_test_db):
    r=workspace.workspace(direction='buyer')['rows']
    self.assertEqual(len(r),1);self.assertEqual(r[0]['duplicate_count'],2);self.assertEqual(r[0]['match_count'],1)
    targets=workspace.recommendations(workspace.Batch(ids=[r[0]['id']]))['groups'][0]['recommendations']
    self.assertEqual(len(targets),1);self.assertEqual(targets[0]['duplicate_count'],2)
  finally:
   with connect_test_db() as c:
    c.execute('DELETE FROM xm.raw_messages WHERE import_id=%s',(job,));c.execute('DELETE FROM xm.imports WHERE id=%s',(job,))
    c.execute("UPDATE xm.app_preferences SET search_terms=%s,matching_mode=%s WHERE company_id=current_setting('xm.workspace_id')",(preferences['search_terms'],preferences['matching_mode']))
    c.execute('DELETE FROM xm.workspace_cache_state');c.commit()
