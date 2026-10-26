#!/usr/bin/env python3
"""
runner.py

Esegue uno degli script admin dentro l'eseguibile, senza Python installato:

    p4-runner.exe kv_status --usernames
    p4-runner.exe perforce_provision --dry-run

È il compagno da console di "P4 Infrastructure.exe": il lanciatore lo apre in una
finestra di PowerShell al posto di «python scripts/<nome>.py». Da sorgente non serve.
"""

import sys

SCRIPT = ("export_p4_users", "kv_status", "perforce_cleanup", "perforce_provision", "perforce_prune")


def carica(nome):
    # Import espliciti: PyInstaller li vede e mette gli script dentro l'eseguibile.
    import export_p4_users
    import kv_status
    import perforce_cleanup
    import perforce_provision
    import perforce_prune
    moduli = {
        "export_p4_users": export_p4_users, "kv_status": kv_status, "perforce_cleanup": perforce_cleanup,
        "perforce_provision": perforce_provision, "perforce_prune": perforce_prune,
    }
    return moduli[nome]


def main(argv):
    if not argv or argv[0] not in SCRIPT:
        print("Uso: p4-runner <script> [opzioni]")
        print("Script: " + ", ".join(SCRIPT))
        return 2
    # L'output usa caratteri di box-drawing: senza UTF-8 muore su una console non UTF-8.
    for flusso in (sys.stdout, sys.stderr):
        if hasattr(flusso, "reconfigure"):
            flusso.reconfigure(encoding="utf-8")
    modulo = carica(argv[0])
    sys.argv = [argv[0] + ".py", *argv[1:]]
    modulo.main()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
