import json
import re
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import quote

import pytest
from playwright.sync_api import APIResponse, Locator, Page, Position, Route, expect

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
    expect(page.locator("#side .leaf")).to_have_count(1)
    expect(page.locator("#overview h1")).to_have_text(TITLE)


def test_the_overview_links_the_author_and_the_commits_to_github(page: Page, server_url: str) -> None:
    open_pr(page, server_url)
    meta = page.locator("#overview .meta")
    expect(meta.get_by_role("link", name="ann")).to_have_attribute("href", "https://github.com/ann")
    head = page.evaluate("PR.head")
    expect(meta.locator(".mono a").last).to_have_attribute("href", f"https://github.com/acme/widgets/commit/{head}")
    assert 'href="https://github.com/apps/dependabot"' in page.evaluate("PR.author = 'app/dependabot', overviewHtml()")


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
    expect(page.locator("#side .leaf")).to_have_count(4)


def test_each_kind_button_lists_the_files_of_its_kind_only(page: Page, server_url: str) -> None:
    open_pr(page, server_url)
    page.click("#side .list .notice a[data-noiseonly]")
    paths = page.locator("#side .leaf")
    expect(paths.first).to_be_visible()
    assert sorted(paths.evaluate_all("ls => ls.map(l => l.dataset.path)")) == [
        "notes/notes.txt",
        "src/app.py",
        "src/cli.py",
    ]
    page.click("#side [data-filter=docs]")
    expect(paths).to_have_count(1)
    expect(paths).to_have_attribute("data-path", "README.md")
    expect(page.locator("section[data-file]")).to_have_count(1)
    page.click("#side [data-filter=test]")
    expect(page.locator("#side .list .notice")).to_have_text("No test files · show all")


def test_a_binary_file_shows_its_size_instead_of_line_counts(page: Page, server_url: str, add_pr: AddPr) -> None:
    open_pr(page, server_url, add_pr({}, {"src/logo.png": b"\x89PNG\0" * 1000}))
    section = page.locator("section[data-file='src/logo.png']")
    section.scroll_into_view_if_needed()
    expect(section.locator(".fhead .counts")).to_have_text("4.9 KB")
    expect(section.locator(".code > .empty")).to_have_text("Binary file added · 4.9 KB")


def test_an_image_of_a_private_repository_in_the_description_loads_through_the_server(
    page: Page, server_url: str, add_pr: AddPr, gh_state: Path
) -> None:
    pixel = (
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89"
        b"\x00\x00\x00\rIDATx\x9cc\xf8\xcf\xc0\xf0\x1f\x00\x05\x00\x01\xff\x89\x99=\x1d\x00\x00\x00\x00IEND\xaeB`\x82"
    )
    path = add_pr({}, {"docs/shot.png": pixel})
    meta_file = gh_state / f"pr{path.rsplit('/', 1)[1]}.json"
    meta = json.loads(meta_file.read_text())
    src = f"https://github.com/acme/widgets/blob/{meta['headRefOid']}/docs/shot.png?raw=true"
    body = (
        f"Before:\n![The new page]({src})\n\n"
        f"![see https://x/x/onerror=document.title='pwned'// z]({src.replace('shot', 'gone')})\n\n"
        "![A tracker](https://example.com/pixel.png)\n\n"
        "see https://e.com/![x](https:x/onmouseover=document.title='pwned'//) and "
        f"[t](https:![x](https:x/onmouseover=document.title='pwned'//)) and [t](https://a\ue0000\ue000)![y]({src})"
    )
    meta_file.write_text(json.dumps({**meta, "body": body}))
    open_pr(page, server_url, path)
    image = page.locator(".prbody img[alt='The new page']")
    expect(image).to_have_attribute("src", f"/image?src={quote(src, safe='')}")
    page.wait_for_function("document.querySelector('.prbody img').complete")
    assert image.evaluate("img => img.naturalWidth") == 1
    expect(page.locator(".prbody img")).to_have_count(3)
    expect(page.locator(".prbody [onerror], .prbody [onmouseover]")).to_have_count(0)
    expect(page.locator(".prbody a[href='https://example.com/pixel.png']")).to_have_text("A tracker")


def test_a_click_on_an_image_opens_it_in_the_shell_and_the_arrows_step_through_the_images_of_the_description(
    page: Page, server_url: str, add_pr: AddPr, gh_state: Path
) -> None:
    path = add_pr({}, {"docs/a.txt": "a\n"})
    number = path.rsplit("/", 1)[1]
    meta_file = gh_state / f"pr{number}.json"
    meta = json.loads(meta_file.read_text())
    shot = f"https://github.com/acme/widgets/blob/{meta['headRefOid']}/docs/{{}}.png?raw=true"
    body = "\n\n".join(
        [
            f"[![one]({shot.format('one')})]({shot.format('one')})",
            f"![two]({shot.format('two')})",
            f"[![badge]({shot.format('badge')})](https://example.com/ci)",
            f"![three]({shot.format('three')})",
        ]
    )
    meta_file.write_text(json.dumps({**meta, "body": body}))
    page.goto(server_url + "/")
    page.evaluate("window.opened = []; window.open = (url) => { opened.push(url); }; 0")
    home = page.frame_locator("#panes iframe.on")
    home.locator("input[name=pr]").fill(f"acme/widgets#{number}")
    home.locator("input[name=pr]").press("Enter")
    pane = page.locator("#panes iframe.on")
    expect(pane.content_frame.locator(".prbody img[alt='one']")).to_be_visible(timeout=30000)
    frame = pane.element_handle().content_frame()
    assert frame
    frame.evaluate(
        "window.cancelled = [];"
        "window.addEventListener('keydown', (e) => {"
        " if (e.key === 'Escape') setTimeout(() => cancelled.push(e.defaultPrevented)); }, true)"
    )
    viewer = frame.locator("#viewer")
    shown = viewer.locator("img")
    # GitHub wraps an image in a link to its own file, which the viewer takes over.
    frame.click(".prbody img[alt='one']")
    expect(shown).to_have_attribute("alt", "one")
    page.keyboard.press("Escape")
    expect(viewer).to_be_hidden()
    frame.wait_for_function("cancelled.length === 1")
    assert frame.evaluate("cancelled") == [True]
    frame.click(".prbody img[alt='two']")
    expect(shown).to_have_attribute("alt", "two")
    page.keyboard.press("ArrowRight")
    expect(shown).to_have_attribute("alt", "three")
    page.keyboard.press("ArrowRight")
    expect(shown).to_have_attribute("alt", "one")
    page.keyboard.press("ArrowLeft")
    expect(shown).to_have_attribute("alt", "three")
    viewer.click(position={"x": 4, "y": 4})
    expect(viewer).to_be_hidden()
    assert page.evaluate("opened") == []
    # An image in a link to another page follows the link.
    frame.click(".prbody img[alt='badge']")
    expect(viewer).to_be_hidden()
    page.wait_for_function("opened.length === 1")
    assert page.evaluate("opened") == ["https://example.com/ci"]


def test_the_arrows_step_through_the_images_of_one_thread_or_of_the_overview(
    page: Page, server_url: str, gh_state: Path
) -> None:
    comments_file = gh_state / "comments.json"
    with kept_comments(gh_state):
        state = json.loads(comments_file.read_text())
        shot = f"https://github.com/acme/widgets/blob/{state['head']}/docs/{{}}.png?raw=true"
        thread = next(t for t in state["threads"] if t["id"] == "T1")
        thread["comments"]["nodes"][0]["body"] = f"![a]({shot.format('a')})\n\n![b]({shot.format('b')})"
        next(c for c in state["comments"] if c["id"] == "K1")["body"] = f"![c]({shot.format('c')})"
        comments_file.write_text(json.dumps(state))
        open_pr(page, server_url)
    viewer = page.locator("#viewer")
    shown = viewer.locator("img")
    page.click(".thread .cbody img[alt='a']")
    expect(shown).to_have_attribute("alt", "a")
    page.keyboard.press("ArrowRight")
    expect(shown).to_have_attribute("alt", "b")
    page.keyboard.press("ArrowRight")
    expect(shown).to_have_attribute("alt", "a")
    page.keyboard.press("Escape")
    page.click("#overview .cbody img[alt='c']")
    expect(shown).to_have_attribute("alt", "c")
    page.keyboard.press("ArrowRight")
    expect(shown).to_have_attribute("alt", "c")


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
    for line in ("@dataclass", "class Order:", "created: str", "shipped: bool"):
        expect(section.locator("tr.ctx", has_text=line)).to_be_visible()
    expect(section.locator("tr.ctx", has_text="id: int")).to_have_count(0)
    expect(section.locator(".inline-fold")).to_have_attribute("title", "Show 4 unchanged lines")
    expect(section.locator("tr.fold")).to_have_text("⋯ 8 unchanged lines")


CONFIG_FIELDS = "".join(f"    field_{n}: int\n" for n in range(1, 13))
CONFIG_ARGS = "".join(f"    arg_{n}={n},\n" for n in range(1, 13))
CONFIG_STEPS = "".join(f"    step_{n}()\n" for n in range(1, 13))
SERVICE_CALLS = {name: "".join(f"        {name}{n}()\n" for n in range(1, 13)) for name in ("a", "b")}
JOBS_FIELDS = "".join(f"    j{n} = {n}\n" for n in range(1, 13))
NEAR_LINES = "".join(f"y{n} = {n}\n" for n in range(2, 20))
DEEP_STEPS = "".join(f"    pre_{n}()\n" for n in range(1, 13))
DEEP_ARGS = "".join(f"            arg_{n}={n},\n" for n in range(1, 13))
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
DEEP_OLD = {
    "src/deep.py": f"def main():\n{DEEP_STEPS}    try:\n        r = upload(\n{DEEP_ARGS}        )\n    except Exception:\n        raise\n"
}
DEEP_NEW = {
    "src/deep.py": (
        f"def main():\n{DEEP_STEPS}    try:\n        r = upload(\n{DEEP_ARGS}            extra=True,\n        )\n"
        "    except Exception:\n        raise\n"
    )
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
    for line in ("class Config:", "field_11: int", "app = make_app(", "arg_11=11,", "def run():", "step_11()"):
        expect(code(line)).to_be_visible()
    for line in ("field_1: int", "field_10: int", "arg_1=1,", "arg_10=10,", "step_1()", "step_10()"):
        expect(code(line)).to_have_count(0)
    expect(section.locator("tr.fold")).to_have_count(0)
    chips = section.locator(".inline-fold")
    expect(chips).to_have_count(3)
    expect(chips.first).to_have_attribute("title", "Show 10 unchanged lines")

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


def test_a_change_in_a_call_inside_a_function_shows_the_call_line_and_folds_the_lines_above_it(
    page: Page, server_url: str, add_pr: AddPr
) -> None:
    open_pr(page, server_url, add_pr(DEEP_OLD, DEEP_NEW))
    section = page.locator("section[data-file='src/deep.py']")
    section.scroll_into_view_if_needed()
    code = lambda line: section.locator("tr.ctx td.c", has_text=re.compile(rf"^\s*{re.escape(line)}"))  # noqa: E731
    for line in ("r = upload(", "arg_11=11,", "arg_12=12,"):
        expect(code(line)).to_be_visible()
    for line in ("def main():", "pre_12()", "try:", "arg_1=1,", "arg_10=10,"):
        expect(code(line)).to_have_count(0)
    expect(section.locator(".inline-fold")).to_have_count(1)
    expect(section.locator(".inline-fold")).to_have_attribute("title", "Show 10 unchanged lines")
    expect(section.locator("tr.fold")).to_have_text("⋯ 14 unchanged lines · main")


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


def test_folds_with_nothing_between_them_join_into_one_fold_that_opens_and_closes_as_one(
    page: Page, server_url: str, add_pr: AddPr
) -> None:
    helper = "".join(f"    h{n} = {n}\n" for n in range(1, 21))
    head = f'"""Date helpers.\n\nMore about them.\n"""\n\nimport os\nimport sys\n\n\ndef helper():\n{helper}\n\ndef target():\n'
    open_pr(page, server_url, add_pr({"dates.py": head + "    return 0\n"}, {"dates.py": head + "    return 1\n"}))
    section = page.locator("section[data-file='dates.py']")
    section.scroll_into_view_if_needed()
    code = lambda line: section.locator("tr.ctx td.c", has_text=re.compile(rf"^\s*{re.escape(line)}$"))  # noqa: E731
    expect(section.locator("tr.collapsed")).to_have_count(0)
    expect(section.locator("tr.fold")).to_have_count(1)
    expect(code("def target():")).to_be_visible()
    section.locator("tr.fold").click()
    for line in ('"""Date helpers.', "import os", "def helper():", "h10 = 10"):
        expect(code(line)).to_be_visible()
    expect(section.locator("tr.fold")).to_have_count(0)
    expect(section.locator("tr.collapsed")).to_have_count(0)
    opened = section.locator("tr.runhead[data-fold]")
    expect(opened).to_have_count(1)
    opened.click()
    expect(section.locator("tr.fold")).to_have_count(1)
    expect(code("import os")).to_have_count(0)


def test_a_comment_inside_an_opened_merged_fold_keeps_the_fold_open_as_one(
    page: Page, server_url: str, add_pr: AddPr
) -> None:
    helper = "".join(f"    h{n} = {n}\n" for n in range(1, 21))
    head = f'"""Date helpers.\n\nMore about them.\n"""\n\nimport os\nimport sys\n\n\ndef helper():\n{helper}\n\ndef target():\n'
    open_pr(page, server_url, add_pr({"dates.py": head + "    return 0\n"}, {"dates.py": head + "    return 1\n"}))
    section = page.locator("section[data-file='dates.py']")
    section.scroll_into_view_if_needed()

    def code(line: str) -> Locator:
        return section.locator("tr.ctx", has=page.locator("td.c", has_text=re.compile(rf"^\s*{re.escape(line)}$")))

    section.locator("tr.fold").click()
    code("import os").locator("td.n.key").click()
    expect(page.locator(".form.compose .hint")).to_contain_text("Comment on line 6 ")
    for line in ('"""Date helpers.', "import os", "h10 = 10", "h19 = 19"):
        expect(code(line)).to_be_visible()
    expect(section.locator("tr.runhead[data-fold]")).to_have_count(1)
    expect(section.locator("tr.fold, tr.collapsed")).to_have_count(0)
    page.keyboard.press("Escape")
    code("h20 = 20").locator("td.n.key").click()
    expect(page.locator(".form.compose .hint")).to_contain_text("Comment on line 30 ")
    expect(section.locator("tr.runhead[data-fold]")).to_have_count(1)
    expect(section.locator("tr.fold")).to_have_count(0)
    expect(code("h10 = 10")).to_be_visible()


def test_an_opened_merged_fold_stays_open_while_comments_start_and_end_around_it(
    page: Page, server_url: str, add_pr: AddPr
) -> None:
    def body(x: str, last: int) -> str:
        above = "".join(f"    a{n} = {n}\n" for n in range(1, 7))
        below = "".join(f"    b{n} = {n}\n" for n in range(1, 16))
        return f"def target():\n{above}    x ={x}0\n{below}    return {last}\n"

    open_pr(page, server_url, add_pr({"t.py": body(" ", 0)}, {"t.py": body("  ", 1)}))
    section = page.locator("section[data-file='t.py']")
    section.scroll_into_view_if_needed()
    noise = section.locator("tr.add td.c", has_text="x =  0")
    section.locator("tr.ctx", has_text="b12 = 12").locator("td.n.key").click()
    expect(page.locator(".form.compose .hint")).to_contain_text("Comment on line 20 ")
    section.locator("tr.fold").click()
    expect(noise).to_be_visible()
    section.locator("[data-compose-cancel]").click()
    expect(page.locator(".form.compose")).to_have_count(0)
    expect(noise).to_be_visible()
    expect(section.locator("tr.fold, tr.collapsed")).to_have_count(0)
    section.locator("tr.ctx", has_text="def target():").locator("td.n.key").click()
    expect(page.locator(".form.compose .hint")).to_contain_text("Comment on line 1 ")
    expect(noise).to_be_visible()
    expect(section.locator("tr.fold, tr.collapsed")).to_have_count(0)


def test_grouped_noise_runs_open_as_one_fold_with_the_lines_between_them(
    page: Page, server_url: str, add_pr: AddPr
) -> None:
    old = "a = 0\nb = 0\nc = 0\nd = 0\ne = 0\nf = 0\ng = 0\nh = 0\n"
    new = "a = 1\nb = 0\nc =  0\nd = 0\ne = 0\nf =  0\ng = 0\nh = 1\n"
    open_pr(page, server_url, add_pr({"ws.py": old}, {"ws.py": new}))
    section = page.locator("section[data-file='ws.py']")
    section.scroll_into_view_if_needed()
    group = section.locator("tr.collapsed[data-fold]")
    expect(group).to_have_count(1)
    expect(group).to_contain_text("in 2 places")
    expect(section.locator("tr.ctx td.c", has_text="d = 0")).to_have_count(0)
    group.click()
    opened = section.locator("tr.runhead[data-fold]")
    expect(opened).to_have_count(1)
    expect(opened).to_contain_text("in 2 places")
    expect(section.locator("tr.collapsed")).to_have_count(0)
    expect(section.locator("tr.ctx td.c", has_text="d = 0")).to_be_visible()
    expect(section.locator("tr.add td.c", has_text="f =  0")).to_be_visible()
    opened.click()
    expect(section.locator("tr.collapsed[data-fold]")).to_have_count(1)
    expect(section.locator("tr.ctx td.c", has_text="d = 0")).to_have_count(0)
    section.locator("tr.collapsed[data-fold]").click()
    section.locator("tr.ctx", has_text="d = 0").locator("td.n.key").click()
    expect(page.locator(".form.compose .hint")).to_contain_text("Comment on line 4 ")
    expect(section.locator("tr.add td.c", has_text="c =  0")).to_be_visible()
    expect(section.locator("tr.add td.c", has_text="f =  0")).to_be_visible()
    expect(section.locator("tr.runhead[data-fold]")).to_contain_text("in 2 places")
    expect(section.locator("tr.collapsed")).to_have_count(0)


def test_a_fold_next_to_an_opened_merged_fold_still_closes(page: Page, server_url: str, add_pr: AddPr) -> None:
    old = "".join(f"{c} = 0\n" for c in "abcdefghi")
    new = "a = 1\nb = 0\nc =  0\nd = 0\ne =  0\nf = 0\ng =  0\nh = 0\ni = 1\n"
    open_pr(page, server_url, add_pr({"ws.py": old}, {"ws.py": new}))
    section = page.locator("section[data-file='ws.py']")
    section.scroll_into_view_if_needed()
    run_g = section.locator("tr.add td.c", has_text="g =  0")
    section.locator("tr.collapsed[data-fold]").click()
    section.locator("tr.ctx", has_text="f = 0").locator("td.n.key").click()
    expect(page.locator(".form.compose .hint")).to_contain_text("Comment on line 6 ")
    section.locator("tr.runhead[data-fold]").click()
    for _ in range(2):
        section.locator("tr.collapsed[data-fold]").last.click()
    expect(section.locator("tr.collapsed")).to_have_count(0)
    section.locator("[data-compose-cancel]").click()
    expect(page.locator(".form.compose")).to_have_count(0)
    expect(run_g).to_be_visible()
    section.locator("tr.runhead[data-fold]").last.click()
    expect(run_g).to_have_count(0)
    expect(section.locator("tr.add td.c", has_text="e =  0")).to_be_visible()


def test_a_comment_ending_next_to_an_opened_fold_opens_no_more_lines(
    page: Page, server_url: str, add_pr: AddPr
) -> None:
    def body(x: str, last: int) -> str:
        above = "".join(f"    a{n} = {n}\n" for n in range(1, 13))
        below = "".join(f"    b{n} = {n}\n" for n in range(1, 40))
        return f"def target():\n    x ={x}0\n{above}    p = 0\n{below}    return {last}\n"

    open_pr(page, server_url, add_pr({"t.py": body(" ", 0)}, {"t.py": body("  ", 1)}))
    section = page.locator("section[data-file='t.py']")
    section.scroll_into_view_if_needed()
    section.locator("tr.fold").click()
    section.locator("tr.ctx", has_text="p = 0").locator("td.n.key").click()
    expect(page.locator(".form.compose .hint")).to_contain_text("Comment on line 15 ")
    section.locator("tr.runhead[data-fold]").click()
    expect(section.locator("tr.fold")).to_have_count(2)
    section.locator("tr.fold").first.click()
    expect(section.locator("tr.ctx td.c", has_text="a6 = 6")).to_be_visible()
    section.locator("[data-compose-cancel]").click()
    expect(page.locator(".form.compose")).to_have_count(0)
    expect(section.locator("tr.ctx td.c", has_text="a6 = 6")).to_be_visible()
    expect(section.locator("tr.ctx td.c", has_text="b20 = 20")).to_have_count(0)
    expect(section.locator("tr.fold")).to_have_count(1)


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


def test_option_down_and_up_go_to_the_next_and_previous_file(page: Page, server_url: str, config_pr: str) -> None:
    open_pr(page, server_url, config_pr)
    active = page.locator("#side .leaf.active")
    page.keyboard.press("Alt+ArrowDown")
    expect(active).to_have_attribute("data-path", "src/config.py")
    page.keyboard.press("Alt+ArrowDown")
    expect(active).to_have_attribute("data-path", "src/jobs.py")
    page.keyboard.press("Alt+ArrowUp")
    expect(active).to_have_attribute("data-path", "src/config.py")


def test_r_marks_the_current_file_reviewed_and_a_second_press_keeps_it_reviewed(
    page: Page, server_url: str, config_pr: str
) -> None:
    open_pr(page, server_url, config_pr)
    meta = page.locator("#side .head .meta")
    page.keyboard.press("r")
    expect(meta).to_contain_text("0 reviewed")
    page.keyboard.press("Alt+ArrowDown")
    leaf = page.locator("#side .leaf[data-path='src/config.py']")
    expect(leaf).to_have_class(re.compile(r"\bactive\b"))
    page.keyboard.press("r")
    expect(leaf).to_have_class(re.compile(r"\breviewed\b"))
    expect(meta).to_contain_text("1 reviewed")
    page.keyboard.press("r")
    expect(leaf).to_have_class(re.compile(r"\breviewed\b"))
    expect(meta).to_contain_text("1 reviewed")


def test_r_twice_after_a_wheel_scroll_near_the_end_marks_one_file(page: Page, server_url: str, config_pr: str) -> None:
    page.set_viewport_size({"width": 1280, "height": 900})
    open_pr(page, server_url, config_pr)
    page.mouse.move(900, 400)
    for _ in range(200):
        if page.evaluate("state.view") == "src/jobs.py":
            break
        page.mouse.wheel(0, 40)
        page.wait_for_timeout(20)
    expect(page.locator("#side .leaf.active")).to_have_attribute("data-path", "src/jobs.py")
    page.keyboard.press("r")
    page.keyboard.press("r")
    expect(page.locator("#side .leaf.reviewed")).to_have_count(1)
    expect(page.locator("#side .leaf.reviewed")).to_have_attribute("data-path", "src/jobs.py")


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
    expect(meta).to_have_text("4 files · 9 lines")
    page.click("#side .leaf[data-path='src/jobs.py']")
    expect(meta).to_have_text("4 files · 3 / 9")
    page.keyboard.press("ArrowUp")
    expect(page.locator("#side .leaf.cursor")).to_have_attribute("data-path", "src/config.py")
    expect(config).to_have_class(re.compile(r"\bclosed\b"))
    expect(meta).to_have_text("4 files · 3 / 9")
    page.keyboard.press("Enter")
    expect(config).not_to_have_class(re.compile(r"\bclosed\b"))
    expect(meta).to_have_text("4 files · 1 / 9")


def test_enter_in_the_search_box_opens_a_reviewed_file_at_its_hit(page: Page, server_url: str, config_pr: str) -> None:
    open_pr(page, server_url, config_pr)
    config = page.locator("section[data-file='src/config.py']")
    page.click("#side .leaf[data-path='src/config.py'] .check")
    expect(config).to_have_class(re.compile(r"\bclosed\b"))
    page.fill("#side .search", "re")
    page.press("#side .search", "Enter")
    expect(page.locator("#searchmeta")).to_have_text("4 files · 1 / 9")
    expect(config).not_to_have_class(re.compile(r"\bclosed\b"))
    expect(config.locator(f"#{hit_row(page)}")).to_have_count(1)


def test_enter_and_shift_enter_go_on_into_a_closed_section_and_open_it(
    page: Page, server_url: str, config_pr: str
) -> None:
    open_pr(page, server_url, config_pr)
    meta = page.locator("#searchmeta")
    config = page.locator("section[data-file='src/config.py']")
    page.fill("#side .search", "re")
    page.press("#side .search", "Enter")
    expect(meta).to_have_text("4 files · 1 / 9")
    config.locator(".fhead .chev").click()
    expect(config).to_have_class(re.compile(r"\bclosed\b"))
    expect(meta).to_have_text("4 files · 1 / 9")
    page.press("#side .search", "Enter")
    expect(meta).to_have_text("4 files · 2 / 9")
    expect(config).not_to_have_class(re.compile(r"\bclosed\b"))
    page.press("#side .search", "Enter")
    expect(meta).to_have_text("4 files · 3 / 9")
    config.locator(".fhead .chev").click()
    expect(config).to_have_class(re.compile(r"\bclosed\b"))
    page.press("#side .search", "Shift+Enter")
    expect(meta).to_have_text("4 files · 2 / 9")
    expect(config).not_to_have_class(re.compile(r"\bclosed\b"))
    expect(config.locator(f"#{hit_row(page)}")).to_have_count(1)


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


def test_escape_in_the_tree_ends_the_search_and_leaves_the_tree(page: Page, server_url: str) -> None:
    open_pr(page, server_url)
    page.fill("#side .search", "hello")
    expect(page.locator("#searchmeta")).to_have_text("1 file · 1 line")
    page.click("#side .leaf[data-path='src/app.py']")
    assert page.evaluate("document.activeElement.matches('#side .list')")
    page.keyboard.press("Escape")
    expect(page.locator("#side .search")).to_have_value("")
    expect(page.locator("#searchmeta")).to_have_text("")
    assert page.evaluate("state.q") == ""
    assert page.evaluate("document.activeElement === document.body")


def test_escape_in_a_comment_box_keeps_the_search(page: Page, server_url: str) -> None:
    open_pr(page, server_url)
    page.fill("#side .search", "hello")
    page.locator(APP).locator("tr.add", has_text='print("hello")').locator("td.n.key").click()
    page.locator("textarea[data-compose-text]").press("Escape")
    expect(page.locator("#side .search")).to_have_value("hello")
    page.keyboard.press("Escape")
    expect(page.locator("#side .search")).to_have_value("")


def test_search_keeps_every_file_in_the_tree(page: Page, server_url: str) -> None:
    open_pr(page, server_url)
    leaves = page.locator("#side .leaf")
    count = leaves.count()
    page.fill("#side .search", "hello")
    expect(page.locator("#searchmeta")).to_have_text("1 file · 1 line")
    expect(leaves).to_have_count(count)
    expect(page.locator("#main section[data-file]")).to_have_count(1)


def test_typing_in_the_search_keeps_the_caret_where_the_reader_types(page: Page, server_url: str) -> None:
    open_pr(page, server_url)
    box = page.locator("#side .search")
    box.click()
    page.keyboard.type("hllo")
    page.keyboard.press("Home")
    page.keyboard.press("ArrowRight")
    page.keyboard.type("e")
    expect(box).to_have_value("hello")
    assert box.evaluate("b => [b.selectionStart, b.selectionEnd]") == [2, 2]
    expect(page.locator("#searchmeta")).to_have_text("1 file · 1 line")


def test_typing_a_space_in_the_search_keeps_it(page: Page, server_url: str) -> None:
    open_pr(page, server_url)
    box = page.locator("#side .search")
    box.click()
    page.keyboard.type('print("hello")')
    page.keyboard.press("Home")
    page.keyboard.type(" ")
    page.keyboard.press("End")
    page.keyboard.type(" ")
    expect(box).to_have_value(' print("hello") ')
    expect(page.locator("#searchmeta")).to_have_text("1 file · 1 line")


def test_search_skips_closed_folds_and_counts_closed_sections(page: Page, server_url: str) -> None:
    open_pr(page, server_url)
    page.click("#side [data-filter=all]")
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
    section.locator(".fhead .chev").click()
    expect(section).to_have_class(re.compile(r"\bclosed\b"))
    expect(page.locator("#searchmeta")).to_have_text("2 files · 8 lines")
    expect(page.locator("#side .leaf[data-path='src/app.py'] .hits")).to_have_text("6")


def test_cmd_f_fills_the_search_with_the_selected_code(page: Page, server_url: str) -> None:
    open_pr(page, server_url)
    page.locator(f"{APP} tr.add td.c", has_text="hello").select_text()
    page.keyboard.press("Meta+f")
    expect(page.locator("#side .search")).to_be_focused()
    expect(page.locator("#side .search")).to_have_value('print("hello")')
    expect(page.locator("#searchmeta")).to_have_text("1 file · 1 line")


def test_a_click_on_the_header_leaves_the_section_open_and_the_chevron_closes_it(page: Page, server_url: str) -> None:
    open_pr(page, server_url)
    section = page.locator(APP)
    section.locator(".fhead .path").click()
    expect(section).not_to_have_class(re.compile(r"\bclosed\b"))
    section.locator(".fhead .chev").click()
    expect(section).to_have_class(re.compile(r"\bclosed\b"))


def test_shift_cmd_f_searches_the_file_paths_and_cmd_f_goes_back_to_the_rows(page: Page, server_url: str) -> None:
    open_pr(page, server_url)
    meta = page.locator("#searchmeta")
    page.keyboard.press("Meta+Shift+f")
    expect(page.locator("#side .search")).to_be_focused()
    page.keyboard.type("app")
    expect(meta).to_have_text("1 file")
    expect(page.locator("#main section[data-file]")).to_have_attribute("data-file", "src/app.py")
    page.keyboard.press("Meta+Shift+f")
    expect(meta).to_have_text("1 file")
    page.keyboard.press("Meta+f")
    expect(meta).to_have_text("0 files · 0 lines")
    page.keyboard.press("Meta+Shift+f")
    page.keyboard.press("Escape")
    page.click("#side .search")
    page.keyboard.type("app")
    expect(meta).to_have_text("0 files · 0 lines")


def search_files_pr(add_pr: AddPr) -> str:
    """A pull request adding four short files, two of them with "one" in their path."""
    return add_pr({}, {p: "a = 1\nb = 2\n" for p in ("a/one.py", "a/two.py", "b/one_more.py", "b/three.py")})


def test_file_search_lists_only_the_found_files_and_enter_steps_through_them(
    page: Page, server_url: str, add_pr: AddPr
) -> None:
    open_pr(page, server_url, search_files_pr(add_pr))
    page.keyboard.press("Meta+Shift+f")
    page.keyboard.type("one")
    expect(page.locator("#side .leaf")).to_have_count(2)
    active = page.locator("#side .leaf.active")
    page.keyboard.press("Enter")
    expect(active).to_have_attribute("data-path", "a/one.py")
    expect(page.locator("#searchmeta")).to_have_text("2 files · 1 / 2")
    page.keyboard.press("Enter")
    expect(active).to_have_attribute("data-path", "b/one_more.py")
    page.keyboard.press("Shift+Enter")
    expect(active).to_have_attribute("data-path", "a/one.py")
    page.click("#side .leaf[data-path='b/one_more.py']")
    expect(page.locator("#searchmeta")).to_have_text("2 files · 2 / 2")
    page.keyboard.press("Alt+ArrowUp")
    expect(page.locator("#searchmeta")).to_have_text("2 files · 1 / 2")
    page.click("#side .search")
    page.keyboard.press("Enter")
    expect(active).to_have_attribute("data-path", "b/one_more.py")


def test_enter_in_file_search_leaves_a_reviewed_file_closed(page: Page, server_url: str, add_pr: AddPr) -> None:
    open_pr(page, server_url, search_files_pr(add_pr))
    page.click("#side .leaf[data-path='a/one.py'] .check")
    section = page.locator("section[data-file='a/one.py']")
    expect(section).to_have_class(re.compile(r"\bclosed\b"))
    page.keyboard.press("Meta+Shift+f")
    page.keyboard.type("one")
    page.keyboard.press("Enter")
    expect(page.locator("#side .leaf.active")).to_have_attribute("data-path", "a/one.py")
    expect(section).to_have_class(re.compile(r"\bclosed\b"))


def test_escape_brings_back_every_file_around_the_one_file_search_went_to(
    page: Page, server_url: str, add_pr: AddPr
) -> None:
    page.set_viewport_size({"width": 1100, "height": 400})
    open_pr(page, server_url, search_files_pr(add_pr))
    page.keyboard.press("Meta+Shift+f")
    page.keyboard.type("one")
    page.keyboard.press("Enter")
    page.keyboard.press("Enter")
    header = page.locator("section[data-file='b/one_more.py'] .fhead")
    before = header.bounding_box()
    page.keyboard.press("Escape")
    expect(page.locator("#side .leaf")).to_have_count(4)
    expect(page.locator("#main section[data-file]")).to_have_count(4)
    expect(page.locator("#side .leaf.active")).to_have_attribute("data-path", "b/one_more.py")
    after = header.bounding_box()
    assert before and after
    assert after["y"] == pytest.approx(before["y"], abs=1)


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
    ).to_have_class("notes")
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
    thread = section.locator("div.thread[data-thread]", has_text="Use a logger here")
    expect(thread).to_be_visible()
    expect(thread.locator(".tag.sending")).to_have_count(0)
    expect(page.locator("textarea[data-compose-text]")).to_have_count(0)
    expect(page.locator("#side .leaf[data-path='src/app.py'] .bubble")).to_have_text("2")

    mutations = [json.loads(line) for line in (gh_state / "mutations.log").read_text().splitlines()]
    posted = [
        m
        for m in mutations
        if "addPullRequestReview(" in m["query"]
        and any(t["body"] == "Use a logger here" for t in m["variables"].get("threads") or [])
    ]
    assert len(posted) == 1
    assert "event: COMMENT" in posted[0]["query"]
    assert posted[0]["variables"]["threads"] == [
        {"path": "src/app.py", "body": "Use a logger here", "line": 11, "side": "RIGHT"}
    ]


def test_a_comment_sent_to_claude_waits_on_its_line_until_claude_replies(page: Page, server_url: str) -> None:
    open_pr(page, server_url)
    foot = page.locator("#side .foot")
    expect(foot).to_contain_text("No Claude session")
    bubble = page.locator("#side .leaf[data-path='src/app.py'] .bubble")
    count = bubble.text_content()
    section = page.locator(APP)
    section.locator("tr.add", has_text='print("hello")').locator("td.n.key").click()
    page.locator("textarea[data-compose-text]").fill("Use a logger here")
    page.click("button[data-compose-send='claude']")
    thread = section.locator("div.thread[data-thread^='claude:']", has_text="Use a logger here")
    expect(thread.locator(".tag.claude")).to_have_text("waiting for Claude")
    expect(foot).to_contain_text("1 comment waiting")
    expect(page.locator("textarea[data-compose-text]")).to_have_count(0)
    assert bubble.text_content() == count
    sent = page.evaluate("CL.comments.at(-1)")
    assert (sent["line"], sent["side"], sent["code"]) == (11, "RIGHT", ['    print("hello")'])

    reply = page.request.post(
        f"{server_url}/api/acme/widgets/1/claude_reply",
        data={"id": sent["id"], "body": "Logged it."},
        headers={"Origin": server_url},
    )
    assert reply.ok
    page.evaluate("loadClaude()")
    expect(thread).to_contain_text("Logged it.")
    expect(thread.locator(".tag.claude")).to_have_count(0)

    thread.locator("[data-claude-delete]").click()
    expect(section.locator("div.thread[data-thread^='claude:']")).to_have_count(0)
    expect(foot).not_to_contain_text("waiting")


@contextmanager
def kept_comments(gh_state: Path) -> Iterator[None]:
    """Put the fake gh's comments back as they were after the block."""
    comments_file = gh_state / "comments.json"
    saved = comments_file.read_text()
    try:
        yield
    finally:
        comments_file.write_text(saved)


@contextmanager
def held_mutations(gh_state: Path) -> Iterator[Callable[[], None]]:
    """Hold every mutation of the fake gh until the function this yields is called. Afterwards, wait for the
    mutations to end, then put the comments back as they were."""
    hold = gh_state / "hold.txt"
    with kept_comments(gh_state):
        hold.write_text("")
        try:
            yield lambda: hold.unlink(missing_ok=True)
        finally:
            hold.unlink(missing_ok=True)
            deadline = time.monotonic() + 10
            while any(gh_state.glob("held-*")) and time.monotonic() < deadline:
                time.sleep(0.05)


@contextmanager
def refused_mutations(page: Page, gh_state: Path) -> Iterator[None]:
    """Make the fake gh refuse every GraphQL call in the block, and wait for the page to show the refusal."""
    fail = gh_state / "fail.txt"
    fail.write_text("api graphql\n")
    try:
        yield
        expect(page.locator("#side .notice.err")).to_contain_text("Comments:")
    finally:
        fail.unlink()


def send_on_hello(page: Page, text: str, mode: str = "single") -> Locator:
    """Send a comment on the line that prints hello, and return its thread."""
    section = page.locator(APP)
    section.locator("tr.add", has_text='print("hello")').locator("td.n.key").click()
    page.locator("textarea[data-compose-text]").fill(text)
    page.click(f"button[data-compose-send='{mode}']")
    return section.locator("div.thread[data-thread]", has_text=text)


def test_a_comment_shows_as_sending_until_github_has_it(page: Page, server_url: str, gh_state: Path) -> None:
    with held_mutations(gh_state) as release:
        open_pr(page, server_url)
        thread = send_on_hello(page, "Greet by name", "review")
        expect(thread.locator(".tag.sending")).to_be_visible()
        expect(thread.locator(".tag.pending")).to_be_visible()
        expect(page.locator("textarea[data-compose-text]")).to_have_count(0)
        expect(thread.locator("a[target=_blank]")).to_have_count(0)

        release()
        expect(thread.locator(".tag.sending")).to_have_count(0)
        expect(thread.locator("a[target=_blank]")).to_have_count(1)
        expect(page.locator(APP).locator("div.thread[data-thread]", has_text="Greet by name")).to_have_count(1)


def test_a_box_opened_while_a_comment_sends_keeps_its_focus(page: Page, server_url: str, gh_state: Path) -> None:
    with held_mutations(gh_state) as release:
        open_pr(page, server_url)
        thread = send_on_hello(page, "Greet by name")
        expect(thread.locator(".tag.sending")).to_be_visible()
        page.locator(APP).locator("tr.ctx", has_text="def main():").locator("td.n.key").click()
        box = page.locator("textarea[data-compose-text]")
        expect(box).to_be_focused()
        page.keyboard.type("abc")

        release()
        expect(thread.locator("a[target=_blank]")).to_have_count(1)
        page.keyboard.type("d")
        expect(box).to_have_value("abcd")
        expect(box).to_be_focused()


def test_a_comment_github_refuses_goes_back_to_its_box(page: Page, server_url: str, gh_state: Path) -> None:
    open_pr(page, server_url)
    with refused_mutations(page, gh_state):
        thread = send_on_hello(page, "Greet by name")
    expect(page.locator("textarea[data-compose-text]")).to_have_value("Greet by name")
    expect(thread).to_have_count(0)


def test_a_refused_comment_takes_the_text_written_meanwhile_on_its_line(
    page: Page, server_url: str, gh_state: Path
) -> None:
    with held_mutations(gh_state) as release:
        open_pr(page, server_url)
        thread = send_on_hello(page, "Greet by name")
        expect(thread.locator(".tag.sending")).to_be_visible()
        page.locator(APP).locator("tr.add", has_text='print("hello")').locator("td.n.key").click()
        page.keyboard.type("Use their login")
        with refused_mutations(page, gh_state):
            release()
    expect(page.locator("textarea[data-compose-text]")).to_have_value("Greet by name\n\nUse their login")
    expect(thread).to_have_count(0)


def test_a_refused_comment_waits_as_a_draft_while_another_box_has_text(
    page: Page, server_url: str, gh_state: Path
) -> None:
    with held_mutations(gh_state) as release:
        open_pr(page, server_url)
        thread = send_on_hello(page, "Greet by name")
        expect(thread.locator(".tag.sending")).to_be_visible()
        page.locator(APP).locator("tr.ctx", has_text="def main():").locator("td.n.key").click()
        page.keyboard.type("Other")
        with refused_mutations(page, gh_state):
            release()
    box = page.locator("textarea[data-compose-text]")
    expect(box).to_have_value("Other")
    expect(box).to_be_focused()
    notice = page.locator("#side .notice", has_text="GitHub refused your comment on line 11 of app.py")
    expect(notice).to_be_visible()
    page.click("#side a[data-comments-reload]")
    expect(page.locator("#side .notice.err")).to_have_count(0)
    expect(notice).to_be_visible()
    notice.locator("a[data-draft-open]").click()
    expect(box).to_have_value("Greet by name")
    expect(notice).to_have_count(0)


def test_a_refused_reply_goes_back_to_its_box_and_leaves_the_focus_where_it_is(
    page: Page, server_url: str, gh_state: Path
) -> None:
    with held_mutations(gh_state) as release:
        open_pr(page, server_url)
        page.click("button[data-reply='T1']")
        page.locator("textarea[data-reply-text='T1']").fill("Will do")
        page.click("button[data-reply-send='T1'][data-mode='single']")
        expect(page.locator("div.thread[data-thread='T1'] .tag.sending")).to_be_visible()
        page.locator(APP).locator("tr.ctx", has_text="def main():").locator("td.n.key").click()
        page.keyboard.type("Other")
        with refused_mutations(page, gh_state):
            release()
    expect(page.locator("textarea[data-reply-text='T1']")).to_have_value("Will do")
    expect(page.locator("div.thread[data-thread='T1'] .comment", has_text="Will do")).to_have_count(0)
    page.keyboard.type("X")
    expect(page.locator("textarea[data-compose-text]")).to_have_value("OtherX")


def test_a_reply_without_a_pending_review_shows_as_sending_and_not_pending(
    page: Page, server_url: str, gh_state: Path
) -> None:
    with held_mutations(gh_state) as release:
        open_pr(page, server_url)
        page.click("button[data-reply='T1']")
        page.locator("textarea[data-reply-text='T1']").fill("Will do")
        page.click("button[data-reply-send='T1'][data-mode='review']")
        reply = page.locator("div.thread[data-thread='T1'] .comment", has_text="Will do")
        expect(reply.locator(".tag.sending")).to_be_visible()
        expect(reply.locator(".tag.pending")).to_have_count(0)
        expect(page.locator("textarea[data-reply-text='T1']")).to_have_count(0)

        release()
        expect(reply.locator(".tag.sending")).to_have_count(0)
        expect(reply.locator("a[target=_blank]")).to_have_count(1)
        expect(page.locator("div.thread[data-thread='T1'] .comment", has_text="Will do")).to_have_count(1)


def hold_requests(page: Page, pattern: str) -> list[tuple[Route, APIResponse]]:
    """Send each request to a URL matching pattern on to the server at once, and hold its answer back from the page.
    Returns the held requests with their answers, in the order they came."""
    held: list[tuple[Route, APIResponse]] = []
    page.route(pattern, lambda route: held.append((route, route.fetch())))
    return held


def wait_for(page: Page, check: Callable[[], bool]) -> None:
    deadline = time.monotonic() + 10
    while not check():
        assert time.monotonic() < deadline
        page.wait_for_timeout(50)


def answer(held: tuple[Route, APIResponse]) -> None:
    route, response = held
    route.fulfill(response=response)


def test_the_sending_tag_goes_when_github_takes_the_comment(page: Page, server_url: str, gh_state: Path) -> None:
    with kept_comments(gh_state):
        open_pr(page, server_url)
        loads = hold_requests(page, "**/api/acme/widgets/1/comments")
        thread = send_on_hello(page, "Greet by name")
        wait_for(page, lambda: len(loads) == 1)
        expect(thread.locator(".tag.sending")).to_have_count(0)
        expect(thread.locator("a[target=_blank]")).to_have_count(0)
        answer(loads[0])
        expect(thread.locator("a[target=_blank]")).to_have_count(1)


def test_a_load_that_started_before_github_took_a_comment_shows_it_once(
    page: Page, server_url: str, gh_state: Path
) -> None:
    with kept_comments(gh_state):
        open_pr(page, server_url)
        sends = hold_requests(page, "**/api/acme/widgets/1/comment")
        loads = hold_requests(page, "**/api/acme/widgets/1/comments")
        thread = send_on_hello(page, "Greet by name")
        wait_for(page, lambda: len(sends) == 1)
        page.evaluate("() => { window.early = loadComments(); }")
        wait_for(page, lambda: len(loads) == 1)
        answer(sends[0])
        wait_for(page, lambda: len(loads) == 2)
        answer(loads[0])
        page.evaluate("early")
        expect(thread).to_have_count(1)
        expect(thread.locator("a[target=_blank]")).to_have_count(1)
        answer(loads[1])
        expect(thread).to_have_count(1)


def test_a_load_that_answers_after_a_later_one_changes_nothing(page: Page, server_url: str, gh_state: Path) -> None:
    with kept_comments(gh_state):
        open_pr(page, server_url)
        loads = hold_requests(page, "**/api/acme/widgets/1/comments")
        page.evaluate("() => { window.late = loadComments(); }")
        wait_for(page, lambda: len(loads) == 1)
        thread = send_on_hello(page, "Greet by name")
        wait_for(page, lambda: len(loads) == 2)
        answer(loads[1])
        expect(thread.locator("a[target=_blank]")).to_have_count(1)
        answer(loads[0])
        page.evaluate("late")
        expect(thread).to_have_count(1)
        expect(thread.locator("a[target=_blank]")).to_have_count(1)


def test_a_comment_box_under_the_last_row_of_a_chunk_opens_and_closes(
    page: Page, server_url: str, add_pr: AddPr
) -> None:
    errors: list[str] = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    open_pr(page, server_url, add_pr({}, {"src/long.py": "".join(f"x{n} = {n}\n" for n in range(1, 1001))}))
    last = page.locator("section[data-file='src/long.py'] tr[id$='-L100']")
    last.locator("td.n.key").click()
    expect(page.locator(".form.compose .hint")).to_contain_text("Comment on line 100 ")
    expect(last.locator("xpath=following-sibling::tr[1]")).to_have_class("notes")
    page.click("button[data-compose-cancel]")
    expect(page.locator(".form.compose")).to_have_count(0)
    expect(page.locator("tr.notes")).to_have_count(0)
    page.locator("section[data-file='src/long.py'] tr[id$='-L2'] td.n.key").click()
    expect(page.locator(".form.compose .hint")).to_contain_text("Comment on line 2 ")
    assert errors == []


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
    page.click("#side [data-filter=all]")
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
    page.click("#side [data-filter=all]")
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


def test_a_comment_box_keeps_its_dragged_height_after_scrolling_out_of_view(
    page: Page, server_url: str, add_pr: AddPr
) -> None:
    open_pr(page, server_url, add_pr({}, {"src/long.py": "".join(f"x{n} = {n}\n" for n in range(1, 1001))}))
    page.locator("section[data-file='src/long.py'] tr[id$='-L2'] td.n.key").click()
    box = page.locator("textarea[data-compose-text]")
    box.press("Escape")
    expect(page.locator("div.notes.resizing")).to_have_count(0)
    edge = box.bounding_box()
    assert edge
    page.mouse.move(edge["x"] + edge["width"] - 3, edge["y"] + edge["height"] - 3)
    page.mouse.down()
    page.mouse.move(edge["x"] + edge["width"] - 3, edge["y"] + edge["height"] + 120, steps=5)
    page.mouse.up()
    height = box.evaluate("b => b.offsetHeight")
    assert height > edge["height"] + 100

    page.evaluate("main.scrollTop = main.scrollHeight")
    expect(box).to_have_count(0)
    page.evaluate("main.scrollTop = 0")
    expect(box).to_have_count(1)
    assert box.evaluate("b => b.offsetHeight") == height


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
    expect(tab.locator(".count")).to_have_text(str(unresolved))
    expect(page.frame_locator("#panes iframe.on").locator("#side .tree")).to_be_visible()

    page.reload()
    tab = page.locator("#tabs .tab:not(.home)")
    expect(tab).to_have_count(1)
    expect(tab.locator(".label")).to_have_text(f"#1 {TITLE}")
    page.click("#tabs .tab.home")
    expect(page.locator("#tabs .tab.home")).to_have_class(re.compile(r"\bon\b"))
    expect(page.frame_locator("#panes iframe.on").locator("input[name=pr]")).to_be_visible()


def test_the_shell_opens_the_settings_on_cmd_comma_and_closes_them_on_escape_and_the_backdrop(
    page: Page, server_url: str
) -> None:
    page.goto(server_url + "/")
    home = page.frame_locator("#panes iframe.on")
    home.locator("input[name=pr]").fill("acme/widgets#1")
    home.locator("input[name=pr]").press("Enter")
    expect(page.locator("#tabs .tab:not(.home) .label")).to_have_text(f"#1 {TITLE}", timeout=30000)
    pane = page.locator("#panes iframe.on")
    expect(pane.content_frame.locator("#side .tree")).to_be_visible()
    frame = pane.element_handle().content_frame()
    assert frame
    frame.evaluate(
        "window.cancelled = [];"
        "window.addEventListener('keydown', (e) => { if (e.key === 'Escape') cancelled.push(e.defaultPrevented); })"
    )
    # Focus sits inside the page, which forwards the chord to the shell.
    frame.click("#side .list")
    page.keyboard.press("Meta+,")
    dialog = page.locator("#settings")
    expect(dialog).to_be_visible()
    expect(dialog.locator("h2", has_text="Cache")).to_be_visible()
    # The shell cancels every Escape, so that the macOS window keeps full screen, and closes the dialog itself.
    page.keyboard.press("Escape")
    expect(dialog).to_be_hidden()
    frame.fill("#side .search", "hello")
    page.keyboard.press("Escape")
    expect(frame.locator("#side .search")).to_have_value("")
    assert frame.evaluate("cancelled") == [True]
    page.click("#gear")
    expect(dialog).to_be_visible()
    page.mouse.click(4, 4)
    expect(dialog).to_be_hidden()


def test_the_start_pages_settings_button_opens_the_settings(page: Page, server_url: str) -> None:
    page.goto(server_url + "/")
    page.frame_locator("#panes iframe.on").locator("footer.start [data-settings]").click()
    expect(page.locator("#settings")).to_be_visible()


def test_the_settings_save_the_followed_repositories_as_they_change(page: Page, server_url: str) -> None:
    page.goto(server_url + "/")
    page.click("#gear")
    dialog = page.locator("#settings")
    fields, add = dialog.locator(".repos input"), dialog.locator(".add input")
    expect(dialog.locator(".repos")).to_be_attached()
    for name in ("Acme/Widgets", "https://github.com/acme/tools.git", "acme/widgets"):
        add.fill(name)
        add.press("Enter")
    # Each name once, in lower case, without the .git of a clone URL; the add field is empty and focused again.
    expect(fields).to_have_count(2)
    expect(fields.nth(0)).to_have_value("acme/widgets")
    expect(fields.nth(1)).to_have_value("acme/tools")
    expect(add).to_have_value("")
    expect(add).to_be_focused()
    # A text that names no repository stays in its field with the error under it, and the list is unchanged.
    add.fill("not a repo")
    add.press("Enter")
    expect(dialog.locator("[data-error]")).to_have_text(
        "Not a repository: 'not a repo'. Use owner/repo or the repository's URL."
    )
    expect(add).to_have_value("not a repo")
    # An edit is saved when the field is left, and the focus stays where it went; an edit and a ✕ fired together
    # (the blur, then the click) save the edited name without the removed one.
    fields.nth(0).fill("Acme/Gadgets")
    fields.nth(0).press("Tab")
    expect(fields.nth(0)).to_have_value("acme/gadgets")
    expect(dialog.locator("[data-remove]").nth(0)).to_be_focused()
    expect(dialog.locator("[data-error]")).to_be_hidden()
    fields.nth(0).fill("acme/gizmos")
    dialog.locator("[data-remove]").nth(1).click()
    expect(fields).to_have_count(1)
    expect(fields.nth(0)).to_have_value("acme/gizmos")
    # A refused edit puts the fields back to the saved list, so a ✕ after it saves.
    add.fill("acme/tools")
    add.press("Enter")
    expect(fields).to_have_count(2)
    fields.nth(0).fill("bad name")
    fields.nth(0).press("Tab")
    expect(dialog.locator("[data-error]")).to_have_text(
        "Not a repository: 'bad name'. Use owner/repo or the repository's URL."
    )
    expect(fields.nth(0)).to_have_value("acme/gizmos")
    dialog.locator("[data-remove]").nth(1).click()
    expect(fields).to_have_count(1)
    expect(dialog.locator("[data-error]")).to_be_hidden()
    # The list comes back from the server on the next open (the body is fetched again, so the test waits for the
    # new one), and an emptied field removes its repository.
    page.keyboard.press("Escape")
    page.click("#gear")
    expect(dialog.locator(".repos")).to_have_attribute("data-repos", '["acme/gizmos"]')
    expect(fields).to_have_count(1)
    expect(fields.nth(0)).to_have_value("acme/gizmos")
    fields.nth(0).press("ControlOrMeta+a")
    fields.nth(0).press("Backspace")
    fields.nth(0).press("Tab")
    expect(fields).to_have_count(0)
    page.keyboard.press("Escape")
    page.click("#gear")
    expect(dialog.locator(".repos")).to_have_attribute("data-repos", "[]")


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


def test_a_tooltip_in_the_settings_shows_above_the_dialog(page: Page, server_url: str) -> None:
    page.goto(server_url + "/")
    page.click("#gear")
    heading = page.locator("#settings h1")
    heading.evaluate("e => e.title = 'What differ does'")
    heading.hover()
    expect(page.locator("#settings .tooltip")).to_have_class(re.compile(r"\bon\b"), timeout=600)
    expect(page.locator("#settings .tooltip")).to_have_text("What differ does")


def test_a_tooltip_shows_on_an_element_reached_by_scrolling(page: Page, server_url: str) -> None:
    open_pr(page, server_url)
    page.locator(f"{APP} .fhead .badge").hover()
    expect(page.locator(".tooltip")).to_have_class(re.compile(r"\bon\b"), timeout=1000)


def test_a_click_dismisses_the_tooltip_even_when_the_click_redraws_the_element(page: Page, server_url: str) -> None:
    open_pr(page, server_url)
    chevron = page.locator(f"{APP} .fhead .chev")
    tip = page.locator(".tooltip")
    chevron.hover()
    expect(tip).to_have_class(re.compile(r"\bon\b"), timeout=1000)
    chevron.click()
    expect(page.locator(APP)).to_have_class(re.compile(r"\bclosed\b"))
    page.wait_for_timeout(600)
    expect(tip).not_to_have_class(re.compile(r"\bon\b"))
    page.locator("#side h1").hover()
    chevron.hover()
    expect(tip).to_have_class(re.compile(r"\bon\b"), timeout=1000)


def own_pr(number: int, draft: bool, ci: str, repo: str = "acme/widgets") -> dict:
    return {
        "number": number,
        "title": f"{TITLE} {number}",
        "url": f"https://github.com/{repo}/pull/{number}",
        "isDraft": draft,
        "state": "OPEN",
        "updatedAt": f"2026-10-{number:02d}T10:00:00Z",
        "mergedAt": None,
        "mergeable": "MERGEABLE",
        "reviewDecision": None,
        "repository": {"nameWithOwner": repo},
        "author": {"__typename": "User", "login": "tester"},
        "commits": {
            "nodes": [{"commit": {"committedDate": "2026-10-01T10:00:00Z", "statusCheckRollup": {"state": ci}}}]
        },
        "reviewRequests": {"nodes": []},
        "reviews": {"nodes": []},
        "comments": {"nodes": []},
        "reviewThreads": {"nodes": []},
    }


def test_the_start_page_lists_the_viewers_pull_requests_and_show_more_opens_the_rest(
    page: Page, server_url: str, gh_state: Path, cache_dir: Path
) -> None:
    found = [own_pr(1, False, "FAILURE"), *(own_pr(n, True, "SUCCESS") for n in range(2, 9))]
    (gh_state / "search.json").write_text(
        json.dumps({"is:pr is:open archived:false author:@me sort:updated-desc": found})
    )
    # The board asks GitHub at most every 30 seconds, so an answer saved by an earlier test goes first.
    (cache_dir / "board.json").unlink(missing_ok=True)
    try:
        page.goto(server_url + "/home")
        action = page.locator('#board .sub[data-key="action"]')
        expect(action.locator(".card .reason")).to_have_text("CI failed")
        drafts = page.locator('#board .sub[data-key="draft"]')
        cards = drafts.locator(".card")
        expect(cards).to_have_count(7)
        expect(cards.nth(4)).to_be_visible()
        expect(cards.nth(5)).to_be_hidden()
        drafts.get_by_role("button", name="Show 2 more").click()
        expect(cards.nth(6)).to_be_visible()
        expect(cards.nth(6).locator(".t")).to_have_text(f"{TITLE} 2")
        expect(drafts.get_by_role("button")).to_have_count(0)
    finally:
        (gh_state / "search.json").unlink()
        (cache_dir / "board.json").unlink(missing_ok=True)


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
        expect(review).to_have_text("Ready to review")
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


def test_choosing_an_editor_in_a_file_s_menu_opens_the_file_in_it_and_keeps_the_choice(
    page: Page, server_url: str
) -> None:
    installed = [
        {"id": "vscode", "name": "VS Code", "local": True},
        {"id": "pycharm", "name": "PyCharm", "local": True},
    ]
    page.route(
        "**/api/acme/widgets/1/editors",
        lambda r: r.fulfill(json={"installed": installed, "known": ["VS Code", "PyCharm"]}),
    )
    opened: list[dict] = []
    page.route(
        "**/api/acme/widgets/1/open", lambda r: (opened.append(r.request.post_data_json or {}), r.fulfill(json={}))
    )
    open_pr(page, server_url)
    page.wait_for_function("EDITOR.installed")
    menu = page.locator(f"{APP} .editor-menu")
    page.click(f"{APP} [data-editor-menu]")
    expect(menu).to_be_visible()
    page.click("#overview h1")
    expect(menu).to_have_count(0)
    page.click(f"{APP} [data-editor-menu]")
    menu.get_by_role("menuitemradio", name="PyCharm").click()
    expect(menu).to_have_count(0)
    page.wait_for_function("!EDITOR.opening.size")
    assert opened == [{"editor": "pycharm", "path": "src/app.py", "line": 2}]
    assert page.evaluate("localStorage.getItem('dr:editor')") == "pycharm"
    page.click("#side [data-filter=docs]")
    page.click("section[data-file='README.md'] [data-open-editor]")
    page.wait_for_function("!EDITOR.opening.size")
    assert opened[-1] == {"editor": "pycharm", "path": "README.md", "line": 2}


def test_the_start_pages_repository_toggles_filter_the_board_and_survive_a_reload(
    page: Page, server_url: str, gh_state: Path, cache_dir: Path
) -> None:
    found = [own_pr(1, False, "SUCCESS"), own_pr(2, False, "SUCCESS", repo="acme/tools")]
    (gh_state / "search.json").write_text(
        json.dumps({"is:pr is:open archived:false author:@me sort:updated-desc": found})
    )
    followed = cache_dir / "followed.json"
    before = followed.read_text() if followed.exists() else None
    followed.write_text(json.dumps(["acme/widgets", "acme/tools"]))
    (cache_dir / "board.json").unlink(missing_ok=True)
    try:
        page.goto(server_url + "/")
        home = page.frame_locator("#panes iframe.on")
        cards = home.locator("#board .card.pr")
        expect(cards).to_have_count(2)
        home.locator('[data-repo="acme/tools"]').click()
        expect(cards).to_have_count(1)
        expect(cards.first).to_have_attribute("href", "/acme/tools/pull/2")
        expect(home.locator('[data-repo="acme/tools"] .count')).to_have_text("1")
        page.reload()
        home = page.frame_locator("#panes iframe.on")
        expect(home.locator('[data-repo="acme/tools"]')).to_have_attribute("aria-pressed", "true")
        expect(home.locator("#board .card.pr")).to_have_count(1)
        home.get_by_role("button", name="Followed repositories").click()
        expect(page.locator('#settings [data-section="repositories"]')).to_be_in_viewport()
    finally:
        (gh_state / "search.json").unlink()
        (cache_dir / "board.json").unlink(missing_ok=True)
        if before is None:
            followed.unlink()
        else:
            followed.write_text(before)


def mine(add_pr: AddPr, gh_state: Path, merge: dict | None = None, **meta: object) -> str:
    """Open a pull request by the viewer, with the fields of meta over its metadata and merge as its merge<n>.json,
    and return the path of its page."""
    path = add_pr({"a.py": "x = 1\n"}, {"a.py": "x = 2\n"})
    number = path.rsplit("/", 1)[1]
    pr = gh_state / f"pr{number}.json"
    pr.write_text(json.dumps(json.loads(pr.read_text()) | {"author": {"login": "tester"}} | meta))
    if merge:
        (gh_state / f"merge{number}.json").write_text(json.dumps(merge))
    return path


def sent(gh_state: Path, mutation: str) -> list[dict]:
    """The variables of each mutation of that name the fake gh took, in order."""
    lines = (gh_state / "mutations.log").read_text().splitlines()
    return [m["variables"] for m in map(json.loads, lines) if f"{mutation}(" in m["query"]]


def check_run(name: str, status: str, conclusion: str | None, **extra: object) -> dict:
    return {
        "__typename": "CheckRun",
        "name": name,
        "status": status,
        "conclusion": conclusion,
        "startedAt": "2026-10-30T10:00:00Z",
        "completedAt": "2026-10-30T10:01:00Z" if status == "COMPLETED" else None,
        "detailsUrl": f"https://github.com/acme/widgets/actions/runs/41/job/{name}",
        "isRequired": False,
        "checkSuite": {"workflowRun": {"databaseId": 41, "workflow": {"name": "CI"}}},
        "steps": {"nodes": []},
        "annotations": {"nodes": []},
    } | extra


def with_checks(*runs: dict, **pr: object) -> dict:
    commits = {"totalCount": 2, "nodes": [{"commit": {"statusCheckRollup": {"contexts": {"nodes": list(runs)}}}}]}
    return {"pullRequest": {"commits": commits} | pr}


def test_someone_elses_pull_request_offers_review_and_the_viewers_own_offers_merge(
    page: Page, server_url: str, add_pr: AddPr, gh_state: Path
) -> None:
    open_pr(page, server_url)
    expect(page.locator("#side .head button[data-review-panel]")).to_be_visible()
    expect(page.locator("#side .head button[data-merge-panel]")).to_have_count(0)
    open_pr(page, server_url, mine(add_pr, gh_state))
    expect(page.locator("#side .head button[data-merge-panel]")).to_have_text("Ready to merge")
    expect(page.locator("#side .head button[data-review-panel]")).to_have_count(0)


def test_merging_sends_the_method_the_commit_message_and_the_pages_head_then_drops_the_button(
    page: Page, server_url: str, add_pr: AddPr, gh_state: Path
) -> None:
    open_pr(page, server_url, mine(add_pr, gh_state))
    page.click("#side button[data-merge-panel]")
    page.locator("textarea[data-merge-text='SQUASH']").fill("Rename the helper\n\nAnd greet.")
    page.on("dialog", lambda dialog: dialog.accept())
    page.click("button[data-merge-do='merge']")
    expect(page.locator("#side .head button[data-merge-panel]")).to_have_count(0)
    expect(page.locator("#side .head .status")).to_have_text("Merged")
    assert page.evaluate("M.timer") == 0, "a merged pull request has nothing left to poll for"
    assert sent(gh_state, "mergePullRequest")[-1] == {
        "pr": f"PR_{page.evaluate('PR.number')}",
        "method": "SQUASH",
        "head": page.evaluate("PR.head"),
        "headline": "Rename the helper",
        "body": "And greet.",
    }


def test_the_merge_method_chosen_in_the_menu_is_sent_and_kept_for_the_repository(
    page: Page, server_url: str, add_pr: AddPr, gh_state: Path
) -> None:
    path = mine(add_pr, gh_state)
    open_pr(page, server_url, path)
    page.click("#side button[data-merge-panel]")
    page.click("button[data-merge-menu]")
    page.click("button[data-merge-method='REBASE']")
    expect(page.locator("button[data-merge-do='merge']")).to_have_text("Rebase and merge")
    expect(page.locator("textarea[data-merge-text]")).to_have_count(0)
    open_pr(page, server_url, mine(add_pr, gh_state))
    page.click("#side button[data-merge-panel]")
    page.on("dialog", lambda dialog: dialog.accept())
    page.click("button[data-merge-do='merge']")
    expect(page.locator("#side .head .status")).to_have_text("Merged")
    last = sent(gh_state, "mergePullRequest")[-1]
    assert (last["method"], last["headline"], last["body"]) == ("REBASE", None, None)


def test_an_empty_commit_message_leaves_the_message_to_github(
    page: Page, server_url: str, add_pr: AddPr, gh_state: Path
) -> None:
    open_pr(page, server_url, mine(add_pr, gh_state))
    page.click("#side button[data-merge-panel]")
    expect(page.locator("textarea[data-merge-text='SQUASH']")).to_have_value("")
    page.on("dialog", lambda dialog: dialog.accept())
    page.click("button[data-merge-do='merge']")
    expect(page.locator("#side .head .status")).to_have_text("Merged")
    last = sent(gh_state, "mergePullRequest")[-1]
    assert (last["method"], last["headline"], last["body"]) == ("SQUASH", None, None)


def test_a_declined_merge_sends_nothing(page: Page, server_url: str, add_pr: AddPr, gh_state: Path) -> None:
    open_pr(page, server_url, mine(add_pr, gh_state))
    before = len(sent(gh_state, "mergePullRequest"))
    page.click("#side button[data-merge-panel]")
    page.on("dialog", lambda dialog: dialog.dismiss())
    page.click("button[data-merge-do='merge']")
    expect(page.locator("#side .head button[data-merge-panel]")).to_have_text("Ready to merge")
    assert len(sent(gh_state, "mergePullRequest")) == before


def test_while_github_blocks_the_merge_auto_merge_turns_on_and_off(
    page: Page, server_url: str, add_pr: AddPr, gh_state: Path
) -> None:
    running = with_checks(check_run("unit", "IN_PROGRESS", None), mergeStateStatus="BLOCKED")
    open_pr(page, server_url, mine(add_pr, gh_state, running))
    on, off = len(sent(gh_state, "enablePullRequestAutoMerge")), len(sent(gh_state, "disablePullRequestAutoMerge"))
    button = page.locator("#side .head button[data-merge-panel]")
    expect(button).to_have_text("Checks running")
    button.click()
    expect(page.locator("button[data-merge-do='merge']")).to_have_count(0)
    page.click("button[data-merge-do='autoon']")
    expect(button).to_have_text("Auto-merge on")
    assert [v["method"] for v in sent(gh_state, "enablePullRequestAutoMerge")[on:]] == ["SQUASH"]
    page.click("button[data-merge-do='autooff']")
    expect(page.locator("button[data-merge-do='autoon']")).to_be_visible()
    assert len(sent(gh_state, "disablePullRequestAutoMerge")) == off + 1


def test_a_running_check_github_does_not_wait_for_leaves_the_merge_to_go_out_now(
    page: Page, server_url: str, add_pr: AddPr, gh_state: Path
) -> None:
    running = with_checks(check_run("unit", "IN_PROGRESS", None), mergeStateStatus="UNSTABLE")
    open_pr(page, server_url, mine(add_pr, gh_state, running))
    page.click("#side button[data-merge-panel]")
    expect(page.locator("button[data-merge-do='merge']")).to_be_enabled()
    expect(page.locator("button[data-merge-do='autoon']")).to_have_count(0)


def test_auto_merge_finishing_during_a_poll_drops_the_button_and_shows_merged(
    page: Page, server_url: str, add_pr: AddPr, gh_state: Path
) -> None:
    auto = {"mergeMethod": "SQUASH", "enabledAt": "2026-10-30T10:00:00Z", "enabledBy": {"login": "tester"}}
    running = with_checks(check_run("unit", "IN_PROGRESS", None), mergeStateStatus="BLOCKED", autoMergeRequest=auto)
    path = mine(add_pr, gh_state, running)
    open_pr(page, server_url, path)
    expect(page.locator("#side .head button[data-merge-panel]")).to_have_text("Auto-merge on")
    page.click("#side button[data-merge-panel]")
    number = path.rsplit("/", 1)[1]
    pr = gh_state / f"pr{number}.json"
    pr.write_text(json.dumps(json.loads(pr.read_text()) | {"state": "MERGED"}))
    (gh_state / f"merge{number}.json").write_text(json.dumps(with_checks(check_run("unit", "COMPLETED", "SUCCESS"))))
    page.evaluate("loadMerge()")
    expect(page.locator("#side .head .status")).to_have_text("Merged")
    expect(page.locator("#side .head button[data-merge-panel]")).to_have_count(0)
    expect(page.locator(".form.merge")).to_have_count(0)


def test_a_failed_required_check_blocks_the_merge_and_reruns_its_failed_jobs(
    page: Page, server_url: str, add_pr: AddPr, gh_state: Path
) -> None:
    failed = check_run(
        "e2e",
        "COMPLETED",
        "FAILURE",
        isRequired=True,
        steps={"nodes": [{"name": "Run pytest", "conclusion": "FAILURE"}]},
        annotations={"nodes": [{"message": "AssertionError: expected 3", "annotationLevel": "FAILURE"}]},
    )
    open_pr(page, server_url, mine(add_pr, gh_state, with_checks(failed, mergeStateStatus="BLOCKED")))
    page.click("#side button[data-merge-panel]")
    expect(page.locator("button[data-merge-do='merge']")).to_be_disabled()
    expect(page.locator(".form.merge .hint.err, .form.merge .hint .err")).to_have_text(
        "The required check CI / e2e failed."
    )
    expect(page.locator(".checks .fail pre")).to_have_text("AssertionError: expected 3")
    page.click("button[data-merge-rerun]")
    expect(page.locator("button[data-merge-rerun]")).to_be_enabled()
    calls = [json.loads(line) for line in (gh_state / "calls.log").read_text().splitlines()]
    assert ["api", "-X", "POST", "repos/acme/widgets/actions/runs/41/rerun-failed-jobs"] in calls


def test_a_refused_rerun_shows_why_and_leaves_the_panel_usable(
    page: Page, server_url: str, add_pr: AddPr, gh_state: Path
) -> None:
    failed = check_run("e2e", "COMPLETED", "FAILURE")
    open_pr(page, server_url, mine(add_pr, gh_state, with_checks(failed, mergeStateStatus="BLOCKED")))
    page.click("#side button[data-merge-panel]")
    (gh_state / "fail.txt").write_text("api -X POST\n")
    try:
        page.click("button[data-merge-rerun]")
        expect(page.locator(".form.merge .hint.err").last).to_contain_text("injected failure")
        expect(page.locator("button[data-merge-rerun]")).to_be_enabled()
    finally:
        (gh_state / "fail.txt").unlink()


def test_a_draft_offers_ready_for_review(page: Page, server_url: str, add_pr: AddPr, gh_state: Path) -> None:
    open_pr(page, server_url, mine(add_pr, gh_state, isDraft=True))
    button = page.locator("#side .head button[data-merge-panel]")
    expect(button).to_have_text("Draft")
    before = len(sent(gh_state, "markPullRequestReadyForReview"))
    button.click()
    page.click("button[data-merge-do='ready']")
    expect(page.locator("#side .head .status")).to_have_text("Open")
    expect(button).to_have_text("Ready to merge")
    assert len(sent(gh_state, "markPullRequestReadyForReview")) == before + 1
