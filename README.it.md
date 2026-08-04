# rada

Una rada per i job pesanti, così che più sessioni di Claude Code sullo stesso portatile
smettano di lanciarli tutte insieme.

[English](README.md)

## Perché esiste

Quattro sessioni di Claude Code aperte su un MacBook Pro da 16 GB, una per progetto.
Ognuna ha deciso, in modo ragionevole e indipendente, che era un buon momento per far
partire qualcosa di grosso: un modello PyTorch in singola precisione, una build Xcode, un
import Unity, una passata di ffmpeg. Nessuna vedeva le altre. La macchina aveva 2992 MB
di swap occupati su 4096 e 88000 pageout prima che si notasse qualcosa, e poi è rimasta
ferma per parecchi minuti.

Niente di tutto questo è un difetto di Claude Code. Le sessioni sono isolate per scelta,
ed è quasi sempre quello che serve. Vuol dire soltanto che su una macchina sola nessuno
tiene il conto.

rada tiene il conto. Un job che sembra pesante aspetta in coda finché lo spazio c'è
davvero, e quando ne aspettano diversi è un modello linguistico a decidere chi passa
prima, perché un modello che legge il nome del progetto e il comando sa distinguere un
test che qualcuno sta aspettando da una reindicizzazione notturna, e l'ordine di arrivo no.

![rada status](docs/status.svg)

## Come funziona

```
    sessione Claude Code                   rada
    ────────────────────                   ────
    Bash: python train.py
      │
      ├─ hook PreToolUse ────────────────► sembra pesante? ── no ──► parte intatto
      │                                        │ sì
      │                                        ▼
      │                                   salva il comando verbatim,
      │                                   riscrive la chiamata al wrapper
      ▼
    Bash: rada run --ticket 8f3a  # rada: aspetta memoria, poi: python train.py
      │
      ▼
    il wrapper prende un biglietto ─────► coda ──► il giudice la ordina
      │                                        │
      │                                        ▼
      │                                   c'è posto, ed è il tuo turno?
      ├─ no ──► aspetta, dicendo perché e chi tiene la memoria
      └─ sì ──► esegue il comando originale e misura quanto ha preso davvero
```

Non c'è nessun demone. Il coordinamento è un solo file JSON sotto `~/.rada` protetto da un
lock, e l'attesa la fa un processo normale, che Claude Code sa già mandare in timeout e
spostare in background.

## Cosa decide il giudice, e cosa non può decidere

Il giudice è `claude -p` con un prompt breve: la coda, e la richiesta di ordinarla secondo
chi è più probabile stia aspettando il risultato. Parte solo quando in coda ci sono almeno
due job, al massimo una volta ogni tre minuti, dentro il processo del job che aspetta da
più tempo. Non c'è nessun account da configurare e niente da installare.

La sua risposta non è un ordine. Diventa un bonus di al massimo tre punti su un punteggio
in cui l'attesa vale un punto ogni trenta secondi:

    punteggio = attesa / 30s + bonus,   bonus fra 0 e 3

Ne seguono due cose, entrambe verificate dai test e non affermate e basta.

**Un job non può essere scavalcato per sempre.** Un job arrivato più di novanta secondi
prima batte un nuovo arrivato qualunque cosa dica il giudice, perché novanta secondi di
attesa valgono più del bonus massimo.

**Un job che ha aspettato dieci minuti smette di essere affare del giudice.** Entra in un
insieme che viene servito per primo e ordinato solo per orario di arrivo, e da quell'insieme
il giudice è escluso del tutto. Da quel momento i job che possono ancora passargli davanti
sono esattamente quelli già dentro prima di lui, e quel gruppo non può più crescere.

Quindi la promessa è: **un job in attesa viene superato da un numero limitato di altri
job, e il limite è fissato nel momento in cui diventa obbligatorio.** La promessa è in
completamenti e non in minuti, di proposito. Una garanzia in tempo reale sarebbe una
bugia, perché un job che tiene memoria può girare quanto vuole e rada non uccide niente
che una persona abbia avviato.

Se il giudice è lento, assente, o risponde con qualcosa che non è una permutazione esatta
della coda che gli è stata data, la risposta viene buttata e la coda va per ordine di
arrivo. La coda non lo aspetta mai.

## La memoria

Il numero che rada può spendere non è quello che sembra disponibile. La cache dei file
conta come disponibile e non lo è davvero, il compressore tiene memoria vera, e su Apple
Silicon un'allocazione PyTorch sulla GPU finisce nella memoria normale, dove nessuno la
rifiuterà. Quindi il budget è

    totale − riserva − (wired + compressore + anonima non compressa)

con una riserva del 15 per cento o 1,5 GB, il maggiore dei due, e tre stop netti: pressione
del kernel sopra il normale, stima di libero del kernel sotto il 25 per cento, e un tetto
quando lo swap è pieno per più di tre quarti. Un job entra solo se la sua stima per 1,3 ci
sta.

La stima viene dal job stesso. rada campiona l'impronta fisica di tutto il gruppo di
processi mentre gira e ricorda il picco, indicizzato su una firma del comando con i numeri
cancellati, così rilanciare lo stesso script con un altro learning rate eredita quello che
si era imparato. Se lo sai già, dichiaralo con `--need 6G`.

Quando il job in testa non ci sta, rada prima si chiede se aspettare possa servire a
qualcosa: una prenotazione libera solo la memoria che rada stessa ha concesso, quindi se il
job non ci starebbe neanche dopo che tutti i job in coda hanno finito, quella memoria è di
programmi fuori dalla coda e tenere fermi gli altri non ottiene niente. In quel caso rada
dice quali programmi la tengono e aspetta senza bloccare nessuno.

Quando invece drenare potrebbe bastare, rada prenota: smette di ammettere qualunque cosa
mangerebbe la sua quota e lascia che la macchina si svuoti, facendo passare sotto solo i
job brevi. Se dopo sette minuti la testa ancora non ci sta, molla la prenotazione con
un'attesa crescente e intanto lascia lavorare gli altri.

## Installazione

```bash
git clone https://github.com/nerln/rada.git ~/dev/rada
cd ~/dev/rada
./bin/rada install
```

Registra un solo hook `PreToolUse`. Gira prima di ogni comando Bash di ogni sessione,
quindi è scritto per fare un fork solo e confrontare con i builtin della shell: circa 3 ms
sopra il costo di lanciare un hook qualunque, per i comandi che non sono pesanti.

La prima volta che un comando pesante viene riscritto, Claude Code chiede il permesso, e la
richiesta mostra il comando vero in un commento a fine riga. Per non farsi più chiedere,
aggiungi una regola per il wrapper:

    Bash(/Users/tuonome/dev/rada/bin/rada run:*)

**Leggi questo prima di aggiungerla.** Claude Code confronta le regole di permesso con il
comando riscritto, quindi incapsulare un comando rompe il prefisso su cui la sua regola era
scritta. Permettere il wrapper significa che un comando pesante che le tue altre regole
Bash avrebbero fermato non verrà più fermato da quelle. Se i tuoi permessi Bash sono già
larghi non cambia niente che noteresti. Se sono stretti e ci contavi, o lasci perdere la
regola e approvi ogni job quando te lo chiede, oppure lanci `rada mode advise`, che
disattiva la coda automatica e lascia rada come qualcosa che invochi a mano.

![rada doctor](docs/doctor.svg)

## Uso

```bash
rada status                        # cosa gira, cosa aspetta, e perché
rada watch                         # lo stesso, aggiornato
rada run --need 6G -- python train.py
rada run --note "blocca la scadenza del paper" -- pytest tests/
rada run --max 600 -- ./build-lento.sh     # dopo dieci minuti smette di aspettare
rada doctor                        # controlla l'installazione
rada reset                         # dimentica la coda
```

Quali comandi contano come pesanti sta in `~/.rada/heavy.txt`, una sottostringa per riga.
Modificalo e rilancia `rada install` per ricompilarlo.

`RADA_FAKE_BUDGET=500M rada status` fissa il budget a un numero che scegli tu, ed è il
modo per vedere cosa fa la coda su una macchina più piccola della tua.

## Cosa non fa

- Non uccide niente. Un job partito arriva alla fine, e un server di sviluppo che tiene
  memoria per sempre la tiene per sempre. rada lo dice, invece di aspettare in silenzio.
- Non intercetta il lavoro che non diventa mai un comando Bash. Un tool MCP che compila un
  progetto Xcode dentro il proprio server è invisibile a un hook su Bash.
- Non sa quanto serve a un job prima di averlo visto una volta. Al primo giro si assume
  512 MB, se non glielo dici tu.
- Non fa scheduling fra macchine diverse, ed è stato eseguito solo su macOS Apple Silicon.
  Dove non riesce a leggere la memoria lascia passare tutto.
- Non manda niente da nessuna parte. Il giudice esegue `claude -p` in locale, su un prompt
  che contiene nomi di progetto e righe di comando. Se per il tuo repository è troppo,
  `rada mode advise` e il giudice non viene mai chiamato.

## Prompt injection

La coda che il giudice legge contiene righe di comando, che contengono testo dei
repository, che può essere ostile. Quel testo viene delimitato, appiattito su una riga,
troncato e ripulito delle parole che marcano il blocco della coda, e al modello viene detto
che è un dato. Niente di tutto ciò è una garanzia. La garanzia sta a valle: l'unica cosa
che rada accetta dal giudice è una permutazione esatta degli identificatori che gli ha
chiesto, che vale al massimo tre punti, scade dopo tre minuti, e non può toccare l'insieme
degli obbligatori né far scattare una prenotazione. Un giudice completamente raggirato
riesce a spostare un job davanti a un altro per novanta secondi.

## Test

```bash
python3 tools/prova.py
```

Settanta controlli, un paio di secondi, nessun modello caricato e nessuna memoria vera
allocata. Coprono la riscrittura che non lascia sfuggire operatori di shell né newline,
tutti e due i lemmi sull'equità inclusa una simulazione avversariale da quattrocento
round, il recupero dei permessi dopo un crash, il lock sotto quattro processi che lo
martellano, la validazione dell'output del giudice, prenotazione, riempimento e attesa
crescente, e due processi veri che si contendono un posto solo.

Due di quei test esistono perché hanno trovato difetti veri durante lo sviluppo: due job
potevano essere ammessi insieme perché la decisione e il permesso stavano in transazioni
diverse, e il lock poteva essere tenuto da due processi insieme perché si annunciava prima
di dire chi lo possedeva.

## Licenza

GPL-3.0. Vedi [LICENSE](LICENSE).
