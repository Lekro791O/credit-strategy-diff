"""Synchronous diagnostic profile; HTTP acceptance figures are measured separately."""
import cProfile
import io
import json
import os
from pathlib import Path
import pstats
import sys

ROOT=Path(__file__).resolve().parents[2]
state=ROOT/'data/mart-benchmark'
os.environ['CSD_DATA_DIR']=str(state)
sys.path.insert(0,str(ROOT))
from api.main import facts
from starlette.requests import Request

rid=json.loads((state/'benchmark-session.json').read_text())['run_id']
profile=cProfile.Profile()
result=profile.runcall(facts,rid,Request({'type':'http','query_string':b''}),dims='path,change_type',limit=5000,offset=0)
assert result['totals']['cnt_apps']==2002
stream=io.StringIO();pstats.Stats(profile,stream=stream).sort_stats('cumtime').print_stats(35)
text=stream.getvalue().replace(str(ROOT), '<repo>').rstrip() + '\n'
path=ROOT/'mart/benchmarks/facts-profile.txt';path.write_text(text);print(text)
