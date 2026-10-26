#!/usr/bin/env python3
"""
launcher.pyw

Elenco cliccabile dei comandi di p4-infrastructure. Doppio clic su una riga (o
Invio su quella evidenziata) e il comando parte in una finestra di PowerShell
aperta nella cartella della repo.

L'app non vede mai ciò che digiti lì dentro: la password di Perforce si scrive
nella finestra del comando, come sempre. Per questo la finestra resta aperta
(-NoExit) e UTF-8 è già impostato: le due righe di sessione dell'elenco a mano
non servono più.

Il token admin del Worker si inserisce una volta, all'apertura (o dalla testata):
resta solo in memoria e lo ricevono, come variabile d'ambiente della loro
finestra, i soli script che lo chiedono. Non è un controllo d'accesso all'app:
serve a non digitarlo a ogni comando.

Se il comando ha bisogno di un valore (username, team, numero di changelist,
workspace, account da salvare) un piccolo dialogo lo chiede al doppio clic.

    doppio clic sul file, oppure:  pythonw scripts/launcher.pyw

Il catalogo qui sotto è scritto a mano accanto agli script: se ne cambi le
opzioni, test/test_launcher.py ti dice quale riga è rimasta indietro.
"""

import ctypes
import os
import re
import subprocess
import tkinter as tk
import tkinter.font as tkfont
from pathlib import Path
from tkinter import messagebox, ttk
from typing import NamedTuple

import naba_store
import p4_common as p4c

REPO = Path(__file__).resolve().parent.parent
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
    *_p4("Changelist", "Cancella una changelist, anche di un altro utente", ["p4", "change", "-d", "-f", "{change}"],
         "destroy", "Cancella la changelist pending; con file aperti o shelve prima vanno tolti.",
         "Con file aperti o in shelve prima servono il revert del workspace e la cancellazione degli shelve: "
         "lo script di rimozione li fa da solo."),

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


def verifica_token(token):
    """Il Worker accetta il token? Una lettura su uno status che non esiste: è autenticata (un token
    sbagliato dà 401) ma l'elenco torna vuoto, quindi non passa nessun dato personale."""
    try:
        naba_store.fetch_users(token, status=STATUS_DI_PROVA)
    except naba_store.StoreError as e:
        raise LauncherError(str(e)) from e


def launch(argv, token=None):
    """Apre una finestra di PowerShell nella repo, ci esegue il comando e la lascia aperta.

    Qui serve per forza una shell, a differenza dei comandi p4 degli script: valori
    validati da check_p4_name e parole quotate da ps_quote, mai testo libero. Il token, se c'è,
    passa dall'ambiente della finestra e mai dalla riga di comando."""
    env = dict(os.environ, PYTHONUTF8="1")
    if token:
        env["NABA_ADMIN_TOKEN"] = token
    return subprocess.Popen(
        ["powershell.exe", "-NoLogo", "-NoExit", "-Command", ps_line(argv)],
        cwd=REPO, env=env, creationflags=CREATE_NEW_CONSOLE,
    )


# ── Cosa chiedere al doppio clic ────────────────────────────────
AREE = {
    "Perforce": "Gli script di provisioning, rimozione e import.",
    "p4": "Comandi diretti del client: usano server e utente di questa macchina (p4 set) e un login già fatto.",
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
    "client": "il nome esatto; lo trovi con «Workspace di un utente»",
}


def campo_extra(preset):
    """Il valore, oltre a username e team, che il comando chiede: (chiave, etichetta, suggerimento), o None."""
    for voce in preset.argv:
        if isinstance(voce, tuple) and "{keep}" in voce:
            return "keep", ELENCO[voce[0]], SUGGERIMENTI["keep"]
    for chiave in ("change", "client"):
        if chiave in segnaposti(preset.argv):
            return chiave, ETICHETTE[chiave], SUGGERIMENTI[chiave]
    return None


def da_chiedere(preset, params):
    """I valori da chiedere prima di lanciare. Username e team solo se mancano: stanno in testata e si
    vedono. Gli altri sempre, già compilati con l'ultimo valore: il numero di changelist di prima non
    deve partire da solo su una cancellazione."""
    chieste = [(k, ETICHETTE[k], SUGGERIMENTI[k]) for k in ("user", "team")
               if k in segnaposti(preset.argv) and not (params.get(k) or "").strip()]
    extra = campo_extra(preset)
    return chieste + ([extra] if extra else [])


# ── Finestra ────────────────────────────────────────────────────
BG, DEEP, SEL, LINE = "#000000", "#0d0d0d", "#262626", "#3a3a3a"
INK, DIM, FAINT, ACCENT = "#f2f2f2", "#b0b0b0", "#808080", "#f5df74"
FILTRI = (("all", "Tutti"), ("read", "Legge"), ("write", "Scrive"), ("destroy", "Cancella"))
CAMPI = (("user", "Username"), ("team", "Team"))


class Chiedi(tk.Toplevel):
    """Dialogo per un valore: etichetta, suggerimento e campo già compilato con l'ultimo valore."""

    def __init__(self, app, etichetta, suggerimento, iniziale, nascosto=False, ok="Continua", annulla="Annulla"):
        super().__init__(app.root, bg=DEEP)
        self.risultato = None
        self.title(etichetta)
        self.transient(app.root)
        self.resizable(False, False)
        px, f = app.px, app.f
        pad = px(22)
        tk.Label(self, text=etichetta, bg=DEEP, fg=INK, font=f["titolo"]).pack(anchor="w", padx=pad, pady=(pad, 0))
        tk.Label(self, text=suggerimento, bg=DEEP, fg=FAINT, font=f["ui"]).pack(anchor="w", padx=pad, pady=(px(2), px(12)))
        self.valore = tk.StringVar(value=iniziale)
        campo = tk.Entry(self, textvariable=self.valore, width=44, bg=BG, fg=INK, insertbackground=ACCENT,
                         selectbackground=ACCENT, selectforeground=BG, relief="flat", font=f["mono"],
                         highlightthickness=1, highlightbackground=LINE, highlightcolor=ACCENT,
                         show="•" if nascosto else "")
        campo.pack(fill="x", padx=pad, ipady=px(5))
        riga = tk.Frame(self, bg=DEEP)
        riga.pack(fill="x", padx=pad, pady=pad)
        tk.Button(riga, text=ok, command=self._ok, bg=INK, fg=BG, activebackground=ACCENT,
                  activeforeground=BG, relief="flat", bd=0, font=f["ui_b"], padx=px(22), pady=px(4),
                  cursor="hand2").pack(side="right")
        tk.Button(riga, text=annulla, command=self.destroy, bg=SEL, fg=INK, activebackground=LINE,
                  activeforeground=INK, relief="flat", bd=0, font=f["ui_b"], padx=px(16), pady=px(4),
                  cursor="hand2").pack(side="right", padx=(0, px(10)))
        self.bind("<Return>", lambda e: self._ok())
        self.bind("<Escape>", lambda e: self.destroy())
        campo.focus_set()
        campo.select_range(0, "end")
        self.update_idletasks()
        x = app.root.winfo_rootx() + (app.root.winfo_width() - self.winfo_width()) // 2
        self.geometry(f"+{max(x, 0)}+{app.root.winfo_rooty() + px(140)}")
        self.wait_visibility()
        self.grab_set()
        self.wait_window()

    def _ok(self):
        self.risultato = self.valore.get()
        self.destroy()


class App:
    def __init__(self, root):
        self.root = root
        self.px = lambda n: round(n * root.winfo_fpixels("1i") / 96)
        self.vis, self.sel, self.filtro, self.area_filtro = [], 0, "all", None
        self.ricerca = tk.StringVar()
        self.campi = {k: tk.StringVar() for k in ("user", "team", "keep", "change", "client")}
        self.token = None  # solo in memoria: mai su disco, mai nella riga di comando
        self.token_ambiente = bool(os.environ.get("NABA_ADMIN_TOKEN", "").strip())
        self._font()
        self._costruisci()
        self.ricerca.trace_add("write", self._ricerca_cambiata)
        for var in self.campi.values():
            var.trace_add("write", lambda *_: self.aggiorna())
        self.aggiorna()
        self.stato_accesso()
        self.cerca.focus_set()

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
        root.title("p4-infrastructure")
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
        self.accesso.bind("<Button-1>", lambda e: self.esci() if self.token else self.accedi())
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
        t.tag_config("rischio", font=f["ui_b"], lmargin1=px(16), lmargin2=px(560), spacing1=px(9))
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
        return " ".join(build_argv(preset.argv, params, strict=False))

    def aggiorna(self):
        params = self.params()
        termini = self.ricerca.get().lower().split()
        self.vis = [p for p in CATALOGO
                    if (self.filtro == "all" or p.risk == self.filtro)
                    and (self.area_filtro is None or p.area == self.area_filtro)
                    and all(w in f"{p.title} {p.desc} {p.area} {p.group} {self.testo(p, params)}".lower()
                            for w in termini)]
        self.sel = min(self.sel, max(len(self.vis) - 1, 0))
        for chiave, (c, nome) in self.chip.items():
            n = sum(1 for p in CATALOGO if chiave == "all" or p.risk == chiave)
            attivo = chiave == self.filtro
            c.config(text=f"{nome} {n}", bg=INK if attivo else BG, fg=BG if attivo else DIM,
                     highlightbackground=INK if attivo else LINE)
        for chiave, c in self.chip_area.items():
            attivo = chiave == self.area_filtro
            c.config(text=f"{chiave} {sum(1 for p in CATALOGO if p.area == chiave)}",
                     bg=INK if attivo else BG, fg=BG if attivo else DIM, highlightbackground=INK if attivo else LINE)
        self.disegna(params)

    def disegna(self, params):
        t = self.lista
        y = t.yview()[0]
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
        t.yview_moveto(y)

    def colora(self):
        for i in range(len(self.vis)):
            self.lista.tag_config(f"r{i}", background=SEL if i == self.sel else BG)

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
    def chiedi(self, etichetta, suggerimento, iniziale, **opzioni):
        return Chiedi(self, etichetta, suggerimento, iniziale, **opzioni).risultato

    def stato_accesso(self):
        if self.token:
            self.accesso.config(text="●  Token admin attivo · Esci", fg=RISCHIO["read"][1], cursor="hand2")
        elif self.token_ambiente:
            self.accesso.config(text="●  Token admin dall'ambiente", fg=DIM, cursor="arrow")
        else:
            self.accesso.config(text="○  Accedi con il token admin", fg=ACCENT, cursor="hand2")

    def accedi(self):
        """Chiede il token admin e lo verifica sul Worker. Vuoto o «Salta»: si lavora senza, e gli script
        lo chiedono nella loro finestra."""
        while True:
            token = self.chiedi("Token admin del Worker", SUGGERIMENTI["token"], "", nascosto=True,
                                ok="Entra", annulla="Salta")
            if not (token or "").strip():
                return
            self.root.config(cursor="watch")
            self.root.update_idletasks()
            try:
                verifica_token(token.strip())
            except LauncherError as e:
                messagebox.showwarning("Accesso non riuscito", str(e), parent=self.root)
                continue
            finally:
                self.root.config(cursor="")
            self.token = token.strip()
            self.stato_accesso()
            return

    def esci(self):
        self.token = None
        self.stato_accesso()

    def raccogli(self, preset):
        """Chiede i valori che il comando usa. False se chi ha fatto doppio clic ha annullato."""
        for chiave, etichetta, suggerimento in da_chiedere(preset, self.params()):
            valore = self.chiedi(etichetta, suggerimento, self.campi[chiave].get())
            if valore is None:
                return False
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
        riga = ps_line(argv)
        if p.risk != "read":
            testo = f"{p.title}\n\n{AVVISO[p.risk]}"
            if p.note:
                testo += f"\n{p.note}"
            if p.risk == "destroy" and "keep" in segnaposti(p.argv) and not self.campi["keep"].get().strip():
                testo += "\nNessun account da salvare: oltre al tuo utente non è protetto nessuno."
            if not messagebox.askokcancel("Eseguire?", f"{testo}\n\n{riga}", icon="warning",
                                          default="cancel", parent=self.root):
                return "break"
        try:
            launch(argv, self.token if usa_token(p) else None)
        except OSError as e:
            messagebox.showerror("Il comando non parte", str(e), parent=self.root)
        return "break"

    def corrente(self):
        return self.vis[self.sel] if self.vis else None


def main():
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)  # senza, su schermi ingranditi il testo è sfocato
    except (AttributeError, OSError):
        pass
    root = tk.Tk()
    app = App(root)
    if not app.token_ambiente:
        root.after(300, app.accedi)
    root.mainloop()


if __name__ == "__main__":
    main()
