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

    def test_every_option_taking_the_account_list_has_a_field_label(self):
        # Il campo compare solo per questi comandi e prende il nome dall'opzione.
        for p in launcher.CATALOGO:
            for voce in p.argv:
                if isinstance(voce, tuple) and "{keep}" in voce:
                    self.assertIn(voce[0], launcher.ELENCO, p.title)

    def test_each_command_sits_under_the_system_it_touches(self):
        # Worker: il KV (kv_status.py) e Cloudflare (wrangler, npm test). p4: i comandi diretti del
        # client. Perforce: gli script, che chiedono da soli server e password.
        for p in launcher.CATALOGO:
            if p.argv[0] in ("npm", "npx") or "scripts/kv_status.py" in p.argv:
                atteso = "Worker"
            elif p.argv[0] == "p4":
                atteso = "p4"
            else:
                atteso = "Perforce"
            self.assertEqual(p.area, atteso, p.title)

    def test_commands_of_one_section_are_listed_together(self):
        # La lista mette l'intestazione quando la sezione cambia: una sezione spezzata comparirebbe due volte.
        aree = [p.area for p in launcher.CATALOGO]
        self.assertEqual(aree, sorted(aree, key=lambda a: aree.index(a)))
        self.assertEqual(set(aree), set(launcher.AREE))

    def test_every_extra_value_a_command_needs_has_a_field(self):
        # Username e team stanno in testata; ogni altro valore ha il suo campo nel dettaglio.
        for p in launcher.CATALOGO:
            extra = launcher.segnaposti(p.argv) - {"user", "team"}
            self.assertLessEqual(len(extra), 1, p.title)
            self.assertEqual(launcher.campo_extra(p) is not None, bool(extra), p.title)

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

    def test_changelist_is_a_number_and_workspace_a_p4_name(self):
        p = self.preset("Cancella una changelist, anche di un altro utente")
        self.assertEqual(launcher.build_argv(p.argv, {"change": " 4821 "}),
                         ["p4", "change", "-d", "-f", "4821"])
        for bad in ("", "48a", "12; calc", "-1", "1 2"):
            with self.assertRaises(launcher.LauncherError, msg=bad):
                launcher.build_argv(p.argv, {"change": bad})
        w = self.preset("Cancella un workspace, anche di un altro utente")
        self.assertEqual(launcher.build_argv(w.argv, {"client": "mario_ws"}),
                         ["p4", "client", "-d", "-f", "mario_ws"])
        with self.assertRaises(launcher.LauncherError):
            launcher.build_argv(w.argv, {"client": "a b"})

    def test_a_placeholder_inside_a_word_is_replaced_and_checked(self):
        p = self.preset("Anteprima: cosa cancellerebbe sul depot del team")
        self.assertEqual(launcher.build_argv(p.argv, {"team": "ProjectAlpha"}),
                         ["p4", "obliterate", "//ProjectAlpha/..."])
        with self.assertRaises(launcher.LauncherError):
            launcher.build_argv(p.argv, {"team": "x/../y"})

    def test_preview_shows_placeholders_instead_of_failing(self):
        argv = launcher.build_argv(self.preset("Anteprima della rimozione").argv, {}, strict=False)
        self.assertEqual(" ".join(argv),
                         "python scripts/perforce_cleanup.py --user <username> --dry-run")


class ExtraFieldTest(unittest.TestCase):
    def campo(self, title):
        return launcher.campo_extra(next(p for p in launcher.CATALOGO if p.title == title))

    def test_the_account_list_is_named_after_its_option(self):
        self.assertEqual(self.campo("Rimuovi gli account orfani")[:2], ("keep", "Account da salvare"))
        self.assertEqual(self.campo("Importa nel KV")[:2], ("keep", "Account da non esportare"))

    def test_changelist_and_workspace_have_their_own_field(self):
        self.assertEqual(self.campo("Cancella una changelist, anche di un altro utente")[:2],
                         ("change", "Numero della changelist"))
        self.assertEqual(self.campo("Cancella un workspace, anche di un altro utente")[:2],
                         ("client", "Workspace"))

    def test_no_field_when_the_command_needs_nothing_extra(self):
        self.assertIsNone(self.campo("Provisioning"))
        self.assertIsNone(self.campo("Gruppi di un utente"))


class PowerShellLineTest(unittest.TestCase):
    def test_plain_words_are_left_alone(self):
        argv = ["python", "scripts/perforce_cleanup.py", "--user", "mario_rossi", "--dry-run"]
        self.assertEqual(launcher.ps_line(argv), " ".join(argv))
        self.assertEqual(launcher.ps_quote("wrangler@4.150.0"), "wrangler@4.150.0")

    def test_depot_paths_are_not_quoted(self):
        self.assertEqual(launcher.ps_quote("//ProjectAlpha/..."), "//ProjectAlpha/...")

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
