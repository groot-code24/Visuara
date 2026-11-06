"""Phase 15: the demo environment seeds a complete marketplace through the real API and refuses unsafe targets."""
import pytest

from app import main
from helpers import TestDB, needs_media
from ops import demo


@needs_media
def test_demo_seed_builds_a_working_marketplace(manual, tmp_path):
    out = demo.seed(main, manual, tmp_path / 'work')
    db = TestDB()
    assert len(out['videos']) == 2 and out['listed_items'] and out['placement_request_id']
    assert db.execute("SELECT COUNT(*) FROM analysis_versions WHERE status='COMPLETED' AND pipeline='visuara-pipeline-v1'").fetchone()[0] == 2
    assert db.execute("SELECT COUNT(*) FROM rights_records WHERE status='VERIFIED'").fetchone()[0] == 2
    assert db.execute("SELECT status FROM placement_requests WHERE id=?", (out['placement_request_id'],)).fetchone()['status'] == 'REQUESTED'
    assert db.execute("SELECT kind FROM organizations WHERE name='Demo Studio'").fetchone()['kind'] == 'publisher'


def test_demo_refuses_a_shared_database_and_foreign_folders(tmp_path, monkeypatch):
    monkeypatch.setenv('DATABASE_URL', 'postgresql://example/prod')
    with pytest.raises(SystemExit, match='DATABASE_URL'): demo.main_cli(['seed', '--data', str(tmp_path / 'd')])
    monkeypatch.delenv('DATABASE_URL')
    foreign = tmp_path / 'important'; foreign.mkdir(); (foreign / 'keep.txt').write_text('x')
    with pytest.raises(SystemExit, match='not created by this script'): demo.main_cli(['reset', '--data', str(foreign)])
    assert (foreign / 'keep.txt').exists()
    with pytest.raises(SystemExit, match='not empty'): demo.main_cli(['seed', '--data', str(foreign)])
