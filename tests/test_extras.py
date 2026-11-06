"""HTML boards, user-drawn marks, and the read-only page of past decisions."""
import hashlib
import json
import os
import re
import unittest

from support import Folder, Running, png_bytes, sample_decisions, signoff

BOARD = b"<!doctype html><meta charset=utf-8><style>body{margin:0}</style><p id=x>board</p><script>1</script>"


def with_board(data=None):
    data = data or sample_decisions()
    data["items"][0]["images"].append({"html": "boards/b.html", "caption": "Live"})
    return data


def board_folder():
    folder = Folder()
    (folder.path / "boards").mkdir()
    (folder.path / "boards" / "b.html").write_bytes(BOARD)
    folder.write(with_board())
    return folder


def view_of(run):
    body = run.request("GET", "/%s/" % run.token)[1].decode("utf-8")
    return json.loads(re.search(r'id="signoff-data">(.*?)</script>', body, re.S).group(1)), body


class BoardFile(unittest.TestCase):
    def setUp(self):
        self.folder = board_folder()

    def tearDown(self):
        self.folder.cleanup()

    def broken(self, change):
        data = with_board()
        change(data["items"][0]["images"][1])
        self.folder.write(data)
        with self.assertRaises(signoff.DecisionsError) as ctx:
            signoff.load_decisions(self.folder.decisions)
        return str(ctx.exception)

    def test_board_loads_beside_pictures(self):
        _, media = signoff.load_decisions(self.folder.decisions)
        self.assertEqual([p.name for p in media], ["a.png", "b.html"])

    def test_board_rules(self):
        self.assertIn("exactly one", self.broken(lambda b: b.update(src="mockups/a.png")))
        self.assertIn("boxes", self.broken(lambda b: b.update(boxes=[[1, 1, 2, 2]])))
        self.assertIn("outside", self.broken(lambda b: b.update(html="../x.html")))
        self.assertIn("HTML", self.broken(lambda b: b.update(html="mockups/a.png")))
        self.assertIn("not found", self.broken(lambda b: b.update(html="boards/none.html")))

    def test_board_size_limit(self):
        (self.folder.path / "boards" / "b.html").write_bytes(b"x" * (signoff.MAX_BOARD + 1))
        with self.assertRaises(signoff.DecisionsError) as ctx:
            signoff.make_server(self.folder.decisions)
        self.assertIn("larger than", str(ctx.exception))

    def test_board_must_be_utf8(self):
        (self.folder.path / "boards" / "b.html").write_bytes(b"\xff\xfe<p>")
        with self.assertRaises(signoff.DecisionsError) as ctx:
            signoff.make_server(self.folder.decisions)
        self.assertIn("UTF-8", str(ctx.exception))


class BoardRoute(unittest.TestCase):
    def setUp(self):
        self.folder = board_folder()
        self.run_ = Running(self.folder)
        self.base = "/%s/" % self.run_.token

    def tearDown(self):
        self.run_.stop()
        self.folder.cleanup()

    def test_page_points_at_the_board_by_number(self):
        view, body = view_of(self.run_)
        board = "/%s/0/0" % self.run_.server.board_token
        self.assertEqual(view["rounds"][0]["decisions"]["items"][0]["images"][1], {"board": board, "caption": "Live"})
        self.assertNotIn("boards/b.html", body)

    def test_a_board_never_learns_the_page_token(self):
        # The board's own URL is all it can read about where it is: it must open nothing but boards.
        srv = self.run_.server
        self.assertNotEqual(srv.board_token, srv.token)
        self.assertEqual(self.run_.request("GET", "/%s/0/0" % srv.board_token)[0].status, 200)
        for path in ("", "img/0", "save/0", "board/0/0", "0/1", "1/0", "0/0/"):
            self.assertEqual(self.run_.request("GET", "/%s/%s" % (srv.board_token, path))[0].status, 404, path)
        resp, _ = self.run_.request("POST", "/%s/save/0" % srv.board_token, {"answers": {}, "free_text": "",
                                    "done": False}, {"Content-Type": "application/json", "Origin": self.run_.origin})
        self.assertEqual(resp.status, 404)
        self.assertEqual(self.run_.request("GET", self.base + "board/0/0")[0].status, 404)

    def test_board_is_served_inside_a_sandbox(self):
        for query in ("", "?theme=light", "?theme=dark"):
            resp, body = self.run_.request("GET", "/%s/0/0%s" % (self.run_.server.board_token, query))
            self.assertEqual(resp.status, 200, query)
            self.assertEqual(body, BOARD)
            self.assertEqual(resp.getheader("Content-Type"), "text/html; charset=utf-8")
            csp = resp.getheader("Content-Security-Policy")
            self.assertTrue(csp.startswith("sandbox allow-scripts;"), csp)
            for part in ("default-src 'none'", "script-src 'unsafe-inline'", "style-src 'unsafe-inline'",
                         "img-src data:", "base-uri 'none'", "form-action 'none'"):
                self.assertIn(part, csp)
            self.assertNotIn("allow-same-origin", csp)
            self.assertNotIn("connect-src", csp)  # default-src 'none' covers fetch
            self.assertEqual(resp.getheader("X-Content-Type-Options"), "nosniff")

    def test_other_queries_and_paths(self):
        boards = "/%s/" % self.run_.server.board_token
        for path in ("0/0?theme=blue", "0/0?theme=dark&x=1", "0/1", "1/0", "0/00", "0", "0/0/", "0/0#x"):
            self.assertEqual(self.run_.request("GET", boards + path)[0].status, 404, path)
        for path in ("img/0?theme=dark", "?theme=dark"):
            self.assertEqual(self.run_.request("GET", self.base + path)[0].status, 404, path)

    def test_board_needs_host_and_token(self):
        board = "/%s/0/0" % self.run_.server.board_token
        self.assertEqual(self.run_.request("GET", board, host="evil.example")[0].status, 403)
        self.assertEqual(self.run_.request("GET", "/x/0/0")[0].status, 404)

    def test_a_board_cannot_save(self):
        # A sandboxed frame without allow-same-origin has an opaque origin, which browsers send as "null".
        resp, _ = self.run_.save({"answers": {}, "free_text": "", "done": False}, Origin="null")
        self.assertEqual(resp.status, 403)

    def test_page_allows_frames_from_itself_only(self):
        csp = self.run_.request("GET", self.base)[0].getheader("Content-Security-Policy")
        self.assertIn("frame-src 'self'", csp)

    def test_page_embeds_boards_sandboxed(self):
        page = (signoff.HERE / "page.html").read_text(encoding="utf-8")
        self.assertIn("frame.setAttribute('sandbox', 'allow-scripts')", page)
        self.assertIn("frame.setAttribute('referrerpolicy', 'no-referrer')", page)
        self.assertNotIn("allow-same-origin", page)

    @unittest.skipIf(os.name == "nt", "symlinks need extra rights on Windows")
    def test_board_is_read_at_start(self):
        (self.folder.path / "boards" / "b.html").write_bytes(b"changed")
        self.assertEqual(self.run_.request("GET", "/%s/0/0" % self.run_.server.board_token)[1], BOARD)


class Marks(unittest.TestCase):
    def setUp(self):
        folder = Folder()
        (folder.path / "boards").mkdir()
        (folder.path / "boards" / "b.html").write_bytes(BOARD)
        data = with_board()
        data["items"][0]["marks"] = True
        folder.write(data)
        self.decisions, _ = signoff.load_decisions(folder.decisions)
        folder.cleanup()

    def check(self, marks, item="layout"):
        return signoff.check_answers({"answers": {item: {"choice": "two", "comment": "", "marks": marks}},
                                      "free_text": "", "done": False}, self.decisions)

    def bad(self, marks, item="layout"):
        with self.assertRaises(ValueError):
            self.check(marks, item)

    def test_marks_default_off(self):
        self.assertFalse(self.decisions["items"][1]["marks"])

    def test_good_marks(self):
        marks = [{"image": 0, "box": [10, 20, 30, 40], "comment": "this one"}, {"image": 0, "box": [0, 0, 100, 100],
                                                                                "comment": ""}]
        self.assertEqual(self.check(marks)["answers"]["layout"]["marks"], marks)

    def test_bad_marks(self):
        self.bad({"image": 0})
        self.bad([{"image": 1, "box": [1, 1, 2, 2], "comment": ""}])  # a board, not a picture
        self.bad([{"image": 2, "box": [1, 1, 2, 2], "comment": ""}])
        self.bad([{"image": True, "box": [1, 1, 2, 2], "comment": ""}])
        self.bad([{"image": 0, "box": [90, 1, 20, 2], "comment": ""}])
        self.bad([{"image": 0, "box": [1, 1, 0, 2], "comment": ""}])
        self.bad([{"image": 0, "box": [1, 1, 2, 2], "comment": 5}])
        self.bad([{"image": 0, "box": [1, 1, 2, 2], "comment": "x" * (signoff.MAX_TEXT + 1)}])
        self.bad([{"image": 0, "box": [1, 1, 2, 2], "comment": "", "colour": "red"}])
        self.bad([{"image": 0, "box": [1, 1, 2, 2], "comment": ""}] * (signoff.MAX_MARKS + 1))

    def test_marks_only_where_allowed(self):
        self.bad([], item="filters")

    def test_markdown_lists_marks(self):
        record = {"answers": {"layout": {"choice": "two", "comment": "", "marks": [
            {"image": 0, "box": [10, 20, 30, 40.5], "comment": "too *tight*"}]}}, "free_text": "", "done": False,
            "saved": "2026-01-01T12:00:00Z"}
        md = signoff.answers_markdown(self.decisions, record)
        self.assertIn("- Mark 1, picture 1 at 10, 20, 30, 40.5 (x, y, w, h in %): too \\*tight\\*", md)


def write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes((data if isinstance(data, str) else json.dumps(data)).encode("utf-8"))  # no \r\n on Windows


class HistoryRoot:
    """A project whose .signoff folder holds a done round with an outcome, an open round, a round whose
    decisions changed after Done, a round with a missing picture and a broken file; one more topic sits in
    docs/decisions, for --folder."""

    def __init__(self):
        import tempfile
        from pathlib import Path
        self._tmp = tempfile.TemporaryDirectory()
        self.path = Path(self._tmp.name).resolve()
        a = self.path / ".signoff" / "2026-01-05-settings"
        b = self.path / ".signoff" / "2026-02-01-export"
        (a / "mockups").mkdir(parents=True)
        (a / "mockups" / "a.png").write_bytes(png_bytes())
        write(a / "round-01.json", sample_decisions())
        sha = hashlib.sha256((a / "round-01.json").read_bytes()).hexdigest()
        write(a / "round-01.answers.json", {"version": 1, "done": True, "saved": "2026-01-05T10:00:00Z",
                                            "decisions": {"file": "round-01.json", "sha256": sha},
                                            "answers": {"layout": {"choice": "one", "comment": "later"}},
                                            "free_text": ""})
        write(a / "round-01.outcome.json", {"log": "docs/decisions.md", "line": "Keep one column <b>for now</b>",
                                            "date": "2026-01-06"})
        second = sample_decisions()
        second["title"] = "Second round"
        second["tab"] = "Two"
        write(a / "round-02.json", second)
        write(a / "round-02.answers.json", {"version": 1, "done": False, "saved": "2026-01-05T11:00:00Z",
                                            "answers": {}, "free_text": "half"})
        write(a / "notes.txt", "not a round")
        changed = sample_decisions()
        changed["title"] = "Export"
        changed["items"][0]["images"][0]["src"] = "mockups/gone.png"
        write(b / "decisions.json", changed)
        write(b / "decisions.answers.json", {"version": 1, "done": True, "saved": "2026-02-01T09:00:00Z",
                                             "decisions": {"file": "decisions.json", "sha256": "0" * 64},
                                             "answers": {}, "free_text": ""})
        write(b / "round-02.json", "{broken")
        write(b / "round-03.outcome.json", "{}")  # an outcome is never a round
        write(self.path / "elsewhere" / "round-01.json", sample_decisions())  # outside the decisions folder
        write(self.path / "docs" / "decisions" / "2026-03-01-custom" / "round-01.json", sample_decisions())

    def cleanup(self):
        self._tmp.cleanup()


class History(unittest.TestCase):
    def setUp(self):
        self.root = HistoryRoot()

    def tearDown(self):
        self.root.cleanup()

    def serve(self, **kw):
        server = signoff.make_history_server(self.root.path, **kw)
        run = Running.__new__(Running)
        run.server, run.port, run.token = server, server.server_address[1], server.token
        run.host = "127.0.0.1:%d" % run.port
        run.origin = "http://" + run.host
        import threading
        run.thread = threading.Thread(target=server.serve_forever, args=(0.05,), daemon=True)
        run.thread.start()
        self.addCleanup(run.stop)
        return run

    def entries(self, **kw):
        return {e["topic"] + "/" + e["file"]: e for e in view_of(self.serve(**kw))[0]["entries"]}

    def test_lists_newest_first(self):
        view, _ = view_of(self.serve())
        self.assertEqual(view["mode"], "history")
        self.assertEqual([e["topic"] + "/" + e["file"] for e in view["entries"]],
                         ["2026-02-01-export/decisions.json", "2026-02-01-export/round-02.json",
                          "2026-01-05-settings/round-01.json", "2026-01-05-settings/round-02.json"])

    def test_status_outcome_and_warnings(self):
        e = self.entries()
        first = e["2026-01-05-settings/round-01.json"]
        self.assertEqual((first["status"], first["date"], first["title"]), ("done", "2026-01-05",
                                                                           "Pick the settings layout"))
        self.assertEqual(first["outcome"], {"log": "docs/decisions.md", "line": "Keep one column <b>for now</b>",
                                            "date": "2026-01-06"})
        self.assertIsNone(first["warning"])
        self.assertTrue(first["readonly"])
        self.assertEqual(first["answers"]["answers"]["layout"]["choice"], "one")
        second = e["2026-01-05-settings/round-02.json"]
        self.assertEqual((second["status"], second["tab"]), ("open", "Two"))
        export = e["2026-02-01-export/decisions.json"]
        self.assertIn("changed after", export["warning"])
        self.assertEqual(export["decisions"]["items"][0]["images"][0]["missing"], "not found")
        broken = e["2026-02-01-export/round-02.json"]
        self.assertEqual(broken["status"], "error")
        self.assertIn("not valid JSON", broken["error"])
        self.assertNotIn(str(self.root.path), broken["error"])
        self.assertIsNone(broken["decisions"])

    def test_since(self):
        self.assertEqual(sorted(self.entries(since="2026-01-10")), ["2026-02-01-export/decisions.json",
                                                                   "2026-02-01-export/round-02.json"])

    def test_images_are_read_when_asked(self):
        run = self.serve()
        view, _ = view_of(run)
        src = [e for e in view["entries"] if e["file"] == "round-01.json"][0]["decisions"]["items"][0]["images"][0]
        self.assertEqual(run.request("GET", "/%s/%s" % (run.token, src["src"]))[1], png_bytes())

    def test_image_budget(self):
        view, _ = view_of(self.serve(budget=10))
        shot = [e for e in view["entries"] if e["file"] == "round-01.json"][0]["decisions"]["items"][0]["images"][0]
        self.assertIn("--since", shot["missing"])

    def test_read_only(self):
        run = self.serve()
        resp, _ = run.save({"answers": {}, "free_text": "", "done": True})
        self.assertEqual(resp.status, 405)
        self.assertEqual(run.request("GET", "/%s/" % run.token, host="evil.example")[0].status, 403)
        self.assertEqual(run.request("GET", "/nope/")[0].status, 404)
        self.assertFalse((self.root.path / ".signoff" / "2026-01-05-settings" / ".gitignore").exists())

    @unittest.skipIf(os.name == "nt", "symlinks need extra rights on Windows")
    def test_answers_and_outcome_linked_from_outside_are_not_read(self):
        folder = self.root.path / ".signoff" / "2026-01-05-settings"
        outside = self.root.path / "elsewhere"
        write(outside / "secret-outcome.json", {"log": "x", "line": "SECRET", "date": "y"})
        (folder / "round-01.outcome.json").rename(folder / "old-outcome.json")
        (folder / "round-01.outcome.json").symlink_to(outside / "secret-outcome.json")
        first = self.entries()["2026-01-05-settings/round-01.json"]
        self.assertIsNone(first["outcome"])
        self.assertIn("outside", first["warning"])

    def test_folder(self):
        custom = ["2026-03-01-custom/round-01.json"]
        self.assertEqual(sorted(self.entries(folder="docs/decisions")), custom)
        self.assertEqual(sorted(self.entries(folder=str(self.root.path / "docs" / "decisions"))), custom)
        self.assertEqual(self.entries(folder="missing"), {})

    def test_private_rounds_show_beside_old_ones(self):
        topic = self.root.path / ".signoff" / "2026-03-02-private"
        data = sample_decisions()
        del data["items"][0]["images"]
        data["items"][0]["sensitive"] = True
        write(topic / "round-01.json", data)
        sha = hashlib.sha256((topic / "round-01.json").read_bytes()).hexdigest()
        write(topic / "round-01.answers.json", {
            "version": 1, "done": True, "saved": "2026-03-02T10:00:00Z",
            "decisions": {"file": "round-01.json", "sha256": sha},
            "answers": {"layout": {"choice": "one", "comment": "mine", "private": True}}, "free_text": ""})
        e = self.entries()
        new = e["2026-03-02-private/round-01.json"]
        self.assertEqual((new["status"], new["error"], new["warning"]), ("done", None, None))
        self.assertTrue(new["decisions"]["items"][0]["sensitive"])
        self.assertIs(new["answers"]["answers"]["layout"]["private"], True)
        old = e["2026-01-05-settings/round-01.json"]
        self.assertEqual((old["status"], old["error"]), ("done", None))
        self.assertNotIn("private", old["answers"]["answers"]["layout"])

    def test_rounds_with_criteria_show_beside_old_ones(self):
        topic = self.root.path / ".signoff" / "2026-03-03-storage"
        data = sample_decisions()
        data["items"][1]["criteria"] = [{"id": "effort", "label": "Effort"}]
        data["items"][1]["options"][0]["facts"] = {"effort": {"text": "S", "tone": "good"}}
        write(topic / "round-01.json", data)
        e = self.entries()
        new = e["2026-03-03-storage/round-01.json"]
        self.assertEqual((new["status"], new["error"]), ("new", None))
        self.assertEqual(new["decisions"]["items"][1]["criteria"], [{"id": "effort", "label": "Effort"}])
        self.assertEqual(new["decisions"]["items"][1]["options"][0]["facts"], {"effort": {"text": "S", "tone": "good"}})
        self.assertNotIn("criteria", e["2026-01-05-settings/round-01.json"]["decisions"]["items"][1])

    def test_root_must_be_a_folder(self):
        with self.assertRaises(signoff.DecisionsError):
            signoff.make_history_server(self.root.path / "nope")

    @unittest.skipIf(os.name == "nt", "symlinks need extra rights on Windows")
    def test_topic_link_loop_is_skipped(self):
        (self.root.path / ".signoff" / "2026-03-05-loop").symlink_to("2026-03-05-loop")
        self.assertIn("2026-01-05-settings/round-01.json", self.entries())

    def test_deeply_nested_files(self):
        topic = self.root.path / ".signoff" / "2026-03-06-deep"
        write(topic / "round-01.json", sample_decisions())
        for name in ("round-01.answers.json", "round-01.outcome.json", "round-02.json"):
            write(topic / name, "[" * 100000)
        e = self.entries()
        self.assertEqual(e["2026-03-06-deep/round-02.json"]["status"], "error")
        self.assertIn("not valid JSON", e["2026-03-06-deep/round-02.json"]["error"])
        warning = e["2026-03-06-deep/round-01.json"]["warning"]
        self.assertIn("answers file cannot be read", warning)
        self.assertIn("outcome file cannot be read", warning)

    @unittest.skipIf(os.name == "nt", "symlinks need extra rights on Windows")
    def test_topic_folder_linked_from_outside_is_skipped(self):
        outside = self.root.path / "elsewhere"
        (self.root.path / ".signoff" / "2026-03-01-linked").symlink_to(outside, target_is_directory=True)
        self.assertNotIn("2026-03-01-linked/round-01.json", self.entries())


class ProjectName(unittest.TestCase):
    """The page shows the project folder's name, never a path: the git root above the decisions folder, else the
    folder above a .signoff folder, else nothing."""

    def setUp(self):
        import tempfile
        from pathlib import Path
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.base = Path(self._tmp.name).resolve()

    def topic(self, *parts):
        path = self.base.joinpath(*parts)
        path.mkdir(parents=True)
        return path

    def write_decisions(self, topic):
        data = sample_decisions()
        data["items"][0]["images"] = []
        (topic / "decisions.json").write_text(json.dumps(data), encoding="utf-8")

    def test_git_root(self):
        self.topic("shop", ".git")
        topic = self.topic("shop", "docs", "decisions", "2026-01-01-a")
        self.assertEqual(signoff.project_name(topic), "shop")

    def test_git_file_of_a_worktree_or_submodule(self):
        topic = self.topic("wt", ".signoff", "2026-01-01-a")
        (self.base / "wt" / ".git").write_text("gitdir: elsewhere\n", encoding="utf-8")
        self.assertEqual(signoff.project_name(topic), "wt")

    def test_the_nearest_git_root_wins(self):
        self.topic("outer", ".git")
        self.topic("outer", "inner", ".git")
        self.assertEqual(signoff.project_name(self.topic("outer", "inner", ".signoff", "t")), "inner")

    def test_a_nearer_signoff_folder_beats_a_farther_git_root(self):
        self.topic("mono", ".git")
        self.assertEqual(signoff.project_name(self.topic("mono", "apps", "foo", ".signoff", "t")), "foo")

    def test_a_dotfiles_repo_at_home_gives_no_name(self):
        from unittest import mock
        self.topic(".git")  # the base is the home folder, and a git repo
        with mock.patch.object(signoff.Path, "home", return_value=self.base):
            self.assertEqual(signoff.project_name(self.topic("projects", "foo", ".signoff", "t")), "foo")
            self.assertIsNone(signoff.project_name(self.topic("projects", "bar", "docs", "t")))
            self.assertIsNone(signoff.project_name(self.topic(".signoff", "t")))  # home's own .signoff folder

    def test_signoff_folder_without_git(self):
        self.assertEqual(signoff.project_name(self.topic("plain", ".signoff", "2026-01-01-a")), "plain")

    def test_nothing_to_go_on_is_none(self):
        self.assertIsNone(signoff.project_name(self.topic("loose", "a", "b")))

    def test_the_page_gets_the_name_and_no_path(self):
        self.topic("shop", ".git")
        topic = self.topic("shop", ".signoff", "2026-01-01-a")
        self.write_decisions(topic)
        server = signoff.make_server(topic / "decisions.json")
        self.addCleanup(server.server_close)
        view = server.view()
        self.assertEqual(view["project"], "shop")
        self.assertNotIn(str(self.base), json.dumps(view))

    def test_no_name_no_key_value(self):
        topic = self.topic("loose", "a")
        self.write_decisions(topic)
        server = signoff.make_server(topic / "decisions.json")
        self.addCleanup(server.server_close)
        self.assertIsNone(server.view()["project"])

    def test_history_names_its_root(self):
        root = HistoryRoot()
        self.addCleanup(root.cleanup)
        server = signoff.make_history_server(root.path)
        self.addCleanup(server.server_close)
        self.assertEqual(server.view()["root"], root.path.name)


if __name__ == "__main__":
    unittest.main()
