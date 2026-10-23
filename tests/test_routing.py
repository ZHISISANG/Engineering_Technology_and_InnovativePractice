"""分派测试：每条工单都必须有去处，不能默认通过（f44）。"""

from __future__ import annotations

import unittest

from tests.helpers import DEFAULT_CONFIG, FIXED_NOW, run_demo, temp_dir

from expo_trace.core.codes import Code
from expo_trace.core.config import load_config
from expo_trace.core.models import Disposition
from expo_trace.core.routing import route


class RoutingTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._tmp = temp_dir()
        cls.result = run_demo(cls._tmp.name)
        cls.config = load_config(DEFAULT_CONFIG)

    @classmethod
    def tearDownClass(cls) -> None:
        cls._tmp.cleanup()

    def test_every_ticket_has_a_disposition(self) -> None:
        self.assertEqual(len(self.result.dispositions), len(self.result.tickets))
        counts = {d.value: 0 for d in Disposition}
        for disposition in self.result.dispositions.values():
            counts[disposition.value] += 1
        self.assertEqual(counts["draft_ready"], 5)
        self.assertEqual(counts["human_review"], 9)
        self.assertEqual(counts["blocked"], 2)

    def test_queue_has_owner_and_deadline(self) -> None:
        self.assertEqual(len(self.result.queue), 11)
        for item in self.result.queue:
            self.assertTrue(item.reasons)
            self.assertTrue(item.owner)
            self.assertTrue(item.due_at)

    def test_irreversible_items_route_to_business_owner(self) -> None:
        fee_items = [q for q in self.result.queue if Code.FEE_OR_QUALIFICATION in q.reasons]
        self.assertEqual({q.ticket_id for q in fee_items}, {"T-1003", "T-1014"})
        self.assertTrue(all(q.owner == "业务负责人" for q in fee_items))
        self.assertTrue(all(q.requires_authorization for q in fee_items))

    def test_paused_pilot_stops_all_drafts(self) -> None:
        dispositions, queue = route(
            self.result.tickets,
            self.result.resolutions,
            self.result.drafts,
            self.result.issues,
            self.config,
            reviews=[],
            paused=True,
            now=FIXED_NOW,
        )
        self.assertEqual(len(queue), len(self.result.tickets))
        self.assertNotIn(Disposition.DRAFT_READY, set(dispositions.values()))
        for item in queue:
            self.assertIn(Code.PILOT_PAUSED, item.reasons)


if __name__ == "__main__":
    unittest.main()
