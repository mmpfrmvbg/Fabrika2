import sqlite3

import pytest
from unittest.mock import patch

from factory.composition import wire
from factory.forge_next_atom import mark_atom_ready_for_forge
from factory.guards import Guards


def _make_atom(tmp_path) -> tuple[dict, sqlite3.Connection, str]:
    """Создаёт in-memory фабрику с одним атомом в статусе draft. Возвращает (fixtures_dict, conn, atom_id)"""
    db_path = tmp_path / "factory.db"
    f = wire(db_path)
    vision_id = f["ops"].create_vision("Test vision", auto_commit=False)
    atom_id = f["ops"].create_child(
        vision_id,
        "atom",
        "Test atom",
        files=[{"path": "src/main.py", "intent": "implementation"}],
        auto_commit=False,
    )
    f["conn"].commit()
    return f, f["conn"], atom_id


def test_mark_atom_ready_with_orchestrator_calls_judge(tmp_path):
    f, conn, aid = _make_atom(tmp_path)

    with patch("factory.agents.judge.run_judge") as run_judge_mock, patch.object(
        f["sm"], "apply_transition", wraps=f["sm"].apply_transition
    ) as apply_mock:
        mark_atom_ready_for_forge(conn, f["sm"], aid, orchestrator=f["orchestrator"])

    run_judge_mock.assert_called_once_with(f["orchestrator"], {"work_item_id": aid})
    assert not any(call.args[1] == "judge_approved" for call in apply_mock.call_args_list)


def test_mark_atom_ready_without_orchestrator_applies_transition(tmp_path):
    f, conn, aid = _make_atom(tmp_path)

    with patch.object(f["sm"], "apply_transition", wraps=f["sm"].apply_transition) as apply_mock:
        mark_atom_ready_for_forge(conn, f["sm"], aid, orchestrator=None)

    assert any(call.args[1] == "judge_approved" for call in apply_mock.call_args_list)


def test_mark_atom_ready_guard_blocks_invalid_atom(tmp_path):
    f, conn, aid = _make_atom(tmp_path)

    with patch.object(Guards, "guard_ready_for_forge", return_value=(False, "blocked for test")), patch.object(
        f["sm"], "apply_transition", wraps=f["sm"].apply_transition
    ) as apply_mock:
        with pytest.raises(ValueError, match="guard_ready_for_forge failed"):
            mark_atom_ready_for_forge(conn, f["sm"], aid, orchestrator=None)

    apply_mock.assert_not_called()
