"""Index experiment on a private DB copy. Does not mutate the measured app run."""
import argparse
import json
from pathlib import Path
import shutil
import statistics
import tempfile
import time

import duckdb

ROOT = Path(__file__).resolve().parents[2]
QUERIES = {
    'path_change_segments': ('''SELECT c.PATH, count(DISTINCT c.APP_ID) AS CNT
        FROM comparison c JOIN segments s USING(run_id, APP_ID)
        WHERE c.run_id=? AND c.PATH=? AND c.CHANGE_TYPE=? AND s.DECISION_CHANGED=1 GROUP BY c.PATH''',
        ['run','decisionResult.product_trials.rules.hit','VALUE_CHANGED']),
    'application_drill': ('''SELECT APP_ID, PATH, ARRAY_KEY, VALUE_BEFORE, VALUE_AFTER, DIFF_FLAG
        FROM comparison WHERE run_id=? AND APP_ID=? ORDER BY PATH, ARRAY_KEY''', ['run','I32554354']),
    'attribute': ('''SELECT APP_ID, PATH, ARRAY_KEY, VALUE_BEFORE, VALUE_AFTER, DIFF_FLAG
        FROM comparison WHERE run_id=? AND APP_ID=? AND PATH=?''', ['run','I32554354','decisionResult.decision']),
}
CANDIDATES = {
    'attr_app_only': ['CREATE INDEX attr_app_lookup ON attributes(APP_ID)'],
    'data_app_only': ['CREATE INDEX data_app_lookup ON datamart(APP_ID)'],
    'app_indexes': ['CREATE INDEX attr_app_lookup ON attributes(APP_ID)', 'CREATE INDEX data_app_lookup ON datamart(APP_ID)'],
    'path_index': ['CREATE INDEX attr_path_lookup ON attributes(PATH)'],
    'path_change_index': ['CREATE INDEX attr_path_change_lookup ON attributes(PATH, CHANGE_TYPE)'],
}


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--state',type=Path,default=ROOT/'data/mart-benchmark')
    args=parser.parse_args()
    state=args.state.resolve()
    rid=json.loads((state/'benchmark-session.json').read_text())['run_id']
    before=state/'variants/before.duckdb'
    database=before if before.exists() else state/'mart'/(rid+'.duckdb')
    out=ROOT/'mart/benchmarks/plans';out.mkdir(exist_ok=True)
    result={}
    for variant, indexes in [('baseline',[]), *CANDIDATES.items()]:
        with tempfile.TemporaryDirectory(prefix='index-experiment-') as folder:
            copy=Path(folder)/'run.duckdb';shutil.copyfile(database,copy)
            con=duckdb.connect(str(copy),config={'threads':2})
            try:
                # Normalize the private copy so the experiment is reproducible
                # even when save_run has already installed the chosen indexes.
                for name in ['attr_app_lookup','data_app_lookup','attr_path_lookup','attr_path_change_lookup']:
                    con.execute('DROP INDEX IF EXISTS ' + name)
                con.execute('CHECKPOINT')
                base_size=copy.stat().st_size
                t=time.perf_counter()
                for sql in indexes:con.execute(sql)
                build=time.perf_counter()-t
                con.execute('CHECKPOINT')
                entry=dict(build_seconds=build,extra_file_bytes=copy.stat().st_size-base_size,queries={})
                for name,(sql,params) in QUERIES.items():
                    elapsed=[]
                    for _ in range(3):
                        t=time.perf_counter();rows=con.execute(sql,params).fetchall();elapsed.append(time.perf_counter()-t)
                    plan='\n'.join(r[1] for r in con.execute('EXPLAIN ANALYZE '+sql,params).fetchall())
                    (out/(variant+'-'+name+'.txt')).write_text(plan,encoding='utf8')
                    entry['queries'][name]=dict(seconds=elapsed,median_seconds=statistics.median(elapsed),rows=len(rows),index_scan='Index Scan' in plan)
                result[variant]=entry
            finally:con.close()
    (ROOT/'mart/benchmarks/index-experiment.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps({name:{key:round(value['median_seconds']*1000,3)
                           for key,value in entry['queries'].items()}
                      for name,entry in result.items()},indent=2))


if __name__=='__main__':main()
