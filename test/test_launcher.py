#!/usr/bin/env python3
"""
test_launcher.py

Test del lanciatore (scripts/launcher.pyw): il catalogo dei comandi, la
costruzione della riga PowerShell e il lancio. Nessun test apre una finestra
né esegue un comando vero.

    python -m unittest discover -s test

Il catalogo è l'unico punto che può restare indietro rispetto agli script (è
già successo con --skip-discord): per questo ogni opzione che usa deve esistere
nel parser dello script a cui punta.
"""

import importlib.machinery
import importlib.util
import os
import re
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))


def load_launcher():
    # Il file è .pyw, così il doppio clic non apre una console: l'import normale non lo trova.
    loader = importlib.machinery.SourceFileLoader("launcher", str(SCRIPTS / "launcher.pyw"))
    spec = importlib.util.spec_from_loader("launcher", loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


launcher = load_launcher()


class CatalogTest(unittest.TestCase):
    def test_every_script_exists_and_knows_its_options(self):
        for p in launcher.CATALOGO:
            tokens = launcher.parole(p.argv)
            script = next((t for t in tokens if t.startswith("scripts/")), None)
            if script is None:
                continue
            path = SCRIPTS.parent / script
            self.assertTrue(path.is_file(), f"{p.title}: {script} non esiste")
            source = path.read_text(encoding="utf-8")
            for flag in (t for t in tokens if t.startswith("--")):
                self.assertIn(f'"{flag}"', source, f"{p.title}: {script} non ha {flag}")

    def test_risk_is_known_and_dry_runs_only_read(self):
        for p in launcher.CATALOGO:
            self.assertIn(p.risk, launcher.RISCHIO, p.title)
            if "--dry-run" in p.argv:
                self.assertEqual(p.risk, "read", f"{p.title}: un'anteprima non modifica niente")

    def test_titles_are_unique(self):
        titles = [p.title for p in launcher.CATALOGO]
        self.assertEqual(len(titles), len(set(titles)))

    def test_first_word_runs_without_quoting(self):
        # Un comando quotato in PowerShell sarebbe una stringa, non una chiamata.
        for p in launcher.CATALOGO:
            self.assertEqual(launcher.ps_quote(p.argv[0]), p.argv[0], p.title)

    def test_dry_runs_do_not_announce_questions_asked_only_when_running(self):
        by_title = {p.title: p for p in launcher.CATALOGO}
        self.assertNotIn("CONFIRM", launcher.domande(by_title["Anteprima della rimozione"]))
        self.assertIn("CONFIRM", launcher.domande(by_title["Rimuovi lo studente"]))


class BuildTest(unittest.TestCase):
    def preset(self, title):
        return next(p for p in launcher.CATALOGO if p.title == title)

    def test_placeholders_are_replaced(self):
        argv = launcher.build_argv(self.preset("Rimuovi da un solo team").argv,
                                   {"user": " mario_rossi ", "team": "ProjectAlpha"})
        self.assertEqual(argv, ["python", "scripts/perforce_cleanup.py", "--user", "mario_rossi",
                                "--team", "ProjectAlpha"])

    def test_optional_group_is_dropped_when_empty_and_kept_when_filled(self):
        p = self.preset("Rimuovi gli account orfani")
        self.assertEqual(launcher.build_argv(p.argv, {}), ["python", "scripts/perforce_prune.py"])
        self.assertEqual(launcher.build_argv(p.argv, {"keep": "admin, servicebot"}),
                         ["python", "scripts/perforce_prune.py", "--keep", "admin,servicebot"])

    def test_missing_required_value_asks_for_it(self):
        with self.assertRaisesRegex(launcher.LauncherError, "Compila lo username"):
            launcher.build_argv(self.preset("Rimuovi lo studente").argv, {"user": "  "})
        with self.assertRaisesRegex(launcher.LauncherError, "Compila il team"):
            launcher.build_argv(self.preset("Cancella il depot del team").argv, {})

    def test_names_that_could_inject_are_rejected(self):
        argv = self.preset("Rimuovi lo studente").argv
        for bad in ("mario; Remove-Item *", "a b", "-D", "..", "x'y", "a&b", "$(calc)"):
            with self.assertRaises(launcher.LauncherError, msg=bad):
                launcher.build_argv(argv, {"user": bad})

    def test_every_name_in_the_keep_list_is_checked(self):
        p = self.preset("Rimuovi gli account orfani")
        with self.assertRaises(launcher.LauncherError):
            launcher.build_argv(p.argv, {"keep": "admin,bad name"})

    def test_preview_shows_placeholders_instead_of_failing(self):
        argv = launcher.build_argv(self.preset("Anteprima della rimozione").argv, {}, strict=False)
        self.assertEqual(" ".join(argv),
                         "python scripts/perforce_cleanup.py --user <username> --dry-run")


class PowerShellLineTest(unittest.TestCase):
    def test_plain_words_are_left_alone(self):
        argv = ["python", "scripts/perforce_cleanup.py", "--user", "mario_rossi", "--dry-run"]
        self.assertEqual(launcher.ps_line(argv), " ".join(argv))
        self.assertEqual(launcher.ps_quote("wrangler@4.150.0"), "wrangler@4.150.0")

    def test_lists_and_quotes_are_single_quoted(self):
        # In PowerShell a,b senza apici è un array, non una stringa.
        self.assertEqual(launcher.ps_quote("a,b"), "'a,b'")
        self.assertEqual(launcher.ps_quote("it's"), "'it''s'")


class LaunchTest(unittest.TestCase):
    def test_opens_a_console_in_the_repo_and_keeps_it_open(self):
        with mock.patch.object(launcher.subprocess, "Popen") as popen:
            launcher.launch(["python", "scripts/kv_status.py", "--usernames"])
        args, kwargs = popen.call_args
        self.assertEqual(args[0], ["powershell.exe", "-NoLogo", "-NoExit", "-Command",
                                   "python scripts/kv_status.py --usernames"])
        self.assertEqual(Path(kwargs["cwd"]), SCRIPTS.parent)
        self.assertEqual(kwargs["env"]["PYTHONUTF8"], "1")
        self.assertTrue(kwargs["creationflags"] & 0x10)  # CREATE_NEW_CONSOLE
        self.assertNotIn("shell", kwargs)


if __name__ == "__main__":
    unittest.main()
