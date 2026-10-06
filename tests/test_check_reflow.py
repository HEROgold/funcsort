from scripts.check_reflow import ExitCode, find_reflows, parse_hunks

REWRAP = """\
diff --git a/src/funcsort/config.py b/src/funcsort/config.py
--- a/src/funcsort/config.py
+++ b/src/funcsort/config.py
@@ -15,2 +15,2 @@ module docstring
-followed by its attribute docstring. Only a matching command-line flag, if the value should have one, lives elsewhere
-(:mod:`funcsort.main`).
+followed by its attribute docstring. Only a matching command-line flag, if the value
+should have one, lives elsewhere (:mod:`funcsort.main`).
"""

REAL_CHANGE = """\
diff --git a/README.md b/README.md
--- a/README.md
+++ b/README.md
@@ -3 +3 @@
-Sorts functions.
+Sorts functions and methods.
@@ -10,0 +11 @@
+New line.
@@ -20 +20,0 @@
-Removed line.
"""


class TestFindReflows:
    def test_rewrapped_prose_is_rejected(self) -> None:
        report = find_reflows(REWRAP)
        assert [(h.path, h.line) for h in report.reflows] == [("src/funcsort/config.py", 15)]
        assert report.exit_code is ExitCode.REFLOW_FOUND

    def test_reindent_is_rejected(self) -> None:
        diff = "+++ b/a.py\n@@ -1 +1 @@\n-    x = 1\n+        x = 1\n"
        assert find_reflows(diff).exit_code is ExitCode.REFLOW_FOUND

    def test_word_changes_pure_additions_and_deletions_pass(self) -> None:
        report = find_reflows(REAL_CHANGE)
        assert report.reflows == ()
        assert report.exit_code is ExitCode.OK

    def test_empty_diff_passes(self) -> None:
        assert find_reflows("").exit_code is ExitCode.OK


class TestParseHunks:
    def test_hunks_are_split_per_header_and_file(self) -> None:
        hunks = parse_hunks(REWRAP + REAL_CHANGE)
        assert [(h.path, h.line) for h in hunks] == [
            ("src/funcsort/config.py", 15),
            ("README.md", 3),
            ("README.md", 11),
            ("README.md", 20),
        ]

    def test_no_newline_marker_is_ignored(self) -> None:
        diff = "+++ b/a.txt\n@@ -1 +1 @@\n-a  b\n\\ No newline at end of file\n+a b\n\\ No newline at end of file\n"
        (hunk,) = parse_hunks(diff)
        assert hunk.removed == ("a  b",)
        assert hunk.added == ("a b",)
