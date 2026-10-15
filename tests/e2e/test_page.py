import json
import re
from collections.abc import Callable
from pathlib import Path

import pytest
from playwright.sync_api import Page, Position, expect

from e2e.conftest import TITLE
from helpers import Files

AddPr = Callable[[Files, Files], str]

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


def test_a_binary_file_shows_its_size_instead_of_line_counts(page: Page, server_url: str, add_pr: AddPr) -> None:
    open_pr(page, server_url, add_pr({}, {"src/logo.png": b"\x89PNG\0" * 1000}))
    section = page.locator("section[data-file='src/logo.png']")
    section.scroll_into_view_if_needed()
    expect(section.locator(".fhead .counts")).to_have_text("4.9 KB")
    expect(section.locator(".code > .empty")).to_have_text("Binary file added · 4.9 KB")


def test_a_file_the_pull_request_empties_shows_its_removed_lines(page: Page, server_url: str, add_pr: AddPr) -> None:
    open_pr(page, server_url, add_pr({"notes.txt": "one\ntwo\n"}, {"notes.txt": ""}))
    section = page.locator("section[data-file='notes.txt']")
    section.scroll_into_view_if_needed()
    expect(section.locator("tr.del td.c")).to_have_text(["one", "two"])


def test_a_renamed_dataclass_field_shows_the_lines_up_to_its_class(page: Page, server_url: str, add_pr: AddPr) -> None:
    fields = (
        "    id: int\n    customer: str\n    amount: float\n    currency: str\n    created: str\n    shipped: bool\n"
    )
    models = "from dataclasses import dataclass\n\n\nTAX_RATE = 0.2\nSHIPPING = 5.0\nDISCOUNT = 0.1\n\n\n@dataclass\nclass Order:\n"
    old = {
        "src/models.py": f"{models}{fields}    old_total: float\n",
        "src/report.py": "def report(o):\n    return o.old_total\n",
    }
    new = {
        "src/models.py": f"{models}{fields}    new_total: float\n",
        "src/report.py": "def report(o):\n    return o.new_total\n",
    }
    open_pr(page, server_url, add_pr(old, new))
    section = page.locator("section[data-file='src/models.py']")
    section.scroll_into_view_if_needed()
    expect(section.locator("tr.add")).to_have_text(re.compile("new_total: float"))
    for line in ("@dataclass", "class Order:", "id: int", "shipped: bool"):
        expect(section.locator("tr.ctx", has_text=line)).to_be_visible()
    expect(section.locator("tr.fold")).to_have_text("⋯ 7 unchanged lines")


CONFIG_FIELDS = "".join(f"    field_{n}: int\n" for n in range(1, 13))
CONFIG_ARGS = "".join(f"    arg_{n}={n},\n" for n in range(1, 13))
CONFIG_STEPS = "".join(f"    step_{n}()\n" for n in range(1, 13))
SERVICE_CALLS = {name: "".join(f"        {name}{n}()\n" for n in range(1, 13)) for name in ("a", "b")}
JOBS_FIELDS = "".join(f"    j{n} = {n}\n" for n in range(1, 13))
NEAR_LINES = "".join(f"y{n} = {n}\n" for n in range(2, 20))
CONFIG_OLD = {
    "src/config.py": f"class Config:\n{CONFIG_FIELDS}\n\napp = make_app(\n{CONFIG_ARGS})\n\n\ndef run():\n{CONFIG_STEPS}    return 0\n",
    "src/service.py": (
        f"class Service:\n    def m1(self):\n{SERVICE_CALLS['a']}        return 0\n\n"
        f'    def m2(self):\n{SERVICE_CALLS["b"]}        return 0\n\n    name = "service"\n'
    ),
    "src/jobs.py": f"class Jobs:\n{JOBS_FIELDS}\n    def run(self, x):\n        return x\n",
    "src/near.py": f"x = 1\n{NEAR_LINES}def f(a):\n    b = 1\n    return a\n",
}
CONFIG_NEW = {
    "src/config.py": (
        f"class Config:\n{CONFIG_FIELDS}    extra: bool\n\n\napp = make_app(\n{CONFIG_ARGS}    extra=True,\n)\n\n\n"
        f"def run():\n{CONFIG_STEPS}    return 1\n"
    ),
    "src/service.py": (
        f"class Service:\n    def m1(self):\n{SERVICE_CALLS['a']}        return 1\n\n"
        f'    def m2(self):\n{SERVICE_CALLS["b"]}        return 1\n\n    name = "service"\n    extra = True\n'
    ),
    "src/jobs.py": f"class Jobs:\n{JOBS_FIELDS}\n    def run(self, x, y):\n        return x\n",
    "src/near.py": f"x = 10\n{NEAR_LINES}def f(a):\n    b = 1\n    return a + b\n",
}


@pytest.fixture(scope="module")
def config_pr(add_pr: AddPr) -> str:
    """Changes inside classes, functions and a call, for the inline folds, and lines holding "re" for the search."""
    return add_pr(CONFIG_OLD, CONFIG_NEW)


def test_a_change_shows_the_class_function_or_call_line_and_folds_the_lines_between(
    page: Page, server_url: str, config_pr: str
) -> None:
    open_pr(page, server_url, config_pr)
    section = page.locator("section[data-file='src/config.py']")
    section.scroll_into_view_if_needed()
    code = lambda line: section.locator("tr.ctx td.c", has_text=re.compile(rf"^\s*{re.escape(line)}"))  # noqa: E731
    expect(section.locator("tr.add")).to_have_count(3)
    for line in ("class Config:", "field_9: int", "app = make_app(", "arg_1=1,", "arg_9=9,", "def run():", "step_9()"):
        expect(code(line)).to_be_visible()
    for line in ("field_1: int", "arg_2=2,", "step_1()"):
        expect(code(line)).to_have_count(0)
    expect(section.locator("tr.fold")).to_have_count(0)
    chips = section.locator(".inline-fold")
    expect(chips).to_have_count(3)
    expect(chips.first).to_have_attribute("title", "Show 8 unchanged lines")

    chips.first.click()
    expect(code("field_1: int")).to_be_visible()
    expect(chips.first).to_have_class(re.compile(r"\bopen\b"))
    chips.first.click()
    expect(code("field_1: int")).to_have_count(0)


def test_a_change_shows_the_line_of_its_own_method_when_a_later_change_sits_in_the_class(
    page: Page, server_url: str, config_pr: str
) -> None:
    open_pr(page, server_url, config_pr)
    section = page.locator("section[data-file='src/service.py']")
    section.scroll_into_view_if_needed()
    code = lambda line: section.locator("tr.ctx td.c", has_text=re.compile(rf"^\s*{re.escape(line)}"))  # noqa: E731
    for line in ("class Service:", "def m1(self):", "def m2(self):"):
        expect(code(line)).to_be_visible()
    expect(code("a1()")).to_have_count(0)
    expect(section.locator(".inline-fold")).to_have_count(2)


def test_a_change_just_below_its_def_line_keeps_the_lines_next_to_it(
    page: Page, server_url: str, config_pr: str
) -> None:
    open_pr(page, server_url, config_pr)
    section = page.locator("section[data-file='src/near.py']")
    section.scroll_into_view_if_needed()
    for line in ("y18 = 18", "y19 = 19", "def f(a):", "b = 1"):
        expect(section.locator("tr.ctx td.c", has_text=re.compile(rf"^\s*{re.escape(line)}$"))).to_be_visible()
    expect(section.locator("tr.fold")).to_have_text("⋯ 12 unchanged lines")


def test_a_changed_def_line_shows_the_class_line_above_it(page: Page, server_url: str, config_pr: str) -> None:
    open_pr(page, server_url, config_pr)
    section = page.locator("section[data-file='src/jobs.py']")
    section.scroll_into_view_if_needed()
    expect(section.locator("tr.ctx td.c", has_text=re.compile(r"^class Jobs:"))).to_be_visible()
    expect(section.locator(".inline-fold")).to_have_count(1)


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


def test_an_opened_context_fold_keeps_its_row_and_folds_again(page: Page, server_url: str, config_pr: str) -> None:
    open_pr(page, server_url, config_pr)
    section = page.locator("section[data-file='src/near.py']")
    section.scroll_into_view_if_needed()
    y10 = section.locator("tr.ctx td.c", has_text=re.compile(r"^y10 = 10$"))
    expect(y10).to_have_count(0)
    section.locator("tr.fold").click()
    expect(y10).to_be_visible()
    opened = section.locator("tr.runhead[data-fold]")
    expect(opened).to_have_text("▾ 12 unchanged lines")
    opened.click()
    expect(y10).to_have_count(0)
    expect(section.locator("tr.fold")).to_have_text("⋯ 12 unchanged lines")


def test_a_noise_button_locks_the_fold_rows_of_its_kind(page: Page, server_url: str) -> None:
    open_pr(page, server_url)
    section = page.locator(APP)
    imports = section.locator("tr.add", has_text="import sys")
    page.click("#overview .chip[data-show='import']")
    expect(imports).to_be_visible()
    locked = section.locator("tr.runhead.locked", has_text="import")
    expect(locked).to_have_count(1)
    expect(section.locator("tr.runhead[data-fold]")).to_have_count(0)
    locked.click()
    page.click("#overview .chip[data-show='import']")
    expect(imports).to_have_count(0)
    expect(section.locator("tr.collapsed[data-fold]", has_text="import")).to_have_count(1)


def test_the_line_number_a_row_shows_starts_and_extends_a_comment(page: Page, server_url: str, add_pr: AddPr) -> None:
    open_pr(page, server_url, add_pr({"src/abc.py": "a = 1\nb = 2\nc = 3\n"}, {"src/abc.py": "a = 1\nb = 20\nc = 3\n"}))
    section = page.locator("section[data-file='src/abc.py']")
    hint = page.locator(".form.compose .hint")
    first = section.locator("tr.ctx", has_text="a = 1").locator("td.n.key")
    expect(first).to_have_text("1")
    first.click()
    expect(hint).to_contain_text("Comment on line 1 ")
    section.locator("tr.ctx", has_text="c = 3").locator("td.n.key").click(modifiers=["Shift"])
    expect(hint).to_contain_text("Comment on lines 1–3 ")
    removed = section.locator("tr.del").locator("td.n.key")
    expect(removed).to_have_text("2")
    removed.click()
    expect(hint).to_contain_text("Comment on line 2 of the old file")


def hit_row(page: Page) -> str:
    """The element id of the row of the search hit Enter goes on from."""
    return page.evaluate("rowId(BY_PATH[state.hit.path], state.hit.key)")


def test_cmd_f_focuses_the_search_and_enter_goes_to_the_first_hit(page: Page, server_url: str) -> None:
    open_pr(page, server_url)
    page.keyboard.press("Meta+f")
    expect(page.locator("#side .search")).to_be_focused()
    page.keyboard.type("hello")
    expect(page.locator("#searchmeta")).to_have_text("1 file · 1 line")
    expect(page.locator("#side .leaf[data-path='src/app.py'] .hits")).to_have_text("1")
    page.keyboard.press("Enter")
    expect(page.locator("#searchmeta")).to_have_text("1 file · 1 / 1")
    expect(page.locator(f"#{hit_row(page)}")).to_contain_text('print("hello")')


def test_a_match_across_highlighter_tokens_is_one_mark(page: Page, server_url: str) -> None:
    open_pr(page, server_url)
    page.fill("#side .search", 'print("hel')
    mark = page.locator(f"{APP} tr.add mark")
    expect(mark).to_have_count(1)
    expect(mark).to_have_text('print("hel')


def test_a_file_opened_from_the_tree_while_searching_starts_at_its_first_hit(
    page: Page, server_url: str, config_pr: str
) -> None:
    open_pr(page, server_url, config_pr)
    meta = page.locator("#searchmeta")
    page.fill("#side .search", "re")
    expect(meta).to_have_text("4 files · 9 lines")
    page.click("#side .leaf[data-path='src/service.py']")
    expect(meta).to_have_text("4 files · 6 / 9")
    expect(page.locator(f"section[data-file='src/service.py'] #{hit_row(page)}")).to_have_count(1)
    page.keyboard.press("Enter")
    expect(meta).to_have_text("4 files · 7 / 9")
    page.keyboard.press("ArrowDown")
    expect(meta).to_have_text("4 files · 7 / 9")
    page.keyboard.press("ArrowUp")
    expect(meta).to_have_text("4 files · 4 / 9")
    expect(page.locator(f"section[data-file='src/near.py'] #{hit_row(page)}")).to_have_count(1)
    page.keyboard.press("Enter")
    expect(meta).to_have_text("4 files · 5 / 9")
    page.keyboard.press("Shift+Enter")
    expect(meta).to_have_text("4 files · 4 / 9")
    page.keyboard.press("ArrowUp")
    page.keyboard.press("ArrowUp")
    expect(meta).to_have_text("4 files · 1 / 9")
    expect(page.locator("#side .leaf.cursor")).to_have_attribute("data-path", "src/config.py")
    page.click("#side .search")
    page.keyboard.press("Enter")
    expect(meta).to_have_text("4 files · 2 / 9")


def test_the_tree_cursor_passes_a_closed_section_and_enter_opens_it_at_its_first_hit(
    page: Page, server_url: str, config_pr: str
) -> None:
    open_pr(page, server_url, config_pr)
    meta = page.locator("#searchmeta")
    config = page.locator("section[data-file='src/config.py']")
    page.fill("#side .search", "re")
    page.click("#side .leaf[data-path='src/config.py'] .check")
    expect(config).to_have_class(re.compile(r"\bclosed\b"))
    expect(meta).to_have_text("4 files · 7 lines")
    page.click("#side .leaf[data-path='src/jobs.py']")
    expect(meta).to_have_text("4 files · 1 / 7")
    page.keyboard.press("ArrowUp")
    expect(page.locator("#side .leaf.cursor")).to_have_attribute("data-path", "src/config.py")
    expect(config).to_have_class(re.compile(r"\bclosed\b"))
    expect(meta).to_have_text("4 files · 1 / 7")
    page.keyboard.press("Enter")
    expect(config).not_to_have_class(re.compile(r"\bclosed\b"))
    expect(meta).to_have_text("4 files · 1 / 9")


def test_enter_in_the_tree_goes_on_when_the_hit_is_in_a_collapsed_folder(
    page: Page, server_url: str, add_pr: AddPr
) -> None:
    pr = add_pr({"lib/a.py": "a = 1\n", "src/b.py": "b = 1\n"}, {"lib/a.py": "a = 2\n", "src/b.py": "b = 2\n"})
    open_pr(page, server_url, pr)
    meta = page.locator("#searchmeta")
    page.fill("#side .search", "= 2")
    page.click("#side .dir[data-folder='lib']")
    page.click("#side .leaf[data-path='src/b.py']")
    expect(meta).to_have_text("2 files · 2 / 2")
    page.keyboard.press("Enter")
    expect(meta).to_have_text("2 files · 1 / 2")
    expect(page.locator("#side .leaf[data-path='lib/a.py']")).to_have_count(0)
    page.keyboard.press("Enter")
    expect(meta).to_have_text("2 files · 2 / 2")


def test_search_keeps_every_file_in_the_tree(page: Page, server_url: str) -> None:
    open_pr(page, server_url)
    leaves = page.locator("#side .leaf")
    count = leaves.count()
    page.fill("#side .search", "hello")
    expect(page.locator("#searchmeta")).to_have_text("1 file · 1 line")
    expect(leaves).to_have_count(count)
    expect(page.locator("#main section[data-file]")).to_have_count(1)


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
    section.locator("tr.add", has_text='print("hello")').locator("td.n.key").click()
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
    hello = page.locator(APP).locator("tr.add", has_text='print("hello")').locator("td.n.key")
    hello.click()
    box = page.locator("textarea[data-compose-text]")
    box.fill("Use a logger here")
    box.press("Escape")
    expect(box).to_have_value("Use a logger here")
    expect(box).not_to_be_focused()

    page.locator("section[data-file='README.md'] tr.add td.n.key").first.click()
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
    hello = section.locator("tr.add", has_text='print("hello")').locator("td.n.key")
    main = section.locator("tr.add", has_text="print(new_name(2))").locator("td.n.key")
    box = page.locator("textarea[data-compose-text]")
    hello.click()
    box.fill("First")
    main.click()
    expect(box).to_have_value("")
    box.fill("Second")
    hello.click(modifiers=["Shift"])
    expect(page.locator(".form.compose .hint")).to_contain_text("Comment on lines 10–11")
    expect(box).to_have_value("First\n\nSecond")
    page.locator("section[data-file='README.md'] tr.add td.n.key").first.click()
    expect(box).to_have_value("")
    hello.click()
    expect(box).to_have_value("First\n\nSecond")
    page.click("button[data-compose-cancel]")


def test_dragging_from_a_line_number_opens_a_comment_on_the_lines_in_between(page: Page, server_url: str) -> None:
    open_pr(page, server_url)
    section = page.locator(APP)
    hello = section.locator("tr.add", has_text='print("hello")').locator("td.n.key")
    main = section.locator("tr.add", has_text="print(new_name(2))").locator("td.n.key")
    hello.scroll_into_view_if_needed()
    start, end = hello.bounding_box(), main.bounding_box()
    assert start and end
    page.mouse.move(start["x"] + 5, start["y"] + 5)
    page.mouse.down()
    page.mouse.move(end["x"] + 5, end["y"] + 5, steps=5)
    expect(section.locator("tr.sel")).to_have_count(2)
    page.mouse.up()
    expect(page.locator(".form.compose .hint")).to_contain_text("Comment on lines 10–11")
    expect(page.locator("textarea[data-compose-text]")).to_be_focused()


def test_dragging_across_chunks_highlights_the_rows_of_both(page: Page, server_url: str, add_pr: AddPr) -> None:
    open_pr(page, server_url, add_pr({}, {"src/long.py": "".join(f"x{n} = {n}\n" for n in range(1, 151))}))
    section = page.locator("section[data-file='src/long.py']")
    start = section.locator("tr[id$='-L98'] td.n.key")
    end = section.locator("tr[id$='-L103'] td.n.key")
    start.scroll_into_view_if_needed()
    a, b = start.bounding_box(), end.bounding_box()
    assert a and b
    page.mouse.move(a["x"] + 5, a["y"] + 5)
    page.mouse.down()
    page.mouse.move(b["x"] + 5, b["y"] + 5)
    expect(section.locator("tr.sel")).to_have_count(6)
    page.mouse.up()
    expect(page.locator(".form.compose .hint")).to_contain_text("Comment on lines 98–103")


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


def test_request_changes_shows_its_tooltip_only_while_it_waits_for_a_summary(page: Page, server_url: str) -> None:
    open_pr(page, server_url)
    page.click("#side button[data-review-panel]")
    box = page.locator("textarea[data-review-text]")
    button = page.locator("button[data-submit='REQUEST_CHANGES']")
    box.fill("Please add a test")
    button.hover()
    page.wait_for_timeout(600)
    expect(page.locator(".tooltip.on")).to_have_count(0)
    box.fill("")
    page.locator("#side h1").hover()
    button.hover()
    expect(page.locator(".tooltip")).to_have_class(re.compile(r"\bon\b"), timeout=600)
    box.fill("Please add a test")
    page.locator("#side h1").hover()
    expect(button).not_to_have_attribute("title", re.compile("."))
    page.click("#side button[data-review-panel]")
    page.click("#side button[data-review-panel]")
    expect(button).not_to_have_attribute("title", re.compile("."))


def test_a_tooltip_inside_a_titled_element_hides_the_outer_title_too(page: Page, server_url: str) -> None:
    open_pr(page, server_url)
    page.click("#side button[data-review-panel]")
    actions = page.locator("#side .actions")
    actions.evaluate("e => e.title = 'Review actions'")
    page.locator("button[data-submit='REQUEST_CHANGES']").hover()
    expect(page.locator(".tooltip")).to_have_class(re.compile(r"\bon\b"), timeout=600)
    expect(page.locator(".tooltip")).to_have_text("Requesting changes needs a review summary")
    expect(actions).not_to_have_attribute("title", re.compile(".*"))
    page.locator("#side h1").hover()
    expect(actions).to_have_attribute("title", "Review actions")


def test_moving_to_another_titled_element_waits_for_its_own_tooltip(page: Page, server_url: str) -> None:
    open_pr(page, server_url)
    badge = page.locator(f"{APP} .fhead .badge")
    badge.scroll_into_view_if_needed()
    tip = page.locator(".tooltip")
    page.locator("#side input.search").hover()
    expect(tip).to_have_class(re.compile(r"\bon\b"), timeout=1000)
    badge.hover()
    assert "on" not in (tip.get_attribute("class") or "").split()
    expect(tip).to_have_class(re.compile(r"\bon\b"), timeout=1000)
    expect(tip).to_have_text("modified")


def test_a_tree_row_shows_a_tooltip_only_while_its_name_is_cut_off(page: Page, server_url: str) -> None:
    open_pr(page, server_url)
    row = page.locator("#side .leaf[data-path='src/app.py']")
    spots: tuple[Position | None, ...] = ({"x": 4, "y": 4}, None)
    for spot in spots:
        row.hover(position=spot)
        page.wait_for_timeout(600)
        expect(page.locator(".tooltip.on")).to_have_count(0)
    page.locator("#side h1").hover()
    page.add_style_tag(content="#side .leaf[data-path='src/app.py'] .nm { max-width: 2ch; }")
    row.hover(position={"x": 4, "y": 4})
    expect(page.locator(".tooltip")).to_have_class(re.compile(r"\bon\b"), timeout=600)
    expect(page.locator(".tooltip")).to_have_text("app.py")


def test_a_folder_row_shows_a_tooltip_only_while_its_name_is_cut_off(page: Page, server_url: str) -> None:
    open_pr(page, server_url)
    row = page.locator("#side .dir[data-folder='src']")
    row.hover(position={"x": 4, "y": 4})
    page.wait_for_timeout(600)
    expect(page.locator(".tooltip.on")).to_have_count(0)
    page.locator("#side h1").hover()
    page.add_style_tag(content="#side .dir[data-folder='src'] .nm { max-width: 1ch; }")
    row.hover(position={"x": 4, "y": 4})
    expect(page.locator(".tooltip")).to_have_class(re.compile(r"\bon\b"), timeout=600)
    expect(page.locator(".tooltip")).to_have_text("src/")


def test_a_tooltip_in_the_docs_window_shows_above_the_dialog(page: Page, server_url: str) -> None:
    open_pr(page, server_url)
    page.click("button[data-docs-open]")
    heading = page.locator("#docs h1")
    heading.evaluate("e => e.title = 'What differ does'")
    heading.hover()
    expect(page.locator("#docs .tooltip")).to_have_class(re.compile(r"\bon\b"), timeout=600)
    expect(page.locator("#docs .tooltip")).to_have_text("What differ does")


def test_a_tooltip_shows_on_an_element_reached_by_scrolling(page: Page, server_url: str) -> None:
    open_pr(page, server_url)
    page.locator(f"{APP} .fhead .badge").hover()
    expect(page.locator(".tooltip")).to_have_class(re.compile(r"\bon\b"), timeout=1000)


def test_a_click_dismisses_the_tooltip_even_when_the_click_redraws_the_element(page: Page, server_url: str) -> None:
    open_pr(page, server_url)
    badge = page.locator(f"{APP} .fhead .badge")
    tip = page.locator(".tooltip")
    badge.hover()
    expect(tip).to_have_class(re.compile(r"\bon\b"), timeout=1000)
    badge.click()
    expect(page.locator(APP)).to_have_class(re.compile(r"\bclosed\b"))
    page.wait_for_timeout(600)
    expect(tip).not_to_have_class(re.compile(r"\bon\b"))
    page.locator("#side h1").hover()
    badge.hover()
    expect(tip).to_have_class(re.compile(r"\bon\b"), timeout=1000)


def test_focusing_the_start_page_search_selects_its_text(page: Page, server_url: str) -> None:
    page.goto(server_url + "/home")
    field = page.locator("input[name=pr]")
    field.fill("acme/widgets#2")
    page.locator("h1").click()
    field.click()
    assert field.evaluate("f => [f.selectionStart, f.selectionEnd]") == [0, len("acme/widgets#2")]


def test_opening_a_pull_request_from_the_shell_empties_the_start_page_search(page: Page, server_url: str) -> None:
    page.goto(server_url + "/")
    home = page.frame_locator("#panes iframe.on")
    home.locator("input[name=pr]").fill("acme/widgets#1")
    home.locator("input[name=pr]").press("Enter")
    expect(page.locator("#tabs .tab:not(.home)")).to_have_count(1)
    page.click("#tabs .tab.home")
    expect(page.frame_locator("#panes iframe.on").locator("input[name=pr]")).to_have_value("")


def test_an_approval_of_the_head_commit_names_itself_and_blocks_another(
    page: Page, server_url: str, gh_state: Path
) -> None:
    comments_file = gh_state / "comments.json"
    saved = comments_file.read_text()
    state = json.loads(saved)
    approval = {**state["reviews"][0], "id": "R2", "author": {"login": "tester"}, "body": ""}
    state["reviews"].append({**approval, "commit": {"oid": state["head"]}})
    comments_file.write_text(json.dumps(state))
    try:
        open_pr(page, server_url)
        review = page.locator("#side button[data-review-panel]")
        expect(review).to_have_text("Approved")
        review.click()
        expect(page.locator("button[data-submit='APPROVE']")).to_be_disabled()
        page.locator("textarea[data-review-text]").fill("Found a problem")
        expect(page.locator("button[data-submit='REQUEST_CHANGES']")).to_be_enabled()

        state["reviews"][-1] = {**approval, "commit": {"oid": "older"}}
        comments_file.write_text(json.dumps(state))
        open_pr(page, server_url)
        expect(review).to_have_text("Review")
        review.click()
        expect(page.locator("button[data-submit='APPROVE']")).to_be_enabled()
    finally:
        comments_file.write_text(saved)


def test_the_tree_cursor_stays_on_a_file_near_the_end_that_cannot_scroll_to_the_top(
    page: Page, server_url: str, config_pr: str
) -> None:
    page.set_viewport_size({"width": 1280, "height": 720})
    open_pr(page, server_url, config_pr)
    page.click("section[data-file='src/near.py'] button[data-review]")
    page.click("#side .leaf[data-path='src/jobs.py']")
    page.keyboard.press("ArrowDown")
    near = page.locator("#side .leaf[data-path='src/near.py']")
    page.evaluate("new Promise((done) => requestAnimationFrame(() => requestAnimationFrame(done)))")
    expect(near).to_have_class(re.compile(r"\bcursor\b"))
    expect(near).to_have_class(re.compile(r"\bactive\b"))
    page.keyboard.press("Shift")
    page.mouse.move(100, 300)
    page.mouse.wheel(0, 200)
    page.mouse.move(640, 360)
    page.mouse.wheel(0, 300)
    page.click("section[data-file='src/jobs.py'] button[data-review]")
    page.evaluate("new Promise((done) => requestAnimationFrame(() => requestAnimationFrame(done)))")
    expect(near).to_have_class(re.compile(r"\bcursor\b"))
    expect(near).to_have_class(re.compile(r"\bactive\b"))
    page.mouse.move(640, 360)
    page.mouse.wheel(0, -2000)
    expect(near).not_to_have_class(re.compile(r"\bactive\b"))


@pytest.mark.parametrize("path", ["src/config.py", "package-lock.json"])
def test_opening_an_address_that_names_a_reviewed_file_leaves_it_closed(
    page: Page, server_url: str, config_pr: str, path: str
) -> None:
    address = {"src/config.py": config_pr, "package-lock.json": PR}[path] + f"#{path}"
    open_pr(page, server_url, address)
    page.click(f"section[data-file='{path}'] button[data-review]")
    page.goto("about:blank")
    open_pr(page, server_url, address)
    expect(page.locator(f"section[data-file='{path}']")).to_have_class(re.compile(r"\bclosed\b"))


def test_while_searching_the_tree_cursor_stays_on_a_hit_near_the_end(
    page: Page, server_url: str, config_pr: str
) -> None:
    page.set_viewport_size({"width": 1280, "height": 720})
    open_pr(page, server_url, config_pr)
    page.keyboard.press("Meta+f")
    page.keyboard.type("a + b")
    page.click("#side .leaf[data-path='src/jobs.py']")
    page.keyboard.press("ArrowDown")
    near = page.locator("#side .leaf[data-path='src/near.py']")
    page.evaluate("new Promise((done) => requestAnimationFrame(() => requestAnimationFrame(done)))")
    expect(near).to_have_class(re.compile(r"\bcursor\b"))
    expect(near).to_have_class(re.compile(r"\bactive\b"))


def in_view(page: Page, selector: str) -> bool:
    """Whether the element lies inside the main pane's viewport."""
    return page.evaluate(
        """(selector) => {
          const r = document.querySelector(selector).getBoundingClientRect();
          const m = document.getElementById('main').getBoundingClientRect();
          return r.top >= m.top && r.bottom <= m.bottom;
        }""",
        selector,
    )


@pytest.mark.parametrize(("link", "anchor"), [(":12", "tr[id$='-L12']"), ("", ".fhead")])
def test_an_address_near_the_end_stays_in_place_when_the_comments_load(
    page: Page, server_url: str, add_pr: AddPr, link: str, anchor: str
) -> None:
    page.set_viewport_size({"width": 1400, "height": 900})
    old = {
        "src/a.py": "".join(f"a{n} = {n}\n" for n in range(1, 6)),
        "src/b.py": "".join(f"b{n} = {n}\n" for n in range(1, 13)),
    }
    new = {
        "src/a.py": "".join(f"a{n} = {n + 1}\n" for n in range(1, 6)),
        "src/b.py": "".join(f"b{n} = {n}\n" for n in range(1, 12)) + "b12 = 0\n",
    }
    open_pr(page, server_url, add_pr(old, new) + f"#src/b.py{link}")
    selector = f"section[data-file='src/b.py'] {anchor}"
    assert in_view(page, selector)
    expect(page.locator("#side .leaf[data-path='src/b.py']")).to_have_class(re.compile(r"\bactive\b"))
    if link:
        expect(page.locator(selector)).to_have_class(re.compile(r"\btarget\b"))
