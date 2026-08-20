"""Chi altro c'è in rada, adesso.

rada esiste perché sessioni parallele non si vedono fra loro. Con una sessione sola non
c'è nessuno con cui coordinarsi, e la coda diventa puro sovrapprezzo: il job aspetta un
giudice e un budget di memoria per sentirsi dire, dopo un minuto, che può fare quello che
era già libero di fare. Peggio, l'attesa si nota, e chi lavora impara a scavalcare rada.

Quindi il gate chiede prima a questo modulo. Due segnali, ne basta uno:

1. un'altra sessione è passata dal gate di recente (battito)
2. c'è un job in coda o in esecuzione adesso (lease o biglietto nello stato condiviso)

Il secondo chiude il buco che il primo lascia aperto. Una sessione che ha avviato un
lavoro lungo e poi è rimasta zitta non emette battiti, e sparirebbe dal conteggio proprio
mentre tiene occupata più memoria di tutti.

Il conteggio non decide mai di far aspettare un job da solo: decide solo se valga la pena
di guardare. Ogni errore qui lascia il gate al suo comportamento di prima.
"""
import os
import time

from . import store

BEATS = os.path.join(store.HOME, "sessions")

# Quanto resta "viva" una sessione dopo il suo ultimo comando. Una sessione che lavora
# tocca Bash molto più spesso di così; questa finestra serve a non dimenticare una
# sessione aperta che sta leggendo file o scrivendo codice fra un comando e l'altro.
WINDOW = float(os.environ.get("RADA_SESSION_WINDOW", 900))

# Oltre questo, il file del battito è spazzatura di sessioni chiuse settimane fa.
FORGET = 86400.0


def _safe(sid):
    """Un id di sessione è un uuid, ma non si costruisce un percorso su una promessa."""
    if not isinstance(sid, str):
        return None
    keep = "".join(c for c in sid if c.isalnum() or c in "-_")[:64]
    return keep or None


def note(sid):
    """Segna che questa sessione è viva. Non solleva mai."""
    sid = _safe(sid)
    if not sid:
        return
    try:
        os.makedirs(BEATS, exist_ok=True)
        p = os.path.join(BEATS, sid)
        # open+close invece di utime: crea il file la prima volta e lo aggiorna dopo
        with open(p, "w") as f:
            f.write(str(int(time.time())))
    except Exception:
        pass


def others(sid, window=None, now=None):
    """Quante altre sessioni hanno toccato il gate dentro la finestra."""
    sid = _safe(sid)
    window = WINDOW if window is None else window
    now = now or time.time()
    n = 0
    try:
        names = os.listdir(BEATS)
    except OSError:
        return 0
    for name in names:
        if name == sid:
            continue
        p = os.path.join(BEATS, name)
        try:
            age = now - os.path.getmtime(p)
        except OSError:
            continue
        if age <= window:
            n += 1
        elif age > FORGET:
            try:
                os.remove(p)
            except OSError:
                pass
    return n


def busy(d=None):
    """C'è un lavoro in coda o in corso, di chiunque sia.

    Legge senza lock: al massimo si sbaglia sul confine di una transazione, e sbagliare
    qui significa mettere in coda un job che poteva partire subito, oppure il contrario.
    Nessuno dei due è un guasto.
    """
    try:
        d = d if d is not None else store.read()
    except Exception:
        return False
    try:
        return bool(d.get("leases")) or bool(d.get("tickets"))
    except Exception:
        return False


def contended(sid, d=None, window=None, now=None):
    """True se vale la pena di accodare: c'è qualcun altro, o c'è già del lavoro in volo.

    Con False il gate lascia passare il comando intatto, che è quello che deve succedere
    quando una sola sessione è aperta e la macchina è tutta sua.
    """
    if busy(d):
        return True
    return others(sid, window=window, now=now) >= 1


def describe(sid=None, now=None):
    """Riga per `rada status`, in inglese come il resto dell'output. Solo lettura."""
    now = now or time.time()
    live = others(sid, now=now) + (1 if _safe(sid) else 0)
    if live >= 2:
        return f"{live} sessions open, so the queue is on"
    if busy():
        return "one session, but there is work in flight, so the queue is on"
    return "one session on its own, so commands do not go through the queue"
