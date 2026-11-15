"""The server's security rules, autosave and resume, driven over real HTTP on 127.0.0.1."""
import errno
import json
import os
import re
import socket
import sys
import unittest
from unittest import mock

from support import Folder, Running, free_port, png_bytes, sample_decisions, signoff


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

    def test_a_fixed_port_on_request(self):
        port = free_port()
        other = signoff.make_server(self.folder.decisions, port=port)
        try:
            self.assertEqual(other.server_address[:2], ("127.0.0.1", port))
            self.assertTrue(other.start_url.startswith("http://127.0.0.1:%d/b/" % port), other.start_url)
        finally:
            other.server_close()

    def test_a_port_in_use_names_the_port(self):
        with socket.socket() as taken:
            taken.bind(("127.0.0.1", 0))
            taken.listen()
            port = taken.getsockname()[1]
            with self.assertRaises(OSError) as caught:
                signoff.make_server(self.folder.decisions, port=port)
        self.assertIn("port %d" % port, str(caught.exception))

    def test_a_listener_on_every_address_counts_as_in_use(self):
        # macOS lets a SO_REUSEADDR bind of 127.0.0.1:P sit beside a 0.0.0.0:P listener of the same user and take
        # its loopback traffic: a connect before the bind sees that one.
        with socket.socket() as taken:
            taken.bind(("0.0.0.0", 0))
            taken.listen()
            port = taken.getsockname()[1]
            with self.assertRaises(OSError) as caught:
                signoff.make_server(self.folder.decisions, port=port)
        self.assertIn("port %d" % port, str(caught.exception))
        self.assertEqual(caught.exception.errno, errno.EADDRINUSE)

    def test_socket_options_per_platform(self):
        # On Windows SO_REUSEADDR lets another socket bind a port in use; SO_REUSEPORT would anywhere.
        self.assertEqual(signoff._Server.allow_reuse_address, sys.platform != "win32")
        self.assertIs(signoff._Server.allow_reuse_port, False)

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
        # The URL's colon too: GFM links a bare URL even inside parentheses.
        self.assertIn("\\*bold\\* \\_x\\_ \\[link\\](http\\://e.example) \\<b\\>tag\\</b\\> # head \\`code\\` \\| pipe", md)
        self.assertIn("\n  1\\. not a list", md)
        self.assertIn("free \\*text\\*", md)
        self.assertTrue(md.rstrip().endswith("Done: no"))
        self.assertEqual([p.name for p in self.folder.path.iterdir() if p.name.startswith(".tmp")], [])
        self.run_.save({"answers": {}, "free_text": "", "done": True})
        self.assertTrue(md_path.read_text(encoding="utf-8").rstrip().endswith("Done: yes"))

    def test_markdown_names_stakes_irreversible_and_settled(self):
        data = sample_decisions()
        data["items"][0].update(stakes="high", irreversible="Drops the *old* table", settled="round-01, 11-14")
        data["items"][1]["stakes"] = "low"
        self.folder.write(data)
        run = Running(self.folder)
        try:
            self.assertEqual(run.save({"answers": {"layout": {"choice": "two", "comment": "", "source": "recommended"}},
                                       "free_text": "", "done": False})[0].status, 200)
        finally:
            run.stop()
        md = (self.folder.path / "decisions.answers.md").read_text(encoding="utf-8")
        # Settled is the agent's word that the user decided it before, and the copy says so.
        self.assertIn("- Stakes: high\n- Cannot be undone: Drops the \\*old\\* table\n"
                      "- Settled earlier, says the agent: round-01, 11-14\n", md)
        self.assertIn("- Stakes: low\n", md)
        self.assertEqual(md.count("Cannot be undone"), 1)
        answers = json.loads(self.folder.answers.read_text(encoding="utf-8"))
        self.assertEqual(set(answers["answers"]["layout"]), {"choice", "comment", "source"})  # no new key there
        decisions = signoff.load_decisions(self.folder.decisions)[0]
        record = {"answers": {"layout": {"choice": "two", "comment": "mine", "private": True}}, "free_text": "",
                  "done": False, "saved": "2026-01-01T12:00:00Z"}
        # The notes folder's copy hides a private answer, not the agent's own lines.
        hidden = signoff.answers_markdown(decisions, record, hide_private=True)
        self.assertIn("(private)\n- Stakes: high\n- Cannot be undone: Drops the \\*old\\* table\n"
                      "- Settled earlier, says the agent: round-01, 11-14\n", hidden)
        self.assertNotIn("mine", hidden)
        hu = signoff.answers_markdown(dict(decisions, lang="hu"), record)
        self.assertIn("- Tét: magas\n- Nem vonható vissza: Drops the \\*old\\* table\n"
                      "- Korábban eldőlt, az ágens szerint: round-01, 11-14\n", hu)

    def test_a_settled_item_cannot_be_saved_as_decided_elsewhere(self):
        data = sample_decisions()
        data["items"][0]["settled"] = "round-01"
        self.folder.write(data)
        run = Running(self.folder)
        try:
            resp, body = run.save({"answers": {"layout": {"choice": None, "comment": "n", "source": "elsewhere"}},
                                   "free_text": "", "done": False})
        finally:
            run.stop()
        self.assertEqual(resp.status, 400)
        self.assertIn(b"settled", body)
        self.assertFalse(self.folder.answers.exists())


class HostAndToken(ServerTest):
    def test_wrong_host(self):
        self.assertEqual(self.get(self.page, host="evil.example")[0].status, 403)

    def test_localhost_names_the_same_page(self):
        # A forwarded port or a devcontainer's Ports tab may open the page as localhost: the same rules hold.
        local = "localhost:%d" % self.run_.port
        start = self.run_.server.start_url[len("http://" + self.run_.host):]
        resp, _ = self.get(start, host=local)
        self.assertEqual((resp.status, resp.getheader("Location")), (303, self.page))
        self.assertEqual(self.get(self.page, host=local)[0].status, 200)
        self.assertEqual(self.get(self.page + "img/0", host=local)[0].status, 200)
        resp, data = self.run_.save(state(), Host=local, Origin="http://" + local)
        self.assertEqual(resp.status, 200, data)
        self.assertTrue(self.folder.answers.exists())

    def test_origin_must_be_its_own_host(self):
        local = "localhost:%d" % self.run_.port
        for host, origin in ((self.run_.host, "http://" + local), (local, self.run_.origin)):
            self.assertEqual(self.run_.save(state(), Host=host, Origin=origin)[0].status, 403, (host, origin))
        self.assertFalse(self.folder.answers.exists())

    def test_other_names_are_not_accepted(self):
        port = self.run_.port
        for host in ("localhost.example:%d" % port, "[::1]:%d" % port, "127.0.0.1:%d" % (port + 1), "localhost",
                     "localhost:%d.evil.example" % port, ""):
            self.assertEqual(self.get(self.page, headers={"Host": host})[0].status, 403, host)
            resp, _ = self.run_.save(state(), Host=host, Origin="http://" + host)
            self.assertEqual(resp.status, 403, host)
        self.assertFalse(self.folder.answers.exists())

    def test_near_misses_are_not_accepted_and_spend_no_start_link(self):
        # Pins against a "normalising" Host check: only the two exact strings pass, once.
        port, own = self.run_.port, self.run_.host
        start = self.run_.server.start_url[len("http://" + own):]
        for host in [h % port for h in ("LOCALHOST:%d", "Localhost:%d", "localhost.:%d", "127.0.0.1.:%d", "127.1:%d",
                                        "0x7f000001:%d", "127.0.0.2:%d", "0.0.0.0:%d", "localhost.example:%d",
                                        "[::1]:%d")] + \
                ["localhost:%d" % (port + 1), "", None, [own, "evil.example"], ["evil.example", own], [own, own]]:
            for path in (self.page, start):
                self.assertEqual(self.get(path, headers={"Host": host})[0].status, 403, (host, path))
            origin = "http://" + host if isinstance(host, str) else self.run_.origin
            self.assertEqual(self.run_.save(state(), Host=host, Origin=origin)[0].status, 403, host)
        self.assertFalse(self.folder.answers.exists())
        self.assertEqual(self.get(start)[0].status, 303)  # still unspent

    def test_a_localhost_save_needs_exactly_its_own_origin(self):
        port = self.run_.port
        local = "localhost:%d" % port
        for origin in ("null", None, "http://%s/" % local, "https://" + local, "http://localhost:%d" % (port + 1),
                       "http://LOCALHOST:%d" % port, ""):
            self.assertEqual(self.run_.save(state(), Host=local, Origin=origin)[0].status, 403, origin)
        self.assertFalse(self.folder.answers.exists())

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


DIFF = "diff --git a/x b/x\n--- a/x\n+++ b/x\n@@ -1,2 +1,2 @@\n-old *line*\n+new <b>line</b>\n"


def with_lines(lang=None):
    """The sample round, its first item with a diff attached and line notes on."""
    data = sample_decisions()
    data["items"][0]["attachments"] = [{"src": "notes/change.diff", "caption": "The change"}]
    data["items"][0]["lines"] = True
    if lang:
        data["lang"] = lang
    return data


def noted(*notes, private=None):
    ans = {"choice": "two", "comment": "", "source": "user", "lines": list(notes)}
    if private is not None:
        ans["private"] = private
    return {"answers": {"layout": ans}, "free_text": "", "done": False}


def note(line=6, quote="+new <b>line</b>", comment="see *this*"):
    return {"att": 0, "line": line, "quote": quote, "comment": comment}


class LineNotes(unittest.TestCase):
    """An answer's line notes: checked against the attachment's text on save when new or changed, kept as stored
    otherwise, counted in the round's body limit, and one line each in the Markdown copy."""

    def setUp(self):
        self.folder = Folder(with_lines())
        self.addCleanup(self.folder.cleanup)
        (self.folder.path / "notes").mkdir()
        self.diff = self.folder.path / "notes" / "change.diff"
        self.diff.write_bytes(DIFF.encode("utf-8"))

    def serve(self):
        run = Running(self.folder)
        self.addCleanup(run.stop)
        return run

    def saved(self):
        return json.loads(self.folder.answers.read_text(encoding="utf-8"))

    def test_a_good_note_is_saved_and_a_bad_one_refused(self):
        run = self.serve()
        self.assertEqual(run.save(noted(note(), note(1, "diff --git a/x b/x")))[0].status, 200)
        self.assertEqual(self.saved()["answers"]["layout"]["lines"], [note(), note(1, "diff --git a/x b/x")])
        before = self.folder.answers.read_bytes()
        for bad in (note(quote="+new <b>SECRET</b>"), note(line=7, quote=""), note(line=5), note(comment="")):
            resp, body = run.save(noted(note(), bad))
            self.assertEqual(resp.status, 400, bad)
            self.assertIn(b"line note 2 ", body)
            self.assertNotIn(b"SECRET", body)
        self.assertEqual(self.folder.answers.read_bytes(), before)

    def test_an_unchanged_note_saves_after_its_attachment_changed(self):
        self.assertEqual(self.serve().save(noted(note()))[0].status, 200)
        self.diff.write_bytes(DIFF.replace("+new", "+newer").encode("utf-8"))  # line 6 is not what the note quotes
        run = self.serve()  # the new text is read at start; the stored note loads all the same
        self.assertEqual(run.save(dict(noted(note(), note(6, "+newer <b>line</b>")), rev=0))[0].status, 200)
        self.assertEqual(self.saved()["answers"]["layout"]["lines"][0], note())
        self.assertEqual(run.save(dict(noted(note(comment="changed")), rev=1))[0].status, 400)  # changed: checked
        self.assertEqual(run.save(dict(noted(note()), rev=1))[0].status, 200)

    def test_at_most_50_notes(self):
        run = self.serve()
        self.assertEqual(run.save(noted(*[note()] * 50))[0].status, 200)
        resp, body = run.save(noted(*[note()] * 51))
        self.assertEqual(resp.status, 400)
        self.assertIn(b"up to 50", body)

    def test_only_an_item_with_lines_takes_notes(self):
        run = self.serve()
        other = {"answers": {"filters": {"choice": ["date"], "lines": []}}, "free_text": "", "done": False}
        self.assertEqual(run.save(other)[0].status, 400)
        self.assertEqual(run.save(noted())[0].status, 200)

    def test_the_body_limit_has_room_for_50_full_notes(self):
        run = self.serve()
        texts = 2  # the free text and layout's comment
        want = texts * signoff.MAX_TEXT * 6 + 64 * 1024 + signoff.MAX_LINE_NOTES * (signoff.MAX_TEXT + 300) * 6
        self.assertEqual(run.server.rounds[0].max_body, want)
        full = noted(*[note(comment="\x01" * signoff.MAX_TEXT)] * 50, private=False)
        full["answers"]["layout"]["comment"] = "\x01" * signoff.MAX_TEXT
        full["free_text"] = "\x01" * signoff.MAX_TEXT
        self.assertEqual(run.save(full)[0].status, 200)  # each \x01 takes 6 bytes as JSON

    def test_markdown_writes_a_line_per_note_and_private_hides_its_comment(self):
        run = self.serve()
        self.assertEqual(run.save(noted(note(), note(1, "diff --git a/x b/x", "first")))[0].status, 200)
        md = (self.folder.path / "decisions.answers.md").read_text(encoding="utf-8")
        self.assertIn("- Line note, notes/change.diff line 6, “+new \\<b\\>line\\</b\\>”: see \\*this\\*\n", md)
        self.assertIn("- Line note, notes/change.diff line 1, “diff --git a/x b/x”: first\n", md)
        self.assertEqual(run.save(dict(noted(note(comment="a SECRET reason"), private=True), rev=1))[0].status, 200)
        md = (self.folder.path / "decisions.answers.md").read_text(encoding="utf-8")
        self.assertIn("- Line note, notes/change.diff line 6, “+new \\<b\\>line\\</b\\>”: (private)\n", md)
        self.assertNotIn("SECRET", md)

    def test_markdown_keeps_a_stored_quote_on_its_line(self):
        # A stored note is not checked against the text, so its quote may hold anything a file can.
        decisions = signoff.load_decisions(self.folder.decisions)[0]
        record = dict(noted(note(quote="a\rb c\n# head‮", comment="x\n## y")),
                      saved="2026-01-01T12:00:00Z")
        md = signoff.answers_markdown(decisions, record)
        [line] = [ln for ln in md.split("\n") if "Line note" in ln]
        self.assertTrue(line.startswith("- Line note, notes/change.diff line 6, “a b c # headU+202E”: x"),
                        line)
        self.assertNotIn("‮", md)
        self.assertIn("”: x  \n  \\## y\n", md)  # the comment's second line is no heading
        self.folder.write(with_lines("hu"))
        decisions = signoff.load_decisions(self.folder.decisions)[0]
        self.assertIn("- Sor-megjegyzés, notes/change.diff 6. sora, „a b c # headU+202E”: x",
                      signoff.answers_markdown(decisions, record))

    def test_an_old_round_keeps_its_body_limit(self):
        self.folder.write(sample_decisions())
        self.assertEqual(self.serve().server.rounds[0].max_body,
                         max(signoff.MAX_BODY, 2 * signoff.MAX_TEXT * 6 + 64 * 1024))


if __name__ == "__main__":
    unittest.main()
