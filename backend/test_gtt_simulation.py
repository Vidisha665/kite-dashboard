"""Mocked regression checks for the local-only GTT simulation."""

import json
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

import main


class ReadOnlyFakeKite:
    def __init__(self, *, gtts=None, orders=None, positions=None, holdings=None):
        self.gtts = gtts or []
        self.order_rows = orders or []
        self.position_rows = positions or []
        self.holding_rows = holdings or []
        self.read_calls = []
        self.mutation_calls = []

    def instruments(self, exchange):
        self.read_calls.append("instruments")
        return [{"tradingsymbol": "ABC", "instrument_token": 123, "tick_size": 0.05}]

    def get_gtts(self):
        self.read_calls.append("get_gtts")
        return self.gtts

    def orders(self):
        self.read_calls.append("orders")
        return self.order_rows

    def positions(self):
        self.read_calls.append("positions")
        return {"net": self.position_rows, "day": []}

    def holdings(self):
        self.read_calls.append("holdings")
        return self.holding_rows

    # Any accidental broker mutation is an immediate test failure.
    def place_gtt(self, *args, **kwargs):
        self.mutation_calls.append("place_gtt")
        raise AssertionError("Broker GTT mutation must never be called")

    def place_order(self, *args, **kwargs):
        self.mutation_calls.append("place_order")
        raise AssertionError("Broker order mutation must never be called")

    def modify_order(self, *args, **kwargs):
        self.mutation_calls.append("modify_order")
        raise AssertionError("Broker order mutation must never be called")

    def cancel_order(self, *args, **kwargs):
        self.mutation_calls.append("cancel_order")
        raise AssertionError("Broker order mutation must never be called")

    def delete_gtt(self, *args, **kwargs):
        self.mutation_calls.append("delete_gtt")
        raise AssertionError("Broker GTT mutation must never be called")


class GttSimulationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=main.APP_FOLDER / ".tmp")
        root = Path(self.temp.name)
        self.store_path = root / "dummy_gtts.json"
        self.lock_path = root / "dummy_gtts.lock"
        self.kite = ReadOnlyFakeKite()
        self.patches = [
            patch.object(main, "make_kite_client", return_value=self.kite),
            patch.object(main, "get_nse_instrument_master", return_value={"ABC": {"tick_size": 0.05}}),
            patch.object(main, "DUMMY_GTT_FILE", self.store_path),
            patch.object(main, "DUMMY_GTT_LOCK_FILE", self.lock_path),
        ]
        for active_patch in self.patches:
            active_patch.start()

    def tearDown(self):
        for active_patch in reversed(self.patches):
            active_patch.stop()
        self.temp.cleanup()

    @staticmethod
    def candidate(**overrides):
        values = {"symbol": "ABC", "quantity": 1, "buy_price": "100.02", "last_price": "101.20"}
        values.update(overrides)
        return main.GttCandidate(**values)

    def live_request(self, **candidate_overrides):
        return main.LiveGttRequest(confirm_live=True, orders=[self.candidate(**candidate_overrides)])

    def saved_records(self):
        if not self.store_path.exists():
            return []
        return json.loads(self.store_path.read_text(encoding="utf-8"))["records"]

    def assert_no_broker_mutations(self):
        self.assertEqual(self.kite.mutation_calls, [])
        self.assertTrue({"get_gtts", "orders", "positions", "holdings"}.issubset(self.kite.read_calls))

    def test_state_endpoint_is_read_only_and_returns_ready(self):
        response = main.algo_gtt_state(main.GttStateRequest(orders=[self.candidate()]))
        self.assertEqual(response["results"][0]["buy_status"], "ready")
        self.assertFalse(self.store_path.exists())
        self.assert_no_broker_mutations()

    def test_confirmed_live_run_creates_only_dummy_record_and_duplicate_is_skipped(self):
        response = main.algo_live_gtt(self.live_request())
        row = response["results"][0]
        self.assertTrue(response["simulation_only"])
        self.assertEqual(row["buy_status"], "success")
        self.assertEqual(row["buy_trigger_id"], "DUMMY-ABC-000001")
        self.assertEqual(row["buy_price"], 100.0)
        self.assertAlmostEqual(row["stoploss"], 95.0)
        self.assertEqual(row["stoploss_status"], "planned_after_buy")
        duplicate = main.algo_live_gtt(self.live_request())["results"][0]
        self.assertEqual(duplicate["buy_status"], "already_active")
        self.assertEqual(duplicate["buy_trigger_id"], row["buy_trigger_id"])
        self.assertEqual(len(self.saved_records()), 1)
        self.assertTrue(self.saved_records()[0]["simulation_only"])
        self.assert_no_broker_mutations()

    def test_positions_holdings_day_orders_and_real_gtts_block_dummy_creation(self):
        today = datetime.now(ZoneInfo("Asia/Kolkata")).isoformat()
        cases = [
            (ReadOnlyFakeKite(positions=[{"tradingsymbol": "ABC", "quantity": 2}]), "position_exists"),
            (ReadOnlyFakeKite(holdings=[{"tradingsymbol": "ABC", "quantity": 1}]), "position_exists"),
            (ReadOnlyFakeKite(orders=[{"tradingsymbol": "ABC", "exchange": "NSE", "transaction_type": "BUY", "product": "CNC", "status": "OPEN", "order_timestamp": today, "order_id": "O1"}]), "day_buy_order_exists"),
            (ReadOnlyFakeKite(gtts=[{"id": 77, "status": "active", "condition": {"tradingsymbol": "ABC"}, "orders": [{"transaction_type": "BUY"}]}]), "already_active"),
        ]
        for fake, expected in cases:
            with self.subTest(expected=expected):
                self.kite = fake
                with patch.object(main, "make_kite_client", return_value=fake):
                    row = main.algo_live_gtt(self.live_request())["results"][0]
                self.assertEqual(row["account_state"], expected)
                self.assertNotEqual(row["buy_status"], "success")
                self.assertEqual(fake.mutation_calls, [])
        self.assertEqual(self.saved_records(), [])

    def test_invalid_and_waiting_for_ltp_candidates_do_not_create_records(self):
        invalid_quantity = main.algo_live_gtt(self.live_request(quantity=2))["results"][0]
        self.assertEqual(invalid_quantity["buy_status"], "invalid")
        no_ltp = main.algo_live_gtt(self.live_request(last_price=None))["results"][0]
        self.assertEqual(no_ltp["buy_status"], "waiting_for_ltp")
        no_buy = main.algo_live_gtt(self.live_request(buy_price=None))["results"][0]
        self.assertEqual(no_buy["buy_status"], "not_available")
        self.assertEqual(self.saved_records(), [])
        self.assert_no_broker_mutations()

    def test_confirmed_concurrent_duplicate_runs_persist_one_dummy_record(self):
        with ThreadPoolExecutor(max_workers=2) as pool:
            responses = list(pool.map(lambda _: main.algo_live_gtt(self.live_request()), range(2)))
        statuses = sorted(response["results"][0]["buy_status"] for response in responses)
        self.assertEqual(statuses, ["already_active", "success"])
        self.assertEqual(len(self.saved_records()), 1)
        self.assert_no_broker_mutations()

    def test_live_run_requires_explicit_confirmation(self):
        with self.assertRaises(main.HTTPException):
            main.algo_live_gtt(main.LiveGttRequest(orders=[self.candidate()]))
        self.assertEqual(self.kite.read_calls, [])
        self.assertFalse(self.store_path.exists())
        self.assertEqual(self.kite.mutation_calls, [])


if __name__ == "__main__":
    unittest.main()
