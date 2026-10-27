"""Database regressions. Set XM_TEST_DATABASE_URL to an isolated PostgreSQL DB."""
import os
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch


def connect_test_db():
    import psycopg
    from psycopg.rows import dict_row
    conn = psycopg.connect(os.environ['XM_TEST_DATABASE_URL'], row_factory=dict_row)
    from tenant import workspace_id
    conn.execute("SELECT set_config('xm.workspace_id',%s,false)", (workspace_id(),))
    return conn


@unittest.skipUnless(os.getenv('XM_TEST_DATABASE_URL'), 'requires isolated XM_TEST_DATABASE_URL')
class WorkspaceDatabaseRegression(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import workspace
        cls.module = workspace
        cls.patch = patch.object(workspace, 'connect', connect_test_db)
        cls.patch.start()
        cls.job = uuid.uuid4()
        cls.tag = 'group-test-' + str(cls.job)
        from tenant import provision_company, workspace_scope
        cls.company_id = 'xm-test-' + str(cls.job)
        cls.scope = workspace_scope(cls.company_id)
        cls.scope.__enter__()
        with connect_test_db() as conn:
            conn.execute(Path(__file__).with_name('schema.sql').read_text())
            from entities import ensure_custom_count
            ensure_custom_count(conn)
            provision_company(conn, cls.company_id)
            conn.execute("INSERT INTO xm.imports(id,agent_name,file_name,file_path,file_sha256) VALUES(%s,%s,'test','test',%s)", (cls.job,cls.tag,cls.tag))
            for index in range(205):
                for copy in range(2):
                    cls.add(conn, cls.tag+' buyer '+str(index), 'buyer_request', copy)
            cls.property_ids = [cls.add(conn, cls.tag+' property', 'property_listing', copy) for copy in range(2)]
            buyers = conn.execute("SELECT id FROM xm.documents WHERE import_id=%s AND normalized_text=%s ORDER BY id", (cls.job,cls.tag+' buyer 0')).fetchall()
            for buyer in buyers:
                for target in cls.property_ids:
                    conn.execute("INSERT INTO xm.matches(id,buyer_request_id,property_listing_id,score,status) VALUES(%s,%s,%s,90,'hot')", (uuid.uuid4(),buyer['id'],target))
            # A strict-location buyer whose area could not be confirmed: stored as 'custom'.
            custom = conn.execute("SELECT id FROM xm.documents WHERE import_id=%s AND normalized_text=%s ORDER BY id", (cls.job,cls.tag+' buyer 1')).fetchall()
            for buyer in custom:
                conn.execute("INSERT INTO xm.matches(id,buyer_request_id,property_listing_id,score,status) VALUES(%s,%s,%s,75,'custom')", (uuid.uuid4(),buyer['id'],cls.property_ids[0]))
            conn.commit()

    @classmethod
    def add(cls, conn, text, kind, copy):
        raw, doc = uuid.uuid4(),uuid.uuid4()
        conn.execute("""INSERT INTO xm.raw_messages(id,import_id,agent_name,chat_id,chat_name,message_position,sent_at,raw_text,message_hash,classification)
         VALUES(%s,%s,%s,%s,'test',%s,%s,%s,%s,%s)""", (raw,cls.job,cls.tag,str(raw),copy,'2026-09-0'+str(copy+1),text,str(raw),kind))
        conn.execute("""INSERT INTO xm.documents(id,raw_message_id,import_id,agent_name,document_type,normalized_text)
         VALUES(%s,%s,%s,%s,%s,%s)""",(doc,raw,cls.job,cls.tag,kind,text))
        return doc

    @classmethod
    def tearDownClass(cls):
        from harness import WORKSPACE_TABLES
        with connect_test_db() as conn:
            for table in WORKSPACE_TABLES:
                conn.execute(f'DELETE FROM xm.{table} WHERE company_id=%s', (cls.company_id,))
            conn.commit()
        cls.patch.stop()
        cls.scope.__exit__(None, None, None)

    def test_multiple_search_phrases_use_or(self):
        rows = self.module.workspace(search=self.tag+' buyer 0\n'+self.tag+' buyer 204')['rows']
        self.assertEqual(len(rows), 2)
        self.assertEqual(len(self.module.workspace(direction='property', search='no-such-term,'+self.tag)['rows']), 1)

    def test_group_before_pagination(self):
        first = self.module.workspace(search=self.tag)
        second = self.module.workspace(search=self.tag, offset=200)
        self.assertEqual((len(first['rows']),len(second['rows'])),(200,5))
        self.assertTrue(first['has_more'])
        self.assertFalse(second['has_more'])
        rows=first['rows']+second['rows']
        self.assertEqual(len({r['raw_text'] for r in rows}),205)
        self.assertTrue(all(r['duplicate_count']==2 for r in rows))

    def test_reverse_recommendations_and_counts(self):
        sources=self.module.workspace(direction='property',search=self.tag)['rows']
        self.assertEqual(len(sources),1)
        source=sources[0]
        self.assertEqual(source['duplicate_count'],2)
        targets=self.module.recommendations(self.module.Batch(direction='property',ids=[source['id']]))['groups'][0]['recommendations']
        self.assertEqual(len(targets),source['match_count'])
        self.assertEqual(len({r['raw_text'] for r in targets}),len(targets))

    def test_filtered_copy_preserves_group_matches(self):
        sources = self.module.workspace(search=self.tag,statuses='hot',date_from='2026-09-02')['rows']
        self.assertEqual(len(sources),1)
        for source in sources:
            targets=self.module.recommendations(self.module.Batch(ids=[source['id']]))['groups'][0]['recommendations']
            self.assertEqual(source['match_count'],len(targets))
            self.assertEqual(source['duplicate_count'],1)
            self.assertEqual(len(targets),1)

    def test_custom_status_is_its_own_filter(self):
        custom = self.module.workspace(search=self.tag, statuses='custom')['rows']
        self.assertEqual([r['raw_text'] for r in custom], [self.tag+' buyer 1'])
        self.assertEqual((custom[0]['custom_count'], custom[0]['hot_count'], custom[0]['warm_count']), (1, 0, 0))
        self.assertEqual(self.module.workspace(search=self.tag+' buyer 1', statuses='hot,warm')['rows'], [])
        unmatched = self.module.workspace(search=self.tag+' buyer 1', statuses='unmatched')['rows']
        self.assertNotIn(self.tag+' buyer 1', [r['raw_text'] for r in unmatched])
        targets = self.module.recommendations(self.module.Batch(ids=[custom[0]['id']]))['groups'][0]['recommendations']
        self.assertEqual([t['status'] for t in targets], ['custom'])

    def test_different_signature_is_not_merged(self):
        values=[{'id':uuid.uuid4(),'raw_text':'same stock Contact A'}, {'id':uuid.uuid4(),'raw_text':'same stock Contact B'}]
        self.assertEqual(len(self.module.group_identical(values)),2)

    def test_cached_workspace_equivalent_to_uncached(self):
        from workspace_cache import refresh_workspace_cache
        cases=[dict(search=self.tag),dict(search=self.tag,offset=200),
               dict(search=self.tag+' buyer 0\n'+self.tag+' buyer 1'),
               dict(direction='property',search='does-not-exist,'+self.tag),
               dict(search=self.tag,statuses='hot'),dict(search=self.tag,statuses='custom'),dict(search=self.tag,statuses='warm,unmatched'),
               dict(direction='property',search=self.tag),
               dict(search=self.tag,date_from='2026-09-01',date_to='2026-09-01')]
        baseline=[self.module.workspace(**args) for args in cases]
        calendar=self.module.workspace_dates('property','2026-09-01','2026-10-01')
        with connect_test_db() as conn:
            refresh_workspace_cache(conn)
            conn.commit()
        try:
            for args,expected in zip(cases,baseline):
                actual=self.module.workspace(**args)
                self.assertEqual(actual['has_more'],expected['has_more'])
                fields=['id','raw_text','hot_count','warm_count','custom_count','match_count','duplicate_count']
                self.assertEqual([[r[k] for k in fields] for r in actual['rows']],[[r[k] for k in fields] for r in expected['rows']])
            self.test_reverse_recommendations_and_counts()
            self.test_custom_status_is_its_own_filter()
            self.assertEqual(self.module.workspace_dates('property','2026-09-01','2026-10-01'),calendar)
        finally:
            with connect_test_db() as conn:
                conn.execute('DELETE FROM xm.workspace_cache_state WHERE company_id=%s', (self.company_id,))
                conn.commit()


if __name__ == '__main__': unittest.main()
