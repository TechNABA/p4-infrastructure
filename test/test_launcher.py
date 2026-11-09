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
import datetime
import importlib.machinery
import importlib.util
import io
import os
import struct
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
            self.assertEqual({k for k, _, _ in launcher.campi_extra(p)}, extra, p.title)

    def test_titles_are_unique(self):
        titles = [p.title for p in launcher.CATALOGO]
        self.assertEqual(len(titles), len(set(titles)))

    def test_first_word_runs_without_quoting(self):
        # Un comando quotato in PowerShell sarebbe una stringa, non una chiamata.
        for p in launcher.CATALOGO:
            self.assertEqual(launcher.ps_quote(p.argv[0]), p.argv[0], p.title)

    def test_every_command_has_a_short_description(self):
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
    def campi(self, title):
        return [c[:2] for c in launcher.campi_extra(next(p for p in launcher.CATALOGO if p.title == title))]

    def test_the_account_list_is_named_after_its_option(self):
        self.assertEqual(self.campi("Rimuovi gli account orfani"), [("keep", "Account da salvare")])
        self.assertEqual(self.campi("Importa nel KV"), [("keep", "Account da non esportare")])

    def test_changelist_and_workspace_have_their_own_field(self):
        self.assertEqual(self.campi("Cancella una changelist, anche di un altro utente"),
                         [("change", "Numero della changelist")])
        self.assertEqual(self.campi("Cancella un workspace, anche di un altro utente"), [("client", "Workspace")])

    def test_the_changelist_revert_asks_for_changelist_and_workspace(self):
        titolo = "Revert dei file di una changelist, anche di un altro utente"
        self.assertEqual([k for k, _ in self.campi(titolo)], ["change", "client"])
        p = next(p for p in launcher.CATALOGO if p.title == titolo)
        self.assertEqual(launcher.build_argv(p.argv, {"change": "123", "client": "mario_ws"}),
                         ["p4", "revert", "-C", "mario_ws", "-c", "123", "//..."])
        self.assertEqual(p.risk, "destroy")

    def test_no_field_when_the_command_needs_nothing_extra(self):
        self.assertEqual(self.campi("Provisioning"), [])
        self.assertEqual(self.campi("Gruppi di un utente"), [])


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


class P4AccessTest(unittest.TestCase):
    def test_server_and_port_become_p4port(self):
        self.assertEqual(launcher.indirizzo(" 10.0.0.5 ", "1666"), "10.0.0.5:1666")
        self.assertEqual(launcher.indirizzo("ssl:perforce.naba", "1667"), "ssl:perforce.naba:1667")
        for server, porta in (("10.0.0.5:1666", "1666"), ("", "1666"), ("a b", "1666"), ("x;calc", "1666"),
                              ("10.0.0.5", ""), ("10.0.0.5", "0"), ("10.0.0.5", "70000"), ("10.0.0.5", "16a")):
            with self.assertRaises(launcher.LauncherError, msg=(server, porta)):
                launcher.indirizzo(server, porta)

    def test_all_empty_means_no_access_and_partial_is_refused(self):
        self.assertIsNone(launcher.accesso_p4("", "1666", " ", ""))
        with self.assertRaisesRegex(launcher.LauncherError, "compila"):
            launcher.accesso_p4("10.0.0.5", "1666", "admin", "")

    def test_password_goes_to_p4_login_on_stdin_never_on_the_command_line(self):
        esito = types.SimpleNamespace(returncode=0, stdout="User admin logged in.", stderr="")
        with mock.patch.object(launcher.subprocess, "run", return_value=esito) as run:
            env = launcher.accesso_p4("10.0.0.5", "1666", "admin", "s3greta")
        args, kwargs = run.call_args
        self.assertEqual(args[0], ["p4", "-p", "10.0.0.5:1666", "-u", "admin", "login"])
        self.assertEqual(kwargs["input"], "s3greta\n")
        self.assertNotIn("s3greta", " ".join(args[0]))
        self.assertEqual(env, {"P4PORT": "10.0.0.5:1666", "P4USER": "admin", "P4PASSWD": "s3greta"})

    def test_a_refused_or_unreachable_server_is_reported(self):
        rifiutato = types.SimpleNamespace(returncode=1, stdout="", stderr="Password invalid.")
        with mock.patch.object(launcher.subprocess, "run", return_value=rifiutato):
            with self.assertRaisesRegex(launcher.LauncherError, "Password invalid"):
                launcher.verifica_p4("10.0.0.5:1666", "admin", "sbagliata")
        with mock.patch.object(launcher.subprocess, "run", side_effect=launcher.subprocess.TimeoutExpired("p4", 30)):
            with self.assertRaisesRegex(launcher.LauncherError, "non risponde"):
                launcher.verifica_p4("10.0.0.5:1666", "admin", "x")
        with mock.patch.object(launcher.subprocess, "run", side_effect=FileNotFoundError("p4")):
            with self.assertRaisesRegex(launcher.LauncherError, "PATH"):
                launcher.verifica_p4("10.0.0.5:1666", "admin", "x")

    def test_only_perforce_and_p4_commands_receive_it(self):
        by_title = {p.title: p for p in launcher.CATALOGO}
        for title in ("Provisioning", "Rimuovi gli account orfani", "Info sul server", "Importa nel KV"):
            self.assertTrue(launcher.usa_p4(by_title[title]), title)
        for title in ("Stato del KV", "Pubblica il Worker", "Test degli script", "Test del Worker e del form"):
            self.assertFalse(launcher.usa_p4(by_title[title]), title)

    def test_launch_hands_it_over_by_environment(self):
        p4 = {"P4PORT": "10.0.0.5:1666", "P4USER": "admin", "P4PASSWD": "s3greta"}
        with mock.patch.object(launcher.subprocess, "Popen") as popen:
            launcher.launch(["p4", "info"], p4=p4)
        args, kwargs = popen.call_args
        self.assertEqual({k: kwargs["env"][k] for k in p4}, p4)
        self.assertNotIn("s3greta", " ".join(args[0]))


class ConfirmTest(unittest.TestCase):
    def preset(self, title):
        return next(p for p in launcher.CATALOGO if p.title == title)

    def test_destroy_asks_to_retype_the_main_value(self):
        params = {"user": " mario_rossi ", "team": "Alpha", "change": "123", "client": "ws"}
        for title, atteso in (("Cancella una changelist, anche di un altro utente", ("Numero della changelist", "123")),
                              ("Revert dei file di una changelist, anche di un altro utente",
                               ("Numero della changelist", "123")),
                              ("Cancella un workspace, anche di un altro utente", ("Workspace", "ws")),
                              ("Rimuovi da un solo team", ("Username", "mario_rossi")),
                              ("Cancella il depot del team", ("Team", "Alpha"))):
            self.assertEqual(launcher.da_riscrivere(self.preset(title), params), atteso, title)

    def test_nothing_to_retype_without_a_value_or_when_not_destroying(self):
        self.assertIsNone(launcher.da_riscrivere(self.preset("Rimuovi gli account orfani"), {}))
        self.assertIsNone(launcher.da_riscrivere(self.preset("Provisioning"), {}))
        self.assertIsNone(launcher.da_riscrivere(self.preset("Sblocca i file di una changelist"), {"change": "1"}))

    def test_values_from_the_header_are_shown_before_running(self):
        p = self.preset("Rimuovi da un solo team")
        self.assertEqual(launcher.valori_usati(p, {"user": "mario_rossi", "team": "Alpha"}),
                         [("Username", "mario_rossi"), ("Team", "Alpha")])
        self.assertEqual(launcher.valori_usati(self.preset("Rimuovi gli account orfani"), {"keep": ""}),
                         [("Account da salvare", "nessuno")])
        self.assertEqual(launcher.valori_usati(self.preset("Provisioning"), {}), [])


class RememberAccessTest(unittest.TestCase):
    def test_server_port_and_user_are_kept_but_never_the_password(self):
        with tempfile.TemporaryDirectory() as d:
            file = Path(d) / "sotto" / "accesso.json"
            launcher.salva_accesso(file, {"server": " 10.0.0.5 ", "porta": "1666", "p4user": "admin",
                                          "p4pass": "s3greta", "token": "t0k3n"})
            self.assertNotIn("s3greta", file.read_text(encoding="utf-8"))
            self.assertNotIn("t0k3n", file.read_text(encoding="utf-8"))
            self.assertEqual(launcher.leggi_accesso(file), {"server": "10.0.0.5", "porta": "1666", "p4user": "admin"})

    def test_a_missing_or_broken_file_is_empty(self):
        with tempfile.TemporaryDirectory() as d:
            file = Path(d) / "accesso.json"
            self.assertEqual(launcher.leggi_accesso(file), {})
            for contenuto in ("non json", "[1, 2]", '{"server": 5, "p4pass": "x"}'):
                file.write_text(contenuto, encoding="utf-8")
                self.assertEqual(launcher.leggi_accesso(file), {}, contenuto)

    def test_a_failed_save_does_not_stop_the_login(self):
        with tempfile.TemporaryDirectory() as d:
            occupato = Path(d) / "file"
            occupato.write_text("")
            launcher.salva_accesso(occupato / "accesso.json", {"server": "x"})  # la cartella è un file


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


class BuildDateTest(unittest.TestCase):
    """L'eseguibile porta una copia degli script: la finestra deve far vedere quanto è vecchia."""

    def setUp(self):
        self.cartella = tempfile.TemporaryDirectory()
        self.addCleanup(self.cartella.cleanup)
        self.dir = Path(self.cartella.name)

    def crea(self, nome, mtime):
        percorso = self.dir / nome
        percorso.write_text("x")
        os.utime(percorso, (mtime, mtime))
        return percorso

    def test_the_oldest_of_the_two_executables_counts(self):
        gui, runner_exe = self.crea("gui.exe", 5000), self.crea("p4-runner.exe", 3000)
        self.assertEqual(launcher.momento_costruzione([gui, runner_exe]), 3000)
        self.assertEqual(launcher.momento_costruzione([gui, self.dir / "manca.exe"]), 5000)
        self.assertIsNone(launcher.momento_costruzione([self.dir / "manca.exe"]))

    def test_the_date_written_by_the_build_wins_over_the_file_dates(self):
        gui = self.crea("gui.exe", 5000)  # scaricato: la data del file è quella del download
        scritto = self.dir / "costruito.txt"
        scritto.write_text("4000\r\n")
        self.assertEqual(launcher.momento_costruzione([gui], scritto), 4000)
        self.assertEqual(launcher.momento_costruzione([gui], self.dir / "manca.txt"), 5000)

    def test_only_scripts_changed_after_the_build_are_reported(self):
        self.crea("nuovo.py", 6000)
        self.crea("vecchio.py", 4000)
        self.crea("launcher.pyw", 7000)
        self.crea("note.txt", 9000)  # non è codice
        self.assertEqual(launcher.script_piu_recenti(self.dir, 5000), ["launcher.pyw", "nuovo.py"])
        self.assertEqual(launcher.script_piu_recenti(self.dir, 8000), [])

    def test_no_repo_next_to_the_executable_means_no_warning(self):
        self.assertEqual(launcher.script_piu_recenti(self.dir / "non-esiste", 5000), [])

    def test_the_title_shows_the_build_date_only_for_an_executable(self):
        self.assertEqual(launcher.titolo(None), "p4-infrastructure")
        marca = datetime.datetime(2026, 10, 26, 11, 54).timestamp()
        self.assertEqual(launcher.titolo(marca), "p4-infrastructure · eseguibile del 26/10/2026 11:54")


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
