# rada, per chi ci lavora

Questa cartella è il progetto **rada**: la coda di ammissione per i job pesanti lanciati
da più sessioni di Claude Code sullo stesso Mac. Se stai leggendo questo file, sei nella
sessione dedicata a rada, ed è qui che va tutto il lavoro su rada. Le altre sessioni di
Eugenio sono su altro (ricerca XAI, scriba, molo, i siti) e non devono occuparsene.

Repo pubblico: https://github.com/nerln/rada (GPL-3.0, account `nerln`, `gh` è già
autenticato con permesso di admin).

## Il problema, in una riga

Quattro sessioni Claude Code su un MacBook Pro da 16 GB lanciano insieme PyTorch, Xcode,
Unity e ffmpeg, nessuna vede le altre, e la macchina si ferma. Misurato il 04/08/2026:
2992 MB di swap su 4096 e 88000 pageout.

## Le invarianti che non vanno rotte

Queste sono il progetto. Tutto il resto è dettaglio.

1. **Un hook rotto non deve poter fermare una sessione.** `bin/rada-gate` esce sempre
   con zero e stampa `{}` quando qualcosa non torna. Ogni percorso di errore nel wrapper
   finisce per eseguire il comando invece di bloccarlo.
2. **Il giudice non può affamare nessuno.** Il suo verdetto vale al massimo `CAP = 3`
   punti contro un'anzianità che ne vale uno ogni `TAU = 30` secondi, quindi la finestra
   di sorpasso è 90 secondi. Dopo `MANDATORY_AFTER = 600` secondi un biglietto entra in
   un insieme ordinato solo per arrivo da cui il giudice è escluso. Se tocchi queste tre
   costanti, i lemmi in `rada/sched.py` e i test in `tools/prova.py` vanno rifatti.
3. **La garanzia si enuncia in completamenti, non in minuti.** Una promessa in tempo
   reale sarebbe falsa, perché rada non uccide niente che una persona abbia avviato.
   Non scrivere mai nel README che un job parte entro N minuti.
4. **La decisione di ammissione e la registrazione del permesso stanno nella stessa
   transazione.** Separarle lascia una finestra in cui due job ricevono entrambi il via.
   È già successo.
5. **Il lock si pubblica già completo.** `os.link` di un file che contiene già il pid.
   Creare il lock e poi scrivere chi lo possiede lascia una finestra in cui un altro
   processo legge "nessun proprietario", conclude che è abbandonato e lo scavalca. È già
   successo.
6. **La riscrittura del comando sta su una riga sola.** I byte originali vanno su file e
   non passano mai da una shell; nella riga riscritta l'originale compare dentro un
   commento `#`, che contiene ogni metacarattere. La newline è l'unica cosa che potrebbe
   chiudere il commento, quindi va tolta.
7. **Con una sessione sola il gate si tira da parte.** Decide `rada/sessions.py`, e il
   gate lo interroga prima di riscrivere. Le condizioni per accodare sono due, in or:
   un'altra sessione ha toccato il gate dentro `WINDOW`, oppure esiste un lease o un
   biglietto. La seconda non è ridondante: una sessione che ha avviato un lavoro lungo e
   poi è rimasta zitta non emette battiti, e sparirebbe dal conteggio proprio mentre
   tiene occupata più memoria di tutte. Se il modulo solleva, il gate accoda come prima:
   il ripiego è il comportamento vecchio, non l'assenza di coda. I test in `tools/prova.py`
   che vogliono vedere una riscrittura devono chiamare `seed_other_session()` prima.

## I quattro fatti su Claude Code verificati a mano

Nessuno dei quattro sta nella documentazione. Sono stati verificati eseguendo sessioni
vere con un hook di prova, non letti.

0. Il payload di un hook PreToolUse contiene `session_id`, `transcript_path`, `cwd`,
   `prompt_id`, `permission_mode`, `hook_event_name`, `tool_name`, `tool_input` e
   `tool_use_id`. Verificato il 06/08/2026 con una sonda temporanea nel gate: senza
   `session_id` l'invariante 6 non starebbe in piedi.

1. `updatedInput` di un hook PreToolUse **viene onorato senza** dichiarare
   `permissionDecision: "allow"`.
2. I permessi sono valutati **sul comando riscritto**, non sull'originale. Quindi
   qualunque wrapper è un allargamento dei permessi, e va detto all'utente. Sta nel
   README sotto l'avviso in grassetto: non toglierlo.
3. Il timeout di default degli hook è alto (centinaia di secondi), ma un hook che
   aspetta non ha modo di mostrare a che punto è. Per questo rada non aspetta dentro
   l'hook: riscrive, e l'attesa la fa un processo normale.

## Come è fatto

```
rada/cli.py        il CLI e il wrapper che aspetta, esegue e misura
bin/rada           lo stesso comando, lanciato dal repo senza installare
bin/rada-gate      hook PreToolUse, sh puro, un fork solo, ~3 ms
bin/rada-gate.py   secondo stadio, raggiunto solo dai comandi che somigliano a pesanti
rada/mem.py        contabilità della memoria su Apple Silicon, ctypes, niente psutil
rada/store.py      stato JSON + lock atomico
rada/sched.py      ammissione, anzianità, insieme obbligatorio, prenotazione
rada/judge.py      il giudice e la sua validazione
rada/setup_claude.py  install, uninstall, doctor
tools/prova.py     78 controlli, un paio di secondi, nessun modello caricato
tools/schermate.py rigenera le immagini del README da output vero
tools/prova-giudice.py  prova viva di resistenza all'iniezione, chiama il modello
```

## Prima di ogni commit

```bash
python3 tools/prova.py          # deve dire 0 failed
python3 tools/schermate.py      # se hai cambiato l'output di status o doctor
python3 ~/dev/scriba/tools/stylecheck.py README.md README.it.md
```

Lo stylecheck è vincolante sui README: niente trattini lunghi, niente termini della lista
vietata, niente contrapposizioni "X, non Y" usate come slogan. Segnala anche `--` dentro
i blocchi di codice: quello è un falso positivo e si ignora.

## Lo stile di scrittura di Eugenio

Vale per README, commit e commenti. Niente trattini lunghi. Niente prima persona nei
testi pubblici. Le motivazioni si scrivono come fatti già successi, non come principi:
"un esperimento da sei giga ha prenotato per tre ore e mezza e otto job sono rimasti
fermi dietro" invece di "le prenotazioni possono causare starvation". I commenti nel
codice spiegano **perché**, non cosa.

## Cosa è aperto

- **La harness del giudice è chiusa** (04/08 sera): prompt di sistema in
  `rada/judge.sys`, `--tools ""`, `--safe-mode`, `--setting-sources ""` (quindi nessun
  hook, nemmeno quello di rada), `--strict-mcp-config` con lista vuota,
  `--disable-slash-commands`, `--no-session-persistence`, `--json-schema`, cartella di
  lavoro vuota, ambiente ridotto a una lista corta, prompt su stdin e non in argv. Il
  contesto resta nuovo a ogni verdetto, ed è la proprietà su cui poggia tutto il resto.
  Una sessione di giudice persistente è stata progettata e scartata: ricorderebbe di più,
  e ricorderebbe anche un'iniezione.
  **Quello che passa ancora**: `tools/prova-giudice.py` misura sei attacchi in confronto
  appaiato. Uno su sei ha funzionato, l'appello alla scadenza ravvicinata, e la
  motivazione del giudice ripeteva la frase iniettata. Compra novanta secondi. Se cambi
  `rada/judge.sys`, rilancia quel test e aggiorna la tabella nei due README con i numeri
  nuovi: la tabella riporta una misura, non una promessa.
- **Il lavoro fuori da Bash non è intercettato.** Un tool MCP che compila un progetto
  Xcode dentro il proprio server è invisibile a un hook su Bash.
- **Nessuna versione taggata.** `rada/__init__.py` ha `__version__` e `SCHEMA`; il numero
  di schema serve a far convivere due versioni durante un aggiornamento e i lettori che
  non lo riconoscono devono lasciar passare il job, non romperlo.
- **Provato solo su macOS Apple Silicon.** La CI gira su macos-latest con Python 3.9 e
  3.12. Dove non riesce a leggere la memoria, rada lascia passare tutto.

## Cosa non fare

- Non aggiungere un demone. L'architettura senza processi residenti è stata scelta da un
  collegio di tre giudici con verdetto unanime, e il motivo è operativo: niente da
  riavviare dopo un reboot, niente da diagnosticare quando non parte, e disinstallare
  lascia la macchina identica a prima.
- Non far uccidere niente a rada. Se un job tiene memoria per sempre, rada lo dice e
  aspetta.
- Non promettere un limite in secondi.
