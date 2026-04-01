import sqlite3
import time
from pathlib import Path


def main() -> int:
    db = Path("wet_hello.db")
    for i in range(30):
        try:
            conn = sqlite3.connect(str(db), timeout=30)
            conn.execute("PRAGMA busy_timeout=30000")
            rows = [
                (
                    "st_07b",
                    "work_item",
                    "ready_for_judge",
                    "judge_rejected",
                    "ready_for_work",
                    "guard_has_review_approval",
                    "action_enqueue_forge",
                    '["atom","atm_change"]',
                    "Judge rejected after review -> back to forge",
                ),
                (
                    "st_07c",
                    "work_item",
                    "ready_for_judge",
                    "judge_rejected",
                    "ready_for_work",
                    "guard_has_file_changes",
                    "action_enqueue_forge",
                    '["atom","atm_change"]',
                    "Judge rejected after forge artifacts -> back to forge",
                ),
            ]
            for row in rows:
                conn.execute(
                    """
                    INSERT OR REPLACE INTO state_transitions
                        (id, entity_type, from_state, event_name, to_state,
                         guard_name, action_name, applicable_kinds, description)
                    VALUES (?,?,?,?,?,?,?,?,?)
                    """,
                    row,
                )
            conn.commit()
            conn.close()
            print("ok")
            return 0
        except sqlite3.OperationalError as e:
            if "locked" not in str(e).lower():
                raise
            time.sleep(1)
    raise SystemExit("failed: database remained locked")


if __name__ == "__main__":
    raise SystemExit(main())

