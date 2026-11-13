import json
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path
import app
import manual_defaults as defaults
import management
import progress


class ManualDefaults(unittest.TestCase):
    def setUp(self):
        self.original_db=app.DB;self.temp=tempfile.TemporaryDirectory()
        app.DB=Path(self.temp.name)/'test.sqlite3';app.initialize()

    def tearDown(self):app.DB=self.original_db;self.temp.cleanup()

    def test_seven_weights_and_original_component_goals(self):
        with app.connect() as db:
            rows=list(db.execute('SELECT * FROM department_indicator_settings'))
            self.assertEqual(len(rows),7)
            self.assertEqual(sum(Decimal(json.loads(r['definition'])['weight']) for r in rows),100)
            data=next(k for k in app.available('ORG-004',db) if k['id']=='KPI-024')
            config=progress.settings(db,'ORG-004',2026,data)
            self.assertEqual(config['target'],'10');self.assertEqual(config['unit'],'환산점')
            self.assertEqual([x['goal'] for x in config['manual_reference']['components']],['74.8점','72.9점'])
            ai=next(k for k in app.available('ORG-004',db) if k['id']=='KPI-025')
            self.assertEqual([x['goal'] for x in progress.settings(db,'ORG-004',2026,ai)['manual_reference']['components']],['20%','3건','10명'])
            report=next(k for k in app.available('ORG-004',db) if k['id']=='KPI-026')
            config=progress.settings(db,'ORG-004',2026,report)
            self.assertEqual(config['entry_mode'],'text');self.assertIsNone(config['target'])
            self.assertEqual(config['weight'],'46.5')

    def test_repeated_start_and_explicit_changes_are_preserved(self):
        with app.connect() as db:
            definition={'target':'77','weight':'12','unit':'건','entry_mode':'numeric','direction':'lower','dashboard_curve':'late'}
            db.execute("UPDATE department_indicator_settings SET definition=?,revision=5 WHERE indicator_id='KPI-025'",(json.dumps(definition),))
            count=db.execute('SELECT COUNT(*) FROM audit_log').fetchone()[0]
        app.initialize()
        with app.connect() as db:
            row=db.execute("SELECT * FROM department_indicator_settings WHERE indicator_id='KPI-025'").fetchone()
            self.assertEqual(json.loads(row['definition']),definition);self.assertEqual(row['revision'],5)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM audit_log').fetchone()[0],count)

    def test_existing_open_and_closed_snapshots_are_never_reinterpreted(self):
        with app.connect() as db:
            ids=['KPI-024'];db.execute('INSERT INTO users(employee_id,password_hash,department_id,indicators,created_at) VALUES(?,?,?,?,?)',('synthetic-worker','unused','ORG-004',json.dumps(ids),app.now()))
            db.execute("DELETE FROM department_indicator_settings WHERE indicator_id='KPI-024'")
            worker=db.execute("SELECT * FROM users WHERE employee_id='synthetic-worker'").fetchone()
            for month in [4,5]:
                management.post(db,worker,'/api/monthly/actual',{'department_id':'ORG-004','year':2026,'month':month,'indicator_id':ids[0],'revision':0,'value':'74.8','status':'진행중','reason':'기존 실적 보존'},management.effective_catalog(db,app.CATALOG),app.available)
            db.execute('INSERT INTO monthly_periods VALUES(?,?,?,?,?,?,?)',('ORG-004',2026,5,'closed',1,'synthetic',app.now()))
            before=[tuple(r) for r in db.execute('SELECT * FROM monthly_actuals')]
        app.initialize()
        with app.connect() as db:
            self.assertEqual([tuple(r) for r in db.execute('SELECT * FROM monthly_actuals')],before)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM department_indicator_settings').fetchone()[0],7)

    def test_other_year_department_and_example_catalog_do_not_get_defaults(self):
        with app.connect() as db:
            k=next(k for k in app.available('ORG-004',db) if k['id']=='KPI-025')
            self.assertNotIn('manual_reference',progress.settings(db,'ORG-004',2027,k))
            self.assertIsNone(defaults.reference('ORG-001',2026,k))
            before=db.total_changes
            example=json.loads((app.ROOT/'catalog.example.json').read_text())
            self.assertEqual(defaults.seed(db,example),0);self.assertEqual(db.total_changes,before)

    def test_editor_retains_reference_and_new_snapshot_supports_score_input(self):
        with app.connect() as db:
            ids=['KPI-025'];db.execute('INSERT INTO users(employee_id,password_hash,department_id,indicators,created_at,role,admin_department_id) VALUES(?,?,?,?,?,?,?)',('synthetic-manager','unused','ORG-004',json.dumps(ids),app.now(),'superadmin','ORG-004'))
            actor=db.execute("SELECT * FROM users WHERE employee_id='synthetic-manager'").fetchone()
            scope={'department_id':'ORG-004','year':2026,'month':11,'indicator_id':ids[0]}
            management.post(db,actor,'/api/monthly/actual',{**scope,'revision':0,'value':'15','status':'진행중','reason':'환산점수 입력'},management.effective_catalog(db,app.CATALOG),app.available)
            record=db.execute('SELECT * FROM monthly_actuals').fetchone()
            self.assertEqual(progress.achievement(record)['percent'],'50.00')
            self.assertIn('manual_reference',json.loads(record['snapshot']))
            management.post(db,actor,'/api/monthly/settings',{**scope,'revision':1,'target':'30','weight':'30','unit':'환산점','entry_mode':'numeric','direction':'higher','dashboard_curve':'late','reason':'계획 변경'},management.effective_catalog(db,app.CATALOG),app.available)
            k=next(k for k in app.available('ORG-004',db) if k['id']==ids[0])
            self.assertIn('manual_reference',progress.settings(db,'ORG-004',2026,k))


if __name__=='__main__':unittest.main()
