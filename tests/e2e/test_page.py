import json
import re
from pathlib import Path

from playwright.sync_api import Page, expect

from e2e.conftest import TITLE

PR = "/acme/widgets/pull/1"
APP = "section[data-file='src/app.py']"


def open_pr(page: Page, server_url: str, path: str = PR) -> None:
    """Open the page and wait until its comments have loaded, since that load rebuilds the sidebar."""
    page.goto(server_url + path)
    page.wait_for_selector("#side .tree", timeout=30000)
    page.wait_for_function("C.loaded")


def test_start_page_builds_and_opens_a_pull_request(page: Page, server_url: str) -> None:
    page.goto(server_url + "/home")
    page.fill("input[name=pr]", "acme/widgets#2")
    page.press("input[name=pr]", "Enter")
    page.wait_for_url("**/acme/widgets/pull/2")
    expect(page.locator("#log")).to_be_visible()
    page.wait_for_selector("#side .tree", timeout=30000)
    expect(page.locator("#side h1")).to_have_text(f"#2 {TITLE}")
    expect(page.locator("#side .leaf")).to_have_count(2)
    expect(page.locator("#overview h1")).to_have_text(TITLE)


def test_the_page_highlights_code_without_the_network(page: Page, server_url: str) -> None:
    requests: list[str] = []
    page.on("request", lambda request: requests.append(request.url))
    open_pr(page, server_url)
    expect(page.locator(f"{APP} .hljs-keyword").first).to_have_text("def")
    assert [url for url in requests if not url.startswith(server_url)] == []


def test_tree_hides_noise_only_and_generated_files_until_asked(page: Page, server_url: str) -> None:
    open_pr(page, server_url)
    notices = page.locator("#side .list .notice")
    expect(notices.nth(0)).to_have_text("2 files with only noise hidden · show")
    expect(notices.nth(1)).to_have_text("1 generated file hidden · show")
    expect(page.locator("#side .leaf[data-path='src/cli.py']")).to_have_count(0)
    expect(page.locator("#side .leaf[data-path='notes/notes.txt']")).to_have_count(0)
    expect(page.locator("#side .leaf[data-path='package-lock.json']")).to_have_count(0)
    page.click("#side .list .notice a[data-noiseonly]")
    expect(page.locator("#side .leaf[data-path='src/cli.py']")).to_be_visible()
    expect(page.locator("#side .leaf[data-path='notes/notes.txt']")).to_be_visible()
    expect(page.locator("#side .list .notice").nth(0)).to_have_text("Showing 2 files with only noise · hide")
    page.click("#side .list .notice a[data-generated]")
    expect(page.locator("#side .leaf[data-path='package-lock.json']")).to_be_visible()
    expect(page.locator("#side .leaf")).to_have_count(5)


def test_a_binary_file_shows_its_size_instead_of_line_counts(page: Page, server_url: str) -> None:
    open_pr(page, server_url, "/acme/widgets/pull/3")
    section = page.locator("section[data-file='src/logo.png']")
    section.scroll_into_view_if_needed()
    expect(section.locator(".fhead .counts")).to_have_text("4.9 KB")
    expect(section.locator(".code > .empty")).to_have_text("Binary file added · 4.9 KB")


def test_a_renamed_dataclass_field_shows_the_lines_up_to_its_class(page: Page, server_url: str) -> None:
    open_pr(page, server_url, "/acme/widgets/pull/4")
    section = page.locator("section[data-file='src/models.py']")
    section.scroll_into_view_if_needed()
    expect(section.locator("tr.add")).to_have_text(re.compile("new_total: float"))
    for line in ("@dataclass", "class Order:", "id: int", "shipped: bool"):
        expect(section.locator("tr.ctx", has_text=line)).to_be_visible()
    expect(section.locator("tr.fold")).to_have_text("⋯ 7 unchanged lines")


def test_a_fold_opens_on_click_and_closes_again(page: Page, server_url: str) -> None:
    open_pr(page, server_url)
    section = page.locator(APP)
    fold = section.locator("tr.collapsed[data-fold]").first
    expect(fold).to_contain_text("import")
    expect(section.locator("tr.add", has_text="import sys")).to_have_count(0)
    fold.click()
    opened = section.locator("tr.runhead[data-fold]")
    expect(opened).to_have_count(1)
    expect(opened).to_contain_text("import")
    expect(section.locator("tr.add", has_text="import sys")).to_be_visible()
    opened.click()
    expect(section.locator("tr.runhead[data-fold]")).to_have_count(0)
    expect(section.locator("tr.collapsed[data-fold]")).to_have_count(1)
    expect(section.locator("tr.add", has_text="import sys")).to_have_count(0)


def test_the_height_estimate_counts_the_rows_each_file_draws(page: Page, server_url: str) -> None:
    open_pr(page, server_url)
    mismatches = page.evaluate("""() => ['', 'name'].flatMap(q => {
        state.q = q;
        const drawn = (f) => codeRows(f).html.filter(h => !/^<tr class="(thread|compose)"/.test(h)).length;
        const counted = (f) => fileBlocks(f, true).reduce((n, b) => n + drawnRows(b), 0);
        return FILES.filter(f => f.runs.length && counted(f) !== drawn(f)).map(f => `${q}:${f.path}`);
    })""")
    assert mismatches == []


def test_cmd_f_focuses_the_search_and_enter_goes_to_the_first_hit(page: Page, server_url: str) -> None:
    open_pr(page, server_url)
    page.keyboard.press("Meta+f")
    expect(page.locator("#side .search")).to_be_focused()
    page.keyboard.type("hello")
    expect(page.locator("#searchmeta")).to_have_text("1 file · 1 line")
    expect(page.locator("#side .leaf[data-path='src/app.py'] .hits")).to_have_text("1")
    page.keyboard.press("Enter")
    expect(page.locator("#searchmeta")).to_have_text("1 file · 1 / 1")
    target = page.locator(f"{APP} tr.target")
    expect(target).to_have_count(1)
    expect(target).to_contain_text('print("hello")')


def test_search_counts_the_lines_on_screen_and_skips_closed_folds_and_sections(page: Page, server_url: str) -> None:
    open_pr(page, server_url)
    section = page.locator(APP)
    page.fill("#side .search", "i")
    expect(page.locator("#searchmeta")).to_have_text("2 files · 6 lines")
    expect(page.locator("#main tr:has(mark)")).to_have_count(6)
    expect(page.locator("#main tr.ctx:has(mark)")).to_have_count(2)
    expect(section.locator("tr.collapsed[data-fold]")).to_contain_text("imports")
    expect(section.locator("tr", has_text="import sys")).to_have_count(0)
    section.locator("tr.collapsed[data-fold]").click()
    expect(page.locator("#searchmeta")).to_have_text("2 files · 8 lines")
    expect(page.locator("#main tr:has(mark)")).to_have_count(8)
    section.locator(".fhead .top").click()
    expect(page.locator("#searchmeta")).to_have_text("2 files · 2 lines")
    expect(page.locator("#side .leaf[data-path='src/app.py']")).to_have_count(1)
    expect(page.locator("#side .leaf[data-path='src/app.py'] .hits")).to_have_count(0)


def test_cmd_f_fills_the_search_with_the_selected_code(page: Page, server_url: str) -> None:
    open_pr(page, server_url)
    page.locator(f"{APP} tr.add td.c", has_text="hello").select_text()
    page.keyboard.press("Meta+f")
    expect(page.locator("#side .search")).to_be_focused()
    expect(page.locator("#side .search")).to_have_value('print("hello")')
    expect(page.locator("#searchmeta")).to_have_text("1 file · 1 line")


def test_mark_reviewed_marks_the_file_reviewed_and_the_mark_survives_a_reload(page: Page, server_url: str) -> None:
    page.set_viewport_size({"width": 1100, "height": 400})
    open_pr(page, server_url)
    leaf = "#side .leaf[data-path='src/app.py']"
    page.click(leaf)
    expect(page.locator(leaf)).to_have_class(re.compile(r"\bactive\b"))
    page.click(f"{APP} button[data-review]")
    expect(page.locator(leaf)).to_have_class(re.compile(r"\breviewed\b"))
    expect(page.locator(APP)).to_have_class(re.compile(r"\bclosed\b"))
    page.reload()
    page.wait_for_selector("#side .tree")
    page.wait_for_function("C.loaded")
    expect(page.locator(leaf)).to_have_class(re.compile(r"\breviewed\b"))
    expect(page.locator(APP)).to_have_class(re.compile(r"\bclosed\b"))
    expect(page.locator("#side .head .meta")).to_contain_text("1 reviewed")


def test_threads_show_on_their_lines_and_in_the_overview(page: Page, server_url: str) -> None:
    open_pr(page, server_url)
    section = page.locator(APP)
    open_thread = section.locator(".thread[data-thread='T1']")
    expect(open_thread).to_contain_text("Say hi properly")
    expect(open_thread.locator(".comment b")).to_have_text("bob")
    expect(
        section.locator("tr.add", has_text='print("hello")').locator("xpath=following-sibling::tr[1]")
    ).to_have_class("thread")
    expect(section.locator(".thread.folded[data-thread-open='T2']")).to_contain_text(
        "Resolved by bob · ann: nit: spacing"
    )
    expect(page.locator("#side .leaf[data-path='src/app.py'] .bubble")).to_have_text("1")
    rows = page.locator("#overview table.threads tr")
    expect(rows).to_have_count(2)
    expect(rows.nth(0)).to_contain_text("app.py:11")
    expect(rows.nth(0)).to_contain_text("Say hi properly")
    expect(rows.nth(1)).to_have_class("resolved")
    expect(page.locator("#overview .comment", has_text="bob approved")).to_contain_text("LGTM")
    expect(page.locator("#overview .comment", has_text="ann commented")).to_contain_text("Opening this for review.")


def test_posting_a_single_comment_sends_it_and_shows_the_thread(page: Page, server_url: str, gh_state: Path) -> None:
    open_pr(page, server_url)
    section = page.locator(APP)
    section.locator("tr.add", has_text='print("hello")').locator("td.n").first.click()
    box = page.locator("textarea[data-compose-text]")
    expect(box).to_be_focused()
    expect(page.locator(".form.compose .hint")).to_contain_text("Comment on line 11")
    box.fill("Use a logger here")
    page.click("button[data-compose-send='single']")
    expect(section.locator("div.thread[data-thread]", has_text="Use a logger here")).to_be_visible()
    expect(page.locator("textarea[data-compose-text]")).to_have_count(0)
    expect(page.locator("#side .leaf[data-path='src/app.py'] .bubble")).to_have_text("2")

    mutations = [json.loads(line) for line in (gh_state / "mutations.log").read_text().splitlines()]
    posted = [m for m in mutations if "addPullRequestReview(" in m["query"]]
    assert len(posted) == 1
    assert "event: COMMENT" in posted[0]["query"]
    assert posted[0]["variables"]["threads"] == [
        {"path": "src/app.py", "body": "Use a logger here", "line": 11, "side": "RIGHT"}
    ]


def test_request_changes_waits_for_a_review_summary(page: Page, server_url: str, gh_state: Path) -> None:
    open_pr(page, server_url)
    page.click("#side button[data-review-panel]")
    box = page.locator("textarea[data-review-text]")
    button = page.locator("button[data-submit='REQUEST_CHANGES']")
    expect(button).to_be_disabled()
    box.fill("   ")
    expect(button).to_be_disabled()
    box.fill("Please add a test")
    expect(button).to_be_enabled()
    box.fill("")
    expect(button).to_be_disabled()
    button.click(force=True)
    box.fill("Please add a test")
    page.click("#side button[data-review-panel]")
    page.click("#side button[data-review-panel]")
    expect(button).to_be_enabled()
    button.click()
    expect(page.locator("textarea[data-review-text]")).to_have_count(0)

    mutations = [json.loads(line) for line in (gh_state / "mutations.log").read_text().splitlines()]
    posted = [m["variables"] for m in mutations if m["variables"].get("event") == "REQUEST_CHANGES"]
    assert len(posted) == 1
    assert posted[0]["body"] == "Please add a test"


def test_a_comment_draft_survives_opening_another_box_and_escape(page: Page, server_url: str) -> None:
    open_pr(page, server_url)
    hello = page.locator(APP).locator("tr.add", has_text='print("hello")').locator("td.n").first
    hello.click()
    box = page.locator("textarea[data-compose-text]")
    box.fill("Use a logger here")
    box.press("Escape")
    expect(box).to_have_value("Use a logger here")
    expect(box).not_to_be_focused()

    page.locator("section[data-file='README.md'] tr.add td.n").first.click()
    expect(page.locator("textarea[data-compose-text]")).to_have_count(1)
    expect(page.locator("textarea[data-compose-text]")).to_have_value("")
    expect(page.locator("section[data-file='README.md'] .form.compose")).to_be_visible()

    hello.click()
    expect(page.locator(APP).locator("textarea[data-compose-text]")).to_have_value("Use a logger here")
    page.click("button[data-compose-cancel]")
    expect(page.locator("textarea[data-compose-text]")).to_have_count(0)
    hello.click()
    expect(page.locator("textarea[data-compose-text]")).to_have_value("")


def test_a_comment_draft_stays_on_its_line_and_joins_a_box_extended_onto_it(page: Page, server_url: str) -> None:
    open_pr(page, server_url)
    section = page.locator(APP)
    hello = section.locator("tr.add", has_text='print("hello")').locator("td.n").first
    main = section.locator("tr.add", has_text="print(new_name(2))").locator("td.n").first
    box = page.locator("textarea[data-compose-text]")
    hello.click()
    box.fill("First")
    main.click()
    expect(box).to_have_value("")
    box.fill("Second")
    hello.click(modifiers=["Shift"])
    expect(page.locator(".form.compose .hint")).to_contain_text("Comment on lines 10–11")
    expect(box).to_have_value("First\n\nSecond")
    page.locator("section[data-file='README.md'] tr.add td.n").first.click()
    expect(box).to_have_value("")
    hello.click()
    expect(box).to_have_value("First\n\nSecond")
    page.click("button[data-compose-cancel]")


def test_dragging_from_a_line_number_opens_a_comment_on_the_lines_in_between(page: Page, server_url: str) -> None:
    open_pr(page, server_url)
    section = page.locator(APP)
    hello = section.locator("tr.add", has_text='print("hello")').locator("td.n").first
    main = section.locator("tr.add", has_text="print(new_name(2))").locator("td.n").first
    hello.scroll_into_view_if_needed()
    start, end = hello.bounding_box(), main.bounding_box()
    page.mouse.move(start["x"] + 5, start["y"] + 5)
    page.mouse.down()
    page.mouse.move(end["x"] + 5, end["y"] + 5, steps=5)
    expect(section.locator("tr.sel")).to_have_count(2)
    page.mouse.up()
    expect(page.locator(".form.compose .hint")).to_contain_text("Comment on lines 10–11")
    expect(page.locator("textarea[data-compose-text]")).to_be_focused()


def test_escape_in_a_reply_box_keeps_the_reply(page: Page, server_url: str) -> None:
    open_pr(page, server_url)
    page.click("button[data-reply='T1']")
    box = page.locator("textarea[data-reply-text='T1']")
    box.fill("Will do")
    box.press("Escape")
    expect(box).to_have_value("Will do")
    expect(box).not_to_be_focused()


def test_shell_opens_a_tab_and_keeps_it_after_a_reload(page: Page, server_url: str, gh_state: Path) -> None:
    page.goto(server_url + "/")
    home = page.frame_locator("#panes iframe.on")
    home.locator("input[name=pr]").fill("acme/widgets#1")
    home.locator("input[name=pr]").press("Enter")
    tab = page.locator("#tabs .tab:not(.home)")
    expect(tab).to_have_count(1)
    expect(tab).to_have_class(re.compile(r"\bon\b"))
    expect(tab.locator(".label")).to_have_text(f"#1 {TITLE}", timeout=30000)
    unresolved = sum(not t["isResolved"] for t in json.loads((gh_state / "comments.json").read_text())["threads"])
    expect(tab.locator(".badge")).to_have_text(str(unresolved))
    expect(page.frame_locator("#panes iframe.on").locator("#side .tree")).to_be_visible()

    page.reload()
    tab = page.locator("#tabs .tab:not(.home)")
    expect(tab).to_have_count(1)
    expect(tab.locator(".label")).to_have_text(f"#1 {TITLE}")
    page.click("#tabs .tab.home")
    expect(page.locator("#tabs .tab.home")).to_have_class(re.compile(r"\bon\b"))
    expect(page.frame_locator("#panes iframe.on").locator("input[name=pr]")).to_be_visible()


def test_cmd_enter_in_the_review_summary_submits_a_comment_review(page: Page, server_url: str, gh_state: Path) -> None:
    open_pr(page, server_url)
    page.click("#side button[data-review-panel]")
    box = page.locator("textarea[data-review-text]")
    box.fill("Looks good")
    box.press("Meta+Enter")
    expect(page.locator("textarea[data-review-text]")).to_have_count(0)

    mutations = [json.loads(line) for line in (gh_state / "mutations.log").read_text().splitlines()]
    posted = [m["variables"] for m in mutations if m["variables"].get("event") == "COMMENT"]
    assert [p["body"] for p in posted] == ["Looks good"]


def test_a_title_shows_as_a_quick_tooltip_and_comes_back_after_the_pointer_leaves(page: Page, server_url: str) -> None:
    open_pr(page, server_url)
    page.click("#side button[data-review-panel]")
    button = page.locator("button[data-submit='REQUEST_CHANGES']")
    tip = page.locator(".tooltip")
    button.hover()
    expect(tip).to_have_class(re.compile(r"\bon\b"), timeout=600)
    expect(tip).to_have_text("Requesting changes needs a review summary")
    expect(button).not_to_have_attribute("title", re.compile(".*"))
    page.locator("#side h1").hover()
    expect(tip).not_to_have_class(re.compile(r"\bon\b"))
    expect(button).to_have_attribute("title", "Requesting changes needs a review summary")


def test_a_tooltip_inside_a_titled_row_hides_the_row_title_too(page: Page, server_url: str) -> None:
    open_pr(page, server_url)
    row = page.locator("#side .leaf[data-path='src/app.py']")
    pill = row.locator(".pill")
    pill.hover()
    expect(page.locator(".tooltip")).to_have_class(re.compile(r"\bon\b"), timeout=600)
    expect(page.locator(".tooltip")).to_have_text("modified")
    expect(row).not_to_have_attribute("title", re.compile(".*"))
    expect(pill).not_to_have_attribute("title", re.compile(".*"))
    page.locator("#side h1").hover()
    expect(row).to_have_attribute("title", "src/app.py")
    expect(pill).to_have_attribute("title", "modified")


def test_a_tooltip_in_the_docs_window_shows_above_the_dialog(page: Page, server_url: str) -> None:
    open_pr(page, server_url)
    page.click("button[data-docs-open]")
    page.hover("button[data-docs-close]")
    expect(page.locator("#docs .tooltip")).to_have_class(re.compile(r"\bon\b"), timeout=600)
    expect(page.locator("#docs .tooltip")).to_have_text("Close")


def test_a_tooltip_shows_on_an_element_reached_by_scrolling(page: Page, server_url: str) -> None:
    open_pr(page, server_url)
    chevron = page.locator(f"{APP} .fhead .chev")
    chevron.hover()
    expect(page.locator(".tooltip")).to_have_class(re.compile(r"\bon\b"), timeout=1000)


def test_a_click_dismisses_the_tooltip_even_when_the_click_redraws_the_element(page: Page, server_url: str) -> None:
    open_pr(page, server_url)
    check = page.locator("#side .leaf[data-path='src/app.py'] .check")
    tip = page.locator(".tooltip")
    check.hover()
    expect(tip).to_have_class(re.compile(r"\bon\b"), timeout=600)
    check.click()
    expect(page.locator("#side .leaf[data-path='src/app.py']")).to_have_class(re.compile(r"\breviewed\b"))
    page.wait_for_timeout(600)
    expect(tip).not_to_have_class(re.compile(r"\bon\b"))
    page.locator("#side h1").hover()
    check.hover()
    expect(tip).to_have_class(re.compile(r"\bon\b"), timeout=600)
