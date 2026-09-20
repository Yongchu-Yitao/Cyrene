"""Run existing frontend contracts against an explicit snapshot without edits."""
import os,sys,tempfile
from pathlib import Path
root=Path(__file__).resolve().parents[3]
snapshot=Path(sys.argv[1]).resolve()
os.environ['CYRENE_BASE_DIR']=tempfile.mkdtemp(prefix='cyrene-contract-audit-')
sys.path.insert(0,str(root/'tests'))
import conftest
conftest._frontend_source=lambda paths:'\n'.join((snapshot/p).read_text(encoding='utf-8') for p in paths)
import pytest
raise SystemExit(pytest.main([str(root/'tests/test_workbench_frontend_logic.py'),'-q','-k','timeline or scroll or split or navigation or reconnect or retry or selection']))
