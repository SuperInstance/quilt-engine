"""Acceptance: the payload-transfer agreement (v0.2, gap 2 deepened).

Two sovereign books, communication only through envelopes (JSON text,
as the file-drop transport carries them). Two phases: an offer is
booked SEND by the sender and RECEIVE by the receiver only after hash
+ declared size check out; SETTLE is permitted only on a matching
pair, and only settled payloads enter derived state. A tampered
payload is disputed with the receiver's own computed hash, the
disagreement is kept verbatim on both sides, and the id is dead
forever — a corrected payload travels under a new transmission id.
"""

import json
import unittest

from quilt_engine.core import (
    PAYLOAD_TYPE,
    StateStore,
    TransmissionEnvelope,
    agreed_crossings,
    book_dispute_receive,
    book_dispute_send,
    book_disputed_receive,
    book_offer_receive,
    book_offer_send,
    book_receipt_receive,
    book_receipt_send,
    book_settle,
    disputes,
    make_dispute,
    make_payload_offer,
    make_receipt,
    read_offer,
    settleable_crossings,
    settled_payloads,
    open_offers,
)
from quilt_engine.core.envelope import Ending

SENDER, RECEIVER = "vessel-a", "vessel-b"
MANIFEST = {"manifest": ["bol-001", "bol-002", "bol-003"]}


def books():
    return StateStore(SENDER), StateStore(RECEIVER)


def tamper_in_transit(offer_json: str) -> str:
    """Mutate the carried content, keep the declared hash and size."""
    d = json.loads(offer_json)
    content = d["payload"]["content"]
    if isinstance(content, dict) and isinstance(content.get("manifest"), list):
        content["manifest"][-1] = content["manifest"][-1] + "-TAMPERED"
    else:
        d["payload"]["content"] = content + "XX=="
    return json.dumps(d, sort_keys=True)


def offer_full_path(content=MANIFEST, contract="cargo-manifest@1"):
    """Sender SEND + receiver verified RECEIVE, over the wire forms."""
    sb, rb = books()
    offer = make_payload_offer(
        content, contract=contract, ending=Ending.COPY,
        copy_authorized_by="fleet-owner-standing-order-3",
    )
    book_offer_send(sb, offer, RECEIVER)
    reading = read_offer(offer.to_json())
    assert reading.ok
    book_offer_receive(rb, reading, SENDER)
    return sb, rb, offer, reading


class TestOfferShape(unittest.TestCase):
    def test_json_and_bytes_round_trip(self):
        for content in (MANIFEST, b"chart-tile-bytes" * 4):
            offer = make_payload_offer(content, contract="c@1")
            back = TransmissionEnvelope.from_json(offer.to_json())
            self.assertEqual(back.type, PAYLOAD_TYPE)
            self.assertEqual(back.hash, offer.hash)
            self.assertEqual(back.payload_size, offer.payload_size)
            self.assertEqual(back.contract, "c@1")

    def test_declared_size_matches_measured(self):
        offer = make_payload_offer(MANIFEST, contract="c@1")
        reading = read_offer(offer.to_json())
        self.assertTrue(reading.ok)
        self.assertEqual(reading.declared_size, reading.measured_size)

    def test_tampered_payload_detected_on_arrival(self):
        offer = make_payload_offer(MANIFEST, contract="c@1")
        reading = read_offer(tamper_in_transit(offer.to_json()))
        self.assertFalse(reading.hash_ok)
        self.assertNotEqual(reading.computed_hash, reading.declared_hash)
        # The strict parser still refuses the same file outright.
        with self.assertRaises(ValueError):
            TransmissionEnvelope.from_json(tamper_in_transit(offer.to_json()))

    def test_falsified_declared_size_detected(self):
        offer = make_payload_offer(MANIFEST, contract="c@1")
        d = json.loads(offer.to_json())
        d["payload_size"] = d["payload_size"] + 1  # a lying size
        reading = read_offer(json.dumps(d, sort_keys=True))
        self.assertTrue(reading.hash_ok)
        self.assertFalse(reading.size_ok)
        self.assertFalse(reading.ok)

    def test_offer_requires_contract(self):
        with self.assertRaises(ValueError):
            make_payload_offer(MANIFEST, contract="")


class TestTwoPhaseBooking(unittest.TestCase):
    def test_settle_only_on_match(self):
        # Sender's booking alone: no settlement is possible anywhere.
        sb, rb = books()
        offer = make_payload_offer(MANIFEST, contract="cargo-manifest@1")
        book_offer_send(sb, offer, RECEIVER)
        self.assertEqual(settleable_crossings(sb.ledger, rb.ledger), ([], [offer.transmission_id]))
        with self.assertRaises(ValueError):
            book_settle(rb, offer, SENDER)  # receiver has nothing to settle on

    def test_receiver_settles_on_verified_receive(self):
        sb, rb, offer, reading = offer_full_path()
        self.assertEqual(
            settleable_crossings(sb.ledger, rb.ledger)[0], [offer.transmission_id]
        )
        book_settle(rb, offer, SENDER)
        self.assertIn(offer.transmission_id, settled_payloads(rb.ledger))

    def test_sender_settles_only_after_receipt_crossing(self):
        sb, rb, offer, reading = offer_full_path()
        with self.assertRaises(ValueError):
            book_settle(sb, offer, RECEIVER)  # SEND alone is not enough
        make_receipt_and_cross(sb, rb, reading)
        book_settle(rb, offer, SENDER)  # receiver settles on its own evidence
        book_settle(sb, offer, RECEIVER)
        # Both books now fold the same settled payload, content included.
        s_view, r_view = settled_payloads(sb.ledger), settled_payloads(rb.ledger)
        self.assertEqual(set(s_view), {offer.transmission_id})
        self.assertEqual(set(r_view), set(s_view))
        self.assertEqual(s_view[offer.transmission_id]["content"], MANIFEST)
        self.assertEqual(r_view[offer.transmission_id]["payload_hash"], offer.hash)

    def test_receipt_with_wrong_hash_does_not_enable_settle(self):
        sb, rb, offer, reading = offer_full_path()
        receipt = make_receipt(reading)
        receipt.payload["payload_hash"] = "0" * 64  # a lying receipt
        book_receipt_send(rb, receipt, reading, SENDER)
        book_receipt_receive(sb, receipt, RECEIVER)
        with self.assertRaises(ValueError):
            book_settle(sb, offer, RECEIVER)

    def test_open_offer_excluded_from_derived_state(self):
        sb, rb, offer, _ = offer_full_path()
        # Both phase-1 bookings exist; nobody books SETTLE. The offer
        # stays open in both books, and neither derived state has it.
        self.assertEqual(settled_payloads(sb.ledger), {})
        self.assertEqual(settled_payloads(rb.ledger), {})
        self.assertEqual(open_offers(sb.ledger), [offer.transmission_id])
        self.assertEqual(open_offers(rb.ledger), [offer.transmission_id])
        self.assertEqual(
            settleable_crossings(sb.ledger, rb.ledger)[0], [offer.transmission_id]
        )  # permission exists; it is simply never exercised

    def test_no_double_settle(self):
        sb, rb, offer, _ = offer_full_path()
        book_settle(rb, offer, SENDER)
        with self.assertRaises(ValueError):
            book_settle(rb, offer, SENDER)

    def test_settled_bytes_payload_decodes_to_bytes(self):
        sb, rb, offer, _ = offer_full_path(content=b"raw-bytes-payload" * 3)
        book_settle(rb, offer, SENDER)
        content = settled_payloads(rb.ledger)[offer.transmission_id]["content"]
        self.assertEqual(content, b"raw-bytes-payload" * 3)


def make_receipt_and_cross(sb, rb, reading):
    receipt = make_receipt(reading)
    book_receipt_send(rb, receipt, reading, SENDER)
    book_receipt_receive(sb, receipt, RECEIVER)
    return receipt


class TestDisputePath(unittest.TestCase):
    def tamper_full_path(self, content=MANIFEST, contract="cargo-manifest@1"):
        sb, rb = books()
        offer = make_payload_offer(
            content, contract=contract, ending=Ending.COPY,
            copy_authorized_by="fleet-owner-standing-order-3",
        )
        book_offer_send(sb, offer, RECEIVER)
        reading = read_offer(tamper_in_transit(offer.to_json()))
        self.assertFalse(reading.ok)
        book_disputed_receive(rb, reading, SENDER)
        return sb, rb, offer, reading

    def test_dispute_blocks_settlement(self):
        sb, rb, offer, reading = self.tamper_full_path()
        settleable, blocked = settleable_crossings(sb.ledger, rb.ledger)
        self.assertEqual(settleable, [])
        self.assertEqual(blocked, [offer.transmission_id])
        with self.assertRaises(ValueError):
            book_settle(rb, offer, SENDER)

    def test_dispute_visible_in_both_books(self):
        sb, rb, offer, reading = self.tamper_full_path()
        dispute = make_dispute(reading)
        book_dispute_send(rb, dispute, reading, SENDER)
        book_dispute_receive(sb, dispute, RECEIVER)
        # Receiver side: the RECEIVE-DISPUTED booking, computed hash
        # carried in the entry, declared hash kept in the detail. (The
        # receiver's book also carries the dispute envelope it sent
        # back; this is the booking the dispute is *about*.)
        r_disp = [d for d in disputes(rb.ledger) if d["seen_as"] == "receive_disputed"]
        self.assertEqual(len(r_disp), 1)
        self.assertEqual(r_disp[0]["transmission_id"], offer.transmission_id)
        self.assertEqual(r_disp[0]["computed_hash"], reading.computed_hash)
        self.assertEqual(r_disp[0]["declared_hash"], offer.hash)
        self.assertNotEqual(r_disp[0]["computed_hash"], r_disp[0]["declared_hash"])
        # Sender side: the returned dispute crossing, payload verbatim.
        s_disp = [d for d in disputes(sb.ledger) if d["seen_as"] == "dispute_response"]
        self.assertEqual(len(s_disp), 1)
        self.assertEqual(s_disp[0]["transmission_id"], offer.transmission_id)
        self.assertEqual(s_disp[0]["computed_hash"], reading.computed_hash)
        self.assertEqual(s_disp[0]["declared_hash"], offer.hash)
        # And the sender can never settle the disputed id.
        with self.assertRaises(ValueError):
            book_settle(sb, offer, RECEIVER)

    def test_tampered_crossing_is_not_agreed(self):
        sb, rb, offer, _ = self.tamper_full_path()
        agreed, unagreed = agreed_crossings(sb.ledger, rb.ledger)
        self.assertEqual(agreed, [])
        self.assertEqual(unagreed, [offer.transmission_id])

    def test_disputed_id_never_settles_even_after_clean_re_receive(self):
        # The same id arrives again, this time intact: the receiver may
        # book a verified RECEIVE, but the earlier dispute kills the id
        # forever — no retries under the same transmission id.
        sb, rb, offer, reading = self.tamper_full_path()
        clean = read_offer(offer.to_json())
        self.assertTrue(clean.ok)
        book_offer_receive(rb, clean, SENDER)
        settleable, blocked = settleable_crossings(sb.ledger, rb.ledger)
        self.assertEqual(settleable, [])
        self.assertEqual(blocked, [offer.transmission_id])
        with self.assertRaises(ValueError):
            book_settle(rb, offer, SENDER)

    def test_corrected_payload_new_id_settles_cleanly(self):
        sb, rb, offer, reading = self.tamper_full_path()
        dispute = make_dispute(reading)
        book_dispute_send(rb, dispute, reading, SENDER)
        book_dispute_receive(sb, dispute, RECEIVER)
        # The corrected payload: same true content, NEW transmission id.
        corrected = make_payload_offer(
            MANIFEST, contract="cargo-manifest@1", ending=Ending.COPY,
            copy_authorized_by="fleet-owner-standing-order-3",
        )
        self.assertNotEqual(corrected.transmission_id, offer.transmission_id)
        book_offer_send(sb, corrected, RECEIVER)
        r2 = read_offer(corrected.to_json())
        self.assertTrue(r2.ok)
        book_offer_receive(rb, r2, SENDER)
        book_settle(rb, corrected, SENDER)
        make_receipt_and_cross(sb, rb, r2)
        book_settle(sb, corrected, RECEIVER)
        s_view, r_view = settled_payloads(sb.ledger), settled_payloads(rb.ledger)
        self.assertEqual(set(s_view), {corrected.transmission_id})
        self.assertEqual(set(r_view), set(s_view))
        self.assertEqual(s_view[corrected.transmission_id]["content"], MANIFEST)
        # The disputed id is still dead, and still open in both books.
        self.assertNotIn(offer.transmission_id, s_view)
        self.assertIn(offer.transmission_id, open_offers(sb.ledger))
        self.assertIn(offer.transmission_id, open_offers(rb.ledger))


if __name__ == "__main__":
    unittest.main()
