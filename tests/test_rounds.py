"""A topic folder of rounds served as tabs, the single-use start link, and the limits around a save."""
import contextlib
import hashlib
import io
import json
import os
import re
import threading
import time
import unittest
from unittest import mock

import support
from support import Folder, Rounds, Running, png_bytes, sample_decisions, signoff, write_outside


def view_of(run):
    body = run.request("GET", "/%s/" % run.token)[1].decode("utf-8")
    block = re.search(r'<script type="application/json" id="signoff-data">(.*?)</script>', body, re.S).group(1)
    return json.loads(block)


EMPTY = {"answers": {}, "free_text": "", "done": False}


class Loading(unittest.TestCase):
    def setUp(self):
        self.rounds = Rounds(2)

    def tearDown(self):
        self.rounds.cleanup()

    def make(self):
        return signoff.make_server(self.rounds.path)

    def test_rounds_are_tabs_in_number_order(self):
        data = sample_decisions()
        data["title"] = "Round 10"
        self.rounds.write(10, data)
        (self.rounds.path / "notes.json").write_text("{}", encoding="utf-8")  # not a round: ignored
        run = Running(self.rounds)
        try:
            view = view_of(run)
        finally:
            run.stop()
        self.assertEqual([r["decisions"]["title"] for r in view["rounds"]], ["Round 1", "Round 2", "Round 10"])
        self.assertEqual(view["rounds"][0]["decisions"]["tab"], "R1")
        self.assertNotIn("tab", view["rounds"][2]["decisions"])

    def test_tab_must_be_text(self):
        data = sample_decisions()
        data["tab"] = 3
        self.rounds.write(1, data)
        with self.assertRaises(signoff.DecisionsError) as ctx:
            self.make()
        self.assertIn("round-01.json", str(ctx.exception))
        self.assertIn("tab", str(ctx.exception))

    def test_folder_without_rounds(self):
        empty = Folder()
        try:
            empty.decisions.rename(empty.path / "other.json")
            with self.assertRaises(signoff.DecisionsError) as ctx:
                signoff.make_server(empty.path)
            self.assertIn("round-01.json", str(ctx.exception))
        finally:
            empty.cleanup()

    def test_error_names_file_item_and_field(self):
        data = sample_decisions()
        data["items"][0]["recommended"] = "three"
        self.rounds.write(2, data)
        with self.assertRaises(signoff.DecisionsError) as ctx:
            self.make()
        for part in ("round-02.json", "layout", "recommended"):
            self.assertIn(part, str(ctx.exception))

    def test_stale_answers_name_the_file(self):
        self.rounds.answers(1).write_text(json.dumps(
            {"version": 1, "done": False, "saved": "2026-01-01T12:00:00Z",
             "answers": {"gone": {"choice": "x", "comment": ""}}, "free_text": ""}), encoding="utf-8")
        with self.assertRaises(signoff.DecisionsError) as ctx:
            self.make()
        self.assertIn("round-01.answers.json", str(ctx.exception))

    def test_done_round_whose_decisions_changed(self):
        self.rounds.finish(1)
        record = json.loads(self.rounds.answers(1).read_text(encoding="utf-8"))
        record["decisions"] = {"file": "round-01.json", "sha256": "0" * 64}
        self.rounds.answers(1).write_text(json.dumps(record), encoding="utf-8")
        with self.assertRaises(signoff.DecisionsError) as ctx:
            self.make()
        self.assertIn("round-01.json changed", str(ctx.exception))
        record["decisions"] = {"file": "round-01.json", "sha256": hashlib.sha256(
            self.rounds.round(1).read_bytes()).hexdigest()}
        self.rounds.answers(1).write_text(json.dumps(record), encoding="utf-8")
        self.make().server_close()

    def test_every_round_done(self):
        self.rounds.finish(1)
        self.rounds.finish(2)
        with self.assertRaises(signoff.DecisionsError) as ctx:
            self.make()
        self.assertIn("done", str(ctx.exception))

    def test_single_file_that_is_done_is_not_reopened(self):
        folder = Folder()
        try:
            folder.answers.write_text(json.dumps({"version": 1, "done": True, "saved": "2026-01-01T12:00:00Z",
                                                  "answers": {}, "free_text": ""}), encoding="utf-8")
            with self.assertRaises(signoff.DecisionsError):
                signoff.make_server(folder.decisions)
        finally:
            folder.cleanup()


class Tabs(unittest.TestCase):
    def setUp(self):
        self.rounds = Rounds(3)
        self.rounds.finish(1, {"layout": {"choice": "one", "comment": "earlier"}})
        self.run_ = Running(self.rounds)

    def tearDown(self):
        self.run_.stop()
        self.rounds.cleanup()

    def test_done_round_is_read_only_and_shows_its_answers(self):
        view = view_of(self.run_)
        self.assertEqual([r["readonly"] for r in view["rounds"]], [True, False, False])
        self.assertEqual(view["rounds"][0]["answers"]["answers"]["layout"]["comment"], "earlier")
        self.assertEqual(view["rounds"][0]["saved"], "2026-01-01T12:00:00Z")
        before = self.rounds.answers(1).read_text(encoding="utf-8")
        self.assertEqual(self.run_.save(dict(EMPTY, free_text="late"), 0)[0].status, 409)
        self.assertEqual(self.rounds.answers(1).read_text(encoding="utf-8"), before)

    def test_save_goes_to_its_round(self):
        self.assertEqual(self.run_.save(dict(EMPTY, free_text="two"), 1)[0].status, 200)
        saved = json.loads(self.rounds.answers(2).read_text(encoding="utf-8"))
        self.assertEqual(saved["free_text"], "two")
        self.assertEqual(saved["decisions"], {"file": "round-02.json", "sha256": hashlib.sha256(
            self.rounds.round(2).read_bytes()).hexdigest()})
        self.assertTrue((self.rounds.path / "round-02.answers.md").exists())
        self.assertFalse(self.rounds.answers(3).exists())

    def test_answers_are_checked_against_their_own_round(self):
        data = sample_decisions()
        data["items"][0]["id"] = "only-in-three"
        self.rounds.write(3, data)
        run = Running(self.rounds)
        try:
            ok = {"answers": {"only-in-three": {"choice": "one", "comment": ""}}, "free_text": "", "done": False}
            self.assertEqual(run.save(ok, 1)[0].status, 400)
            self.assertEqual(run.save(ok, 2)[0].status, 200)
        finally:
            run.stop()

    def test_unknown_round(self):
        for path in ("save/3", "save/9", "save/01", "save/-1", "save/a", "save", "save/", "save/1/"):
            resp, _ = self.run_.request("POST", "/%s/%s" % (self.run_.token, path), EMPTY, {
                "Content-Type": "application/json", "Origin": self.run_.origin})
            self.assertEqual(resp.status, 404, path)

    def test_done_needs_every_other_round_accepted(self):
        resp, body = self.run_.save(dict(EMPTY, done=True), 1)
        self.assertEqual(resp.status, 409)
        self.assertIn(b"R3", body)  # names the round still to accept, by its tab
        self.assertIn(b"not accepted", body)
        self.assertFalse(self.rounds.answers(2).exists())
        self.assertFalse(self.run_.server.finished.is_set())

    def test_accepting_a_round_is_recorded_and_a_later_change_undoes_it(self):
        self.assertEqual(self.run_.save(dict(EMPTY, accepted=True), 1)[0].status, 200)
        first = json.loads(self.rounds.answers(2).read_text(encoding="utf-8"))
        self.assertTrue(first["accepted"])
        self.assertRegex(first["accepted_at"], r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$")
        self.assertFalse(first["changed_after_accept"])
        self.assertEqual(self.run_.save(dict(EMPTY, accepted=True, free_text="same"), 1)[0].status, 200)
        self.assertEqual(json.loads(self.rounds.answers(2).read_text(encoding="utf-8"))["accepted_at"],
                         first["accepted_at"])
        self.assertEqual(self.run_.save(dict(EMPTY, free_text="changed"), 1)[0].status, 200)
        changed = json.loads(self.rounds.answers(2).read_text(encoding="utf-8"))
        self.assertEqual((changed["accepted"], changed["changed_after_accept"]), (False, True))
        self.assertNotIn("accepted_at", changed)
        md = (self.rounds.path / "round-02.answers.md").read_text(encoding="utf-8")
        self.assertIn("Accepted: no, changed after it was accepted", md)
        self.assertEqual(self.run_.server.view()["rounds"][1]["changed"], True)
        self.assertEqual(self.run_.save(dict(EMPTY, accepted=True), 1)[0].status, 200)
        again = json.loads(self.rounds.answers(2).read_text(encoding="utf-8"))
        self.assertEqual((again["accepted"], again["changed_after_accept"]), (True, False))

    def test_a_round_closed_by_a_half_done_shows_read_only_after_a_reload(self):
        two = Rounds(2)
        run = Running(two)
        try:
            self.assertEqual(run.save(dict(EMPTY, accepted=True), 1)[0].status, 200)
            run.server.shutdown = lambda: None  # hold the stop back, as if the second Done never arrived
            self.assertEqual(run.save(dict(EMPTY, done=True), 0)[0].status, 200)
            self.assertFalse(run.server.finished.is_set())
            view = run.server.view()["rounds"]
            self.assertEqual([r["readonly"] for r in view], [True, False])
            self.assertEqual(run.server.editable_answers(), [two.answers(1), two.answers(2)])
            del run.server.shutdown
            self.assertEqual(run.save(dict(EMPTY, done=True), 1)[0].status, 200)  # the reloaded page's one Done
            run.thread.join(5)
            self.assertTrue(run.server.finished.is_set())
        finally:
            run.stop()
            two.cleanup()

    def test_a_stale_tab_cannot_overwrite_a_newer_save(self):
        self.assertEqual(self.run_.server.view()["rounds"][1]["rev"], 0)
        resp, body = self.run_.save(dict(EMPTY, free_text="tab A", rev=0), 1)
        self.assertEqual(resp.status, 200)
        self.assertEqual(json.loads(body)["rev"], 1)
        resp, body = self.run_.save(dict(EMPTY, free_text="tab B, from before", accepted=True, rev=0), 1)
        self.assertEqual(resp.status, 409)
        self.assertIn(b"another tab", body)
        self.assertEqual(json.loads(self.rounds.answers(2).read_text(encoding="utf-8"))["free_text"], "tab A")
        self.assertEqual(self.run_.save(dict(EMPTY, accepted=True, rev=1), 2)[0].status, 409)  # each round counts alone
        self.assertEqual(self.run_.save(dict(EMPTY, accepted=True, rev=0), 2)[0].status, 200)
        self.assertEqual(self.run_.save(dict(EMPTY, done=True, rev=0), 1)[0].status, 409)  # Done too
        self.assertFalse(self.run_.server.finished.is_set())
        for bad in (True, -1, "1", 1.5):
            self.assertEqual(self.run_.save(dict(EMPTY, rev=bad), 1)[0].status, 400, bad)

    def test_a_second_run_on_the_same_folder_cannot_undo_a_done(self):
        folder = Folder()
        old, new = Running(folder), Running(folder)
        try:
            self.assertEqual(old.save(EMPTY)[0].status, 200)  # the old run's first save
            self.assertIsNone(new.server.rounds[0].saved)  # loaded before that save, and no reload since
            self.assertEqual(new.save(dict(EMPTY, free_text="newer"))[0].status, 409)  # the file changed under it
            reloaded = Running(folder)
            try:
                self.assertEqual(reloaded.save(dict(EMPTY, free_text="final", done=True))[0].status, 200)
            finally:
                reloaded.stop()
            resp, body = old.save(dict(EMPTY, free_text="stale"))
            self.assertEqual(resp.status, 409)
            self.assertIn(b"another tab", body)
            saved = json.loads(folder.answers.read_text(encoding="utf-8"))
            self.assertEqual((saved["done"], saved["free_text"]), (True, "final"))
        finally:
            old.stop()
            new.stop()
            folder.cleanup()

    def test_a_reload_picks_up_a_file_another_run_wrote(self):
        folder = Folder()
        old, new = Running(folder), Running(folder)
        try:
            self.assertEqual(old.save(dict(EMPTY, free_text="from the old tab"))[0].status, 200)
            self.assertEqual(new.save(dict(EMPTY, free_text="newer", rev=0))[0].status, 409)
            view = new.server.view()["rounds"][0]  # what a reload of the page gets
            self.assertEqual(view["answers"]["free_text"], "from the old tab")
            resp, body = new.save(dict(EMPTY, free_text="after the reload", rev=view["rev"]))
            self.assertEqual(resp.status, 200)
            self.assertEqual(json.loads(folder.answers.read_text(encoding="utf-8"))["free_text"], "after the reload")
        finally:
            old.stop()
            new.stop()
            folder.cleanup()

    def test_a_done_from_another_run_stays_done_after_a_reload(self):
        folder = Folder()
        other, this = Running(folder), Running(folder)
        try:
            self.assertEqual(other.save(dict(EMPTY, free_text="final", done=True))[0].status, 200)
            view = this.server.view()["rounds"][0]
            self.assertTrue(view["readonly"])
            this.thread.join(10)
            self.assertTrue(this.server.finished.is_set())  # nothing left to answer: this run ends too
            self.assertEqual(json.loads(folder.answers.read_text(encoding="utf-8"))["free_text"], "final")
        finally:
            other.stop()
            this.stop()
            folder.cleanup()

    @unittest.skipIf(os.name == "nt" or (hasattr(os, "geteuid") and os.geteuid() == 0), "POSIX file modes, not root")
    def test_the_hash_is_of_the_bytes_written_not_a_read_back(self):
        folder = Folder()
        run = Running(folder)
        real = signoff.write_atomic

        def write_then_lock(path, data):
            real(path, data)
            if path.name.endswith(".json"):
                os.chmod(str(path), 0)  # as if something held the new file for a moment

        try:
            with mock.patch.object(signoff, "write_atomic", write_then_lock):
                self.assertEqual(run.save(dict(EMPTY, done=True))[0].status, 200)
            os.chmod(str(folder.answers), 0o600)
            raw = folder.answers.read_bytes()
            self.assertNotIn(b"\r\n", raw)
            self.assertEqual(run.server.rounds[0].disk, hashlib.sha256(raw).digest())
        finally:
            os.chmod(str(folder.answers), 0o600)
            run.stop()
            folder.cleanup()

    def test_accepted_without_a_time_gets_one(self):
        record = {"version": 1, "done": False, "saved": "2026-01-01T12:00:00Z", "accepted": True, "answers": {},
                  "free_text": ""}
        self.rounds.answers(2).write_text(json.dumps(record), encoding="utf-8")
        run = Running(self.rounds)
        try:
            self.assertEqual(run.save(dict(EMPTY, accepted=True), 1)[0].status, 200)
        finally:
            run.stop()
        saved = json.loads(self.rounds.answers(2).read_text(encoding="utf-8"))
        self.assertRegex(saved["accepted_at"], r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$")
        signoff.make_server(self.rounds.path).server_close()  # and the next run still loads it

    def test_one_done_needs_every_editable_round(self):
        self.assertEqual(self.run_.save(dict(EMPTY, accepted=True), 2)[0].status, 200)
        self.assertEqual(self.run_.save(dict(EMPTY, done=True), 1)[0].status, 200)
        self.assertFalse(self.run_.server.finished.is_set())
        self.assertEqual(self.run_.save(EMPTY, 1)[0].status, 409)  # round 2 is final now
        self.assertEqual(self.run_.save(dict(EMPTY, done=True), 2)[0].status, 200)
        self.run_.thread.join(5)
        self.assertFalse(self.run_.thread.is_alive())
        self.assertEqual(self.run_.server.editable_answers(), [self.rounds.answers(2), self.rounds.answers(3)])

    def test_images_have_one_index_across_rounds(self):
        view = view_of(self.run_)
        self.assertEqual([r["decisions"]["items"][0]["images"][0]["src"] for r in view["rounds"]],
                         ["img/0", "img/1", "img/2"])
        self.assertEqual(self.run_.request("GET", "/%s/img/1" % self.run_.token)[1], png_bytes() + b"b")
        self.assertEqual(self.run_.request("GET", "/%s/img/3" % self.run_.token)[0].status, 404)


class OutsideWriter(unittest.TestCase):
    """check_disk(): what the run's once-a-second look at its answers files does, called here directly."""

    def setUp(self):
        self.rounds = Rounds(2)
        self.run_ = Running(self.rounds)
        self.srv = self.run_.server

    def tearDown(self):
        self.run_.stop()
        self.rounds.cleanup()

    def check(self):
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            self.srv.check_disk()
        return err.getvalue()

    def test_its_own_write_is_not_taken_for_an_outside_one(self):
        self.assertEqual(json.loads(self.run_.save(dict(EMPTY, rev=0), 1)[1])["rev"], 1)
        self.assertEqual(self.check(), "")
        self.assertEqual(self.srv.rounds[1].rev, 1)
        self.assertEqual(self.run_.save(dict(EMPTY, free_text="next", rev=1), 1)[0].status, 200)

    def test_an_outside_write_without_done_is_taken_in(self):
        write_outside(self.rounds.answers(1), dict(support.done_record(), done=False, free_text="outside"))
        self.assertEqual(self.check(), "")
        r = self.srv.rounds[0]
        self.assertEqual((r.saved["free_text"], r.rev, r.finished), ("outside", 1, False))
        self.assertFalse(self.srv.finished.is_set())
        resp, body = self.run_.save(dict(EMPTY, free_text="stale page", rev=0), 0)  # a page loaded before it
        self.assertEqual(resp.status, 409)
        self.assertIn(b"reload", body)
        self.assertEqual(json.loads(self.rounds.answers(1).read_text(encoding="utf-8"))["free_text"], "outside")

    def test_an_outside_done_closes_its_round_and_the_run_waits_for_the_others(self):
        write_outside(self.rounds.answers(1), support.done_record())
        self.check()
        self.assertTrue(self.srv.rounds[0].finished)
        self.assertFalse(self.srv.finished.is_set())
        self.assertEqual([r["readonly"] for r in self.srv.view()["rounds"]], [True, False])
        self.assertEqual(self.run_.save(dict(EMPTY, rev=1), 0)[0].status, 409)  # Done is final
        write_outside(self.rounds.answers(2), support.done_record())
        self.check()
        self.run_.thread.join(5)
        self.assertTrue(self.srv.finished.is_set())
        self.assertFalse(self.run_.thread.is_alive())
        self.assertEqual(self.srv.editable_answers(), [self.rounds.answers(1), self.rounds.answers(2)])

    def test_an_invalid_outside_file_is_reported_once_and_never_written_over(self):
        for bad in ("{not json", json.dumps(dict(support.done_record(), answers={"nope": {}}))):
            write_outside(self.rounds.answers(1), bad)
            err = self.check()
            self.assertIn("round-01.answers.json", err)
            self.assertEqual(err.count("\n"), 1)
            self.assertEqual(self.check(), "")  # the same content is not reported again
            self.assertFalse(self.srv.rounds[0].finished)
            self.assertFalse(self.srv.finished.is_set())
            self.assertEqual(self.run_.save(dict(EMPTY, rev=0), 0)[0].status, 409)
            self.assertEqual(self.rounds.answers(1).read_text(encoding="utf-8"), bad)

    def test_an_outside_done_for_another_version_of_the_round_is_refused(self):
        record = dict(support.done_record(), decisions={"file": "round-01.json", "sha256": "1" * 64})
        write_outside(self.rounds.answers(1), record)
        self.assertIn("round-01", self.check())
        self.assertFalse(self.srv.rounds[0].finished)

    @unittest.skipUnless(hasattr(os, "mkfifo"), "POSIX FIFOs")
    def test_a_fifo_in_place_of_the_answers_file_neither_blocks_nor_stops_the_run(self):
        os.mkfifo(str(self.rounds.answers(1)))
        out = []
        looker = threading.Thread(target=lambda: out.append(self.check()), daemon=True)
        looker.start()
        looker.join(5)
        self.assertFalse(looker.is_alive())  # it holds the lock: blocking here would block every save
        self.assertIn("cannot read it", out[0])
        self.assertFalse(self.srv.rounds[0].finished)

    def test_an_outside_done_soon_after_the_start_link_still_waits_out_the_grace(self):
        self.srv.start_used_at = time.monotonic()
        write_outside(self.rounds.answers(1), support.done_record())
        write_outside(self.rounds.answers(2), support.done_record())
        self.check()
        self.assertTrue(self.srv.finished.is_set())
        self.run_.thread.join(signoff.DONE_GRACE / 2)
        self.assertTrue(self.run_.thread.is_alive())
        self.run_.thread.join(signoff.DONE_GRACE + 5)
        self.assertFalse(self.run_.thread.is_alive())


def within(seconds, fn):
    """fn's result or exception, raising AssertionError when it is still running after seconds (a blocked read)."""
    out = []

    def go():
        try:
            out.append((True, fn()))
        except BaseException as e:  # handed back to the test
            out.append((False, e))
    t = threading.Thread(target=go, daemon=True)
    t.start()
    t.join(seconds)
    if t.is_alive():
        raise AssertionError("still blocked after %s s" % seconds)
    ok, value = out[0]
    if not ok:
        raise value
    return value


@unittest.skipUnless(hasattr(os, "mkfifo"), "POSIX FIFOs")
class Fifos(unittest.TestCase):
    """A FIFO (or another non-file) where a round or its answers should be: an error, never a read that blocks.
    The inbox builds rounds on its look thread, so one such read would stall every project."""

    def setUp(self):
        self.rounds = Rounds(2)

    def tearDown(self):
        self.rounds.cleanup()

    def test_a_fifo_round_file_is_an_error(self):
        self.rounds.round(1).unlink()
        os.mkfifo(str(self.rounds.round(1)))
        with self.assertRaises(signoff.DecisionsError) as ctx:
            within(5, lambda: signoff.load_decisions(self.rounds.round(1)))
        self.assertIn("cannot read it", str(ctx.exception))
        with self.assertRaises(signoff.DecisionsError):
            within(5, lambda: signoff.Round(self.rounds.round(1)))

    def test_a_fifo_swapped_in_after_the_check_is_an_error(self):
        checked = signoff.load_decisions(self.rounds.round(1))
        self.rounds.round(1).unlink()
        os.mkfifo(str(self.rounds.round(1)))
        with mock.patch.object(signoff, "load_decisions", return_value=checked):  # the hash's own read
            with self.assertRaises(OSError):
                within(5, lambda: signoff.Round(self.rounds.round(1)))

    def test_a_fifo_answers_file_at_start_is_an_error(self):
        os.mkfifo(str(self.rounds.answers(1)))
        with self.assertRaises(signoff.DecisionsError) as ctx:
            within(5, lambda: signoff.Round(self.rounds.round(1)))
        self.assertIn("cannot resume", str(ctx.exception))

    def test_a_save_over_a_fifo_answers_file_is_refused(self):
        run = Running(self.rounds)
        self.addCleanup(run.stop)
        os.mkfifo(str(self.rounds.answers(1)))
        r = run.server.rounds[0]
        state = signoff.check_answers(EMPTY, r.decisions)
        with self.assertRaises(OSError):  # the page gets a 500, and nothing is written
            within(5, lambda: run.server.store(0, state, 0))
        self.assertEqual(r.rev, 0)


class StartLink(unittest.TestCase):
    def setUp(self):
        self.folder = Folder()
        self.run_ = Running(self.folder)
        self.link = "/b/%s/" % self.run_.server.start_token

    def tearDown(self):
        self.run_.stop()
        self.folder.cleanup()

    def test_start_token_is_separate_and_long(self):
        srv = self.run_.server
        self.assertNotEqual(srv.start_token, srv.token)
        self.assertGreaterEqual(len(srv.start_token), 40)
        self.assertEqual(srv.start_url, "http://%s%s" % (self.run_.host, self.link))

    def test_first_use_redirects_to_the_page(self):
        resp, _ = self.run_.request("GET", self.link)
        self.assertEqual(resp.status, 303)
        self.assertEqual(resp.getheader("Location"), "/%s/" % self.run_.token)
        self.assertEqual(resp.getheader("Referrer-Policy"), "no-referrer")
        self.assertIn("no-store", resp.getheader("Cache-Control"))
        self.assertIsNone(resp.getheader("Set-Cookie"))
        self.assertEqual(self.run_.request("GET", "/%s/" % self.run_.token)[0].status, 200)
        self.assertEqual(self.run_.save(EMPTY)[0].status, 200)

    def test_second_use_is_gone_and_stops_the_run(self):
        self.assertEqual(self.run_.request("GET", self.link)[0].status, 303)
        resp, body = self.run_.request("GET", self.link)
        self.assertEqual(resp.status, 410)
        self.assertNotIn(self.run_.token.encode(), body)
        self.run_.thread.join(5)
        self.assertFalse(self.run_.thread.is_alive())
        self.assertTrue(self.run_.server.start_reused)

    def test_wrong_host_does_not_spend_the_link(self):
        self.assertEqual(self.run_.request("GET", self.link, host="evil.example")[0].status, 403)
        self.assertEqual(self.run_.request("GET", self.link)[0].status, 303)

    def test_wrong_start_token(self):
        for path in ("/b/%s/" % ("A" * 43), "/b/%s" % self.run_.server.start_token, "/b/", "/b/%s/x" %
                     self.run_.server.start_token, "/b/%s/" % self.run_.token):
            self.assertEqual(self.run_.request("GET", path)[0].status, 404, path)
        self.assertEqual(self.run_.request("GET", self.link)[0].status, 303)

    def test_done_soon_after_the_start_link_keeps_the_run_open_a_moment(self):
        # A program that wins the race for the start link must not end the run before the browser arrives.
        self.assertEqual(self.run_.request("GET", self.link)[0].status, 303)
        self.assertEqual(self.run_.save(dict(EMPTY, done=True))[0].status, 200)
        self.run_.thread.join(0.5)
        self.assertTrue(self.run_.thread.is_alive())
        self.assertEqual(self.run_.request("GET", self.link)[0].status, 410)  # the browser, arriving late
        self.run_.thread.join(5)
        self.assertFalse(self.run_.thread.is_alive())
        self.assertTrue(self.run_.server.start_reused)

    def test_done_long_after_the_start_link_ends_the_run_at_once(self):
        self.assertEqual(self.run_.request("GET", self.link)[0].status, 303)
        self.run_.server.start_used_at -= signoff.DONE_GRACE
        self.assertEqual(self.run_.save(dict(EMPTY, done=True))[0].status, 200)
        self.run_.thread.join(2)
        self.assertFalse(self.run_.thread.is_alive())

    def test_nothing_is_stored_after_the_link_was_used_twice(self):
        self.run_.server.shutdown = lambda: None  # keep serving, as if a save raced the stop
        try:
            self.run_.request("GET", self.link)
            self.run_.request("GET", self.link)
            self.assertEqual(self.run_.save(EMPTY)[0].status, 409)
        finally:
            del self.run_.server.shutdown
        self.assertFalse(self.folder.answers.exists())

    def test_post_to_the_start_link(self):
        resp, _ = self.run_.request("POST", self.link, EMPTY, {"Content-Type": "application/json",
                                                               "Origin": self.run_.origin})
        self.assertEqual(resp.status, 404)
        self.assertEqual(self.run_.request("GET", self.link)[0].status, 303)


class Limits(unittest.TestCase):
    def setUp(self):
        data = sample_decisions()
        data["items"] = [dict(data["items"][0], id="item-%d" % n, images=[]) for n in range(6)]
        self.folder = Folder(data)
        self.run_ = Running(self.folder)

    def tearDown(self):
        self.run_.stop()
        self.folder.cleanup()

    def test_every_text_field_full_of_wide_characters_still_saves(self):
        wide = "€" * signoff.MAX_TEXT  # 3 bytes each in UTF-8
        state = {"answers": {"item-%d" % n: {"choice": "one", "comment": wide} for n in range(6)},
                 "free_text": wide, "done": False}
        self.assertGreater(len(json.dumps(state, ensure_ascii=False).encode("utf-8")), signoff.MAX_BODY)
        self.assertEqual(self.run_.save(state)[0].status, 200)

    def test_round_limit_has_a_ceiling(self):
        data = sample_decisions()
        data["items"] = [{"id": "i%d" % n, "title": "x", "options": [{"id": "a", "label": "a"}, {"id": "b", "label": "b"}],
                          "other": True} for n in range(3000)]
        self.folder.write(data)
        run = Running(self.folder)
        try:
            self.assertEqual(run.server.rounds[0].max_body, signoff.MAX_ROUND_BODY)
        finally:
            run.stop()

    def test_body_over_the_round_limit(self):
        limit = self.run_.server.rounds[0].max_body
        resp, _ = self.run_.save(EMPTY, **{"Content-Length": str(limit + 1)})
        self.assertEqual(resp.status, 413)

    def test_huge_content_length_is_refused_without_a_crash(self):
        resp, _ = self.run_.save(EMPTY, **{"Content-Length": "9" * 5000})
        self.assertEqual(resp.status, 413)
        self.assertEqual(self.run_.save(EMPTY)[0].status, 200)

    def test_wrong_host_names_the_expected_one(self):
        resp, body = self.run_.request("GET", "/%s/" % self.run_.token, host="127.0.0.1:1")
        self.assertEqual(resp.status, 403)
        self.assertIn(self.run_.host.encode(), body)

    def test_write_error_is_reported(self):
        with mock.patch.object(signoff, "write_atomic", side_effect=PermissionError(13, "locked")):
            resp, body = self.run_.save(EMPTY)
        self.assertEqual(resp.status, 500)
        self.assertIn(b"could not write", body)
        self.assertEqual(self.run_.save(EMPTY)[0].status, 200)


class Page(unittest.TestCase):
    page = (signoff.HERE / "page.html").read_text(encoding="utf-8")

    def test_tabs_follow_the_aria_pattern(self):
        for part in ('role="tablist"', "'tab'", "'tabpanel'", "aria-selected", "aria-controls", "aria-labelledby",
                     "ArrowRight", "ArrowLeft", "Home", "End"):
            self.assertIn(part, self.page)

    def test_shortcuts_never_fire_while_typing_or_with_modifiers(self):
        self.assertIn("e.ctrlKey || e.metaKey || e.altKey", self.page)
        self.assertIn("function typing(", self.page)
        self.assertIn("showModal", self.page)

    def test_element_ids_come_from_positions_not_from_agent_ids(self):
        # An option id such as "comment" must not collide with the comment box's element id.
        self.assertIn("'-o' + k", self.page)
        self.assertNotIn("'-' + value", self.page)

    def test_rounds_are_accepted_one_by_one(self):
        for part in ("acceptRound: 'Accept this round'", "acceptRound: 'Kör elfogadása'", "summary: 'Summary'",
                     "summary: 'Összegzés'", "aria-disabled", "accepted: 'accepted'"):
            self.assertIn(part, self.page)

    def test_done_keeps_readable_colours_when_waiting(self):
        # A Done that waits for acceptance gets its own colour pair instead of a faded orange that fails contrast,
        # and the hidden attribute always wins over a display rule.
        self.assertIn("button.primary[aria-disabled=true]{opacity:1;background:var(--btnoff);border-color:var(--btnoff);"
                      "color:var(--btnofftext)", self.page)
        self.assertIn("[hidden]{display:none!important}", self.page)

    def test_label_lookup_ignores_inherited_keys(self):
        self.assertIn("Object.prototype.hasOwnProperty.call(LABELS,", self.page)

    def test_a_stale_save_tells_the_user_to_reload(self):
        self.assertIn("stale: 'Changed in another tab or window, so this tab can no longer save.", self.page)
        self.assertIn("Copy any text you have not saved yet, then reload to see the latest answers.", self.page)
        self.assertIn("Az újratöltés előtt másold ki a még nem mentett szövegedet.", self.page)
        self.assertIn("rev: rev[ri]", self.page)

    def test_labels_in_english_and_hungarian(self):
        self.assertRegex(self.page, r"\ben: \{")
        self.assertRegex(self.page, r"\bhu: \{")

    def test_every_label_of_a_round_page_has_hungarian(self):
        # A round with lang "hu" uses LABELS.hu; a key only in English would show as "undefined". The inbox's own
        # labels (its Markdown and inboxPage) are English only: the inbox has no round language.
        labels = self.page[self.page.index("var LABELS = {"):self.page.index("var data = JSON.parse")]
        en = set(re.findall(r"\b(\w+): '", labels[:labels.index("hu: {")]))
        hu = set(re.findall(r"\b(\w+): '", labels[labels.index("hu: {"):]))
        script = self.page[self.page.index("var data = JSON.parse"):]
        inbox = script[script.index("// ---- The inbox."):script.index("if (data.mode === 'inbox')")]
        used = set(re.findall(r"\bL\.(\w+)", script.replace(inbox, "")))
        self.assertTrue(used and used <= en, used - en)
        self.assertEqual(used - hu, set())

    def test_an_orphan_round_says_its_session_is_gone_in_english_and_hungarian(self):
        labels = self.page[self.page.index("var LABELS = {"):self.page.index("var data = JSON.parse")]
        en, hu = labels[:labels.index("hu: {")], labels[labels.index("hu: {"):]
        self.assertIn("orphan: 'The session that asked this has ended; your answer goes to the project\\'s next "
                      "session.'", en)
        self.assertIn("orphan: 'A kérdező munkamenet már nincs; a válaszod a projekt következő munkamenetéhez "
                      "kerül.'", hu)
        self.assertIn("orphanKind: 'Session ended'", en)
        self.assertIn("orphanKind: '", hu)
        script = self.page[self.page.index("var data = JSON.parse"):]
        inbox = script[script.index("// ---- The inbox."):script.index("if (data.mode === 'inbox')")]
        self.assertRegex(inbox, r"\bL\.orphan\b")  # the list's opened row
        self.assertRegex(inbox, r"\bL\.orphanKind\b")  # the list's row
        self.assertRegex(script.replace(inbox, ""), r"\bL\.orphan\b")  # the round's page, in the round's language

    def test_no_label_is_defined_twice(self):
        # A second key of the same name wins silently, so an inbox label could rename a round page's heading.
        labels = self.page[self.page.index("var LABELS = {"):self.page.index("var data = JSON.parse")]
        for part in (labels[:labels.index("hu: {")], labels[labels.index("hu: {"):]):
            keys = re.findall(r"\b(\w+): '", part)
            self.assertEqual(sorted({k for k in keys if keys.count(k) > 1}), [])

    def test_the_date_filter_hint_follows_the_language(self):
        self.assertIn("byId('h-from').placeholder = L.fromHint;", self.page)
        self.assertIn("fromHint: 'YYYY-MM-DD'", self.page)
        self.assertIn("fromHint: 'ÉÉÉÉ-HH-NN'", self.page)


if __name__ == "__main__":
    unittest.main()
