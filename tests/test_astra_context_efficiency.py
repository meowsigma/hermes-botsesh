"""Pure planner regressions; no gateway, network or model needed."""
import unittest
from unittest.mock import patch

import lean_dm
import lean_group


class ContextPlanningTests(unittest.TestCase):
    def test_dm_overlap_can_come_from_a_late_control_but_not_a_bool_number(self):
        binding = {'dm_session': {'session_id': 's', 'through': 1}}
        missed = {'number': 3, 'senderParticipantId': 'owner', 'text': 'still needed'}
        pending = {'number': 4, 'senderParticipantId': 'owner', 'text': 'now'}

        def plan(control_number):
            rows = [pending, missed, {'number': control_number, 'dmControl': {}}]
            return lean_dm.plan(binding, 's', rows, {4}, 'owner', history=1)

        self.assertEqual(plan(2), {'session_id': 's', 'missed': [missed]})
        self.assertIsNone(plan(False))

    def test_group_orders_only_deliverable_rows_but_checks_skipped_history(self):
        entry = {'through': 4, 'ctx': 'd', 'own': [6], 'edits': {'2': 1}}
        rows = [{'number': n, 'authorLabel': 'Sam (person)', 'text': str(n)} for n in (8, 7, 6, 5, 3, 2)]
        rows[2]['isOwn'] = True
        rows[-1]['editCount'] = 1

        def plan():
            return lean_group.plan(entry, rows, [7, 3], 'd', 1)

        result = plan()
        self.assertEqual([i['n'] for i in result['earlier']], [5, 8])
        self.assertEqual([i['n'] for i in result['new']], [3, 7])  # pending #3 is below the pointer
        self.assertEqual([r['number'] for r in rows], [8, 7, 6, 5, 3, 2])
        rows[-1]['editCount'] = 2
        self.assertIsNone(plan())
        rows[-1].update(number=False, isOwn=True)
        self.assertIsNone(plan())

    def test_group_over_count_needs_no_line_serialization(self):
        rows = [{'number': n, 'authorLabel': 'Sam (person)', 'text': 'hello'}
                for n in range(1, lean_group.MAX_MESSAGES + 2)]
        with patch.object(lean_group, 'line', wraps=lean_group.line) as encode:
            result = lean_group.plan({'through': 0, 'ctx': 'd'}, rows, [1], 'd', 1)
            self.assertIsNone(result)
            encode.assert_not_called()

    def test_group_character_boundary_includes_json_escaping_and_separator(self):
        rows = [{'number': 1, 'authorLabel': 'Sam (person)', 'text': 'café\n"hi"'}]
        cost = len('{"n":1,"by":"Sam (person)","text":"café\\n\\"hi\\""}') + 1
        for budget, plan in [('BUDGET', lambda: lean_group.plan({'through': 0, 'ctx': 'd'}, rows, [1], 'd', 1)),
                             ('FIRST_BUDGET', lambda: lean_group.first_plan({'briefing': [{}]}, rows, [1]))]:
            with self.subTest(budget=budget):
                with patch.object(lean_group, budget, cost):
                    self.assertEqual(plan()['new'][0]['text'], rows[0]['text'])
                with patch.object(lean_group, budget, cost - 1):
                    self.assertIsNone(plan())
