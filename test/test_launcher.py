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

import contextlib
import importlib.machinery
import importlib.util
import io
import os
import re
import struct
import subprocess
import sys
import tempfile
import types
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

import runner


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

    def test_every_command_has_a_description_and_the_risky_ones_a_note_or_none(self):
        for p in launcher.CATALOGO:
            self.assertTrue(p.desc.strip(), f"{p.title}: manca la descrizione")
            self.assertLessEqual(len(p.desc), 130, f"{p.title}: descrizione troppo lunga per la riga")


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


class AskTest(unittest.TestCase):
    def da_chiedere(self, title, params):
        p = next(p for p in launcher.CATALOGO if p.title == title)
        return [chiave for chiave, _, _ in launcher.da_chiedere(p, params)]

    def test_username_and_team_are_asked_only_when_missing(self):
        self.assertEqual(self.da_chiedere("Rimuovi lo studente", {}), ["user"])
        self.assertEqual(self.da_chiedere("Rimuovi lo studente", {"user": "mario_rossi"}), [])
        self.assertEqual(self.da_chiedere("Rimuovi da un solo team", {"user": " "}), ["user", "team"])

    def test_changelist_workspace_and_account_list_are_asked_every_time(self):
        # Il numero dell'ultima volta non deve partire da solo su una cancellazione.
        for title, chiave in (("Cancella una changelist, anche di un altro utente", "change"),
                              ("Cancella un workspace, anche di un altro utente", "client"),
                              ("Rimuovi gli account orfani", "keep")):
            self.assertEqual(self.da_chiedere(title, {chiave: "gia-compilato", "user": "x", "team": "y"}), [chiave])

    def test_commands_with_nothing_to_fill_ask_nothing(self):
        self.assertEqual(self.da_chiedere("Provisioning", {}), [])
        self.assertEqual(self.da_chiedere("Info sul server", {}), [])


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


class TokenTest(unittest.TestCase):
    def test_token_goes_only_to_the_scripts_that_ask_for_it(self):
        # Chi lo chiede: tutti e soli gli script che chiamano get_admin_token. Gli altri comandi (p4, npx che
        # scarica pacchetti di terzi, prune, test) non devono riceverlo nell'ambiente.
        chiedono = {f"scripts/{path.name}" for path in SCRIPTS.glob("*.py")
                    if path.name != "naba_store.py" and "get_admin_token(" in path.read_text(encoding="utf-8")}
        self.assertEqual(chiedono, launcher.SCRIPT_CON_TOKEN)

    def test_which_commands_receive_it(self):
        by_title = {p.title: p for p in launcher.CATALOGO}
        for title in ("Provisioning", "Rimuovi lo studente", "Stato del KV", "Importa nel KV"):
            self.assertTrue(launcher.usa_token(by_title[title]), title)
        for title in ("Rimuovi gli account orfani", "Info sul server", "Pubblica il Worker",
                      "Cambia il token admin del Worker", "Test degli script", "Test del Worker e del form"):
            self.assertFalse(launcher.usa_token(by_title[title]), title)

    def test_launch_hands_it_over_by_environment_never_on_the_command_line(self):
        with mock.patch.dict(os.environ), mock.patch.object(launcher.subprocess, "Popen") as popen:
            os.environ.pop("NABA_ADMIN_TOKEN", None)
            launcher.launch(["python", "scripts/kv_status.py"], token="t0k3n-segreto")
            args, kwargs = popen.call_args
            self.assertEqual(kwargs["env"]["NABA_ADMIN_TOKEN"], "t0k3n-segreto")
            self.assertNotIn("t0k3n-segreto", " ".join(args[0]))
            launcher.launch(["python", "scripts/kv_status.py"])
            self.assertNotIn("NABA_ADMIN_TOKEN", popen.call_args[1]["env"])

    def test_login_asks_the_worker_without_pulling_personal_data(self):
        # Lo status usato per il controllo non esiste: la lettura è autenticata ma l'elenco torna vuoto.
        self.assertNotIn(launcher.STATUS_DI_PROVA, launcher.naba_store.VALID_STATUS)
        with mock.patch.object(launcher.naba_store, "fetch_users", return_value=[]) as fetch:
            launcher.verifica_token("tok")
        fetch.assert_called_once_with("tok", status=launcher.STATUS_DI_PROVA)

    def test_a_rejected_or_unreachable_worker_is_reported(self):
        errore = launcher.naba_store.StoreError("token admin rifiutato (401). Verifica ...")
        with mock.patch.object(launcher.naba_store, "fetch_users", side_effect=errore):
            with self.assertRaisesRegex(launcher.LauncherError, "rifiutato"):
                launcher.verifica_token("sbagliato")


class RunnerTest(unittest.TestCase):
    """runner.py è ciò che gira nell'eseguibile al posto di «python scripts/<nome>.py»."""

    def test_runs_the_script_with_its_own_arguments(self):
        visti = []
        finto = types.SimpleNamespace(main=lambda: visti.append(list(sys.argv)))
        with mock.patch.object(runner, "carica", return_value=finto):
            self.assertEqual(runner.main(["kv_status", "--usernames"]), 0)
        self.assertEqual(visti, [["kv_status.py", "--usernames"]])

    def test_an_unknown_name_is_refused_and_the_choices_are_listed(self):
        fuori = io.StringIO()
        with contextlib.redirect_stdout(fuori):
            self.assertEqual(runner.main(["calc"]), 2)
            self.assertEqual(runner.main([]), 2)
        for nome in runner.SCRIPT:
            self.assertIn(nome, fuori.getvalue())

    def test_a_real_script_answers_to_help(self):
        fuori = io.StringIO()
        with contextlib.redirect_stdout(fuori), self.assertRaises(SystemExit) as uscita:
            runner.main(["kv_status", "--help"])
        self.assertEqual(uscita.exception.code, 0)
        self.assertIn("--usernames", fuori.getvalue())

    def test_it_runs_every_script_the_catalog_uses_and_nothing_missing(self):
        usati = {Path(t).stem for p in launcher.CATALOGO for t in launcher.parole(p.argv) if t.startswith("scripts/")}
        self.assertEqual(usati, set(runner.SCRIPT))
        for nome in runner.SCRIPT:
            self.assertTrue((SCRIPTS / f"{nome}.py").is_file(), nome)


class ExecutableTest(unittest.TestCase):
    def setUp(self):
        self.cartella = tempfile.TemporaryDirectory(prefix="p4 tool ")  # lo spazio è voluto: un percorso vero ne ha
        self.addCleanup(self.cartella.cleanup)
        (Path(self.cartella.name) / launcher.RUNNER).write_bytes(b"")
        patcher = mock.patch.multiple(launcher, IN_ESEGUIBILE=True, CARTELLA=Path(self.cartella.name))
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_python_scripts_become_the_runner_next_to_the_executable(self):
        runner_exe = str(Path(self.cartella.name) / launcher.RUNNER)
        self.assertEqual(launcher.per_eseguibile(["python", "scripts/kv_status.py", "--usernames"]),
                         [runner_exe, "kv_status", "--usernames"])

    def test_the_list_shows_what_actually_runs(self):
        self.assertEqual(launcher.visibile(["python", "scripts/kv_status.py", "--usernames"]),
                         ["p4-runner", "kv_status", "--usernames"])
        self.assertEqual(launcher.visibile(["p4", "info"]), ["p4", "info"])

    def test_other_commands_are_left_alone(self):
        for argv in (["p4", "info"], ["npm", "test"], ["python", "-m", "unittest", "discover", "-s", "test"]):
            self.assertEqual(launcher.per_eseguibile(argv), argv)

    def test_a_missing_runner_is_reported(self):
        (Path(self.cartella.name) / launcher.RUNNER).unlink()
        with self.assertRaisesRegex(launcher.LauncherError, "p4-runner.exe"):
            launcher.per_eseguibile(["python", "scripts/kv_status.py"])

    def test_a_path_with_spaces_is_called_not_read_as_text(self):
        with mock.patch.object(launcher.subprocess, "Popen") as popen:
            launcher.launch(["python", "scripts/kv_status.py", "--usernames"])
        args, kwargs = popen.call_args
        riga = args[0][-1]
        self.assertTrue(riga.startswith("& '"), riga)
        self.assertTrue(riga.endswith("p4-runner.exe' kv_status --usernames"), riga)
        self.assertEqual(Path(kwargs["cwd"]), Path(self.cartella.name))

    def test_commands_that_need_the_repo_and_node_are_left_out(self):
        titoli = {p.title for p in launcher.comandi(True)}
        for fuori in ("Test del Worker e del form", "Test degli script", "Pubblica il Worker",
                      "Cambia il token admin del Worker"):
            self.assertNotIn(fuori, titoli)
        self.assertIn("Stato del KV", titoli)
        self.assertIn("Info sul server", titoli)
        self.assertEqual(len(launcher.comandi(False)), len(launcher.CATALOGO))

    def test_every_area_keeps_something_to_show(self):
        self.assertEqual({p.area for p in launcher.comandi(True)}, set(launcher.AREE))


class SourceModeTest(unittest.TestCase):
    def test_from_source_nothing_changes(self):
        self.assertFalse(launcher.IN_ESEGUIBILE)
        argv = ["python", "scripts/kv_status.py", "--usernames"]
        self.assertEqual(launcher.per_eseguibile(argv), argv)
        self.assertEqual(launcher.visibile(argv), argv)


class IconTest(unittest.TestCase):
    def test_the_icon_has_every_size_windows_asks_for(self):
        dati = (SCRIPTS / "icona.ico").read_bytes()
        _, tipo, quante = struct.unpack("<HHH", dati[:6])
        larghezze = {(dati[6 + 16 * i] or 256) for i in range(quante)}
        self.assertEqual(tipo, 1)
        self.assertTrue({16, 32, 48, 256} <= larghezze, larghezze)
        self.assertTrue(launcher.ICONA.is_file())


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
