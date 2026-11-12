"""The server's security rules, autosave and resume, driven over real HTTP on 127.0.0.1."""
import json
import os
import re
import unittest
from unittest import mock

from support import Folder, Running, png_bytes, sample_decisions, signoff


def state(done=False):
    return {"answers": {"layout": {"choice": "one", "comment": "a note"}}, "free_text": "more", "done": done}


class ServerTest(unittest.TestCase):
    def setUp(self):
        self.folder = Folder()
        self.run_ = Running(self.folder)
        self.page = "/%s/" % self.run_.token

    def tearDown(self):
        self.run_.stop()
        self.folder.cleanup()

    def get(self, path, **kw):
        return self.run_.request("GET", path, **kw)


class Binding(ServerTest):
    def test_binds_loopback_on_a_chosen_port(self):
        host, port = self.run_.server.server_address[:2]
        self.assertEqual(host, "127.0.0.1")
        self.assertGreater(port, 0)

    def test_fresh_token_each_run(self):
        other = signoff.make_server(self.folder.decisions)
        try:
            self.assertNotEqual(other.token, self.run_.token)
            self.assertGreaterEqual(len(other.token), 40)
        finally:
            other.server_close()

    def test_url(self):
        self.assertEqual(self.run_.server.url, "http://%s/%s/" % (self.run_.host, self.run_.token))

    def test_no_name_lookup_at_start(self):
        # http.server looks up the host's name when it binds; on a macOS CI machine that took 35 s a process.
        with mock.patch.object(signoff.socket, "getfqdn", side_effect=AssertionError("a name lookup at start")):
            other = signoff.make_server(self.folder.decisions)
        other.server_close()


class Page(ServerTest):
    def test_page_is_served_with_strict_headers(self):
        resp, body = self.get(self.page)
        self.assertEqual(resp.status, 200)
        csp = resp.getheader("Content-Security-Policy")
        nonce = re.search(r"script-src 'nonce-([^']+)'", csp).group(1)
        for part in ("default-src 'none'", "style-src 'nonce-%s'" % nonce, "img-src 'self'", "connect-src 'self'",
                     "base-uri 'none'", "form-action 'none'", "frame-ancestors 'none'"):
            self.assertIn(part, csp)
        self.assertEqual(resp.getheader("X-Content-Type-Options"), "nosniff")
        self.assertEqual(resp.getheader("Referrer-Policy"), "no-referrer")
        self.assertIn("no-store", resp.getheader("Cache-Control"))
        text = body.decode("utf-8")
        self.assertIn('<script nonce="%s">' % nonce, text)
        self.assertNotIn("__NONCE__", text)
        self.assertNotIn("__DATA__", text)

    def test_nonce_changes_per_request(self):
        a = self.get(self.page)[0].getheader("Content-Security-Policy")
        b = self.get(self.page)[0].getheader("Content-Security-Policy")
        self.assertNotEqual(a, b)

    def test_agent_text_cannot_break_out_of_the_data_block(self):
        data = sample_decisions()
        data["title"] = "</script><script>alert(1)</script><!--"
        self.folder.write(data)
        run = Running(self.folder)
        try:
            body = run.request("GET", "/%s/" % run.token)[1].decode("utf-8")
        finally:
            run.stop()
        self.assertNotIn("<script>alert(1)", body)
        self.assertEqual(body.count("</script>"), 2)  # the data block and the page script, nothing more

    def test_page_holds_no_file_paths(self):
        body = self.get(self.page)[1].decode("utf-8")
        self.assertNotIn("mockups/a.png", body)
        self.assertNotIn(str(self.folder.path), body)
        self.assertIn('"img/0"', body)

    def test_page_never_uses_inner_html(self):
        page = (signoff.HERE / "page.html").read_text(encoding="utf-8")
        for unsafe in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", " style=", "onclick=",
                       "setAttribute('style'", "cssText", 'src="http', 'href="http', "url(http", "@import", "fetch('http"):
            self.assertNotIn(unsafe, page)


class Additions(ServerTest):
    def test_accept_all_is_a_native_button_in_the_header(self):
        page = (signoff.HERE / "page.html").read_text(encoding="utf-8")
        header = page[page.index("<header>"):page.index("</header>")]
        button = re.search(r'<button type="button" id="accept-all">(.*?)</button>', header, re.S)
        self.assertIsNotNone(button)
        self.assertEqual(re.sub(r"<[^>]+>", "", button.group(1).split('<span class="undo"')[0]).strip(),
                         "Accept all recommendations")

    def test_notice_reaches_the_page_as_text(self):
        data = sample_decisions()
        data["notice"] = "<b>Synthetic</b> data only <script>alert(1)</script>"
        self.folder.write(data)
        run = Running(self.folder)
        try:
            body = run.request("GET", "/%s/" % run.token)[1].decode("utf-8")
        finally:
            run.stop()
        self.assertNotIn("<b>Synthetic", body)
        self.assertNotIn("<script>alert(1)", body)
        self.assertIn("\\u003cb\\u003eSynthetic", body)
        page = (signoff.HERE / "page.html").read_text(encoding="utf-8")
        self.assertIn("notice.textContent = d.notice", page)

    def test_markdown_structure_cannot_be_changed(self):
        lines = ["# heading", "## sub", "- item", "+ item", "* item", "> quote", "2) x", "3. y", "---", "===",
                 "~~~", "```js", "    indented code", "<script>alert(1)</script>", "[a](javascript:x)", "| a | b |",
                 "![img](http://e.example/x.png)"]
        self.run_.save({"answers": {"layout": {"choice": "one", "comment": "\n".join(lines)}},
                        "free_text": "\n".join(lines), "done": False})
        md = (self.folder.path / "decisions.answers.md").read_text(encoding="utf-8")
        own = re.compile(r"^(# Pick the settings layout|## (Layout|Anything else)|### [12]\. |- (Choice|Source|Comment): |"
                         r"Saved: |Done: )")
        for line in md.splitlines():
            if not line.strip() or own.match(line):
                continue
            body = line.strip()
            self.assertNotRegex(body, r"^(#|>|[-+*]\s|\d+[.)]|-{3,}$|={3,}$|~~~|```|<|!?\[)", line)
            self.assertNotRegex(body, r"(?<!\\)[<>`*_\[\]|]", line)
        self.assertNotIn("    indented", md)

    def test_markdown_stays_readable(self):
        data = sample_decisions()
        data["title"] = "Release plan: 2026-10-23, v1.2 - final"
        self.folder.write(data)
        run = Running(self.folder)
        try:
            run.save({"answers": {"layout": {"choice": "two", "comment": "Ship on 2026-11-02: ok."}},
                      "free_text": "", "done": False})
        finally:
            run.stop()
        md = (self.folder.path / "decisions.answers.md").read_text(encoding="utf-8")
        self.assertTrue(md.startswith("# Release plan: 2026-10-23, v1.2 - final\n"), md[:80])
        self.assertIn("### 1. Two columns or one?", md)
        self.assertIn("- Comment: Ship on 2026-11-02: ok.", md)
        self.assertRegex(md, r"\nSaved: \d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ  \n")

    def test_markdown_export_on_every_save(self):
        hostile = "*bold* _x_ [link](http://e.example) <b>tag</b> # head `code` | pipe\n1. not a list"
        self.run_.save({"answers": {"layout": {"choice": "one", "comment": hostile, "source": "user"},
                                    "filters": {"choice": ["date", "tag"], "comment": "", "source": "accept_all"}},
                        "free_text": "free *text*", "done": False})
        md_path = self.folder.path / "decisions.answers.md"
        md = md_path.read_text(encoding="utf-8")
        self.assertTrue(md.startswith("# Pick the settings layout\n"))
        self.assertIn("## Layout", md)
        self.assertIn("Two columns or one?", md)
        self.assertIn("Keep one", md)
        self.assertIn("not recommended", md)
        self.assertIn("Date (recommended), Tag (recommended)", md)
        self.assertIn("\\*bold\\* \\_x\\_ \\[link\\](http://e.example) \\<b\\>tag\\</b\\> # head \\`code\\` \\| pipe", md)
        self.assertIn("\n  1\\. not a list", md)
        self.assertIn("free \\*text\\*", md)
        self.assertTrue(md.rstrip().endswith("Done: no"))
        self.assertEqual([p.name for p in self.folder.path.iterdir() if p.name.startswith(".tmp")], [])
        self.run_.save({"answers": {}, "free_text": "", "done": True})
        self.assertTrue(md_path.read_text(encoding="utf-8").rstrip().endswith("Done: yes"))


class HostAndToken(ServerTest):
    def test_wrong_host(self):
        self.assertEqual(self.get(self.page, host="evil.example")[0].status, 403)

    def test_localhost_name_is_not_accepted(self):
        self.assertEqual(self.get(self.page, host="localhost:%d" % self.run_.port)[0].status, 403)

    def test_host_without_port(self):
        self.assertEqual(self.get(self.page, host="127.0.0.1")[0].status, 403)

    def test_host_with_suffix(self):
        self.assertEqual(self.get(self.page, host=self.run_.host + ".evil.example")[0].status, 403)

    def test_wrong_host_cannot_save(self):
        resp, _ = self.run_.request("POST", self.page + "save/0", state(), host="evil.example",
                                    headers={"Content-Type": "application/json", "Origin": self.run_.origin})
        self.assertEqual(resp.status, 403)
        self.assertFalse(self.folder.answers.exists())

    def test_missing_token(self):
        self.assertEqual(self.get("/")[0].status, 404)
        self.assertEqual(self.get("/save")[0].status, 404)
        self.assertEqual(self.get("/img/0")[0].status, 404)

    def test_wrong_token(self):
        self.assertEqual(self.get("/%s/" % ("A" * len(self.run_.token)))[0].status, 404)
        self.assertEqual(self.get("/%s/" % self.run_.token[:-1])[0].status, 404)

    def test_non_ascii_token_is_a_plain_404(self):
        self.assertEqual(self.get("/%C3%A9/")[0].status, 404)

    def test_wrong_token_cannot_save(self):
        resp, _ = self.run_.request("POST", "/x/save/0", state(),
                                    headers={"Content-Type": "application/json", "Origin": self.run_.origin})
        self.assertEqual(resp.status, 404)
        self.assertFalse(self.folder.answers.exists())

    def test_no_trailing_slash_no_redirect(self):
        self.assertEqual(self.get("/%s" % self.run_.token)[0].status, 404)

    def test_other_methods(self):
        self.assertEqual(self.run_.request("PUT", self.page + "save/0", b"{}")[0].status, 501)
        self.assertEqual(self.run_.request("DELETE", self.page)[0].status, 501)


class Save(ServerTest):
    def test_save_writes_the_answers(self):
        resp, _ = self.run_.save(state())
        self.assertEqual(resp.status, 200)
        saved = json.loads(self.folder.answers.read_text(encoding="utf-8"))
        self.assertEqual(saved["version"], 1)
        self.assertFalse(saved["done"])
        self.assertEqual(saved["answers"]["layout"], {"choice": "one", "comment": "a note"})
        self.assertEqual(saved["free_text"], "more")
        self.assertRegex(saved["saved"], r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$")
        self.assertEqual([p.name for p in self.folder.path.iterdir() if p.name.startswith(".tmp")], [])

    @unittest.skipIf(os.name == "nt", "POSIX file modes")
    def test_answers_file_is_private(self):
        self.run_.save(state())
        self.assertEqual(self.folder.answers.stat().st_mode & 0o777, 0o600)

    def test_charset_is_allowed(self):
        self.assertEqual(self.run_.save(state(), **{"Content-Type": "application/json; charset=utf-8"})[0].status, 200)

    def test_foreign_origin(self):
        self.assertEqual(self.run_.save(state(), Origin="http://evil.example")[0].status, 403)
        self.assertFalse(self.folder.answers.exists())

    def test_other_local_port_origin(self):
        self.assertEqual(self.run_.save(state(), Origin="http://127.0.0.1:1")[0].status, 403)

    def test_null_origin(self):
        self.assertEqual(self.run_.save(state(), Origin="null")[0].status, 403)

    def test_missing_origin(self):
        headers = {"Content-Type": "application/json"}
        resp, _ = self.run_.request("POST", self.page + "save/0", state(), headers)
        self.assertEqual(resp.status, 403)
        self.assertFalse(self.folder.answers.exists())

    def test_sec_fetch_site_other_than_same_origin(self):
        for value in ("same-site", "cross-site", "none"):
            self.assertEqual(self.run_.save(state(), **{"Sec-Fetch-Site": value})[0].status, 403, value)
        self.assertFalse(self.folder.answers.exists())

    def test_sec_fetch_site_may_be_absent(self):
        headers = {"Content-Type": "application/json", "Origin": self.run_.origin}
        self.assertEqual(self.run_.request("POST", self.page + "save/0", state(), headers)[0].status, 200)

    def test_text_plain_body(self):
        self.assertEqual(self.run_.save(state(), **{"Content-Type": "text/plain"})[0].status, 415)
        self.assertFalse(self.folder.answers.exists())

    def test_form_body(self):
        resp, _ = self.run_.save(state(), **{"Content-Type": "application/x-www-form-urlencoded"})
        self.assertEqual(resp.status, 415)

    def test_oversized_body(self):
        big = dict(state(), free_text="x" * (self.run_.server.rounds[0].max_body + 10))
        self.assertEqual(self.run_.save(big)[0].status, 413)
        self.assertFalse(self.folder.answers.exists())

    def test_oversized_length_is_refused_before_reading(self):
        resp, _ = self.run_.save(state(), **{"Content-Length": str(self.run_.server.rounds[0].max_body + 1)})
        self.assertEqual(resp.status, 413)

    def test_bad_length(self):
        self.assertEqual(self.run_.save(state(), **{"Content-Length": "abc"})[0].status, 411)

    def test_invalid_json(self):
        resp, _ = self.run_.request("POST", self.page + "save/0", b"{nope",
                                    {"Content-Type": "application/json", "Origin": self.run_.origin})
        self.assertEqual(resp.status, 400)

    def test_answers_are_checked_against_the_decisions(self):
        bad = state()
        bad["answers"]["layout"]["choice"] = "three"
        self.assertEqual(self.run_.save(bad)[0].status, 400)
        self.assertFalse(self.folder.answers.exists())

    def test_chunked_body(self):
        resp, _ = self.run_.request("POST", self.page + "save/0", b"0\r\n\r\n", {
            "Content-Type": "application/json", "Origin": self.run_.origin, "Transfer-Encoding": "chunked"})
        self.assertEqual(resp.status, 411)

    def test_non_ascii_length(self):
        self.assertEqual(self.run_.save(state(), **{"Content-Length": "\u00b2"})[0].status, 411)

    def test_deeply_nested_json(self):
        resp, _ = self.run_.request("POST", self.page + "save/0", b"[" * 200000,
                                    {"Content-Type": "application/json", "Origin": self.run_.origin})
        self.assertEqual(resp.status, 400)

    def test_lone_surrogate_in_a_save(self):
        body = b'{"answers": {}, "free_text": "\\ud800", "done": false}'
        resp, _ = self.run_.request("POST", self.page + "save/0", body,
                                    {"Content-Type": "application/json", "Origin": self.run_.origin})
        self.assertEqual(resp.status, 400)
        self.assertFalse(self.folder.answers.exists())

    def test_wrong_host_does_not_keep_the_server_alive(self):
        self.run_.server.last_activity = 0
        self.get(self.page, host="evil.example")
        self.get("/nope/")
        self.assertEqual(self.run_.server.last_activity, 0)
        self.get(self.page)
        self.assertGreater(self.run_.server.last_activity, 0)

    def test_errors_carry_the_security_headers(self):
        resp, _ = self.run_.request("OPTIONS", self.page)
        self.assertEqual(resp.getheader("X-Content-Type-Options"), "nosniff")
        self.assertEqual(resp.getheader("Referrer-Policy"), "no-referrer")
        self.assertTrue(resp.getheader("Content-Type").startswith("text/plain"))

    def test_saves_after_done_are_refused(self):
        # Hold the shutdown back, as if a second tab's save arrived before the server stopped.
        self.run_.server.shutdown = lambda: None
        try:
            self.assertEqual(self.run_.save(state(done=True))[0].status, 200)
            late = dict(state(), free_text="late")
            self.assertEqual(self.run_.save(late)[0].status, 409)
        finally:
            del self.run_.server.shutdown
        saved = json.loads(self.folder.answers.read_text(encoding="utf-8"))
        self.assertTrue(saved["done"])
        self.assertEqual(saved["free_text"], "more")

    def test_get_save_is_not_a_save(self):
        self.assertEqual(self.get(self.page + "save/0")[0].status, 404)

    def test_done_stops_the_server(self):
        self.assertFalse(self.run_.server.finished.is_set())
        self.assertEqual(self.run_.save(state(done=True))[0].status, 200)
        self.run_.thread.join(5)
        self.assertFalse(self.run_.thread.is_alive())
        self.assertTrue(self.run_.server.finished.is_set())
        self.assertTrue(json.loads(self.folder.answers.read_text(encoding="utf-8"))["done"])


class Images(ServerTest):
    def test_listed_image(self):
        resp, body = self.get(self.page + "img/0")
        self.assertEqual(resp.status, 200)
        self.assertEqual(resp.getheader("Content-Type"), "image/png")
        self.assertEqual(resp.getheader("X-Content-Type-Options"), "nosniff")
        self.assertEqual(body, png_bytes())

    def test_image_not_on_the_list(self):
        for path in ("img/1", "img/99", "img/-1", "img/00", "img/a", "img/", "img/0/", "mockups/a.png",
                     "decisions.json", "decisions.answers.json"):
            self.assertEqual(self.get(self.page + path)[0].status, 404, path)

    def test_path_traversal(self):
        for path in ("img/../decisions.json", "img/..%2fdecisions.json", "../%s/img/0" % self.run_.token,
                     "img/0/../../decisions.json", "%2e%2e/decisions.json", "img/0?../x"):
            resp, body = self.get(self.page + path)
            self.assertIn(resp.status, (404,), path)
            self.assertNotIn(b'"version"', body)

    @unittest.skipIf(os.name == "nt", "symlinks need extra rights on Windows")
    def test_image_swapped_for_a_link_after_start(self):
        secret = self.folder.path.parent / ("secret-%d.png" % os.getpid())
        secret.write_bytes(b"SECRET-OUTSIDE")
        try:
            img = self.folder.path / "mockups" / "a.png"
            img.rename(self.folder.path / "mockups" / "old.png")
            img.symlink_to(secret)
            resp, body = self.get(self.page + "img/0")
        finally:
            secret.unlink()
        self.assertNotIn(b"SECRET", body)
        self.assertEqual(body, png_bytes())

    def test_svg_never_served(self):
        (self.folder.path / "mockups" / "a.svg").write_text("<svg/>", encoding="utf-8")
        data = sample_decisions()
        data["items"][0]["images"][0]["src"] = "mockups/a.svg"
        self.folder.write(data)
        with self.assertRaises(signoff.DecisionsError):
            signoff.make_server(self.folder.decisions)


class Resume(unittest.TestCase):
    def setUp(self):
        self.folder = Folder()

    def tearDown(self):
        self.folder.cleanup()

    def test_resume_from_saved_answers(self):
        first = Running(self.folder)
        first.save(state())
        first.stop()
        second = Running(self.folder)
        try:
            body = second.request("GET", "/%s/" % second.token)[1].decode("utf-8")
        finally:
            second.stop()
        self.assertIn('"a note"', body)
        self.assertIn('"more"', body)

    def test_no_answers_yet(self):
        run = Running(self.folder)
        try:
            body = run.request("GET", "/%s/" % run.token)[1].decode("utf-8")
        finally:
            run.stop()
        self.assertIn('"answers": null', body)

    def test_broken_answers_file_is_refused_and_kept(self):
        self.folder.answers.write_text("{broken", encoding="utf-8")
        with self.assertRaises(signoff.DecisionsError) as ctx:
            signoff.make_server(self.folder.decisions)
        self.assertIn("answers", str(ctx.exception))
        self.assertEqual(self.folder.answers.read_text(encoding="utf-8"), "{broken")

    def test_stale_answers_file_is_refused(self):
        self.folder.answers.write_text(json.dumps(
            {"version": 1, "done": False, "answers": {"gone": {"choice": "x", "comment": ""}}, "free_text": ""}),
            encoding="utf-8")
        with self.assertRaises(signoff.DecisionsError):
            signoff.make_server(self.folder.decisions)


if __name__ == "__main__":
    unittest.main()
