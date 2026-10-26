#!/usr/bin/env python3
"""
launcher.pyw

Elenco cliccabile dei comandi di p4-infrastructure. Doppio clic su una riga e il
comando parte in una finestra di PowerShell aperta nella cartella della repo.

L'app non vede mai ciò che digiti lì dentro: password e token di Perforce e del
Worker si scrivono nella finestra del comando, come sempre. Per questo la
finestra resta aperta (-NoExit) e UTF-8 è già impostato: le due righe di
sessione dell'elenco a mano non servono più.

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

import p4_common as p4c

REPO = Path(__file__).resolve().parent.parent
CREATE_NEW_CONSOLE = getattr(subprocess, "CREATE_NEW_CONSOLE", 0x10)


class LauncherError(ValueError):
    """Un valore mancante o rifiutato: il comando non parte."""


# ── Catalogo ────────────────────────────────────────────────────
class Preset(NamedTuple):
    area: str    # Perforce o Worker: cosa tocca il comando
    group: str
    title: str
    argv: list   # parole del comando; una tupla è un gruppo opzionale, omesso se vuoto
    risk: str    # read, write, destroy
    what: str
    asks: tuple  # (domanda, solo_in_esecuzione): cosa chiederà lo script, in ordine
    note: str


RISCHIO = {
    "read": ("legge", "#7fd99a"),
    "write": ("scrive", "#f5df74"),
    "destroy": ("cancella", "#ff8793"),
}
AVVISO = {
    "write": "Scrive su Perforce, sul KV o sul Worker.",
    "destroy": "Cancella e non si torna indietro.",
}

TOKEN = ("token admin", False)
CONNESSIONE = (("server", False), ("utente Perforce", False), ("password", False))


def _famiglia(area, group, base, what, asks, note, presets):
    return [Preset(area, group, title, base + args, risk, what, asks, note) for title, args, risk in presets]


def _p4(group, title, argv, risk, what, note=""):
    return _famiglia("p4", group, argv, what, (), note, [(title, [], risk)])


CATALOGO = [
    # ── Perforce: il server, gli utenti, i gruppi e i depot ──
    *_famiglia(
        "Perforce", "Provisioning", ["python", "scripts/perforce_provision.py"],
        "Crea utenti, gruppi, depot e permessi dai record pending del KV.",
        (TOKEN, *CONNESSIONE,
         ("password iniziale studenti (due volte, Invio per non impostarla)", False),
         ("il team esiste già: aggiungere? [s/N]", True)),
        "Un gruppo o un depot già esistente senza la riga di permesso non è di questo flusso: "
        "il team viene rifiutato e il record va in error.",
        [("Anteprima del provisioning", ["--dry-run"], "read"),
         ("Provisioning", [], "write"),
         ("Anteprima dei retry", ["--retry-errors", "--dry-run"], "read"),
         ("Riprova i record in error", ["--retry-errors"], "write")]),

    *_famiglia(
        "Perforce", "Rimozione", ["python", "scripts/perforce_cleanup.py", "--user", "{user}"],
        "Toglie lo studente da Perforce e dal KV: gruppi, changelist pending, workspace e, se non gli resta accesso, l'account.",
        (TOKEN, *CONNESSIONE, ("CONFIRM", True)),
        "Irreversibile. Il depot del team e la sua riga in p4 protect restano: vanno tolti a mano. "
        "A fine esecuzione lo script elenca ruoli e canali Discord da cercare.",
        [("Anteprima della rimozione", ["--dry-run"], "read"),
         ("Rimuovi lo studente", [], "destroy"),
         ("Rimuovi da un solo team", ["--team", "{team}"], "destroy"),
         ("Rimuovi dai gruppi, tieni l'account", ["--keep-account"], "destroy"),
         ("Rimuovi e cancella il record", ["--delete-record"], "destroy")]),
    *_famiglia(
        "Perforce", "Rimozione", ["python", "scripts/perforce_prune.py"],
        "Rimuove gli account che non stanno in nessun gruppo con protezioni.",
        (*CONNESSIONE, ("CONFIRM", True)),
        "Il tuo utente è sempre protetto. Service account e altri admin vanno indicati in «Account da salvare».",
        [("Anteprima degli account orfani", ["--dry-run", ("--keep", "{keep}")], "read"),
         ("Rimuovi gli account orfani", [("--keep", "{keep}")], "destroy")]),

    *_famiglia(
        "Perforce", "Import", ["python", "scripts/export_p4_users.py"],
        "Porta gli account già presenti su Perforce nel KV come existing, così il provisioning non li ricrea.",
        (*CONNESSIONE, ("token admin", True)),
        "L'utente con cui ti connetti non finisce mai nell'export.",
        [("Anteprima dell'import", ["--dry-run", ("--exclude", "{keep}")], "read"),
         ("Importa nel KV", [("--exclude", "{keep}")], "write"),
         ("Importa senza sovrascrivere", ["--skip-existing", ("--exclude", "{keep}")], "write")]),
    *_famiglia("Perforce", "Test", ["python", "-m", "unittest", "discover", "-s", "test"],
               "Test degli script.", (), "Non chiedono credenziali.",
               [("Test degli script", [], "read")]),

    # ── p4: comandi diretti del client, per l'admin ──
    *_p4("Server e account", "Accedi a Perforce", ["p4", "login"], "read",
         "Apre la sessione con l'utente impostato su questa macchina (p4 set).",
         "Gli altri comandi p4 la richiedono. La password si scrive nella finestra."),
    *_p4("Server e account", "Info sul server", ["p4", "info"], "read",
         "Versione del server, licenza, Case Handling e statistiche."),
    *_p4("Server e account", "Licenze in uso", ["p4", "license", "-u"], "read",
         "Quanti utenti usano la licenza, il limite e la scadenza.", "Serve un admin."),
    *_p4("Server e account", "Utenti del server", ["p4", "users"], "read", "Tutti gli account."),
    *_p4("Server e account", "Depot del server", ["p4", "depots"], "read", "Tutti i depot."),

    *_p4("Un utente", "Gruppi di un utente", ["p4", "groups", "{user}"], "read",
         "I gruppi di cui l'utente fa parte."),
    *_p4("Un utente", "Permessi di un utente", ["p4", "protects", "-u", "{user}"], "read",
         "Le righe della tabella protect che valgono per l'utente.", "Serve un super user."),
    *_p4("Un utente", "Workspace di un utente", ["p4", "clients", "-u", "{user}"], "read",
         "I workspace dell'utente: il nome serve ai comandi sui workspace."),
    *_p4("Un utente", "Changelist pending di un utente", ["p4", "changes", "-s", "pending", "-u", "{user}"], "read",
         "Le changelist pending dell'utente: il numero serve ai comandi sulle changelist."),
    *_p4("Un utente", "File aperti da un utente", ["p4", "opened", "-a", "-u", "{user}"], "read",
         "I file che l'utente tiene aperti, in qualsiasi workspace."),
    *_p4("Un utente", "Reimposta la password di un utente", ["p4", "passwd", "{user}"], "write",
         "Imposta una nuova password per l'utente.",
         "Serve un super user. La nuova password si scrive nella finestra, non sulla riga di comando."),

    *_p4("Changelist", "File aperti in una changelist", ["p4", "opened", "-a", "-c", "{change}"], "read",
         "I file aperti nella changelist, con il workspace che li tiene."),
    *_p4("Changelist", "Sblocca i file di una changelist", ["p4", "unlock", "-f", "-c", "{change}"], "write",
         "Sblocca i file bloccati di una changelist, anche di un altro utente.", "Serve un admin."),
    *_p4("Changelist", "Cancella gli shelve di una changelist", ["p4", "shelve", "-d", "-f", "-c", "{change}"],
         "destroy", "Cancella i file in shelve di una changelist, anche di un altro utente.",
         "Chi ha una risoluzione in sospeso su quello shelve non potrà più unirne il contenuto."),
    *_p4("Changelist", "Cancella una changelist, anche di un altro utente", ["p4", "change", "-d", "-f", "{change}"],
         "destroy", "Cancella la changelist pending.",
         "Con file aperti o in shelve prima servono il revert del workspace e la cancellazione degli shelve: "
         "lo script di rimozione li fa da solo."),

    *_p4("Workspace", "Revert dei file aperti in un workspace", ["p4", "revert", "-C", "{client}", "//..."],
         "destroy", "Annulla i file aperti in un workspace, anche di un altro utente.",
         "Le modifiche non ancora inviate vanno perse. Serve un admin."),
    *_p4("Workspace", "Cancella un workspace, anche di un altro utente", ["p4", "client", "-d", "-f", "{client}"],
         "destroy", "Cancella il workspace anche se è bloccato; i file aperti vengono annullati.",
         "Se dentro ci sono degli shelve p4 rifiuta: usa la variante con gli shelve."),
    *_p4("Workspace", "Cancella un workspace e i suoi shelve", ["p4", "client", "-d", "-f", "-Fd", "{client}"],
         "destroy", "Come sopra, e cancella anche gli shelve del workspace.", "Gli shelve cancellati non si recuperano."),

    *_p4("Team chiuso", "Cancella il depot del team", ["p4", "depot", "-d", "{team}"], "destroy",
         "Cancella il depot del team, che il cleanup lascia di proposito.",
         "Solo se il depot è vuoto e il team è chiuso davvero."),
    *_p4("Team chiuso", "Togli la riga di permesso", ["p4", "protect"], "write",
         "Apre la tabella dei permessi.", "Cancella la riga write group NomeTeam * //NomeTeam/... e salva."),
    *_p4("Team chiuso", "Anteprima: cosa cancellerebbe sul depot del team", ["p4", "obliterate", "//{team}/..."], "read",
         "Mostra i file che verrebbero cancellati, senza toccare niente.", "Senza -y p4 fa solo l'anteprima."),
    *_p4("Team chiuso", "Cancella per sempre i file del depot del team", ["p4", "obliterate", "-y", "//{team}/..."],
         "destroy", "Cancella per sempre i file del depot e la loro storia.",
         "Il server non conserva traccia che esistessero. Fai prima l'anteprima; poi il depot vuoto si cancella. "
         "Serve un admin."),

    # ── Worker: il KV e Cloudflare ──
    *_famiglia(
        "Worker", "Record nel KV", ["python", "scripts/kv_status.py"],
        "Conta i record del KV per status e per team.",
        (TOKEN,), "CSV e XLSX contengono dati personali: finiscono nella cartella della repo, già nel .gitignore.",
        [("Stato del KV", [], "read"),
         ("Elenca gli username", ["--usernames"], "read"),
         ("Solo i pending", ["--status", "pending"], "read"),
         ("Solo gli error", ["--status", "error"], "read"),
         ("Esporta in XLSX", ["--xlsx", "utenti.xlsx"], "read"),
         ("Esporta in CSV", ["--csv", "utenti.csv"], "read")]),
    *_famiglia("Worker", "Pubblicazione", ["npx", "wrangler@4.150.0", "deploy"],
               "Pubblica worker.js in produzione, con wrangler.jsonc.", (),
               "Il Worker si chiama ancora perforce-registration: il nome sta nell'URL scritto negli script degli studenti.",
               [("Pubblica il Worker", [], "write")]),
    *_famiglia("Worker", "Pubblicazione", ["npx", "wrangler@4.150.0", "secret", "put", "ADMIN_TOKEN"],
               "Chiede il nuovo token admin e lo salva come Secret del Worker.", (),
               "Cloudflare non lo rimostra dopo il salvataggio: aggiorna anche il Vaultwarden.",
               [("Cambia il token admin del Worker", [], "write")]),
    *_famiglia("Worker", "Test", ["npm", "test"], "Test del Worker e del form.", (),
               "Nessun test tocca Perforce o il KV.",
               [("Test del Worker e del form", [], "read")]),
]


def parole(argv):
    return [t for voce in argv for t in (voce if isinstance(voce, tuple) else (voce,))]


def domande(preset):
    """Le domande che lo script farà: in anteprima mancano quelle che ci sono solo eseguendo."""
    dry = "--dry-run" in preset.argv
    return [d for d, solo_esecuzione in preset.asks if not (dry and solo_esecuzione)]


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


def launch(argv):
    """Apre una finestra di PowerShell nella repo, ci esegue il comando e la lascia aperta.

    Qui serve per forza una shell, a differenza dei comandi p4 degli script: valori
    validati da check_p4_name e parole quotate da ps_quote, mai testo libero."""
    env = dict(os.environ, PYTHONUTF8="1")
    return subprocess.Popen(
        ["powershell.exe", "-NoLogo", "-NoExit", "-Command", ps_line(argv)],
        cwd=REPO, env=env, creationflags=CREATE_NEW_CONSOLE,
    )


AREE = {
    "Perforce": "Gli script di provisioning, rimozione e import.",
    "p4": "Comandi diretti del client: usano server e utente di questa macchina (p4 set) e un login già fatto.",
    "Worker": "Il KV e Cloudflare.",
}

# Valori che il dettaglio chiede solo ai comandi che li usano (username e team stanno in testata).
# L'elenco di account prende il nome dall'opzione: --keep salva, --exclude non esporta.
ELENCO = {"--keep": "Account da salvare", "--exclude": "Account da non esportare"}
SUGGERIMENTI = {
    "keep": "separati da virgola, ad esempio admin,servicebot",
    "change": "solo cifre; lo trovi con «Changelist pending di un utente»",
    "client": "il nome esatto; lo trovi con «Workspace di un utente»",
}
ETICHETTE = {"change": "Numero della changelist", "client": "Workspace"}


def campo_extra(preset):
    """Il campo che il dettaglio mostra per questo comando: (chiave, etichetta, suggerimento), o None."""
    for voce in preset.argv:
        if isinstance(voce, tuple) and "{keep}" in voce:
            return "keep", ELENCO[voce[0]], SUGGERIMENTI["keep"]
    for chiave in ("change", "client"):
        if chiave in segnaposti(preset.argv):
            return chiave, ETICHETTE[chiave], SUGGERIMENTI[chiave]
    return None


# ── Finestra ────────────────────────────────────────────────────
BG, DEEP, SEL, LINE = "#000000", "#0d0d0d", "#262626", "#3a3a3a"
INK, DIM, FAINT, ACCENT = "#f2f2f2", "#b0b0b0", "#808080", "#f5df74"
FILTRI = (("all", "Tutti"), ("read", "Legge"), ("write", "Scrive"), ("destroy", "Cancella"))
CAMPI = (("user", "Username"), ("team", "Team"))


class App:
    def __init__(self, root):
        self.root = root
        self.px = lambda n: round(n * root.winfo_fpixels("1i") / 96)
        self.vis, self.sel, self.filtro, self.area_filtro = [], 0, "all", None
        self.ricerca = tk.StringVar()
        self.campi = {k: tk.StringVar() for k in ("user", "team", "keep", "change", "client")}
        self._font()
        self._costruisci()
        self.ricerca.trace_add("write", self._ricerca_cambiata)
        for var in self.campi.values():
            var.trace_add("write", lambda *_: self.aggiorna())
        self.aggiorna()
        self.cerca.focus_set()

    def params(self):
        return {k: v.get() for k, v in self.campi.items()}

    # ── aspetto ──
    def _font(self):
        presenti = set(tkfont.families())
        ui = next(f for f in ("Segoe UI Variable Text", "Segoe UI", "Arial") if f in presenti)
        mono = next(f for f in ("Cascadia Mono", "Consolas", "Courier New") if f in presenti)
        self.f = {"ui": (ui, 10), "ui_b": (ui, 10, "bold"), "titolo": (ui, 11, "bold"),
                  "area": (ui, 14, "bold"), "piccolo": (ui, 9), "mono": (mono, 10), "mono_g": (mono, 13)}

    def _campo(self, genitore, chiave, larghezza, fondo):
        return tk.Entry(genitore, textvariable=self.campi[chiave], width=larghezza, bg=fondo, fg=INK,
                        insertbackground=ACCENT, relief="flat", font=self.f["mono"], highlightthickness=1,
                        highlightbackground=LINE, highlightcolor=ACCENT)

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
        root.geometry(f"{px(1040)}x{px(800)}")
        root.minsize(px(780), px(600))
        root.columnconfigure(0, weight=1)
        root.rowconfigure(3, weight=1)
        m = px(22)

        testa = tk.Frame(root, bg=BG)
        testa.grid(row=0, sticky="ew", padx=m, pady=(px(18), 0))
        tk.Label(testa, text="p4-infrastructure", bg=BG, fg=DIM, font=f["mono"]).pack(side="left")
        for chiave, etichetta in reversed(CAMPI):
            self._campo(testa, chiave, 16, DEEP).pack(side="right", padx=(px(6), 0), ipady=px(3))
            tk.Label(testa, text=etichetta, bg=BG, fg=DIM, font=f["ui"]).pack(side="right", padx=(px(14), 0))

        barra = tk.Frame(root, bg=DEEP, highlightthickness=1, highlightbackground=LINE)
        barra.grid(row=1, sticky="ew", padx=m, pady=(px(16), 0))
        tk.Label(barra, text="PS>", bg=DEEP, fg=ACCENT, font=f["mono_g"]).pack(side="left", padx=(px(14), px(8)))
        self.cerca = tk.Entry(barra, textvariable=self.ricerca, bg=DEEP, fg=INK, insertbackground=ACCENT,
                              relief="flat", font=f["mono_g"], highlightthickness=0)
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
        corpo.grid(row=3, sticky="nsew", padx=(m, px(8)), pady=(px(8), 0))
        corpo.columnconfigure(0, weight=1)
        corpo.rowconfigure(0, weight=1)
        self.lista = tk.Text(corpo, bg=BG, fg=INK, bd=0, highlightthickness=0, wrap="word", cursor="arrow",
                             state="disabled", takefocus=0, padx=0, tabs=(px(126),), font=f["ui"])
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
        t.tag_config("rischio", font=f["ui_b"], lmargin1=px(16), spacing1=px(9))
        for chiave, (_, colore) in RISCHIO.items():
            t.tag_config("c_" + chiave, foreground=colore)
        t.tag_config("titolo", font=f["titolo"], foreground=INK)
        t.tag_config("comando", font=f["mono"], foreground=DIM, lmargin1=px(126), lmargin2=px(126), spacing3=px(9))
        t.tag_config("vuoto", foreground=DIM, lmargin1=px(16), spacing1=px(20))

        d = tk.Frame(root, bg=DEEP)
        d.grid(row=4, sticky="ew", pady=(px(8), 0))
        d.columnconfigure(0, weight=1)
        pad = px(22)
        self.d_titolo = tk.Label(d, bg=DEEP, fg=INK, font=f["titolo"], anchor="w")
        self.d_titolo.grid(row=0, column=0, sticky="w", padx=(pad, 0), pady=(px(14), 0))
        self.d_rischio = tk.Label(d, bg=DEEP, font=f["ui_b"])
        self.d_rischio.grid(row=0, column=1, sticky="e", padx=pad, pady=(px(14), 0))
        self.d_testi = []
        for riga, colore in ((1, DIM), (2, DIM), (3, FAINT)):
            lbl = tk.Label(d, bg=DEEP, fg=colore, font=f["ui"], anchor="w", justify="left")
            lbl.grid(row=riga, column=0, columnspan=2, sticky="w", padx=pad, pady=(px(4), 0))
            self.d_testi.append(lbl)
        d.bind("<Configure>", lambda e: [l.config(wraplength=e.width - 2 * pad) for l in self.d_testi])
        self.d_extra = tk.Frame(d, bg=DEEP)
        self.d_extra.grid(row=4, column=0, columnspan=2, sticky="w", padx=pad, pady=(px(12), 0))
        self.d_extra_nome = tk.Label(self.d_extra, bg=DEEP, fg=INK, font=f["ui"])
        self.d_extra_nome.pack(side="left")
        self.d_extra_campo = self._campo(self.d_extra, "keep", 26, BG)
        self.d_extra_campo.pack(side="left", padx=px(10), ipady=px(3))
        self.d_extra_nota = tk.Label(self.d_extra, bg=DEEP, fg=FAINT, font=f["ui"])
        self.d_extra_nota.pack(side="left")
        cmd = tk.Frame(d, bg=BG)
        cmd.grid(row=5, column=0, columnspan=2, sticky="ew", padx=pad, pady=px(14))
        cmd.columnconfigure(0, weight=1)
        self.d_cmd = tk.Label(cmd, bg=BG, fg=INK, font=f["mono"], anchor="w", justify="left",
                              padx=px(12), pady=px(10), wraplength=px(640))
        self.d_cmd.grid(row=0, column=0, sticky="ew")
        self.b_copia = tk.Button(cmd, text="Copia", command=self.copia, bg=SEL, fg=INK, activebackground=LINE,
                                 activeforeground=INK, relief="flat", bd=0, font=f["ui_b"], padx=px(16),
                                 cursor="hand2")
        self.b_copia.grid(row=0, column=1, sticky="ns", padx=(0, px(1)))
        self.b_esegui = tk.Button(cmd, text="Esegui", command=self.esegui, bg=INK, fg=BG, activebackground=ACCENT,
                                  activeforeground=BG, relief="flat", bd=0, font=f["ui_b"], padx=px(22),
                                  cursor="hand2")
        self.b_esegui.grid(row=0, column=2, sticky="ns")

        self.stato_lbl = tk.Label(root, bg=DEEP, fg=FAINT, font=f["piccolo"], anchor="w", padx=pad, pady=px(7))
        self.stato_lbl.grid(row=5, sticky="ew")

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
                    and all(w in f"{p.title} {p.area} {p.group} {self.testo(p, params)}".lower() for w in termini)]
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
        self.dettaglio()

    def disegna(self, params):
        t, px = self.lista, self.px
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
            t.insert("end", "\t" + p.title + "\n", (riga, "titolo"))
            t.insert("end", self.testo(p, params) + "\n", (riga, "comando"))
            t.tag_bind(riga, "<Button-1>", lambda e, i=i: self.scegli(i))
            t.tag_bind(riga, "<Double-Button-1>", lambda e, i=i: self.doppio(i))
            t.tag_bind(riga, "<Enter>", lambda e: t.config(cursor="hand2"))
            t.tag_bind(riga, "<Leave>", lambda e: t.config(cursor="arrow"))
        if not self.vis:
            t.insert("end", "Nessun comando per questa ricerca. Prova con una parola del comando: "
                            "cleanup, dry-run, xlsx.", "vuoto")
        t.config(state="disabled")
        self.colora()
        t.yview_moveto(y)

    def colora(self):
        for i in range(len(self.vis)):
            self.lista.tag_config(f"r{i}", background=SEL if i == self.sel else BG)

    def scegli(self, i):
        self.sel = i
        self.colora()
        self.dettaglio()
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
            self.dettaglio()
            self.lista.see(self.lista.tag_ranges(f"r{self.sel}")[0])
        return "break"

    # ── dettaglio ──
    def corrente(self):
        return self.vis[self.sel] if self.vis else None

    def dettaglio(self):
        p = self.corrente()
        stato = "normal" if p else "disabled"
        self.b_esegui.config(state=stato)
        self.b_copia.config(state=stato)
        if not p:
            self.d_titolo.config(text="")
            self.d_rischio.config(text="")
            self.d_cmd.config(text="")
            self.d_extra.grid_remove()
            for lbl in self.d_testi:
                lbl.grid_remove()
            return
        etichetta, colore = RISCHIO[p.risk]
        self.d_titolo.config(text=p.title)
        self.d_rischio.config(text=etichetta, fg=colore)
        chiede = domande(p)
        for lbl, testo in zip(self.d_testi, (p.what, "Ti chiederà, in ordine: " + " › ".join(chiede) if chiede else "", p.note)):
            lbl.config(text=testo)
            lbl.grid() if testo else lbl.grid_remove()
        extra = campo_extra(p)
        if extra:
            chiave, nome, suggerimento = extra
            self.d_extra_nome.config(text=nome)
            self.d_extra_campo.config(textvariable=self.campi[chiave])
            self.d_extra_nota.config(text=suggerimento)
            self.d_extra.grid()
        else:
            self.d_extra.grid_remove()
        self.d_cmd.config(text="PS> " + self.testo(p, self.params()))
        self.b_esegui.config(text="Esegui" if p.risk == "read" else "Esegui…")
        self.stato("Doppio clic o Invio per eseguire. Password e token si scrivono nella finestra che si apre.")

    def stato(self, testo):
        self.stato_lbl.config(text=testo)

    # ── azioni ──
    def argv(self, preset):
        try:
            return build_argv(preset.argv, self.params())
        except LauncherError as e:
            messagebox.showwarning("Manca un dato", str(e), parent=self.root)
            return None

    def esegui(self, _event=None):
        p = self.corrente()
        argv = self.argv(p) if p else None
        if argv is None:
            return "break"
        riga = ps_line(argv)
        if p.risk != "read":
            testo = f"{p.title}\n\n{AVVISO[p.risk]}"
            if p.risk == "destroy" and "keep" in segnaposti(p.argv) and not self.campi["keep"].get().strip():
                testo += "\nNessun account da salvare: oltre al tuo utente non è protetto nessuno."
            if not messagebox.askokcancel("Eseguire?", f"{testo}\n\n{riga}", icon="warning",
                                          default="cancel", parent=self.root):
                return "break"
        try:
            launch(argv)
        except OSError as e:
            messagebox.showerror("Il comando non parte", str(e), parent=self.root)
            return "break"
        self.stato(f"Avviato in una nuova finestra: {riga}")
        return "break"

    def copia(self):
        p = self.corrente()
        argv = self.argv(p) if p else None
        if argv is not None:
            self.root.clipboard_clear()
            self.root.clipboard_append(ps_line(argv))
            self.stato(f"Copiato: {ps_line(argv)}")


def main():
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)  # senza, su schermi ingranditi il testo è sfocato
    except (AttributeError, OSError):
        pass
    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
