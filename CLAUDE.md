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
7. **La finestra non decide niente.** `macapp/` legge `rada status --json` e disegna la
   risposta, e ogni bottone è un comando che si sarebbe potuto scrivere a mano. Dentro non
   ci va nessuna copia della regola di ammissione: due scheduler non sarebbero d'accordo
   proprio il giorno in cui conta. Le frasi sul perché un job aspetta arrivano da
   `sched.py` come stringhe e non vengono riscritte in Swift.
8. **Le viste leggono da una copia ripulita.** Leggere la coda non prende il lock, quindi
   rada non si accorge che un processo è morto finché qualcun altro non scrive. Senza la
   pulizia in lettura, su una macchina tranquilla restavano a schermo job finiti un'ora
   prima con la loro memoria ancora contata contro il budget. `cli.swept()` fa la pulizia
   su una copia, le due viste prendono i numeri da lì, e quello che la pulizia toglierebbe
   viene riportato a parte come `left_behind`. A scrivere davvero è solo `rada reap`.
9. **Quando rada non tiene niente, si tira da parte.** (Da non confondere con la 10:
   quella guarda se c'è un'altra sessione, questa se rada tiene un posto assegnato.) Con
   `d["leases"]` vuoto il biglietto **più vecchio per arrivo** (non la testa: sulla testa
   cadono i punti del giudice, e questo potere è più di una prenotazione) viene misurato
   su `sched.alone_bar` (`sched.oldest` salta i trattenuti e i forzati a più tardi, o uno
   solo di loro spegne la regola per tutta la coda), cioè quanto è libero più `TOTAL // ALONE_SWAP` di paginazione,
   invece che sul budget. A fermarlo restano quattro letture del kernel: pressione al
   livello massimo, la percentuale del killer sotto la soglia, swap oltre metà della
   memoria della macchina, e meno di `DISK_FLOOR` liberi sul disco da cui si fanno i file
   di swap (`sched.kernel_says_stop`). La soglia deve muoversi con `used`: per un
   pomeriggio è stata `TOTAL - wired`, e su questa macchina faceva passare 12 GB con 13 GB
   già residenti. `ALONE_GAP` separa due ammissioni di questo tipo, e
   `outstanding_alone` tiene quello che il job precedente ha promesso contro la soglia
   finché non compare in `used` o finché non passa `ALONE_WATCH`: aspettare qualche
   secondo non basta, un checkpoint ci mette di più ad arrivare nel residente. Il motivo della regola è misurato: su
   questo portatile, coda vuota, un job dichiarato a 4 GB aspettava memoria tenuta da
   browser ed editor, e rada aveva già stabilito che nessun job in coda gliela avrebbe
   restituita. A limitarla a un job per volta è `not d["leases"]`, che vale solo perché la
   decisione e il permesso stanno nella stessa transazione (invariante 4): appena il posto
   è assegnato, la regola si spegne per tutti gli altri. Se aggiungi un terzo punto di
   ammissione, deve concedere dentro la transazione in cui decide, o questa regola salta.
   `RADA_ALONE=0` la disattiva.
10. **Con una sessione sola il gate si tira da parte.** Decide `rada/sessions.py`, e il
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
macapp/            la finestra: SwiftUI, macOS 14+, nessuna dipendenza, niente .xcodeproj
rada/mcp.py        ask, queue, release come tool MCP: ammissione senza esecuzione
bin/rada-mcp       il server MCP, lo lancia Claude Code
tools/prova.py     245 controlli, un paio di secondi, nessun modello caricato
tools/schermate.py rigenera le immagini a terminale del README da output vero
tools/schermate-app.sh  fotografa la finestra sulla stessa coda inventata
tools/prova-giudice.py  prova viva di resistenza all'iniezione, chiama il modello
```

## Prima di ogni commit

```bash
python3 tools/prova.py          # deve dire 0 failed
swift test --package-path macapp   # se hai toccato la finestra
python3 tools/schermate.py      # se hai cambiato l'output di status o doctor
./tools/schermate-app.sh        # se hai cambiato il layout della finestra
python3 ~/dev/scriba/tools/stylecheck.py README.md README.it.md docs/index.html
```

Le schermate si rifanno solo da questi due script: la coda che si vede nelle immagini è
inventata e sta in `tools/schermate.py`, e la macchina su cui gira è dichiarata lì dentro
(`MACHINE`, `BUDGET`) invece di essere quella vera, altrimenti il budget della prima riga
e la pressione della seconda raccontano due macchine diverse. Le foto della finestra
passano da `open`, non dall'eseguibile dentro il bundle: lanciato per path, dopo qualche
giro, si è ritrovato senza finestra e da uccidere.

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
  Xcode dentro il proprio server è invisibile a un hook su Bash. Da `rada/mcp.py` un
  agente può chiederlo lui un posto, ma resta volontario: il gate obbligatorio è ancora
  solo quello su Bash.
- **Un posto preso via MCP lo restituisce solo chi lo ha preso.** Il server non vede il
  processo del lavoro, quindi non può accorgersi che è finito: se l'agente non chiama
  `rada_release`, la memoria resta promessa a lui finché la sessione non si chiude. Le due
  reti sotto sono lo spegnimento del server, che restituisce quello che teneva, e
  `sched.reap`, che libera tutto quando quel pid non c'è più. Una scadenza sul posto
  sarebbe una regola di scheduling nuova, e quelle stanno in `sched.py` o non stanno.
- **Le prenotazioni avevano tre difetti**, trovati il 02/09/2026 e corretti insieme alla
  regola 9: la testa che rinunciava si riprendeva la prenotazione al giro dopo (tre
  secondi invece di due minuti), il conteggio dei fallimenti veniva letto dalla copia
  svuotata e quindi l'attesa non raddoppiava mai, e `head_need` era letto come 0 invece
  che con la soglia `DEFAULT_NEED`, così chi si infila sotto poteva mangiare la quota
  tenuta. Sezione 7e di `tools/prova.py`. Se tocchi quel ramo, quei test sono la rete.
- **Essere chiamato dall'hook di un altro è un'installazione corretta.** Su questa
  macchina `rada-gate` lo chiama `~/.claude/hooks/no-cd-persist.py` (v2), che è il punto
  d'ingresso unico apposta: la v1 si faceva da parte per i pattern pesanti di rada e un
  comando passava non avvolto, perché rada risponde `{}` quando il job può partire
  subito. Registrare `rada-gate` a parte in settings.json accoderebbe due volte un job
  pesante, e sta scritto nell'intestazione di quel file. Quindi `setup_claude.reached_through`
  legge i comandi degli hook registrati (ogni parola che nomina un file leggibile, non
  solo la prima, o si trova l'interprete invece dello script) e cerca chi nomina
  `rada-gate`: `doctor` lo riporta come installazione corretta e `install` si rifiuta di
  registrarsi una seconda volta. Sezione 9c di `tools/prova.py`. Misurato il 21/10/2026:
  il gate costa 3,2 ms oltre il costo di far partire un hook qualunque (7,4 ms contro
  10,6, mediana su 40 giri) e per un comando non pesante stampa `{}`, quindi non mette
  niente nel contesto del modello.
- **Il sito sta in `docs/`** e lo serve GitHub Pages da `master`. `docs/brand.md` tiene
  il marchio e i colori; `docs/img/mark.svg` e `macapp/Tools/make-icon.swift` sono lo
  stesso disegno in due linguaggi e vanno cambiati insieme.
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

## App installata in /Applications

La copia che l'utente usa sta in `/Applications/Rada.app`. La copia in
`~/dev/rada/macapp/Rada.app` resta dov'è: è quella che produce `macapp/build.sh` e quella
che fotografa `tools/schermate-app.sh`.

- Dopo ogni build che l'utente deve usare, lancia
  `~/.local/bin/installa-app ~/dev/rada/macapp/Rada.app`. Copia la app, verifica che il
  cdhash sia identico e la registra. Si ferma se la copia installata è aperta.
- Rada.app non chiede permessi di privacy (nessun entitlement, non legge cartelle
  protette), quindi una build nuova non fa perdere niente. `build.sh` la firma con la
  prima identità Apple Development della macchina (ad-hoc solo se non ce n'è), perché
  una firma ad-hoc non ha team e le notifiche vengono negate in partenza.
- La app trova la CLI da sola: `RADA_BIN`, poi il PATH, poi `~/.local/bin/rada`
  (`Cli.swift`). Il link `~/.local/bin/rada` -> `~/dev/rada/bin/rada` lo usano gli hook di
  Claude e `installa-app` non lo tocca. Non spostarlo.

## L'app non si apre da una sessione

- Mai `open -a Rada`, `open Rada.app` o il binario da una sessione, nemmeno per provare:
  la finestra viene avanti sul Mac di Eugenio, che può stare facendo prove con utenti
  (18/11/2026). Le prove si fanno da terminale: `rada status --json`, `rada_queue`,
  `swift test`, e per il ramo degli avvisi `open -g --env RADA_NOTICE_OUT=<file> -a
  /Applications/Rada.app rada://stuck/<id>`, che non porta niente in primo piano e deve
  uscire entro tre secondi. Anche questo lancia l'app: mentre Eugenio ha qualcuno davanti
  allo schermo, chiediglielo prima.
- Lanciata per un avviso, l'app deve chiudersi in ogni ramo. `announce` è
  `deliver` più la chiusura, apposta: prima tre rami tornavano senza chiudere e il
  processo restava vivo, invisibile, e ogni doppio clic andava a lui. Un ramo nuovo va
  dentro `deliver`, mai in `announce`.
- Restare o andarsene dipende da `personSeen`, che diventa vero solo quando l'app è
  attiva (una persona l'ha aperta o portata avanti; `open -g` non la attiva mai). Prima
  decideva un orologio di cinque secondi, e sbagliava nei due versi: un avvio rallentato
  dallo swap restava aperto per sempre con finestra e Dock, e la finestra appena aperta
  da una persona poteva sparire perché nello stesso momento arrivava un avviso. Le reti
  sotto: `stepBack` chiude comunque dopo 20 s, e un processo che nessuno ha mai attivato
  e che non ha finestre sullo schermo esce dopo 12 s (non in modalità `--shot` o
  `--notice`). Il bottone «Start it anyway»
  rilancia l'app in background, e anche quel lancio si fa da parte ed esce.
- Un avvio che nessuno ha chiesto di vedere tiene la finestra nascosta finché una persona
  non attiva l'app. Misurato il 18/11 campionando la lista delle finestre ogni decimo di
  secondo: l'url arriva prima della fine dell'avvio, e la finestra che SwiftUI crea dopo
  restava sullo schermo per un secondo e mezzo. Ora in `applicationDidFinishLaunching` un
  avvio non di default e non attivo mette via le finestre, un osservatore di
  `didUpdate` mette via quelle che compaiono dopo, e `applicationDidBecomeActive` le
  ridà. Non quando la persona ha già chiesto: un doppio clic che arriva durante l'avvio
  viene prima, e rinascondere lì lasciava l'app davanti senza finestra.
- `comeForward` cerca la finestra per `titled` e `canBecomeKey`, non per
  `canBecomeMain`: una finestra messa via mentre l'app era accessory risponde false a
  quello finché non torna sullo schermo, e il filtro non trovava niente.
- La prova dal vivo che ha trovato questi due difetti (18/11): lancio per avviso tre
  volte, lancio da persona, doppio clic a 0,3, 0,6, 1,0 e 1,6 secondi da un lancio per
  avviso, doppio clic e avviso con la finestra aperta, lancio in background senza url.
  Si ripete con `open` verso `macapp/Rada.app` e `osascript -l JavaScript
  tools/finestre-rada.js 4`, che campiona le finestre e non chiede permessi.
- L'url arriva a `handle(url:)` da un gestore Apple Event messo in
  `applicationWillFinishLaunching`, prima che SwiftUI lo veda: lasciato a SwiftUI, un url
  verso un'app aperta può aprire una finestra nuova. `onOpenURL` resta come seconda
  strada, e il doppione si scarta per biglietto entro tre secondi.
- La riga di comando passa un avviso all'app solo se `~/.rada/app.json` dice granted o
  provisional, e al massimo uno ogni `NOTICE_EVERY` (600 s) per tutta la macchina, con il
  turno nella chiave `noticed` di state.json sotto il lucchetto. Non togliere nessuno dei
  due: senza, sei sessioni con un job bloccato erano sei lanci dell'app nello stesso
  minuto.
- `rada_queue` deve restare la stessa immagine della finestra (`cli.picture`). Se la
  finestra mostra un campo nuovo, va anche lì: un modello che non lo trova apre l'app.
