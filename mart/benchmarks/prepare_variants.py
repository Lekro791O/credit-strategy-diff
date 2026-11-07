"""Controlled index A/B: same source rows, fresh files, unchanged public schema."""
import importlib.util
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from mart import MartStore
from pipeline import build_dataset


def main():
    state=ROOT/'data/mart-benchmark'
    variants=state/'variants';variants.mkdir(exist_ok=True)
    result=build_dataset(ROOT/'data/RUN-20260827-0001')
    rows=sorted(result['comparison'],key=lambda r:(r['APP_ID'],r['PATH'],r['ARRAY_KEY']))
    segments=sorted(result['segments'],key=lambda r:r['APP_ID'])
    assert len(segments)==2002 and len(rows)==289533
    # The integration base includes Windows compatibility and the same views.
    baseline=subprocess.check_output(['git','show','2aa905a:mart/store.py'],cwd=ROOT,text=True)
    with tempfile.TemporaryDirectory(prefix='baseline-module-') as tmp:
        path=Path(tmp)/'baseline_store.py';path.write_text(baseline)
        spec=importlib.util.spec_from_file_location('baseline_store',path)
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        for label, cls in [('before',module.MartStore),('after',MartStore)]:
            # Temporary fresh DB avoids free-page/row-group history differences.
            target=Path(tmp)/(label+'.duckdb')
            store=cls(target);store.save_run('run',rows,segments)
            with store.connection() as con:
                con.execute('CHECKPOINT')
                indexes=con.execute('SELECT index_name FROM duckdb_indexes() ORDER BY index_name').fetchall()
            shutil.copyfile(target,variants/(label+'.duckdb'))
            print(label,len(rows),'attributes',indexes,flush=True)


if __name__=='__main__':main()
