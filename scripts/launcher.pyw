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
        "Perforce", "Rimozione", ["p4", "depot", "-d", "{team}"],
        "Cancella il depot del team, che il cleanup lascia di proposito.",
        (), "Solo se il depot è vuoto e il team è chiuso davvero.",
        [("Cancella il depot del team", [], "destroy")]),
    *_famiglia(
        "Perforce", "Rimozione", ["p4", "protect"],
        "Apre la tabella dei permessi.", (),
        "Cancella la riga write group NomeTeam * //NomeTeam/... e salva.",
        [("Togli la riga di permesso", [], "write")]),
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
MANCA = {"user": "lo username", "team": "il team"}
NOME = {"user": "username", "team": "team", "keep": "account"}
SEGNAPOSTI = {"{user}", "{team}", "{keep}"}
PAROLA_SICURA = re.compile(r"^[A-Za-z0-9_./\\:-][A-Za-z0-9_./\\:@=+-]*$")


def _valore(segnaposto, params, strict):
    """Valore validato di un segnaposto. keep è un elenco e può restare vuoto."""
    chiave = segnaposto[1:-1]
    grezzo = (params.get(chiave) or "").strip()
    nomi = [n.strip() for n in grezzo.split(",") if n.strip()] if chiave == "keep" else [grezzo]
    if chiave == "keep" and not nomi:
        return ""
    try:
        if not grezzo:
            raise LauncherError(f"Compila {MANCA[chiave]}.")
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
        valori = [_valore(t, params, strict) if t in SEGNAPOSTI else t for t in gruppo]
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


# ── Finestra ────────────────────────────────────────────────────
BG, DEEP, SEL, LINE = "#000000", "#0d0d0d", "#262626", "#3a3a3a"
INK, DIM, FAINT, ACCENT = "#f2f2f2", "#b0b0b0", "#808080", "#f5df74"
FILTRI = (("all", "Tutti"), ("read", "Legge"), ("write", "Scrive"), ("destroy", "Cancella"))
CAMPI = (("user", "Username"), ("team", "Team"))
# L'elenco di account compare nel dettaglio solo per i comandi che lo usano, col nome dell'opzione.
ELENCO = {"--keep": "Account da salvare", "--exclude": "Account da non esportare"}


class App:
    def __init__(self, root):
        self.root = root
        self.px = lambda n: round(n * root.winfo_fpixels("1i") / 96)
        self.vis, self.sel, self.filtro = [], 0, "all"
        self.ricerca = tk.StringVar()
        self.campi = {k: tk.StringVar() for k in ("user", "team", "keep")}
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
        self.chip = {}
        for chiave, nome in FILTRI:
            c = tk.Label(chips, bg=BG, fg=DIM, font=f["ui"], padx=px(12), pady=px(3), cursor="hand2",
                         highlightthickness=1, highlightbackground=LINE)
            c.pack(side="left", padx=(0, px(8)))
            c.bind("<Button-1>", lambda e, k=chiave: self._filtra(k))
            self.chip[chiave] = (c, nome)

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
        t.tag_config("area", font=f["area"], foreground=INK, lmargin1=px(16), spacing1=px(26), spacing3=px(2))
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
        self.d_elenco = tk.Frame(d, bg=DEEP)
        self.d_elenco.grid(row=4, column=0, columnspan=2, sticky="w", padx=pad, pady=(px(12), 0))
        self.d_elenco_nome = tk.Label(self.d_elenco, bg=DEEP, fg=INK, font=f["ui"])
        self.d_elenco_nome.pack(side="left")
        self._campo(self.d_elenco, "keep", 26, BG).pack(side="left", padx=px(10), ipady=px(3))
        tk.Label(self.d_elenco, text="separati da virgola, ad esempio admin,servicebot", bg=DEEP, fg=FAINT,
                 font=f["ui"]).pack(side="left")
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

    def _azzera(self, _event=None):
        self.filtro, self.sel = "all", 0
        self.ricerca.set("")
        self.aggiorna()

    def testo(self, preset, params):
        return " ".join(build_argv(preset.argv, params, strict=False))

    def aggiorna(self):
        params = self.params()
        termini = self.ricerca.get().lower().split()
        self.vis = [p for p in CATALOGO
                    if (self.filtro == "all" or p.risk == self.filtro)
                    and all(w in f"{p.title} {p.area} {p.group} {self.testo(p, params)}".lower() for w in termini)]
        self.sel = min(self.sel, max(len(self.vis) - 1, 0))
        for chiave, (c, nome) in self.chip.items():
            n = sum(1 for p in CATALOGO if chiave == "all" or p.risk == chiave)
            attivo = chiave == self.filtro
            c.config(text=f"{nome} {n}", bg=INK if attivo else BG, fg=BG if attivo else DIM,
                     highlightbackground=INK if attivo else LINE)
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
                t.insert("end", p.area + "\n", "area")
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
            self.d_elenco.grid_remove()
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
        opzione = next((v[0] for v in p.argv if isinstance(v, tuple) and "{keep}" in v), None)
        if opzione:
            self.d_elenco_nome.config(text=ELENCO[opzione])
            self.d_elenco.grid()
        else:
            self.d_elenco.grid_remove()
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
            if p.risk == "destroy" and "{keep}" in parole(p.argv) and not self.campi["keep"].get().strip():
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
