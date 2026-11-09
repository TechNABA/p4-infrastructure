#!/usr/bin/env python3
"""
launcher.pyw

Elenco cliccabile dei comandi di p4-infrastructure. Doppio clic su una riga (o
Invio su quella evidenziata) e il comando parte in una finestra di PowerShell
aperta nella cartella della repo.

L'app non vede mai ciò che digiti lì dentro: senza l'accesso a Perforce (sotto) la
password si scrive nella finestra del comando, come sempre. Per questo la finestra resta aperta
(-NoExit) e UTF-8 è già impostato: le due righe di sessione dell'elenco a mano
non servono più.

Il token admin del Worker si inserisce una volta, all'apertura (o dalla testata):
resta solo in memoria e lo ricevono, come variabile d'ambiente della loro
finestra, i soli script che lo chiedono. Non è un controllo d'accesso all'app:
serve a non digitarlo a ogni comando. Lo stesso vale per server, utente e password di
Perforce, se li inserisci: vanno ai soli comandi Perforce e p4 (P4PORT, P4USER, P4PASSWD).

Se il comando ha bisogno di un valore (username, team, numero di changelist,
workspace, account da salvare) un piccolo dialogo lo chiede al doppio clic.

    doppio clic sul file, oppure:  pythonw scripts/launcher.pyw

Come eseguibile (vedi il README) gira anche senza Python: gli script stanno in
p4-runner.exe, accanto a "P4 Infrastructure.exe", e restano fuori solo i comandi
che chiedono la repo e Node (test e pubblicazione del Worker).

Il catalogo qui sotto è scritto a mano accanto agli script: se ne cambi le
opzioni, test/test_launcher.py ti dice quale riga è rimasta indietro.
"""

import ctypes
import datetime
import json
import os
import re
import subprocess
import sys
import tkinter as tk
import tkinter.font as tkfont
from pathlib import Path
from tkinter import messagebox, ttk
from typing import NamedTuple

import naba_store
import p4_common as p4c

REPO = Path(__file__).resolve().parent.parent
IN_ESEGUIBILE = getattr(sys, "frozen", False)  # PyInstaller
# Dove girano i comandi: la repo, o la cartella dell'eseguibile (dove sta anche il runner).
CARTELLA = Path(sys.executable).parent if IN_ESEGUIBILE else REPO
RUNNER = "p4-runner.exe"
# L'icona viaggia dentro l'eseguibile (--add-data), estratta in _MEIPASS.
ICONA = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent)) / "icona.ico"
# La data di build la scrive GitHub Actions accanto all'icona: dopo un download i file hanno la data del download.
COSTRUITO = ICONA.with_name("costruito.txt")
CREATE_NEW_CONSOLE = getattr(subprocess, "CREATE_NEW_CONSOLE", 0x10)


class LauncherError(ValueError):
    """Un valore mancante o rifiutato: il comando non parte."""


# ── Catalogo ────────────────────────────────────────────────────
class Preset(NamedTuple):
    area: str    # Perforce, p4 o Worker: cosa tocca il comando
    group: str
    title: str
    argv: list   # parole del comando; una tupla è un gruppo opzionale, omesso se vuoto
    risk: str    # read, write, destroy
    desc: str    # una riga, accanto al titolo
    note: str    # in più, solo nella conferma dei comandi che scrivono o cancellano


RISCHIO = {
    "read": ("legge", "#7fd99a"),
    "write": ("scrive", "#f5df74"),
    "destroy": ("cancella", "#ff8793"),
}
AVVISO = {
    "write": "Scrive su Perforce, sul KV o sul Worker.",
    "destroy": "Cancella e non si torna indietro.",
}


def _famiglia(area, group, base, note, presets):
    return [Preset(area, group, title, base + args, risk, desc, note) for title, args, risk, desc in presets]


def _p4(group, title, argv, risk, desc, note=""):
    return _famiglia("p4", group, argv, note, [(title, [], risk, desc)])


CATALOGO = [
    # ── Perforce: gli script, che chiedono da soli server, utente e password ──
    *_famiglia(
        "Perforce", "Provisioning", ["python", "scripts/perforce_provision.py"],
        "Un gruppo o un depot già esistente senza la riga di permesso non è di questo flusso: "
        "il team viene rifiutato e il record va in error.",
        [("Anteprima del provisioning", ["--dry-run"], "read",
          "Mostra utenti, gruppi e depot che creerebbe. Non modifica niente."),
         ("Provisioning", [], "write",
          "Crea utenti, gruppi, depot e permessi dai record pending del KV."),
         ("Anteprima dei retry", ["--retry-errors", "--dry-run"], "read",
          "Come l'anteprima, ma ripassa anche i record in error."),
         ("Riprova i record in error", ["--retry-errors"], "write",
          "Rilancia il provisioning anche sui record in error, dopo aver sistemato la causa.")]),

    *_famiglia(
        "Perforce", "Rimozione", ["python", "scripts/perforce_cleanup.py", "--user", "{user}"],
        "Irreversibile. Il depot del team e la sua riga in p4 protect restano: vanno tolti a mano. "
        "A fine esecuzione lo script elenca ruoli e canali Discord da cercare.",
        [("Anteprima della rimozione", ["--dry-run"], "read",
          "Mostra cosa toglierebbe allo studente, senza modificare niente."),
         ("Rimuovi lo studente", [], "destroy",
          "Lo toglie da gruppi, changelist, workspace e KV; cancella l'account se non gli resta accesso."),
         ("Rimuovi da un solo team", ["--team", "{team}"], "destroy",
          "Lo toglie solo da quel team; l'account resta se ne ha altri."),
         ("Rimuovi dai gruppi, tieni l'account", ["--keep-account"], "destroy",
          "Lo toglie dai gruppi ma non cancella l'account Perforce."),
         ("Rimuovi e cancella il record", ["--delete-record"], "destroy",
          "Cancella anche il record dal KV invece di segnarlo removed.")]),
    *_famiglia(
        "Perforce", "Rimozione", ["python", "scripts/perforce_prune.py"],
        "Il tuo utente è sempre protetto. Service account e altri admin vanno indicati in «Account da salvare».",
        [("Anteprima degli account orfani", ["--dry-run", ("--keep", "{keep}")], "read",
          "Elenca gli account senza accesso a nessun depot, senza rimuoverli."),
         ("Rimuovi gli account orfani", [("--keep", "{keep}")], "destroy",
          "Rimuove gli account che non stanno in nessun gruppo con protezioni.")]),

    *_famiglia(
        "Perforce", "Import", ["python", "scripts/export_p4_users.py"],
        "L'utente con cui ti connetti non finisce mai nell'export.",
        [("Anteprima dell'import", ["--dry-run", ("--exclude", "{keep}")], "read",
          "Mostra quali account di Perforce porterebbe nel KV."),
         ("Importa nel KV", [("--exclude", "{keep}")], "write",
          "Porta gli account già su Perforce nel KV come existing, così il provisioning non li ricrea."),
         ("Importa senza sovrascrivere", ["--skip-existing", ("--exclude", "{keep}")], "write",
          "Come sopra, ma non tocca i record già presenti nel KV.")]),
    *_famiglia(
        "Perforce", "Test", ["python", "-m", "unittest", "discover", "-s", "test"], "",
        [("Test degli script", [], "read", "Controllano gli script senza toccare Perforce né il KV.")]),

    # ── p4: comandi diretti del client, per l'admin ──
    *_p4("Server e account", "Accedi a Perforce", ["p4", "login"], "read",
         "Apre la sessione: gli altri comandi p4 la richiedono."),
    *_p4("Server e account", "Info sul server", ["p4", "info"], "read",
         "Versione, licenza, Case Handling e statistiche del server."),
    *_p4("Server e account", "Licenze in uso", ["p4", "license", "-u"], "read",
         "Quanti utenti usano la licenza, il limite e la scadenza (serve un admin)."),
    *_p4("Server e account", "Utenti del server", ["p4", "users"], "read", "Tutti gli account."),
    *_p4("Server e account", "Depot del server", ["p4", "depots"], "read", "Tutti i depot."),

    *_p4("Un utente", "Gruppi di un utente", ["p4", "groups", "{user}"], "read",
         "I gruppi di cui l'utente fa parte."),
    *_p4("Un utente", "Permessi di un utente", ["p4", "protects", "-u", "{user}"], "read",
         "Le righe di protect che valgono per l'utente (serve un super user)."),
    *_p4("Un utente", "Workspace di un utente", ["p4", "clients", "-u", "{user}"], "read",
         "I suoi workspace: il nome serve ai comandi sui workspace."),
    *_p4("Un utente", "Changelist pending di un utente", ["p4", "changes", "-s", "pending", "-u", "{user}"], "read",
         "Le sue changelist pending: il numero serve ai comandi sulle changelist."),
    *_p4("Un utente", "File aperti da un utente", ["p4", "opened", "-a", "-u", "{user}"], "read",
         "I file che tiene aperti, in qualsiasi workspace."),
    *_p4("Un utente", "Reimposta la password di un utente", ["p4", "passwd", "{user}"], "write",
         "Imposta una nuova password: si scrive nella finestra (serve un super user).",
         "La nuova password si scrive nella finestra, non sulla riga di comando."),

    *_p4("Changelist", "File aperti in una changelist", ["p4", "opened", "-a", "-c", "{change}"], "read",
         "I file aperti nella changelist, con il workspace che li tiene."),
    *_p4("Changelist", "Sblocca i file di una changelist", ["p4", "unlock", "-f", "-c", "{change}"], "write",
         "Sblocca i file bloccati, anche di un altro utente (serve un admin)."),
    *_p4("Changelist", "Cancella gli shelve di una changelist", ["p4", "shelve", "-d", "-f", "-c", "{change}"],
         "destroy", "Cancella i suoi file in shelve, anche di un altro utente.",
         "Chi ha una risoluzione in sospeso su quello shelve non potrà più unirne il contenuto."),
    *_p4("Changelist", "Revert dei file di una changelist, anche di un altro utente",
         ["p4", "revert", "-C", "{client}", "-c", "{change}", "//..."], "destroy",
         "Annulla i file aperti nella changelist, così si può cancellare. Le modifiche non inviate vanno perse.",
         "Serve un admin. Il workspace è quello dopo la @ in «Changelist pending di un utente»."),
    *_p4("Changelist", "Cancella una changelist, anche di un altro utente", ["p4", "change", "-d", "-f", "{change}"],
         "destroy", "Cancella la changelist pending; con file aperti o shelve prima fai revert e togli gli shelve.",
         "Con file aperti prima serve «Revert dei file di una changelist», con file in shelve «Cancella gli "
         "shelve di una changelist»: lo script di rimozione li fa da solo."),

    *_p4("Workspace", "Revert dei file aperti in un workspace", ["p4", "revert", "-C", "{client}", "//..."],
         "destroy", "Annulla i file aperti, anche di un altro utente; le modifiche non inviate vanno perse.",
         "Serve un admin."),
    *_p4("Workspace", "Cancella un workspace, anche di un altro utente", ["p4", "client", "-d", "-f", "{client}"],
         "destroy", "Lo cancella anche se è bloccato; i file aperti vengono annullati. Rifiuta se ha shelve.",
         "Se dentro ci sono degli shelve p4 rifiuta: usa la variante con gli shelve."),
    *_p4("Workspace", "Cancella un workspace e i suoi shelve", ["p4", "client", "-d", "-f", "-Fd", "{client}"],
         "destroy", "Come sopra, e cancella anche gli shelve.", "Gli shelve cancellati non si recuperano."),

    *_p4("Team chiuso", "Cancella il depot del team", ["p4", "depot", "-d", "{team}"], "destroy",
         "Solo se è vuoto e il team è chiuso: il cleanup lo lascia di proposito."),
    *_p4("Team chiuso", "Togli la riga di permesso", ["p4", "protect"], "write",
         "Apre la tabella protect: cancella la riga write group NomeTeam * //NomeTeam/... e salva."),
    *_p4("Team chiuso", "Anteprima: cosa cancellerebbe sul depot del team", ["p4", "obliterate", "//{team}/..."], "read",
         "Mostra i file che verrebbero cancellati, senza toccare niente."),
    *_p4("Team chiuso", "Cancella per sempre i file del depot del team", ["p4", "obliterate", "-y", "//{team}/..."],
         "destroy", "Cancella file e storia, senza traccia. Fai prima l'anteprima.",
         "Il server non conserva traccia che esistessero. Serve un admin."),

    # ── Worker: il KV e Cloudflare ──
    *_famiglia(
        "Worker", "Record nel KV", ["python", "scripts/kv_status.py"], "",
        [("Stato del KV", [], "read", "Conta i record per status e per team."),
         ("Elenca gli username", ["--usernames"], "read", "Username per team e status, senza nomi né email."),
         ("Solo i pending", ["--status", "pending"], "read", "Solo i record ancora da provisionare."),
         ("Solo gli error", ["--status", "error"], "read", "Solo i record da sistemare."),
         ("Esporta in XLSX", ["--xlsx", "utenti.xlsx"], "read",
          "Scrive utenti.xlsx nella cartella della repo: dati personali, non committarlo."),
         ("Esporta in CSV", ["--csv", "utenti.csv"], "read",
          "Scrive utenti.csv nella cartella della repo: dati personali, non committarlo.")]),
    *_famiglia(
        "Worker", "Pubblicazione", ["npx", "wrangler@4.150.0", "deploy"],
        "Il Worker si chiama ancora perforce-registration: il nome sta nell'URL scritto negli script degli studenti.",
        [("Pubblica il Worker", [], "write", "Pubblica worker.js in produzione, con wrangler.jsonc.")]),
    *_famiglia(
        "Worker", "Pubblicazione", ["npx", "wrangler@4.150.0", "secret", "put", "ADMIN_TOKEN"],
        "Cloudflare non lo rimostra dopo il salvataggio: aggiorna anche il Vaultwarden.",
        [("Cambia il token admin del Worker", [], "write", "Chiede il nuovo token e lo salva come Secret del Worker.")]),
    *_famiglia(
        "Worker", "Test", ["npm", "test"], "",
        [("Test del Worker e del form", [], "read", "Nessun test tocca Perforce o il KV.")]),
]


def parole(argv):
    return [t for voce in argv for t in (voce if isinstance(voce, tuple) else (voce,))]


def solo_sorgente(preset):
    """Test e pubblicazione del Worker chiedono la repo e Node: nell'eseguibile non ci sono."""
    return preset.argv[0] in ("npm", "npx") or "unittest" in preset.argv


def comandi(in_eseguibile):
    return [p for p in CATALOGO if not (in_eseguibile and solo_sorgente(p))]


COMANDI = comandi(IN_ESEGUIBILE)


# ── Costruzione del comando ─────────────────────────────────────
MANCA = {"user": "lo username", "team": "il team", "change": "il numero della changelist", "client": "il workspace"}
NOME = {"user": "username", "team": "team", "keep": "account", "change": "changelist", "client": "workspace"}
SEGNAPOSTO = re.compile(r"\{(user|team|keep|change|client)\}")
PAROLA_SICURA = re.compile(r"^[A-Za-z0-9_./\\:-][A-Za-z0-9_./\\:@=+-]*$")


def segnaposti(argv):
    """Le chiavi dei segnaposto usate da un comando, anche dentro una parola come //{team}/..."""
    return {m.group(1) for t in parole(argv) for m in SEGNAPOSTO.finditer(t)}


def _valore(chiave, params, strict):
    """Valore validato di un segnaposto. keep è un elenco e può restare vuoto."""
    grezzo = (params.get(chiave) or "").strip()
    nomi = [n.strip() for n in grezzo.split(",") if n.strip()] if chiave == "keep" else [grezzo]
    if chiave == "keep" and not nomi:
        return ""
    try:
        if not grezzo:
            raise LauncherError(f"Compila {MANCA[chiave]}.")
        if chiave == "change":
            if not re.fullmatch(r"[0-9]+", grezzo):
                raise LauncherError(f"La changelist è un numero: {grezzo!r} non va bene.")
            return grezzo
        # Gli stessi controlli degli script: quello che passa qui, passa anche lì.
        return ",".join(p4c.check_p4_name(n, NOME[chiave]) for n in nomi)
    except (LauncherError, p4c.P4Error) as e:
        if strict:
            raise LauncherError(str(e)) from e
        return f"<{NOME[chiave]}>"


def build_argv(argv, params, strict=True):
    """Il comando con i segnaposto sostituiti. Con strict=False i valori che mancano
    restano come <username>, per mostrare il comando prima di compilare i campi."""
    out = []
    for voce in argv:
        gruppo = voce if isinstance(voce, tuple) else (voce,)
        valori = [SEGNAPOSTO.sub(lambda m: _valore(m.group(1), params, strict), t) for t in gruppo]
        if isinstance(voce, tuple) and "" in valori:
            continue
        out.extend(valori)
    return out


def ps_quote(parola):
    """PowerShell vede 'a,b' come un array e interpreta $ e &: tra apici singoli resta una stringa."""
    return parola if PAROLA_SICURA.match(parola) else "'" + parola.replace("'", "''") + "'"


def ps_line(argv):
    return " ".join(ps_quote(t) for t in argv)


# Gli script che chiedono il token admin (naba_store.get_admin_token): solo a loro lo si passa. Agli altri
# comandi no: npx esegue pacchetti di terzi, e p4, prune e i test non ne hanno bisogno.
SCRIPT_CON_TOKEN = {"scripts/perforce_provision.py", "scripts/perforce_cleanup.py",
                    "scripts/kv_status.py", "scripts/export_p4_users.py"}
STATUS_DI_PROVA = "verifica-accesso"


def usa_token(preset):
    return any(t in SCRIPT_CON_TOKEN for t in parole(preset.argv))


def usa_p4(preset):
    """Server, utente e password dell'accesso vanno ai soli comandi che parlano con Perforce."""
    return preset.area in ("Perforce", "p4") and not solo_sorgente(preset)


SERVER = re.compile(r"(?:(?:ssl|tcp)[46]*:)?[A-Za-z0-9.-]+")
CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)


def indirizzo(server, porta):
    """P4PORT da IP (o nome) e porta, separati nel dialogo."""
    server, porta = server.strip(), porta.strip()
    if not SERVER.fullmatch(server):
        raise LauncherError(f"Server non valido: {server!r}. Scrivi solo l'IP o il nome, ad esempio 10.0.0.5.")
    if not porta.isdigit() or not 0 < int(porta) < 65536:
        raise LauncherError(f"Porta non valida: {porta!r}. È un numero, di solito 1666.")
    return f"{server}:{porta}"


def verifica_p4(port, user, password):
    """p4 login con la password su stdin, mai sulla riga di comando. Lascia un ticket su questa macchina,
    che vale anche per i comandi p4 quando il server non accetta la password in P4PASSWD."""
    env = {k: v for k, v in os.environ.items() if k != "P4PASSWD"}
    try:
        esito = subprocess.run(["p4", "-p", port, "-u", user, "login"], input=password + "\n", capture_output=True,
                               text=True, env=env, timeout=30, creationflags=CREATE_NO_WINDOW)
    except subprocess.TimeoutExpired as e:
        raise LauncherError(f"Il server {port} non risponde.") from e
    except OSError as e:
        raise LauncherError(f"p4 non parte: è installato e nel PATH? ({e})") from e
    if esito.returncode != 0:
        raise LauncherError((esito.stderr or esito.stdout).strip() or f"Login su {port} rifiutato.")


def accesso_p4(server, porta, utente, password):
    """L'ambiente Perforce dell'accesso, o None se il server non è stato compilato (la porta ha un default)."""
    if not (server.strip() or utente.strip() or password):
        return None
    if not (server.strip() and utente.strip() and password):
        raise LauncherError("Per accedere a Perforce compila server, porta, utente e password, o lasciali tutti vuoti.")
    port = indirizzo(server, porta)
    try:
        utente = p4c.check_p4_name(utente.strip(), "utente")
    except p4c.P4Error as e:
        raise LauncherError(str(e)) from e
    verifica_p4(port, utente, password)
    return {"P4PORT": port, "P4USER": utente, "P4PASSWD": password}


def verifica_token(token):
    """Il Worker accetta il token? Una lettura su uno status che non esiste: è autenticata (un token
    sbagliato dà 401) ma l'elenco torna vuoto, quindi non passa nessun dato personale."""
    try:
        naba_store.fetch_users(token, status=STATUS_DI_PROVA)
    except naba_store.StoreError as e:
        raise LauncherError(str(e)) from e


def _e_uno_script(argv):
    return argv[:1] == ["python"] and len(argv) > 1 and argv[1].startswith("scripts/")


def visibile(argv):
    """Come il comando si legge nell'elenco e nelle conferme: nell'eseguibile «python scripts/X.py»
    è «p4-runner X», perché è quello che parte."""
    if IN_ESEGUIBILE and _e_uno_script(argv):
        return [Path(RUNNER).stem, Path(argv[1]).stem, *argv[2:]]
    return argv


def momento_costruzione(file, scritto=None):
    """Quando sono stati costruiti gli eseguibili: la data scritta dalla build, se c'è; altrimenti il più vecchio
    dei due, basta che uno sia rimasto indietro."""
    if scritto is not None and scritto.is_file():
        return float(scritto.read_text().strip())
    tempi = [Path(f).stat().st_mtime for f in file if Path(f).is_file()]
    return min(tempi) if tempi else None


def script_piu_recenti(cartella, costruito):
    """I file di codice modificati dopo la costruzione: l'eseguibile ne porta una copia vecchia. Vuoto se la
    cartella non c'è (l'eseguibile sta fuori dalla repo, e allora non si può sapere)."""
    if not cartella.is_dir():
        return []
    return sorted(p.name for p in cartella.iterdir() if p.suffix in (".py", ".pyw") and p.stat().st_mtime > costruito)


def titolo(costruito):
    if costruito is None:
        return "p4-infrastructure"
    return "p4-infrastructure · eseguibile del " + datetime.datetime.fromtimestamp(costruito).strftime("%d/%m/%Y %H:%M")


def per_eseguibile(argv):
    """Nell'eseguibile non c'è Python né la cartella scripts: «python scripts/X.py» diventa il runner
    accanto a noi. Da sorgente, e per gli altri comandi, resta com'è."""
    if not IN_ESEGUIBILE or not _e_uno_script(argv):
        return argv
    runner = CARTELLA / RUNNER
    if not runner.is_file():
        raise LauncherError(f"Manca {RUNNER} accanto all'eseguibile ({CARTELLA}).")
    return [str(runner), Path(argv[1]).stem, *argv[2:]]


def launch(argv, token=None, p4=None):
    """Apre una finestra di PowerShell nella repo, ci esegue il comando e la lascia aperta.

    Qui serve per forza una shell, a differenza dei comandi p4 degli script: valori
    validati da check_p4_name e parole quotate da ps_quote, mai testo libero. Il token e
    l'accesso a Perforce (P4PORT, P4USER, P4PASSWD), se ci sono, passano dall'ambiente della
    finestra e mai dalla riga di comando."""
    env = dict(os.environ, PYTHONUTF8="1")
    if token:
        env["NABA_ADMIN_TOKEN"] = token
    if p4:
        env.update(p4)
    argv = per_eseguibile(argv)
    riga = ps_line(argv)
    if not PAROLA_SICURA.match(argv[0]):
        riga = "& " + riga  # un percorso con spazi è quotato: senza & PowerShell lo leggerebbe come testo
    return subprocess.Popen(
        ["powershell.exe", "-NoLogo", "-NoExit", "-Command", riga],
        cwd=CARTELLA, env=env, creationflags=CREATE_NEW_CONSOLE,
    )


# ── Cosa chiedere al doppio clic ────────────────────────────────
AREE = {
    "Perforce": "Gli script di provisioning, rimozione e import.",
    "p4": "Comandi diretti del client: usano il server dell'accesso o, senza, quello di questa macchina (p4 set).",
    "Worker": "Il KV e Cloudflare.",
}

# L'elenco di account prende il nome dall'opzione: --keep salva, --exclude non esporta.
ELENCO = {"--keep": "Account da salvare", "--exclude": "Account da non esportare"}
ETICHETTE = {"user": "Username", "team": "Team", "change": "Numero della changelist", "client": "Workspace"}
SUGGERIMENTI = {
    "token": "Lo trovi nel Vaultwarden. Resta in memoria finché l'app è aperta e va solo ai comandi che lo usano.",
    "user": "l'account Perforce, ad esempio mario_rossi",
    "team": "il nome del team come lo scrive Perforce, ad esempio ProjectAlpha",
    "keep": "separati da virgola, ad esempio admin,servicebot; si può lasciare vuoto",
    "change": "solo cifre; lo trovi con «Changelist pending di un utente»",
    "client": "il nome esatto: è dopo la @ in «Changelist pending di un utente», o in «Workspace di un utente»",
    "server": "l'IP, ad esempio 10.0.0.5, senza la porta",
    "porta": "di solito 1666",
    "p4user": "il tuo account admin",
    "p4pass": "resta solo in memoria e va solo ai comandi Perforce e p4; server e utente si ricordano",
}
NOTA_ACCESSO = ("Tutto facoltativo. Senza token gli script lo chiedono nella loro finestra; senza server "
                "chiedono server, utente e password, e i comandi p4 usano quelli di questa macchina.")


def campi_extra(preset):
    """I valori, oltre a username e team, che il comando chiede: [(chiave, etichetta, suggerimento)]."""
    for voce in preset.argv:
        if isinstance(voce, tuple) and "{keep}" in voce:
            return [("keep", ELENCO[voce[0]], SUGGERIMENTI["keep"])]
    return [(k, ETICHETTE[k], SUGGERIMENTI[k]) for k in ("change", "client") if k in segnaposti(preset.argv)]


def da_chiedere(preset, params):
    """I valori da chiedere prima di lanciare. Username e team solo se mancano: stanno in testata e si
    vedono. Gli altri sempre, già compilati con l'ultimo valore: il numero di changelist di prima non
    deve partire da solo su una cancellazione."""
    chieste = [(k, ETICHETTE[k], SUGGERIMENTI[k]) for k in ("user", "team")
               if k in segnaposti(preset.argv) and not (params.get(k) or "").strip()]
    return chieste + campi_extra(preset)


def valori_usati(preset, params):
    """I valori che il comando usa, da mostrare in grande nella conferma: username e team arrivano dalla
    testata senza che il dialogo li chieda, e qui si vedono prima di partire."""
    usati = [(ETICHETTE[k], params[k].strip()) for k in ("user", "team", "change", "client")
             if k in segnaposti(preset.argv)]
    elenco = campi_extra(preset)
    if elenco and elenco[0][0] == "keep":
        usati.append((elenco[0][1], (params.get("keep") or "").strip() or "nessuno"))
    return usati


def da_riscrivere(preset, params):
    """Per i comandi che cancellano, il valore da riscrivere per confermare: (etichetta, valore), o None se
    il comando non ne usa (allora basta il bottone)."""
    if preset.risk != "destroy":
        return None
    for k in ("change", "client", "user", "team"):
        if k in segnaposti(preset.argv):
            return ETICHETTE[k], params[k].strip()
    return None


# Server, porta e utente dell'ultimo accesso riuscito, per non riscriverli: la password no, mai su disco.
FILE_ACCESSO = Path(os.environ.get("APPDATA") or Path.home()) / "p4-infrastructure" / "accesso.json"
DA_RICORDARE = ("server", "porta", "p4user")


def leggi_accesso(file):
    """Ciò che è stato salvato, solo le chiavi note e solo testo. Un file mancante o rovinato vale vuoto."""
    try:
        dati = json.loads(file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(dati, dict):
        return {}
    return {k: dati[k] for k in DA_RICORDARE if isinstance(dati.get(k), str)}


def salva_accesso(file, valori):
    """Non riuscire a salvare non ferma l'accesso: la prossima volta si riscrive."""
    try:
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text(json.dumps({k: valori.get(k, "").strip() for k in DA_RICORDARE}), encoding="utf-8")
    except OSError:
        pass


# ── Finestra ────────────────────────────────────────────────────
BG, DEEP, SEL, LINE = "#000000", "#0d0d0d", "#262626", "#3a3a3a"
INK, DIM, FAINT, ACCENT = "#f2f2f2", "#b0b0b0", "#808080", "#f5df74"
FILTRI = (("all", "Tutti"), ("read", "Legge"), ("write", "Scrive"), ("destroy", "Cancella"))
CAMPI = (("user", "Username"), ("team", "Team"))


def barra_nera(finestra):
    """Barra del titolo nera, come il resto: tema scuro (Windows 10 1809+) e, da Windows 11, anche colore
    della barra, del testo e del bordo. Dove non è supportato la barra resta quella di sistema."""
    try:
        finestra.update_idletasks()
        hwnd = ctypes.windll.user32.GetParent(finestra.winfo_id())
        for attributo, colorref in ((20, 1), (35, 0x000000), (36, 0x00F2F2F2), (34, 0x003A3A3A)):
            ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, attributo, ctypes.byref(ctypes.c_int(colorref)), 4)
    except (AttributeError, OSError):
        pass


class Dialogo(tk.Toplevel):
    """Base dei dialoghi: scuri come la finestra, modali, centrati e dentro lo schermo. Esc annulla."""

    def __init__(self, app, titolo):
        super().__init__(app.root, bg=DEEP)
        self.app, self.px, self.f = app, app.px, app.f
        self.pad = self.px(22)
        self.title(titolo)
        self.transient(app.root)
        self.resizable(False, False)
        tk.Label(self, text=titolo, bg=DEEP, fg=INK, font=self.f["titolo"]).pack(anchor="w", padx=self.pad,
                                                                                  pady=(self.pad, 0))
        self.bind("<Escape>", lambda e: self.destroy())

    def _testo(self, testo, colore=DIM, sopra=4, font="ui"):
        tk.Label(self, text=testo, bg=DEEP, fg=colore, font=self.f[font], justify="left",
                 wraplength=self.px(440)).pack(anchor="w", padx=self.pad, pady=(self.px(sopra), 0))

    def _campo(self, variabile, nascosto=False):
        campo = tk.Entry(self, textvariable=variabile, width=44, bg=BG, fg=INK, insertbackground=ACCENT,
                         selectbackground=ACCENT, selectforeground=BG, relief="flat", font=self.f["mono"],
                         highlightthickness=1, highlightbackground=LINE, highlightcolor=ACCENT,
                         show="•" if nascosto else "")
        campo.pack(fill="x", padx=self.pad, pady=(self.px(6), 0), ipady=self.px(5))
        return campo

    def _bottoni(self, ok, annulla, comando, colore=INK):
        """Il bottone che esegue a destra, Annulla accanto. Il bordo giallo mostra quale ha il fuoco."""
        riga = tk.Frame(self, bg=DEEP)
        riga.pack(fill="x", padx=self.pad, pady=self.pad)
        stile = dict(relief="flat", bd=0, font=self.f["ui_b"], pady=self.px(4), cursor="hand2",
                     highlightthickness=2, highlightcolor=ACCENT, highlightbackground=DEEP)
        si = tk.Button(riga, text=ok, command=comando, bg=colore, fg=BG, activebackground=ACCENT,
                       activeforeground=BG, disabledforeground=FAINT, padx=self.px(22), **stile)
        si.pack(side="right")
        no = tk.Button(riga, text=annulla, command=self.destroy, bg=SEL, fg=INK, activebackground=LINE,
                       activeforeground=INK, padx=self.px(16), **stile)
        no.pack(side="right", padx=(0, self.px(10)))
        return si, no

    def _mostra(self, fuoco):
        fuoco.focus_set()
        barra_nera(self)
        self.update_idletasks()
        root = self.app.root
        x = root.winfo_rootx() + (root.winfo_width() - self.winfo_width()) // 2
        # Al centro della finestra ma dentro lo schermo: con più campi il dialogo è alto.
        y = root.winfo_rooty() + (root.winfo_height() - self.winfo_height()) // 3
        y = min(y, self.winfo_screenheight() - self.winfo_height() - self.px(60))
        self.geometry(f"+{max(x, 0)}+{max(y, 0)}")
        self.wait_visibility()
        self.grab_set()
        self.wait_window()


class Modulo(Dialogo):
    """Dialogo con uno o più campi, ognuno con etichetta, suggerimento e valore iniziale.
    risultato è {chiave: valore}, o None se annullato."""

    def __init__(self, app, titolo, campi, nota="", ok="Continua", annulla="Annulla"):
        super().__init__(app, titolo)
        self.risultato = None
        if nota:
            self._testo(nota)
        self.valori, primo = {}, None
        for chiave, etichetta, suggerimento, iniziale, nascosto in campi:
            self._testo(etichetta, INK, 14, "ui_b")
            self._testo(suggerimento, FAINT, 0)
            self.valori[chiave] = tk.StringVar(value=iniziale)
            campo = self._campo(self.valori[chiave], nascosto)
            primo = primo or campo
        self._bottoni(ok, annulla, self._ok)
        self.bind("<Return>", lambda e: self._ok())
        primo.select_range(0, "end")
        self._mostra(primo)

    def _ok(self):
        self.risultato = {k: v.get() for k, v in self.valori.items()}
        self.destroy()


class Conferma(Dialogo):
    """Conferma di un comando che scrive o cancella: cosa fa, su quali valori, la riga che parte. Per
    cancellare, il valore principale va riscritto. Invio esegue il bottone che ha il fuoco: all'inizio
    Annulla, o il campo da riscrivere."""

    def __init__(self, app, preset, testo, valori, riga, riscrivi):
        super().__init__(app, preset.title)
        self.ok = False
        colore = RISCHIO[preset.risk][1]
        avviso, _, nota = testo.partition("\n")
        self._testo(avviso, colore)
        if nota:
            self._testo(nota, DIM, 2)
        for etichetta, valore in valori:
            r = tk.Frame(self, bg=DEEP)
            r.pack(fill="x", padx=self.pad, pady=(self.px(10), 0))
            tk.Label(r, text=etichetta, bg=DEEP, fg=FAINT, font=self.f["ui"], width=24, anchor="w").pack(side="left")
            tk.Label(r, text=valore, bg=DEEP, fg=INK, font=self.f["mono_g"]).pack(side="left")
        tk.Label(self, text=riga, bg=BG, fg=DIM, font=self.f["mono"], justify="left", anchor="w",
                 wraplength=self.px(440), padx=self.px(10), pady=self.px(8)).pack(fill="x", padx=self.pad,
                                                                                  pady=(self.px(14), 0))
        self.attesa = riscrivi[1] if riscrivi else None
        if riscrivi:
            self._testo(f"Per confermare riscrivi {riscrivi[0].lower()}: {riscrivi[1]}", INK, 14)
            self.scritto = tk.StringVar()
            fuoco = self._campo(self.scritto)
            self.scritto.trace_add("write", lambda *_: self._abilita())
        self.si, no = self._bottoni("Esegui", "Annulla", self._ok, colore)
        if riscrivi:
            self._abilita()
        else:
            fuoco = no
        self.bind("<Return>", self._invio)
        self._mostra(fuoco)

    def _pronto(self):
        return self.attesa is None or self.scritto.get().strip() == self.attesa

    def _abilita(self):
        self.si.config(state="normal" if self._pronto() else "disabled",
                       bg=RISCHIO["destroy"][1] if self._pronto() else SEL)

    def _invio(self, evento):
        if isinstance(evento.widget, tk.Button):
            evento.widget.invoke()
        elif self._pronto():
            self._ok()

    def _ok(self):
        if self._pronto():
            self.ok = True
            self.destroy()


class App:
    def __init__(self, root):
        self.root = root
        self.px = lambda n: round(n * root.winfo_fpixels("1i") / 96)
        self.vis, self.sel, self.filtro, self.area_filtro = [], 0, "all", None
        self.ricerca = tk.StringVar()
        self.campi = {k: tk.StringVar() for k in ("user", "team", "keep", "change", "client")}
        self.token = None  # solo in memoria: mai su disco, mai nella riga di comando
        self.p4 = None     # P4PORT, P4USER, P4PASSWD dell'accesso: come il token, solo in memoria
        self.token_ambiente = bool(os.environ.get("NABA_ADMIN_TOKEN", "").strip())
        self.costruito = momento_costruzione([sys.executable, CARTELLA / RUNNER], COSTRUITO) if IN_ESEGUIBILE else None
        self._font()
        self._costruisci()
        self.ricerca.trace_add("write", self._ricerca_cambiata)
        for var in self.campi.values():
            var.trace_add("write", lambda *_: self.aggiorna())
        self.aggiorna()
        self.stato_accesso()
        self.stato_versione()
        self.cerca.focus_set()
        root.after(50, lambda: barra_nera(root))

    def params(self):
        return {k: v.get() for k, v in self.campi.items()}

    # ── aspetto ──
    def _font(self):
        presenti = set(tkfont.families())
        ui = next(f for f in ("Segoe UI Variable Text", "Segoe UI", "Arial") if f in presenti)
        mono = next(f for f in ("Cascadia Mono", "Consolas", "Courier New") if f in presenti)
        self.f = {"ui": (ui, 10), "ui_b": (ui, 10, "bold"), "titolo": (ui, 11, "bold"),
                  "area": (ui, 14, "bold"), "mono": (mono, 10), "mono_g": (mono, 13)}

    def _campo(self, genitore, chiave, larghezza, fondo):
        return tk.Entry(genitore, textvariable=self.campi[chiave], width=larghezza, bg=fondo, fg=INK,
                        insertbackground=ACCENT, selectbackground=ACCENT, selectforeground=BG, relief="flat",
                        font=self.f["mono"], highlightthickness=1, highlightbackground=LINE,
                        highlightcolor=ACCENT)

    def _chip(self, genitore, comando):
        c = tk.Label(genitore, bg=BG, fg=DIM, font=self.f["ui"], padx=self.px(12), pady=self.px(3),
                     cursor="hand2", highlightthickness=1, highlightbackground=LINE)
        c.pack(side="left", padx=(0, self.px(8)))
        c.bind("<Button-1>", lambda e: comando())
        return c

    def _costruisci(self):
        px, f, root = self.px, self.f, self.root
        root.title(titolo(self.costruito))
        try:
            root.iconbitmap(default=str(ICONA))  # vale anche per i dialoghi
        except tk.TclError:
            pass
        root.configure(bg=BG)
        root.geometry(f"{px(1080)}x{px(820)}")
        root.minsize(px(820), px(520))
        root.columnconfigure(0, weight=1)
        root.rowconfigure(3, weight=1)
        m = px(22)

        testa = tk.Frame(root, bg=BG)
        testa.grid(row=0, sticky="ew", padx=m, pady=(px(18), 0))
        tk.Label(testa, text="p4-infrastructure", bg=BG, fg=DIM, font=f["mono"]).pack(side="left")
        self.accesso = tk.Label(testa, bg=BG, font=f["ui"])
        self.accesso.pack(side="left", padx=(px(22), 0))
        self.accesso.bind("<Button-1>", lambda e: self.esci() if self.token or self.p4 else self.accedi())
        for chiave, etichetta in reversed(CAMPI):
            self._campo(testa, chiave, 16, DEEP).pack(side="right", padx=(px(6), 0), ipady=px(3))
            tk.Label(testa, text=etichetta, bg=BG, fg=DIM, font=f["ui"]).pack(side="right", padx=(px(14), 0))

        barra = tk.Frame(root, bg=DEEP, highlightthickness=1, highlightbackground=LINE)
        barra.grid(row=1, sticky="ew", padx=m, pady=(px(16), 0))
        tk.Label(barra, text="PS>", bg=DEEP, fg=ACCENT, font=f["mono_g"]).pack(side="left", padx=(px(14), px(8)))
        self.cerca = tk.Entry(barra, textvariable=self.ricerca, bg=DEEP, fg=INK, insertbackground=ACCENT,
                              selectbackground=ACCENT, selectforeground=BG, relief="flat", font=f["mono_g"],
                              highlightthickness=0)
        self.cerca.pack(side="left", fill="x", expand=True, ipady=px(9))
        self.cerca.bind("<FocusIn>", lambda e: barra.config(highlightbackground=ACCENT))
        self.cerca.bind("<FocusOut>", lambda e: barra.config(highlightbackground=LINE))
        self.cerca.bind("<Down>", lambda e: self.muovi(1))
        self.cerca.bind("<Up>", lambda e: self.muovi(-1))
        self.cerca.bind("<Return>", self.esegui)
        self.cerca.bind("<Escape>", self._azzera)

        chips = tk.Frame(root, bg=BG)
        chips.grid(row=2, sticky="ew", padx=m, pady=(px(12), 0))
        self.chip = {chiave: (self._chip(chips, lambda k=chiave: self._filtra(k)), nome) for chiave, nome in FILTRI}
        tk.Frame(chips, bg=BG, width=px(14)).pack(side="left")
        self.chip_area = {chiave: self._chip(chips, lambda k=chiave: self._filtra_area(k)) for chiave in AREE}
        self.avviso = tk.Label(chips, bg=BG, fg=ACCENT, font=f["ui"])
        self.avviso.pack(side="right")
        self.esito = tk.Label(chips, bg=BG, fg=DIM, font=f["ui"])  # l'ultimo comando avviato
        self.esito.pack(side="right", padx=(0, px(14)))

        corpo = tk.Frame(root, bg=BG)
        corpo.grid(row=3, sticky="nsew", padx=(m, px(8)), pady=(px(8), px(8)))
        corpo.columnconfigure(0, weight=1)
        corpo.rowconfigure(0, weight=1)
        self.lista = tk.Text(corpo, bg=BG, fg=INK, bd=0, highlightthickness=0, wrap="word", cursor="arrow",
                             state="disabled", takefocus=0, padx=0, tabs=(px(126), px(560)), font=f["ui"])
        self.lista.grid(row=0, column=0, sticky="nsew")
        stile = ttk.Style(root)
        stile.theme_use("clam")
        stile.configure("Vertical.TScrollbar", background=LINE, troughcolor=BG, bordercolor=BG,
                        lightcolor=LINE, darkcolor=LINE, arrowcolor=DIM, relief="flat")
        stile.map("Vertical.TScrollbar", background=[("active", FAINT)])
        barra_scorrimento = ttk.Scrollbar(corpo, orient="vertical", command=self.lista.yview)
        barra_scorrimento.grid(row=0, column=1, sticky="ns")
        self.lista.config(yscrollcommand=barra_scorrimento.set)
        t = self.lista
        t.tag_config("area", font=f["area"], foreground=INK, lmargin1=px(16), lmargin2=px(16), spacing1=px(26),
                     spacing3=px(2))
        t.tag_config("area_nota", font=f["ui"], foreground=FAINT)
        t.tag_config("gruppo", font=f["ui_b"], foreground=FAINT, lmargin1=px(16), spacing1=px(14), spacing3=px(2))
        # La descrizione, se va a capo, resta nella sua colonna: lmargin2 è dove inizia.
        t.tag_config("rischio", font=f["ui_b"], lmargin1=px(4), lmargin2=px(560), spacing1=px(9))
        for chiave, (_, colore) in RISCHIO.items():
            t.tag_config("c_" + chiave, foreground=colore)
        t.tag_config("titolo", font=f["titolo"], foreground=INK, lmargin2=px(560))
        t.tag_config("descrizione", font=f["ui"], foreground=DIM, lmargin2=px(560))
        t.tag_config("comando", font=f["mono"], foreground=FAINT, lmargin1=px(126), lmargin2=px(126), spacing3=px(9))
        t.tag_config("vuoto", foreground=DIM, lmargin1=px(16), spacing1=px(20))

    # ── elenco ──
    def _ricerca_cambiata(self, *_):
        self.sel = 0
        self.aggiorna()

    def _filtra(self, chiave):
        self.filtro, self.sel = chiave, 0
        self.aggiorna()

    def _filtra_area(self, chiave):
        self.area_filtro, self.sel = (None if self.area_filtro == chiave else chiave), 0
        self.aggiorna()

    def _azzera(self, _event=None):
        self.filtro, self.area_filtro, self.sel = "all", None, 0
        self.ricerca.set("")
        self.aggiorna()

    def testo(self, preset, params):
        return " ".join(visibile(build_argv(preset.argv, params, strict=False)))

    def aggiorna(self):
        params = self.params()
        termini = self.ricerca.get().lower().split()
        self.vis = [p for p in COMANDI
                    if (self.filtro == "all" or p.risk == self.filtro)
                    and (self.area_filtro is None or p.area == self.area_filtro)
                    and all(w in f"{p.title} {p.desc} {p.area} {p.group} {self.testo(p, params)}".lower()
                            for w in termini)]
        self.sel = min(self.sel, max(len(self.vis) - 1, 0))
        for chiave, (c, nome) in self.chip.items():
            n = sum(1 for p in COMANDI if chiave == "all" or p.risk == chiave)
            attivo = chiave == self.filtro
            c.config(text=f"{nome} {n}", bg=INK if attivo else BG, fg=BG if attivo else DIM,
                     highlightbackground=INK if attivo else LINE)
        for chiave, c in self.chip_area.items():
            attivo = chiave == self.area_filtro
            c.config(text=f"{chiave} {sum(1 for p in COMANDI if p.area == chiave)}",
                     bg=INK if attivo else BG, fg=BG if attivo else DIM, highlightbackground=INK if attivo else LINE)
        self.disegna(params)

    def disegna(self, params):
        t = self.lista
        # La riga in cima, non la frazione: con righe che vanno a capo, dopo il ridisegno la stessa
        # frazione cade qualche riga più in là e la lista scatta.
        cima = t.index("@0,0")
        t.config(state="normal")
        t.delete("1.0", "end")
        area = gruppo = None
        for i, p in enumerate(self.vis):
            if p.area != area:
                area, gruppo = p.area, None
                t.insert("end", p.area, "area")
                t.insert("end", "   " + AREE[p.area] + "\n", ("area", "area_nota"))
            if p.group != gruppo:
                gruppo = p.group
                t.insert("end", p.group + "\n", "gruppo")
            riga = f"r{i}"
            t.insert("end", "▌ ", (riga, "rischio", f"m{i}"))  # la barra gialla della riga selezionata
            t.insert("end", RISCHIO[p.risk][0], (riga, "rischio", "c_" + p.risk))
            t.insert("end", "\t" + p.title, (riga, "titolo"))
            t.insert("end", "\t" + p.desc + "\n", (riga, "descrizione"))
            t.insert("end", self.testo(p, params) + "\n", (riga, "comando"))
            t.tag_bind(riga, "<Button-1>", lambda e, i=i: self.scegli(i))
            t.tag_bind(riga, "<Double-Button-1>", lambda e, i=i: self.doppio(i))
            t.tag_bind(riga, "<Enter>", lambda e: t.config(cursor="hand2"))
            t.tag_bind(riga, "<Leave>", lambda e: t.config(cursor="arrow"))
        if not self.vis:
            t.insert("end", "Nessun comando per questa ricerca. Prova con una parola del comando o della "
                            "descrizione: cleanup, dry-run, licenza.", "vuoto")
        t.config(state="disabled")
        self.colora()
        t.yview(cima)

    def colora(self):
        for i in range(len(self.vis)):
            self.lista.tag_config(f"r{i}", background=SEL if i == self.sel else BG)
            self.lista.tag_config(f"m{i}", foreground=ACCENT if i == self.sel else BG)

    def scegli(self, i):
        self.sel = i
        self.colora()
        self.cerca.focus_set()
        # Un doppio clic seleziona una parola: la selezione del testo qui non serve a niente.
        self.root.after_idle(lambda: self.lista.tag_remove("sel", "1.0", "end"))

    def doppio(self, i):
        self.scegli(i)
        self.esegui()
        return "break"

    def muovi(self, passo):
        if self.vis:
            self.sel = (self.sel + passo) % len(self.vis)
            self.colora()
            self.lista.see(self.lista.tag_ranges(f"r{self.sel}")[0])
        return "break"

    # ── azioni ──
    def modulo(self, titolo, campi, **opzioni):
        return Modulo(self, titolo, campi, **opzioni).risultato

    def stato_accesso(self):
        attivi = (["Token admin"] if self.token else ["Token admin dall'ambiente"] if self.token_ambiente else [])
        if self.p4:
            attivi.append(f"{self.p4['P4USER']}@{self.p4['P4PORT']}")
        if self.token or self.p4:
            self.accesso.config(text="●  " + " · ".join(attivi + ["Esci"]), fg=RISCHIO["read"][1], cursor="hand2")
        else:
            self.accesso.config(text="○  " + " · ".join(attivi + ["Accedi"]), fg=ACCENT, cursor="hand2")

    def scripts_nuovi(self):
        """Gli script della repo cambiati dopo la costruzione, se l'eseguibile sta in dist/ dentro la repo."""
        if self.costruito is None:
            return []
        return script_piu_recenti(CARTELLA.parent / "scripts", self.costruito)

    def stato_versione(self):
        self.avviso.config(text="▲  Eseguibile vecchio: ricostruiscilo" if self.scripts_nuovi() else "")

    def accedi(self):
        """Chiede token admin e accesso a Perforce e li verifica: il token sul Worker, il server con p4 login.
        Vuoti o «Salta»: si lavora senza, e gli script li chiedono nella loro finestra."""
        valori = {"porta": "1666", **leggi_accesso(FILE_ACCESSO)}
        while True:
            campi = [] if self.token_ambiente else [
                ("token", "Token admin del Worker", SUGGERIMENTI["token"], valori.get("token", ""), True)]
            campi += [("server", "Server Perforce", SUGGERIMENTI["server"], valori.get("server", ""), False),
                      ("porta", "Porta", SUGGERIMENTI["porta"], valori.get("porta", ""), False),
                      ("p4user", "Utente Perforce", SUGGERIMENTI["p4user"], valori.get("p4user", ""), False),
                      ("p4pass", "Password Perforce", SUGGERIMENTI["p4pass"], valori.get("p4pass", ""), True)]
            risposta = self.modulo("Accesso", campi, nota=NOTA_ACCESSO, ok="Entra", annulla="Salta")
            if risposta is None:
                return
            valori = risposta
            token = risposta.get("token", "").strip()
            self.root.config(cursor="watch")
            self.root.update_idletasks()
            try:
                if token:
                    verifica_token(token)
                p4 = accesso_p4(risposta["server"], risposta["porta"], risposta["p4user"], risposta["p4pass"])
            except LauncherError as e:
                messagebox.showwarning("Accesso non riuscito", str(e), parent=self.root)
                continue
            finally:
                self.root.config(cursor="")
            self.token = token or self.token
            if p4:
                self.p4 = p4
                salva_accesso(FILE_ACCESSO, risposta)
            self.stato_accesso()
            return

    def esci(self):
        self.token = self.p4 = None
        self.stato_accesso()

    def raccogli(self, preset):
        """Chiede in un solo dialogo i valori che il comando usa. False se chi ha fatto doppio clic ha annullato."""
        chieste = da_chiedere(preset, self.params())
        if not chieste:
            return True
        valori = self.modulo(preset.title, [(k, e, s, self.campi[k].get(), False) for k, e, s in chieste])
        if valori is None:
            return False
        for chiave, valore in valori.items():
            self.campi[chiave].set(valore.strip())
        return True

    def esegui(self, _event=None):
        p = self.corrente()
        if not p or not self.raccogli(p):
            return "break"
        try:
            argv = build_argv(p.argv, self.params())
        except LauncherError as e:
            messagebox.showwarning("Valore non valido", str(e), parent=self.root)
            return "break"
        riga = ps_line(visibile(argv))
        if p.risk != "read":
            testo = AVVISO[p.risk]
            if p.note:
                testo += f"\n{p.note}"
            if self.scripts_nuovi():
                testo += "\nL'eseguibile è più vecchio degli script nella repo: ricostruiscilo (comandi nel README)."
            if p.risk == "destroy" and "keep" in segnaposti(p.argv) and not self.campi["keep"].get().strip():
                testo += "\nNessun account da salvare: oltre al tuo utente non è protetto nessuno."
            params = self.params()
            if not Conferma(self, p, testo, valori_usati(p, params), riga, da_riscrivere(p, params)).ok:
                return "break"
        try:
            launch(argv, self.token if usa_token(p) else None, self.p4 if usa_p4(p) else None)
        except (OSError, LauncherError) as e:
            messagebox.showerror("Il comando non parte", str(e), parent=self.root)
            return "break"
        self.esito.config(text=f"Avviato alle {datetime.datetime.now():%H:%M}: {p.title}")
        return "break"

    def corrente(self):
        return self.vis[self.sel] if self.vis else None


def main():
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)  # senza, su schermi ingranditi il testo è sfocato
    except (AttributeError, OSError):
        pass
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("TechNABA.P4Infrastructure")  # icona nella barra
    except (AttributeError, OSError):
        pass
    root = tk.Tk()
    app = App(root)
    root.after(300, app.accedi)
    root.mainloop()


if __name__ == "__main__":
    main()
