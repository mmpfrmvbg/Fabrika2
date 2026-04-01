-- ⚠️ DEPRECATED — not used in production. For reference only.
-- See AUDIT_REPORT.md for details.

-- ============================================================================
-- FSM TRANSITION SEEDS — canonical transitions for work_items
-- Guards and actions reference Python functions in orchestrator code.
-- ============================================================================

-- ════════════════════════════════════════════════════════════════════════════
-- HIGH-LEVEL ITEMS (vision, initiative, epic, story, task)
-- ════════════════════════════════════════════════════════════════════════════

INSERT INTO state_transitions VALUES
-- Creator submits a new vision/initiative
('t01','work_item',NULL,    'draft',            'creator_submitted',    'planned',          NULL,                       'action_notify_planner',
 'Creator finishes drafting; item enters planning pipeline'),

-- Planner decomposes and structures
('t02','work_item',NULL,    'planned',          'planner_decomposed',   'ready_for_judge',  'guard_has_children',       'action_build_judge_context',
 'Planner created sub-items; needs judge validation'),

-- Judge approves high-level direction → stays in planned for further decomposition
('t03','work_item',NULL,    'ready_for_judge',  'judge_approved',       'planned',          'guard_has_non_atom_children','action_notify_planner',
 'Judge approves but more decomposition needed'),

-- Judge approves and all children are atoms → mark as ready
('t04','work_item',NULL,    'ready_for_judge',  'judge_approved',       'ready_for_work',   'guard_all_children_atoms',  'action_enqueue_children',
 'Judge approves; all children atomic → children enter forge pipeline'),

-- Judge rejects
('t05','work_item',NULL,    'ready_for_judge',  'judge_rejected',       'judge_rejected',   NULL,                       'action_return_to_initiator',
 'Judge rejects with structured comment'),

-- Architect/Planner rework after rejection
('t06','work_item',NULL,    'judge_rejected',   'reworked',             'planned',          'guard_has_rework_comment',  'action_increment_judge_rejections',
 'Re-formulated after judge feedback; re-enters planning'),

-- Abandoned after rejection
('t07','work_item',NULL,    'judge_rejected',   'abandoned',            'cancelled',        NULL,                       'action_log_abandonment',
 'Initiator gives up on rejected item'),

-- Parent completes when all children done
('t08','work_item',NULL,    'ready_for_work',   'all_children_done',    'done',             'guard_all_children_done',   'action_propagate_completion',
 'Rolls up: parent done when every child done'),

-- ════════════════════════════════════════════════════════════════════════════
-- ATOMS (kind = 'atom')
-- ════════════════════════════════════════════════════════════════════════════

-- Atom fully specified → judge review
('t10','work_item','atom',  'draft',            'atom_specified',       'planned',          'guard_has_files_declared',  NULL,
 'Atom has files, description, acceptance criteria'),

('t11','work_item','atom',  'planned',          'send_to_judge',        'ready_for_judge',  'guard_has_files_declared',  'action_build_judge_context',
 'Atom ready for judge gate'),

('t12','work_item','atom',  'ready_for_judge',  'judge_approved',       'ready_for_work',   NULL,                       'action_enqueue_forge',
 'Judge approves atom → forge queue'),

('t13','work_item','atom',  'ready_for_judge',  'judge_rejected',       'judge_rejected',   NULL,                       'action_return_to_planner',
 'Judge rejects atom → back to planner/architect'),

-- Forge picks up
('t14','work_item','atom',  'ready_for_work',   'forge_started',        'in_progress',      'guard_can_acquire_locks',   'action_acquire_file_locks',
 'Forge leases atom, locks files, creates run'),

-- Forge completes → review
('t15','work_item','atom',  'in_progress',      'forge_completed',      'in_review',        NULL,                       'action_enqueue_review',
 'Forge done; code enters review sandbox'),

-- Forge fails
('t16','work_item','atom',  'in_progress',      'forge_failed',         'ready_for_work',   'guard_under_retry_limit',   'action_release_locks_and_retry',
 'Forge crashed; release locks, re-queue if under limit'),

('t17','work_item','atom',  'in_progress',      'forge_failed',         'blocked',          'guard_over_retry_limit',    'action_block_needs_human',
 'Forge exceeded retries → blocked, needs human'),

-- Review passes → done
('t18','work_item','atom',  'in_review',        'review_passed',        'done',             'guard_no_blocking_failures','action_commit_and_release',
 'All checks pass → git merge, release locks'),

-- Review fails → back through judge
('t19','work_item','atom',  'in_review',        'review_failed',        'review_rejected',  NULL,                       'action_build_judge_context',
 'Reviewer rejects → judge gets context for meta-review'),

('t20','work_item','atom',  'review_rejected',  'judge_reviewed_rejection','ready_for_work', NULL,                       'action_enqueue_forge_with_feedback',
 'Judge adds meta-comment → atom goes back to forge'),

('t21','work_item','atom',  'review_rejected',  'judge_escalated',      'blocked',          NULL,                       'action_block_needs_human',
 'Judge decides atom needs human intervention'),

-- ════════════════════════════════════════════════════════════════════════════
-- ATM_CHANGE (HR prompt changes)
-- ════════════════════════════════════════════════════════════════════════════

('t30','work_item','atm_change','draft',        'hr_proposed',          'ready_for_judge',  NULL,                       'action_build_judge_context',
 'HR proposes prompt change → judge review'),

('t31','work_item','atm_change','ready_for_judge','judge_approved',     'ready_for_work',   NULL,                       'action_enqueue_forge',
 'Judge approves HR change → forge applies it'),

('t32','work_item','atm_change','ready_for_judge','judge_rejected',     'judge_rejected',   NULL,                       'action_return_to_hr',
 'Judge rejects HR change'),

-- ════════════════════════════════════════════════════════════════════════════
-- UNIVERSAL TRANSITIONS (any kind, any state)
-- ════════════════════════════════════════════════════════════════════════════

-- Blocking
('t40','work_item',NULL,    '*',                'dependency_blocked',   'blocked',          'guard_has_unresolved_dep',  'action_save_resume_state',
 'Unresolved dependency detected → blocked with resume target'),

-- Unblocking
('t41','work_item',NULL,    'blocked',          'dependency_resolved',  '{resume_to_status}','guard_all_deps_resolved',  'action_restore_from_block',
 'All deps resolved → restore to resume_to_status'),

-- Cancellation
('t42','work_item',NULL,    '*',                'creator_cancelled',    'cancelled',        NULL,                       'action_cascade_cancel',
 'Creator cancels → cascade to children'),

-- Archive
('t43','work_item',NULL,    'done',             'archived',             'archived',         NULL,                       'action_cleanup_locks',
 'Completed item moved to archive');
